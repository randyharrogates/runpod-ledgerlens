"""Router safeguards, including examples reserved before implementation."""

import pytest

from ledgerlens.agent.rules import is_capability_question, is_document_fact_edit


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Could you revise this?", True),
        ("CAN YOU HELP ME?!", True),
        ("Are you able to update it?", True),
        ("Bisakah anda mengedit memo?", True),
        ("Bolehkah awak sunting memo?", True),
        ("Apakah kamu bisa terjemahkan?", True),
        ("您可以编辑这个吗？", True),
        ("你会翻译它吗", True),
        ("Could you revise this paragraph?", False),
        ("Can you help me find missing transactions?", False),
        ("Are you able to update it to 500?", False),
        ("Bisakah anda mengedit nama bank?", False),
        ("Bolehkah awak sunting memo menjadi ringkas?", False),
        ("Apakah kamu bisa terjemahkan ke Inggris?", False),
        ("您可以编辑这个金额吗？", False),
        ("你会翻译它成马来文吗", False),
    ],
)
def test_held_out_capabilities(message, expected):
    assert is_capability_question(message) is expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Rename the company to Pine Services", True),
        ("Replace Cedar Trading with Maple Works", True),
        ("Set the total inflow to 12,500", True),
        ("Hide the February entry", True),
        ("Ganti nama bank menjadi Bank Rimba", True),
        ("Buang bulan Mac dari jadual", True),
        ("把付款方名称换成山林商贸", True),
        ("去掉表格中的三月", True),
        ("Rename the summary heading", False),
        ("Replace technical language with simple language", False),
        ("Set the tone to friendly", False),
        ("Hide repetitive wording", False),
        ("Ganti gaya penulisan memo", False),
        ("Buang pengulangan dalam ringkasan", False),
        ("把摘要改成更简短的文字", False),
        ("删除重复的描述", False),
    ],
)
def test_held_out_document_facts(message, expected):
    assert is_document_fact_edit(message) is expected


@pytest.mark.parametrize(
    "message",
    [
        "are you able to edit the memo for me?",
        "can you translate?",
        "Bisa edit memo?",
        "Bisa menerjemahkan?",
        "boleh edit memo?",
        "你能修改备忘录吗?",
        "你可以翻译吗？",
        "what can you do?",
        "Apa yang bisa kamu lakukan?",
        "Apa yang anda boleh buat?",
    ],
)
def test_capabilities(message):
    assert is_capability_question(message)


@pytest.mark.parametrize(
    "message",
    [
        "can you make the summary shorter",
        "can you translate the memo into Malay",
        "can you write a memo",
        "can you list the payers",
        "bisa persingkat ringkasan?",
        "你能把备忘录翻译成英文吗",
    ],
)
def test_concrete_requests(message):
    assert not is_capability_question(message)


@pytest.mark.parametrize(
    "message",
    [
        "change all mention of Bayu Emas Murni Niaga to Bank Singapore",
        "can you remove Jan 2026 cashflow line for me from the memo",
        "Replace the borrower name with Cedar Trading",
        "Tukar nama pembayar kepada Sinar Niaga",
        "Change the closing balance to 10000",
        "Hapus baris Januari dari tabel arus kas",
        "删除现金流表中一月的行",
        "把借款人名字改成星河贸易",
    ],
)
def test_document_facts(message):
    assert is_document_fact_edit(message)


@pytest.mark.parametrize(
    "message",
    [
        "make the summary shorter",
        "rewrite the red flags section",
        "translate the memo into Chinese",
        "change the memo audience to a loan officer",
        "change the tone to formal",
        "remove repetition from the existing memo",
        "replace jargon with plain words",
        "Ubah ringkasan memo agar lebih formal",
        "把已有摘要改得更正式",
    ],
)
def test_text_edits(message):
    assert not is_document_fact_edit(message)


@pytest.mark.parametrize(
    "message",
    ['  "CAN YOU EDIT THE MEMO?"  ', "¿Can you translate?", "？！你能做什么？！"],
)
def test_capability_punctuation(message):
    assert is_capability_question(message)


@pytest.mark.parametrize(
    "message",
    [
        "remove anything that may confuse readers",
        "delete the last two lines of the summary",
        "删除关于银行的那句话",
        "change the concentration section to bullet points",
        "replace the word jargon with plain words",
    ],
)
def test_review_text_edits_are_not_document_facts(message):
    assert not is_document_fact_edit(message)


@pytest.mark.parametrize(
    "message",
    [
        "remove the May 2026 row from the cash flow table",
        "delete the March line from the cash flow table",
        "删除三月的数据",
    ],
)
def test_review_month_and_row_removals_are_document_facts(message):
    assert is_document_fact_edit(message)


@pytest.mark.parametrize(
    "message",
    [
        "Remove the sentence about the March dip",
        "Delete the point about the last three months",
        "Drop the mention of June from the summary",
        "Remove the bullet about the large entry on 12 Feb",
        "Hide the red flag about month-end cash deposits",
        "Delete the entries about round-tripping",
        "Remove the line about the April payroll",
        "Hapus kalimat tentang bulan Januari",
        "Buang ayat tentang aliran tunai Mac",
        "删除关于三月的句子",
        "Remove the phrase about SGD 50,000",
        "Delete the paragraph about the February row",
        "Hapus poin tentang entri besar",
    ],
)
def test_prose_topics_are_not_values(message):
    assert not is_document_fact_edit(message)


@pytest.mark.parametrize(
    "message",
    [
        "remove the Jan 2026 row from the cash flow table",
        "delete the March row",
        "change the total inflow to 50,000",
        "rename the borrower to ABC Trading",
        "set the balance to 1,000",
        "Change the amount in the sentence to 500",
        "Ganti nama dalam kalimat menjadi Sinar Niaga",
        "把摘要的银行名称改成山林银行",
        "Ubah saldo menjadi 1000",
        "Buang baris Februari dari jadual",
    ],
)
def test_values_still_protected(message):
    assert is_document_fact_edit(message)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Remove the questions section.", ["questions"]),
        ("drop the cash flow section", ["cash_flow"]),
        ("hapus bagian pertanyaan", ["questions"]),
        ("buang bahagian ringkasan", ["summary"]),
        ("删除问题部分", ["questions"]),
        ("移除摘要章节", ["summary"]),
        ("Remove the sentence about March from the summary", []),
        ("Remove the March row", []),
    ],
)
def test_removed_sections(message, expected):
    from ledgerlens.agent.rules import removed_sections

    assert removed_sections(message) == expected


def test_section_hints_and_restore():
    from ledgerlens.agent.rules import named_sections, restored_sections

    assert named_sections(
        'Remove "visit the site" and shorten 摘要',
        {"questions": "Please visit the site."},
    ) == ["summary", "questions"]
    assert restored_sections("add the questions section back") == ["questions"]
    assert restored_sections("bring back the summary") == ["summary"]
    assert restored_sections("Make questions shorter") == []
