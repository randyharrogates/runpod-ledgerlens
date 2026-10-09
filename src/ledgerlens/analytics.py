"""Decimal cash-flow calculations and cited anomaly rules, without an LLM."""

import re
from collections import defaultdict
from decimal import Decimal
from statistics import median
from typing import TypedDict

from ledgerlens.schema import Statement, Transaction

ZERO = Decimal(0)
CENT = Decimal("0.01")


class Citation(TypedDict):
    """A zero-based row reference in a named page."""

    page_id: str
    row_index: int


def payer(description: str) -> str:
    """Extract a counterparty from pipe-separated descriptions or plain text."""
    parts = description.split("|")
    if len(parts) > 1:
        return parts[1].strip().casefold()
    match = re.search(
        r"(?:FROM|TO)\s+(.+?)(?:\s+(?:REF|REFERENCE)\s+\S+)?$", description, re.I
    )
    return (
        match.group(1).strip().casefold() if match else description.strip().casefold()
    )


def cited_rows(
    statements: list[Statement], page_ids: list[str] | None = None
) -> list[tuple[Transaction, Citation]]:
    """Flatten pages without losing their row references."""
    ids = (
        page_ids
        if page_ids is not None
        else [f"page_{i}" for i in range(len(statements))]
    )
    if len(ids) != len(statements) or len(set(ids)) != len(ids):
        raise ValueError("page_ids must contain one unique id per statement")
    return [
        (row, {"page_id": ids[i], "row_index": j})
        for i, statement in enumerate(statements)
        for j, row in enumerate(statement.transactions)
    ]


def reconciliation_checks(
    statements: list[Statement], page_ids: list[str] | None = None
) -> list[dict]:
    """Check each printed page to the cent, including empty pages."""
    cited_rows(statements, page_ids)
    ids = (
        page_ids
        if page_ids is not None
        else [f"page_{i}" for i in range(len(statements))]
    )
    checks = []
    for page_id, statement in zip(ids, statements, strict=True):
        expected = statement.opening_balance + sum(
            (
                (row.credit or ZERO) - (row.debit or ZERO)
                for row in statement.transactions
            ),
            ZERO,
        )
        difference = expected.quantize(CENT) - statement.closing_balance.quantize(CENT)
        checks.append(
            {
                "page_id": page_id,
                "reconciles": difference == ZERO,
                "expected_closing_balance": expected,
                "printed_closing_balance": statement.closing_balance,
                "difference": difference,
                "citations": [
                    {"page_id": page_id, "row_index": len(statement.transactions) - 1}
                ],
            }
        )
    return checks


def reconciliation_failures(
    statements: list[Statement], page_ids: list[str] | None = None
) -> list[dict]:
    """Return the pages whose printed closing balances do not add up."""
    return [
        check
        for check in reconciliation_checks(statements, page_ids)
        if not check["reconciles"]
    ]


def detect_anomalies(
    statements: list[Statement], page_ids: list[str] | None = None
) -> list[dict]:
    """Find returned cheques, round trips, cash spikes and concentration."""
    rows = cited_rows(statements, page_ids)
    findings = []
    keywords = ("RTN", "RETURN", "RETURNED", "退票", "DIKEMBALIKAN", "CHQ RET")
    for row, citation in rows:
        if any(keyword in row.description.upper() for keyword in keywords):
            findings.append({"type": "returned_cheque", "citations": [citation]})
    used_incoming: set[int] = set()
    for outgoing, outgoing_citation in rows:
        if not outgoing.debit:
            continue
        for i, (incoming, incoming_citation) in enumerate(rows):
            gap = (incoming.date - outgoing.date).days
            if (
                i not in used_incoming
                and incoming.credit is not None
                and outgoing.debit * Decimal("0.99")
                <= incoming.credit
                <= outgoing.debit
                and 0 < gap <= 10
                and payer(incoming.description) == payer(outgoing.description)
            ):
                findings.append(
                    {
                        "type": "round_trip",
                        "amount": outgoing.debit,
                        "citations": [outgoing_citation, incoming_citation],
                    }
                )
                used_incoming.add(i)
                break
    credits = [row.credit for row, _ in rows if row.credit and row.credit > ZERO]
    reference = median(credits) if credits else ZERO
    for row, citation in rows:
        if (
            row.credit
            and reference
            and row.credit > 3 * reference
            and any(
                word in row.description.upper() for word in ("CASH", "TUNAI", "现金")
            )
        ):
            findings.append(
                {
                    "type": "large_cash_deposit",
                    "amount": row.credit,
                    "median_inflow": reference,
                    "citations": [citation],
                }
            )
    concentration = top_payer_concentration(statements, page_ids)
    if concentration["share"] > Decimal("0.5"):
        findings.append({"type": "customer_concentration", **concentration})
    for check in reconciliation_failures(statements, page_ids):
        findings.append({"type": "unreconciled_page", **check})
    return findings


def top_payer_concentration(
    statements: list[Statement], page_ids: list[str] | None = None
) -> dict:
    """Measure the largest payer against all positive inflows in the period."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    citations: dict[str, list[Citation]] = defaultdict(list)
    total = ZERO
    for row, citation in cited_rows(statements, page_ids):
        if row.credit and row.credit > ZERO:
            name = payer(row.description)
            totals[name] += row.credit
            total += row.credit
            citations[name].append(citation)
    if not totals:
        return {
            "payer": None,
            "amount": ZERO,
            "total_inflow": ZERO,
            "share": ZERO,
            "citations": [],
        }
    name = max(totals, key=lambda key: totals[key])
    return {
        "payer": name,
        "amount": totals[name],
        "total_inflow": total,
        "share": totals[name] / total,
        "citations": citations[name],
    }


def analyze(statements: list[Statement], page_ids: list[str] | None = None) -> dict:
    """Compute cited monthly flows and observed running-balance statistics."""
    if len({statement.currency for statement in statements}) > 1:
        raise ValueError(
            "Analyze one currency at a time; no exchange rates are assumed"
        )
    rows = cited_rows(statements, page_ids)
    monthly: dict[str, dict] = {}
    for row, citation in rows:
        month = row.date.strftime("%Y-%m")
        bucket = monthly.setdefault(
            month, {"inflow": ZERO, "outflow": ZERO, "net": ZERO, "citations": []}
        )
        bucket["inflow"] += row.credit or ZERO
        bucket["outflow"] += row.debit or ZERO
        bucket["net"] = bucket["inflow"] - bucket["outflow"]
        bucket["citations"].append(citation)
    balances = [
        (row.balance, citation) for row, citation in rows if row.balance is not None
    ]
    average = (
        sum((value for value, _ in balances), ZERO) / len(balances)
        if balances
        else None
    )
    minimum = min((value for value, _ in balances), default=None)
    return {
        "monthly": dict(sorted(monthly.items())),
        "average_balance": {
            "value": average,
            "citations": [citation for _, citation in balances],
        },
        "minimum_balance": {
            "value": minimum,
            "citations": [citation for value, citation in balances if value == minimum],
        },
        "balance_basis": "observed transaction running balances",
        "top_payer_concentration": top_payer_concentration(statements, page_ids),
        "reconciliation": reconciliation_checks(statements, page_ids),
        "findings": detect_anomalies(statements, page_ids),
    }
