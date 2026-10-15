"""Validate and bound chat request data."""

MAX_PROMPT_CHARS = 2000
MAX_HISTORY_TURNS = 10
MAX_DOCUMENTS = 100
MAX_NEW_PAGES = 8
MAX_BODY_BYTES = 9 * 1024 * 1024
MAX_TOOL_ROUNDS = 3
MAX_TOOL_CALLS_PER_ROUND = 5
# Bound per-document processing and request size.
MAX_ROWS_PER_DOCUMENT = 500
# Bounds total analysis work for a 100-page turn (real pages hold ~20-40 rows).
MAX_TOTAL_ROWS = 10_000
MAX_MEMO_SECTION_CHARS = 4000
_SECTION_IDS = {"summary", "cash_flow", "concentration", "red_flags", "questions"}


def validate_agent_input(data: dict) -> dict:
    """Return a cleaned request or raise a plain validation error."""
    if not isinstance(data, dict):
        raise ValueError("Input must be an object")
    message = data.get("message")
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_PROMPT_CHARS
    ):
        raise ValueError("Message must be non-empty text of at most 2000 characters")
    cleaned = {"message": message.strip()}
    if "stop_after" in data:
        if data["stop_after"] != "route":
            raise ValueError("stop_after must be route")
        cleaned["stop_after"] = "route"
    history = data.get("history", [])
    if not isinstance(history, list):
        raise ValueError("History must be a list")
    turns = []
    for turn in history:
        if (
            not isinstance(turn, dict)
            or turn.get("role") not in ("user", "assistant")
            or not isinstance(turn.get("text"), str)
            or len(turn["text"]) > 4000
        ):
            raise ValueError(
                "History entries need a user or assistant role and text of at most 4000 characters"
            )
        turns.append({"role": turn["role"], "text": turn["text"]})
    cleaned["history"] = turns[-MAX_HISTORY_TURNS:]
    ids = set()
    for field in ("documents", "new_pages"):
        pages = data.get(field, [])
        if not isinstance(pages, list):
            raise ValueError(f"{field} must be a list")
        result = []
        for page in pages:
            if not isinstance(page, dict):
                raise ValueError("Each page must be an object")
            page_id = page.get("page_id")
            if not isinstance(page_id, str) or not page_id.strip():
                raise ValueError("Page id must be non-empty text")
            if page_id in ids:
                raise ValueError("Page ids must be unique")
            ids.add(page_id)
            if field == "documents":
                if page.get("doc_type") not in (
                    "statement",
                    "invoice",
                    "receipt",
                ) or not isinstance(page.get("document"), dict):
                    raise ValueError(
                        "Documents need a supported doc_type and a document object"
                    )
                rows = page["document"].get("transactions") or page["document"].get(
                    "line_items"
                )
                if isinstance(rows, list) and len(rows) > MAX_ROWS_PER_DOCUMENT:
                    raise ValueError("At most 500 rows per document are allowed")
                result.append(
                    {key: page[key] for key in ("page_id", "doc_type", "document")}
                )
            else:
                if not isinstance(page.get("image_base64"), str):
                    raise ValueError("New pages need image_base64 text")
                result.append({key: page[key] for key in ("page_id", "image_base64")})
        cleaned[field] = result
    if len(cleaned["new_pages"]) > MAX_NEW_PAGES:
        raise ValueError("At most 8 new pages are allowed")
    total_rows = sum(
        len(
            page["document"].get("transactions")
            or page["document"].get("line_items")
            or []
        )
        for page in cleaned["documents"]
    )
    if total_rows > MAX_TOTAL_ROWS:
        raise ValueError("At most 10000 rows across all documents are allowed")
    if len(ids) > MAX_DOCUMENTS:
        raise ValueError("At most 100 documents including new pages are allowed")
    if "last_memo" in data:
        memo = data["last_memo"]
        if not isinstance(memo, dict) or any(
            key not in _SECTION_IDS
            or not isinstance(value, str)
            or len(value) > MAX_MEMO_SECTION_CHARS
            for key, value in memo.items()
        ):
            raise ValueError(
                "Last memo must map section ids to text of at most 4000 characters"
            )
        cleaned["last_memo"] = dict(memo)
    return cleaned
