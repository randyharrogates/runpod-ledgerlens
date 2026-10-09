"""CPU-only training pipeline contracts."""

import json
from decimal import Decimal

import pytest
from PIL import Image

from ledgerlens.datagen.cli import generate_dataset
from ledgerlens.schema import DOCUMENT_ADAPTER, Receipt
from train.build_dataset import build_dataset, cord_receipt
from train.common import build_messages, canonical_target, resize_image
from train.report import cost_per_1000, make_report
from worker.prompts import EXTRACT_SYSTEM


@pytest.mark.parametrize(
    "menu",
    [
        [{"nm": "Tea", "cnt": "2", "unitprice": "750", "price": "1,500"}],
        {"nm": "Tea", "cnt": "2", "unitprice": "750", "price": "1,500"},
        {"0": {"nm": "Tea", "cnt": "2", "price": "1,500"}},
    ],
)
def test_cord_mapping(menu):
    result = cord_receipt(
        json.dumps(
            {
                "gt_parse": {
                    "menu": menu,
                    "sub_total": {"subtotal_price": "1,500", "tax_price": "0"},
                }
            }
        )
    )
    assert result["menu_items"][0]["amount"] == Decimal("1500")
    assert result["subtotal"] == Decimal("1500")
    assert result["total"] is None
    if "0" in menu:
        assert result["menu_items"][0]["unit_price"] is None
    with pytest.raises(ValueError):
        canonical_target(result)


def test_canonical_roundtrip():
    receipt = Receipt(
        menu_items=[], subtotal=Decimal("1500"), tax=Decimal("0"), total=Decimal("1500")
    )
    target = canonical_target(receipt)
    assert (
        canonical_target(dict(reversed(list(receipt.model_dump().items())))) == target
    )
    assert canonical_target(DOCUMENT_ADAPTER.validate_json(target)) == target


def test_messages():
    messages = build_messages(
        "receipt",
        Image.new("RGB", (10, 10)),
        canonical_target(
            {
                "doc_type": "receipt",
                "menu_items": [],
                "subtotal": "1",
                "tax": "0",
                "total": "1",
            }
        ),
    )
    assert messages[0]["content"] == EXTRACT_SYSTEM
    assert messages[1]["content"][0]["text"] == "Transcribe this receipt."
    assert messages[2]["role"] == "assistant"


def test_resize():
    image = resize_image(Image.new("RGB", (2000, 1000)), 100000)
    assert image.width * image.height <= 100000
    assert abs(image.width / image.height - 2) < 0.02
    assert resize_image(image, 100000).size == image.size
    with pytest.raises(ValueError):
        resize_image(image, 0)


def test_build_and_report(tmp_path):
    synth, out = tmp_path / "synth", tmp_path / "data"
    manifest = generate_dataset(synth, 8, 7, no_render=True)
    for page_id in manifest["pages"]:
        Image.new("RGB", (8, 8)).save(synth / f"{page_id}.png")
    splits = build_dataset(synth, out)
    assert len(splits["train"]) == len(manifest["train"])
    assert len(splits["val"]) == len(manifest["val"])
    assert len(splits["test_seen_layout"]) + len(splits["test_unseen_layout"]) == len(
        manifest["test"]
    )
    ids = [row["id"] for rows in splits.values() for row in rows]
    assert len(ids) == len(set(ids)) == len(manifest["pages"])
    assert splits["test_cord"] == []
    predictions = tmp_path / "predictions"
    for split in ("test_seen_layout", "test_unseen_layout"):
        directory = predictions / split
        directory.mkdir(parents=True)
        for row in splits[split]:
            assert row["image"].startswith("/")
            (directory / f"{row['id']}.json").write_text(row["target"])
        (directory / "_timing.json").write_text(
            json.dumps(
                {
                    "pages": len(splits[split]),
                    "page_ids": [row["id"] for row in splits[split]],
                    "seconds_per_page": 2,
                }
            )
        )
    results = make_report(
        out,
        {"base": predictions},
        {"base": 0.72},
        tmp_path / "eval",
        ["test_seen_layout", "test_unseen_layout"],
    )
    for result in results.values():
        assert result["base"]["header_accuracy"] == 1
        assert result["base"]["row_f1"] == 1
    assert (tmp_path / "eval/results.md").exists()


def test_cost():
    assert cost_per_1000(2, 0.72) == pytest.approx(0.4)
    with pytest.raises(ValueError):
        cost_per_1000(-1, 0.72)


def test_assistant_boundary():
    from train.common import assistant_start

    assert assistant_start([1, 2, 9, 10, 3], [9, 10]) == 4
    with pytest.raises(ValueError):
        assistant_start([1, 2, 3], [9, 10])
    with pytest.raises(ValueError):
        assistant_start([9, 10, 9, 10], [9, 10])


def test_cord_complete_printed_values():
    mapped = cord_receipt(
        {
            "gt_parse": {
                "menu": {"nm": "Tea", "cnt": "2", "unitprice": "750", "price": "1,500"},
                "sub_total": {"subtotal_price": "1,500", "tax_price": "0"},
                "total": {"total_price": "1,500"},
            }
        }
    )
    receipt = DOCUMENT_ADAPTER.validate_json(canonical_target(mapped))
    assert receipt.total == Decimal("1500")
    assert receipt.menu_items[0].quantity == Decimal("2")
    assert receipt.menu_items[0].unit_price == Decimal("750")


def test_render_pod_splits_page_ids_across_workers(tmp_path, monkeypatch):
    import sys

    from train import render_pod

    (tmp_path / "splits.json").write_text(
        json.dumps({"pages": {f"biz_0000_p00{i}": {} for i in range(1, 6)}})
    )
    seen = []

    class InlinePool:
        def __init__(self, workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, fn, *iterables):
            return [fn(*args) for args in zip(*iterables)]

    def fake_render(data, page_ids):
        seen.extend(page_ids)
        return len(page_ids)

    monkeypatch.setattr(render_pod, "ProcessPoolExecutor", InlinePool)
    monkeypatch.setattr(render_pod, "render_chunk", fake_render)
    monkeypatch.setattr(
        sys, "argv", ["render_pod", "--data", str(tmp_path), "--workers", "2"]
    )
    render_pod.main()
    assert sorted(seen) == [f"biz_0000_p00{i}" for i in range(1, 6)]
