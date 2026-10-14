"""Deterministic checks for generated text."""

import re
from decimal import Decimal, InvalidOperation

# Plain integers of 4+ digits are amounts too ("SGD 55041"); years are skipped below.
MEMO_NUMBER = re.compile(
    r"\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|(?<![\d:r])\d{4,}(?![\d,]|\.\d)"
)
MEMO_CITATION = re.compile(r"\[p\d+:r\d+[^\]]*\]")


def unverified_memo_numbers(text, payload):
    """Compare memo figures with supplied analysis values, including rounded forms."""
    allowed = set()

    def add(value, share=False):
        number = Decimal(str(value).replace(",", "").rstrip("%"))
        if not number.is_finite():
            return
        allowed.add(number)
        allowed.update(round(number, places) for places in (0, 1, 2))
        if share and 0 <= number <= 1:
            allowed.update(round(number * 100, places) for places in (0, 1, 2))

    def collect(value, name=""):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, key)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, name)
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            add(value, name == "share")
        elif isinstance(value, str):
            try:
                add(value, name == "share")
            except InvalidOperation:
                pass
            for match in MEMO_NUMBER.finditer(MEMO_CITATION.sub("", value)):
                add(match.group())

    collect(payload)
    unverified = []
    for match in MEMO_NUMBER.finditer(MEMO_CITATION.sub("", text)):
        token = match.group()
        number = Decimal(token.replace(",", "").rstrip("%"))
        if token.isdigit() and 1900 <= number <= 2100:
            continue
        # A whole number may drop the cents ("55,041" for 55,041.59).
        tolerance = Decimal("0.005") if "." in token else Decimal("0.995")
        if token not in unverified and not any(
            abs(number - value) <= tolerance for value in allowed
        ):
            unverified.append(token)
    return unverified


CITATION = re.compile(r"\[(?P<page>[A-Za-z0-9_-]+):r(?P<row>\d+)\]")


def bad_citations(text, row_counts: dict[str, int]) -> list[str]:
    """Return unknown or out-of-range row references."""
    return list(
        dict.fromkeys(
            match.group()
            for match in CITATION.finditer(text)
            if not 1 <= int(match["row"]) <= row_counts.get(match["page"], 0)
        )
    )


def check_text(
    text, payload, row_counts, *, max_words=None, required_heading=None
) -> list[dict]:
    """Check figures, references, length and the section heading."""
    problems = [
        {"kind": "unverified_figure", "detail": value}
        for value in unverified_memo_numbers(text, payload)
    ]
    problems.extend(
        {"kind": "bad_citation", "detail": value}
        for value in bad_citations(text, row_counts)
    )
    if max_words is not None and len(text.split()) > max_words:
        problems.append(
            {
                "kind": "too_long",
                "detail": f"{len(text.split())} words exceeds {max_words}",
            }
        )
    headings = {line.strip().lstrip("#").strip() for line in text.splitlines()}
    if (
        required_heading is not None
        and required_heading.lstrip("#").strip() not in headings
    ):
        problems.append({"kind": "missing_heading", "detail": required_heading})
    return problems
