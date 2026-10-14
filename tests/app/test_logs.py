"""Unified logs, with local HTTP transports instead of Runpod."""

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backend import chat, logs, main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "chat-secret")
    monkeypatch.setenv("RUNPOD_ENDPOINT_AGENT", "agent-test")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("RUNPOD_WORKER_LOGS", "0")
    monkeypatch.setattr(logs, "BUFFER", logs.LogBuffer())
    monkeypatch.setattr(logs, "_WINDOWS", {})
    monkeypatch.setattr(logs, "_POLLING", {})
    main.SESSIONS.clear()
    logs.install()
    return TestClient(main.app)


def new_session(client):
    return client.post("/api/sessions").json()["session"]


def test_ring_and_filters(client):
    s = new_session(client)
    buffer = logs.LogBuffer(3)
    for i in range(5):
        buffer.add(
            "worker",
            "warn" if i % 2 else "info",
            str(i),
            session=s,
            turn="t",
            job_id="j",
            ignored="no",
        )
    entries, cursor = buffer.since()
    assert [e["id"] for e in entries] == [3, 4, 5]
    assert cursor == buffer.latest_id() == 5
    assert "ignored" not in entries[0]
    assert buffer.since(3, level="warning")[0][0]["id"] == 4
    assert buffer.since(source="browser")[0] == []
    logs.BUFFER = buffer
    response = client.get(
        "/api/logs",
        params=dict(
            since=2,
            source="worker",
            level="warning",
            session=s,
            turn="t",
            job_id="j",
            limit=1,
        ),
    ).json()
    assert response["cursor"] == 5 and len(response["entries"]) == 1
    assert client.get(f"/api/logs?session={s}&job_id=other").json()["entries"] == []


def test_redaction_and_install(client):
    logs.install()
    assert (
        sum(
            getattr(h, "_ledgerlens_buffer", False)
            for h in logging.getLogger().handlers
        )
        == 1
    )
    logs.LOG.warning("app chat-secret")
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %s',
        ("client", "POST", "/chat-secret", "1.1", 500),
        None,
    )
    logging.getLogger("uvicorn.access").handle(record)
    s = new_session(client)
    response = client.post(
        "/api/logs/browser",
        json={
            "session": s,
            "entries": [
                {"level": "error", "message": "chat-secret", "url": "/chat-secret"}
            ],
        },
    )
    assert response.json() == {"accepted": 1}
    response = client.get(f"/api/logs?session={s}")
    assert response.text.count("[REDACTED]") >= 3
    entries = response.json()["entries"]
    # An access line with a 5xx status counts as an error; the app's own lines carry no
    # logger name in front.
    assert [e["level"] for e in entries if "uvicorn.access" in e["message"]] == [
        "error"
    ]
    assert "app [REDACTED]" in [e["message"] for e in entries]
    assert "chat-secret" not in response.text and "[REDACTED]" in response.text
    assert (
        logs.BUFFER.add("vllm", "fatal", "chat-secret", job_id="chat-secret")["job_id"]
        == "[REDACTED]"
    )


def test_browser_validation_and_rate(client):
    entry = {"level": "info", "message": "hello", "source": "worker"}
    s = new_session(client)
    assert client.post("/api/logs/browser", content=b"x" * 32769).status_code == 413
    for body in (
        {"session": s, "entries": [entry] * 21},
        {"entries": [entry]},
        {"session": s, "entries": [{"level": "info", "message": "x" * 2001}]},
        {"session": s, "entries": []},
    ):
        assert client.post("/api/logs/browser", json=body).status_code == 422
    # Only a live session may write: a made-up or malformed id is not found.
    for bad in ("bad", "0" * 32):
        body = {"session": bad, "entries": [entry]}
        assert client.post("/api/logs/browser", json=body).status_code == 404
    batch = {"session": s, "entries": [entry] * 20}
    for _ in range(6):
        assert client.post("/api/logs/browser", json=batch).status_code == 200
    body = {"session": s, "entries": [entry]}
    assert client.post("/api/logs/browser", json=body).status_code == 429
    entries = logs.BUFFER.since()[0]
    assert len(entries) == 120
    assert all(e["source"] == "browser" and e["session"] == s for e in entries)


def test_sse(client, monkeypatch):
    async def run():
        first = logs.BUFFER.add("app", "info", "backlog")
        stream = logs.stream_entries(0, {}, poll=0.01, ping=0.01)
        assert f"id: {first['id']}\nevent: log\ndata: " in await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.001)
        logs.BUFFER.add("app", "info", "new line")
        frame = await pending
        if frame.startswith(": ping"):
            frame = await anext(stream)
        assert '"message": "new line"' in frame
        await stream.aclose()

    asyncio.run(run())
    monkeypatch.setattr(logs, "STREAM_MAX_SECONDS", 0)
    s = new_session(client)
    response = client.get(f"/api/logs/stream?session={s}&since=0")
    assert "event: log" in response.text
    assert response.headers["x-accel-buffering"] == "no"
    response = client.get(
        f"/api/logs/stream?session={s}", headers={"Last-Event-ID": "0"}
    )
    assert "backlog" in response.text


def test_logs_are_scoped_to_the_callers_session(client, monkeypatch):
    mine, theirs = new_session(client), new_session(client)
    logs.BUFFER.add("app", "info", "app-wide line")
    logs.BUFFER.add("app", "info", "my line", session=mine)
    logs.BUFFER.add("worker", "error", "their line", session=theirs, job_id="j2")
    logs.BUFFER.add("app", "info", f"mentions {theirs} by id", session=mine)
    # An access line for another session is tagged with it and its id is masked.
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %s',
        ("client", "POST", f"/api/sessions/{theirs}/chat", "1.1", 200),
        None,
    )
    logging.getLogger("uvicorn.access").handle(record)
    text = client.get(f"/api/logs?session={mine}").text
    assert "app-wide line" in text and "my line" in text
    assert "their line" not in text and "/api/sessions/[session]/chat" not in text
    assert theirs not in text and "mentions [session] by id" in text
    other = client.get(f"/api/logs?session={theirs}").text
    assert "their line" in other and "/api/sessions/[session]/chat" in other
    assert theirs not in json.dumps([e["message"] for e in logs.BUFFER.since()[0]])
    for bad in ("0" * 32, "not-a-session"):
        assert client.get(f"/api/logs?session={bad}").status_code == 404
        assert client.get(f"/api/logs/stream?session={bad}").status_code == 404
    assert client.get("/api/logs").status_code == 422
    monkeypatch.setattr(logs, "STREAM_MAX_SECONDS", 0)
    streamed = client.get(f"/api/logs/stream?session={mine}&since=0").text
    assert "my line" in streamed and "their line" not in streamed
    assert theirs not in streamed


def test_chat_logs(client, monkeypatch):
    async def relay(endpoint, payload, key):
        yield {"type": "job", "job_id": "job-1"}
        yield {
            "type": "log",
            "entries": [
                {"source": "worker", "message": "chat-secret worker", "level": "error"},
                {"source": "vllm", "message": "model loaded", "section": "reader"},
            ],
        }
        yield {
            "type": "final",
            "intent": "memo",
            "answer": "Answer",
            "timing": {"total_s": 12.3},
        }
        yield {"done": True, "executionTime": 1200, "delayTime": 100}

    monkeypatch.setattr(chat, "relay", relay)
    session = client.post("/api/sessions").json()["session"]
    response = client.post(f"/api/sessions/{session}/chat", json={"message": "hello"})
    assert response.text.startswith("event: turn")
    assert "event: job" in response.text and "event: log" not in response.text
    entries = logs.BUFFER.since(session=session, limit=1000)[0]
    workers = [e for e in entries if e["source"] in ("worker", "vllm")]
    assert len(workers) == 2
    assert all(e["job_id"] == "job-1" and len(e["turn"]) == 12 for e in workers)
    done = [e for e in entries if "agent turn done" in e["message"]]
    assert len(done) == 1 and "queue 0.1 s, execution 1.2 s" in done[0]["message"]
    record = main.SESSIONS[session]["requests"][0]
    assert record["job_id"] == "job-1" and record["turn_id"] == workers[0]["turn"]
    assert "chat-secret" not in client.get(f"/api/logs?session={session}").text

    async def fail(endpoint, payload, key):
        yield {"type": "error", "message": "failure"}

    monkeypatch.setattr(chat, "relay", fail)
    client.post(f"/api/sessions/{session}/chat", json={"message": "hello"})
    assert (
        len([e for e in logs.BUFFER.since()[0] if "agent turn failed" in e["message"]])
        == 1
    )


def test_real_relay_job(client, monkeypatch):
    def chunks(endpoint, payload, key, on_job=None, stop=None):
        on_job("job-9")
        yield {"done": True}

    monkeypatch.setattr(main, "memo_chunks", chunks)

    async def run():
        return [e async for e in chat.relay("endpoint", {}, "key")]

    assert asyncio.run(run())[0] == {"type": "job", "job_id": "job-9"}


@pytest.mark.parametrize("failure", [False, True])
def test_poller(client, failure):
    s = new_session(client)
    stop = asyncio.Event()
    calls = 0

    def transport(request):
        nonlocal calls
        assert request.headers["Authorization"] == "Bearer chat-secret"
        if request.url.path.endswith("workers"):
            calls += 1
            if calls == 3:
                stop.set()
            return httpx.Response(
                500 if failure else 200, json={"workers": [{"id": "worker-12345"}]}
            )
        data = [
            {
                "ts": "2026-10-15T01:00:00Z",
                "source": "container",
                "line": "chat-secret loaded",
            },
            {
                "ts": "2026-10-15T01:00:01Z",
                "source": "system",
                "line": "error happened",
            },
        ]
        return httpx.Response(
            200,
            text="".join(
                f"id: {i}\ndata: {json.dumps(e)}\n\n" for i, e in enumerate(data)
            ),
        )

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)

    asyncio.run(
        logs.poll_worker_logs(
            "endpoint",
            "chat-secret",
            session=s,
            turn="t",
            job_id="j",
            stop=stop,
            interval=0.001,
            client_factory=factory,
            since=0,
        )
    )
    entries = logs.BUFFER.since(job_id="j")[0]
    assert len(entries) == (1 if failure else 2)
    if failure:
        assert entries[0]["level"] == "warning"
    else:
        assert all(e["source"] == "worker" for e in entries)
        assert entries[1]["level"] == "error"
    text = client.get(f"/api/logs?session={s}").text
    assert "chat-secret" not in text and (
        failure or "[worker worker-1] [container]" in text
    )


def test_access_filter():
    filter = logs.AccessFilter()
    for method, path, status, keep in (
        ("GET", "/api/status", 200, False),
        ("POST", "/api/chat", 200, True),
        ("POST", "/api/logs/browser", 500, False),
        ("GET", "/missing", 404, True),
    ):
        record = logging.LogRecord(
            "uvicorn.access",
            20,
            "",
            1,
            "",
            ("client", method, path, "1.1", status),
            None,
        )
        assert filter.filter(record) is keep
    record.args = None
    assert filter.filter(record)


def test_worker_cap_and_disconnect(client, monkeypatch):
    async def relay(endpoint, payload, key):
        yield {"type": "log", "entries": [{"message": "line"}] * 503}
        yield {"type": "final", "intent": "knowledge", "answer": "Answer"}

    monkeypatch.setattr(chat, "relay", relay)
    session = client.post("/api/sessions").json()["session"]
    client.post(f"/api/sessions/{session}/chat", json={"message": "hello"})
    entries = logs.BUFFER.since(session=session, limit=1000)[0]
    assert len([e for e in entries if e["source"] == "worker"]) == 500
    assert len([e for e in entries if "dropped after 500" in e["message"]]) == 1

    async def disconnect():
        response = await chat.chat(session, chat.ChatRequest(message="hello"))
        assert "turn" in await anext(response.body_iterator)
        await response.body_iterator.aclose()

    asyncio.run(disconnect())
    assert (
        len(
            [
                e
                for e in logs.BUFFER.since(session=session, limit=1000)[0]
                if "stopped before it finished" in e["message"]
            ]
        )
        == 1
    )


def test_browser_window_and_bound(client, monkeypatch):
    now = [1.0]
    monkeypatch.setattr(logs.time, "monotonic", lambda: now[0])
    logs.accept_browser("session", [None] * 120)
    with pytest.raises(logs.RateLimited):
        logs.accept_browser("session", [None])
    now[0] = 62.0
    assert logs.accept_browser("session", [None]) == 1
    for i in range(999):
        logs.accept_browser(str(i), [None])
    with pytest.raises(logs.RateLimited):
        logs.accept_browser("extra", [None])
    assert len(logs._WINDOWS) == 1000


def test_relay_announces_job_before_first_chunk(client, monkeypatch):
    import threading

    released = threading.Event()

    def chunks(endpoint, payload, key, on_job=None, stop=None):
        on_job("job-cold")
        # A cold start: no chunk until the job id has already reached the browser.
        assert released.wait(5)
        yield {"done": True}

    monkeypatch.setattr(main, "memo_chunks", chunks)

    async def run():
        relay = chat.relay("endpoint", {}, "key")
        first = await anext(relay)
        released.set()
        return [first] + [e async for e in relay]

    events = asyncio.run(run())
    assert events == [{"type": "job", "job_id": "job-cold"}, {"done": True}]


def test_relay_job_without_chunk(client, monkeypatch):
    def chunks(endpoint, payload, key, on_job=None, stop=None):
        on_job("job-empty")
        return
        yield

    cancelled = []

    async def cancel(endpoint, key, job):
        cancelled.append(job)

    monkeypatch.setattr(main, "memo_chunks", chunks)
    monkeypatch.setattr(chat, "cancel_job", cancel)

    async def run():
        return [e async for e in chat.relay("endpoint", {}, "key")]

    assert asyncio.run(run()) == [{"type": "job", "job_id": "job-empty"}]
    assert cancelled == ["job-empty"]


def test_poller_scoping(client):
    """Old lines and lines seen while another session polls are not recorded."""
    s = new_session(client)
    stop = asyncio.Event()
    calls = []

    def transport(request):
        if request.url.path.endswith("workers"):
            calls.append(1)
            if len(calls) % 2 == 0:
                stop.set()
            return httpx.Response(200, json={"workers": [{"id": "worker-1"}]})
        data = [
            {"ts": "2020-01-01T00:00:00Z", "line": "old line"},
            {"ts": "2999-01-01T00:00:00Z", "line": "new line"},
        ]
        return httpx.Response(
            200, text="".join(f"data: {json.dumps(e)}\n\n" for e in data)
        )

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)

    def run(turn):
        stop.clear()
        asyncio.run(
            logs.poll_worker_logs(
                "ep",
                "k",
                session=s,
                turn=turn,
                job_id=turn,
                stop=stop,
                interval=0.001,
                client_factory=factory,
            )
        )
        return [e["message"] for e in logs.BUFFER.since(job_id=turn)[0]]

    alone = run("a")
    assert any("new line" in m for m in alone)
    assert not any("old line" in m for m in alone)
    logs._POLLING["ep"] = {"other-turn": "other-session"}
    shared = run("b")
    assert not any("new line" in m for m in shared)
    assert any("paused" in m for m in shared)
    assert logs._POLLING["ep"] == {"other-turn": "other-session"}
