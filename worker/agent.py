"""Concurrent document readers and cited chat orchestration."""

import asyncio
import base64
import json
import os
import re
import time
from decimal import Decimal

import httpx
from pydantic import ValidationError

from ledgerlens.agent.checker import canonical_heading, check_text, word_count
from ledgerlens.agent.findings import describe_finding
from ledgerlens.agent.limits import (
    MAX_BODY_BYTES,
    MAX_TOOL_CALLS_PER_ROUND,
    MAX_TOOL_ROUNDS,
    validate_agent_input,
)
from ledgerlens.agent.prompts import (
    DATA_AGENT_SYSTEM,
    KNOWLEDGE_DOCUMENTS_SYSTEM,
    KNOWLEDGE_SYSTEM,
    REPLIES,
    ROUTER_SCHEMA,
    ROUTER_SYSTEM,
    SECTIONS,
    TRIAGE_SCHEMA,
    TRIAGE_SYSTEM,
    WORD_LIMITS,
    section_messages,
)
from ledgerlens.agent.report import build_report
from ledgerlens.agent.tools import TOOL_SPECS, ToolContext, run_tool
from ledgerlens.analytics import analyze
from ledgerlens.direction import check_direction
from ledgerlens.schema import json_schema
from worker.handler import (
    BASE_URL,
    MAX_PIXELS,
    MODELS,
    STREAM_FLUSH_CHARS,
    STREAM_FLUSH_SECONDS,
    VLLM_LISTENERS,
    VLLM_LISTENERS_LOCK,
    gpu_name,
    prepare_image,
    validate_image,
)
from worker.prompts import EXTRACT_SYSTEM

BASE_MODEL = os.getenv("BASE_MODEL", "ledgerlens")
READER_MODEL = os.getenv("READER_MODEL", "ledgerlens")
MEMO_MODEL = os.getenv("MEMO_MODEL", BASE_MODEL)
TRIAGE_MAX_PIXELS = int(os.getenv("TRIAGE_MAX_PIXELS", "500000"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "4"))


class Engine:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(
            base_url=BASE_URL + "/v1/", timeout=600, trust_env=False
        )
        self.log = None
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    def record(self, response):
        for key in self.usage:
            self.usage[key] += (response.get("usage") or {}).get(key, 0)

    async def log_error(self, kind, label, model, exc):
        if self.log is None:
            return
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f"HTTP {exc.response.status_code} {exc.response.text[:300]}"
        else:
            detail = f"{type(exc).__name__}: {str(exc)[:300]}"
        await self.log(
            "error", f"model {kind} {label} on {model} failed: {detail}", model=model
        )

    async def complete(self, payload):
        label, model = call_label(payload), payload.get("model", "unknown")
        started = time.monotonic()
        try:
            response = await self.client.post("chat/completions", json=payload)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPError as exc:
            await self.log_error("call", label, model, exc)
            raise
        self.record(result)
        if self.log is not None:
            ms = (time.monotonic() - started) * 1000
            usage = result.get("usage") or {}
            await self.log(
                "info",
                f"model call {label} on {model}: {ms:.0f} ms, "
                f"{usage.get('prompt_tokens', 0)} tokens in / {usage.get('completion_tokens', 0)} out",
                ms=ms,
                model=model,
            )
        return result

    async def stream(self, payload):
        label, model = call_label(payload), payload.get("model", "unknown")
        started, chars = time.monotonic(), 0
        payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        try:
            async with self.client.stream(
                "POST", "chat/completions", json=payload
            ) as response:
                if not response.is_success:
                    await response.aread()
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    result = json.loads(data)
                    self.record(result)
                    for choice in result.get("choices", []):
                        if text := choice.get("delta", {}).get("content"):
                            chars += len(text)
                            yield text
        except httpx.HTTPError as exc:
            await self.log_error("stream", label, model, exc)
            raise
        if self.log is not None:
            ms = (time.monotonic() - started) * 1000
            await self.log(
                "info",
                f"model stream {label} on {model}: {ms:.0f} ms, {chars} chars",
                ms=ms,
                model=model,
            )


def call_label(payload):
    return (payload.get("response_format") or {}).get("json_schema", {}).get(
        "name"
    ) or ("data_agent" if "tools" in payload else "chat")


async def emit_log(emit, level, message, **fields):
    await emit(
        {
            "type": "_log",
            "level": level,
            "source": "worker",
            "message": message,
            **fields,
        }
    )


def payload(
    system, content, *, model=BASE_MODEL, max_tokens=400, schema=None, name="route"
):
    result = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    if schema is not None:
        result["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema},
        }
    return result


def message(response):
    return response["choices"][0]["message"]


async def together(*coroutines):
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def read_pages(engine, pages, emit, *, triage_only=False):
    async def read(page):
        page_id = page["page_id"]
        validate_image(page["image_base64"])
        raw = base64.b64decode(page["image_base64"], validate=True)

        async def call(name, system, text, cap, model, schema, tokens):
            started = time.monotonic()
            await emit(
                {"type": "step", "name": name, "status": "start", "page_id": page_id}
            )
            try:
                image = await asyncio.to_thread(prepare_image, raw, cap)
                content = [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image}"},
                    },
                ]
                response = await engine.complete(
                    payload(
                        system,
                        content,
                        model=model,
                        schema=schema,
                        name=name if name == "triage" else doc_type,
                        max_tokens=tokens,
                    )
                )
            except Exception:
                await emit(
                    {
                        "type": "step",
                        "name": name,
                        "status": "error",
                        "page_id": page_id,
                    }
                )
                raise
            await emit(
                {
                    "type": "step",
                    "name": name,
                    "status": "done",
                    "page_id": page_id,
                    "ms": (time.monotonic() - started) * 1000,
                }
            )
            return message(response)["content"]

        doc_type = "other"
        triage = json.loads(
            await call(
                "triage",
                TRIAGE_SYSTEM,
                "Classify this page.",
                TRIAGE_MAX_PIXELS,
                BASE_MODEL,
                TRIAGE_SCHEMA,
                100,
            )
        )
        doc_type = triage["doc_type"]
        accepted = (
            triage["is_financial_document"]
            and doc_type != "other"
            and triage["readable"] != "no"
        )
        await emit(
            {"type": "triage", "page_id": page_id, **triage, "accepted": accepted}
        )
        if triage_only:
            return {"page_id": page_id, **triage, "accepted": accepted}
        if not accepted:
            await emit_log(
                emit, "info", f"{page_id} triage: {doc_type}, rejected", page_id=page_id
            )
            return None
        raw_document = await call(
            "reader",
            EXTRACT_SYSTEM,
            f"Transcribe this {doc_type}.",
            MAX_PIXELS,
            READER_MODEL,
            json_schema(doc_type),
            4096,
        )
        event = {
            "type": "rows",
            "page_id": page_id,
            "doc_type": doc_type,
            "document": None,
            "valid": False,
            "balance_fixes": [],
        }
        try:
            document = MODELS[doc_type].model_validate_json(raw_document)
            if doc_type == "statement":
                document, event["balance_fixes"] = check_direction(document)
            event.update(document=document.model_dump(mode="json"), valid=True)
        except ValidationError as exc:
            errors = exc.errors()
            first = errors[0]
            await emit_log(
                emit,
                "warning",
                f"{page_id} reader output failed validation: {len(errors)} errors, first at {first['loc']} ({first['type']})",
                page_id=page_id,
            )
            event["validation_error"] = str(exc)
        await emit(event)
        return (
            {key: event[key] for key in ("page_id", "doc_type", "document")}
            if event["valid"]
            else None
        )

    async def safe_read(page):
        # One unreadable page must not sink the others; it is reported and left out.
        try:
            return await read(page)
        except Exception as exc:
            await emit_log(
                emit,
                "error",
                f"{page['page_id']} could not be read: {type(exc).__name__}: {str(exc)[:300]}",
                page_id=page["page_id"],
            )
            await emit(
                {
                    "type": "rows",
                    "page_id": page["page_id"],
                    "doc_type": None,
                    "document": None,
                    "valid": False,
                    "validation_error": f"Could not read page: {exc}",
                    "balance_fixes": [],
                }
            )
            return None

    return [
        page for page in await together(*(safe_read(page) for page in pages)) if page
    ]


def validate(data, read=False):
    if len(json.dumps(data).encode()) > MAX_BODY_BYTES:
        raise ValueError("Input exceeds 9 MB")
    allowed = "triage" if read else "route"
    if "stop_after" in data and data["stop_after"] != allowed:
        raise ValueError(f"stop_after must be {allowed}")
    clean = validate_agent_input(
        {"message": "Read pages", "new_pages": data.get("pages", [])} if read else data
    )
    if "stop_after" in data:
        clean["stop_after"] = data["stop_after"]
    return clean


def cited(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        if "page_id" in value and "row_index" in value:
            return f"[{value['page_id']}:r{value['row_index'] + 1}]"
        return {key: cited(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cited(item) for item in value]
    return value


async def events(work, engine):
    queue = asyncio.Queue()
    timing = {}
    started = time.monotonic()

    def stamped(event):
        event["ts"] = time.time()
        return event

    async def emit(event):
        await queue.put(stamped(event))

    async def log(level, message, source="worker", **fields):
        await emit(
            {
                "type": "_log",
                "level": level,
                "source": source,
                "message": message,
                **fields,
            }
        )

    previous_log = getattr(engine, "log", None)
    setattr(engine, "log", log)
    loop = asyncio.get_running_loop()
    line_count = 0

    def listener(line):
        nonlocal line_count
        if line_count >= 50 or not any(
            word in line for word in ("WARNING", "ERROR", "Traceback")
        ):
            return
        line_count += 1
        item = {
            "type": "_log",
            "level": "error" if "ERROR" in line or "Traceback" in line else "warning",
            "source": "vllm",
            "message": line[:2000],
        }
        loop.call_soon_threadsafe(queue.put_nowait, stamped(item))

    with VLLM_LISTENERS_LOCK:
        VLLM_LISTENERS.add(listener)

    async def produce():
        try:
            await work(emit)
        except Exception as exc:
            await log("error", f"{type(exc).__name__}: {str(exc)[:500]}")
            await emit({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    pending = {}
    logs = []
    chars = 0
    deadline = None

    def flush():
        nonlocal chars, deadline
        event = {
            "type": "deltas",
            "parts": [{"part": part, "text": text} for part, text in pending.items()],
        }
        pending.clear()
        chars = 0
        deadline = None
        return stamped(event)

    def flush_logs():
        nonlocal deadline
        event = {"type": "log", "entries": list(logs)}
        logs.clear()
        if not pending:
            deadline = None
        return stamped(event)

    try:
        while True:
            try:
                event = (
                    await asyncio.wait_for(
                        queue.get(), max(0, deadline - time.monotonic())
                    )
                    if deadline is not None
                    else await queue.get()
                )
            except TimeoutError:
                if pending:
                    yield flush()
                if logs:
                    yield flush_logs()
                continue
            if event is not None and event["type"] == "_log":
                logs.append(
                    {
                        key: value
                        for key, value in event.items()
                        if key
                        in (
                            "ts",
                            "level",
                            "source",
                            "message",
                            "page_id",
                            "section",
                            "ms",
                            "model",
                        )
                    }
                )
                if deadline is None:
                    deadline = time.monotonic() + STREAM_FLUSH_SECONDS
                if len(logs) >= 20:
                    yield flush_logs()
                continue
            if event is not None and event["type"] == "_text":
                text = event["text"]
                if not text:
                    continue
                pending[event["part"]] = pending.get(event["part"], "") + text
                chars += len(text)
                if deadline is None:
                    deadline = time.monotonic() + STREAM_FLUSH_SECONDS
                if chars >= STREAM_FLUSH_CHARS:
                    yield flush()
                    if logs:
                        yield flush_logs()
                continue
            if pending:
                yield flush()
                if logs:
                    yield flush_logs()
            if logs and (event is None or event["type"] in ("final", "error")):
                yield flush_logs()
            if event is None:
                break
            if event["type"] == "step" and "ms" in event:
                key = ":".join(
                    str(event[k]) for k in ("name", "page_id", "section") if k in event
                )
                timing[key] = timing.get(key, 0) + event["ms"] / 1000
            if event["type"] == "final":
                event.update(
                    usage=dict(engine.usage),
                    timing={"total_s": time.monotonic() - started, "per_step": timing},
                    gpu=await asyncio.to_thread(gpu_name),
                    models={
                        "base": BASE_MODEL,
                        "reader": READER_MODEL,
                        "memo": MEMO_MODEL,
                    },
                )
            yield event
    finally:
        with VLLM_LISTENERS_LOCK:
            VLLM_LISTENERS.discard(listener)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        setattr(engine, "log", previous_log)


async def run_read(data, engine):
    async def work(emit):
        clean = validate(data, read=True)
        triage_only = clean.get("stop_after") == "triage"
        documents = await read_pages(
            engine, clean["new_pages"], emit, triage_only=triage_only
        )
        await emit(
            {
                "type": "final",
                "intent": "read",
                "documents": [] if triage_only else documents,
                "problems": [],
                **({"triage": documents} if triage_only else {}),
            }
        )

    async for event in events(work, engine):
        yield event


async def run_agent(data, engine):
    async def work(emit):
        clean = validate(data)
        timings = {}

        async def step(name, operation, section=None):
            extra = {"section": section} if section else {}
            await emit({"type": "step", "name": name, "status": "start", **extra})
            started = time.monotonic()
            try:
                result = await operation()
            except Exception:
                await emit({"type": "step", "name": name, "status": "error", **extra})
                raise
            timings[name] = time.monotonic() - started
            await emit(
                {
                    "type": "step",
                    "name": name,
                    "status": "done",
                    "ms": timings[name] * 1000,
                    **extra,
                }
            )
            return result

        async def router():
            history = "\n".join(
                f"{turn['role']}: {turn['text'][:300]}"
                for turn in clean["history"][-4:]
            )
            context = (
                f"A memo has been written: {'yes' if clean.get('last_memo') else 'no'}."
                + (f"\nRecent turns:\n{history}" if history else "")
            )
            response = await engine.complete(
                payload(
                    ROUTER_SYSTEM,
                    f"{context}\n\nUser message: {clean['message']}",
                    schema=ROUTER_SCHEMA,
                    max_tokens=600,
                )
            )
            return json.loads(message(response)["content"])

        async def prepare():
            documents = clean["documents"] + await read_pages(
                engine, clean["new_pages"], emit
            )
            statements = [
                MODELS["statement"].model_validate(p["document"])
                for p in documents
                if p["doc_type"] == "statement"
            ]
            ids = [p["page_id"] for p in documents if p["doc_type"] == "statement"]

            async def analyst():
                analysis = await asyncio.to_thread(analyze, statements, ids)
                # The writer copied the raw share (28 digits, then "0.2006") into memos;
                # it gets the percentage it should write instead.
                concentration = analysis["top_payer_concentration"]
                concentration["share_percent"] = round(
                    concentration.pop("share") * 100, 2
                )
                pages = {p["page_id"]: p for p in documents}
                for finding in analysis["findings"]:
                    finding["description"] = describe_finding(
                        {
                            **finding,
                            "citations": [
                                c for c in finding["citations"] if c["row_index"] >= 0
                            ],
                        },
                        pages,
                        statements[0].currency if statements else "",
                    )
                return cited(analysis)

            analysis = await step("analyst", analyst)
            return statements, ids, analysis, documents

        if clean.get("stop_after") == "route":
            route = await step("router", router)
            statements = [p for p in clean["documents"] if p["doc_type"] == "statement"]
        else:
            route, (statements, ids, analysis, documents) = await together(
                step("router", router), prepare()
            )
        intent = route["intent"]
        original = intent
        if intent in ("memo", "ask_data", "edit_memo") and not statements:
            intent = "needs_documents"
        elif intent == "edit_memo" and not clean.get("last_memo"):
            intent = "memo"
        await emit_log(
            emit,
            "info",
            f"route: {intent}" + (f" (from {original})" if original != intent else ""),
        )
        plan = route["plan"]
        await emit(
            {
                "type": "route",
                "intent": intent,
                "plan": plan,
                "reason": route["reason"],
                **({"overridden_from": original} if original != intent else {}),
            }
        )
        final = {"type": "final", "intent": intent, "problems": []}
        if clean.get("stop_after") == "route":
            await emit({**final, "plan": plan, "reason": route["reason"]})
            return
        row_counts = {
            page: len(statement.transactions)
            for page, statement in zip(ids, statements, strict=True)
        }

        async def stream(request, part):
            text = ""
            async for delta in engine.stream(request):
                text += delta
                await emit({"type": "_text", "part": part, "text": delta})
            return text

        async def check(text, subset, section=None, **kwargs):
            async def operation():
                return check_text(text, subset, row_counts, **kwargs)

            return await step("checker", operation, section)

        if intent in REPLIES:
            final["answer"] = REPLIES[intent]
        elif intent == "knowledge":
            final["answer"] = await step(
                "writer",
                lambda: stream(
                    payload(
                        KNOWLEDGE_DOCUMENTS_SYSTEM if documents else KNOWLEDGE_SYSTEM,
                        clean["message"],
                    ),
                    "answer",
                ),
            )
            final["problems"] = await check(final["answer"], {})
        elif intent in ("memo", "edit_memo"):
            report = build_report(
                statements,
                [p for p in documents if p["doc_type"] == "invoice"],
                [p["page_id"] for p in documents],
                analysis,
                plan,
            )
            await emit(report)
            final["report"] = report
            selected = (
                route.get("edit_sections") if intent == "edit_memo" else None
            ) or [s["id"] for s in SECTIONS]
            memo = dict(clean.get("last_memo", {})) if intent == "edit_memo" else {}

            async def write(section):
                section_id = section["id"]
                subset = {
                    "currency": statements[0].currency,
                    **{key: analysis[key] for key in section["analysis_keys"]},
                }
                previous = memo.get(section_id) if intent == "edit_memo" else None
                problems = [clean["message"]] if intent == "edit_memo" else None
                max_words = check_max_words = WORD_LIMITS[plan["length"]]
                if previous and re.search(
                    r"\b(shorter|shorten|concise|brief|trim|cut|singkat|pendek|ringkas)\b|简短|缩短|精简|短一些|更短",
                    clean["message"],
                    re.IGNORECASE,
                ):
                    max_words = max(25, word_count(previous) * 3 // 5)
                    check_max_words = max(25, (word_count(previous) * 4 + 4) // 5)
                request = {
                    "model": MEMO_MODEL,
                    "temperature": 0,
                    "max_tokens": 1500,
                    "messages": section_messages(
                        section, plan, subset, previous, problems, max_words=max_words
                    ),
                }
                text = await step(
                    "writer", lambda: stream(request, section_id), section_id
                )
                kwargs = {
                    "max_words": check_max_words,
                    "required_heading": section["heading"],
                    "language": plan["language"],
                }
                problems = await check(text, subset, section_id, **kwargs)
                rewritten = bool(problems)
                if problems:
                    await emit_log(
                        emit,
                        "warning",
                        f"checker found {len(problems)} problems in {section_id}; rewriting once",
                        section=section_id,
                    )
                    await emit({"type": "section_reset", "section": section_id})
                    request["messages"] = section_messages(
                        section, plan, subset, text, problems, max_words=max_words
                    )
                    text = await step(
                        "writer", lambda: stream(request, section_id), section_id
                    )
                    problems = await check(text, subset, section_id, **kwargs)
                text = canonical_heading(text, section["heading"])
                memo[section_id] = text
                await emit(
                    {
                        "type": "section",
                        "section": section_id,
                        "text": text,
                        "problems": problems,
                        "rewritten": rewritten,
                    }
                )
                return problems

            results = await together(
                *(write(section) for section in SECTIONS if section["id"] in selected)
            )
            final.update(
                memo={s["id"]: memo[s["id"]] for s in SECTIONS if s["id"] in memo},
                problems=[p for result in results for p in result],
            )
        elif intent == "ask_data":
            context = ToolContext(statements, ids, statements[0].currency)
            request = payload(DATA_AGENT_SYSTEM, clean["message"])
            request["tools"] = TOOL_SPECS
            collected = []
            names = {}

            def mask_names(value, key=""):
                if isinstance(value, dict):
                    return {k: mask_names(v, k) for k, v in value.items()}
                if isinstance(value, list):
                    return [mask_names(v, key) for v in value]
                if (
                    isinstance(value, str)
                    and any(
                        part in key.lower()
                        for part in ("payer", "counterparty", "name", "description")
                    )
                    and any(ord(char) > 255 for char in value)
                ):
                    if value not in names:
                        names[value] = f"NAME_{len(names) + 1}"
                    return names[value]
                return value

            def restore_names(text):
                originals = {token: original for original, token in names.items()}
                return re.sub(
                    r"\bNAME_\d+\b", lambda m: originals.get(m[0], m[0]), text
                )

            async def data_agent():
                for _ in range(MAX_TOOL_ROUNDS):
                    answer = message(await engine.complete(request))
                    calls = answer.get("tool_calls") or []
                    if not calls:
                        return answer.get("content") or ""
                    request["messages"].append(
                        {
                            "role": "assistant",
                            "content": answer.get("content"),
                            "tool_calls": calls,
                        }
                    )
                    results = await together(
                        *(
                            asyncio.to_thread(
                                run_tool,
                                context,
                                call["function"]["name"],
                                call["function"]["arguments"],
                            )
                            for call in calls[:MAX_TOOL_CALLS_PER_ROUND]
                        )
                    )
                    results += [{"error": "Too many tool calls in one round"}] * len(
                        calls[MAX_TOOL_CALLS_PER_ROUND:]
                    )
                    for call, result in zip(calls, results, strict=True):
                        function = call["function"]
                        if "error" in result:
                            await emit_log(
                                emit,
                                "warning",
                                f"tool {function['name']} error: {str(result['error'])[:300]}",
                            )
                        collected.append(result)
                        await emit(
                            {
                                "type": "tool",
                                "name": function["name"],
                                "arguments": function["arguments"],
                                "result": result,
                            }
                        )
                        request["messages"].append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps(mask_names(result)),
                            }
                        )
                await emit_log(
                    emit,
                    "info",
                    f"tool budget used up after {MAX_TOOL_ROUNDS} rounds; answering without tools",
                )
                request["tool_choice"] = "none"
                return message(await engine.complete(request)).get("content") or ""

            model_text = await step("data_agent", data_agent)
            text = restore_names(model_text)
            await emit({"type": "_text", "part": "answer", "text": text})
            problems = await check(text, collected, question=clean["message"])
            if problems:
                await emit_log(
                    emit,
                    "warning",
                    f"checker found {len(problems)} problems in answer; rewriting once",
                    section="answer",
                )
                request["tool_choice"] = "none"
                request["messages"].extend(
                    [
                        {"role": "assistant", "content": model_text},
                        {
                            "role": "user",
                            "content": "Your answer had these problems: "
                            + "; ".join(f"{p['kind']}: {p['detail']}" for p in problems)
                            + ". Answer the user's question again using only the tool results; "
                            "copy names and figures exactly. Do not mention these problems.",
                        },
                    ]
                )
                await emit({"type": "section_reset", "section": "answer"})
                text = restore_names(
                    await step("data_agent", lambda: stream(request, "answer"))
                )
                await emit({"type": "section_reset", "section": "answer"})
                await emit({"type": "_text", "part": "answer", "text": text})
                problems = await check(text, collected, question=clean["message"])
            final.update(answer=text, problems=problems)
        else:
            raise ValueError("Unknown router intent")
        await emit(final)

    async for event in events(work, engine):
        yield event
