import json

import pytest
from PIL import Image

from train import agent_eval as ev


def result(case_id, final, events=()):
    return dict(
        id=case_id,
        status="COMPLETED",
        events=[*events, {"type": "final", **final}],
        executionTime=1000,
        wall_s=2,
    )


@pytest.mark.parametrize("suite", ["e2", "e3", "e4", "e5"])
def test_build(tmp_path, suite):
    cases = ev.build(suite, tmp_path, 1)
    assert cases == ev.load_jsonl(tmp_path / suite / "cases.jsonl")
    assert cases and cases[0]["payload"]["task"] in ("read", "agent")
    if suite == "e2":
        prompts = json.loads((ev.ROOT / "train/agent_eval_prompts.json").read_text())
        assert len(prompts) >= 150
        assert sum(c["injection"] for c in prompts) >= 20
        assert len({c["lang"] for c in prompts}) == 4
    elif suite == "e3":
        for path in (tmp_path / suite / "pages").glob("*.png"):
            with Image.open(path) as image:
                assert image.width * image.height < 40_000_000
                image.load()
    elif suite == "e4":
        assert cases[0]["expected"]["name"]
        assert float(cases[0]["expected"]["percent"]) > 0
    else:
        assert cases[1]["depends_on"] == cases[0]["id"]


def test_router_score(tmp_path):
    cases = ev.build("e2", tmp_path, 2)
    ev.write_jsonl(
        tmp_path / "e2/results.jsonl",
        [
            result(
                cases[0]["id"],
                {"intent": cases[0]["expected"], "usage": {"prompt_tokens": 10}},
            ),
            result(cases[1]["id"], {"intent": "off_topic"}),
        ],
    )
    score = ev.score("e2", tmp_path)
    assert score["accuracy"] == 0.5
    assert score["execution_s"] == 2
    assert score["estimated_cost_usd"] == pytest.approx(2 * 0.69 / 3600)
    assert score["tokens"]["prompt_tokens"] == 10


def test_triage_score(tmp_path):
    directory = tmp_path / "e3"
    cases = [
        dict(
            id="a",
            pages=[
                dict(
                    page_id="p1",
                    source="sample",
                    variant="original",
                    expected={"doc_type": "statement"},
                ),
                dict(
                    page_id="p2",
                    source="sample",
                    variant="original",
                    expected={"doc_type": "invoice"},
                ),
                dict(
                    page_id="p3",
                    source="nonfinancial",
                    variant="nonfinancial",
                    expected={"financial": False},
                ),
            ],
        )
    ]
    ev.write_jsonl(directory / "cases.jsonl", cases)
    ev.write_jsonl(
        directory / "results.jsonl",
        [
            result(
                "a",
                {},
                [
                    {"type": "triage", "page_id": "p1", "doc_type": "statement"},
                    {"type": "triage", "page_id": "p2", "doc_type": "other"},
                    {
                        "type": "triage",
                        "page_id": "p3",
                        "doc_type": "other",
                        "is_financial_document": False,
                    },
                ],
            )
        ],
    )
    score = ev.score("e3", tmp_path)
    assert score["doc_type_accuracy"] == 0.5
    assert score["rejection_rate"] == 1


def test_data_score(tmp_path):
    cases = [
        dict(
            id=str(i),
            template="inflow",
            expected={"amount": "1234.56"},
            payload={"message": "Total?"},
        )
        for i in range(2)
    ]
    ev.write_jsonl(tmp_path / "e4/cases.jsonl", cases)
    ev.write_jsonl(
        tmp_path / "e4/results.jsonl",
        [
            result(
                "0",
                {"intent": "ask_data", "answer": "SGD 1,234.56"},
                [{"type": "tool"}],
            ),
            result("1", {"intent": "ask_data", "answer": "SGD 100"}),
        ],
    )
    score = ev.score("e4", tmp_path)
    assert score["accuracy"] == 0.5
    assert score["tool_call_rate"] == 0.5
    assert ev.answer_matches("Cannot find this in the documents.", {"refusal": True})
    assert not ev.answer_matches("Not available, perhaps 700.", {"refusal": True})


def test_writer_score(tmp_path):
    cases = ev.build("e5", tmp_path, 1)
    memo = {
        s["id"]: s["heading"] + "\n" + " ".join([s["id"]] * 20) for s in ev.SECTIONS
    }
    edit = {**memo, "red_flags": "Red flags\nBrief."}
    ev.write_jsonl(
        tmp_path / "e5/results.jsonl",
        [
            result(cases[0]["id"], {"memo": memo, "problems": []}),
            result(
                cases[1]["id"],
                {"memo": edit, "problems": [{"kind": "unverified_figure"}]},
            ),
        ],
    )
    score = ev.score("e5", tmp_path)
    assert score["all_sections_rate"] == 1
    assert score["problems"]["unverified_figure"] == 1
    assert score["memos"][1]["edit_followed"]
    assert score["memos"][1]["others_unchanged"]


def test_run_resume_and_errors(tmp_path, monkeypatch):
    cases = ev.build("e2", tmp_path, 3)
    path = tmp_path / "e2/results.jsonl"
    ev.write_jsonl(path, [result(cases[0]["id"], {"intent": "memo"})])
    calls = []
    monkeypatch.setattr(ev.client, "runpodctl_key", lambda: "secret-test-key")

    def call(endpoint, payload, key, **kwargs):
        calls.append(payload)
        assert kwargs == {"stream": True, "timeout": 900}
        if len(calls) == 1:
            raise RuntimeError(key)
        return {
            "status": "COMPLETED",
            "delayTime": 7,
            "executionTime": 10,
            "stream": [{"output": {"type": "final", "intent": "memo"}}],
        }

    monkeypatch.setattr(ev.client, "call_endpoint", call)
    ev.run("e2", tmp_path, "fake", concurrency=1)
    rows = ev.load_jsonl(path)
    assert len(calls) == 2 and len(rows) == 3
    assert rows[1]["error"] == "RuntimeError"
    assert "secret-test-key" not in path.read_text()
    ev.run("e2", tmp_path, "fake")
    # Only the failed case is sent again.
    assert len(calls) == 3
    assert ev.load_jsonl(path)[-1]["status"] == "COMPLETED"
    ev.run("e2", tmp_path, "fake")
    assert len(calls) == 3


def test_refusal_score_accepts_knowledge(tmp_path):
    ev.write_jsonl(
        tmp_path / "e4/cases.jsonl",
        [
            {
                "id": "0",
                "template": "refusal",
                "expected": {"refusal": True},
                "payload": {"message": "Credit score?"},
            },
            {
                "id": "1",
                "template": "refusal",
                "expected": {"refusal": True},
                "payload": {"message": "Credit score?"},
            },
            {
                "id": "2",
                "template": "inflow",
                "expected": {"amount": "1234.56"},
                "payload": {"message": "Total?"},
            },
        ],
    )
    ev.write_jsonl(
        tmp_path / "e4/results.jsonl",
        [
            result(
                "0",
                {"intent": "knowledge", "answer": "Not available in the documents."},
            ),
            result(
                "1", {"intent": "knowledge", "answer": "Not available, perhaps 700."}
            ),
            result("2", {"intent": "knowledge", "answer": "1234.56"}),
        ],
    )
    score = ev.score("e4", tmp_path)
    assert score["refusal_accuracy"] == 0.5
    assert score["per_template"]["refusal"] == {"accuracy": 0.5, "n": 2}
    assert score["accuracy"] == 0
