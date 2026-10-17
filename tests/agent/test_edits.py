import pytest

from ledgerlens.agent.edits import apply_structural_edit, split_sentences


@pytest.mark.parametrize(
    "ordinal,index",
    [
        ("first", 0),
        ("second", 1),
        ("third", 2),
        ("last", 3),
        ("final", 3),
        ("2nd", 1),
        ("4th", 3),
    ],
)
@pytest.mark.parametrize("verb", ["Remove", "Delete", "drop"])
def test_sentence_ordinals(ordinal, index, verb):
    units = ["One.", "Two.", "Three.", "Four."]
    assert apply_structural_edit(
        f"{verb} the {ordinal} sentence of the summary",
        {"summary": "## Summary\n" + " ".join(units)},
    ) == {
        "summary": "## Summary\n"
        + " ".join(u for i, u in enumerate(units) if i != index)
    }


@pytest.mark.parametrize(
    "message",
    [
        "Remove the second question",
        "Delete the third bullet in the red flags",
        "drop the first point of the red flags",
    ],
)
def test_lists(message):
    questions = "question" in message
    section = "questions" if questions else "red_flags"
    text = "## Heading\n1. One? [p1:r1]\n2. Two?\n   [p1:r2]\n3. Three?"
    result = apply_structural_edit(message, {section: text})[section]
    assert result.startswith("## Heading\n1. ")
    assert "\n2. " in result and "\n3. " not in result
    if questions:
        assert "Two" not in result and "[p1:r2]" not in result


@pytest.mark.parametrize("unit", ["sentence", "sentences"])
@pytest.mark.parametrize("relation", ["about", "mentioning", "on", "regarding"])
def test_all_month_matches(unit, relation):
    memo = {
        "cash_flow": "## Cash flow\nJanuary fell. [p1:r1] Jan recovered. [p1:r2] February rose."
    }
    assert apply_structural_edit(
        f"Remove the {unit} {relation} JAN from the cash flow section", memo
    ) == {"cash_flow": "## Cash flow\nFebruary rose."}


@pytest.mark.parametrize("topic", ['"Cedar Trading"', "Cedar Trading", "January Cedar"])
def test_topic_keys_all_required(topic):
    memo = {
        "summary": "## Summary\nJanuary Cedar Trading fell. Pine grew. January Pine fell."
    }
    result = apply_structural_edit(f"Remove the sentences about {topic}", memo)
    assert result == {"summary": "## Summary\nPine grew. January Pine fell."}


def test_topic_lists_and_inferred_bullets():
    memo = {
        "red_flags": "## Red flags\n- January risk.\n- Jan concern.\n- February risk."
    }
    assert apply_structural_edit("Remove bullets about January", memo) == {
        "red_flags": "## Red flags\n- February risk."
    }
    assert apply_structural_edit("Drop the first bullet", memo) == {
        "red_flags": "## Red flags\n- Jan concern.\n- February risk."
    }
    assert (
        apply_structural_edit(
            "Drop the first bullet", {**memo, "questions": "1. Why?\n2. How?"}
        )
        is None
    )


@pytest.mark.parametrize(
    "message",
    [
        "Remove the last sentence of the summary",
        "Remove sentences about January",
        "Remove the 9th sentence of the summary",
        "Remove sentences about liquidity",
        "Rewrite summary",
    ],
)
def test_safe_defer(message):
    assert (
        apply_structural_edit(message, {"summary": "## Summary\nJanuary fell."}) is None
    )


def test_splitter():
    assert split_sentences("SGD 39,948.92 grew. Cedar Inc. reported growth! Why?") == [
        "SGD 39,948.92 grew.",
        "Cedar Inc. reported growth!",
        "Why?",
    ]
    assert split_sentences("余额上升。 风险仍在！ 为什么？") == [
        "余额上升。",
        "风险仍在！",
        "为什么？",
    ]
    assert split_sentences("One. [p1:r1] [p2:r2] Two.") == [
        "One. [p1:r1] [p2:r2]",
        "Two.",
    ]
    assert apply_structural_edit(
        "Remove the last sentence of the summary",
        {"summary": "## Summary\n余额上升。 风险仍在！"},
    ) == {"summary": "## Summary\n余额上升。"}


def test_bounds_and_no_mutation():
    memo = {"summary": "## Summary\nOne. Two.", "cash_flow": "Unchanged"}
    before = dict(memo)
    assert apply_structural_edit("Remove the first sentence of the summary", memo)
    assert memo == before
    assert (
        apply_structural_edit(
            "Remove the first sentence of the summary", {"summary": "a" * 4001}
        )
        is None
    )


def test_quoted_topic_with_preposition_and_heading_whitespace():
    memo = {"summary": "\n## Summary\nCedar of Pine fell. Cedar grew."}
    assert apply_structural_edit(
        'Remove the sentence about "Cedar of Pine" from the summary', memo
    ) == {"summary": "## Summary\nCedar grew."}
    assert apply_structural_edit("Remove the first sentence of the summary", memo) == {
        "summary": "## Summary\nCedar grew."
    }
