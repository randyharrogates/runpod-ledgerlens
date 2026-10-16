"""Session-scoped asynchronous cover illustrations."""

import base64
import io
import os
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel

from app.backend import logs
from ledgerlens.cover import cover_prompt, user_prompt

router = APIRouter()
SAMPLE_PATH = Path(__file__).resolve().parents[1] / "sample/cover/sample.jpg"


def backend():
    from app.backend import main

    return main


def image_url(encoded, mime):
    data = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 4_000_000:
            raise ValueError("Image exceeds 4,000,000 pixels")
        actual = Image.MIME.get(image.format)
        if mime not in {"image/jpeg", "image/png", "image/webp"} or mime != actual:
            raise ValueError("Invalid image MIME type")
        image.load()
    return f"data:{mime};base64,{encoded}"


def run_cover(s, cover, endpoint, key, seed):
    main = backend()
    fields = {"session": s, "task": "cover"}
    logs.LOG.info(f"cover started: {len(cover['prompt'])} chars", extra=fields)
    try:
        payload = dict(
            prompt=cover["prompt"],
            width=1024,
            height=576,
            steps=28,
            guidance=3.5,
            format="jpeg",
            quality=88,
        )
        if seed is not None:
            payload["seed"] = seed
        with httpx.Client(
            headers={"Authorization": f"Bearer {key}"}, timeout=30
        ) as client:
            response = client.post(
                f"https://api.runpod.ai/v2/{endpoint}/run", json={"input": payload}
            )
            response.raise_for_status()
            job = response.json()["id"]
            with main.LOCK:
                cover["job_id"] = job
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                response = client.get(
                    f"https://api.runpod.ai/v2/{endpoint}/status/{job}"
                )
                response.raise_for_status()
                result = response.json()
                if result.get("status") == "COMPLETED":
                    output = result.get("output") or {}
                    if isinstance(output, list):
                        output = output[0] if len(output) == 1 else {}
                    if output.get("error"):
                        raise ValueError(str(output["error"]))
                    image = image_url(output["image_base64"], output["mime"])
                    measured = main.metrics(result, output, "image")
                    with main.LOCK:
                        cover.update(
                            status="done",
                            image=image,
                            seed=output.get("seed"),
                            metrics=measured,
                        )
                        main.session(s)["requests"].append(
                            {"task": "cover", **measured}
                        )
                    logs.LOG.info("cover finished", extra=fields)
                    return
                if result.get("status") in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                    raise ValueError(result.get("error") or result["status"])
                time.sleep(2)
        raise TimeoutError("Timed out")
    except Exception as exc:
        message = main.clean(str(exc)).replace(key, "[REDACTED]")[:300]
        with main.LOCK:
            cover.update(status="error", error=message)
            main.session(s)["requests"].append(
                {"task": "cover", "source": "runpod", "error": message}
            )
        logs.LOG.warning("cover failed", extra=fields)


class CoverRequest(BaseModel):
    prompt: str | None = None
    seed: int | None = None
    force: bool = False


@router.get("/api/sessions/{s}/cover")
def get_cover(s: str):
    main = backend()
    with main.LOCK:
        return main.clean(main.session(s).get("cover", {"status": "idle"}))


@router.post("/api/sessions/{s}/cover")
def start_cover(s: str, request: CoverRequest):
    main = backend()
    endpoint, key = main.config("image")
    with main.LOCK:
        session = main.session(s)
        auto = request.prompt is None
        info = cover_prompt(
            [p["document"] for p in session["pages"].values() if p.get("document")]
        )
        try:
            prompt = info["prompt"] if auto else user_prompt(request.prompt)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        old = session.get("cover", {})
        if (
            auto
            and old.get("auto")
            and old.get("prompt") == prompt
            and old.get("status") in {"running", "done"}
            and not request.force
        ):
            return main.clean(old)
        if old.get("status") == "running":
            raise HTTPException(409, "Cover already running")
        cover = dict(
            info,
            status="running",
            auto=auto,
            prompt=prompt,
            image=None,
            seed=request.seed,
            metrics=None,
            error=None,
            started=time.time(),
            job_id=None,
        )
        session["cover"] = cover
        if os.getenv("DEMO_MODE") == "1" or not endpoint or not key:
            cover.update(info, auto=True)
            if SAMPLE_PATH.exists():
                try:
                    cover.update(
                        status="done",
                        image=image_url(
                            base64.b64encode(SAMPLE_PATH.read_bytes()).decode(),
                            "image/jpeg",
                        ),
                        source="recorded",
                    )
                except Exception:
                    cover.update(
                        status="error",
                        error="Recorded cover image is invalid; showing figures only.",
                    )
            else:
                cover.update(
                    status="error",
                    error="Image endpoint not configured; showing figures only.",
                )
        else:
            threading.Thread(
                target=run_cover,
                args=(s, cover, endpoint, key, request.seed),
                daemon=True,
            ).start()
        return main.clean(cover)
