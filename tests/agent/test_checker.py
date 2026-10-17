"""Figure, citation and section checks."""

from decimal import Decimal

from ledgerlens.agent.checker import (
    CITATION,
    MEMO_CITATION,
    MEMO_NUMBER,
    bad_citations,
    check_text,
    unverified_memo_numbers,
    unverified_names,
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


def test_names_and_cjk_word_count():
    from ledgerlens.agent.checker import word_count

    payload = [{"payer": "星林峰服务"}]
    assert check_text("六福珠宝", payload, {}, question="Who paid?") == [
        {"kind": "unverified_name", "detail": "六福珠宝"}
    ]
    assert check_text("星林峰服务", payload, {}, question="Who paid?") == []
    assert check_text("Birch Goods", payload, {}, question="") == []
    assert word_count("中文字符 English words") == 4
    assert check_text("中文字符", {}, {}, max_words=1)[0]["kind"] == "too_long"


def test_unverified_names_skips_chinese_questions():
    tools = [{"payer": "星林峰服务"}]
    assert (
        unverified_names("最大的付款方是星林峰服务", tools, "最大的付款方是谁？") == []
    )
    assert unverified_names("The payer is 六福珠宝.", tools, "Who paid most?") == [
        {"kind": "unverified_name", "detail": "六福珠宝"}
    ]


def test_canonical_heading_fixes_punctuation_only():
    from ledgerlens.agent.checker import canonical_heading

    text = "## Questions for the applicant.\n\n- Why?"
    assert canonical_heading(text, "Questions for the applicant") == (
        "## Questions for the applicant\n\n- Why?"
    )
    assert canonical_heading("## Other\n", "Summary") == "## Other\n"


def test_scaled_amounts_pass_only_when_they_round_from_a_real_figure():
    # From live E5 run 4 (biz_0364): nets 1,427,510 and 1,115,345; average 5,518,589.29.
    payload = {
        "monthly": {"2026-01": {"net": "1427510"}, "2026-04": {"net": "1115345"}},
        "average_balance": {"value": "5518589.29"},
    }
    assert unverified_memo_numbers("Net fell to IDR 1.12M in April.", payload) == []
    assert (
        unverified_memo_numbers("Average balance was IDR 5.5 million.", payload) == []
    )
    assert unverified_memo_numbers("Net was IDR 3.77M in January.", payload) == [
        "3.77M"
    ]
    assert unverified_memo_numbers("Net was IDR 1.45 juta.", payload) == ["1.45 juta"]


def test_wrong_language_in_english_section():
    payload = {"top_payer_concentration": {"payer": "星林峰服务"}}
    problems = check_text(
        "Round trip — inefficiency or fraud嫌疑.", payload, {}, language="en"
    )
    assert problems == [{"kind": "wrong_language", "detail": "嫌疑"}]
    # A Chinese name from the analysis is allowed; Chinese memos are not checked.
    assert check_text("Largest payer 星林峰服务.", payload, {}, language="en") == []
    assert check_text("可疑交易。", payload, {}, language="zh") == []


def test_indonesian_number_format_is_read_both_ways():
    # Seen live: a correct Indonesian translation was flagged on every amount.
    payload = {
        "period_totals": {"inflow": 391862237, "outflow": 220304021, "net": 171558216},
        "top_payer_concentration": {"share": "0.7316", "amount": 286670519},
    }
    text = (
        "Posisi kas: IDR 220.304.021 keluar, IDR 391.862.237 masuk, bersih "
        "IDR 171.558.216. Pelangi mencakup 73,16% arus masuk, sebesar IDR 286.670.519."
    )
    assert unverified_memo_numbers(text, payload) == []
    assert unverified_memo_numbers("IDR 999.999.999 dan 12,50%", payload) == [
        "999.999.999",
        "12,50%",
    ]
