"""Bounded, redacted logs shared by backend, browser and workers."""

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3}
FIELDS = ("session", "job_id", "turn", "page_id", "task")
LOG = logging.getLogger("ledgerlens")
# A session id is a bearer token for that session's documents: never show one in a message.
SESSION_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
SESSION_PATTERN = re.compile(r"[a-f0-9]{32}")
router = APIRouter()
STREAM_MAX_SECONDS = None


class LogBuffer:
    def __init__(self, maxlen=2000):
        self.entries = deque(maxlen=maxlen)
        self.lock = threading.Lock()
        self.counter = 0

    def add(self, source, level, message, **fields):
        from app.backend.main import clean

        level = str(level).lower()
        level = {"critical": "error", "fatal": "error", "warn": "warning"}.get(
            level, level
        )
        entry = clean(
            {
                "ts": time.time(),
                "source": source
                if source in ("app", "browser", "worker", "vllm")
                else "app",
                "level": level if level in LEVELS else "info",
                "message": str(message),
                **{k: str(fields[k]) for k in FIELDS if fields.get(k) is not None},
            }
        )
        entry["message"] = SESSION_ID.sub("[session]", entry["message"])[:2000]
        for k in FIELDS:
            if k in entry:
                entry[k] = entry[k][:100]
        with self.lock:
            self.counter += 1
            entry["id"] = self.counter
            self.entries.append(entry)
        return dict(entry)

    def latest_id(self):
        with self.lock:
            return self.counter

    def since(
        self,
        cursor=0,
        *,
        source=None,
        level=None,
        session=None,
        turn=None,
        job_id=None,
        limit=500,
        viewer=None,
    ):
        """Entries after `cursor`; with `viewer`, only that session's own entries and
        app-wide entries that carry no session."""
        filters = {"source": source, "session": session, "turn": turn, "job_id": job_id}
        with self.lock:
            entries = [
                dict(e)
                for e in self.entries
                if e["id"] > cursor
                and (viewer is None or e.get("session") in (None, viewer))
                and LEVELS[e["level"]] >= LEVELS.get(level, 0)
                and all(not v or e.get(k) == v for k, v in filters.items())
            ]
            return entries[: max(1, min(limit, 1000))], max(cursor, self.counter)


BUFFER = LogBuffer()


class BufferHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._ledgerlens_buffer = True
        self.local = threading.local()
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        self.plain = logging.Formatter("%(message)s")

    def emit(self, record):
        # The handler sits on several loggers; a record that propagates through two of
        # them is stored once.
        if getattr(self.local, "active", False) or getattr(
            record, "_ledgerlens", False
        ):
            return
        record._ledgerlens = True
        self.local.active = True
        try:
            fields = {k: record.__dict__.get(k) for k in FIELDS}
            level = record.levelname
            if record.name == "uvicorn.access" and isinstance(record.args, tuple):
                # An access line for /api/sessions/<id>/... belongs to that session only.
                found = SESSION_ID.search(" ".join(map(str, record.args)))
                fields["session"] = fields["session"] or (found and found.group(0))
                status = record.args[-1] if record.args else 0
                if isinstance(status, int) and status >= 400:
                    level = "error" if status >= 500 else "warning"
            # The app's own lines need no logger name in front.
            formatter = self.plain if record.name == LOG.name else self.formatter
            BUFFER.add("app", level, formatter.format(record), **fields)
        except Exception:
            try:
                self.handleError(record)
            except Exception:
                pass
        finally:
            self.local.active = False


class AccessFilter(logging.Filter):
    def filter(self, record):
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        try:
            _, method, path, _, status = record.args
            return not (
                path.startswith("/api/logs") or (method == "GET" and int(status) < 400)
            )
        except (TypeError, ValueError, AttributeError):
            return True


def install():
    for name in (None, "uvicorn", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(getattr(h, "_ledgerlens_buffer", False) for h in logger.handlers):
            handler = BufferHandler()
            if name == "uvicorn.access":
                handler.addFilter(AccessFilter())
            logger.addHandler(handler)
    LOG.setLevel(logging.INFO)


class RateLimited(Exception):
    pass


_WINDOWS = {}
_WINDOW_LOCK = threading.Lock()


def accept_browser(session_or_ip, entries):
    now = time.monotonic()
    with _WINDOW_LOCK:
        for key in list(_WINDOWS):
            window = _WINDOWS[key]
            while window and window[0] <= now - 60:
                window.popleft()
            if not window:
                del _WINDOWS[key]
        window = _WINDOWS.get(session_or_ip)
        if window is None:
            if len(_WINDOWS) >= 1000:
                raise RateLimited()
            window = _WINDOWS[session_or_ip] = deque()
        if len(window) + len(entries) > 120:
            raise RateLimited()
        window.extend([now] * len(entries))
    return len(entries)


class BrowserEntry(BaseModel):
    level: Literal["debug", "info", "warning", "error"]
    message: str = Field(min_length=1, max_length=2000)
    url: str | None = Field(default=None, max_length=300)


class BrowserBatch(BaseModel):
    session: str
    entries: list[BrowserEntry] = Field(min_length=1, max_length=20)


def viewer(session):
    """Only someone holding a live session id may read or write its logs."""
    from app.backend.main import SESSIONS

    if not SESSION_PATTERN.fullmatch(session or "") or session not in SESSIONS:
        raise HTTPException(404, "Session not found")
    return session


@router.post("/api/logs/browser")
async def browser_logs(request: Request):
    body = await request.body()
    if len(body) > 32 * 1024:
        raise HTTPException(413, "Log body too large")
    try:
        batch = BrowserBatch.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid browser logs") from None
    viewer(batch.session)
    try:
        accepted = accept_browser(batch.session, batch.entries)
    except RateLimited:
        raise HTTPException(429, "Log rate limit exceeded") from None
    for entry in batch.entries:
        BUFFER.add(
            "browser",
            entry.level,
            f"{entry.url}: {entry.message}" if entry.url else entry.message,
            session=batch.session,
        )
    return {"accepted": accepted}


def filters(session, source=None, level=None, turn=None, job_id=None):
    # `session` is the caller's own session: it scopes what they may see.
    return dict(
        viewer=viewer(session), source=source, level=level, turn=turn, job_id=job_id
    )


@router.get("/api/logs")
def get_logs(
    session: str,
    since: int = 0,
    source: str | None = None,
    level: str | None = None,
    turn: str | None = None,
    job_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
):
    entries, cursor = BUFFER.since(
        since, **filters(session, source, level, turn, job_id), limit=limit
    )
    return {"entries": entries, "cursor": cursor}


async def stream_entries(since, filters, poll=0.25, ping=15.0, is_disconnected=None):
    started = last_ping = time.monotonic()
    while True:
        if is_disconnected and await is_disconnected():
            return
        entries, cursor = BUFFER.since(since, **filters, limit=1000)
        for entry in entries:
            yield f"id: {entry['id']}\nevent: log\ndata: {json.dumps(entry)}\n\n"
        # Drain a full batch before advancing past entries not yet sent.
        since = entries[-1]["id"] if len(entries) == 1000 else cursor
        now = time.monotonic()
        if now - last_ping >= ping:
            yield ": ping\n\n"
            last_ping = now
        if STREAM_MAX_SECONDS is not None and now - started >= STREAM_MAX_SECONDS:
            return
        await asyncio.sleep(poll)


@router.get("/api/logs/stream")
def log_stream(
    request: Request,
    session: str,
    since: int | None = None,
    source: str | None = None,
    level: str | None = None,
    turn: str | None = None,
    job_id: str | None = None,
):
    scope = filters(session, source, level, turn, job_id)
    if since is None:
        try:
            since = int(request.headers["last-event-id"])
        except KeyError:
            since = BUFFER.latest_id()
        except ValueError:
            raise HTTPException(422, "Invalid Last-Event-ID") from None
    return StreamingResponse(
        stream_entries(
            since,
            scope,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def poll_worker_logs(
    endpoint,
    key,
    *,
    session,
    turn,
    job_id,
    stop,
    interval=5.0,
    read_seconds=2.0,
    client_factory=httpx.AsyncClient,
):
    timestamps = {}
    seen = set()
    ordered = deque()
    warned = False
    fields = dict(session=session, turn=turn, job_id=job_id, task="agent")
    try:
        async with client_factory(timeout=10) as client:
            while not stop.is_set():
                try:
                    base = f"https://api.runpod.io/v2/serverless/{endpoint}/workers"
                    headers = {"Authorization": f"Bearer {key}"}
                    response = await client.get(base, headers=headers)
                    response.raise_for_status()
                    for worker in response.json().get("workers", [])[:4]:
                        if stop.is_set():
                            break
                        wid = worker["id"]
                        params = (
                            {"since": timestamps[wid]}
                            if wid in timestamps
                            else {"tail": 50}
                        )
                        try:
                            async with asyncio.timeout(read_seconds):
                                async with client.stream(
                                    "GET",
                                    f"{base}/{wid}/logs",
                                    headers=headers,
                                    params=params,
                                ) as response:
                                    response.raise_for_status()
                                    async for line in response.aiter_lines():
                                        if stop.is_set():
                                            break
                                        if not line.startswith("data:"):
                                            continue
                                        try:
                                            data = json.loads(line[5:])
                                            ts, message = data["ts"], str(data["line"])
                                        except (ValueError, KeyError, TypeError):
                                            continue
                                        identity = (wid, ts, message)
                                        if identity in seen:
                                            continue
                                        seen.add(identity)
                                        ordered.append(identity)
                                        if len(ordered) > 5000:
                                            seen.discard(ordered.popleft())
                                        timestamps[wid] = max(
                                            ts, timestamps.get(wid, ts)
                                        )
                                        level = (
                                            "error"
                                            if any(
                                                word in message.lower()
                                                for word in ("error", "traceback")
                                            )
                                            else "info"
                                        )
                                        origin = data.get("source", "container")
                                        BUFFER.add(
                                            "worker",
                                            level,
                                            f"[worker {wid[:8]}] [{origin}] {message}",
                                            **fields,
                                        )
                        except TimeoutError:
                            pass
                except Exception as exc:
                    if not warned:
                        BUFFER.add(
                            "app",
                            "warning",
                            f"worker log poll failed: {type(exc).__name__}",
                            **fields,
                        )
                        warned = True
                try:
                    await asyncio.wait_for(stop.wait(), interval)
                except TimeoutError:
                    pass
    except Exception as exc:
        if not warned:
            BUFFER.add(
                "app",
                "warning",
                f"worker log poll failed: {type(exc).__name__}",
                **fields,
            )
