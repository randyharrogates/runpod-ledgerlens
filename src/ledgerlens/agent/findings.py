"""Evidence-specific finding descriptions."""

from datetime import date
from decimal import Decimal

from ledgerlens.analytics import payer, payer_key


def amount(value, currency):
    """Format computed amounts consistently in explanations and the offline memo."""
    return f"{currency} {Decimal(str(value or 0)):,.{0 if currency == 'IDR' else 2}f}"


def describe_finding(finding, pages, currency):
    """Turn deterministic rules into evidence-specific explanations."""
    rows = [
        pages[c["page_id"]]["document"]["transactions"][c["row_index"]]
        for c in finding["citations"]
    ]
    row = rows[0] if rows else {}

    def when(row):
        return date.fromisoformat(row["date"]).strftime("%-d %b")

    kind = finding["type"]
    if kind == "returned_cheque":
        return f"Returned cheque · {amount(row.get('debit') or row.get('credit'), currency)} · {when(row)} · {row['description']}"
    if kind == "round_trip":
        name = next(
            (
                payer(transaction["description"])
                for page in pages.values()
                for transaction in (page.get("document") or {}).get("transactions", [])
                if payer_key(transaction["description"])
                == payer_key(row["description"])
            ),
            payer(row["description"]),
        )
        return f"Round trip · {amount(finding['amount'], currency)} out to {name} on {when(row)}, back on {when(rows[1])}"
    if kind == "large_cash_deposit":
        ratio = Decimal(finding["amount"]) / Decimal(finding["median_inflow"])
        return f"Large cash deposit · {amount(finding['amount'], currency)} · {when(row)} · {row['description']} · {ratio:.1f}× the median credit"
    if kind == "unreconciled_page":
        return f"Unreconciled page · printed closing balance off by {amount(abs(Decimal(finding['difference'])), currency)}"
    if kind == "customer_concentration":
        return f"Customer concentration · {finding['payer']} is {Decimal(finding['share']) * 100:.0f}% of inflows"
    return kind.replace("_", " ").capitalize()
