"""GPU controls use mocked HTTP only."""

import asyncio
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
    monkeypatch.setattr(
        main,
        "config",
        lambda model: ("assistant" if model == "agent" else None, "private-test-key"),
    )
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
    original_async = httpx.AsyncClient
    monkeypatch.setattr(
        gpu.httpx, "AsyncClient", lambda **kw: original_async(transport=transport, **kw)
    )
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
        assert not client.get("/api/gpu").json()["ready"]
        assert not gpu.PROBERS
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
        assert "Runpod did not apply the change" in response.json()["detail"]


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


def test_switch_refused_from_another_machine(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    request = SimpleNamespace(client=SimpleNamespace(host="192.168.0.20"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(gpu.change_gpu(gpu.Change(on=True, minutes=30), request))
    assert error.value.status_code == 403


@pytest.mark.parametrize("output_list", [False, True])
@pytest.mark.parametrize("worker_status", ["ready", "starting", "failed", "exited"])
def test_probe_result(output_list, worker_status):
    async def run():
        gpu.READINESS.clear()
        calls = []

        def respond(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/health"):
                return httpx.Response(200, json={"workers": {"idle": 2}})
            if request.url.path.endswith("/run"):
                assert json.loads(request.content) == {"input": {"task": "health"}}
                return httpx.Response(200, json={"id": "probe-1"})
            output = {
                "status": worker_status,
                "engine_load_seconds": 176.2,
                "cold_start": False,
            }
            return httpx.Response(
                200,
                json={
                    "status": "COMPLETED",
                    "output": [output] if output_list else output,
                    "delayTime": 55,
                    "executionTime": 401,
                    "workerId": "worker-1",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await gpu.probe_cycle("test", "secret", client)
        state = gpu.ready_fields("test")
        assert state["ready"] is (worker_status == "ready")
        assert bool(state["ready_error"]) is (worker_status in ("failed", "exited"))
        if state["ready"]:
            assert state["checked_at"] <= time.time()
            assert state["last_probe"] == {
                "delay_ms": 55,
                "execution_ms": 401,
                "worker_id": "worker-1",
                "engine_load_s": 176.2,
                "cold_start": False,
            }
        assert calls.count("/v2/test/run") == 1

    asyncio.run(run())


@pytest.mark.parametrize("workers", [{"initializing": 1, "idle": 2}, {}])
def test_probe_waits_for_workers(workers):
    async def run():
        def respond(request):
            assert request.url.path.endswith("/health")
            return httpx.Response(200, json={"workers": workers})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await gpu.probe_cycle("waiting", "secret", client)
        assert not gpu.readiness("waiting")["ready"]
        assert gpu.readiness("waiting")["probe"] == "waiting"

    asyncio.run(run())


def test_probe_queue_timeout(monkeypatch):
    monkeypatch.setattr(gpu, "PROBE_TIMEOUT", 0)

    async def run():
        calls = []

        def respond(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/health"):
                value = {"workers": {"ready": 1}}
            elif request.url.path.endswith("/run"):
                value = {"id": "queued"}
            else:
                value = {"status": "IN_QUEUE"}
            return httpx.Response(200, json=value)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await gpu.probe_cycle("timeout", "secret", client)
            assert "/v2/timeout/cancel/queued" in calls
            assert not gpu.readiness("timeout")["ready"]
            # Still loading inside the grace period: starting, not a problem.
            assert gpu.ready_fields("timeout")["ready_error"] is None
            gpu.readiness("timeout")["unready_since"] = time.time() - gpu.STARTUP_GRACE
            await gpu.probe_cycle("timeout", "secret", client)
            assert (
                gpu.ready_fields("timeout")["ready_error"]
                == "Worker not taking jobs yet"
            )

    asyncio.run(run())


def test_one_prober_and_stop(monkeypatch):
    async def run():
        entered = asyncio.Event()

        async def blocked(endpoint, key):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(gpu, "prober", blocked)
        gpu.start_prober("single", "secret")
        task = gpu.PROBERS["single"]
        await entered.wait()
        gpu.start_prober("single", "secret")
        assert gpu.PROBERS["single"] is task
        gpu.readiness("single")["ready"] = True
        gpu.stop_prober("single")
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert not gpu.readiness("single")["ready"]

    asyncio.run(run())


@pytest.fixture
def fleet(control, monkeypatch):
    ids = {
        "agent": "assistant",
        "4b": "assistant",
        "ft": "trained",
        "8b": "big",
        "image": "image",
    }
    monkeypatch.setattr(main, "config", lambda model: (ids[model], "private-test-key"))
    values = {item: 0 for item in ids.values()}
    calls = []
    failures = set()

    def respond(request):
        endpoint = request.url.path.split("/")[-1]
        if request.url.host == "api.runpod.ai":
            return httpx.Response(200, json={"workers": {"idle": 1}})
        if endpoint in failures:
            return httpx.Response(503, text="private-test-key unavailable")
        if request.method == "PATCH":
            minimum = json.loads(request.content)["workersMin"]
            calls.append((endpoint, minimum))
            values[endpoint] = minimum
        return httpx.Response(
            200, json={"workersMin": values[endpoint], "workersMax": 2}
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(gpu.httpx, "get", client.get)
    monkeypatch.setattr(gpu.httpx, "patch", client.patch)
    yield values, calls, failures
    client.close()


def test_arm_all_dedup_and_off_external(fleet):
    values, calls, _ = fleet
    with TestClient(main.app) as client:
        result = client.post("/api/gpu", json={"on": True, "scope": "all"})
        assert result.status_code == 200
        assert calls == [(item, 1) for item in ("assistant", "trained", "big", "image")]
        assert result.json()["endpoints"][0]["keys"] == ["agent", "4b"]
        assert result.json()["scope"] == "all"
        client.post("/api/gpu", json={"on": False})
        client.post("/api/gpu", json={"on": True})
        values["image"] = 1
        assert client.post("/api/gpu", json={"on": False}).status_code == 200
        assert not any(values.values())


def test_fleet_partial_failure(fleet):
    _, _, failures = fleet
    failures.add("image")
    with TestClient(main.app) as client:
        result = client.get("/api/gpu")
        assert result.status_code == 200
        assert result.json()["endpoints"][-1]["state"] == "problem"
        result = client.post("/api/gpu", json={"on": True, "scope": "all"})
        assert result.status_code == 502
        assert "image" in result.text
        assert "private-test-key" not in result.text
        assert "image" in json.loads(gpu.state_path().read_text())["endpoint_ids"]


@pytest.mark.parametrize("scope", ["bad", None, 3, [], {}])
def test_scope_validation(control, scope):
    with TestClient(main.app) as client:
        assert (
            client.post("/api/gpu", json={"on": True, "scope": scope}).status_code
            == 400
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"ready_error": "failed", "ready": True}, "problem"),
        ({"ready": True}, "ready"),
        ({"workers_min": 1}, "starting"),
        ({"workers": {"running": 1}, "jobs": {"inProgress": 1}}, "busy"),
        ({"workers": {"running": 1}}, "stopping"),
        ({"workers": {"initializing": 1}}, "waking"),
        ({"jobs": {"inQueue": 1}}, "waking"),
        ({"workers": {"idle": 4}}, "asleep"),
        ({}, "asleep"),
    ],
)
def test_state_mapping(value, expected):
    assert gpu.endpoint_state(value) == expected


def test_ready_disappears(control):
    gpu.CACHE = {"gone": (time.monotonic(), {"workers_min": 1})}
    gpu.readiness("gone")["ready"] = True
    assert gpu.cached_endpoint("gone", "secret", {"workers": {}})["state"] == "starting"


def test_status_gpu_state(fleet):
    main.STATUS["time"] = 0
    with TestClient(main.app) as client:
        value = client.get("/api/status").json()
        assert value["agent"]["gpu_state"] == "asleep"
        assert value["4b"]["gpu_state"] == value["agent"]["gpu_state"]


def test_auto_off_retries(fleet, monkeypatch):
    values, calls, failures = fleet
    values.update({item: 1 for item in values})
    failures.add("image")
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if seconds == 30:
            failures.clear()

    monkeypatch.setattr(gpu.asyncio, "sleep", sleep)
    gpu.STATE = {"endpoint_ids": list(values), "scope": "all", "warm_until": 0}
    asyncio.run(gpu.auto_off(list(values), 0))
    assert not any(values.values())
    assert sleeps == [0, 30]
    assert len(calls) == 4


def test_flux_health():
    async def run():
        def respond(request):
            if request.url.path.endswith("/health"):
                return httpx.Response(200, json={"workers": {"ready": 1}})
            if request.url.path.endswith("/run"):
                return httpx.Response(200, json={"id": "flux-check"})
            return httpx.Response(
                200,
                json={
                    "status": "COMPLETED",
                    "output": {
                        "status": "ready",
                        "load_seconds": 42,
                        "gpu": "Fictional GPU",
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await gpu.probe_cycle("flux", "secret", client)
        assert gpu.ready_fields("flux")["last_probe"]["engine_load_s"] == 42
        assert gpu.ready_fields("flux")["last_probe"]["gpu"] == "Fictional GPU"

    asyncio.run(run())


def test_status_and_gpu_share_settings_cache(fleet):
    with TestClient(main.app) as client:
        main.STATUS["time"] = 0
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/gpu").status_code == 200
        assert set(gpu.CACHE) == {"assistant", "big", "trained", "image"}


def test_rearm_widens_scope_and_resets_deadline(fleet):
    with TestClient(main.app) as client:
        first = client.post("/api/gpu", json={"on": True, "minutes": 15}).json()
        second = client.post(
            "/api/gpu", json={"on": True, "minutes": 60, "scope": "all"}
        ).json()
        assert second["warm_until"] > first["warm_until"]
        third = client.post("/api/gpu", json={"on": True}).json()
        assert third["scope"] == "all"
        assert len(json.loads(gpu.state_path().read_text())["endpoint_ids"]) == 4


def test_status_reuses_fresh_gpu_health(fleet, monkeypatch):
    calls = []
    monkeypatch.setattr(
        main, "fetch_health", lambda endpoint, key: calls.append(endpoint) or {}
    )
    monkeypatch.setitem(main.STATUS, "time", -1e9)
    with TestClient(main.app) as client:
        now = time.monotonic()
        for item in ("assistant", "trained", "big", "image"):
            gpu.CACHE[item] = (
                now,
                {"workers_min": 0, "workers": {"running": 1}, "jobs": {}},
            )
        value = client.get("/api/status").json()
    assert not calls
    assert value["agent"]["gpu_state"] == "stopping"
    gpu.CACHE["big"] = (time.monotonic() - 5, gpu.CACHE["big"][1])
    assert gpu.fresh_health("big") is None
