"""Scorer sanity checks, perturbations, normalization and invalid outputs."""

import json
import shutil
from datetime import date, timedelta
from decimal import Decimal

import pytest

from ledgerlens.datagen.cli import generate_dataset
from ledgerlens.eval.score import (
    normalize_date,
    normalize_money,
    score_batch,
    score_document,
    write_markdown,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("$1,234.50", "1234.50"),
        ("SGD 1,234.50", "1234.50"),
        ("S$1,234.50", "1234.50"),
        ("RM 1,234.50", "1234.50"),
        ("Rp 1,234.50", "1234.50"),
        ("(1,234.50)", "-1234.50"),
        ("1,234.50 CR", "1234.50"),
        ("1,234.50 DR", "-1234.50"),
        ("-12.50", "-12.50"),
        (0, "0"),
        (None, None),
        ("", None),
    ],
)
def test_money_normalization(value, expected):
    assert normalize_money(value) == (
        Decimal(expected) if expected is not None else None
    )


@pytest.mark.parametrize(
    "value", ["05/10/2026", "5 Oct 2026", "2026-10-05", "5 October 2026", "05-10-2026"]
)
def test_date_normalization(value):
    assert normalize_date(value) == date(2026, 10, 5)


def test_invalid_normalization():
    with pytest.raises(ValueError):
        normalize_money("unknown")
    with pytest.raises(ValueError):
        normalize_money("NaN")
    with pytest.raises(ValueError):
        normalize_date("31/02/2026")


def test_gold_and_perturbed_rows(ledger):
    gold = ledger.statement
    perfect = score_document(gold, gold)
    assert perfect["header_accuracy"] == 1.0
    assert perfect["rows"]["f1"] == 1.0
    assert perfect["page_reconciles"] is True
    amount_change = gold.model_copy(deep=True)
    amount_change.transactions[0].credit += Decimal("0.01")
    score = score_document(amount_change, gold)
    assert score["rows"]["matched"] == len(gold.transactions) - 1
    assert score["rows"]["f1"] < 1
    assert score["page_reconciles"] is False
    dropped = gold.model_copy(deep=True)
    dropped.transactions.pop(0)
    result = score_document(dropped, gold)
    assert result["rows"]["precision"] == 1
    assert result["rows"]["recall"] < 1
    swapped = gold.model_copy(deep=True)
    other = next(
        i
        for i, row in enumerate(swapped.transactions)
        if row.date != swapped.transactions[0].date
    )
    swapped.transactions[0].date, swapped.transactions[other].date = (
        swapped.transactions[other].date,
        swapped.transactions[0].date,
    )
    assert (
        score_document(swapped, gold)["rows"]["matched"] == len(gold.transactions) - 2
    )
    changed_header = gold.model_copy(update={"account_holder": "Wrong holder"})
    assert score_document(changed_header, gold)["header_accuracy"] < 1


def test_duplicate_rows_are_consumed_once(ledger):
    gold = ledger.statement.model_copy(deep=True)
    gold.transactions = [gold.transactions[0], gold.transactions[0]]
    prediction = gold.model_copy(deep=True)
    prediction.transactions.pop()
    score = score_document(prediction, gold)
    assert score["rows"]["precision"] == 1
    assert score["rows"]["recall"] == 0.5


def test_normalized_document(ledger):
    payload = ledger.statement.model_dump(mode="json")
    payload["opening_balance"] = f"SGD {ledger.statement.opening_balance:,.2f}"
    payload["period_start"] = "01/01/2026"
    assert score_document(payload, ledger.statement)["header_accuracy"] == 1


def test_batch_gold_and_invalid_predictions(tmp_path):
    gold_dir = tmp_path / "gold"
    pred_dir = tmp_path / "pred"
    generate_dataset(gold_dir, 3, 7, no_render=True)
    shutil.copytree(gold_dir, pred_dir)
    report = score_batch(pred_dir, gold_dir)
    for metric in (
        "header_accuracy",
        "row_precision",
        "row_recall",
        "row_f1",
        "valid_json_rate",
        "valid_document_rate",
        "reconciliation_accuracy",
        "anomaly_recall",
    ):
        assert report[metric] == 1.0, (metric, report["anomalies"])
    assert (
        report["page_reconciliation_rate"] < 1
    )  # Printed errors are part of the gold.
    out = tmp_path / "report.md"
    write_markdown(report, out)
    assert "| Row F1 (raw model) | 1.0000 |" in out.read_text()
    pages = sorted(
        path
        for path in pred_dir.glob("*_p*.json")
        if not path.name.endswith(".meta.json")
    )
    pages[0].write_text("invalid JSON")
    pages[1].unlink()
    pages[2].write_text(json.dumps({"doc_type": "statement"}))
    broken = score_batch(pred_dir, gold_dir)
    assert broken["valid_json_rate"] == (report["documents"] - 2) / report["documents"]
    assert (
        broken["valid_document_rate"] == (report["documents"] - 3) / report["documents"]
    )
    assert broken["row_f1"] < 1
    assert broken["header_accuracy"] < 1


def test_anomaly_recall_drops_when_returned_cheque_removed(tmp_path):
    gold_dir = tmp_path / "gold"
    pred_dir = tmp_path / "pred"
    generate_dataset(gold_dir, 30, 7, no_render=True)
    shutil.copytree(gold_dir, pred_dir)
    for path in pred_dir.glob("*_p*.json"):
        if path.name.endswith(".meta.json"):
            continue
        payload = json.loads(path.read_text())
        payload["transactions"] = [
            row
            for row in payload["transactions"]
            if not any(
                word in row["description"].upper()
                for word in ("RTN", "RETURN", "退票", "DIKEMBALIKAN")
            )
        ]
        path.write_text(json.dumps(payload))
    report = score_batch(pred_dir, gold_dir)
    assert report["anomalies"]["by_type"]["returned_cheque"]["detected"] == 0
    assert report["anomaly_recall"] < 1


def test_wrong_document_type(ledger):
    receipt = {
        "doc_type": "receipt",
        "menu_items": [],
        "subtotal": "0",
        "tax": "0",
        "total": "0",
    }
    assert score_document(receipt, ledger.statement)["rows"]["f1"] == 0


def test_swapped_date_without_matching_pair(ledger):
    predicted = ledger.statement.model_copy(deep=True)
    predicted.transactions[0].date += timedelta(days=1)
    assert score_document(predicted, ledger.statement)["rows"]["recall"] < 1


def test_false_positives_on_clean_business(ledger):
    from ledgerlens.eval.score import anomaly_recall

    statement = ledger.statement.model_copy(deep=True)
    statement.transactions = [
        statement.transactions[0].model_copy(
            update={
                "description": "CHQ RETURN - NO FUNDS",
                "credit": None,
                "debit": Decimal("37.21"),
            }
        )
    ]
    statement.closing_balance = statement.opening_balance - Decimal("37.21")
    result = anomaly_recall([statement], ["clean"], [])
    assert result["precision"] == 0
    assert result["by_type"]["returned_cheque"]["false_positives"] == 1
    assert result["by_type"]["returned_cheque"]["precision"] == 0


def test_direction_checked_aggregation(tmp_path, ledger):
    gold_dir, pred_dir = tmp_path / "gold", tmp_path / "pred"
    gold_dir.mkdir()
    pred_dir.mkdir()
    gold = ledger.statement
    swapped = gold.model_copy(deep=True)
    row = swapped.transactions[0]
    row.debit, row.credit = row.credit, row.debit
    for name in ("valid", "missing", "invalid"):
        (gold_dir / f"{name}.json").write_text(gold.model_dump_json())
    (pred_dir / "valid.json").write_text(swapped.model_dump_json())
    (pred_dir / "invalid.json").write_text("{}")
    report = score_batch(pred_dir, gold_dir)
    n = len(gold.transactions)
    assert report["pages"]["valid"]["rows"]["matched"] == n - 1
    assert report["pages"]["valid"]["rows_direction_checked"]["matched"] == n
    assert report["direction_fixes"] == 1
    checked = report["rows_direction_checked"]
    assert checked["matched"] == n
    assert checked["precision"] == 1
    assert checked["recall"] == pytest.approx(1 / 3)
    assert checked["f1"] == pytest.approx(0.5)
    for name in ("missing", "invalid"):
        assert report["pages"][name]["rows_direction_checked"]["f1"] == 0
