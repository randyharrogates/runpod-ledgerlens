"""Bounded, conservative deletions of memo prose units."""

import re

from ledgerlens.agent.rules import SECTION_PATTERNS, named_sections

_CITE = r"\[p\d+:r\d+\]"
_TRAILING_CITES = re.compile(rf"(?:\s*{_CITE})+")
_LOWER = re.compile(r"\s+[a-z]")
_NUMBER = re.compile(r"\s*\d+\.")
_LIST = re.compile(r"^\s*(?:\d+\.|[-*•])\s+", re.M)
_MONTHS = "January February March April May June July August September October November December".split()


def split_sentences(text: str) -> list[str]:
    """Split prose, retaining punctuation and following row citations with its unit."""
    units = []
    start = 0
    for match in re.finditer(r"[.!?。！？]+(?=\s|$)", text):
        end = match.end()
        # Common company abbreviation followed by lowercase prose is not a boundary.
        if text[max(start, end - 4) : end] == "Inc." and _LOWER.match(text, end):
            continue
        if _NUMBER.fullmatch(text, start, end):
            continue
        tail = _TRAILING_CITES.match(text, end)
        if tail:
            end = tail.end()
        if end <= start:
            continue
        units.append(text[start:end].strip())
        start = end
    if text[start:].strip():
        units.append(text[start:].strip())
    return units


def _parts(text, kind):
    lines = text.lstrip().splitlines(keepends=True)
    heading = ""
    if lines and lines[0].lstrip().startswith("#"):
        heading = lines.pop(0).rstrip() + "\n"
    body = "".join(lines)
    if kind == "sentence":
        return heading, split_sentences(body), " "
    starts = [m.start() for m in _LIST.finditer(body)]
    if not starts or body[: starts[0]].strip():
        return heading, [], "\n"
    return (
        heading,
        [body[a:b].strip() for a, b in zip(starts, starts[1:] + [len(body)])],
        "\n",
    )


def _keys(topic):
    keys = []
    for month in _MONTHS:
        if re.search(rf"\b(?:{month}|{month[:3]})\b", topic, re.I):
            keys.append(rf"\b(?:{month}|{month[:3]})\b")
    quotes = re.findall(r"""["“‘']([^"”’']+)["”’']""", topic)
    keys.extend(re.escape(q) for q in quotes)
    stop = {"The", "A", "An", "Please", "Remove", "Delete", "Drop"}
    unquoted = re.sub(r"""["“‘']([^"”’']+)["”’']""", " ", topic)
    months = {v.casefold() for m in _MONTHS for v in (m, m[:3])}
    keys.extend(
        rf"\b{re.escape(w)}\b"
        for w in re.findall(r"\b[A-Z][\w'-]*\b", unquoted)
        if w not in stop and w.casefold() not in months
    )
    return keys


def apply_structural_edit(message: str, memo: dict) -> dict | None:
    """Return a single safe deletion, or defer unsupported/ambiguous edits."""
    if len(message) > 4000:
        return None
    ordinal = re.search(
        r"\b(?:remove|delete|drop)\s+(?:the\s+)?(first|second|third|last|final|\d+(?:st|nd|rd|th))\s+(sentence|question|bullet|point)\b",
        message,
        re.I,
    )
    topic = re.search(
        r"\b(?:remove|delete|drop)\s+(?:the\s+)?(sentences?|bullets?|questions?|points?)\s+(?:about|mentioning|on|regarding)\s+(.+)",
        message,
        re.I,
    )
    if not ordinal and not topic:
        return None
    kind = ordinal[2].lower() if ordinal else topic[1].lower().rstrip("s")
    sections = named_sections(message, memo)
    if ordinal and not sections:
        if kind == "question":
            sections = ["questions"]
        elif kind in ("bullet", "point"):
            sections = [
                s
                for s, text in memo.items()
                if len(text) <= 4000 and _LIST.search(text)
            ]
        if len(sections) != 1:
            return None
    if len(sections) > 1:
        return None
    topic_text = topic[2] if topic else ""
    # Strip only an explicit section suffix, preserving e.g. quoted "Cedar of Pine".
    section_names = "|".join(SECTION_PATTERNS.values())
    topic_text = re.sub(
        rf"\s+(?:from|in|of)\s+(?:the\s+)?(?:{section_names})(?:\s+section)?[.!?]*$",
        "",
        topic_text,
        flags=re.I,
    )
    keys = _keys(topic_text) if topic else []
    if topic and (not keys or len(keys) > 16):
        return None
    candidates = []
    for section in sections or memo:
        text = memo.get(section, "")
        if not text or len(text) > 4000:
            continue
        heading, units, joiner = _parts(text, kind)
        if ordinal:
            value = ordinal[1].lower()
            index = {
                "first": 0,
                "second": 1,
                "third": 2,
                "last": len(units) - 1,
                "final": len(units) - 1,
            }.get(value)
            if index is None:
                index = int(re.match(r"\d+", value)[0]) - 1
            remove = {index} if 0 <= index < len(units) else set()
        else:
            remove = {
                i
                for i, unit in enumerate(units)
                if all(re.search(key, unit, re.I) for key in keys)
            }
        if not remove or len(remove) == len(units):
            continue
        kept = [unit for i, unit in enumerate(units) if i not in remove]
        if kind != "sentence":
            kept = [re.sub(r"^\d+\.", f"{i}.", unit) for i, unit in enumerate(kept, 1)]
        candidates.append({section: heading + joiner.join(kept)})
    return candidates[0] if len(candidates) == 1 else None
