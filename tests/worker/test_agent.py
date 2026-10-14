import asyncio
import base64
import io
import json
import os
from collections import Counter
from pathlib import Path

import httpx
import pytest
from PIL import Image

os.environ["LEDGERLENS_NO_ENGINE"] = "1"
from worker import agent, client  # noqa: E402
from worker import handler as worker

STATEMENT = json.loads(Path("app/sample/biz_0000_p001.json").read_text())
PLAN = {
    "audience": "credit committee",
    "length": "standard",
    "sections": [],
    "focus": "",
    "language": "en",
}


def response(content):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content)
                    if isinstance(content, dict)
                    else content,
                }
            }
        ]
    }


def image(color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def document():
    return {"page_id": "p1", "doc_type": "statement", "document": STATEMENT}


class FakeEngine:
    def __init__(self, intent="memo", edits=None, invented=False, tools=False):
        self.intent = intent
        self.edits = edits or []
        self.invented = invented
        self.tools = tools
        self.calls = []
        self.streams = Counter()
        self.active = 0
        self.maximum = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.router_done = False
        self.analyst_during_router = False

    def record(self, request):
        self.calls.append(request.copy())
        self.usage["prompt_tokens"] += 2
        self.usage["completion_tokens"] += 3

    async def complete(self, request):
        self.record(request)
        system = request["messages"][0]["content"]
        if system == agent.ROUTER_SYSTEM:
            await asyncio.sleep(0.03)
            self.router_done = True
            return response(
                {
                    "intent": self.intent,
                    "reason": "Requested",
                    "plan": PLAN,
                    "edit_sections": self.edits,
                }
            )
        if system == agent.TRIAGE_SYSTEM:
            return response(
                {
                    "is_financial_document": True,
                    "doc_type": "statement",
                    "readable": "yes",
                }
            )
        if system == agent.EXTRACT_SYSTEM:
            return response(STATEMENT)
        if system == agent.DATA_AGENT_SYSTEM:
            if self.tools and request.get("tool_choice") != "none":
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": str(i),
                                        "type": "function",
                                        "function": {"name": name, "arguments": "{}"},
                                    }
                                    for i, name in enumerate(
                                        ("monthly_totals", "find_rows")
                                    )
                                ],
                            }
                        }
                    ]
                }
            return response(
                "The supplied rows support this answer."
                if self.tools
                else "SGD 999999999.99"
            )
        raise AssertionError(system)

    async def stream(self, request):
        self.record(request)
        system = request["messages"][0]["content"]
        section = next(
            (s for s in agent.SECTIONS if f"## {s['heading']}." in system), None
        )
        key = section["id"] if section else "answer"
        self.streams[key] += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0.01)
            text = f"## {section['heading']}\n" if section else ""
            text += "Observed evidence."
            if self.invented and key == "summary":
                text += " SGD 999999999.99"
            for chunk in (text[:5], "", text[5:]):
                yield chunk
                await asyncio.sleep(0)
        finally:
            self.active -= 1


def collect(run, data, engine):
    async def consume():
        return [event async for event in run(data, engine)]

    return asyncio.run(consume())


def test_memo_concurrency_rewrite_order_and_usage(monkeypatch):
    engine = FakeEngine(invented=True)
    original = agent.analyze

    def analyze(*args):
        engine.analyst_during_router = not engine.router_done
        return original(*args)

    monkeypatch.setattr(agent, "analyze", analyze)
    events = collect(
        agent.run_agent, {"message": "Write memo", "documents": [document()]}, engine
    )
    final = events[-1]
    assert final["type"] == "final"
    assert engine.analyst_during_router
    assert engine.maximum == len(agent.SECTIONS)
    assert engine.streams["summary"] == 2
    assert all(engine.streams[s["id"]] == 1 for s in agent.SECTIONS[1:])
    assert [e["section"] for e in events if e["type"] == "section_reset"] == ["summary"]
    assert list(final["memo"]) == [s["id"] for s in agent.SECTIONS]
    assert final["problems"][0]["kind"] == "unverified_figure"
    assert final["usage"] == {
        "prompt_tokens": 2 * len(engine.calls),
        "completion_tokens": 3 * len(engine.calls),
    }


def test_edit_only_named_section():
    engine = FakeEngine("edit_memo", edits=["cash_flow"])
    previous = {s["id"]: f"## {s['heading']}\nOriginal." for s in agent.SECTIONS}
    events = collect(
        agent.run_agent,
        {
            "message": "Change cash flow",
            "documents": [document()],
            "last_memo": previous,
        },
        engine,
    )
    assert engine.streams == {"cash_flow": 1}
    assert events[-1]["memo"]["summary"] == previous["summary"]
    writer = next(
        r
        for r in engine.calls
        if "Write this memo section" in r["messages"][0]["content"]
    )
    content = json.loads(writer["messages"][1]["content"])
    assert content["previous_text"] == previous["cash_flow"]
    assert content["problems"] == ["Change cash flow"]


def test_tool_rounds_two_calls_and_final_none():
    engine = FakeEngine("ask_data", tools=True)
    events = collect(
        agent.run_agent, {"message": "Query rows", "documents": [document()]}, engine
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert len(tools) == 2 * agent.MAX_TOOL_ROUNDS
    assert {e["name"] for e in tools} == {"monthly_totals", "find_rows"}
    calls = [
        r
        for r in engine.calls
        if r["messages"][0]["content"] == agent.DATA_AGENT_SYSTEM
    ]
    assert len(calls) == agent.MAX_TOOL_ROUNDS + 1
    assert calls[-1]["tool_choice"] == "none"
    assert events[-1]["problems"] == []


def test_tool_answer_check_rewrites_once():
    engine = FakeEngine("ask_data")
    events = collect(
        agent.run_agent, {"message": "Query", "documents": [document()]}, engine
    )
    assert engine.streams == {"answer": 1}
    assert any(e["type"] == "section_reset" for e in events)
    assert events[-1]["problems"] == []


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("memo", "needs_documents"),
        ("ask_data", "needs_documents"),
        ("off_topic", "off_topic"),
    ],
)
def test_overrides_and_canned_answers(intent, expected):
    engine = FakeEngine(intent)
    events = collect(agent.run_agent, {"message": "Request"}, engine)
    assert events[-1]["answer"] == agent.REPLIES[expected]
    assert len(engine.calls) == 1
    assert not engine.streams


def test_edit_without_previous_becomes_memo():
    events = collect(
        agent.run_agent,
        {"message": "Edit", "documents": [document()]},
        FakeEngine("edit_memo"),
    )
    assert events[-1]["intent"] == "memo"


def test_knowledge():
    engine = FakeEngine("knowledge")
    events = collect(agent.run_agent, {"message": "Credit?"}, engine)
    assert events[-1]["intent"] == "knowledge"
    assert len(engine.calls) == 2


def test_read_rejection_and_independent_pages(monkeypatch):
    class Reader(FakeEngine):
        async def complete(self, request):
            system = request["messages"][0]["content"]
            url = request["messages"][1]["content"][1]["image_url"]["url"]
            with Image.open(io.BytesIO(base64.b64decode(url.split(",")[1]))) as page:
                red, green, blue = page.getpixel((0, 0))
            if system == agent.TRIAGE_SYSTEM:
                if blue > red:
                    return response(
                        {
                            "is_financial_document": False,
                            "doc_type": "other",
                            "readable": "yes",
                        }
                    )
                if red > green:
                    await asyncio.sleep(0.05)
            return await super().complete(request)

    engine = Reader()
    events = collect(
        agent.run_read,
        {
            "pages": [
                {"page_id": name, "image_base64": image(color)}
                for name, color in (
                    ("slow", "red"),
                    ("fast", "white"),
                    ("rejected", "blue"),
                )
            ]
        },
        engine,
    )
    assert [e["page_id"] for e in events if e["type"] == "rows"] == ["fast", "slow"]
    assert (
        sum(r["messages"][0]["content"] == agent.EXTRACT_SYSTEM for r in engine.calls)
        == 2
    )
    rejected = next(
        e for e in events if e["type"] == "triage" and e["page_id"] == "rejected"
    )
    assert not rejected["accepted"]


@pytest.mark.parametrize(
    "data",
    [
        {"pages": [{"page_id": str(i), "image_base64": ""} for i in range(9)]},
        {"pages": [{"page_id": "p1", "image_base64": ""}] * 2},
        {"pages": [{"page_id": "p1", "image_base64": "a" * (9 * 1024 * 1024)}]},
    ],
)
def test_read_limits(data):
    engine = FakeEngine()
    events = collect(agent.run_read, data, engine)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert not engine.calls


@pytest.mark.parametrize("limit", [5, 1000])
def test_emitter_batches_and_flushes_before_events(monkeypatch, limit):
    monkeypatch.setattr(agent, "STREAM_FLUSH_CHARS", limit)
    monkeypatch.setattr(agent, "STREAM_FLUSH_SECONDS", 0.01)

    async def work(emit):
        for part, text in (
            ("summary", "a"),
            ("summary", "bc"),
            ("cash_flow", "de"),
            ("answer", ""),
        ):
            await emit({"type": "_text", "part": part, "text": text})
        await emit({"type": "section_reset", "section": "summary"})
        await emit({"type": "_text", "part": "answer", "text": "tail"})
        await asyncio.sleep(0.03)
        await emit({"type": "section", "section": "answer"})

    async def consume():
        return [e async for e in agent.events(work, FakeEngine())]

    events = asyncio.run(consume())
    assert events[0] == {
        "type": "deltas",
        "parts": [
            {"part": "summary", "text": "abc"},
            {"part": "cash_flow", "text": "de"},
        ],
    }
    assert events[1]["type"] == "section_reset"
    assert events[2] == {
        "type": "deltas",
        "parts": [{"part": "answer", "text": "tail"}],
    }
    assert all(p["text"] for e in events if e["type"] == "deltas" for p in e["parts"])


def test_engine_http_usage():
    def respond(request):
        assert request.url.path == "/v1/chat/completions"
        data = json.loads(request.content)
        if data.get("stream"):
            assert data["stream_options"]["include_usage"]
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\ndata: [DONE]\n',
            )
        return httpx.Response(
            200,
            json={
                **response("hi"),
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        )

    async def run():
        async with agent.Engine(
            httpx.AsyncClient(
                base_url=worker.BASE_URL + "/v1/",
                transport=httpx.MockTransport(respond),
            )
        ) as engine:
            await engine.complete({})
            assert [text async for text in engine.stream({})] == ["hi"]
            assert engine.usage == {"prompt_tokens": 7, "completion_tokens": 10}

    asyncio.run(run())


def test_handler_plain_prompt_and_extract(monkeypatch):
    async def fake(data, engine):
        assert data["message"] == "Explain credit"
        yield {"type": "final", "intent": "knowledge", "pages": data.get("new_pages")}

    monkeypatch.setattr(agent, "run_agent", fake)

    async def run():
        assert [
            e async for e in worker.handler({"input": {"prompt": "Explain credit"}})
        ] == [{"type": "final", "intent": "knowledge", "pages": None}]
        page = image()
        events = [
            e
            async for e in worker.handler(
                {"input": {"prompt": "Explain credit", "image_base64": page}}
            )
        ]
        assert events[0]["pages"] == [{"page_id": "p1", "image_base64": page}]
        monkeypatch.setattr(
            worker, "vllm_client", lambda _: iter([response(STATEMENT)])
        )
        events = [
            e
            async for e in worker.handler(
                {
                    "input": {
                        "task": "extract",
                        "doc_type": "statement",
                        "image_base64": image(),
                    }
                }
            )
        ]
        assert events[0]["valid"]

    asyncio.run(run())


def test_failure_cancels_other_sections():
    class Broken(FakeEngine):
        async def stream(self, request):
            if "## Summary." in request["messages"][0]["content"]:
                await asyncio.sleep(0.005)
                raise RuntimeError("writer failed")
            self.active += 1
            try:
                await asyncio.sleep(10)
                yield "unreachable"
            finally:
                self.active -= 1

    engine = Broken()
    events = collect(
        agent.run_agent, {"message": "Memo", "documents": [document()]}, engine
    )
    assert events[-1] == {"type": "error", "message": "writer failed"}
    assert engine.active == 0
    assert not any(e["type"] == "final" for e in events)


@pytest.mark.parametrize("task", ["agent", "read"])
def test_cli_event_lines_and_summary(monkeypatch, tmp_path, capsys, task):
    monkeypatch.setenv("RUNPOD_API_KEY", "secret")
    page = tmp_path / "page.png"
    page.write_bytes(base64.b64decode(image()))

    def call(endpoint, payload, key, **kwargs):
        assert kwargs["stream"]
        assert payload["task"] == task
        assert len(payload["pages" if task == "read" else "new_pages"]) == 2
        kwargs["on_event"]({"type": "final", "intent": task, "timing": {"total_s": 1}})
        return {"status": "COMPLETED", "delayTime": 2, "executionTime": 3}

    monkeypatch.setattr(client, "call_endpoint", call)
    args = [task, "--endpoint-id", "id", "--image", str(page), "--image", str(page)]
    if task == "agent":
        args += ["--message", "Request"]
    assert client.main(args) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2 and lines[0]["type"] == "final"
    assert lines[1] == {
        "intent": task,
        "total_s": 1,
        "delayTime": 2,
        "executionTime": 3,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"message": ""},
        {"new_pages": [{"page_id": str(i), "image_base64": ""} for i in range(9)]},
        {
            "documents": [document()],
            "new_pages": [{"page_id": "p1", "image_base64": ""}],
        },
        {"new_pages": [{"page_id": "p2", "image_base64": "a" * (9 * 1024 * 1024)}]},
    ],
)
def test_agent_limits_before_router(fields):
    engine = FakeEngine()
    events = collect(agent.run_agent, {"message": "Memo", **fields}, engine)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert not engine.calls


def test_read_dimensions_checked_before_model_call(monkeypatch):
    monkeypatch.setattr(worker, "MAX_INPUT_PIXELS", 100)
    engine = FakeEngine()
    events = collect(
        agent.run_read, {"pages": [{"page_id": "p1", "image_base64": image()}]}, engine
    )
    assert events[-1]["type"] == "final" and events[-1]["documents"] == []
    assert events[-2]["type"] == "rows" and not events[-2]["valid"]
    assert "megapixels" in events[-2]["validation_error"]
    assert events[-3] == {
        "type": "step",
        "name": "triage",
        "status": "error",
        "page_id": "p1",
    }
    assert not engine.calls


@pytest.mark.parametrize("task", ["read", "agent"])
def test_async_handler_failed_engine(monkeypatch, task):
    monkeypatch.setattr(worker, "ENGINE", {"status": "failed", "error": "unavailable"})

    async def run():
        return [event async for event in worker.handler({"input": {"task": task}})]

    assert asyncio.run(run()) == [
        {"error": "Model engine failed to start: unavailable"}
    ]


def test_tool_calls_per_round_capped(monkeypatch):
    monkeypatch.setattr(agent, "MAX_TOOL_CALLS_PER_ROUND", 1)
    engine = FakeEngine("ask_data", tools=True)
    events = collect(
        agent.run_agent, {"message": "Query rows", "documents": [document()]}, engine
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert [e["name"] for e in tools[:2]] == ["monthly_totals", "find_rows"]
    assert "monthly" in tools[0]["result"]
    assert tools[1]["result"] == {"error": "Too many tool calls in one round"}
