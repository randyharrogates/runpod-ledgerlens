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
    assert content["edit_request"] == "Change cash flow"
    assert "problems" not in content


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
    assert "A memo has been written: no." in engine.calls[0]["messages"][1]["content"]
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
    assert [event["type"] for event in events] == ["log", "error"]
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
    assert {k: v for k, v in events[0].items() if k != "ts"} == {
        "type": "deltas",
        "parts": [
            {"part": "summary", "text": "abc"},
            {"part": "cash_flow", "text": "de"},
        ],
    }
    assert events[1]["type"] == "section_reset"
    assert {k: v for k, v in events[2].items() if k != "ts"} == {
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
    assert {k: v for k, v in events[-1].items() if k != "ts"} == {
        "type": "error",
        "message": "writer failed",
    }
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
    assert [event["type"] for event in events] == ["log", "error"]
    assert not engine.calls


def test_read_dimensions_checked_before_model_call(monkeypatch):
    monkeypatch.setattr(worker, "MAX_INPUT_PIXELS", 100)
    engine = FakeEngine()
    events = collect(
        agent.run_read, {"pages": [{"page_id": "p1", "image_base64": image()}]}, engine
    )
    assert events[-1]["type"] == "final" and events[-1]["documents"] == []
    rows = [event for event in events if event["type"] == "rows"]
    assert len(rows) == 1 and not rows[0]["valid"]
    assert "megapixels" in rows[0]["validation_error"]
    assert {
        k: v
        for k, v in [event for event in events if event["type"] == "step"][-1].items()
        if k != "ts"
    } == {
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


def test_engine_call_and_stream_logs():
    entries = []

    async def log(level, message, **fields):
        entries.append({"level": level, "message": message, **fields})

    def respond(request):
        data = json.loads(request.content)
        if data.get("stream"):
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n',
            )
        return httpx.Response(
            200,
            json={
                **response("ok"),
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    async def run():
        async with agent.Engine(
            httpx.AsyncClient(
                base_url="http://test/v1/", transport=httpx.MockTransport(respond)
            )
        ) as engine:
            engine.log = log
            request = agent.payload(
                "system", "text", model="test-model", schema={}, name="route"
            )
            await engine.complete(request)
            assert "".join([text async for text in engine.stream(request)]) == "hello"

    asyncio.run(run())
    assert len(entries) == 2
    assert entries[0]["level"] == "info"
    assert "model call route on test-model" in entries[0]["message"]
    assert "11 tokens in / 7 out" in entries[0]["message"]
    assert entries[0]["model"] == "test-model" and entries[0]["ms"] >= 0
    assert "5 chars" in entries[1]["message"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("transport_error", [False, True])
def test_engine_error_logs(stream, transport_error):
    entries = []

    async def log(level, message, **fields):
        entries.append((level, message))

    def respond(request):
        if transport_error:
            raise httpx.ConnectError("connection unavailable")
        return httpx.Response(500, text="engine unavailable")

    async def run():
        async with agent.Engine(
            httpx.AsyncClient(
                base_url="http://test/v1/", transport=httpx.MockTransport(respond)
            )
        ) as engine:
            engine.log = log
            with pytest.raises(httpx.HTTPError):
                if stream:
                    async for _ in engine.stream({"model": "test-model"}):
                        pass
                else:
                    await engine.complete({"model": "test-model"})

    asyncio.run(run())
    assert len(entries) == 1 and entries[0][0] == "error"
    assert (
        "ConnectError" if transport_error else "HTTP 500 engine unavailable"
    ) in entries[0][1]


@pytest.mark.parametrize("count,steps", [(30, False), (5, True)])
def test_log_batching(count, steps):
    async def work(emit):
        for i in range(count):
            await emit(
                {"type": "_log", "level": "info", "source": "worker", "message": str(i)}
            )
            if steps:
                await emit({"type": "step", "name": "reader", "status": "start"})
        await emit({"type": "final"})

    async def run():
        return [event async for event in agent.events(work, FakeEngine())]

    events = asyncio.run(run())
    logs = [event for event in events if event["type"] == "log"]
    assert len(logs) <= (2 if steps else 3)
    assert [entry["message"] for event in logs for entry in event["entries"]] == [
        str(i) for i in range(count)
    ]
    assert events[-1]["type"] == "final"
    if steps:
        assert [event["type"] for event in events[:count]] == ["step"] * count


def test_work_exception_logged_before_error():
    async def work(emit):
        raise ValueError("broken")

    async def run():
        return [event async for event in agent.events(work, FakeEngine())]

    events = asyncio.run(run())
    assert [event["type"] for event in events] == ["log", "error"]
    assert events[0]["entries"][0]["message"] == "ValueError: broken"


@pytest.mark.parametrize(
    "intent,invented,tools", [("memo", True, False), ("ask_data", False, True)]
)
def test_agent_logs_do_not_contain_documents(intent, invented, tools):
    events = collect(
        agent.run_agent,
        {"message": "Help", "documents": [document()]},
        FakeEngine(intent, invented=invented, tools=tools),
    )
    messages = [
        entry["message"]
        for event in events
        if event["type"] == "log"
        for entry in event["entries"]
    ]
    assert f"route: {intent}" in messages
    if invented:
        assert any("rewriting once" in message for message in messages)
    else:
        assert any("tool budget used up" in message for message in messages)
    assert all(
        "base64" not in message
        and STATEMENT["transactions"][0]["description"] not in message
        for message in messages
    )


def test_reader_validation_log_summary():
    class InvalidEngine(FakeEngine):
        async def complete(self, request):
            if request["messages"][0]["content"] == agent.EXTRACT_SYSTEM:
                return response({"transactions": "PRIVATE DOCUMENT TEXT"})
            return await super().complete(request)

    events = collect(
        agent.run_read,
        {"pages": [{"page_id": "p1", "image_base64": image()}]},
        InvalidEngine(),
    )
    entries = [
        entry
        for event in events
        if event["type"] == "log"
        for entry in event["entries"]
    ]
    assert any(
        "first at" in entry["message"] and "missing" in entry["message"]
        for entry in entries
    )
    assert all("PRIVATE DOCUMENT TEXT" not in entry["message"] for entry in entries)


def test_vllm_listener_filters_caps_and_unregisters():
    async def work(emit):
        await asyncio.to_thread(worker.notify_vllm, "INFO ignored")
        for i in range(60):
            await asyncio.to_thread(worker.notify_vllm, f"ERROR engine {i}")
        await asyncio.sleep(0)
        await emit({"type": "final"})

    async def run():
        return [event async for event in agent.events(work, FakeEngine())]

    before = set(worker.VLLM_LISTENERS)
    events = asyncio.run(run())
    entries = [
        entry
        for event in events
        if event["type"] == "log"
        for entry in event["entries"]
    ]
    assert len(entries) == 50
    assert all(
        entry["source"] == "vllm" and entry["level"] == "error" for entry in entries
    )
    assert worker.VLLM_LISTENERS == before


def test_log_deadline_and_text_flush(monkeypatch):
    monkeypatch.setattr(agent, "STREAM_FLUSH_SECONDS", 0.01)
    monkeypatch.setattr(agent, "STREAM_FLUSH_CHARS", 5)

    async def work(emit):
        await emit(
            {"type": "_log", "level": "info", "source": "worker", "message": "deadline"}
        )
        await asyncio.sleep(0.03)
        await emit({"type": "step", "name": "reader", "status": "start"})
        await emit(
            {
                "type": "_log",
                "level": "info",
                "source": "worker",
                "message": "with text",
            }
        )
        await emit({"type": "_text", "part": "answer", "text": "hello"})
        await emit(
            {
                "type": "_log",
                "level": "info",
                "source": "worker",
                "message": "producer end",
            }
        )

    async def run():
        return [event async for event in agent.events(work, FakeEngine())]

    events = asyncio.run(run())
    assert [event["type"] for event in events] == [
        "log",
        "step",
        "deltas",
        "log",
        "log",
    ]
    assert [
        event["entries"][0]["message"] for event in events if event["type"] == "log"
    ] == ["deadline", "with text", "producer end"]


def test_log_count_flush_retains_text_deadline(monkeypatch):
    monkeypatch.setattr(agent, "STREAM_FLUSH_SECONDS", 0.1)
    monkeypatch.setattr(agent, "STREAM_FLUSH_CHARS", 1000)

    async def work(emit):
        await emit({"type": "_text", "part": "answer", "text": "tail"})
        for i in range(20):
            await emit(
                {"type": "_log", "level": "info", "source": "worker", "message": str(i)}
            )
        await asyncio.sleep(0.3)
        await emit({"type": "step", "name": "reader", "status": "start"})

    async def run():
        return [event async for event in agent.events(work, FakeEngine())]

    events = asyncio.run(run())
    assert [event["type"] for event in events] == ["log", "deltas", "step"]
    assert len(events[0]["entries"]) == 20


@pytest.mark.parametrize(
    "intent,docs,last,expected",
    [
        ("memo", True, False, "memo"),
        ("ask_data", False, False, "needs_documents"),
        ("edit_memo", True, False, "memo"),
        ("edit_memo", True, True, "edit_memo"),
        ("off_topic", True, False, "off_topic"),
    ],
)
def test_route_stop(intent, docs, last, expected, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("route-only ran analysis/tool")

    monkeypatch.setattr(agent, "analyze", forbidden)
    monkeypatch.setattr(agent, "run_tool", forbidden)
    engine = FakeEngine(intent=intent)
    data = dict(
        message="Review this",
        documents=[document()] if docs else [],
        stop_after="route",
        new_pages=[{"page_id": "p2", "image_base64": image()}],
    )
    if last:
        data["last_memo"] = {"summary": "Summary\nExisting memo"}

    async def collect():
        return [e async for e in agent.run_agent(data, engine)]

    events = asyncio.run(collect())
    final = next(e for e in events if e["type"] == "final")
    assert final["intent"] == expected
    assert final["problems"] == []
    assert final["plan"] == PLAN
    assert "usage" in final and "timing" in final
    assert len(engine.calls) == 1
    assert (
        f"A memo has been written: {'yes' if last else 'no'}."
        in engine.calls[0]["messages"][1]["content"]
    )
    assert not engine.streams
    assert not any(e["type"] in ("rows", "triage", "tool", "section") for e in events)


def test_triage_stop():
    engine = FakeEngine()

    async def collect():
        return [
            e
            async for e in agent.run_read(
                {
                    "pages": [
                        {"page_id": f"p{i}", "image_base64": image()} for i in range(3)
                    ],
                    "stop_after": "triage",
                },
                engine,
            )
        ]

    events = asyncio.run(collect())
    final = next(e for e in events if e["type"] == "final")
    assert final["documents"] == []
    assert len(final["triage"]) == 3
    assert len(engine.calls) == 3
    assert all(c["messages"][0]["content"] == agent.TRIAGE_SYSTEM for c in engine.calls)
    assert not any(e["type"] == "rows" for e in events)


@pytest.mark.parametrize(
    "read,value", [(False, "triage"), (True, "route"), (False, None), (True, 1)]
)
def test_stop_validation(read, value):
    with pytest.raises(ValueError, match="stop_after"):
        agent.validate({"message": "hello", "stop_after": value}, read=read)


@pytest.mark.parametrize("loaded", [False, True])
def test_knowledge_document_prompt(loaded):
    engine = FakeEngine("knowledge")
    events = collect(
        agent.run_agent,
        {
            "message": "Director credit score?",
            "documents": [document()] if loaded else [],
        },
        engine,
    )
    assert events[-1]["intent"] == "knowledge"
    system = engine.calls[-1]["messages"][0]["content"]
    assert ("no documents were read" in system) is not loaded
    if loaded:
        assert "loaded documents do not show this" in system


@pytest.mark.parametrize(
    "message,previous",
    [
        ("Make summary shorter", "word " * 100),
        ("精简摘要", "中文字词" * 50),
    ],
)
@pytest.mark.parametrize("answer_words", [78, 90])
def test_shorter_edit_rewrites_to_target(message, previous, answer_words):
    class LongEngine(FakeEngine):
        async def stream(self, request):
            if self.streams["summary"] == 0:
                self.record(request)
                self.streams["summary"] += 1
                yield "## Summary\n" + "word " * answer_words
            else:
                async for chunk in super().stream(request):
                    yield chunk

    engine = LongEngine("edit_memo", edits=["summary"])
    previous = "## Summary\n" + previous
    target = max(25, agent.word_count(previous) * 3 // 5)
    events = collect(
        agent.run_agent,
        {
            "message": message,
            "documents": [document()],
            "last_memo": {"summary": previous},
        },
        engine,
    )
    assert engine.streams["summary"] == (2 if answer_words == 90 else 1)
    writers = [
        r
        for r in engine.calls
        if "Write this memo section" in r["messages"][0]["content"]
    ]
    assert all(
        f"at most {target} words" in r["messages"][0]["content"] for r in writers
    )
    if answer_words == 78:
        assert events[-1]["problems"] == []
        return
    rewrite = json.loads(writers[1]["messages"][1]["content"])
    assert rewrite["problems"] == [
        {
            "kind": "too_long",
            "detail": f"92 words exceeds {max(25, (agent.word_count(previous) * 4 + 4) // 5)}",
        }
    ]
    assert events[-1]["problems"] == []


@pytest.mark.parametrize("rewrite", [False, True])
def test_data_name_placeholders(monkeypatch, rewrite):
    original = {
        "counterparties": [
            {"payer": "星林峰服务", "share_percent": "22.1", "cites": ["[p1:r6]"]}
        ]
    }
    monkeypatch.setattr(agent, "run_tool", lambda *args: original)

    class NameEngine(FakeEngine):
        async def complete(self, request):
            if request["messages"][0]["content"] != agent.DATA_AGENT_SYSTEM:
                return await super().complete(request)
            self.record(request)
            if not any(m["role"] == "tool" for m in request["messages"]):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "1",
                                        "type": "function",
                                        "function": {
                                            "name": "top_counterparties",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            return response(
                "Invented 六福珠宝"
                if rewrite
                else "The largest payer is NAME_1, 22.1% [p1:r6]"
            )

        async def stream(self, request):
            self.record(request)
            yield "The largest payer is NAME_"
            yield "1, 22.1% [p1:r6]"

    original["rows"] = [
        {"description": "星林峰服务"},
        {"description": "FROM 星林峰服务"},
        {"description": "FROM 星林峰服务"},
        {"description": "Café Services"},
    ]
    engine = NameEngine("ask_data")
    events = collect(
        agent.run_agent,
        {"message": "Largest payer?", "documents": [document()]},
        engine,
    )
    final = events[-1]
    assert "星林峰服务" in final["answer"]
    assert "NAME_1" not in final["answer"]
    assert final["problems"] == []
    assert next(e for e in events if e["type"] == "tool")["result"] == original
    tool_message = next(
        m for r in engine.calls for m in r["messages"] if m["role"] == "tool"
    )
    assert "NAME_1" in tool_message["content"]
    assert "星林峰服务" not in tool_message["content"]
    masked = json.loads(tool_message["content"])
    assert [r["description"] for r in masked["rows"]] == [
        "NAME_1",
        "NAME_2",
        "NAME_2",
        "Café Services",
    ]
    if rewrite:
        instruction = engine.calls[-1]["messages"][-1]["content"]
        assert "Your answer had these problems:" in instruction
        assert '[{"kind"' not in instruction
        assert "Do not mention these problems" in instruction
    displayed = ""
    for event in events:
        if event["type"] == "section_reset":
            displayed = ""
        if event["type"] == "deltas":
            displayed += "".join(
                p["text"] for p in event["parts"] if p["part"] == "answer"
            )
    assert displayed == final["answer"]
    assert "NAME_1" not in displayed


def test_writer_gets_share_percent_not_raw_share():
    engine = FakeEngine()
    collect(
        agent.run_agent, {"message": "Write memo", "documents": [document()]}, engine
    )
    writer = [
        c["messages"][1]["content"]
        for c in engine.calls
        if "top_payer_concentration" in str(c["messages"][-1]["content"])
    ]
    assert writer
    assert all('"share_percent"' in text and '"share"' not in text for text in writer)


@pytest.mark.parametrize("intent", ["memo", "edit_memo"])
def test_report_precedes_writers_and_has_checked_data(intent):
    events = collect(
        agent.run_agent,
        {
            "message": "Write memo",
            "documents": [document()],
            "last_memo": {"summary": "## Summary\nPrevious"},
        },
        FakeEngine(intent),
    )
    report = next(e for e in events if e["type"] == "report")
    assert all(isinstance(e["ts"], float) for e in events)
    assert events.index(report) < next(
        i for i, e in enumerate(events) if e["type"] == "deltas"
    )
    assert events[-1]["report"] == report
    assert report["borrower"] == STATEMENT["account_holder"]
    assert report["period"] == {
        "start": STATEMENT["period_start"],
        "end": STATEMENT["period_end"],
    }
    assert [m["month"] for m in report["monthly"]] == sorted(
        m["month"] for m in report["monthly"]
    )
    from ledgerlens.agent.report import LABELS

    assert all(f["label"] == LABELS[f["type"]] for f in report["findings"])
    expected = agent.analyze(
        [worker.MODELS["statement"].model_validate(STATEMENT)], ["p1"]
    )
    assert report["top_payer"]["share_percent"] == str(
        round(expected["top_payer_concentration"]["share"] * 100, 2)
    )
    assert report["evidence"] == {"statements": 1, "invoices": 0, "page_ids": ["p1"]}


def test_report_commentary_instructions():
    instructions = {s["id"]: s["instruction"] for s in agent.SECTIONS}
    assert "two or three sentences" in instructions["summary"]
    assert "do not list every month" in instructions["cash_flow"]
    assert "why it matters to a lender" in instructions["concentration"]
    assert "Do not guess causes" in instructions["red_flags"]
    assert "numbered list, one question per concern" in instructions["questions"]


def test_report_fills_finding_amounts_and_caps_citations():
    from decimal import Decimal

    from ledgerlens.agent.report import build_report

    statement = worker.MODELS["statement"].model_validate(STATEMENT)
    analysis = agent.analyze([statement], ["p1"])
    citation = {"page_id": "p1", "row_index": 0}
    analysis["findings"] = [
        {
            "type": "returned_cheque",
            "description": "Keep this description",
            "citations": [{"page_id": "p1", "row_index": -1}] + [citation] * 5,
        },
        {
            "type": "unreconciled_page",
            "page_id": "p1",
            "citations": [],
            "difference": Decimal("-123.45"),
        },
    ]
    analysis["reconciliation"][0]["difference"] = Decimal("-123.45")
    report = build_report([statement], [], ["p1"], analysis, {})
    row = statement.transactions[0]
    assert Decimal(report["findings"][0]["amount"]) == (row.debit or row.credit)
    assert report["findings"][0]["description"] == "Keep this description"
    assert report["findings"][1]["amount"] == "123.45"
    assert all(len(f["cites"]) <= 3 for f in report["findings"])


def test_report_amounts_from_cited_analysis():
    # The worker passes cited analysis: Decimals as strings, citations as "[p1:r1]".
    from decimal import Decimal

    from ledgerlens.agent.report import build_report

    statement = worker.MODELS["statement"].model_validate(STATEMENT)
    analysis = agent.analyze([statement], ["p1"])
    analysis["findings"] = [
        {
            "type": "returned_cheque",
            "description": "d",
            "citations": [{"page_id": "p1", "row_index": 0}],
        },
        {
            "type": "unreconciled_page",
            "page_id": "p1",
            "description": "d",
            "citations": [],
        },
    ]
    analysis["reconciliation"][0]["difference"] = Decimal("-123.45")
    report = build_report([statement], [], ["p1"], agent.cited(analysis), {})
    row = statement.transactions[0]
    assert Decimal(report["findings"][0]["amount"]) == (row.debit or row.credit)
    assert report["findings"][1]["amount"] == "123.45"


@pytest.mark.parametrize(
    "intent,target,key",
    [
        ("capabilities", "text", "capabilities"),
        ("edit_memo", "document_facts", "document_facts"),
    ],
)
def test_fixed_replies_no_writer(intent, target, key):
    class RouterOnly(FakeEngine):
        async def complete(self, request):
            self.record(request)
            assert request["messages"][0]["content"] == agent.ROUTER_SYSTEM
            return response(
                {
                    "intent": intent,
                    "reason": "Requested",
                    "plan": PLAN,
                    "edit_sections": ["summary"],
                    "edit_target": target,
                }
            )

    engine = RouterOnly()
    previous = {"summary": "## Summary\nOriginal."}
    final = collect(
        agent.run_agent,
        {"message": "Edit memo?", "documents": [document()], "last_memo": previous},
        engine,
    )[-1]
    assert final["answer"] == agent.REPLIES[key]
    assert "memo" not in final
    assert previous == {"summary": "## Summary\nOriginal."}
    assert len(engine.calls) == 1 and not engine.streams


def test_unchanged_edit_problem():
    engine = FakeEngine("edit_memo", edits=["summary"])
    final = collect(
        agent.run_agent,
        {
            "message": "Rewrite summary",
            "documents": [document()],
            "last_memo": {"summary": "## Summary\nObserved evidence."},
        },
        engine,
    )[-1]
    assert final["problems"] == [
        {
            "kind": "edit_not_applied",
            "section": "summary",
            "detail": "The requested change was not made to this section.",
        }
    ]


def test_edit_explicit_target_left_behind():
    assert agent.edit_not_applied(
        "Old commentary about Cedar Trading",
        "Completely rewritten commentary about Cedar Trading",
        'remove "Cedar Trading"',
    )
    assert agent.edit_not_applied(
        "Old commentary about Cedar Trading",
        "Completely rewritten commentary about Cedar Trading",
        "change all mention of Cedar Trading to Pine Trading",
    )
    assert not agent.edit_not_applied(
        "Old commentary about Cedar Trading",
        "New commentary about Pine Trading",
        'replace "Cedar Trading" with "Pine Trading"',
    )


def test_balance_gap_report():
    from decimal import Decimal

    from ledgerlens.agent.report import build_report
    from ledgerlens.analytics import analyze
    from ledgerlens.schema import Statement

    prior = Statement.model_validate(STATEMENT)
    current = prior.model_copy(deep=True)
    prior.closing_balance = Decimal("6014.30")
    current.opening_balance = Decimal("6204.27")
    report = build_report(
        [prior, current],
        [],
        ["p5", "p6"],
        analyze([prior, current], ["p5", "p6"]),
        PLAN,
    )
    flag = next(f for f in report["findings"] if f["type"] == "balance_gap")
    assert flag["label"] == "Balance gap between pages"
    assert flag["amount"] == "189.97"
    assert (
        "p5 closes at SGD 6,014.30 but p6 opens at SGD 6,204.27" in flag["description"]
    )
    assert len(flag["cites"]) == 2


def test_writer_subset_bounds():
    subset = agent.writer_subset(
        {
            "findings": [{"citations": ["ref"] * 2500}] * 100,
            "reconciliation": [{}] * 100,
        }
    )
    assert len(subset["findings"]) == len(subset["reconciliation"]) == 50
    assert len(subset["findings"][0]["citations"]) == 3


@pytest.mark.parametrize(
    "message,key,routed",
    [
        ("are you able to edit the memo for me?", "capabilities", "edit_memo"),
        (
            "change all mention of Bayu Emas Murni Niaga to Bank Singapore",
            "document_facts",
            "edit_memo",
        ),
        # Seen live on 15 Oct: the router sent this to off_topic.
        (
            "can you remove Jan 2026 cashflow line for me from the memo",
            "document_facts",
            "off_topic",
        ),
    ],
)
def test_rules_override_router_without_writer(message, key, routed):
    class WrongRouter(FakeEngine):
        async def complete(self, request):
            self.record(request)
            assert request["messages"][0]["content"] == agent.ROUTER_SYSTEM
            return response(
                {
                    "intent": routed,
                    "edit_target": "text",
                    "reason": "Requested",
                    "plan": PLAN,
                    "edit_sections": ["summary"],
                }
            )

    engine = WrongRouter()
    events = collect(
        agent.run_agent,
        {
            "message": message,
            "documents": [document()],
            "last_memo": {"summary": "## Summary\nOriginal."},
        },
        engine,
    )
    route = next(event for event in events if event["type"] == "route")
    assert route["rule"] == key
    assert events[-1]["answer"] == agent.REPLIES[key]
    assert events[-1]["intent"] == (
        "capabilities" if key == "capabilities" else "edit_memo"
    )
    if key == "document_facts":
        assert events[-1]["edit_target"] == "document_facts"
    assert len(engine.calls) == 1
    assert not engine.streams


@pytest.mark.parametrize(
    "message",
    [
        "Remove the questions section.",
        "hapus bagian pertanyaan",
        "删除问题部分",
    ],
)
def test_remove_section_without_writer(message):
    engine = FakeEngine("edit_memo")
    previous = {s["id"]: f"## {s['heading']}\nOriginal." for s in agent.SECTIONS}
    events = collect(
        agent.run_agent,
        {
            "message": message,
            "documents": [document()],
            "last_memo": previous,
        },
        engine,
    )
    assert not engine.streams
    assert events[-1]["memo"] == {**previous, "questions": ""}
    assert next(e for e in events if e["type"] == "section")["removed"] is True


def test_restore_removed_section_and_preserve_it_in_other_edits():
    previous = {s["id"]: f"## {s['heading']}\nOriginal." for s in agent.SECTIONS}
    previous["questions"] = ""
    for message, restored in [
        ("add the questions section back", True),
        ("Make summary formal", False),
    ]:
        engine = FakeEngine(
            "edit_memo", edits=["questions"] if restored else ["summary"]
        )
        events = collect(
            agent.run_agent,
            {
                "message": message,
                "documents": [document()],
                "last_memo": previous,
            },
            engine,
        )
        assert bool(events[-1]["memo"]["questions"]) is restored
        if restored:
            content = json.loads(engine.calls[-1]["messages"][1]["content"])
            assert "previous_text" not in content and "edit_request" not in content


def test_reverse_router_override_and_section_union():
    class Misrouter(FakeEngine):
        async def complete(self, request):
            if request["messages"][0]["content"] == agent.ROUTER_SYSTEM:
                self.record(request)
                return response(
                    {
                        "intent": "edit_memo",
                        "edit_target": "document_facts",
                        "reason": "Requested",
                        "plan": PLAN,
                        "edit_sections": ["summary"],
                    }
                )
            return await super().complete(request)

    engine = Misrouter()
    previous = {s["id"]: f"## {s['heading']}\nOriginal." for s in agent.SECTIONS}
    events = collect(
        agent.run_agent,
        {
            "message": "Remove the sentence about March from the cash flow section",
            "documents": [document()],
            "last_memo": previous,
        },
        engine,
    )
    route = next(e for e in events if e["type"] == "route")
    assert route["rule"] == "prose_edit" and route["edit_target"] == "text"
    assert set(engine.streams) == {"summary", "cash_flow"}


def test_unchanged_edit_retries_exactly_once():
    engine = FakeEngine("edit_memo", edits=["summary"])
    events = collect(
        agent.run_agent,
        {
            "message": "Rewrite summary",
            "documents": [document()],
            "last_memo": {"summary": "## Summary\nObserved evidence."},
        },
        engine,
    )
    assert engine.streams == {"summary": 2}
    assert events[-1]["problems"][0]["kind"] == "edit_not_applied"
    assert next(e for e in events if e["type"] == "section")["rewritten"]
    content = json.loads(engine.calls[-1]["messages"][1]["content"])
    assert content["edit_request"] == "Rewrite summary"
    assert "must change" in content["problems"][-1]["detail"]


@pytest.mark.parametrize(
    "message,words,expected",
    [
        ("Expand the summary", 150, 304),
        ("Tambah ringkasan", 250, 400),
        ("Make the summary formal", 150, 192),
    ],
)
def test_edit_length_budget(message, words, expected):
    engine = FakeEngine("edit_memo", edits=["summary"])
    collect(
        agent.run_agent,
        {
            "message": message,
            "documents": [document()],
            "last_memo": {"summary": "## Summary\n" + "word " * words},
        },
        engine,
    )
    writer = next(
        r
        for r in engine.calls
        if "Write this memo section" in r["messages"][0]["content"]
    )
    assert f"at most {expected} words" in writer["messages"][0]["content"]


def test_structural_edit_skips_all_writers():
    engine = FakeEngine("edit_memo", edits=["cash_flow"])
    previous = {
        "summary": "## Summary\nObserved evidence. Further evidence.",
        "cash_flow": "Keep this.",
    }
    events = collect(
        agent.run_agent,
        {
            "message": "Remove the last sentence of the summary",
            "documents": [document()],
            "last_memo": previous,
        },
        engine,
    )
    assert not engine.streams
    assert events[-1]["memo"] == {
        **previous,
        "summary": "## Summary\nObserved evidence.",
    }
    section = next(e for e in events if e["type"] == "section")
    assert section["applied_by"] == "code"
    assert "problems" in section
    assert any("edit applied in code:" in str(e) for e in events)


def test_expand_too_short_retries_once():
    class Engine(FakeEngine):
        async def stream(self, request):
            self.record(request)
            self.streams["summary"] += 1
            yield "## Summary\n" + "Additional evidence warrants review. " * 8

    engine = Engine("edit_memo", edits=["summary"])
    previous = "## Summary\n" + "Original evidence requires consideration. " * 8
    events = collect(
        agent.run_agent,
        {
            "message": "Expand the summary with more detail",
            "documents": [document()],
            "last_memo": {"summary": previous},
        },
        engine,
    )
    assert engine.streams["summary"] == 2
    assert any(
        p["kind"] == "too_short" and "1.2 times" in p["detail"]
        for p in events[-1]["problems"]
    )
    writers = [
        r
        for r in engine.calls
        if "Write this memo section" in r["messages"][0]["content"]
    ]
    for request in writers:
        system = request["messages"][0]["content"]
        assert (
            "Keep the language of previous_text unless the request asks for another language."
            in system
        )
        assert (
            f"Write at least {(agent.word_count(previous) * 13 + 9) // 10} words"
            in system
        )
        assert "not filler" in system


def test_bullets_merged_into_a_paragraph_count_as_applied():
    # Seen live: same words, bullets joined into one paragraph, falsely "not applied".
    bullets = "## Red flags\n- Returned cheque [p1:r4].\n- Round trip [p3:r17]."
    paragraph = "## Red flags\nReturned cheque [p1:r4]. Round trip [p3:r17]."
    request = "Rewrite the red flags as one paragraph without bullet points."
    assert not agent.edit_not_applied(bullets, paragraph, request)
    assert agent.edit_not_applied(bullets, bullets + " ", request)
