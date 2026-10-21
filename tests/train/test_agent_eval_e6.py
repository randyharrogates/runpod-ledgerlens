import pytest

from train import agent_eval as ev


@pytest.mark.parametrize(
    "template,previous,good,bad,month",
    [
        (
            "drop-sentence",
            "## Summary\nOne. Two. Three.",
            "## Summary\nOne. Two.",
            "## Summary\nOne.",
            None,
        ),
        ("drop-month", "January dipped.", "Other months.", "January dipped.", "Jan"),
        (
            "add-sentence",
            "Old.",
            "Recommend a SITE VISIT.",
            "Recommend approval.",
            None,
        ),
        (
            "paragraph",
            "- Risk [p1:r1]",
            "Risk remains [p1:r1].",
            "- Risk remains [p1:r1].",
            None,
        ),
        (
            "drop-question",
            "## Questions for the applicant\n1. Why?\n2. How?",
            "1. Why?",
            "1. Why?\n2. How?",
            None,
        ),
        (
            "formal",
            "A casual old sentence.",
            "The concentration warrants careful review.",
            "A casual old sentence.",
            None,
        ),
        (
            "expand",
            "one two three four five",
            "one two three four five six seven",
            "one two",
            None,
        ),
        (
            "shorter",
            "one two three four five",
            "one two three",
            "one two three four five",
            None,
        ),
        (
            "translate",
            "English summary.",
            "Kas ini dan bulan yang lalu.",
            "Kas only.",
            None,
        ),
        ("remove-section", "Questions", "", "## Questions", None),
    ],
)
def test_checks(template, previous, good, bad, month):
    assert ev.e6_check(template, good, previous, month)
    assert not ev.e6_check(template, bad, previous, month)


def result(memo=None, **kwargs):
    return {
        "status": "COMPLETED",
        "wall_s": 2,
        "events": [
            {"type": "final", **({"memo": memo} if memo is not None else {}), **kwargs}
        ],
    }


def test_month_and_counts():
    assert ev.memo_month({"cash_flow": "No dates", "summary": "March then Apr"}) is None
    assert ev.memo_month({"cash_flow": "Feb and January", "summary": "March"}) == "Feb"
    assert ev.memo_month({"summary": "No dates"}) is None
    assert ev.sentence_count("## Summary\nOne! Two? Three.") == 3
    assert ev.numbered_count("## Questions for the applicant\n1. A\n2. B") == 2


@pytest.mark.skipif(
    not (ev.ROOT / "data/synth400").is_dir(),
    reason="Requires local data/synth400 synthetic documents",
)
def test_build_e6(tmp_path):
    cases = ev.build("e6", tmp_path)
    assert len(cases) == 8 * 14
    assert len({c["id"] for c in cases}) == len(cases)
    parents = {c["id"] for c in cases if not c.get("depends_on")}
    assert len(parents) == 8
    assert all(c["depends_on"] in parents for c in cases if c.get("depends_on"))
    assert len(ev.build("e6", tmp_path, 1)) == 14


@pytest.mark.parametrize("template", ["refuse-name", "refuse-row", "refuse-figure"])
def test_refusal_check(template):
    parent = {"summary": "Old.", "questions": "1. Why?"}
    case = {"id": "x", "template": template, "target": None}
    good = ev.e6_metrics(
        case, result(answer=ev.REPLIES["document_facts"]), result(parent)
    )
    assert good["followed"] and good["correct_refusal"]
    bad = ev.e6_metrics(
        case,
        result({"summary": "Changed"}, answer=ev.REPLIES["document_facts"]),
        result(parent),
    )
    assert not bad["followed"]


def test_metrics_wrongful_refusal_and_others():
    parent = {"summary": "Old.", "questions": "1. Why?"}
    case = {"id": "x", "template": "add-sentence", "target": "summary"}
    metric = ev.e6_metrics(
        case,
        result(
            {**parent, "summary": "Site visit."},
            intent="edit_memo",
            problems=[{"kind": "edit_not_applied"}],
        ),
        result(parent),
    )
    assert metric["followed"] and metric["others_unchanged"]
    assert metric["edit_not_applied"] == 1
    metric = ev.e6_metrics(
        case, result(answer=ev.REPLIES["document_facts"]), result(parent)
    )
    assert metric["refused_wrongly"] and not metric["followed"]
    metric = ev.e6_metrics(case, {"status": "SKIPPED"}, result(parent))
    assert not metric["applicable"]


@pytest.mark.skipif(
    not (ev.ROOT / "data/synth400").is_dir(),
    reason="Requires local data/synth400 synthetic documents",
)
def test_run_skip_and_score(tmp_path, monkeypatch):
    cases = ev.build("e6", tmp_path, 1)
    monkeypatch.setattr(ev.client, "runpodctl_key", lambda: "offline-test")
    seen = []
    parent = {s["id"]: "No month here." for s in ev.SECTIONS}

    def call(endpoint, payload, key, **kwargs):
        seen.append(payload)
        if "last_memo" in payload:
            assert payload["last_memo"] == parent
        return {"status": "COMPLETED", "output": [{"type": "final", "memo": parent}]}

    monkeypatch.setattr(ev.client, "call_endpoint", call)
    ev.run("e6", tmp_path, "offline", concurrency=1)
    rows = ev.load_jsonl(tmp_path / "e6/results.jsonl")
    assert sum(r["status"] == "SKIPPED" for r in rows) == 2
    assert len(seen) == 12
    report = ev.score("e6", tmp_path)
    assert report["skipped"] == 2 and report["applicable"] == 11
    assert report["median_latency_s"] >= 0
    ev.run("e6", tmp_path, "offline", concurrency=1)
    assert len(seen) == 12
    assert len(cases) == 14


@pytest.mark.skipif(
    not (ev.ROOT / "data/synth400").is_dir(),
    reason="Requires local data/synth400 synthetic documents",
)
def test_run_substitutes_month_from_parent(tmp_path, monkeypatch):
    cases = ev.build("e6", tmp_path, 1)
    selected = [
        c
        for c in cases
        if c.get("template") in ("drop-month", "refuse-row") or not c.get("depends_on")
    ]
    ev.write_jsonl(tmp_path / "e6/cases.jsonl", selected)
    monkeypatch.setattr(ev.client, "runpodctl_key", lambda: "offline-test")
    seen = []
    parent = {s["id"]: "February activity." for s in ev.SECTIONS}

    def call(endpoint, payload, key, **kwargs):
        seen.append(payload)
        return {"status": "COMPLETED", "output": [{"type": "final", "memo": parent}]}

    monkeypatch.setattr(ev.client, "call_endpoint", call)
    ev.run("e6", tmp_path, "offline", concurrency=1)
    assert len(seen) == 3
    assert all(
        "February" in p["message"] and "{month}" not in p["message"] for p in seen[1:]
    )


def test_paragraph_without_parent_citation_and_decimal_sentences():
    assert ev.e6_check("paragraph", "Risk remains.", "- Risk remains.")
    assert not ev.e6_check("paragraph", "Risk remains.", "- Risk remains [p1:r1]")
    assert (
        ev.sentence_count(
            "## Summary\nSGD 39,948.92 remains. Cedar Inc. reported growth."
        )
        == 2
    )


@pytest.mark.parametrize(
    "template,text,target",
    [
        ("formal", "Formal assessment of concentration.", "concentration"),
        ("add-sentence", "Site visit.", "summary"),
    ],
)
def test_wrong_language_never_followed(template, text, target):
    case = {"id": "x", "template": template, "target": target}
    metric = ev.e6_metrics(
        case,
        result(
            {target: text},
            intent="edit_memo",
            problems=[{"kind": "wrong_language", "section": target}],
        ),
        result({target: "Old."}),
    )
    assert not metric["followed"]


def test_score_results_only_offline(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline scoring must not access credentials or datasets")

    monkeypatch.setattr(ev.client, "runpodctl_key", forbidden)
    monkeypatch.setattr(ev, "businesses", forbidden)
    parent = {"summary": "## Summary\nOne. Two."}
    edited = result({"summary": "## Summary\nOne."}, intent="edit_memo", problems=[])
    edited["events"].insert(
        0,
        {"type": "section", "section": "summary", "applied_by": "code", "problems": []},
    )
    ev.write_jsonl(
        tmp_path / "e6/results.jsonl",
        [{"id": "biz-memo", **result(parent)}, {"id": "biz-drop-sentence", **edited}],
    )
    score = ev.score("e6", tmp_path)
    assert score["followed"] == score["applied_by_code"] == 1
