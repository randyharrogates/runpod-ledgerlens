"""document layouts with two statement styles reserved for test."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ledgerlens.schema import Invoice, Statement


@dataclass(frozen=True)
class Layout:
    """Formatting choices and split restriction for a template."""

    id: str
    bank_name: str
    date_format: str
    columns: tuple[str, ...]
    held_out: bool = False
    suffixes: bool = False


LAYOUTS = {
    "stmt_a": Layout(
        "stmt_a",
        "Moonreed Bank",
        "%d/%m/%Y",
        ("date", "description", "debit", "credit", "balance"),
    ),
    "stmt_b": Layout(
        "stmt_b",
        "Cloudpetal Bank",
        "%d %b %Y",
        ("date", "description", "amount", "balance"),
    ),
    "stmt_c": Layout(
        "stmt_c",
        "Rimba Bintang Bank",
        "%Y-%m-%d",
        ("description", "date", "credit", "debit", "balance"),
    ),
    "stmt_d": Layout(
        "stmt_d",
        "星露 Bank",
        "%d/%m/%Y",
        ("date", "balance", "debit", "credit", "description"),
        suffixes=True,
    ),
    "stmt_e": Layout(
        "stmt_e",
        "Ferncomet Bank",
        "%d %b %Y",
        ("date", "amount", "description", "balance"),
        held_out=True,
        suffixes=True,
    ),
    "stmt_f": Layout(
        "stmt_f",
        "Senja Awan Bank",
        "%Y-%m-%d",
        ("description", "date", "balance", "credit", "debit"),
        held_out=True,
        suffixes=True,
    ),
}
INVOICE_LAYOUTS = ("invoice_a", "invoice_b")


def format_date(value: date, pattern: str) -> str:
    """Format dates, using unpadded days in the textual month style."""
    text = value.strftime(pattern)
    return text.lstrip("0") if "%b" in pattern else text


def money(value: Decimal | None, suffixes: bool = False) -> str:
    """Format money with grouping and optional credit/debit suffixes."""
    if value is None:
        return ""
    return (
        f"{abs(value):,.2f} {'DR' if value < 0 else 'CR'}"
        if suffixes
        else f"{value:,.2f}"
    )


def render_html(document: Statement | Invoice, layout_id: str) -> str:
    """Render one document using packaged, autoescaped templates."""
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent),
        autoescape=select_autoescape(["html"]),
    )
    env.filters.update(money=money, printed_date=format_date)
    if isinstance(document, Statement):
        layout = LAYOUTS[layout_id]
        rows = []
        for row in document.transactions:
            values = {
                "date": format_date(row.date, layout.date_format),
                "description": row.description,
                "debit": money(row.debit),
                "credit": money(row.credit),
                "balance": money(row.balance, layout.suffixes),
                "amount": money(
                    (row.credit or Decimal(0)) - (row.debit or Decimal(0)),
                    layout.suffixes,
                ),
            }
            rows.append(values)
        return env.get_template(f"{layout_id}.html").render(
            doc=document,
            layout=layout,
            rows=rows,
            statement_heading={
                "SGD": "STATEMENT OF ACCOUNT",
                "MYR": "PENYATA AKAUN / STATEMENT",
                "IDR": "MUTASI REKENING",
            }.get(document.currency, "STATEMENT OF ACCOUNT"),
        )
    if layout_id not in INVOICE_LAYOUTS:
        raise ValueError(f"Unknown invoice layout: {layout_id}")
    from ledgerlens.datagen.invoice import TAX

    country = {"SGD": "SG", "MYR": "MY", "IDR": "ID"}[document.currency]
    name, rate = TAX[country]
    return env.get_template(f"{layout_id}.html").render(
        doc=document,
        tax_label=f"{name} {rate * 100:.0f}%",
        printed_date=format_date(
            document.date, "%d %b %Y" if layout_id == "invoice_b" else "%Y-%m-%d"
        ),
    )
