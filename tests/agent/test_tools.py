"""Small statement queries with exact source references."""

import json
from decimal import Decimal

import pytest

from ledgerlens.agent.findings import amount, describe_finding
from ledgerlens.agent.tools import (
    TOOL_SPECS,
    ToolContext,
    balance_on,
    find_rows,
    flag_detail,
    monthly_totals,
    run_tool,
    top_counterparties,
)
from ledgerlens.schema import Statement


@pytest.fixture
def ctx():
    statement = Statement(
        bank_name="Cedar Bank",
        account_holder="Cedar Foods",
        account_number_masked="***1",
        period_start="2026-01-01",
        period_end="2026-02-28",
        currency="SGD",
        opening_balance="0",
        closing_balance="190",
        transactions=[
            {
                "date": "2026-01-01",
                "description": "FROM Cedar Foods",
                "credit": "100",
                "balance": "100",
            },
            {
                "date": "2026-01-03",
                "description": "TO Cedar Foods",
                "debit": "20",
                "balance": "80",
            },
            {
                "date": "2026-01-03",
                "description": "RTN cheque",
                "debit": "10",
                "balance": "70",
            },
            {
                "date": "2026-02-01",
                "description": "FAST | cedar foods | ref",
                "credit": "50",
                "balance": "120",
            },
            {
                "date": "2026-02-02",
                "description": "FROM Birch Goods",
                "credit": "70",
                "balance": "190",
            },
            {"date": "2026-02-03", "description": "No balance"},
        ],
    )
    return ToolContext([statement], ["p1"], "SGD")


def test_find_rows(ctx):
    result = find_rows(
        ctx, text="CEDAR", month="2026-01", min_amount="90", direction="in"
    )
    assert result["count"] == 1
    assert result["rows"][0]["cite"] == "[p1:r1]"
    assert result["rows"][0]["credit"] == "100"
    assert not result["truncated"]
    assert (
        find_rows(ctx, direction="out", min_amount="20")["rows"][0]["cite"] == "[p1:r2]"
    )
    limited = find_rows(ctx, limit=2)
    assert limited["count"] == 6 and limited["truncated"]
    assert find_rows(ctx, text="missing")["rows"] == []
    json.dumps(result)


def test_monthly_totals(ctx):
    monthly = monthly_totals(ctx)["monthly"]
    assert monthly["2026-01"] == {
        "inflow": "100",
        "outflow": "30",
        "net": "70",
        "citations_count": 3,
        "cites": ["[p1:r1]", "[p1:r2]", "[p1:r3]"],
    }
    assert monthly["2026-02"]["net"] == "120"


def test_counterparties(ctx):
    result = top_counterparties(ctx)
    first = result["counterparties"][0]
    assert first["payer"] == "Cedar Foods"
    assert first["total"] == "150"
    assert first["share_percent"] == "68.2"
    assert result["currency"] == ctx.currency and result["direction"] == "in"
    assert first["cites"] == ["[p1:r1]", "[p1:r4]"]
    assert (
        top_counterparties(ctx, n=1, direction="out")["counterparties"][0]["total"]
        == "20"
    )
    json.dumps(result)


def test_balance_on(ctx):
    assert balance_on(ctx, "2025-12-31")["balance"] is None
    assert balance_on(ctx, "2026-01-03") == {
        "currency": ctx.currency,
        "balance": "70",
        "date": "2026-01-03",
        "cite": "[p1:r3]",
    }
    assert balance_on(ctx, "2026-02-10")["cite"] == "[p1:r5]"
    ctx.statements.reverse()
    ctx.statements.append(
        ctx.statements[0].model_copy(
            update={"transactions": [ctx.statements[0].transactions[0]]}
        )
    )
    ctx.page_ids.append("p2")
    assert balance_on(ctx, "2026-02-10")["cite"] == "[p1:r5]"


def test_flags_and_formatting(ctx):
    result = flag_detail(ctx, 0)
    assert result["type"] == "returned_cheque"
    assert result["cites"] == ["[p1:r3]"]
    assert "SGD 10.00" in result["description"]
    assert "error" in flag_detail(ctx, -1)
    assert "error" in flag_detail(ctx, 100)
    assert amount("1234.56", "IDR") == "IDR 1,235"
    assert amount(None, "SGD") == "SGD 0.00"
    pages = {"p1": {"document": ctx.statements[0].model_dump(mode="json")}}
    for finding, expected in [
        (
            {
                "type": "round_trip",
                "amount": "20",
                "citations": [
                    {"page_id": "p1", "row_index": 1},
                    {"page_id": "p1", "row_index": 3},
                ],
            },
            "out to Cedar Foods",
        ),
        (
            {
                "type": "large_cash_deposit",
                "amount": "100",
                "median_inflow": "20",
                "citations": [{"page_id": "p1", "row_index": 0}],
            },
            "5.0×",
        ),
        (
            {"type": "unreconciled_page", "difference": "-3", "citations": []},
            "SGD 3.00",
        ),
        (
            {
                "type": "customer_concentration",
                "payer": "Cedar",
                "share": "0.75",
                "citations": [],
            },
            "75%",
        ),
        ({"type": "other_flag", "citations": []}, "Other flag"),
    ]:
        assert expected in describe_finding(finding, pages, "SGD")


@pytest.mark.parametrize(
    "name,args",
    [
        ("unknown", {}),
        ("find_rows", "{"),
        ("find_rows", "[]"),
        ("find_rows", {"extra": 1}),
        ("find_rows", {"text": 2}),
        ("find_rows", {"month": "2026-13"}),
        ("find_rows", {"month": "2026-1"}),
        ("find_rows", {"month": "0000-01"}),
        ("find_rows", {"min_amount": "-1"}),
        ("find_rows", {"min_amount": "NaN"}),
        ("find_rows", {"min_amount": True}),
        ("find_rows", {"min_amount": []}),
        ("find_rows", {"min_amount": "oops"}),
        ("find_rows", {"direction": "both"}),
        ("find_rows", {"limit": 0}),
        ("find_rows", {"limit": 51}),
        ("find_rows", {"limit": True}),
        ("top_counterparties", {"n": 0}),
        ("top_counterparties", {"n": 11}),
        ("top_counterparties", {"n": 1.5}),
        ("balance_on", {}),
        ("balance_on", {"day": "2026-02-30"}),
        ("balance_on", {"day": "20260201"}),
        ("flag_detail", {"index": -1}),
        ("flag_detail", {}),
    ],
)
def test_invalid_arguments(ctx, name, args):
    assert "error" in run_tool(ctx, name, args)


def test_dispatch_and_empty(ctx):
    for name, args in [
        ("find_rows", {}),
        ("monthly_totals", {}),
        ("top_counterparties", {}),
        ("flag_detail", {"index": 0}),
        ("balance_on", {"day": "2026-01-01"}),
    ]:
        assert "error" not in run_tool(ctx, name, json.dumps(args))
    empty = ToolContext([], [], "SGD")
    assert find_rows(empty)["count"] == 0
    assert monthly_totals(empty) == {"currency": empty.currency, "monthly": {}}
    assert top_counterparties(empty) == {
        "counterparties": [],
        "total": "0",
        "currency": empty.currency,
        "direction": "in",
    }
    assert balance_on(empty, "2026-01-01")["balance"] is None
    assert len(TOOL_SPECS) == 6
    for spec in TOOL_SPECS:
        assert spec["type"] == "function"
        assert spec["function"]["parameters"]["additionalProperties"] is False


def test_empty_unreconciled_page(ctx):
    ctx.statements[0] = ctx.statements[0].model_copy(
        update={"transactions": [], "closing_balance": Decimal("1")}
    )
    result = flag_detail(ctx, 0)
    assert result["type"] == "unreconciled_page"
    assert result["cites"] == []
    assert result["citations"] == []
    assert "SGD 1.00" in result["description"]


def test_citation_cap_and_multiple_pages(ctx):
    ctx.statements[0].transactions = [ctx.statements[0].transactions[0]] * 5
    ctx.statements.append(ctx.statements[0].model_copy(deep=True))
    ctx.page_ids.append("p2")
    assert find_rows(ctx)["rows"][5]["cite"] == "[p2:r1]"
    assert top_counterparties(ctx)["counterparties"][0]["cites"] == [
        "[p1:r1]",
        "[p1:r2]",
        "[p1:r3]",
    ]
    assert balance_on(ctx, "2026-01-01")["cite"] == "[p2:r5]"


def test_list_findings_and_largest_row(ctx):
    from ledgerlens.agent.tools import list_findings

    result = run_tool(ctx, "list_findings", {})
    assert result == list_findings(ctx)
    assert result["count"] == len(result["findings"])
    assert result["findings"][0] == {
        "index": 0,
        "type": "returned_cheque",
        "cites": ["[p1:r3]"],
    }
    empty = list_findings(ToolContext([], [], "SGD"))
    assert empty["count"] == 0 and empty["findings"] == []
    assert empty["counts_by_type"] == dict.fromkeys(
        [
            "returned_cheque",
            "round_trip",
            "large_cash_deposit",
            "customer_concentration",
            "unreconciled_page",
        ],
        0,
    )
    assert result["counts_by_type"]["returned_cheque"] == 1
    assert sum(result["counts_by_type"].values()) == result["count"]
    clean = ctx.statements[0].model_copy(
        update={"transactions": [], "closing_balance": Decimal("0")}
    )
    assert list_findings(ToolContext([clean], ["p1"], "SGD"))["count"] == 0
    ctx.statements[0].transactions[2].debit = Decimal("200")
    result = run_tool(
        ctx, "find_rows", {"direction": "out", "sort": "amount_desc", "limit": 1}
    )
    assert result["rows"][0]["cite"] == "[p1:r3]"
    assert result["count"] == 2
    assert "error" in run_tool(ctx, "find_rows", {"sort": "unknown"})
