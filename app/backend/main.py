"""Session-scoped demo API. Credentials remain on the server."""

import asyncio
import base64
import io
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app.backend.datasets import router as datasets_router
from app.backend.insights import router as insights_router
from app.backend.training import router as training_router
from app.backend.training import shutdown_runs
from ledgerlens.analytics import analyze, payer, payer_key
from ledgerlens.datagen.layouts import LAYOUTS
from ledgerlens.direction import check_direction
from ledgerlens.eval.score import score_document
from ledgerlens.schema import DOCUMENT_ADAPTER, Statement
from worker import client as worker_client

ROOT = Path(__file__).resolve().parents[1]
SESSIONS: dict = {}
LOCK = threading.Lock()
STATUS_LOCK = threading.Lock()
STATUS: dict = {"time": 0, "value": {}}
MAX_BYTES = 15 * 1024 * 1024


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        shutdown_runs()


app = FastAPI(title="LedgerLens", lifespan=lifespan)
app.include_router(insights_router)
app.include_router(datasets_router)
app.include_router(training_router)


def config(model):
    endpoint = os.getenv("RUNPOD_ENDPOINT_" + model.upper())
    key = os.getenv("RUNPOD_API_KEY") or worker_client.runpodctl_key()
    return endpoint, key


def clean(value):
    """Redact even a credential echoed by a remote worker."""
    key = os.getenv("RUNPOD_API_KEY") or worker_client.runpodctl_key()
    encoded = json.dumps(jsonable_encoder(value))
    return json.loads(encoded.replace(key, "[REDACTED]") if key else encoded)


def session(s):
    if s not in SESSIONS:
        raise HTTPException(404, "Session not found")
    return SESSIONS[s]


def page(s, p):
    pages = session(s)["pages"]
    if p not in pages:
        raise HTTPException(404, "Page not found")
    return pages[p]


def base_url():
    override = os.getenv("PUBLIC_BASE_URL")
    if override:
        return override.rstrip("/")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
    except OSError:
        address = "127.0.0.1"
    return f"http://{address}:{os.getenv('PORT', '8090')}"


@app.post("/api/sessions")
def create_session():
    s = secrets.token_hex(16)
    SESSIONS[s] = {"pages": {}, "requests": []}
    return {
        "session": s,
        "upload_url_for_phone": f"{base_url()}/m/{s}",
        "qr_png_url": f"/api/sessions/{s}/qr.png",
    }


@app.get("/api/sessions/{s}/qr.png")
def qr(s: str):
    session(s)
    import qrcode

    output = io.BytesIO()
    qrcode.make(f"{base_url()}/m/{s}").save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png")


def validate_image(data):
    mime = None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    if not mime:
        raise HTTPException(415, "Use PNG, JPEG or WEBP. HEIC is not supported.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 60_000_000:
                raise HTTPException(
                    413, "Image exceeds 60,000,000 pixels; resize it before uploading"
                )
            image.verify()
    except HTTPException:
        raise
    except Image.DecompressionBombError:
        raise HTTPException(
            413, "Image exceeds 60,000,000 pixels; resize it before uploading"
        ) from None
    except Exception:
        raise HTTPException(415, "Invalid image") from None
    return mime


def add_page(pages, data, mime, truth=None):
    p = f"p{len(pages) + 1}"
    pages[p] = {
        "id": p,
        "data": data,
        "mime": mime,
        "status": "uploaded",
        "document": None,
        "metrics": None,
        "edited": [],
        "direction_fixes": [],
        "truth": truth,
    }
    return p


@app.post("/api/sessions/{s}/pages")
async def upload(s: str, files: list[UploadFile] = File(...)):
    pages = session(s)["pages"]
    pending = []
    for file in files:
        try:
            data = await file.read(MAX_BYTES + 1)
        finally:
            await file.close()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Each page must be at most 15 MB")
        pending.append((data, validate_image(data)))
    with LOCK:
        if len(pages) + len(pending) > 30:
            raise HTTPException(400, "Maximum 30 pages per session")
        ids = [add_page(pages, data, mime) for data, mime in pending]
    return {"page_ids": ids}


@app.get("/api/sessions/{s}")
def get_session(s: str):
    return clean(
        {
            "pages": [
                {
                    **{
                        k: v
                        for k, v in item.items()
                        if k not in ("data", "mime", "truth")
                    },
                    "thumbnail_url": f"/api/sessions/{s}/pages/{p}/image",
                    "doc_type": item["truth"]["doc_type"] if item["truth"] else None,
                }
                for p, item in session(s)["pages"].items()
            ],
            "requests": session(s)["requests"],
        }
    )


@app.get("/api/sessions/{s}/pages/{p}/image")
def image(s: str, p: str):
    item = page(s, p)
    return Response(item["data"], media_type=item["mime"])


@app.post("/api/sessions/{s}/sample")
def sample(s: str):
    pages = session(s)["pages"]
    manifest = ROOT / "sample/manifest.json"
    if not manifest.exists():
        raise HTTPException(503, "Run uv run python app/make_sample.py first")
    pending = []
    for name in json.loads(manifest.read_text()):
        data = (ROOT / "sample" / f"{name}.png").read_bytes()
        truth = json.loads((ROOT / "sample" / f"{name}.json").read_text())
        metadata_path = ROOT / "sample" / f"{name}.meta.json"
        metadata = (
            json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        )
        pending.append((data, truth, metadata))
    with LOCK:
        if len(pages) + len(pending) > 30:
            raise HTTPException(400, "Maximum 30 pages per session")
        ids = []
        for data, truth, metadata in pending:
            identity = add_page(pages, data, "image/png", truth)
            layout = LAYOUTS.get(metadata.get("layout_id"))
            pages[identity]["date_format"] = layout.date_format if layout else None
            ids.append(identity)
    return {"page_ids": ids}


class Extract(BaseModel):
    model: Literal["8b", "ft", "4b"]
    doc_type: Literal["statement", "invoice"]


# Runpod Serverless flex rates per GPU tier, $/h as listed in the endpoint editor (9–11 Oct 2026):
# 32 GB PRO $1.58, 24 GB PRO $1.10, 24 GB $0.69. A worker reports its GPU, so each request is
# priced at the tier it actually ran on (the fine-tuned endpoint falls back from 5090 to 4090).
GPU_PRICE_PER_SEC = {
    "RTX 5090": 1.58 / 3600,
    "RTX PRO 4500": 1.58 / 3600,
    "RTX 4090": 1.10 / 3600,
    "RTX 3090": 0.69 / 3600,
}


def gpu_price(model, gpu):
    """Dollars per second: env override, else the reported GPU's tier, else 32 GB PRO."""
    if override := os.getenv("GPU_PRICE_PER_SEC_" + model.upper()):
        return float(override)
    for name, price in GPU_PRICE_PER_SEC.items():
        if gpu and name in gpu:
            return price
    return GPU_PRICE_PER_SEC["RTX 5090"]


def metrics(result, output, model, source="runpod"):
    seconds = float(result.get("executionTime") or 0) / 1000
    price = gpu_price(model, output.get("gpu"))
    return clean(
        {
            "source": source,
            "model": output.get("model", model),
            "gpu": output.get("gpu"),
            "delayTime": result.get("delayTime"),
            "executionTime": result.get("executionTime"),
            "timing": output.get("timing", {}),
            "usage": output.get("usage", {}),
            "cold_start": output.get("timing", {}).get("cold_start"),
            "cost": seconds * price if source == "runpod" else None,
            "cost_per_1000_pages": seconds * price * 1000
            if source == "runpod"
            else None,
        }
    )


def run_model(item, model, doc_type):
    endpoint, key = config(model)
    if os.getenv("DEMO_MODE") == "1" or not endpoint or not key:
        raise RuntimeError("offline")
    result = worker_client.call_endpoint(
        endpoint,
        {
            "task": "extract",
            "doc_type": doc_type,
            "image_base64": base64.b64encode(item["data"]).decode(),
        },
        key,
    )
    output = result["output"]
    if isinstance(output, list):
        output = output[-1]
    document = DOCUMENT_ADAPTER.validate_python(output["document"])
    if document.doc_type != doc_type:
        raise ValueError("Wrong document type")
    corrections = []
    if isinstance(document, Statement):
        document, corrections = check_direction(document)
    return document, metrics(result, output, model), corrections


class Compare(BaseModel):
    doc_type: Literal["statement", "invoice"]


@app.post("/api/sessions/{s}/pages/{p}/compare")
async def compare(s: str, p: str, request: Compare):
    item = page(s, p)

    async def call(model):
        endpoint, key = config(model)
        entry = {
            "status": "offline",
            "document": None,
            "metrics": None,
            "direction_fixes": [],
            "error": None,
        }
        if os.getenv("DEMO_MODE") == "1" or not endpoint or not key:
            if item["truth"]:
                entry.update(
                    document=item["truth"],
                    metrics=metrics({}, {}, model, "cached-demo"),
                    source="cached-demo",
                )
            return model, entry
        try:
            document, measured, corrections = await run_in_threadpool(
                run_model, item, model, request.doc_type
            )
            measured["direction_fixes"] = len(corrections)
            entry.update(
                status="ok",
                document=document.model_dump(mode="json"),
                metrics=measured,
                direction_fixes=corrections,
            )
            session(s)["requests"].append({"page_id": p, "task": "extract", **measured})
        except Exception as exc:
            entry.update(status="error", error=clean(f"{type(exc).__name__}: {exc}"))
            session(s)["requests"].append(
                {
                    "page_id": p,
                    "task": "extract",
                    "model": model,
                    "source": "runpod",
                    "error": entry["error"],
                }
            )
        return model, entry

    models = dict(await asyncio.gather(*(call(m) for m in ("8b", "ft", "4b"))))
    rows = []
    if item["truth"]:
        truth = item["truth"]
        field = "transactions" if truth["doc_type"] == "statement" else "line_items"
        rows = [
            {"gold_row": i, "models": {m: None for m in models}}
            for i in range(len(truth[field]))
        ]
        for model, entry in models.items():
            if entry["status"] != "ok":
                continue
            matches = {
                r["gold_row"]
                for r in score_document(entry["document"], truth)["rows"]["matches"]
            }
            for row in rows:
                row["models"][model] = row["gold_row"] in matches
    return clean(
        {"models": models, "truth_available": bool(item["truth"]), "rows": rows}
    )


@app.post("/api/sessions/{s}/pages/{p}/extract")
async def extract(s: str, p: str, request: Extract):
    item = page(s, p)
    if item["status"] == "extracting":
        raise HTTPException(409, "Extraction already running")
    item["status"] = "extracting"
    corrections = []
    endpoint, key = config(request.model)
    try:
        if os.getenv("DEMO_MODE") == "1" or not endpoint or not key:
            raise RuntimeError("offline")
        document, item["metrics"], corrections = await run_in_threadpool(
            run_model, item, request.model, request.doc_type
        )
    except Exception as exc:
        live_error = None if str(exc) == "offline" else f"{type(exc).__name__}: {exc}"
        if live_error:
            # Never fall back silently: a failed live call must not pass for a working one.
            live_error = clean(live_error)
            logging.warning("Live extraction of %s failed: %s", p, live_error)
        if item["truth"]:
            corrections = []
            document = DOCUMENT_ADAPTER.validate_python(item["truth"])
            item["metrics"] = metrics({}, {}, request.model, "cached-demo")
            item["metrics"]["live_error"] = live_error
        else:
            item.update(
                status="error",
                error="No endpoint is configured or the endpoint failed. Configure an endpoint and retry; sample pages work offline.",
            )
            raise HTTPException(503, item["error"]) from None
    item["direction_fixes"] = corrections
    item["metrics"]["direction_fixes"] = len(corrections)
    item.update(document=document.model_dump(mode="json"), status="done", edited=[])
    item.pop("error", None)
    session(s)["requests"].append({"page_id": p, "task": "extract", **item["metrics"]})
    return clean(
        {
            "document": item["document"],
            "metrics": item["metrics"],
            "direction_fixes": item["direction_fixes"],
        }
    )


class Edit(BaseModel):
    field: str
    value: str | int | float | None


@app.patch("/api/sessions/{s}/pages/{p}/rows/{i}")
def edit(s: str, p: str, i: int, request: Edit):
    item = page(s, p)
    if item["status"] == "extracting":
        raise HTTPException(409, "Wait for extraction to finish")
    document = item["document"]
    if not document:
        raise HTTPException(409, "Extract this page first")
    rows_key = "transactions" if document["doc_type"] == "statement" else "line_items"
    rows = document[rows_key]
    if i < 0 or i >= len(rows) or request.field not in rows[i]:
        raise HTTPException(422, "Unknown row or field")
    candidate = json.loads(json.dumps(document))
    candidate[rows_key][i][request.field] = request.value
    try:
        validated = DOCUMENT_ADAPTER.validate_python(candidate)
    except ValidationError:
        raise HTTPException(422, "Value does not match the document schema") from None
    item["document"] = validated.model_dump(mode="json")
    marker = f"{i}:{request.field}"
    if marker not in item["edited"]:
        item["edited"].append(marker)
    return clean({"document": item["document"], "edited": item["edited"]})


@app.get("/api/sessions/{s}/analysis")
def analysis(s: str):
    extracted = [
        (p, item["document"])
        for p, item in session(s)["pages"].items()
        if item["document"] and item["document"]["doc_type"] == "statement"
    ]
    try:
        computed = clean(
            analyze(
                [Statement.model_validate(d) for _, d in extracted],
                [p for p, _ in extracted],
            )
        )
        currency = extracted[0][1]["currency"] if extracted else "SGD"
        computed["currency"] = currency
        for finding in computed["findings"]:
            finding["description"] = describe_finding(
                finding, session(s)["pages"], currency
            )
        return computed
    except ValueError:
        raise HTTPException(422, "Analyze one currency at a time") from None


def amount(value, currency):
    """Format computed amounts consistently in explanations and the offline memo."""
    return f"{currency} {Decimal(str(value or 0)):,.{0 if currency == 'IDR' else 2}f}"


def describe_finding(finding, pages, currency):
    """Turn deterministic rules into evidence-specific explanations."""
    rows = [
        pages[c["page_id"]]["document"]["transactions"][c["row_index"]]
        for c in finding["citations"]
    ]
    row = rows[0] if rows else {}

    def when(row):
        return date.fromisoformat(row["date"]).strftime("%-d %b")

    kind = finding["type"]
    if kind == "returned_cheque":
        return f"Returned cheque · {amount(row.get('debit') or row.get('credit'), currency)} · {when(row)} · {row['description']}"
    if kind == "round_trip":
        name = next(
            (
                payer(transaction["description"])
                for page in pages.values()
                for transaction in (page.get("document") or {}).get("transactions", [])
                if payer_key(transaction["description"])
                == payer_key(row["description"])
            ),
            payer(row["description"]),
        )
        return f"Round trip · {amount(finding['amount'], currency)} out to {name} on {when(row)}, back on {when(rows[1])}"
    if kind == "large_cash_deposit":
        ratio = Decimal(finding["amount"]) / Decimal(finding["median_inflow"])
        return f"Large cash deposit · {amount(finding['amount'], currency)} · {when(row)} · {row['description']} · {ratio:.1f}× the median credit"
    if kind == "unreconciled_page":
        return f"Unreconciled page · printed closing balance off by {amount(abs(Decimal(finding['difference'])), currency)}"
    if kind == "customer_concentration":
        return f"Customer concentration · {finding['payer']} is {Decimal(finding['share']) * 100:.0f}% of inflows"
    return kind.replace("_", " ").capitalize()


def cached_memo(computed):
    """Write a concise offline memo from the same numbers used in the analysis."""
    currency = computed["currency"]
    monthly = computed["monthly"]
    inflow = sum((Decimal(v["inflow"]) for v in monthly.values()), Decimal(0))
    outflow = sum((Decimal(v["outflow"]) for v in monthly.values()), Decimal(0))
    concentration = computed["top_payer_concentration"]
    flags = []
    for finding in computed["findings"]:
        refs = " ".join(
            f"[{c['page_id']}:r{c['row_index'] + 1}]" for c in finding["citations"][:2]
        )
        flags.append(f"• {finding['description']} {refs}.")
    questions = []
    kinds = {f["type"] for f in computed["findings"]}
    for kind, question in (
        (
            "returned_cheque",
            "What caused the returned cheque, and has it been settled?",
        ),
        (
            "round_trip",
            "What was the business purpose of the transfer that returned shortly afterwards?",
        ),
        (
            "large_cash_deposit",
            "Can you provide receipts and explain the source of the large cash deposit?",
        ),
        (
            "unreconciled_page",
            "Can you supply the original bank statement to resolve the closing balance discrepancy?",
        ),
        (
            "customer_concentration",
            "How secure is the largest customer's contract, and what other customers are in the pipeline?",
        ),
    ):
        if kind in kinds:
            questions.append("• " + question)
    return "\n\n".join(
        [
            "Cached demo memo — no model call",
            "Summary\n"
            + (
                f"The supplied statements cover {len(monthly)} months ({sum(not v['partial'] for v in monthly.values())} full). Net cash flow is {amount(inflow - outflow, currency)}. Verify the evidence and resolve the questions below before a credit decision."
                if monthly
                else "No statements have been extracted. Extract the evidence before assessing the applicant."
            ),
            f"Cash flow\nRecorded inflows total {amount(inflow, currency)} against outflows of {amount(outflow, currency)}. "
            + (
                f"Average observed balance is {amount(computed['average_balance']['value'], currency)}; the minimum is {amount(computed['minimum_balance']['value'], currency)}."
                if monthly
                else "Balance statistics are not yet available."
            ),
            "Concentration\n"
            + (
                f"{concentration['payer']} accounts for {Decimal(concentration['share']) * 100:.1f}% of inflows. Consider the impact of delayed payments from this payer."
                if concentration["payer"]
                else "No positive inflows are available to assess customer concentration."
            ),
            "Red flags\n" + ("\n".join(flags) or "No red flags found."),
            "Questions for the applicant\n"
            + (
                "\n".join(questions)
                or "• Are these statements complete, and what explains any seasonal cash flow changes?"
            ),
        ]
    )


def memo_chunks(endpoint, payload, key):
    """Poll Runpod incrementally rather than buffering worker.client's stream."""
    with httpx.Client(headers={"Authorization": f"Bearer {key}"}, timeout=30) as client:
        response = client.post(
            f"https://api.runpod.ai/v2/{endpoint}/run", json={"input": payload}
        )
        response.raise_for_status()
        job = response.json()["id"]
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            response = client.get(f"https://api.runpod.ai/v2/{endpoint}/stream/{job}")
            response.raise_for_status()
            result = response.json()
            for chunk in result.get("stream") or []:
                yield chunk.get("output", chunk)
            if result.get("status") == "COMPLETED":
                # /stream doesn't report delayTime/executionTime; /status does (needed for cost).
                response = client.get(
                    f"https://api.runpod.ai/v2/{endpoint}/status/{job}"
                )
                response.raise_for_status()
                times = response.json()
                yield {
                    "done": True,
                    "delayTime": times.get("delayTime", result.get("delayTime")),
                    "executionTime": times.get(
                        "executionTime", result.get("executionTime")
                    ),
                }
                return
            if result.get("status") in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise RuntimeError("Memo failed")
            time.sleep(1)
    raise TimeoutError("Memo timed out")


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(clean(data))}\n\n"


# Plain integers of 4+ digits are amounts too ("SGD 55041"); years are skipped below.
MEMO_NUMBER = re.compile(
    r"\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|(?<![\d:r])\d{4,}(?![\d,]|\.\d)"
)
MEMO_CITATION = re.compile(r"\[p\d+:r\d+[^\]]*\]")


def unverified_memo_numbers(text, payload):
    """Compare memo figures with supplied analysis values, including rounded forms."""
    allowed = set()

    def add(value, share=False):
        number = Decimal(str(value).replace(",", "").rstrip("%"))
        if not number.is_finite():
            return
        allowed.add(number)
        allowed.update(round(number, places) for places in (0, 1, 2))
        if share and 0 <= number <= 1:
            allowed.update(round(number * 100, places) for places in (0, 1, 2))

    def collect(value, name=""):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, key)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, name)
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            add(value, name == "share")
        elif isinstance(value, str):
            try:
                add(value, name == "share")
            except InvalidOperation:
                pass
            for match in MEMO_NUMBER.finditer(MEMO_CITATION.sub("", value)):
                add(match.group())

    collect(payload)
    unverified = []
    for match in MEMO_NUMBER.finditer(MEMO_CITATION.sub("", text)):
        token = match.group()
        number = Decimal(token.replace(",", "").rstrip("%"))
        if token.isdigit() and 1900 <= number <= 2100:
            continue
        # A whole number may drop the cents ("55,041" for 55,041.59).
        tolerance = Decimal("0.005") if "." in token else Decimal("0.995")
        if token not in unverified and not any(
            abs(number - value) <= tolerance for value in allowed
        ):
            unverified.append(token)
    return unverified


@app.get("/api/sessions/{s}/memo")
def memo(s: str, model: Literal["8b", "ft", "4b"] = "8b"):
    computed = analysis(s)
    endpoint, key = config(model)

    def memo_analysis(value):
        # Rebuild the payload so the analysis and cached demo retain zero-based rows.
        if isinstance(value, dict):
            if "page_id" in value and "row_index" in value:
                return f"[{value['page_id']}:r{value['row_index'] + 1}]"
            return {name: memo_analysis(item) for name, item in value.items()}
        if isinstance(value, list):
            return [memo_analysis(item) for item in value]
        return value

    def generate():
        final = {"usage": {}, "timing": {}}
        try:
            if os.getenv("DEMO_MODE") == "1" or not endpoint or not key:
                yield event("chunk", {"delta": cached_memo(computed)})
                final["source"] = "cached-demo"
            else:
                payload = memo_analysis(computed)
                deltas = []
                for chunk in memo_chunks(
                    endpoint, {"task": "memo", "analysis": payload}, key
                ):
                    if chunk.get("error"):
                        raise RuntimeError("Worker failed")
                    if chunk.get("delta"):
                        deltas.append(chunk["delta"])
                        yield event("chunk", {"delta": chunk["delta"]})
                    if chunk.get("done"):
                        final.update({k: v for k, v in chunk.items() if k != "done"})
                final["source"] = "runpod"
                final["unverified"] = unverified_memo_numbers("".join(deltas), payload)
        except Exception:
            yield event(
                "error",
                {
                    "message": "Memo connection failed. Your documents and analysis are saved; retry to generate again."
                },
            )
            final["error"] = True
        request_metrics = metrics(final, final, model, final.get("source", "runpod"))
        session(s)["requests"].append({"task": "memo", **request_metrics})
        final["metrics"] = request_metrics
        yield event("final", final)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def fetch_health(endpoint, key):
    response = httpx.get(
        f"https://api.runpod.ai/v2/{endpoint}/health",
        headers={"Authorization": f"Bearer {key}"},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


@app.get("/api/status")
def status():
    with STATUS_LOCK:
        if time.monotonic() - STATUS["time"] < 3:
            return STATUS["value"]
        value = {}
        for model in ("8b", "ft", "4b"):
            endpoint, key = config(model)
            if not endpoint:
                value[model] = {"offline": True}
            else:
                try:
                    value[model] = (
                        fetch_health(endpoint, key)
                        if key and os.getenv("DEMO_MODE") != "1"
                        else {"offline": True}
                    )
                except Exception:
                    value[model] = {"unavailable": True}
        STATUS.update(time=time.monotonic(), value=clean(value))
        return STATUS["value"]


@app.get("/api/eval")
def evaluation():
    path = ROOT.parent / "eval/summary.json"
    return (
        clean(json.loads(path.read_text())) if path.exists() else {"available": False}
    )


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Route not found")
    dist = ROOT / "frontend/dist"
    target = (dist / path).resolve()
    # index.html must never be cached: it names the hashed bundle, so a stale copy keeps old code.
    no_cache = {"Cache-Control": "no-cache"}
    if target.is_relative_to(dist.resolve()) and target.is_file():
        headers = no_cache if target.name == "index.html" else None
        return FileResponse(target, headers=headers)
    if (dist / "index.html").exists():
        return FileResponse(dist / "index.html", headers=no_cache)
    raise HTTPException(
        503, "Frontend not built: run npm install && npm run build in app/frontend"
    )
