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


class Receipt(BaseModel):
    """Receipt menu items and totals, including CORD-style receipts."""

    doc_type: Literal["receipt"] = "receipt"
    menu_items: list[InvoiceLine]
    subtotal: Decimal
    tax: Decimal
    total: Decimal


Document = Annotated[Statement | Invoice | Receipt, Field(discriminator="doc_type")]
DOCUMENT_ADAPTER = TypeAdapter(Document)


def json_schema(doc_type: str) -> dict:
    """Return a document schema for future structured extraction."""
    models = {"statement": Statement, "invoice": Invoice, "receipt": Receipt}
    if doc_type not in models:
        raise ValueError(f"Unknown document type: {doc_type}")
    return models[doc_type].model_json_schema()
