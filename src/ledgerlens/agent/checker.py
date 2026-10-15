"""Deterministic checks for generated text."""

import re
from decimal import Decimal, InvalidOperation

# Plain integers of 4+ digits are amounts too ("SGD 55041"); years are skipped below.
MEMO_NUMBER = re.compile(
    r"\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|(?<![\d:r])\d{4,}(?![\d,]|\.\d)"
)
MEMO_CITATION = re.compile(r"\[p\d+:r\d+[^\]]*\]")
SCALES = {
    "k": Decimal(10) ** 3,
    "thousand": Decimal(10) ** 3,
    "ribu": Decimal(10) ** 3,
    "m": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "juta": Decimal(10) ** 6,
    "b": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
    "miliar": Decimal(10) ** 9,
}
# An amount written with a scale word: "3.77M", "1.2 million", "455,5 juta" is not matched
# (comma decimals are rare in these memos; they stay checked as plain numbers).
SCALED = re.compile(
    r"(?<![\w.])(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s?(?P<unit>"
    + "|".join(sorted(SCALES, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


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
    plain = MEMO_CITATION.sub("", text)
    for match in SCALED.finditer(plain):
        # "IDR 3.77M" passes only if a real figure rounds to it at the shown precision.
        number = Decimal(match["value"].replace(",", ""))
        scale = SCALES[match["unit"].lower()]
        places = len(match["value"].split(".")[1]) if "." in match["value"] else 0
        tolerance = Decimal(5) * Decimal(10) ** -(places + 1) * scale
        if not any(abs(number * scale - value) <= tolerance for value in allowed):
            unverified.append(match.group().strip())
    plain = SCALED.sub(" ", plain)
    for match in MEMO_NUMBER.finditer(plain):
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


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def word_count(text):
    """Count whitespace words plus one word per two CJK characters."""
    characters = sum(len(run) for run in CJK.findall(text))
    return len(CJK.sub(" ", text).split()) + (characters + 1) // 2


def unverified_names(text, collected, question):
    """Check CJK runs against original tool strings and the question.

    Only for questions without CJK text: then any CJK in the answer must be a copied name.
    A Chinese answer runs ordinary words into names, so it cannot be checked this way.
    """
    if CJK.search(question):
        return []
    strings = [question]

    def collect(value):
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(collected)
    return [
        {"kind": "unverified_name", "detail": run}
        for run in dict.fromkeys(CJK.findall(text))
        if len(run) >= 2 and not any(run in value for value in strings)
    ]


def heading(line):
    """Compare headings without '#', a trailing '.' or ':', or case."""
    return line.strip().lstrip("#").strip().rstrip(".:").strip().lower()


def canonical_heading(text, required_heading):
    """Rewrite a heading that only differs in punctuation or case to the exact one."""
    return "\n".join(
        f"## {required_heading}"
        if line.lstrip().startswith("#") and heading(line) == heading(required_heading)
        else line
        for line in text.split("\n")
    )


def _strings(value):
    """Every string inside a nested payload (names in the analysis may be Chinese)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def check_text(
    text,
    payload,
    row_counts,
    *,
    max_words=None,
    required_heading=None,
    question=None,
    language=None,
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
    if question is not None:
        problems.extend(unverified_names(text, payload, question))
    # A section in en/id/ms must not switch script ("fraud嫌疑" in an English memo).
    if language in ("en", "id", "ms"):
        problems.extend(
            {"kind": "wrong_language", "detail": run}
            for run in dict.fromkeys(CJK.findall(MEMO_CITATION.sub("", text)))
            if not any(run in str(value) for value in _strings(payload))
        )
    if max_words is not None and word_count(text) > max_words:
        problems.append(
            {
                "kind": "too_long",
                "detail": f"{word_count(text)} words exceeds {max_words}",
            }
        )

    headings = {heading(line) for line in text.splitlines()}
    if required_heading is not None and heading(required_heading) not in headings:
        problems.append({"kind": "missing_heading", "detail": required_heading})
    return problems
