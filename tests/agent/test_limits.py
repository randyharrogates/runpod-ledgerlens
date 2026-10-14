"""Request limits and clean input validation."""

import pytest

from ledgerlens.agent.limits import (
    MAX_BODY_BYTES,
    MAX_HISTORY_TURNS,
    MAX_PAGES,
    MAX_PROMPT_CHARS,
    MAX_TOOL_ROUNDS,
    validate_agent_input,
)


def test_clean_input():
    data = {
        "message": " hello ",
        "history": [{"role": "user", "text": str(i), "extra": 1} for i in range(12)],
        "documents": [{"page_id": "p1", "doc_type": "statement", "document": {}}],
        "new_pages": [{"page_id": "p2", "image_base64": "data"}],
        "last_memo": {"summary": "old"},
        "extra": 1,
    }
    cleaned = validate_agent_input(data)
    assert cleaned["message"] == "hello"
    assert len(cleaned["history"]) == MAX_HISTORY_TURNS
    assert cleaned["history"][0] == {"role": "user", "text": "2"}
    assert "extra" not in cleaned
    assert len(data["history"]) == 12
    assert cleaned["last_memo"] == {"summary": "old"}
    assert validate_agent_input({"message": "x"}) == {
        "message": "x",
        "history": [],
        "documents": [],
        "new_pages": [],
    }
    assert MAX_PROMPT_CHARS == 2000 and MAX_PAGES == 8
    assert MAX_BODY_BYTES == 9 * 1024 * 1024 and MAX_TOOL_ROUNDS == 3
    assert validate_agent_input({"message": "x" * 2000})["message"] == "x" * 2000


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"message": None},
        {"message": 1},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 2001},
        {"history": None},
        {"history": {}},
        {"history": [None]},
        {"history": [{"role": "system", "text": "x"}]},
        {"history": [{"role": "user", "text": 1}]},
        {"history": [{"role": "user", "text": "x" * 4001}]},
        {"documents": None},
        {"new_pages": {}},
        {"documents": [1]},
        {"documents": [{"page_id": "", "doc_type": "statement", "document": {}}]},
        {"documents": [{"page_id": 1, "doc_type": "statement", "document": {}}]},
        {"documents": [{"page_id": "p1", "doc_type": "other", "document": {}}]},
        {"documents": [{"page_id": "p1", "doc_type": "invoice", "document": []}]},
        {"new_pages": [{"page_id": "p1", "image_base64": 1}]},
        {"new_pages": [{"page_id": "p1"}]},
        {"new_pages": [{"page_id": "p1", "image_base64": ""}] * 2},
        {
            "documents": [{"page_id": "p1", "doc_type": "receipt", "document": {}}],
            "new_pages": [{"page_id": "p1", "image_base64": ""}],
        },
        {"new_pages": [{"page_id": f"p{i}", "image_base64": ""} for i in range(9)]},
        {"last_memo": []},
        {"last_memo": {"other": "x"}},
        {"last_memo": {"summary": 1}},
    ],
)
def test_rejects_bad_input(bad):
    if isinstance(bad, dict) and "message" not in bad and bad:
        bad = {"message": "hello", **bad}
    with pytest.raises(ValueError):
        validate_agent_input(bad)
