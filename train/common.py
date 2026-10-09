"""Prompt, serialization and image preprocessing shared by SFT and inference."""

import json
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from ledgerlens.images import (  # noqa: F401 (re-exported)
    DEFAULT_MAX_PIXELS,
    resize_image,
)
from ledgerlens.schema import DOCUMENT_ADAPTER
from worker.prompts import EXTRACT_SYSTEM

SETS = ("train", "val", "test_unseen_layout", "test_seen_layout", "test_cord")


def canonical_target(value: str | dict | BaseModel) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, str):
        value = json.loads(value)
    return DOCUMENT_ADAPTER.validate_python(value).model_dump_json(exclude_none=False)


def build_messages(
    doc_type: str, image: Image.Image, target: str | None = None
) -> list:
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Transcribe this {doc_type}."},
                {"type": "image", "image": image},
            ],
        },
    ]
    if target is not None:
        messages.append({"role": "assistant", "content": canonical_target(target)})
    return messages


def read_rows(data: Path, split: str) -> list[dict]:
    rows = [
        json.loads(line)
        for line in (data / f"{split}.jsonl").read_text().splitlines()
        if line.strip()
    ]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate IDs in {split}")
    for row in rows:
        if Path(row["id"]).name != row["id"] or row["id"].startswith("."):
            raise ValueError(f"Unsafe page ID: {row['id']}")
    return rows


def assistant_start(input_ids: list[int], delimiter: list[int]) -> int:
    """Find the single assistant delimiter in this one-turn extraction sample."""
    positions = [
        index + len(delimiter)
        for index in range(len(input_ids) - len(delimiter) + 1)
        if input_ids[index : index + len(delimiter)] == delimiter
    ]
    if len(positions) != 1:
        raise ValueError("Expected exactly one assistant token delimiter")
    return positions[0]
