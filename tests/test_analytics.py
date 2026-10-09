"""Cited cash flow and planted-anomaly detection on known ground truth."""

from decimal import Decimal

import pytest

from ledgerlens.analytics import analyze, detect_anomalies, reconciliation_failures
from ledgerlens.datagen.business import generate_businesses
from ledgerlens.datagen.ledger import generate_ledger
from ledgerlens.datagen.paginate import paginate


@pytest.mark.parametrize("seed", [0, 7, 42, 100])
def test_detects_all_planted_anomalies(seed):
    ledger = generate_ledger(generate_businesses(1, seed)[0], seed)
    pages = paginate(ledger, "biz", rows_per_page=8)
    statements = [page.statement for page in pages]
    ids = [page.id for page in pages]
    findings = detect_anomalies(statements, ids)
    assert {finding["type"] for finding in findings} == {
        label["type"] for label in ledger.labels
    }
    for finding in findings:
        assert finding["citations"]
        for citation in finding["citations"]:
            page = pages[ids.index(citation["page_id"])]
            assert 0 <= citation["row_index"] < len(page.statement.transactions)
    failures = reconciliation_failures(statements, ids)
    expected = [
        page.id
        for page in pages
        if page.metadata["true_closing_balance"]
        != page.metadata["printed_closing_balance"]
    ]
    assert [failure["page_id"] for failure in failures] == expected


def test_cash_flow_and_balance_statistics(pages, ledger):
    report = analyze([page.statement for page in pages], [page.id for page in pages])
    rows = ledger.statement.transactions
    assert len(report["monthly"]) == 6
    for month, bucket in report["monthly"].items():
        selected = [row for row in rows if row.date.strftime("%Y-%m") == month]
        assert bucket["inflow"] == sum(
            (row.credit or Decimal(0) for row in selected), Decimal(0)
        )
        assert bucket["outflow"] == sum(
            (row.debit or Decimal(0) for row in selected), Decimal(0)
        )
        assert bucket["net"] == bucket["inflow"] - bucket["outflow"]
        assert len(bucket["citations"]) == len(selected)
    assert report["minimum_balance"]["value"] == min(row.balance for row in rows)
    assert report["average_balance"]["value"] == sum(
        (row.balance for row in rows), Decimal(0)
    ) / len(rows)
    assert (report["top_payer_concentration"]["share"] > Decimal("0.5")) == any(
        label["type"] == "customer_concentration" for label in ledger.labels
    )


def test_empty_and_currency_validation(ledger):
    assert analyze([])["monthly"] == {}
    assert detect_anomalies([]) == []
    other = ledger.statement.model_copy(update={"currency": "USD"})
    with pytest.raises(ValueError, match="one currency"):
        analyze([ledger.statement, other])
    with pytest.raises(ValueError, match="unique id"):
        analyze([ledger.statement], [])


@pytest.mark.parametrize(
    "ratio, gap, expected",
    [
        ("0.99", 10, True),
        ("1", 3, True),
        ("0.989", 5, False),
        ("1.001", 5, False),
        ("1", 11, False),
    ],
)
def test_round_trip_uses_flows_and_counterparties(ledger, ratio, gap, expected):
    from datetime import date, timedelta

    from ledgerlens.schema import Transaction

    start = date(2026, 2, 1)
    statement = ledger.statement.model_copy(deep=True)
    statement.transactions = [
        Transaction(
            date=start,
            description="GIRO TO Cedar Cove Holdings REF 137",
            debit=Decimal("123.45"),
        ),
        Transaction(
            date=start + timedelta(days=gap),
            description="DuitNow FROM Cedar Cove Holdings REF 923",
            credit=Decimal("123.45") * Decimal(ratio),
        ),
    ]
    assert (
        any(f["type"] == "round_trip" for f in detect_anomalies([statement]))
        == expected
    )
