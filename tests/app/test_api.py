"""Offline API behavior, with external requests replaced at their boundary."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.backend import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("RUNPOD_API_KEY", "secret-test-key")
    main.SESSIONS.clear()
    main.STATUS.update(time=0, value={})
    return TestClient(main.app)


def create(client):
    response = client.post("/api/sessions")
    assert response.status_code == 200
    body = response.json()
    assert len(body["session"]) == 32
    return body["session"]


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def sample(client, s):
    response = client.post(f"/api/sessions/{s}/sample")
    assert response.status_code == 200
    return response.json()["page_ids"]


def extract(client, s, p):
    return client.post(
        f"/api/sessions/{s}/pages/{p}/extract",
        json={"model": "8b", "doc_type": "statement"},
    )


def test_lifecycle_and_isolation(client):
    a, b = create(client), create(client)
    sample(client, a)
    assert client.get(f"/api/sessions/{b}").json()["pages"] == []
    assert client.get(f"/api/sessions/{b}/pages/p1/image").status_code == 404
    assert client.get("/api/sessions/unknown").status_code == 404
    assert (
        client.get(f"/api/sessions/{a}/pages/p1/image").headers["content-type"]
        == "image/png"
    )


def test_upload_validation(client):
    s = create(client)
    url = f"/api/sessions/{s}/pages"
    assert client.post(url, files={"files": ("bad.png", b"bad")}).status_code == 415
    assert (
        client.post(
            url, files={"files": ("large.png", b"x" * (main.MAX_BYTES + 1))}
        ).status_code
        == 413
    )
    assert (
        client.post(
            url, files=[("files", ("ok.png", png())) for _ in range(31)]
        ).status_code
        == 400
    )
    assert client.get(f"/api/sessions/{s}").json()["pages"] == []
    assert client.post(url, files={"files": ("ok.png", png())}).status_code == 200
    assert extract(client, s, "p1").status_code == 503


def test_sample_demo_and_edit(client):
    s = create(client)
    ids = sample(client, s)
    assert len(ids) == 5
    result = extract(client, s, ids[0]).json()
    assert result["metrics"]["source"] == "cached-demo"
    assert result["document"] == main.SESSIONS[s]["pages"][ids[0]]["truth"]
    before = client.get(f"/api/sessions/{s}/analysis").json()
    row = result["document"]["transactions"][0]
    value = str(float(row.get("credit") or 0) + 100)
    url = f"/api/sessions/{s}/pages/{ids[0]}/rows/0"
    assert (
        client.patch(url, json={"field": "credit", "value": value}).status_code == 200
    )
    after = client.get(f"/api/sessions/{s}/analysis").json()
    assert before["monthly"] != after["monthly"]
    assert (
        client.patch(url, json={"field": "date", "value": "wrong"}).status_code == 422
    )
    assert client.patch(url, json={"field": "bogus", "value": 1}).status_code == 422
    assert "0:credit" in client.get(f"/api/sessions/{s}").json()["pages"][0]["edited"]


def test_extract_metrics_and_no_secrets(client, monkeypatch, caplog):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")
    monkeypatch.setenv("GPU_PRICE_PER_SEC_8B", "0.001")
    s = create(client)
    sample(client, s)
    truth = main.SESSIONS[s]["pages"]["p1"]["truth"]
    monkeypatch.setattr(
        main.worker_client,
        "call_endpoint",
        lambda *a, **k: {
            "delayTime": 1500,
            "executionTime": 2500,
            "output": {
                "document": truth,
                "gpu": "secret-test-key",
                "timing": {"cold_start": True, "inference_s": 2},
            },
        },
    )
    response = extract(client, s, "p1")
    metrics = response.json()["metrics"]
    assert metrics["cost"] == pytest.approx(0.0025)
    assert metrics["cost_per_1000_pages"] == pytest.approx(2.5)
    assert metrics["delayTime"] == 1500
    assert metrics["cold_start"] is True
    assert "secret-test-key" not in response.text
    assert "secret-test-key" not in client.get(f"/api/sessions/{s}").text
    assert "secret-test-key" not in caplog.text


def test_failure_fallback(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")

    def fail(*args, **kwargs):
        raise RuntimeError("secret-test-key")

    monkeypatch.setattr(main.worker_client, "call_endpoint", fail)
    s = create(client)
    sample(client, s)
    assert extract(client, s, "p1").json()["metrics"]["source"] == "cached-demo"


def test_memo_sse(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")
    monkeypatch.setattr(
        main,
        "memo_chunks",
        lambda *a: iter(
            [
                {"delta": "Hello "},
                {"delta": "[p1:r1]"},
                {"done": True, "usage": {"tokens": 2}, "timing": {"inference_s": 1}},
            ]
        ),
    )
    s = create(client)
    response = client.get(f"/api/sessions/{s}/memo")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: chunk") == 2
    assert response.text.count("event: final") == 1
    assert '"tokens": 2' in response.text
    assert response.text.index("Hello") < response.text.index("event: final")


def test_status_cache(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")
    calls = []

    def health(*args):
        calls.append(args)
        return {
            "workers": {"idle": 1},
            "jobs": {"inQueue": 0},
            "echo": "secret-test-key",
        }

    monkeypatch.setattr(main, "fetch_health", health)
    for _ in range(2):
        response = client.get("/api/status")
        assert "secret-test-key" not in response.text
    assert len(calls) == 1
    main.STATUS["time"] -= 4
    client.get("/api/status")
    assert len(calls) == 2


def test_polling_forwards_chunks(monkeypatch):
    results = iter(
        [
            {"id": "job"},
            {"status": "IN_PROGRESS", "stream": [{"output": {"delta": "a"}}]},
            {
                "status": "COMPLETED",
                "executionTime": 10,
                "stream": [{"output": {"done": True, "usage": {"tokens": 1}}}],
            },
        ]
    )

    class FakeHTTP:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            return self.get()

        def get(self, *args):
            data = next(results)
            return http_response(data)

    def http_response(data):
        import httpx

        return httpx.Response(
            200, json=data, request=httpx.Request("GET", "https://example.invalid")
        )

    monkeypatch.setattr(main.httpx, "Client", FakeHTTP)
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    stream = main.memo_chunks("ep", {}, "key")
    assert next(stream) == {"delta": "a"}
    assert next(stream)["usage"] == {"tokens": 1}
    assert next(stream)["executionTime"] == 10


def test_eval_unavailable(client):
    response = client.get("/api/eval")
    assert response.status_code == 200
    assert isinstance(json.loads(response.text), dict)


def test_upload_rejects_declared_pixel_bomb_before_verify(client, monkeypatch):
    """A valid PNG header can declare huge dimensions in a tiny upload."""
    import struct
    import zlib

    from PIL.PngImagePlugin import PngImageFile

    data = bytearray(png())
    data[16:24] = struct.pack(">II", 10_000, 6_001)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    verified = []
    monkeypatch.setattr(PngImageFile, "verify", lambda self: verified.append(True))
    s = create(client)
    response = client.post(
        f"/api/sessions/{s}/pages", files={"files": ("huge.png", bytes(data))}
    )
    assert response.status_code == 413
    assert "60,000,000 pixels" in response.json()["detail"]
    assert not verified
    assert client.get(f"/api/sessions/{s}").json()["pages"] == []


def test_cached_memo_uses_computed_numbers_and_explained_flags(client):
    s = create(client)
    for p in sample(client, s)[:4]:
        assert extract(client, s, p).status_code == 200
    computed = client.get(f"/api/sessions/{s}/analysis").json()
    assert computed["currency"] == "SGD"
    assert all(f["description"] for f in computed["findings"])
    response = client.get(f"/api/sessions/{s}/memo")
    chunks = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    text = "".join(chunk.get("delta", "") for chunk in chunks)
    assert "Cached demo memo — no model call" in text
    for heading in (
        "Summary",
        "Cash flow",
        "Concentration",
        "Red flags",
        "Questions for the applicant",
    ):
        assert heading in text
    assert "SGD" in text
    assert " ." not in text


def test_live_direction_fixes(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")
    s = create(client)
    sample(client, s)
    truth = main.SESSIONS[s]["pages"]["p1"]["truth"]
    prediction = json.loads(json.dumps(truth))
    row = prediction["transactions"][0]
    row["debit"], row["credit"] = row["credit"], row["debit"]
    monkeypatch.setattr(
        main.worker_client,
        "call_endpoint",
        lambda *a, **k: {
            "output": [{"document": prediction}],
            "delayTime": 1234,
        },
    )
    response = extract(client, s, "p1").json()
    assert response["document"] == truth
    assert len(response["direction_fixes"]) == 1
    assert response["metrics"]["direction_fixes"] == 1
    assert response["metrics"]["delayTime"] == 1234
    page = client.get(f"/api/sessions/{s}").json()["pages"][0]
    assert page["direction_fixes"] == response["direction_fixes"]
    monkeypatch.setenv("DEMO_MODE", "1")
    cached = extract(client, s, "p1").json()
    assert cached["direction_fixes"] == []
    assert cached["metrics"]["direction_fixes"] == 0
