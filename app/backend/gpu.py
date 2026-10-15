"""Timed minimum-worker control for the Assistant endpoint."""

import asyncio
import json
import os
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
CACHE = None
LOCK = asyncio.Lock()


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


def schedule(endpoint, until):
    global TASK
    cancel()
    TASK = asyncio.create_task(auto_off(endpoint, until))


async def auto_off(endpoint, until):
    global STATE, CACHE
    await asyncio.sleep(max(0, until - time.time()))
    while True:
        async with LOCK:
            current, key, reason = credentials()
            if reason or current != endpoint:
                return
            try:
                await run_in_threadpool(remote, endpoint, key, 0)
                persist(None)
                STATE = CACHE = None
                logs.LOG.info("GPU auto-off completed")
                return
            except (HTTPException, OSError):
                logs.LOG.info("GPU auto-off failed; retrying in 30 seconds")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    global STATE, CACHE
    STATE = CACHE = None
    endpoint, key, reason = credentials()
    if not reason:
        try:
            saved = json.loads(state_path().read_text())
            if saved["endpoint_id"] == endpoint and isinstance(
                saved["warm_until"], (int, float)
            ):
                STATE = saved
                schedule(endpoint, saved["warm_until"])
                if saved["warm_until"] <= time.time():
                    # Give the recovery task a chance to turn off before serving requests.
                    await asyncio.sleep(0)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        yield
    finally:
        task = TASK
        cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)


router = APIRouter(lifespan=lifespan)


async def snapshot(endpoint, key, fresh=False):
    global CACHE
    from app.backend.main import GPU_PRICE_PER_SEC, fetch_health

    if not fresh and CACHE and CACHE[0] == endpoint and time.monotonic() - CACHE[1] < 3:
        return CACHE[2]
    settings = await run_in_threadpool(remote, endpoint, key)
    try:
        health = await run_in_threadpool(fetch_health, endpoint, key)
    except Exception:
        health = {}
    prices = []
    for gpu in settings.get("gpuTypeIds", []):
        matches = [
            price * 3600 for name, price in GPU_PRICE_PER_SEC.items() if name in gpu
        ]
        prices.extend(
            matches
            or [
                min(GPU_PRICE_PER_SEC.values()) * 3600,
                max(GPU_PRICE_PER_SEC.values()) * 3600,
            ]
        )
    prices = prices or [
        min(GPU_PRICE_PER_SEC.values()) * 3600,
        max(GPU_PRICE_PER_SEC.values()) * 3600,
    ]
    value = {
        "available": True,
        "endpoint_id": endpoint,
        "name": str(settings.get("name", "")).replace(key, "[REDACTED]"),
        "workers_min": settings["workersMin"],
        "workers_max": settings.get("workersMax"),
        "workers": {
            name: health.get("workers", {}).get(name, 0)
            for name in ("idle", "ready", "running", "initializing", "throttled")
        },
        "warm_until": STATE["warm_until"]
        if STATE and STATE["endpoint_id"] == endpoint
        else None,
        "cost_per_hour_low": round(min(prices), 2),
        "cost_per_hour_high": round(max(prices), 2),
    }
    CACHE = endpoint, time.monotonic(), value
    return value


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


@router.post("/api/gpu")
async def change_gpu(change: Change, request: Request):
    global STATE, CACHE
    # The app listens on the LAN for phone uploads; only this machine may spend money.
    if not request.client or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(403, "The GPU can only be switched from this computer")
    if type(change.minutes) is not int or change.minutes not in (15, 30, 60, 120):
        raise HTTPException(400, "Minutes must be 15, 30, 60 or 120")
    endpoint, key, reason = credentials()
    if reason:
        raise HTTPException(409, reason)
    async with LOCK:
        saved = (
            {"endpoint_id": endpoint, "warm_until": time.time() + change.minutes * 60}
            if change.on
            else None
        )
        # Persist before enabling so a crash cannot lose the shutoff deadline.
        if saved:
            try:
                persist(saved)
            except OSError:
                raise HTTPException(
                    500, "Could not save the GPU shutoff timer"
                ) from None
            STATE = saved
            schedule(endpoint, saved["warm_until"])
        await run_in_threadpool(remote, endpoint, key, int(change.on))
        if not change.on:
            cancel()
            try:
                persist(None)
            except OSError:
                raise HTTPException(
                    500, "GPU is off, but the saved timer could not be cleared"
                ) from None
            STATE = None
        CACHE = None
        logs.LOG.info("GPU %s", "on with shutoff timer" if change.on else "off")
        return await snapshot(endpoint, key, fresh=True)
