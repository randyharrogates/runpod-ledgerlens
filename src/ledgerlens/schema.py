"""Shared extraction schemas; monetary values stay decimal throughout."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class Transaction(BaseModel):
    """One printed statement row."""

    date: date
    description: str
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None


class Statement(BaseModel):
    """A statement page with its printed opening and closing balances."""

    doc_type: Literal["statement"] = "statement"
    bank_name: str
    account_holder: str
    account_number_masked: str
    period_start: date
    period_end: date
    currency: str
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[Transaction]


class InvoiceLine(BaseModel):
    """An invoice or receipt item."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class Invoice(BaseModel):
    """Invoice header, items and totals as printed."""

    doc_type: Literal["invoice"] = "invoice"
    issuer: str
    recipient: str
    invoice_number: str
    date: date
    currency: str
    line_items: list[InvoiceLine]
    subtotal: Decimal
    tax: Decimal
    total: Decimal


class ReceiptItem(BaseModel):
    """One printed receipt item, with absent numeric fields left null."""

    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class Receipt(BaseModel):
    """Printed fields only; null when not printed.

    CORD often omits unit price, tax and subtotal.
    """

    doc_type: Literal["receipt"] = "receipt"
    menu_items: list[ReceiptItem]
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None


Document = Annotated[Statement | Invoice | Receipt, Field(discriminator="doc_type")]
DOCUMENT_ADAPTER = TypeAdapter(Document)


# Amounts are written as short decimal strings ("206204.73"), as in the training targets. A free
# JSON number let the base 8B write "4000000.000000…" until the token limit (10 Oct evaluation).
AMOUNT = {"type": "string", "pattern": r"^-?[0-9]{1,15}(\.[0-9]{1,4})?$"}


def _bound_amounts(node):
    """Replace every number-or-string choice (pydantic's Decimal) with AMOUNT."""
    if isinstance(node, list):
        return [_bound_amounts(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: _bound_amounts(value) for key, value in node.items()}
    options = node.get("anyOf")
    if options and {"type": "number"} in options:
        rest = [o for o in options if o.get("type") not in ("number", "string")]
        del node["anyOf"]
        if rest:
            node["anyOf"] = [AMOUNT, *rest]
        else:
            node.update(AMOUNT)
    return node


def json_schema(doc_type: str) -> dict:
    """Schema for structured extraction, with amounts as bounded decimal strings."""
    models = {"statement": Statement, "invoice": Invoice, "receipt": Receipt}
    if doc_type not in models:
        raise ValueError(f"Unknown document type: {doc_type}")
    return _bound_amounts(models[doc_type].model_json_schema())
