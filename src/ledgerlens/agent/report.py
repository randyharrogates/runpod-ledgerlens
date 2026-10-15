"""Deterministic presentation data for the credit report."""

import re
from collections import Counter
from datetime import date
from decimal import Decimal

from ledgerlens.agent.findings import describe_finding

LABELS = {
    "balance_gap": "Balance gap between pages",
    "returned_cheque": "Returned cheque",
    "round_trip": "Round trip",
    "large_cash_deposit": "Large cash deposit",
    "customer_concentration": "Customer concentration",
    "unreconciled_page": "Unreconciled page",
}


def _row_ref(citation):
    """Return (page_id, row_index) from a citation dict or a "[p1:r4]" string."""
    if isinstance(citation, dict):
        return (
            (citation["page_id"], citation["row_index"])
            if citation["row_index"] >= 0
            else None
        )
    match = re.fullmatch(r"\[([A-Za-z0-9_-]+):r(\d+)\]", str(citation))
    return (match[1], int(match[2]) - 1) if match else None


def build_report(statements, invoices, ids, analysis, plan):
    """Build a bounded report from checked analysis, without model output."""

    def common(field):
        values = [getattr(s, field) for s in statements]
        return Counter(values).most_common(1)[0][0] if values else ""

    def serial(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            if "row_index" in value and "page_id" in value:
                return f"[{value['page_id']}:r{value['row_index'] + 1}]"
            return {k: serial(v) for k, v in value.items()}
        if isinstance(value, list):
            return [serial(v) for v in value]
        return value

    pages = {
        check["page_id"]: {"document": statement.model_dump(mode="json")}
        for check, statement in zip(analysis["reconciliation"], statements, strict=True)
    }
    findings = [
        {
            **finding,
            "description": finding.get("description")
            or describe_finding(
                {
                    **finding,
                    "citations": [
                        c
                        for c in finding["citations"]
                        if isinstance(c, dict) and c["row_index"] >= 0
                    ],
                },
                pages,
                common("currency"),
            ),
        }
        for finding in analysis["findings"][:50]
    ]
    for finding in findings:
        if finding.get("amount") is not None:
            continue
        if finding["type"] == "returned_cheque":
            # The agent passes cited analysis ("[p1:r4]" strings); tests pass raw dicts.
            citation = next(
                filter(None, map(_row_ref, finding.get("citations", []))), None
            )
            if citation and citation[0] in pages:
                rows = pages[citation[0]]["document"]["transactions"]
                if citation[1] < len(rows):
                    row = rows[citation[1]]
                    finding["amount"] = row.get("debit") or row.get("credit")
        elif finding["type"] == "balance_gap":
            finding["amount"] = abs(Decimal(str(finding["difference"])))
        elif finding["type"] == "unreconciled_page":
            check = next(
                (
                    r
                    for r in analysis["reconciliation"]
                    if r["page_id"] == finding.get("page_id")
                ),
                None,
            )
            if check:
                finding["amount"] = abs(Decimal(str(check["difference"])))
    a = serial({**analysis, "findings": findings})
    top = a.get("top_payer_concentration") or {}
    return {
        "type": "report",
        "title": "Credit memo",
        "subtitle": "Cash-flow review",
        "borrower": common("account_holder"),
        "bank": common("bank_name"),
        "account": common("account_number_masked"),
        "currency": common("currency"),
        "period": {
            "start": min(s.period_start for s in statements).isoformat(),
            "end": max(s.period_end for s in statements).isoformat(),
        },
        "evidence": {
            "statements": len(statements),
            "invoices": len(invoices),
            "page_ids": ids[:100],
        },
        **{k: plan.get(k, "") for k in ("audience", "length", "language")},
        "prepared": date.today().isoformat(),
        "totals": {k: str(v) for k, v in a["period_totals"].items()},
        "balances": {
            name: {"value": str(a[key]["value"]), "cites": a[key]["citations"][:3]}
            for name, key in (
                ("average", "average_balance"),
                ("minimum", "minimum_balance"),
            )
        },
        "monthly": [
            {
                "month": month,
                **{k: row[k] for k in ("inflow", "outflow", "net", "partial")},
            }
            for month, row in sorted(a["monthly"].items())[:36]
        ],
        "top_payer": {
            **{k: top.get(k) for k in ("payer", "amount", "total_inflow")},
            "share_percent": str(
                top.get("share_percent", Decimal(str(top.get("share", 0))) * 100)
            ),
            "cites": top.get("citations", [])[:3],
        }
        if top.get("payer")
        else None,
        "findings": [
            {
                "type": f["type"],
                "label": LABELS.get(
                    f["type"], f["type"].replace("_", " ").capitalize()
                ),
                "description": f.get("description", ""),
                "amount": str(f["amount"]) if f.get("amount") is not None else None,
                "cites": f.get("citations", [])[:3],
            }
            for f in a["findings"][:50]
        ],
        "reconciliation": [
            {
                "page_id": r["page_id"],
                "printed": r["printed_closing_balance"],
                "expected": r["expected_closing_balance"],
                "difference": r["difference"],
                "ok": r["reconciles"],
            }
            for r in a["reconciliation"]
        ],
    }
