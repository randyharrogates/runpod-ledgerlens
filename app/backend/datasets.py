"""Validate uploaded archives before publishing a dataset."""

import io
import json
import random
import re
import shutil
import stat
import tempfile
import threading
import uuid
import warnings
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image

from ledgerlens.images import resize_image
from ledgerlens.schema import DOCUMENT_ADAPTER

ROOT = Path(__file__).resolve().parents[2] / "data/uploads"
LOCK = threading.RLock()
router = APIRouter()
NAME = re.compile(r"[A-Za-z0-9_]+")
FILE = re.compile(
    r"([A-Za-z0-9_]+)(\.meta\.json|\.json|\.html|\.png|\.jpg|\.jpeg|\.webp)"
)


def upload_path(identity):
    if not NAME.fullmatch(identity):
        raise HTTPException(404, "Dataset not found")
    path = ROOT / identity
    if not (path / ".validation.json").is_file():
        raise HTTPException(404, "Dataset not found")
    return path


def validate_archive(data):
    problems, files, metadata, pages = [], {}, {}, set()

    def problem(file, message):
        if len(problems) < 50:
            problems.append({"file": file, "problem": message})

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
        if len(entries) > 20_000 or sum(i.file_size for i in entries) > 2 * 1024**3:
            raise ValueError("Archive exceeds entry or uncompressed size limit")
        tops = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            parts = path.parts
            if (
                not parts
                or path.is_absolute()
                or ".." in parts
                or "\\" in entry.filename
                or stat.S_ISLNK(entry.external_attr >> 16)
                or len(parts) > 2
                or entry.file_size > max(entry.compress_size, 1) * 100
            ):
                problem(
                    entry.filename,
                    "Unsafe path, symlink or compression ratio above 100",
                )
                continue
            if len(parts) == 2:
                tops.add(parts[0])
            elif not entry.is_dir():
                tops.add("")
            if entry.is_dir():
                if len(parts) != 1:
                    problem(entry.filename, "Only one optional top folder is allowed")
                continue
            name = parts[-1]
            if name != "splits.json" and not FILE.fullmatch(name):
                problem(name, "Unsupported file name or type")
            elif name in files:
                problem(name, "Duplicate file")
            else:
                files[name] = entry
        if len(tops) > 1:
            problem("archive", "Use flat files or a single top folder")
        if problems:
            return None, {
                "valid": False,
                "entries": len(entries),
                "pages": 0,
                "problems": problems,
            }
        contents = {}
        for name, entry in files.items():
            payload = archive.read(entry)
            contents[name] = payload
            match = FILE.fullmatch(name)
            try:
                if name == "splits.json":
                    continue
                identity, suffix = match.groups()
                if suffix == ".json":
                    DOCUMENT_ADAPTER.validate_json(payload)
                    pages.add(identity)
                elif suffix == ".meta.json":
                    meta = json.loads(payload)
                    if not isinstance(meta, dict) or any(
                        not isinstance(meta.get(k), str) or not meta[k]
                        for k in ("business_id", "layout_id")
                    ):
                        raise ValueError("Meta needs business_id and layout_id strings")
                    if meta.get("strength", "clean") not in (
                        "clean",
                        "light",
                        "medium",
                        "heavy",
                    ):
                        raise ValueError("Unknown strength")
                    labels = meta.get("anomalies", [])
                    if not isinstance(labels, list) or any(
                        not isinstance(a, dict) or not isinstance(a.get("type"), str)
                        for a in labels
                    ):
                        raise ValueError("Invalid anomaly metadata")
                    if not isinstance(meta.get("country", "unknown"), str):
                        raise ValueError("Country must be text")
                    metadata[identity] = meta
                elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", Image.DecompressionBombWarning)
                        with Image.open(io.BytesIO(payload)) as image:
                            if image.width * image.height > 60_000_000:
                                raise ValueError("Image exceeds 60,000,000 pixels")
                            if image.format not in ("PNG", "JPEG", "WEBP"):
                                raise ValueError("Use PNG, JPEG or WEBP images")
                            image.verify()
                        # Dimensions were checked before decoding. Training expects PNG.
                        with Image.open(io.BytesIO(payload)) as image:
                            normalized = io.BytesIO()
                            resize_image(image).save(normalized, format="PNG")
                            contents[identity + ".png"] = normalized.getvalue()
            except Exception:
                problem(
                    name,
                    "Invalid document, metadata or image (image limit: 60,000,000 pixels)",
                )
        for identity in pages:
            if identity not in metadata:
                problem(identity, "Missing valid meta file")
            if not any(
                identity + suffix in contents
                for suffix in (".html", ".png", ".jpg", ".jpeg", ".webp")
            ):
                problem(identity, "Every page needs HTML or an image")
        for name in files:
            match = FILE.fullmatch(name)
            if match and match[1] not in pages:
                problem(name, "Missing valid gold document")
        if not pages:
            problem("archive", "No valid pages found")
        splits = {s: [] for s in ("train", "val", "test")}
        try:
            if "splits.json" in contents:
                splits = json.loads(contents["splits.json"])
                if not isinstance(splits, dict) or set(splits) != {
                    "train",
                    "val",
                    "test",
                }:
                    raise ValueError
                splits = {k: splits[k] for k in ("train", "val", "test")}
                listed = [p for values in splits.values() for p in values]
                if (
                    any(not isinstance(v, list) for v in splits.values())
                    or len(listed) != len(set(listed))
                    or set(listed) != pages
                ):
                    raise ValueError
                businesses = {}
                for split, values in splits.items():
                    for p in values:
                        business = metadata[p]["business_id"]
                        if business in businesses and businesses[business] != split:
                            raise ValueError
                        businesses[business] = split
            elif not problems:
                businesses = sorted({m["business_id"] for m in metadata.values()})
                random.Random(7).shuffle(businesses)
                n = len(businesses)
                membership = {
                    b: "train"
                    if i < round(n * 0.8)
                    else "val"
                    if i < round(n * 0.9)
                    else "test"
                    for i, b in enumerate(businesses)
                }
                for p in sorted(pages):
                    splits[membership[metadata[p]["business_id"]]].append(p)
            if not problems:
                assignments = {
                    metadata[p]["business_id"]: split
                    for split, values in splits.items()
                    for p in values
                }
                training_layouts = {metadata[p]["layout_id"] for p in splits["train"]}
                test_businesses = {metadata[p]["business_id"] for p in splits["test"]}
                splits.update(
                    pages={
                        p: {k: metadata[p][k] for k in ("business_id", "layout_id")}
                        for p in sorted(pages)
                    },
                    business_splits=assignments,
                    business_test_sets={
                        b: "test_unseen_layout"
                        if all(
                            metadata[p]["layout_id"] not in training_layouts
                            for p in splits["test"]
                            if metadata[p]["business_id"] == b
                        )
                        else "test_seen_layout"
                        for b in test_businesses
                    },
                    seed=7,
                )
                csp = b"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:\">"
                for p in pages:
                    meta = metadata[p]
                    meta.update(
                        strength=meta.get("strength", "clean"),
                        seed=7,
                        rendered=p + ".png" in contents,
                    )
                    contents[p + ".meta.json"] = json.dumps(meta).encode()
                    if p + ".html" in contents:
                        contents[p + ".html"] = csp + contents[p + ".html"]
            contents["splits.json"] = json.dumps(splits).encode()
        except Exception:
            problem(
                "splits.json",
                "Splits must cover every page once, with no business shared between splits",
            )
        report = {
            "valid": not problems,
            "entries": len(entries),
            "pages": len(pages),
            "businesses": len({m["business_id"] for m in metadata.values()}),
            "problems": problems,
        }
        return (contents if not problems else None), report
    except Exception:
        return None, {
            "valid": False,
            "pages": 0,
            "problems": [
                {
                    "file": "archive",
                    "problem": "Invalid ZIP or archive exceeds size limits",
                }
            ],
        }


@router.post("/api/datasets")
async def upload(file: UploadFile = File(...)):
    data = await file.read(200 * 1024**2 + 1)
    if len(data) > 200 * 1024**2 or not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(422, "Upload a ZIP no larger than 200 MB")
    contents, report = validate_archive(data)
    if contents is None:
        raise HTTPException(422, report)
    identity = uuid.uuid4().hex
    with LOCK:
        ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            folder = Path(temporary)
            for name, payload in contents.items():
                (folder / name).write_bytes(payload)
            (folder / ".validation.json").write_text(json.dumps(report))
            folder.rename(ROOT / identity)
    return {"id": identity, "validation": report}


@router.get("/api/datasets")
def listing():
    with LOCK:
        return [
            {
                "id": p.name,
                "validation": json.loads((p / ".validation.json").read_text()),
            }
            for p in sorted(ROOT.glob("*"))
            if (p / ".validation.json").is_file()
        ]


@router.delete("/api/datasets/{identity}")
def delete(identity: str):
    from app.backend.training import LOCK as training_lock
    from app.backend.training import active_states

    with training_lock, LOCK:
        if any(s["dataset"] == identity for s in active_states()):
            raise HTTPException(409, "Dataset is in use by a training run")
        shutil.rmtree(upload_path(identity))
    return {"deleted": identity}
