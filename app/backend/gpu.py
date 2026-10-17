"""Timed minimum-worker control and proven readiness for configured endpoints."""

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictBool
from starlette.concurrency import run_in_threadpool

from app.backend import logs

LOCAL_HOSTS = {"127.0.0.1", "::1", "testclient"}
TASK = None
STATE = None
CACHE = {}
LOCK = asyncio.Lock()
SETTINGS_LOCK = threading.Lock()
SETTINGS_LOCKS = {}
PROBERS = {}
READINESS = {}
PROBE_TIMEOUT = 90
# A fresh 8B worker measured ~5 min to pull and load; only a longer wait is a problem.
STARTUP_GRACE = 600


def readiness(endpoint):
    return READINESS.setdefault(
        endpoint,
        {
            "ready": False,
            "checked_at": None,
            "probe": "idle",
            "last_probe": None,
            "error": None,
            "unready_since": None,
        },
    )


def start_prober(endpoint, key):
    if endpoint not in PROBERS or PROBERS[endpoint].done():
        PROBERS[endpoint] = asyncio.create_task(prober(endpoint, key))


def stop_prober(endpoint):
    task = PROBERS.pop(endpoint, None)
    if task:
        task.cancel()
    READINESS.pop(endpoint, None)
    return task


async def probe_cycle(endpoint, key, client):
    state = readiness(endpoint)
    # Keep a proven "ready" through a re-check; only a failed check clears it.
    state.update(probe="waiting", error=None)
    base = f"https://api.runpod.ai/v2/{endpoint}"

    async def request(method, path, **kwargs):
        response = await client.request(method, base + path, **kwargs)
        check(response, key)
        return response.json()

    health = await request("GET", "/health")
    workers = health.get("workers", {})
    if workers.get("initializing", 0) or not any(workers.values()):
        state["ready"] = False
        return
    job = None
    terminal = False
    try:
        value = await request("POST", "/run", json={"input": {"task": "health"}})
        job = value["id"]
        state["probe"] = "running"
        deadline = time.monotonic() + PROBE_TIMEOUT
        while True:
            value = await request("GET", f"/status/{job}")
            status = value.get("status")
            if status in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
                terminal = True
                output = value.get("output")
                if isinstance(output, list):
                    output = output[-1] if output else {}
                output = output if isinstance(output, dict) else {}
                if status == "COMPLETED" and output.get("status") == "ready":
                    state.update(
                        ready=True,
                        unready_since=None,
                        checked_at=time.time(),
                        last_probe={
                            "delay_ms": value.get("delayTime"),
                            "execution_ms": value.get("executionTime"),
                            "worker_id": str(value["workerId"]).replace(
                                key, "[REDACTED]"
                            )
                            if value.get("workerId") is not None
                            else None,
                            "engine_load_s": output.get(
                                "engine_load_seconds", output.get("load_seconds")
                            ),
                            "cold_start": output.get("cold_start"),
                        },
                    )
                    if output.get("gpu") is not None:
                        state["last_probe"]["gpu"] = str(output["gpu"]).replace(
                            key, "[REDACTED]"
                        )
                else:
                    state["ready"] = False
                    if status != "COMPLETED" or output.get("status") != "starting":
                        state["error"] = "Worker health check failed"
                return
            if time.monotonic() >= deadline:
                # A queued health job while the model loads is normal, not a fault.
                since = state["unready_since"] or time.time()
                state.update(ready=False, unready_since=since)
                if time.time() - since >= STARTUP_GRACE:
                    state["error"] = "Worker not taking jobs yet"
                return
            await asyncio.sleep(2)
    finally:
        if job and not terminal:
            await request("POST", f"/cancel/{job}")
        state["probe"] = "idle"


async def prober(endpoint, key):
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {key}"}, timeout=15
    ) as client:
        while True:
            try:
                await probe_cycle(endpoint, key, client)
            except (httpx.HTTPError, HTTPException, ValueError, KeyError, TypeError):
                readiness(endpoint).update(
                    ready=False, probe="idle", error="Could not check the worker"
                )
            await asyncio.sleep(15 if readiness(endpoint)["ready"] else 5)


def state_path():
    return Path(
        os.getenv("LEDGERLENS_GPU_STATE", "~/.ledgerlens/gpu-warm.json")
    ).expanduser()


def credentials():
    from app.backend.main import config

    if os.getenv("DEMO_MODE") == "1":
        return None, None, "GPU control is unavailable in demo mode"
    endpoint, key = config("agent")
    return (
        endpoint,
        key,
        None if endpoint and key else "Assistant endpoint or API key is missing",
    )


def remote(endpoint, key, minimum=None):
    url = f"https://rest.runpod.io/v1/endpoints/{endpoint}"
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if minimum is not None:
            response = httpx.patch(
                url, headers=headers, json={"workersMin": minimum}, timeout=15
            )
            check(response, key)
        response = httpx.get(url, headers=headers, timeout=15)
        check(response, key)
        value = response.json()
        if minimum is not None and value.get("workersMin") != minimum:
            raise HTTPException(502, "Runpod did not apply the change")
        return value
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            502, "Could not read or change Runpod GPU settings"
        ) from None


def check(response, key):
    if not response.is_success:
        body = response.text.replace(key, "[REDACTED]")[:240]
        raise HTTPException(502, f"Runpod returned HTTP {response.status_code}: {body}")


def persist(value):
    path = state_path()
    if value is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(path)


def cancel():
    global TASK
    if TASK:
        TASK.cancel()
    TASK = None


def targets():
    from app.backend.main import config

    result = {}
    for model, label in (
        ("agent", "Assistant"),
        ("ft", "Trained small model"),
        ("8b", "Big model"),
        ("4b", "Small model"),
        ("image", "Cover image (FLUX)"),
    ):
        endpoint, _ = config(model)
        if not endpoint:
            continue
        if endpoint in result:
            result[endpoint]["keys"].append(model)
            result[endpoint]["label"] += " + " + label
        else:
            result[endpoint] = {
                "keys": [model],
                "endpoint_id": endpoint,
                "label": label,
            }
    return list(result.values())


def endpoint_state(value):
    if value.get("ready_error"):
        return "problem"
    if value.get("ready"):
        return "ready"
    if value.get("workers_min", 0) >= 1:
        return "starting"
    jobs = value.get("jobs", {})
    if value.get("workers", {}).get("running", 0):
        # A running worker with no jobs is winding down after min went to 0
        # (measured ~5 min on Runpod) and is still billed.
        if jobs.get("inProgress", 0) or jobs.get("inQueue", 0):
            return "busy"
        return "stopping"
    if value.get("workers", {}).get("initializing", 0) or value.get("jobs", {}).get(
        "inQueue", 0
    ):
        return "waking"
    return "asleep"


def schedule(endpoints, until):
    global TASK
    cancel()
    TASK = asyncio.create_task(auto_off(endpoints, until))


async def auto_off(endpoints, until):
    global STATE, CACHE
    pending = set([endpoints] if isinstance(endpoints, str) else endpoints)
    await asyncio.sleep(max(0, until - time.time()))
    while pending:
        async with LOCK:
            _, key, reason = credentials()
            if reason:
                return
            for endpoint in list(pending):
                try:
                    await run_in_threadpool(remote, endpoint, key, 0)
                    pending.remove(endpoint)
                    stop_prober(endpoint)
                    CACHE.pop(endpoint, None)
                except HTTPException:
                    logs.LOG.info("GPU auto-off failed; retrying in 30 seconds")
            if not pending:
                try:
                    persist(None)
                except OSError:
                    pending.update(STATE["endpoint_ids"] if STATE else [])
                else:
                    STATE = None
                    return
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    global STATE, CACHE
    STATE = None
    CACHE = {}
    READINESS.clear()
    endpoint, key, reason = credentials()
    if not reason:
        try:
            saved = json.loads(state_path().read_text())
            ids = saved.get("endpoint_ids", [saved.get("endpoint_id")])
            if (
                ids
                and all(isinstance(item, str) and item for item in ids)
                and isinstance(saved["warm_until"], (int, float))
            ):
                STATE = {
                    "endpoint_ids": list(dict.fromkeys(ids)),
                    "warm_until": saved["warm_until"],
                    "scope": saved.get("scope", "agent"),
                }
                schedule(STATE["endpoint_ids"], saved["warm_until"])
                for item in STATE["endpoint_ids"]:
                    start_prober(item, key)
                await asyncio.sleep(0)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        yield
    finally:
        tasks = list(PROBERS.values())
        for endpoint in list(PROBERS):
            stop_prober(endpoint)
        task = TASK
        cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if task:
            await asyncio.gather(task, return_exceptions=True)


router = APIRouter(lifespan=lifespan)


def settings_snapshot(endpoint, key):
    with SETTINGS_LOCK:
        lock = SETTINGS_LOCKS.setdefault(endpoint, threading.Lock())
    with lock:
        cached = CACHE.get(endpoint)
        if cached and time.monotonic() - cached[0] < 3:
            if cached[1].get("settings_error"):
                raise HTTPException(502, "Could not check this endpoint")
            return dict(cached[1])
        try:
            settings = remote(endpoint, key)
        except (HTTPException, httpx.HTTPError, ValueError):
            CACHE[endpoint] = (
                time.monotonic(),
                {**(cached[1] if cached else {}), "settings_error": True},
            )
            raise
        value = {
            "workers_min": settings.get("workersMin", 0),
            "workers_max": settings.get("workersMax"),
            "settings": settings,
        }
        CACHE[endpoint] = (time.monotonic(), value)
        return dict(value)


def fresh_health(endpoint):
    """The last /health seen within three seconds, so every view shows the same reading."""
    cached = CACHE.get(endpoint)
    if cached and time.monotonic() - cached[0] < 3 and "workers" in cached[1]:
        return {"workers": cached[1]["workers"], "jobs": cached[1].get("jobs", {})}
    return None


def cached_endpoint(endpoint, key, health=None):
    """Shared three-second settings cache, also used by /api/status."""
    value = settings_snapshot(endpoint, key)
    if health is not None:
        value = {
            **value,
            "workers": health.get("workers", {}),
            "jobs": health.get("jobs", {}),
        }
        CACHE[endpoint] = (CACHE[endpoint][0], value)
    if readiness(endpoint)["error"] == "Could not check this endpoint":
        readiness(endpoint)["error"] = None
    workers = value.get("workers", {})
    if "workers" in value and not any(
        workers.get(name, 0) for name in ("idle", "ready", "running")
    ):
        readiness(endpoint)["ready"] = False
    value = {**value, **ready_fields(endpoint)}
    value["state"] = endpoint_state(value)
    return value


async def snapshot(endpoint, key, fresh=False):
    from app.backend.main import (
        GPU_PRICE_PER_SEC,
        IMAGE_GPU_PRICE_PER_SEC,
        fetch_health,
    )

    async def one(target):
        item = target["endpoint_id"]
        try:
            cached = CACHE.get(item)
            if (
                not fresh
                and cached
                and time.monotonic() - cached[0] < 3
                and "workers" in cached[1]
            ):
                value = cached_endpoint(item, key)
            else:
                _, health = await asyncio.gather(
                    run_in_threadpool(settings_snapshot, item, key),
                    run_in_threadpool(fetch_health, item, key),
                )
                value = cached_endpoint(item, key, health)
            if value["workers_min"] >= 1:
                start_prober(item, key)
            else:
                stop_prober(item)
                value.update(ready_fields(item))
                value["state"] = endpoint_state(value)
        except (HTTPException, httpx.HTTPError, ValueError, TypeError, KeyError):
            readiness(item).update(ready=False, error="Could not check this endpoint")
            value = {
                **(CACHE.get(item, (0, {}))[1]),
                "workers_min": CACHE.get(item, (0, {}))[1].get("workers_min", 0),
                "workers_max": CACHE.get(item, (0, {}))[1].get("workers_max"),
                **ready_fields(item),
                "state": "problem",
            }
        settings = value.pop("settings", {})
        table = (
            IMAGE_GPU_PRICE_PER_SEC if "image" in target["keys"] else GPU_PRICE_PER_SEC
        )
        prices = [
            price * 3600
            for gpu in settings.get("gpuTypeIds", [])
            for name, price in table.items()
            if name in gpu
        ]
        prices = prices or [min(table.values()) * 3600, max(table.values()) * 3600]
        return {
            **target,
            **value,
            "name": str(settings.get("name", "")).replace(key, "[REDACTED]"),
            "workers": {
                name: value.get("workers", {}).get(name, 0)
                for name in ("idle", "ready", "running", "initializing", "throttled")
            },
            "jobs": {
                name: value.get("jobs", {}).get(name, 0)
                for name in ("inQueue", "inProgress")
            },
            "cost_per_hour_low": round(min(prices), 2),
            "cost_per_hour_high": round(max(prices), 2),
        }

    values = await asyncio.gather(*(one(target) for target in targets()))
    agent = next(value for value in values if value["endpoint_id"] == endpoint)
    return {
        **agent,
        "available": True,
        "warm_until": STATE["warm_until"] if STATE else None,
        "scope": STATE["scope"] if STATE else None,
        "endpoints": values,
        "all_cost_per_hour_low": round(sum(v["cost_per_hour_low"] for v in values), 2),
        "all_cost_per_hour_high": round(
            sum(v["cost_per_hour_high"] for v in values), 2
        ),
    }


def ready_fields(endpoint):
    state = readiness(endpoint)
    return {
        **{k: v for k, v in state.items() if k not in ("error", "unready_since")},
        "ready_error": state["error"],
    }


@router.get("/api/gpu")
async def get_gpu():
    endpoint, key, reason = credentials()
    if reason:
        return {"available": False, "reason": reason}
    async with LOCK:
        return await snapshot(endpoint, key)


class Change(BaseModel):
    on: StrictBool
    minutes: object = 30
    scope: object = "agent"


@router.post("/api/gpu")
async def change_gpu(change: Change, request: Request):
    global STATE, CACHE
    # The app listens on the LAN for phone uploads; only this machine may spend money.
    if not request.client or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(403, "The GPU can only be switched from this computer")
    if type(change.minutes) is not int or change.minutes not in (15, 30, 60, 120):
        raise HTTPException(400, "Minutes must be 15, 30, 60 or 120")
    if change.scope not in ("agent", "all"):
        raise HTTPException(400, "Scope must be agent or all")
    endpoint, key, reason = credentials()
    if reason:
        raise HTTPException(409, reason)
    async with LOCK:
        if change.on:
            ids = (
                [target["endpoint_id"] for target in targets()]
                if change.scope == "all"
                else [endpoint]
            )
            ids = list(dict.fromkeys((STATE["endpoint_ids"] if STATE else []) + ids))
            saved = {
                "endpoint_ids": ids,
                "warm_until": time.time() + change.minutes * 60,
                "scope": "all"
                if change.scope == "all" or (STATE and STATE["scope"] == "all")
                else "agent",
            }
            try:
                persist(saved)
            except OSError:
                raise HTTPException(
                    500, "Could not save the GPU shutoff timer"
                ) from None
            STATE = saved
            schedule(ids, saved["warm_until"])
        else:
            ids = list(
                dict.fromkeys(
                    [t["endpoint_id"] for t in targets()]
                    + (STATE["endpoint_ids"] if STATE else [])
                )
            )

        async def apply(item):
            # Endpoints are changed in parallel so the switch answers in one round trip.
            try:
                if (
                    change.on
                    or (await run_in_threadpool(remote, item, key)).get("workersMin", 0)
                    >= 1
                ):
                    await run_in_threadpool(remote, item, key, int(change.on))
                if change.on:
                    start_prober(item, key)
                else:
                    stop_prober(item)
            except HTTPException as exc:
                return f"{item}: {exc.detail}"

        failures = [f for f in await asyncio.gather(*map(apply, ids)) if f]
        CACHE = {}
        if not change.on:
            for item in list(PROBERS):
                stop_prober(item)
        if failures and not change.on:
            saved = {"endpoint_ids": ids, "warm_until": time.time(), "scope": "all"}
            persist(saved)
            STATE = saved
            schedule(ids, saved["warm_until"])
        if failures:
            raise HTTPException(502, "; ".join(failures))
        if not change.on:
            cancel()
            persist(None)
            STATE = None
        return await snapshot(endpoint, key, fresh=True)
