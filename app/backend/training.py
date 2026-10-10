"""One-time training plans and durable, budget-limited run history."""

import copy
import json
import math
import os
import secrets
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backend import datasets
from app.backend.pod_runner import (
    TERMINAL,
    FakeRunner,
    RealRunner,
    assert_volume_idle,
    json_output,
    sanitize,
    sanitize_data,
    shutdown,
)

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "data/runs"
LOCK = threading.RLock()
TOKENS = {}
RUNNERS = {}
router = APIRouter()
# Measured on run 1, 10 Oct 2026, RTX PRO 4500 at $0.72/hour.
MEASURED = {
    "generate": 15,
    "dataset": 10,
    "smoke": 16,
    "main": 86,
    "predict-8b": 105,
    "predict-4b": 55,
    "predict-ft": 50,
    "report": 9,
    "pod-start-copy": 5,
}
LABELS = {
    "full": "Full training and evaluation",
    "quick": "Quick check: proves the whole pipeline runs end to end on a small set; it does not produce scores (the report stage is skipped)",
}


def presets():
    result = []
    for name in LABELS:
        stages = []
        for stage in (
            "pod-start-copy",
            *(s for s in MEASURED if s != "pod-start-copy"),
        ):
            minutes = MEASURED[stage]
            if name == "quick":
                if stage == "report":
                    continue
                # Approximate allocation only; the total comes from the rehearsal.
                minutes *= 180 / (351 - 9)
            stages.append({"id": stage, "minutes": minutes, "estimated": True})
        minutes = sum(s["minutes"] for s in stages)
        cost = 2.70 if name == "quick" else minutes / 60 * 0.72
        if name == "quick":
            minutes = 180
        result.append(
            {
                "id": name,
                "label": LABELS[name],
                "estimated": True,
                "stages": stages,
                "total_minutes": minutes,
                "total_cost": cost,
                "suggested_cap_usd": math.ceil(cost * 1.5),
                "dataset_note": "Real shop receipts (CORD v2) are always mixed in.",
                "stage_note": "Per-stage timings are approximate; the quick total is measured.",
                "measurement_note": "About 3 h and about $2.70 including setup, measured in the 9 Oct rehearsal"
                if name == "quick"
                else "Stage estimates from the full run",
                "env": {
                    "N_BUSINESSES": "20",
                    "STOP_AFTER": "predict-ft",
                    "PREDICT_LIMIT": "3",
                }
                if name == "quick"
                else {"N_BUSINESSES": "400"},
            }
        )
    return result


@router.get("/api/training/presets")
def get_presets():
    return presets()


def simulated():
    return os.getenv("TRAINING_ENABLED") != "1" or os.getenv("DEMO_MODE") == "1"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def states():
    index = ROOT / "index.json"
    identities = json.loads(index.read_text()) if index.exists() else []
    # Include orphan state files if interrupted between writing state and index.
    identities = set(identities) | {p.parent.name for p in ROOT.glob("*/run.json")}
    return [
        json.loads((ROOT / i / "run.json").read_text())
        for i in identities
        if (ROOT / i / "run.json").is_file()
    ]


def active_states():
    return [s for s in states() if s["status"] not in TERMINAL]


def assert_idle():
    if active_states():
        raise HTTPException(
            409, "Another run is active or its Pod deletion needs confirmation"
        )


def stock():
    try:
        result = subprocess.run(
            ["runpodctl", "gpu", "list", "--include-unavailable"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        data = json_output(result.stdout)
        gpus = data if isinstance(data, list) else data["gpus"]
        for preferred in ("NVIDIA RTX PRO 4500 Blackwell", "NVIDIA RTX 5090"):
            for gpu in gpus:
                if gpu.get("id") != preferred and gpu.get("displayName") != preferred:
                    continue
                here = next(
                    (
                        a
                        for a in gpu.get("dataCenterAvailability", [])
                        if a.get("dataCenterId") == "EU-RO-1"
                    ),
                    {},
                )
                if here.get("stockStatus") not in (
                    "High",
                    "Medium",
                    "Low",
                    "HIGH",
                    "MEDIUM",
                    "LOW",
                    "Available",
                    "AVAILABLE",
                ):
                    continue
                price = here.get("securePricePerHr") or gpu.get("securePricePerHr")
                if price and math.isfinite(float(price)) and float(price) > 0:
                    return preferred, float(price), "Available in EU-RO-1", True
        return None, 0.72, "Neither supported GPU is available in EU-RO-1", False
    except Exception:
        return (
            None,
            0.72,
            "GPU stock could not be read; the price below is an estimate",
            False,
        )


class Plan(BaseModel):
    preset: Literal["full", "quick"]
    dataset: str = "default"
    cap_usd: float = Field(gt=0, le=25, allow_inf_nan=False)


@router.post("/api/training/plan")
def plan(request: Plan):
    with LOCK, datasets.LOCK:
        assert_idle()
        try:
            assert_volume_idle()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        if request.dataset != "default":
            folder = datasets.upload_path(request.dataset)
            if not json.loads((folder / ".validation.json").read_text()).get("valid"):
                raise HTTPException(422, "Dataset did not pass validation")
        preset = copy.deepcopy(next(p for p in presets() if p["id"] == request.preset))
        gpu, price, availability, verified = stock()
        if request.preset != "quick":
            preset["total_cost"] = preset["total_minutes"] / 60 * price
        preset["suggested_cap_usd"] = math.ceil(preset["total_cost"] * 1.5)
        value = {
            "preset": request.preset,
            "dataset": request.dataset,
            "cap_usd": request.cap_usd,
            "gpu": gpu,
            "price_per_hour": price,
            "stock": availability,
            "stock_verified": verified,
            "estimate": preset,
            "env": preset["env"],
            "simulated": simulated(),
            "steps": [
                "Create a GPU Pod in EU-RO-1 using the existing network volume.",
                "Check that /workspace has at least 20 GB free.",
                "Copy the committed code using a Git bundle.",
                "Copy the rendered uploaded dataset; skip generation."
                if request.dataset != "default"
                else "Generate fictional business documents.",
                "Build the dataset, run smoke training, then train the main model.",
                "Predict a few pages with all three models; skip the report and scores."
                if request.preset == "quick"
                else "Evaluate the 8B, 4B and fine-tuned 4B models and build the report.",
                "Copy results back and delete the Pod, including on failure, cancel or spending limit.",
            ],
        }
        if request.dataset != "default":
            value["dataset_note"] = preset["dataset_note"]
        if request.preset == "quick":
            value["warning"] = (
                "About 3 h and about $2.70 including setup, measured in the 9 Oct rehearsal. Uploaded datasets may take longer."
            )
        else:
            value["warning"] = (
                "Estimates exclude setup cache misses and storage charges. Uploaded datasets may take longer than the preset estimate."
            )
        for expired in [
            t for t, (expiry, _) in TOKENS.items() if expiry <= time.time()
        ]:
            TOKENS.pop(expired)
        token = secrets.token_urlsafe(32)
        TOKENS[token] = (time.time() + 600, copy.deepcopy(value))
        return {**value, "confirm_token": token, "expires_in_seconds": 600}


class Confirm(BaseModel):
    confirm_token: str


@router.post("/api/training/runs")
def start(request: Confirm):
    with LOCK, datasets.LOCK:
        token = TOKENS.pop(request.confirm_token, None)
        if token is None or token[0] <= time.time():
            raise HTTPException(
                422, "Confirmation token is invalid, expired or already used"
            )
        assert_idle()
        value = token[1]
        if value["simulated"] != simulated():
            raise HTTPException(409, "Training mode changed; make a new plan")
        if not value["simulated"] and not value["stock_verified"]:
            raise HTTPException(
                409, "GPU availability and price must be verified before a real run"
            )
        if value["dataset"] != "default":
            datasets.upload_path(value["dataset"])
        identity = uuid.uuid4().hex[:16]
        folder = ROOT / identity
        folder.mkdir(parents=True)
        state = {
            **value,
            "id": identity,
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "queued",
            "stage": "pending",
            "elapsed": 0,
            "spend": 0,
            "progress": 0,
        }

        def update(**changes):
            with LOCK:
                # A cleanup warning must never be overwritten by normal progress.
                if (
                    state["status"].startswith("POD MAY")
                    and "status" in changes
                    and not state.get("pod_deleted")
                ):
                    changes.pop("status")
                if "result_summary" in changes:
                    changes["result_summary"] = sanitize_data(changes["result_summary"])
                state.update(changes)
                atomic_json(folder / "run.json", state)

        update()
        atomic_json(ROOT / "index.json", [s["id"] for s in states()])
        (folder / "log.txt").touch()
        runner = (
            FakeRunner(state, folder, update)
            if value["simulated"]
            else RealRunner(state, folder, update)
        )
        RUNNERS[identity] = runner

        def execute():
            try:
                runner.run()
            except Exception:
                update(status="failed")
                runner.log("Training failed")
            finally:
                with LOCK:
                    RUNNERS.pop(identity, None)

        threading.Thread(target=execute, daemon=value["simulated"]).start()
        return {"id": identity, "simulated": state["simulated"]}


def reported():
    def read(name):
        path = REPO / "eval" / name
        return sanitize_data(json.loads(path.read_text())) if path.exists() else None

    return {
        "id": "reported",
        "created": "2026-10-10",
        "preset": "full",
        "dataset": "default",
        "simulated": False,
        "read_only": True,
        "status": "done",
        "stage": "report",
        "elapsed": None,
        "spend": None,
        "cap_usd": None,
        "result_summary": {
            "simulated": False,
            "training": read("training_summary.json"),
            "evaluation": read("summary.json"),
        },
    }


@router.get("/api/training/runs")
def history():
    with LOCK:
        return [reported(), *sorted(states(), key=lambda s: s["created"], reverse=True)]


def state_path(identity):
    if not identity.isalnum() or not (ROOT / identity / "run.json").is_file():
        raise HTTPException(404, "Run not found")
    return ROOT / identity


@router.get("/api/training/runs/{identity}")
def detail(identity: str):
    with LOCK:
        if identity == "reported":
            return {**reported(), "logs": []}
        folder = state_path(identity)
        value = json.loads((folder / "run.json").read_text())
        log = folder / "log.txt"
        return {
            **value,
            "logs": sanitize(log.read_text()).splitlines()[-200:]
            if log.exists()
            else [],
        }


@router.post("/api/training/runs/{identity}/cancel")
def cancel(identity: str):
    if identity == "reported":
        raise HTTPException(409, "The reported run is read-only")
    with LOCK:
        folder = state_path(identity)
        value = json.loads((folder / "run.json").read_text())
        runner = RUNNERS.get(identity)
        if value["status"] in TERMINAL:
            return {
                "id": identity,
                "status": value["status"],
                "simulated": value["simulated"],
            }
        if runner:
            runner.cancel()
        elif not value["simulated"] and (
            value.get("pod_creation_attempted")
            or value.get("pod_id")
            or value["status"].startswith("POD MAY")
        ):

            def update(**changes):
                with LOCK:
                    value.update(changes)
                    atomic_json(folder / "run.json", value)

            runner = RealRunner(value, folder, update)
            runner.pod_id = value.get("pod_id")
            runner.created_at = value.get("pod_created")
            if not runner.pod_id:
                runner.find_pod()
        else:
            value["status"] = "cancelled"
            atomic_json(folder / "run.json", value)
    if isinstance(runner, RealRunner):
        runner.cleanup()
        if not runner.pod_id and identity not in RUNNERS:
            runner.update(
                status=f"POD MAY STILL BE RUNNING: check ledgerlens-train-{identity} in the Runpod console"
            )
        if runner.cleaned and identity not in RUNNERS:
            with LOCK:
                value["status"] = "cancelled"
                value["pod_deleted"] = True
                atomic_json(folder / "run.json", value)
    return {
        "id": identity,
        "simulated": value["simulated"],
        "status": detail(identity)["status"],
    }


def shutdown_runs():
    with LOCK:
        runners = list(RUNNERS.values())
    for runner in runners:
        runner.cancel()
    shutdown()
