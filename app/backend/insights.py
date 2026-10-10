"""Read-only dataset exploration with committed demo fallbacks."""

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ledgerlens.insights import (
    STRENGTHS,
    dataset_summary,
    page_index,
    read_json,
    results_breakdown,
    training_summary,
)

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter()


def data_source():
    value = os.getenv("LEDGERLENS_DATA")
    if value and Path(value).is_dir():
        return Path(value), "local-data"
    return ROOT / "app/sample/dataset", "committed-summary"


def summary_file(name):
    path = ROOT / "eval" / (name + ".json")
    return read_json(path) if path.exists() else {"available": False}


@router.get("/api/dataset/summary")
def summary():
    root, source = data_source()
    return {
        **(
            dataset_summary(root)
            if source == "local-data"
            else summary_file("dataset_summary")
        ),
        "source": source,
    }


@router.get("/api/dataset/pages")
def pages(
    split: str | None = None,
    layout: str | None = None,
    doc_type: str | None = None,
    anomaly: str | None = None,
    min_strength: int | None = Query(None, ge=0, le=3),
    max_strength: int | None = Query(None, ge=0, le=3),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    root, source = data_source()
    index = page_index(root) if source == "local-data" else summary_file("page_index")
    selected = []
    for p in index.get("pages", []):
        strength = (
            STRENGTHS.index(p["strength"]) if p["strength"] in STRENGTHS else None
        )
        if any(
            value is not None and p[key] != value
            for key, value in [
                ("split", split),
                ("layout_id", layout),
                ("doc_type", doc_type),
            ]
        ):
            continue
        if (
            anomaly == "any"
            and not p["anomalies"]
            or anomaly == "none"
            and p["anomalies"]
        ):
            continue
        if anomaly and anomaly not in ("any", "none") and anomaly not in p["anomalies"]:
            continue
        if (
            min_strength is not None
            and (strength is None or strength < min_strength)
            or max_strength is not None
            and (strength is None or strength > max_strength)
        ):
            continue
        selected.append(
            {
                **p,
                "detail_available": source == "local-data"
                or (root / (p["id"] + ".json")).is_file(),
            }
        )
    return {
        "pages": selected[offset : offset + limit],
        "total": len(selected),
        "source": source,
        "note": index.get("note"),
        "strength_scale": list(STRENGTHS),
    }


def page_path(page_id, suffix):
    root, source = data_source()
    path = (root / (page_id + suffix)).resolve()
    if (
        not re.fullmatch(r"[A-Za-z0-9_]+", page_id)
        or not path.is_relative_to(root.resolve())
        or not path.is_file()
    ):
        raise HTTPException(404, "Page not found")
    return path, source


@router.get("/api/dataset/pages/{page_id}")
def detail(page_id: str):
    gold, source = page_path(page_id, ".json")
    meta, _ = page_path(page_id, ".meta.json")
    return {"document": read_json(gold), "meta": read_json(meta), "source": source}


@router.get("/api/dataset/pages/{page_id}/preview")
def preview(page_id: str):
    path, source = page_path(page_id, ".html")
    return FileResponse(
        path,
        media_type="text/html",
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:",
            "X-Content-Type-Options": "nosniff",
            "source": source,
        },
    )


@router.get("/api/training/summary")
def training():
    root, source = data_source()
    path = root.parent / "eval-final/main/train_summary.json"
    if source == "local-data" and path.is_file():
        return {**training_summary(path), "source": source}
    return {**summary_file("training_summary"), "source": "committed-summary"}


@router.get("/api/results/breakdown")
def breakdown():
    root, source = data_source()
    path = root.parent / "eval-final/eval/results.json"
    if source == "local-data" and path.is_file():
        return {**results_breakdown(path, root), "source": source}
    return {**summary_file("breakdown"), "source": "committed-summary"}
