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
    response = extract(client, s, "p1").json()
    assert response["metrics"]["source"] == "cached-demo"
    assert response["metrics"]["live_error"] == "RuntimeError: [REDACTED]"
    payload = client.get(f"/api/sessions/{s}").json()
    assert (
        payload["pages"][0]["metrics"]["live_error"]
        == response["metrics"]["live_error"]
    )
    assert payload["requests"][-1]["live_error"] == response["metrics"]["live_error"]


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


def test_memo_payload_citations(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")
    computed = {
        "citations": [{"page_id": "p1", "row_index": 0}],
        "monthly": [{"findings": {"citation": {"page_id": "p2", "row_index": 7}}}],
    }
    original = json.dumps(computed)
    monkeypatch.setattr(main, "analysis", lambda _: computed)
    payloads = []

    class FakeHTTP:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            payloads.append(kwargs["json"]["input"])
            return self.response({"id": "job"})

        def get(self, url):
            return self.response({"status": "COMPLETED"})

        def response(self, data):
            import httpx

            return httpx.Response(
                200, json=data, request=httpx.Request("GET", "https://example.invalid")
            )

    monkeypatch.setattr(main.httpx, "Client", FakeHTTP)
    response = client.get(f"/api/sessions/{create(client)}/memo")
    assert "event: error" not in response.text
    assert payloads == [
        {
            "task": "memo",
            "analysis": {
                "citations": ["[p1:r1]"],
                "monthly": [{"findings": {"citation": "[p2:r8]"}}],
            },
        }
    ]
    assert "row_index" not in json.dumps(payloads)
    assert "page_id" not in json.dumps(payloads)
    assert json.dumps(computed) == original


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
            # /stream doesn't carry timings; the follow-up /status call does.
            {
                "status": "COMPLETED",
                "stream": [{"output": {"done": True, "usage": {"tokens": 1}}}],
            },
            {"status": "COMPLETED", "delayTime": 5, "executionTime": 10},
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
    done = next(stream)
    assert (done["delayTime"], done["executionTime"]) == (5, 10)


def test_eval_unavailable(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ROOT", tmp_path / "app")
    directory = tmp_path / "eval"
    directory.mkdir()
    (directory / "results.json").write_text('{"full_results": true}')
    response = client.get("/api/eval")
    assert response.status_code == 200
    assert response.json() == {"available": False}


def test_eval_summary(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ROOT", tmp_path / "app")
    directory = tmp_path / "eval"
    directory.mkdir()
    summary = {"generated_from": "batch evaluation, one GPU Pod", "splits": {}}
    (directory / "summary.json").write_text(json.dumps(summary))
    (directory / "results.json").write_text('{"full_results": true}')
    assert client.get("/api/eval").json() == summary


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
    full = sum(not month["partial"] for month in computed["monthly"].values())
    assert f"cover {len(computed['monthly'])} months ({full} full)" in text
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


def test_cost_uses_the_reported_gpu_tier(monkeypatch):
    from app.backend.main import metrics

    monkeypatch.delenv("GPU_PRICE_PER_SEC_FT", raising=False)
    result = {"executionTime": 10_000}
    on_4090 = metrics(result, {"gpu": "NVIDIA GeForce RTX 4090"}, "ft")
    on_5090 = metrics(result, {"gpu": "NVIDIA GeForce RTX 5090"}, "ft")
    unknown = metrics(result, {"gpu": None}, "ft")
    assert round(on_4090["cost"], 6) == round(10 * 1.10 / 3600, 6)
    assert round(on_5090["cost"], 6) == round(10 * 1.58 / 3600, 6)
    assert unknown["cost"] == on_5090["cost"]
    monkeypatch.setenv("GPU_PRICE_PER_SEC_FT", "0.001")
    assert metrics(result, {"gpu": "NVIDIA GeForce RTX 4090"}, "ft")["cost"] == 0.01


def test_unverified_memo_live_totals():
    payload = {
        "monthly": [
            {"outflow": "21487.85"},
            {"outflow": "26539.21"},
            {"outflow": "7014.53"},
        ],
        "period_totals": {"inflow": "89218.66", "outflow": "55041.59"},
    }
    assert main.unverified_memo_numbers(
        "Outflow SGD 54,987.85; inflow SGD 89,218.66. Again 54,987.85.", payload
    ) == ["54,987.85"]


def test_unverified_memo_rounding_strings_percentages_and_citations():
    from decimal import Decimal

    assert main.unverified_memo_numbers("1.0", {"boolean": True}) == ["1.0"]
    assert main.unverified_memo_numbers("10.005 10.006", {"amount": 10}) == ["10.006"]
    payload = {
        "amount": Decimal("1234.567"),
        "float": 42.125,
        "count": 20,
        "top_payer_concentration": {"share": "0.63789"},
        "description": "SGD 1,499.51; 6.3×",
        "not_a_share": "0.25",
        "citations": ["p1:r1"],
        "boolean": True,
    }
    assert main.unverified_memo_numbers(
        "1,234.567 1,234.57 1,234.6 1,235 42.12 20.0 1,499.51 6.3 "
        "63.789% 64% 63.8% 63.79% [p1:r4] [p1:r1–r18] "
        "2026 18 25% 1.0 99.99 99.99 88.88",
        payload,
    ) == ["25%", "99.99", "88.88"]


def test_memo_checks_full_stream_against_sent_payload(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "endpoint")
    monkeypatch.setattr(main, "analysis", lambda _: {"amount": "89218.66"})
    monkeypatch.setattr(
        main,
        "memo_chunks",
        lambda *args: iter(
            [
                {"delta": "SGD 54,"},
                {"delta": "987.85 and 89,218.66 [p1:r1]"},
                {"done": True},
            ]
        ),
    )
    response = client.get(f"/api/sessions/{create(client)}/memo")
    final = json.loads(response.text.split("event: final\ndata: ")[1])
    assert final["unverified"] == ["54,987.85"]


def test_cash_deposit_finding_gives_the_date_and_bank_text():
    # Without a date the memo model invented one ("6 Mar" for a 30 Jan deposit, seen live 11 Oct).
    pages = {
        "p2": {
            "document": {
                "transactions": [
                    {
                        "date": "2026-01-30",
                        "description": "CASH LODGEMENT",
                        "credit": "12071.87",
                    }
                ]
            }
        }
    }
    finding = {
        "type": "large_cash_deposit",
        "amount": "12071.87",
        "median_inflow": "1929.10",
        "citations": [{"page_id": "p2", "row_index": 0}],
    }
    assert main.describe_finding(finding, pages, "SGD") == (
        "Large cash deposit · SGD 12,071.87 · 30 Jan · CASH LODGEMENT · 6.3× the median credit"
    )


def test_memo_check_catches_plain_integer_amounts_but_not_years():
    payload = {"period_totals": {"outflow": 55041.59, "net": 34177.07}}
    text = (
        "In 2026 total outflow was SGD 55041 (or 55,041) and net was SGD 34177.07 [p1:r1]. "
        "Wrong: SGD 54987. Also wrong: 55,040.59."
    )
    assert main.unverified_memo_numbers(text, payload) == ["54987", "55,040.59"]


def test_compare_demo_and_4b(client):
    s = create(client)
    p = sample(client, s)[0]
    item = main.SESSIONS[s]["pages"][p]
    response = client.post(
        f"/api/sessions/{s}/pages/{p}/compare", json={"doc_type": "statement"}
    )
    body = response.json()
    assert body["truth_available"]
    assert all(
        v["status"] == "offline" and v["metrics"]["source"] == "cached-demo"
        for v in body["models"].values()
    )
    assert item["status"] == "uploaded" and item["document"] is None
    assert not main.SESSIONS[s]["requests"]
    assert all(all(v is None for v in r["models"].values()) for r in body["rows"])
    assert (
        client.post(
            f"/api/sessions/{s}/pages/{p}/extract",
            json={"model": "4b", "doc_type": "statement"},
        ).status_code
        == 200
    )


def test_compare_live_error(client, monkeypatch):
    import threading

    s = create(client)
    p = sample(client, s)[0]
    item = main.SESSIONS[s]["pages"][p]
    monkeypatch.delenv("DEMO_MODE")
    monkeypatch.setenv("RUNPOD_ENDPOINT_8B", "base")
    monkeypatch.setenv("RUNPOD_ENDPOINT_FT", "ft")
    monkeypatch.delenv("RUNPOD_ENDPOINT_4B", raising=False)
    barrier = threading.Barrier(2)

    def call(endpoint, payload, key):
        barrier.wait(timeout=5)
        if endpoint == "ft":
            raise RuntimeError("failed secret-test-key")
        return {"output": {"document": item["truth"]}, "executionTime": 1000}

    monkeypatch.setattr(main.worker_client, "call_endpoint", call)
    result = client.post(
        f"/api/sessions/{s}/pages/{p}/compare", json={"doc_type": "statement"}
    ).json()
    assert result["models"]["8b"]["status"] == "ok"
    assert result["models"]["ft"]["status"] == "error"
    assert result["models"]["ft"]["error"] == "RuntimeError: failed [REDACTED]"
    assert result["models"]["4b"]["status"] == "offline"
    assert all(r["models"]["8b"] for r in result["rows"])
    assert item["status"] == "uploaded" and item["document"] is None
    assert len(main.SESSIONS[s]["requests"]) == 2


def test_compare_without_truth(client):
    s = create(client)
    client.post(f"/api/sessions/{s}/pages", files={"files": ("ok.png", png())})
    result = client.post(
        f"/api/sessions/{s}/pages/p1/compare", json={"doc_type": "statement"}
    ).json()
    assert not result["truth_available"] and result["rows"] == []
    assert all(
        v["status"] == "offline" and v["document"] is None
        for v in result["models"].values()
    )


def test_status_4b_offline(client, monkeypatch):
    monkeypatch.delenv("RUNPOD_ENDPOINT_4B", raising=False)
    assert client.get("/api/status").json()["4b"] == {"offline": True}
