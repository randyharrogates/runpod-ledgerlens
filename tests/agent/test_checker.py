"""Figure, citation and section checks."""

from decimal import Decimal

from ledgerlens.agent.checker import (
    CITATION,
    MEMO_CITATION,
    MEMO_NUMBER,
    bad_citations,
    check_text,
    unverified_memo_numbers,
)


def test_citations():
    assert CITATION.fullmatch("[page_a-2:r3]")["page"] == "page_a-2"
    assert bad_citations("[p1:r1] [p1:r0] [p1:r3] [p2:r1] [p1:r0]", {"p1": 2}) == [
        "[p1:r0]",
        "[p1:r3]",
        "[p2:r1]",
    ]
    assert bad_citations("[p1:r2]", {"p1": 2}) == []
    assert MEMO_CITATION.fullmatch("[p1:r4]")
    assert MEMO_NUMBER.fullmatch("1,234.56")


def test_figures():
    payload = {
        "inflow": Decimal("1234.56"),
        "share": "0.75",
        "description": "SGD 20.00",
        "nested": [True, None, "NaN", "text"],
    }
    assert (
        unverified_memo_numbers("1,235 and 75% and 20.00 in 2026 [p1:r4]", payload)
        == []
    )
    assert unverified_memo_numbers("9,999.99 and 9,999.99", payload) == ["9,999.99"]
    assert (
        check_text(
            "## Summary\nSGD 1,234.56 [p1:r1]",
            payload,
            {"p1": 1},
            max_words=10,
            required_heading="## Summary",
        )
        == []
    )
    problems = check_text(
        "9,999.99 [p2:r1]",
        payload,
        {"p1": 1},
        max_words=1,
        required_heading="## Summary",
    )
    assert [p["kind"] for p in problems] == [
        "unverified_figure",
        "bad_citation",
        "too_long",
        "missing_heading",
    ]
    assert all(isinstance(p["detail"], str) for p in problems)
    assert (
        check_text(
            "## Questions for the applicant.\n- Why?",
            payload,
            {"p1": 1},
            required_heading="## Questions for the applicant",
        )
        == []
    )


def test_app_exports():
    from app.backend import main
    from ledgerlens.agent import findings

    assert main.MEMO_NUMBER is MEMO_NUMBER
    assert main.MEMO_CITATION is MEMO_CITATION
    assert main.unverified_memo_numbers is unverified_memo_numbers
    assert main.amount is findings.amount
    assert main.describe_finding is findings.describe_finding
