"""Read-only, cited queries over extracted statements."""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from ledgerlens.agent.findings import describe_finding
from ledgerlens.analytics import analyze, cited_rows, detect_anomalies, payer, payer_key
from ledgerlens.schema import Statement


@dataclass
class ToolContext:
    """Statement pages available to a data agent."""

    statements: list[Statement]
    page_ids: list[str]
    currency: str


def _cite(citation):
    return f"[{citation['page_id']}:r{citation['row_index'] + 1}]"


def _safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def find_rows(ctx, text=None, month=None, min_amount=None, direction=None, limit=20):
    """Find rows matching all supplied filters."""
    matches = []
    threshold = Decimal(str(min_amount)) if min_amount is not None else None
    for row, citation in cited_rows(ctx.statements, ctx.page_ids):
        values = (
            [row.credit]
            if direction == "in"
            else [row.debit]
            if direction == "out"
            else [row.debit, row.credit]
        )
        if text is not None and text.casefold() not in row.description.casefold():
            continue
        if month is not None and row.date.strftime("%Y-%m") != month:
            continue
        if direction is not None and not any(
            value is not None and value > 0 for value in values
        ):
            continue
        if threshold is not None and not any(
            value is not None and value >= threshold for value in values
        ):
            continue
        matches.append({**row.model_dump(mode="json"), "cite": _cite(citation)})
    return {
        "currency": ctx.currency,
        "rows": matches[:limit],
        "count": len(matches),
        "truncated": len(matches) > limit,
    }


def monthly_totals(ctx):
    """Return computed monthly flows and the number of cited rows."""
    return {
        "currency": ctx.currency,
        "monthly": {
            month: _safe(
                {
                    **{key: bucket[key] for key in ("inflow", "outflow", "net")},
                    "citations_count": len(bucket["citations"]),
                    "cites": [_cite(c) for c in bucket["citations"]],
                }
            )
            for month, bucket in analyze(ctx.statements, ctx.page_ids)[
                "monthly"
            ].items()
        },
    }


def top_counterparties(ctx, n=5, direction="in"):
    """Rank counterparties by positive inflows or outflows."""
    groups = {}
    total = Decimal(0)
    for row, citation in cited_rows(ctx.statements, ctx.page_ids):
        value = row.credit if direction == "in" else row.debit
        if value is None or value <= 0:
            continue
        key = payer_key(row.description)
        group = groups.setdefault(
            key, {"payer": payer(row.description), "total": Decimal(0), "cites": []}
        )
        group["total"] += value
        total += value
        if len(group["cites"]) < 3:
            group["cites"].append(_cite(citation))
    ranked = sorted(groups.values(), key=lambda group: group["total"], reverse=True)
    return _safe(
        {
            "counterparties": [
                {
                    **group,
                    "share_percent": str(round(group["total"] / total * 100, 1)),
                }
                for group in ranked[:n]
            ],
            "total": total,
            "currency": ctx.currency,
            "direction": direction,
        }
    )


def flag_detail(ctx, index):
    """Explain a zero-based finding with its evidence."""
    findings = detect_anomalies(ctx.statements, ctx.page_ids)
    if not 0 <= index < len(findings):
        return {"error": "Finding index is out of range"}
    finding = {
        **findings[index],
        "citations": [c for c in findings[index]["citations"] if c["row_index"] >= 0],
    }
    pages = {
        page_id: {"document": statement.model_dump(mode="json")}
        for page_id, statement in zip(ctx.page_ids, ctx.statements, strict=True)
    }
    return _safe(
        {
            **finding,
            "description": describe_finding(finding, pages, ctx.currency),
            "cites": [_cite(c) for c in finding["citations"] if c["row_index"] >= 0],
        }
    )


def balance_on(ctx, day):
    """Return the latest printed balance on or before a date."""
    cutoff = date.fromisoformat(day)
    selected = None
    for row, citation in cited_rows(ctx.statements, ctx.page_ids):
        if (
            row.date <= cutoff
            and row.balance is not None
            and (selected is None or row.date >= selected[0].date)
        ):
            selected = (row, citation)
    if selected is None:
        return {"balance": None, "cite": None}
    row, citation = selected
    return {
        "currency": ctx.currency,
        "balance": str(row.balance),
        "date": row.date.isoformat(),
        "cite": _cite(citation),
    }


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }
    for name, description, properties, required in [
        (
            "find_rows",
            "Find matching statement rows.",
            {
                "text": {"type": "string"},
                "month": {"type": "string", "pattern": r"^\d{4}-(0[1-9]|1[0-2])$"},
                "min_amount": {"type": "number", "minimum": 0},
                "direction": {"type": "string", "enum": ["in", "out"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            [],
        ),
        ("monthly_totals", "Get monthly cash flows.", {}, []),
        (
            "top_counterparties",
            "Rank counterparties: direction 'in' = who paid the business (payers, customers; the default), 'out' = whom it paid (suppliers, payroll).",
            {
                "n": {"type": "integer", "minimum": 1, "maximum": 10},
                "direction": {"type": "string", "enum": ["in", "out"]},
            },
            [],
        ),
        (
            "flag_detail",
            "Explain a finding by zero-based index.",
            {"index": {"type": "integer", "minimum": 0}},
            ["index"],
        ),
        (
            "balance_on",
            "Get the last printed balance by date.",
            {"day": {"type": "string", "format": "date"}},
            ["day"],
        ),
    ]
]


def run_tool(ctx, name, arguments: dict | str) -> dict:
    """Validate model arguments and return plain errors for invalid calls."""
    specs = {
        spec["function"]["name"]: spec["function"]["parameters"] for spec in TOOL_SPECS
    }
    try:
        if name not in specs:
            raise ValueError("Unknown tool")
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
        spec = specs[name]
        if args.keys() - spec["properties"].keys():
            raise ValueError("Unknown argument")
        if any(key not in args for key in spec["required"]):
            raise ValueError("Missing required argument")
        for key, value in args.items():
            rule = spec["properties"][key]
            if rule["type"] == "integer":
                if (
                    type(value) is not int
                    or value < rule.get("minimum", 0)
                    or value > rule.get("maximum", float("inf"))
                ):
                    raise ValueError(f"Invalid {key}")
            elif key == "min_amount":
                if isinstance(value, bool) or not isinstance(
                    value, (str, int, float, Decimal)
                ):
                    raise ValueError("Invalid min_amount")
                number = Decimal(str(value))
                if not number.is_finite() or number < 0:
                    raise ValueError("min_amount must be non-negative and finite")
            elif not isinstance(value, str):
                raise ValueError(f"Invalid {key}")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"Invalid {key}")
            if key == "month":
                if not re.fullmatch(rule["pattern"], value):
                    raise ValueError("Invalid month; use YYYY-MM")
                date.fromisoformat(value + "-01")
            if key == "day":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("Invalid day; use YYYY-MM-DD")
                date.fromisoformat(value)
        return {
            "find_rows": find_rows,
            "monthly_totals": monthly_totals,
            "top_counterparties": top_counterparties,
            "flag_detail": flag_detail,
            "balance_on": balance_on,
        }[name](ctx, **args)
    except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
        return {"error": str(exc)}
