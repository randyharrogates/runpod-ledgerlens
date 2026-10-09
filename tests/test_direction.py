"""Exact balance checks never change digits or the source document."""

from decimal import Decimal

import pytest

from ledgerlens.direction import check_direction
from ledgerlens.schema import Transaction


@pytest.mark.parametrize(
    "debit,credit,balance,destination",
    [
        ("0.10", None, "100.1", "credit"),
        (None, "0.10", "99.9", "debit"),
        ("0.10", None, "99.90", None),
        (None, "0.10", "100.10", None),
        ("0.10", None, "100.11", None),
        ("0.10", "0.10", "100.10", None),
        (None, None, "100.10", None),
        ("0.10", None, None, None),
    ],
)
def test_direction(ledger, debit, credit, balance, destination):
    statement = ledger.statement.model_copy(deep=True)
    statement.opening_balance = Decimal("100")
    statement.transactions = [
        Transaction(
            date="2026-01-01",
            description="Payment",
            debit=debit,
            credit=credit,
            balance=balance,
        )
    ]
    before = statement.model_dump()
    checked, fixes = check_direction(statement)
    assert statement.model_dump() == before
    assert checked is not statement
    assert checked.transactions[0] is not statement.transactions[0]
    if destination:
        source = "debit" if destination == "credit" else "credit"
        assert fixes == [
            {"row": 0, "from": source, "to": destination, "amount": "0.10"}
        ]
        assert getattr(checked.transactions[0], destination) == Decimal("0.1")
        assert getattr(checked.transactions[0], source) is None
    else:
        assert fixes == []
        assert checked.model_dump() == before


def test_previous_extracted_balance(ledger):
    statement = ledger.statement.model_copy(deep=True)
    statement.opening_balance = Decimal("100")
    statement.transactions = [
        Transaction(date="2026-01-01", description="First", debit="10", balance="110"),
        Transaction(date="2026-01-02", description="Second", debit="10", balance="120"),
        Transaction(date="2026-01-03", description="Missing", debit="10"),
        Transaction(date="2026-01-04", description="Skip", debit="10", balance="130"),
    ]
    checked, fixes = check_direction(statement)
    assert [fix["row"] for fix in fixes] == [0, 1]
    assert checked.transactions[3].debit == Decimal("10")
