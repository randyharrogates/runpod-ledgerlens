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
    assert DOCUMENT_ADAPTER.validate_json(canonical_target(result)).total is None


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


@pytest.mark.parametrize(
    ("value", "quantity", "expected"),
    [
        ("60.000", False, "60000"),
        ("5.455", False, "5455"),
        ("1,200,000", False, "1200000"),
        ("Rp 12.000", False, "12000"),
        ("@12.000", False, "12000"),
        ("x2", True, "2"),
        ("2.00", True, "2.00"),
        ("1,0", True, "1.0"),
        ("", False, None),
        (None, False, None),
    ],
)
def test_cord_numbers(value, quantity, expected):
    from train.build_dataset import cord_number

    assert cord_number(value, quantity=quantity) == (
        Decimal(expected) if expected is not None else None
    )


def test_cord_real_dict_and_addons():
    from collections import Counter

    real = cord_receipt(
        {
            "gt_parse": {
                "menu": {
                    "nm": "-TICKET CP",
                    "num": "901016",
                    "cnt": "2",
                    "price": "60.000",
                    "itemsubtotal": "60.000",
                },
                "sub_total": [{"tax_price": "5.455"}],
                "total": [{"total_price": "60.000"}, {"total_price": "1"}],
            }
        }
    )
    assert real["menu_items"][0]["amount"] == Decimal("60000")
    assert real["menu_items"][0]["unit_price"] is None
    assert real["tax"] == Decimal("5455")
    assert real["total"] == Decimal("60000")
    stats = Counter()
    mapped = cord_receipt(
        {
            "gt_parse": {
                "menu": [
                    {"nm": "Tea", "sub": {"nm": "Milk", "price": "@12.000"}},
                    {"nm": "Rice", "sub": [{"nm": "Egg"}, {"nm": "Sauce"}]},
                    {"price": "bad"},
                    "invalid entry",
                ]
            }
        },
        stats=stats,
    )
    assert [item["description"] for item in mapped["menu_items"]] == [
        "Tea",
        "Milk",
        "Rice",
        "Egg",
        "Sauce",
    ]
    assert mapped["menu_items"][1]["amount"] == Decimal("12000")
    assert stats["items_skipped"] == 2
    assert DOCUMENT_ADAPTER.validate_json(canonical_target(mapped)).total is None


@pytest.mark.parametrize("price", ["bad", "1.2.3", "NaN", "Infinity"])
def test_cord_unparseable_price(price):
    with pytest.raises(ValueError):
        cord_receipt({"gt_parse": {"menu": {"nm": "Tea", "price": price}}})


def test_cord_unnamed_dict_is_one_skipped_item():
    from collections import Counter

    stats = Counter()
    with pytest.raises(ValueError, match="No receipt items"):
        cord_receipt(
            {"gt_parse": {"menu": {"num": "901016", "price": "60.000"}}}, stats=stats
        )
    assert stats["items_skipped"] == 1


def test_cord_split_logging(tmp_path, monkeypatch, caplog):
    import logging
    import sys
    from types import SimpleNamespace

    synth = tmp_path / "synth"
    synth.mkdir()
    (synth / "splits.json").write_text(json.dumps({"train": [], "val": [], "test": []}))
    examples = [
        {
            "ground_truth": {"gt_parse": {"menu": [{"nm": "Tea"}, "skip"]}},
            "image": Image.new("RGB", (8, 8)),
        },
        {"ground_truth": {"gt_parse": {"menu": {"nm": "Tea", "price": "bad"}}}},
        {"ground_truth": {"gt_parse": {"menu": {"price": "60.000"}}}},
    ]
    monkeypatch.setitem(
        sys.modules,
        "datasets",
        SimpleNamespace(load_dataset=lambda *args, **kwargs: examples),
    )
    with caplog.at_level(logging.INFO, logger="train.build_dataset"):
        splits = build_dataset(synth, tmp_path / "out", cord=True)
    assert len(splits["test_cord"]) == 1
    for source in ("train", "validation", "test"):
        message = next(
            record.message
            for record in caplog.records
            if record.message.startswith(f"CORD {source}:")
        )
        assert "accepted=1 mapping_failed=2" in message
        assert "No receipt items" in message
        assert "Unparseable CORD number" in message
        assert "items_skipped=2" in message
        for field in ("quantity", "unit_price", "amount", "subtotal", "tax", "total"):
            assert f"'{field}': 1" in message


@pytest.mark.parametrize(
    ("printed", "expected"),
    [("-60.000", "-60000"), ("(12.000)", "-12000"), ("+5.455", "5455")],
)
def test_cord_number_keeps_sign_of_grouped_amounts(printed, expected):
    from decimal import Decimal

    from train.build_dataset import cord_number

    assert cord_number(printed) == Decimal(expected)


@pytest.mark.parametrize(
    ("printed", "quantity", "expected"),
    [
        ("181,500.00", False, "181500.00"),
        ("8.500,00", False, "8500.00"),
        ("Rp. 22.000", False, "22000"),
        ("Rp.56.000", False, "56000"),
        ("40,000.", False, "40000"),
        ("*46000", False, "46000"),
        ("-", False, None),
        ("---", False, None),
        ("*2", True, "2"),
        ("1Prs", True, "1"),
        ("*4 Pcs", True, "4"),
    ],
)
def test_cord_number_formats_seen_in_rehearsal(printed, quantity, expected):
    from decimal import Decimal

    from train.build_dataset import cord_number

    result = cord_number(printed, quantity=quantity)
    assert result == (Decimal(expected) if expected is not None else None)


def test_set_max_pixels_handles_both_processor_apis():
    from types import SimpleNamespace

    from train.sft import set_max_pixels

    new_style = SimpleNamespace(size={"shortest_edge": 3136, "longest_edge": 99})
    set_max_pixels(new_style, 1_003_520)
    assert new_style.size == {"shortest_edge": 3136, "longest_edge": 1_003_520}
    old_style = SimpleNamespace(size=None, max_pixels=99)
    set_max_pixels(old_style, 1_003_520)
    assert old_style.max_pixels == 1_003_520


def test_parse_prediction_fills_omitted_doc_type():
    from train.predict import parse_prediction

    # Base models under structured decoding omitted doc_type (a schema default) in the rehearsal.
    raw = json.dumps(
        {
            "bank_name": "Rimba Bintang Bank",
            "account_holder": "Fern Brook Ridge Foods",
            "account_number_masked": "***3721",
            "period_start": "2026-01-01",
            "period_end": "2026-06-30",
            "currency": "MYR",
            "opening_balance": 206204.73,
            "closing_balance": 199706.43,
            "transactions": [
                {"date": "2026-01-04", "description": "RENT", "balance": 199706.43}
            ],
        }
    )
    parsed = json.loads(parse_prediction(raw, "statement"))
    assert parsed["doc_type"] == "statement"
    assert parsed["transactions"][0]["debit"] is None
    with pytest.raises(ValueError, match="Wrong document type"):
        parse_prediction(raw.replace("{", '{"doc_type": "invoice", ', 1), "statement")
    with pytest.raises(TypeError):
        parse_prediction("[]", "statement")
