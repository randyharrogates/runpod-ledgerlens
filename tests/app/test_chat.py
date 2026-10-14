"""Assistant streams with the Runpod boundary replaced by local events."""

import asyncio
import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.backend import chat, main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "chat-secret")
    monkeypatch.setenv("RUNPOD_ENDPOINT_AGENT", "agent-test")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    main.SESSIONS.clear()
    main.STATUS.update(time=0, value={})
    return TestClient(main.app)


def setup(client, count=1):
    s = client.post("/api/sessions").json()["session"]
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    for _ in range(count):
        main.add_page(main.SESSIONS[s]["pages"], buffer.getvalue(), "image/png")
    return s, main.SESSIONS[s]


def parse(response):
    result = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        kind = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        result.append({**data, "type": kind})
    return result


def fake(monkeypatch, events, inputs=None):
    async def relay(endpoint, payload, key):
        if inputs is not None:
            inputs.append(payload)
        for event in events:
            yield event

    monkeypatch.setattr(chat, "relay", relay)


def document():
    return json.loads((main.ROOT / "sample/biz_0000_p001.json").read_text())


def rows(p="p1"):
    return {
        "type": "rows",
        "page_id": p,
        "valid": True,
        "document": document(),
        "doc_type": "statement",
        "balance_fixes": [],
    }


def test_read_caches_and_rejects(client, monkeypatch):
    s, session = setup(client, 2)
    fake(
        monkeypatch,
        [
            rows(),
            {"type": "triage", "page_id": "p2", "accepted": False},
            {"type": "final", "intent": "read", "gpu": "RTX 3090"},
            {"done": True, "executionTime": 1200, "delayTime": 100},
        ],
    )
    events = parse(client.post(f"/api/sessions/{s}/read", json={}))
    assert events[-1]["source"] == "runpod"
    pages = client.get(f"/api/sessions/{s}").json()["pages"]
    assert pages[0]["document"] == document()
    assert pages[0]["status"] == "done"
    assert pages[0]["metrics"]["source"] == "agent-read"
    assert pages[1]["status"] == "rejected" and pages[1]["document"] is None
    assert not session["chat"]["reading"]
    assert client.get(f"/api/sessions/{s}/analysis").json()["monthly"]
    assert (
        client.post(
            f"/api/sessions/{s}/read", json={"page_ids": ["p1"] * 9}
        ).status_code
        == 400
    )


def test_payload_cap_history_and_memo(client, monkeypatch):
    s, session = setup(client, 10)
    session["pages"]["p1"]["document"] = document()
    session["pages"]["p10"]["status"] = "rejected"
    inputs = []
    memo = {section["id"]: "x" * 4100 for section in chat.SECTIONS}
    fake(
        monkeypatch,
        [
            {
                "type": "final",
                "intent": "memo",
                "memo": memo,
                "gpu": "RTX 3090",
                "timing": {"total_s": 2},
            },
            {"done": True, "executionTime": 1000, "delayTime": 500},
        ],
        inputs,
    )
    events = parse(client.post(f"/api/sessions/{s}/chat", json={"message": "Write it"}))
    assert events[0]["type"] == "turn"
    assert events[1]["type"] == "notice"
    assert [d["page_id"] for d in inputs[0]["documents"]] == ["p1"]
    assert [d["page_id"] for d in inputs[0]["new_pages"]] == [
        f"p{i}" for i in range(2, 9)
    ]
    assert session["chat"]["history"][0] == {"role": "user", "text": "Write it"}
    assert len(session["chat"]["history"][1]["text"]) == 4000
    assert all(len(text) == 4000 for text in session["chat"]["last_memo"].values())
    assert session["requests"][0]["intent"] == "memo"
    assert events[-1]["cost"] > 0 and events[-1]["total_s"] == 2


def test_error_and_redaction(client, monkeypatch):
    s, session = setup(client)
    fake(monkeypatch, [{"error": "chat-secret failure"}])
    response = client.post(f"/api/sessions/{s}/chat", json={"message": "Question"})
    assert "chat-secret" not in response.text
    assert "[REDACTED]" in response.text
    assert not session["chat"]["history"] and not session["requests"]
    fake(
        monkeypatch,
        [
            {"type": "final", "intent": "knowledge", "answer": "chat-secret answer"},
            {"type": "error", "message": "failed after final"},
        ],
    )
    client.post(f"/api/sessions/{s}/chat", json={"message": "Question"})
    assert not session["chat"]["history"]


def test_disconnect_cancels_and_records_nothing(client, monkeypatch):
    s, session = setup(client)
    cancelled = []

    def chunks(endpoint, payload, key, on_job=None, stop=None):
        on_job("job-test")
        yield {"type": "route", "intent": "knowledge"}
        yield {"type": "final", "answer": "Do not save"}

    async def cancel(endpoint, key, job):
        cancelled.append(job)

    monkeypatch.setattr(main, "memo_chunks", chunks)
    monkeypatch.setattr(chat, "cancel_job", cancel)

    async def run():
        response = await chat.chat(s, chat.ChatRequest(message="Question"))
        await anext(response.body_iterator)
        await anext(response.body_iterator)
        await anext(response.body_iterator)
        await response.body_iterator.aclose()

    asyncio.run(run())
    assert cancelled == ["job-test"]
    assert not session["chat"]["history"] and not session["requests"]
    assert not session["chat"]["reading"]


def test_warm_throttles_and_offline(client, monkeypatch):
    s, _ = setup(client)
    submissions = []

    async def submit(endpoint, key):
        submissions.append(endpoint)

    monkeypatch.setattr(chat, "submit_health", submit)
    assert client.post(f"/api/sessions/{s}/warm").json() == {"warming": True}
    assert client.post(f"/api/sessions/{s}/warm").json()["warming"] is False
    assert len(submissions) == 1
    monkeypatch.setenv("DEMO_MODE", "1")
    assert client.post(f"/api/sessions/{s}/warm").json()["reason"] == "offline"
    assert len(submissions) == 1


def test_demo_recording_and_notices(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setattr(chat, "RECORDINGS", tmp_path)
    s = client.post("/api/sessions").json()["session"]
    client.post(f"/api/sessions/{s}/sample")
    recording = [
        {"t": 0, "type": "deltas", "parts": [{"part": "answer", "text": "Recorded"}]},
        {
            "t": 0,
            "type": "final",
            "intent": "ask_data",
            "answer": "Recorded answer",
            "timing": {"total_s": 1},
        },
    ]
    (tmp_path / "ask_data.json").write_text(json.dumps(recording))
    message = chat.SUGGESTIONS[1][0].upper()
    events = parse(
        client.post(f"/api/sessions/{s}/chat", json={"message": f" {message} "})
    )
    assert events[-2]["answer"] == "Recorded answer"
    assert events[-2]["source"] == events[-1]["source"] == "recorded"
    assert events[-1]["cost"] is None
    for message in ("Unknown prompt", chat.SUGGESTIONS[0][0]):
        events = parse(
            client.post(f"/api/sessions/{s}/chat", json={"message": message})
        )
        assert events[1]["answer"] == chat.DEMO_NOTICE
    other, other_session = setup(client)
    events = parse(
        client.post(
            f"/api/sessions/{other}/chat", json={"message": chat.SUGGESTIONS[1][0]}
        )
    )
    assert events[1]["answer"] == chat.DEMO_NOTICE
    assert other_session["pages"]["p1"]["status"] == "uploaded"


def test_offline_read(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    s = client.post("/api/sessions").json()["session"]
    client.post(f"/api/sessions/{s}/sample")
    events = parse(client.post(f"/api/sessions/{s}/read", json={}))
    assert len([event for event in events if event["type"] == "rows"]) == 5
    assert all(page["document"] for page in main.SESSIONS[s]["pages"].values())
    other, session = setup(client)
    events = parse(client.post(f"/api/sessions/{other}/read", json={}))
    assert events[1]["valid"] is False
    assert session["pages"]["p1"]["status"] == "error"


@pytest.mark.parametrize("message", ["", " " * 20, "x" * 2001, 123, None])
def test_message_validation(client, message):
    s, _ = setup(client)
    assert (
        client.post(f"/api/sessions/{s}/chat", json={"message": message}).status_code
        == 422
    )


def test_status_and_suggestions(client, monkeypatch):
    monkeypatch.setattr(
        main, "fetch_health", lambda endpoint, key: {"workers": {"idle": 1}}
    )
    assert client.get("/api/status").json()["agent"] == {"workers": {"idle": 1}}
    prompts = client.get("/api/chat/suggestions").json()
    assert prompts[-1]["needs_documents"] is False
    assert prompts[3]["needs_memo"] is True


def test_chat_waits_for_read(client, monkeypatch):
    s, session = setup(client)
    inputs = []
    started = asyncio.Event()
    finish = asyncio.Event()

    async def relay(endpoint, payload, key):
        inputs.append(payload)
        if payload["task"] == "read":
            started.set()
            await finish.wait()
            yield rows()
        yield {"type": "final", "intent": payload["task"], "answer": "Answer"}

    monkeypatch.setattr(chat, "relay", relay)

    async def run():
        response = await chat.read(s, chat.ReadRequest())

        async def consume(response):
            return [event async for event in response.body_iterator]

        read_task = asyncio.create_task(consume(response))
        await started.wait()
        response = await chat.chat(s, chat.ChatRequest(message="Question"))
        iterator = response.body_iterator
        assert "turn" in await anext(iterator)
        assert "Waiting" in await anext(iterator)
        chat_task = asyncio.create_task(consume(response))
        await asyncio.sleep(0.01)
        assert len(inputs) == 1
        finish.set()
        await read_task
        await chat_task

    asyncio.run(run())
    assert inputs[1]["new_pages"] == []
    assert inputs[1]["documents"][0]["page_id"] == "p1"
    assert session["chat"]["history"]


def test_disconnect_while_polling(client, monkeypatch):
    s, session = setup(client)
    submitted = asyncio.Event()
    cancelled = []

    def chunks(endpoint, payload, key, on_job=None, stop=None):
        on_job("waiting-job")
        loop.call_soon_threadsafe(submitted.set)
        stop.wait(2)
        return
        yield

    async def cancel(endpoint, key, job):
        cancelled.append(job)

    monkeypatch.setattr(main, "memo_chunks", chunks)
    monkeypatch.setattr(chat, "cancel_job", cancel)

    async def run():
        nonlocal loop
        loop = asyncio.get_running_loop()
        response = await chat.chat(s, chat.ChatRequest(message="Question"))
        await anext(response.body_iterator)
        task = asyncio.create_task(anext(response.body_iterator))
        await submitted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    loop = None
    asyncio.run(run())
    assert cancelled == ["waiting-job"]
    assert not session["chat"]["history"]
    assert not session["chat"]["reading"]


def test_disconnect_after_final_does_not_record(client, monkeypatch):
    s, session = setup(client)
    fake(monkeypatch, [{"type": "final", "intent": "knowledge", "answer": "Answer"}])

    async def run():
        response = await chat.chat(s, chat.ChatRequest(message="Question"))
        assert "turn" in await anext(response.body_iterator)
        assert "final" in await anext(response.body_iterator)
        await response.body_iterator.aclose()

    asyncio.run(run())
    assert not session["chat"]["history"] and not session["requests"]


def test_read_reserves_pages_before_preparing_images(client, monkeypatch):
    s, session = setup(client)
    original = chat.images

    def prepare(pages, ids, *args):
        assert session["chat"]["reading"] == {"p1"}
        return original(pages, ids, *args)

    monkeypatch.setattr(chat, "images", prepare)
    fake(monkeypatch, [rows(), {"type": "final", "intent": "read"}])
    client.post(f"/api/sessions/{s}/read", json={})
    assert not session["chat"]["reading"]


def test_failed_read_keeps_the_worker_reason(client, monkeypatch):
    s, session = setup(client, 1)
    fake(monkeypatch, [{"error": "task must be extract, memo, or health"}])
    events = parse(client.post(f"/api/sessions/{s}/read", json={}))
    assert events[-1]["type"] == "error"
    assert session["pages"]["p1"]["status"] == "error"
    assert session["pages"]["p1"]["error"] == "task must be extract, memo, or health"


def test_memo_chunks_reports_the_failed_job_reason(monkeypatch):
    class FakeHTTP:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, url, json):
            return httpx.Response(
                200, json={"id": "job1"}, request=httpx.Request("POST", url)
            )

        def get(self, url):
            body = (
                {"status": "FAILED", "error": "engine down"}
                if "/status/" in url
                else {"status": "FAILED", "stream": []}
            )
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(main.httpx, "Client", FakeHTTP)
    with pytest.raises(RuntimeError, match="Worker job failed: engine down"):
        list(main.memo_chunks("ep", {}, "key"))


def test_images_stay_lossless_within_budget():
    """Small pages go as their original bytes; big ones as PNG; JPEG only over budget."""

    def png(size):
        out = io.BytesIO()
        Image.new("RGB", size, "white").save(out, format="PNG")
        return out.getvalue()

    small, big = png((60, 80)), png((1800, 1800))
    pages = {"a": {"data": small}, "b": {"data": big}}
    result = {
        r["page_id"]: base64.b64decode(r["image_base64"])
        for r in chat.images(pages, ["a", "b"])
    }
    assert result["a"] == small
    with Image.open(io.BytesIO(result["b"])) as image:
        assert image.format == "PNG"
        assert image.width * image.height <= chat.DEFAULT_MAX_PIXELS
    tight = chat.images(
        pages,
        ["b"],
        budget=len(json.dumps([{"page_id": "b", "image_base64": ""}])) + 4000,
    )
    with Image.open(io.BytesIO(base64.b64decode(tight[0]["image_base64"]))) as image:
        assert image.format == "JPEG"
