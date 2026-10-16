import base64
import io
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.backend import cover, main


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNPOD_API_KEY", "cover-secret")
    monkeypatch.setenv("RUNPOD_ENDPOINT_IMAGE", "test-image")
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setattr(cover, "SAMPLE_PATH", tmp_path / "sample.jpg")
    client = TestClient(main.app)
    s = client.post("/api/sessions").json()["session"]
    main.SESSIONS[s]["pages"]["p1"] = {"document": READ}
    return client, s, f"/api/sessions/{s}/cover"


READ = {
    "doc_type": "invoice",
    "currency": "SGD",
    "line_items": [{"description": "Sugar"}],
}


def picture(size=(8, 8)):
    data = io.BytesIO()
    Image.new("RGB", size).save(data, format="JPEG")
    return base64.b64encode(data.getvalue()).decode()


def wait(client, url):
    for _ in range(200):
        state = client.get(url).json()
        if state["status"] != "running":
            return state
        time.sleep(0.005)
    pytest.fail("Cover thread did not finish")


def fake_http(monkeypatch, result, gate=None):
    submissions = []

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            submissions.append(json)
            return Response({"id": "job"})

        def get(self, url):
            if gate:
                assert gate.wait(3)
            return Response(result)

    monkeypatch.setattr(cover.httpx, "Client", Client)
    return submissions


def test_offline(setup, monkeypatch):
    client, _, url = setup
    monkeypatch.setenv("DEMO_MODE", "1")
    assert client.get(url).json() == {"status": "idle"}
    assert (
        client.post(url, json={}).json()["error"]
        == "Image endpoint not configured; showing figures only."
    )
    cover.SAMPLE_PATH.write_bytes(base64.b64decode(picture()))
    state = client.post(url, json={}).json()
    assert state["status"] == "done"
    assert state["source"] == "recorded"
    assert state["metrics"] is None
    assert state["image"].startswith("data:image/jpeg;base64,")


def test_live_reuse_force_and_secret(setup, monkeypatch):
    client, s, url = setup
    gate = threading.Event()
    calls = fake_http(
        monkeypatch,
        {
            "status": "COMPLETED",
            "delayTime": 20,
            "executionTime": 2000,
            "output": [
                {
                    "image_base64": picture(),
                    "mime": "image/jpeg",
                    "gpu": "RTX 3090",
                    "seed": 42,
                }
            ],
        },
        gate,
    )
    first = client.post(url, json={}).json()
    assert first["status"] == "running"
    reused = client.post(url, json={}).json()
    assert reused["started"] == first["started"]
    assert reused["prompt"] == first["prompt"]
    assert reused["status"] == "running"
    assert client.post(url, json={"force": True}).status_code == 409
    assert client.post(url, json={"prompt": "custom"}).status_code == 409
    gate.set()
    state = wait(client, url)
    assert state["status"] == "done"
    assert state["image"].startswith("data:image/jpeg;base64,")
    assert state["metrics"]["cost"] == pytest.approx(2 * 0.69 / 3600)
    client.post(url, json={})
    assert len(calls) == 1
    client.post(url, json={"force": True, "seed": 7})
    assert wait(client, url)["status"] == "done"
    assert len(calls) == 2
    assert calls[-1]["input"]["seed"] == 7
    assert main.SESSIONS[s]["requests"][0]["task"] == "cover"
    assert "cover-secret" not in client.get(url).text


@pytest.mark.parametrize(
    "result",
    [
        {"status": "FAILED", "error": "cover-secret worker failed"},
        {"status": "COMPLETED", "output": {"error": "cover-secret failed"}},
        {
            "status": "COMPLETED",
            "output": {"image_base64": picture((2001, 2000)), "mime": "image/jpeg"},
        },
    ],
)
def test_failed_and_oversized(setup, monkeypatch, result):
    client, _, url = setup
    fake_http(monkeypatch, result)
    client.post(url, json={})
    state = wait(client, url)
    assert state["status"] == "error"
    assert "cover-secret" not in str(state)
    assert len(state["error"]) <= 300


def test_no_read_pages_skips_the_gpu(setup, monkeypatch):
    client, s, url = setup
    calls = fake_http(monkeypatch, {"status": "COMPLETED"})
    main.SESSIONS[s]["pages"].clear()
    state = client.post(url, json={}).json()
    assert state["status"] == "error"
    assert state["error"] == "No pages read yet; showing figures only."
    assert calls == []
    assert client.get(url).json() == {"status": "idle"}
    main.SESSIONS[s]["pages"]["p1"] = {"document": READ}
    assert client.post(url, json={}).json()["status"] == "running"
    assert len(calls) == 1


def test_invalid_prompt(setup):
    client, _, url = setup
    assert client.post(url, json={"prompt": "  "}).status_code == 422


def test_image_gpu_prices_apply_only_to_the_image_endpoint():
    from app.backend import main

    assert main.gpu_price("image", "NVIDIA L40S") == 1.75 / 3600
    assert main.gpu_price("image", "NVIDIA RTX A6000") == 1.22 / 3600
    blackwell = "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb"
    assert main.gpu_price("image", blackwell) == 1.75 / 3600
    assert main.gpu_price("agent", "NVIDIA L40S") == main.GPU_PRICE_PER_SEC["RTX 5090"]
