"""GPU controls use mocked HTTP only."""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backend import gpu, main


@pytest.fixture
def control(monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGERLENS_GPU_STATE", str(tmp_path / "warm.json"))
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setattr(main, "config", lambda model: ("assistant", "private-test-key"))
    state = {"minimum": 0, "patches": [], "apply": True, "status": 200}

    def respond(request):
        assert request.headers["Authorization"] == "Bearer private-test-key"
        if request.method == "PATCH":
            value = json.loads(request.content)["workersMin"]
            state["patches"].append(value)
            if state["status"] != 200:
                return httpx.Response(state["status"], text="private-test-key failed")
            if state["apply"]:
                state["minimum"] = value
        if request.url.host == "api.runpod.ai":
            return httpx.Response(200, json={"workers": {"idle": 1}})
        return httpx.Response(
            200,
            json={
                "workersMin": state["minimum"],
                "workersMax": 2,
                "name": "Assistant",
                "gpuTypeIds": ["NVIDIA GeForce RTX 5090", "NVIDIA GeForce RTX 3090"],
            },
        )

    transport = httpx.MockTransport(respond)
    client = httpx.Client(transport=transport)
    monkeypatch.setattr(gpu.httpx, "get", client.get)
    monkeypatch.setattr(gpu.httpx, "patch", client.patch)
    yield state
    client.close()


def test_on_off(control, caplog):
    with TestClient(main.app) as client:
        response = client.post("/api/gpu", json={"on": True})
        assert response.status_code == 200
        value = response.json()
        assert value["workers_min"] == 1
        assert value["cost_per_hour_low"] == 0.69
        assert value["cost_per_hour_high"] == 1.58
        assert json.loads(gpu.state_path().read_text())["warm_until"] > time.time()
        assert client.post("/api/gpu", json={"on": False}).status_code == 200
        assert not gpu.state_path().exists()
        assert control["patches"] == [1, 0]
        assert "private-test-key" not in response.text + caplog.text


@pytest.mark.parametrize("minutes", [5, 30.5, "30", None, True])
def test_invalid_and_unapplied(control, minutes):
    with TestClient(main.app) as client:
        assert (
            client.post("/api/gpu", json={"on": True, "minutes": minutes}).status_code
            == 400
        )
        control["apply"] = False
        response = client.post("/api/gpu", json={"on": True})
        assert response.status_code == 502
        assert response.json()["detail"] == "Runpod did not apply the change"


def test_error_redaction(control, caplog):
    control["status"] = 403
    with TestClient(main.app) as client:
        response = client.post("/api/gpu", json={"on": True})
        assert response.status_code == 502
        assert "403" in response.text
        assert "private-test-key" not in response.text + caplog.text


def test_expired_startup(control):
    control["minimum"] = 1
    gpu.persist({"endpoint_id": "assistant", "warm_until": time.time() - 5})
    with TestClient(main.app) as client:
        for _ in range(20):
            if client.get("/api/gpu").json()["workers_min"] == 0:
                break
        assert control["patches"] == [0]
        assert not gpu.state_path().exists()


def test_demo(control, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    with TestClient(main.app) as client:
        assert client.get("/api/gpu").json()["available"] is False
        assert client.post("/api/gpu", json={"on": True}).status_code == 409
        assert control["patches"] == []


def test_external_and_restored_timer(control):
    control["minimum"] = 1
    with TestClient(main.app) as client:
        assert client.get("/api/gpu").json()["warm_until"] is None
        assert not control["patches"]
    until = time.time() + 600
    gpu.persist({"endpoint_id": "assistant", "warm_until": until})
    with TestClient(main.app) as client:
        assert client.get("/api/gpu").json()["warm_until"] == until
        assert not control["patches"]
