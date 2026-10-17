"""Conservative deterministic safeguards for router mistakes."""

import re
import unicodedata


def _clean(message: str) -> str:
    message = message.strip()
    while message and unicodedata.category(message[0]).startswith("P"):
        message = message[1:].lstrip()
    while message and unicodedata.category(message[-1]).startswith("P"):
        message = message[:-1].rstrip()
    return re.sub(r"\s+", " ", message)


def is_capability_question(message: str) -> bool:
    """Recognize only complete, generic ability questions without edit details."""
    text = _clean(message).casefold()
    patterns = (
        r"(?:what can you do|apa yang (?:(?:bisa|boleh) (?:kamu|anda|awak)|"
        r"(?:kamu|anda|awak) (?:bisa|boleh)) (?:lakukan|buat))",
        r"(?:can|could) you (?:edit|change|modify|revise|rewrite|update|translate|help)"
        r"(?: (?:the memo|it|this|me))?(?: for me)?",
        r"are you able to (?:edit|change|modify|revise|rewrite|update|translate|help)"
        r"(?: (?:the memo|it|this|me))?(?: for me)?",
        r"(?:(?:bisa|bisakah|boleh|bolehkah|dapatkah)(?: (?:kamu|anda|awak))?|"
        r"apakah (?:kamu|anda|awak) bisa) "
        r"(?:edit|ubah|mengedit|sunting|terjemah|terjemahkan|menerjemahkan)(?: memo)?",
        r"[你您](?:能|可以|会)(?:修改|编辑|改|翻译)(?:备忘录|它|这个)?吗",
        r"[你您](?:能|会)做什么",
    )
    return any(re.fullmatch(pattern, text) for pattern in patterns)


def is_document_fact_edit(message: str) -> bool:
    """Identify requests to alter source names, figures, or table contents."""
    text = _clean(message)
    months = (
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may\s+\d{4}|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    )
    patterns = (
        r"\b(?:change|replace|rename)\b.*\bnames?\b.*\b(?:to|with|as)\b\s+\S+",
        r"\b(?:change|replace|rename)\s+(?:the\s+)?(?:borrower|payer|bank|company)"
        r"\s+(?:to|with|as)\s+\S+",
        r"\bchange\s+all\s+mentions?\s+of\s+.+?\s+to\s+\S+",
        r"\b(?:change|set)\b.*\b(?:balance|amount|total|figure|revenue|inflow|outflow)\b"
        r".*\bto\s+[+-]?(?:\d[\d,.]*)",
        # Table rows and months; "lines" only when they are cash-flow or table lines,
        # so "delete the last two lines of the summary" stays a text edit.
        rf"\b(?:remove|delete|drop|hide)\b.*\b(?:rows?|entries|entry|months?|{months})\b",
        r"\b(?:remove|delete|drop|hide)\b",  # Line targets use separate scans below.
        r"\b(?:tukar|ganti|ubah)\s+nama\b.+",
        r"\b(?:hapus|buang)\s+(?:baris|bulan)\b.+",
        r"\bubah\s+(?:saldo|jumlah)\b.*\bmenjadi\s+[+-]?\d[\d,.]*",
        r"把[^。！？!?]*(?:名字|名称|借款人|付款方|银行)[^。！？!?]*(?:改成|改为|换成).+",
        # A month (一月, 3月) or a table row; a bare 行 also occurs in 银行 (bank).
        r"(?:删除|去掉)[^。！？!?]*(?:[一二三四五六七八九十\d]+月|表[^。！？!?]*行)",
        r"把(?:余额|金额)[^。！？!?]*(?:改成|改为|换成)\s*[+-]?\d[\d,.]*",
    )
    matches = [bool(re.search(pattern, text, re.IGNORECASE)) for pattern in patterns]
    if any(matches[i] for i in (0, 1, 2, 3, 6, 8, 9, 11)):
        return True
    cash_line = (
        matches[5]
        and re.search(r"\b(?:cash\s*-?flow|table)\b", text, re.I)
        and re.search(r"\blines?\b", text, re.I)
    )
    if (
        cash_line
        and not re.search(r"\blines?\s+about\b", text, re.I)
        and not has_prose_unit(re.sub(r"\blines?\b", "", text, flags=re.I))
    ):
        return True
    if not has_prose_unit(text) and any(
        match for i, match in enumerate(matches) if i != 5
    ):
        return True
    # Keep capitalization here: generic replacements like jargon are prose edits.
    proper_name = r"[A-Z][\w’\'-]*(?:\s+[A-Z][\w’\'-]*)*"
    return bool(
        re.search(
            rf'(?i:\breplace)\s+["“\']?{proper_name}["”\']?\s+(?i:with)\s+\S+',
            text,
        )
    )


SECTION_PATTERNS = {
    "summary": r"\bsummary\b|\bringkasan\b|摘要",
    "cash_flow": r"\bcash\s*-?flow\b|\barus kas\b|\baliran tunai\b|现金流",
    "concentration": r"\bconcentration\b|\blargest payer\b|\bkonsentrasi\b|集中",
    "red_flags": r"\b(?:red )?flags?\b|\btanda bahaya\b|风险|红旗",
    "questions": r"\bquestions?\b|\bpertanyaan\b|\bsoalan\b|问题",
}


def has_prose_unit(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:sentences?|points?|bullets?|mentions?|paragraphs?|questions?|flags?|"
            r"words?|phrases?|lines?|tone|length|format|language|"
            r"kalimat|ayat|poin|butir|sebutan|paragraf|pertanyaan|soalan|nada|"
            r"bahasa)\b|tanda bahaya|"
            r"entries about|句|段落|提及|描述|问题|风险|红旗|措辞|语气|语言",
            message,
            re.I,
        )
    )


def is_prose_edit(message: str) -> bool:
    return (
        has_prose_unit(message) or bool(removed_sections(message))
    ) and not is_document_fact_edit(message)


def named_sections(message: str, previous: dict) -> list[str]:
    """Union explicit section names and literal quoted snippets from prior text."""
    quotes = re.findall(r"""["“‘']([^"”’']+)["”’']""", message)
    return [
        section
        for section, pattern in SECTION_PATTERNS.items()
        if re.search(pattern, message, re.I)
        or any(quote in previous.get(section, "") for quote in quotes)
    ]


def removed_sections(message: str) -> list[str]:
    """Only explicit whole-section removals bypass the writer."""
    result = []
    for section, pattern in SECTION_PATTERNS.items():
        name = f"(?:{pattern})"
        if re.search(
            rf"\b(?:remove|delete|drop)\s+(?:the\s+)?{name}\s+section\b|"
            rf"\b(?:hapus|buang|padam)\s+(?:bagian|bahagian|seksyen)\s+{name}|"
            rf"(?:删除|移除|去掉){name}(?:部分|章节)",
            message,
            re.I,
        ):
            result.append(section)
    return result


def restored_sections(message: str) -> list[str]:
    """Recognize explicit requests to write a removed section again."""
    result = []
    for section, pattern in SECTION_PATTERNS.items():
        name = f"(?:{pattern})"
        if re.search(
            rf"\badd\s+(?:the\s+)?{name}\s+(?:section\b|back\b)|"
            rf"\b(?:bring back|restore|reinstate)\s+(?:the\s+)?{name}|"
            rf"\b(?:tambah|tambahkan|kembalikan)\s+(?:bagian|bahagian|seksyen)\s+{name}|"
            rf"(?:恢复|加回|添加){name}(?:部分|章节)",
            message,
            re.I,
        ):
            result.append(section)
    return result
