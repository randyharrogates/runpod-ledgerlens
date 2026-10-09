"""Schema validation, decimal serialization and discriminator tests."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ledgerlens.schema import (
    DOCUMENT_ADAPTER,
    Invoice,
    InvoiceLine,
    Receipt,
    json_schema,
)


def test_statement_round_trip(ledger):
    statement = ledger.statement
    restored = DOCUMENT_ADAPTER.validate_json(statement.model_dump_json())
    assert restored == statement
    assert isinstance(restored.transactions[0].credit, Decimal)
    assert json_schema("statement")["properties"]["doc_type"]["const"] == "statement"


def test_invoice_and_receipt_round_trip():
    line = InvoiceLine(
        description="面包",
        quantity=Decimal(2),
        unit_price=Decimal("3.25"),
        amount=Decimal("6.50"),
    )
    totals = {
        "subtotal": Decimal("6.50"),
        "tax": Decimal("0.50"),
        "total": Decimal("7.00"),
    }
    documents = [
        Invoice(
            issuer="Fictional 云芽",
            recipient="Fictional Senja",
            invoice_number="I-1",
            date=date(2026, 10, 5),
            currency="SGD",
            line_items=[line],
            **totals,
        ),
        Receipt(menu_items=[line.model_dump()], **totals),
    ]
    for document in documents:
        assert DOCUMENT_ADAPTER.validate_json(document.model_dump_json()) == document
        assert json_schema(document.doc_type)["type"] == "object"
    with pytest.raises(ValidationError):
        DOCUMENT_ADAPTER.validate_python({"doc_type": "unknown"})
    with pytest.raises(ValueError):
        json_schema("unknown")


def test_optional_receipt_fields_and_schema():
    from ledgerlens.schema import ReceiptItem

    receipt = Receipt(menu_items=[ReceiptItem(description="Tea")])
    assert receipt.subtotal is receipt.tax is receipt.total is None
    item = receipt.menu_items[0]
    assert item.quantity is item.unit_price is item.amount is None
    assert DOCUMENT_ADAPTER.validate_json(receipt.model_dump_json()) == receipt
    schema = json_schema("receipt")
    assert schema["required"] == ["menu_items"]
    assert schema["$defs"]["ReceiptItem"]["required"] == ["description"]
    for field in ("subtotal", "tax", "total"):
        assert {"type": "null"} in schema["properties"][field]["anyOf"]
    with pytest.raises(ValidationError):
        InvoiceLine(description="Tea")
