"""Read attached evidence and stream session-scoped assistant turns."""

import asyncio
import base64
import io
import json
import math
import os
import secrets
import threading
import time
from contextlib import aclosing
from pathlib import Path

import anyio
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.backend import logs
from ledgerlens.agent.limits import MAX_BODY_BYTES
from ledgerlens.agent.prompts import SECTIONS
from ledgerlens.direction import check_direction
from ledgerlens.images import DEFAULT_MAX_PIXELS, resize_image
from ledgerlens.schema import DOCUMENT_ADAPTER, Statement

router = APIRouter()
RECORDINGS = Path(__file__).resolve().parents[1] / "sample/agent"
SUGGESTIONS = [
    ("Write a credit memo for the committee.", "memo_preread"),
    (
        "Who is the largest payer, and what share of money in do they make up?",
        "ask_data",
    ),
    ("Which month had the lowest net cash flow?", "ask_data_month"),
    ("Make the red flags section shorter.", "edit_memo"),
    ("What does a returned cheque usually tell a lender?", "knowledge"),
]
DEMO_NOTICE = (
    "Demo mode replays recorded answers for the suggested prompts. "
    "Connect the agent endpoint to ask anything."
)


def backend():
    from app.backend import main

    return main


def state(session):
    if "chat" not in session:
        session["chat"] = {
            "triage": {},
            "reading": set(),
            "history": [],
            "last_memo": {},
            "warmed_at": 0,
            "idle": asyncio.Event(),
        }
        session["chat"]["idle"].set()
    return session["chat"]


def connection():
    endpoint, key = backend().config("agent")
    return endpoint, key, bool(endpoint and key and os.getenv("DEMO_MODE") != "1")


class ReadRequest(BaseModel):
    page_ids: list[str] | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Message must contain text")
        return value


@router.get("/api/chat/suggestions")
def suggestions():
    return [
        {
            "text": text,
            "needs_documents": name != "knowledge",
            "needs_memo": name == "edit_memo",
        }
        for text, name in SUGGESTIONS
    ]


def lossless(data, cap):
    """The original bytes when within the pixel cap, else a resized PNG.

    Re-encoding small scans as JPEG blurred digits enough to wreck the reader
    (one sample page: 0 of 18 rows exact as JPEG, 15 of 18 as PNG).
    """
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height <= cap:
            return data
        output = io.BytesIO()
        resize_image(image, cap).save(output, format="PNG", optimize=True)
        return output.getvalue()


def images(pages, ids, budget=MAX_BODY_BYTES - 2048):
    cap = DEFAULT_MAX_PIXELS
    for p in ids:
        backend().validate_image(pages[p]["data"])
    result = [
        {
            "page_id": p,
            "image_base64": base64.b64encode(lossless(pages[p]["data"], cap)).decode(),
        }
        for p in ids
    ]
    if len(json.dumps(result).encode()) <= budget:
        return result
    while True:
        result = []
        for p in ids:
            with Image.open(io.BytesIO(pages[p]["data"])) as image:
                resized = resize_image(image, cap)
                output = io.BytesIO()
                resized.save(output, format="JPEG", quality=90)
            result.append(
                {
                    "page_id": p,
                    "image_base64": base64.b64encode(output.getvalue()).decode(),
                }
            )
        size = len(json.dumps(result).encode())
        if size <= budget:
            return result
        if cap <= 1000 or budget <= 0:
            raise HTTPException(400, "Input exceeds 9 MB; attach fewer pages")
        cap = max(1000, int(cap * min(0.8, budget / size * 0.8)))


async def cancel_job(endpoint, key, job):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.runpod.ai/v2/{endpoint}/cancel/{job}",
            headers={"Authorization": f"Bearer {key}"},
        )
        response.raise_for_status()


async def relay(endpoint, payload, key):
    job = None
    complete = False
    announced = False
    stopped = threading.Event()

    def remember(value):
        nonlocal job
        job = value
        if stopped.is_set():
            # Submission can finish after the client has disconnected.
            with httpx.Client(timeout=30) as client:
                client.post(
                    f"https://api.runpod.ai/v2/{endpoint}/cancel/{value}",
                    headers={"Authorization": f"Bearer {key}"},
                )

    chunks = backend().memo_chunks(
        endpoint, payload, key, on_job=remember, stop=stopped
    )
    sentinel = object()
    try:
        while True:
            waiting = asyncio.ensure_future(asyncio.to_thread(next, chunks, sentinel))
            # The job id is known long before the first chunk on a cold start: announce it
            # while waiting, so its logs can be followed during the queue/cold start.
            while not waiting.done():
                await asyncio.wait({waiting}, timeout=0.25)
                if job and not announced:
                    announced = True
                    yield {"type": "job", "job_id": job}
            chunk = waiting.result()
            if job and not announced:
                announced = True
                yield {"type": "job", "job_id": job}
            if chunk is sentinel:
                break
            if chunk.get("done"):
                complete = True
            yield chunk
    finally:
        stopped.set()
        try:
            chunks.close()
        except ValueError:
            pass
        if job and not complete:
            with anyio.CancelScope(shield=True):
                try:
                    await cancel_job(endpoint, key, job)
                except Exception:
                    pass


async def offline_read(pages, ids):
    for p in ids:
        truth = pages[p].get("truth")
        if not truth:
            yield {
                "type": "rows",
                "page_id": p,
                "valid": False,
                "validation_error": "Live reading is off; sample pages work offline",
            }
            continue
        for name in ("triage", "reader"):
            yield {"type": "step", "name": name, "status": "start", "page_id": p}
            await asyncio.sleep(0.15)
            yield {
                "type": "step",
                "name": name,
                "status": "done",
                "page_id": p,
                "ms": 150,
            }
            if name == "triage":
                yield {
                    "type": "triage",
                    "page_id": p,
                    "accepted": True,
                    "is_financial_document": True,
                    "readable": "yes",
                    "doc_type": truth["doc_type"],
                }
        document = DOCUMENT_ADAPTER.validate_python(truth)
        fixes = []
        if isinstance(document, Statement):
            document, fixes = check_direction(document)
        yield {
            "type": "rows",
            "page_id": p,
            "doc_type": truth["doc_type"],
            "document": document.model_dump(mode="json"),
            "valid": True,
            "balance_fixes": fixes,
        }
    yield {"type": "final", "intent": "read", "source": "recorded"}


def sample_business(pages):
    if set(pages) != {f"p{i}" for i in range(1, 6)}:
        return False
    root = backend().ROOT / "sample"
    names = json.loads((root / "manifest.json").read_text())
    return all(
        item.get("truth") == json.loads((root / f"{name}.json").read_text())
        for item, name in zip(pages.values(), names, strict=True)
    )


async def offline_chat(pages, message):
    name = next(
        (name for text, name in SUGGESTIONS if text.lower() == message.strip().lower()),
        None,
    )
    path = RECORDINGS / f"{name}.json"
    if not name or not sample_business(pages) or not path.exists():
        yield {
            "type": "final",
            "intent": "demo",
            "answer": DEMO_NOTICE,
            "source": "recorded",
        }
        return
    previous = 0
    replay_start = time.time()
    for raw in json.loads(path.read_text()):
        chunk = dict(raw)
        timestamp = float(chunk.pop("t", previous))
        chunk.setdefault("ts", replay_start + timestamp)
        await asyncio.sleep(min(1.5, max(0, timestamp - previous)))
        previous = timestamp
        if chunk.get("type") in ("final", "metrics"):
            chunk["source"] = "recorded"
        yield chunk


def update_page(session, chunk):
    p = chunk.get("page_id")
    if p not in session["pages"]:
        return
    item = session["pages"][p]
    if chunk["type"] == "triage":
        state(session)["triage"][p] = chunk
        if not chunk.get("accepted"):
            item.update(status="rejected", document=None)
    elif chunk["type"] == "rows":
        if chunk.get("valid") and chunk.get("document"):
            item.update(
                document=chunk["document"],
                status="done",
                direction_fixes=chunk.get("balance_fixes", []),
            )
            item.pop("error", None)
        else:
            item.update(
                status="error",
                document=None,
                error=str(chunk.get("validation_error", "Could not read page"))[:300],
            )


def release(session, ids, reason="Reading stopped"):
    chat = state(session)
    chat["reading"].difference_update(ids)
    for p in ids:
        if session["pages"][p]["status"] == "reading":
            session["pages"][p].update(status="error", error=reason[:300])
    if not chat["reading"]:
        chat["idle"].set()


def reserve(session, ids):
    chat = state(session)
    chat["reading"].update(ids)
    if ids:
        chat["idle"].clear()
    for p in ids:
        session["pages"][p]["status"] = "reading"


def bounded(payload):
    if len(json.dumps(payload).encode()) > MAX_BODY_BYTES:
        raise HTTPException(400, "Input exceeds 9 MB; attach fewer pages")
    return payload


async def stream(
    session,
    payload,
    source,
    endpoint,
    key,
    live,
    ids,
    message=None,
    session_id=None,
    turn_id=None,
    emit_turn=True,
):
    main = backend()
    turn_id = turn_id or secrets.token_hex(6)
    started = time.monotonic()
    job_id = None
    error = None
    finished = False
    worker_lines = 0
    poll_task = None
    stop = asyncio.Event()
    task = "read" if message is None else "agent"
    fields = dict(session=session_id, turn=turn_id, task=task)
    final = None
    failed = False
    times = {}
    read_ids = set(ids)
    events = (
        relay(endpoint, payload, key)
        if live
        else offline_read(session["pages"], ids)
        if message is None
        else offline_chat(session["pages"], message)
    )
    try:
        logs.LOG.info(
            f"read started: {len(ids)} pages"
            if message is None
            else f"agent turn started: {len(message)} chars, {len(payload.get('documents', []))} saved documents, {len(ids)} new pages",
            extra=fields,
        )
        if emit_turn:
            yield main.event("turn", {"turn_id": turn_id})
        async with aclosing(events):
            async for raw in events:
                chunk = main.clean(raw)
                if chunk.get("done"):
                    times.update(chunk)
                    continue
                if "error" in chunk and "type" not in chunk:
                    chunk = {"type": "error", "message": str(chunk["error"])}
                kind = chunk.get("type", "error")
                if kind == "job":
                    job_id = chunk.get("job_id")
                    fields["job_id"] = job_id
                    logs.LOG.info("runpod job submitted", extra=fields)
                    yield main.event("job", {"job_id": job_id})
                    if (
                        live
                        and job_id
                        and os.getenv("RUNPOD_WORKER_LOGS", "1") != "0"
                        and poll_task is None
                    ):
                        poll_task = asyncio.create_task(
                            logs.poll_worker_logs(
                                endpoint,
                                key,
                                session=session_id,
                                turn=turn_id,
                                job_id=job_id,
                                stop=stop,
                            )
                        )
                    continue
                if kind == "log":
                    for entry in chunk.get("entries", []):
                        if not isinstance(entry, dict):
                            continue
                        if worker_lines < 500:
                            origin = entry.get("source")
                            prefix = (
                                f"[{entry['section']}] " if "section" in entry else ""
                            )
                            logged = logs.BUFFER.add(
                                origin if origin in ("worker", "vllm") else "worker",
                                entry.get("level", "info"),
                                prefix + str(entry.get("message", "")),
                                page_id=entry.get("page_id"),
                                **fields,
                            )
                            # Keep worker time so trace spans align even after transport delay.
                            stamp = entry.get("ts")
                            if isinstance(stamp, (int, float)) and math.isfinite(stamp):
                                with logs.BUFFER.lock:
                                    for line in reversed(logs.BUFFER.entries):
                                        if line["id"] == logged["id"]:
                                            line["ts"] = stamp
                                            break
                        elif worker_lines == 500:
                            logs.LOG.warning(
                                "worker log lines dropped after 500", extra=fields
                            )
                        worker_lines += 1
                    continue
                if kind == "error":
                    error = str(chunk.get("message", "Unknown worker error"))[:300]
                    logs.LOG.error(error, extra=fields)
                failed |= kind == "error"
                if kind == "metrics":
                    continue
                if kind == "final":
                    final = chunk
                if kind in ("rows", "triage"):
                    update_page(session, chunk)
                    if kind == "rows" and chunk.get("page_id") in session["pages"]:
                        read_ids.add(chunk["page_id"])
                yield main.event(kind, chunk)
        if final and not failed:
            measured = main.metrics(times, final, "agent", source)
            measured["total_s"] = final.get("timing", {}).get("total_s")
            measured.update(turn_id=turn_id, job_id=job_id if live else None)
            if message is not None:
                chat = state(session)
                memo = final.get("memo")
                text = final.get("answer") or "\n\n".join(
                    (memo or {}).get(section["id"], "") for section in SECTIONS
                )
                chat["history"] = (
                    chat["history"]
                    + [
                        {"role": "user", "text": main.clean(message[:4000])},
                        {"role": "assistant", "text": text[:4000]},
                    ]
                )[-10:]
                if memo is not None:
                    chat["last_memo"] = {
                        section["id"]: memo[section["id"]][:4000]
                        for section in SECTIONS
                        if isinstance(memo.get(section["id"]), str)
                    }
            session["requests"].append(
                main.clean({"task": task, "intent": final.get("intent"), **measured})
            )
            for p in read_ids:
                if session["pages"][p].get("document"):
                    session["pages"][p]["metrics"] = {
                        **measured,
                        "source": "agent-read",
                    }
            finished = True
            yield main.event("metrics", measured)
    except Exception as exc:
        failed = True
        error = main.clean(str(exc))[:300]
        logs.LOG.error(error, extra=fields)
        yield main.event("error", {"message": error})
    finally:
        stop.set()
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        label = "read" if message is None else "agent turn"
        if failed:
            logs.LOG.error(f"{label} failed: {error}", extra=fields)
        elif finished:
            total = final.get("timing", {}).get("total_s")
            if total is None:
                total = time.monotonic() - started
            detail = (
                f"{len(read_ids)} pages"
                if message is None
                else f"intent {final.get('intent', 'unknown')}"
            )
            line = f"{label} done: {detail}, {total:.1f} s total"
            for field, name in (("delayTime", "queue"), ("executionTime", "execution")):
                if times.get(field) is not None:
                    line += f", {name} {times[field] / 1000:.1f} s"
            if not live:
                line += ", recorded run"
            logs.LOG.info(line, extra=fields)
        else:
            logs.LOG.warning(f"{label} stopped before it finished", extra=fields)
        # Pages left unread show the worker's own reason, not a generic one.
        release(session, ids, error or "Reading stopped")


@router.post("/api/sessions/{s}/read")
async def read(s: str, request: ReadRequest):
    session = backend().session(s)
    chat = state(session)
    pages = session["pages"]
    ids = request.page_ids
    if ids is None:
        ids = [
            p
            for p, item in pages.items()
            if not item.get("document")
            and item["status"] != "rejected"
            and p not in chat["reading"]
        ]
    if len(ids) > 8:
        raise HTTPException(400, "Read at most 8 pages at once")
    if any(p not in pages for p in ids):
        raise HTTPException(404, "Page not found")
    ids = list(dict.fromkeys(p for p in ids if p not in chat["reading"]))
    endpoint, key, live = connection()
    reserve(session, ids)
    try:
        payload = bounded(
            {
                "task": "read",
                "pages": await run_in_threadpool(images, pages, ids) if live else [],
            }
        )
    except BaseException:
        release(session, ids)
        raise
    return StreamingResponse(
        stream(
            session,
            payload,
            "runpod" if live else "recorded",
            endpoint,
            key,
            live,
            ids,
            session_id=s,
        ),
        media_type="text/event-stream",
    )


@router.post("/api/sessions/{s}/chat")
async def chat(s: str, request: ChatRequest):
    session = backend().session(s)
    current = state(session)
    endpoint, key, live = connection()

    async def generate():
        ids = []
        turn_id = secrets.token_hex(6)
        delegated = False
        error = None
        fields = dict(session=s, turn=turn_id, task="agent")
        try:
            yield backend().event("turn", {"turn_id": turn_id})
            if current["reading"]:
                yield backend().event(
                    "notice",
                    {"message": "Waiting for attached pages to finish reading."},
                )
                await asyncio.wait_for(current["idle"].wait(), 120)
            pages = session["pages"]
            cached = sorted(
                [
                    (p, item["document"])
                    for p, item in pages.items()
                    if item.get("document")
                ],
                key=lambda pair: pair[1]["doc_type"] != "statement",
            )
            unread = [
                p
                for p, item in pages.items()
                if not item.get("document")
                and item["status"] != "rejected"
                and p not in current["reading"]
            ]
            documents = [
                {"page_id": p, "doc_type": d["doc_type"], "document": d}
                for p, d in cached[:8]
                if len(d.get("transactions", d.get("line_items", []))) <= 500
            ]
            ids = unread[: 8 - len(documents)]
            if live:
                reserve(session, ids)
            else:
                ids = []
            if len(cached) + len(unread) > 8:
                yield backend().event(
                    "notice",
                    {"message": "Only the first 8 pages are included in this turn."},
                )
            payload = {
                "task": "agent",
                "message": request.message,
                "documents": documents,
                "new_pages": [],
                "history": current["history"][-10:],
                "last_memo": current["last_memo"],
            }
            if live:
                budget = MAX_BODY_BYTES - len(json.dumps(payload).encode()) - 2048
                payload["new_pages"] = await run_in_threadpool(
                    images, pages, ids, budget
                )
            bounded(payload)
            delegated = True
            async with aclosing(
                stream(
                    session,
                    payload,
                    "runpod" if live else "recorded",
                    endpoint,
                    key,
                    live,
                    ids,
                    request.message,
                    session_id=s,
                    turn_id=turn_id,
                    emit_turn=False,
                )
            ) as events:
                async for chunk in events:
                    yield chunk
        except TimeoutError:
            error = "Pages are still reading; try again shortly"
            logs.LOG.error(error, extra=fields)
            yield backend().event("error", {"message": error})
        except Exception as exc:
            error = backend().clean(str(exc))[:300]
            logs.LOG.error(error, extra=fields)
            yield backend().event("error", {"message": error})
        finally:
            if not delegated:
                if error:
                    logs.LOG.error(f"agent turn failed: {error}", extra=fields)
                else:
                    logs.LOG.warning(
                        "agent turn stopped before it finished", extra=fields
                    )
            release(session, ids)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def submit_health(endpoint, key):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.runpod.ai/v2/{endpoint}/run",
            headers={"Authorization": f"Bearer {key}"},
            json={"input": {"task": "health"}},
        )
        response.raise_for_status()


@router.post("/api/sessions/{s}/warm")
async def warm(s: str):
    current = state(backend().session(s))
    endpoint, key, live = connection()
    if not live:
        return {"warming": False, "reason": "offline"}
    now = time.monotonic()
    if current["warmed_at"] and now - current["warmed_at"] < 60:
        return {"warming": False, "reason": "recently warmed"}
    current["warmed_at"] = now
    try:
        await submit_health(endpoint, key)
    except Exception:
        current["warmed_at"] = 0
        return {"warming": False, "reason": "unavailable"}
    return {"warming": True}
