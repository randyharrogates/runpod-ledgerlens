"""Runpod streaming worker backed by a local vLLM HTTP server."""

import asyncio
import base64
import binascii
import io
import json
import logging
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from PIL import Image
from pydantic import ValidationError

from ledgerlens.images import DEFAULT_MAX_PIXELS, resize_image
from ledgerlens.schema import Invoice, Receipt, Statement, json_schema

try:
    from worker.prompts import EXTRACT_SYSTEM, GENERATE_SYSTEM, MEMO_SYSTEM
except ModuleNotFoundError:  # Direct execution by the Docker CMD.
    from prompts import EXTRACT_SYSTEM, GENERATE_SYSTEM, MEMO_SYSTEM

BASE_URL = "http://127.0.0.1:8000"
MAX_PIXELS = int(os.getenv("MAX_PIXELS", str(DEFAULT_MAX_PIXELS)))


def positive_env(name, default):
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


STREAM_FLUSH_CHARS = positive_env("STREAM_FLUSH_CHARS", 64)
STREAM_FLUSH_SECONDS = positive_env("STREAM_FLUSH_MS", 200) / 1000
# Largest input accepted before decoding: covers 50 MP phone cameras, rejects decompression bombs.
MAX_INPUT_PIXELS = 60_000_000
Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
MODELS = {"statement": Statement, "invoice": Invoice, "receipt": Receipt}
ENGINE = {"status": "disabled", "engine_load_seconds": 0.0, "model_source": None}
PROCESS = None
_STDERR = None
_FIRST_JOB = True
_JOB_LOCK = threading.Lock()


def resolve_model(cache_root=Path("/runpod-volume/huggingface-cache/hub")):
    """Return the model argument and its source; snapshot mtime defines newest."""
    if path := os.getenv("MODEL_PATH"):
        if not Path(path).is_dir():
            raise ValueError(f"MODEL_PATH is not a local directory: {path}")
        # A path on the network volume is reported as such; anything else was baked into the image.
        return path, "volume" if path.startswith("/runpod-volume/") else "baked"
    name = os.getenv("MODEL_NAME", "Qwen/Qwen3-VL-8B-Instruct")
    snapshots = Path(cache_root) / ("models--" + name.replace("/", "--")) / "snapshots"
    candidates = (
        [p for p in snapshots.iterdir() if p.is_dir()] if snapshots.is_dir() else []
    )
    if candidates:
        return str(max(candidates, key=lambda p: (p.stat().st_mtime, p.name))), "cache"
    logging.warning("Model cache missing for %s; vLLM will download weights", name)
    return name, "download"


def start_engine():
    """Launch once at cold start and wait for readiness, retaining stderr on disk."""
    global PROCESS, _STDERR
    started = time.monotonic()
    model, source = resolve_model()
    ENGINE.update(status="starting", model=model, model_source=source)
    command = [
        "vllm",
        "serve",
        model,
        "--served-model-name",
        "ledgerlens",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--max-model-len",
        os.getenv("MAX_MODEL_LEN", "8192"),
        "--gpu-memory-utilization",
        os.getenv("GPU_MEM_UTIL", "0.92"),
        "--limit-mm-per-prompt",
        '{"image": 1, "video": 0}',
        # Same pixel cap as training (ledgerlens.images), so the processor never rescales again.
        "--mm-processor-kwargs",
        json.dumps({"max_pixels": MAX_PIXELS}),
        # The chat agent's data questions use OpenAI tool calls; Qwen3 emits Hermes-style calls.
        # No free whitespace inside forced JSON: the router padded its plan with blank lines until
        # it hit the token cap. The reader was trained on compact JSON, so it is unaffected.
        "--structured-outputs-config",
        '{"backend": "xgrammar", "disable_any_whitespace": true}',
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "hermes",
        *shlex.split(os.getenv("VLLM_EXTRA_ARGS", "")),
    ]
    env = os.environ.copy()
    if source == "cache":
        env["HF_HUB_OFFLINE"] = "1"
    _STDERR = tempfile.TemporaryFile()
    PROCESS = subprocess.Popen(command, env=env, stderr=_STDERR)
    timeout = float(os.getenv("ENGINE_START_TIMEOUT", "900"))
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            while time.monotonic() - started < timeout:
                if PROCESS.poll() is not None:
                    _STDERR.seek(0, os.SEEK_END)
                    size = _STDERR.tell()
                    _STDERR.seek(max(0, size - 65536))
                    tail = _STDERR.read().decode(errors="replace").splitlines()[-80:]
                    raise RuntimeError("vLLM exited early:\n" + "\n".join(tail))
                try:
                    if client.get(BASE_URL + "/health").is_success:
                        ENGINE.update(
                            status="ready",
                            engine_load_seconds=time.monotonic() - started,
                        )
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(1)
        raise TimeoutError(f"vLLM did not become ready within {timeout}s")
    except Exception:
        ENGINE["status"] = "failed"
        if PROCESS.poll() is None:
            PROCESS.terminate()
            try:
                PROCESS.wait(timeout=10)
            except subprocess.TimeoutExpired:
                PROCESS.kill()
                PROCESS.wait()
        raise


def vllm_client(payload):
    """Yield one JSON response or decoded SSE events; this is the test seam."""
    with httpx.Client(
        timeout=httpx.Timeout(300, connect=10), trust_env=False
    ) as client:
        if not payload.get("stream"):
            response = client.post(BASE_URL + "/v1/chat/completions", json=payload)
            response.raise_for_status()
            yield response.json()
            return
        with client.stream(
            "POST", BASE_URL + "/v1/chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if data:
                        yield json.loads(data)


def validate_input(job):
    if not isinstance(job, dict) or not isinstance(job.get("input"), dict):
        raise ValueError("job.input must be an object")
    data = job["input"]
    data = dict(data)
    if "task" not in data and "prompt" in data:
        data["task"] = "generate"
    task = data.get("task")
    if task not in ("health", "extract", "memo", "generate"):
        raise ValueError("task must be extract, memo, generate, or health")
    if "max_tokens" in data and (
        type(data["max_tokens"]) is not int or data["max_tokens"] <= 0
    ):
        raise ValueError("max_tokens must be a positive integer")
    if task == "generate":
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 20000:
            raise ValueError(
                "prompt must be a non-empty string of at most 20000 characters"
            )
        data["prompt"] = prompt.strip()
        if "system" in data and (
            not isinstance(data["system"], str) or len(data["system"]) > 4000
        ):
            raise ValueError("system must be a string of at most 4000 characters")
        if data.get("max_tokens", 512) > 4096:
            raise ValueError("max_tokens must be at most 4096")
        temperature = data.get("temperature", 0.7)
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be a number between 0 and 2")
        if "stream" in data and type(data["stream"]) is not bool:
            raise ValueError("stream must be a boolean")
    if task == "memo":
        if not isinstance(data.get("analysis"), dict):
            raise ValueError("analysis must be an object")
        try:
            json.dumps(data["analysis"], allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("analysis must contain JSON values") from exc
    if task == "extract":
        if not isinstance(data.get("doc_type"), str) or data["doc_type"] not in MODELS:
            raise ValueError("doc_type must be statement, invoice, or receipt")
        return data, validate_image(data.get("image_base64"))
    if task == "generate" and "image_base64" in data:
        return data, validate_image(data["image_base64"])
    return data, None


def validate_image(encoded):
    if not isinstance(encoded, str) or len(encoded) > 4 * ((15 * 1024 * 1024 + 2) // 3):
        raise ValueError("image_base64 must be base64 and decode to at most 15 MB")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("image_base64 is invalid base64") from exc
    if len(image) > 15 * 1024 * 1024:
        raise ValueError("image exceeds 15 MB")
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif image.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("image must be PNG, JPEG, or WEBP")
    return mime


def prepare_image(image_bytes, max_pixels=None):
    """Shrink the page exactly as training did, then re-encode it as PNG base64.

    The image is untrusted: a small compressed file can declare enormous dimensions (a
    decompression bomb). Image.open only reads the header, so the size is checked before any
    pixel is decoded.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        if image.width * image.height > MAX_INPUT_PIXELS:
            raise ValueError(
                f"image is {image.width}x{image.height}; the limit is "
                f"{MAX_INPUT_PIXELS // 1_000_000} megapixels"
            )
        # JPEG only: let the decoder downscale while decoding instead of after.
        image.draft("RGB", (image.width // 2, image.height // 2))
        resized = resize_image(image, MAX_PIXELS if max_pixels is None else max_pixels)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def gpu_name(driver_root=Path("/proc/driver/nvidia/gpus")):
    if name := os.getenv("RUNPOD_GPU_TYPE"):
        return name
    if shutil.which("nvidia-smi"):
        try:
            if name := subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                text=True,
                timeout=5,
            ).strip():
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    # Some hosts have neither (seen 11 Oct); the driver lists each GPU as "Model: <name>".
    for info in sorted(Path(driver_root).glob("*/information")):
        try:
            for line in info.read_text().splitlines():
                if line.startswith("Model:"):
                    return line.split(":", 1)[1].strip() or None
        except OSError:
            continue
    return None


def sync_handler(job):
    global _FIRST_JOB
    with _JOB_LOCK:
        cold = _FIRST_JOB
        _FIRST_JOB = False
    try:
        data, mime = validate_input(job)
    except ValueError as exc:
        yield {"error": str(exc)}
        return
    if data["task"] == "health":
        status = dict(ENGINE)
        if (
            ENGINE["status"] != "failed"
            and PROCESS is not None
            and PROCESS.poll() is not None
        ):
            status["status"] = "exited"
        yield {"task": "health", **status, "cold_start": cold}
        return
    if ENGINE["status"] == "failed":
        yield {
            "error": f"Model engine failed to start: {ENGINE.get('error', 'unknown error')}"
        }
        return
    started = time.monotonic()
    payload = {
        "model": "ledgerlens",
        "temperature": 0,
        "max_tokens": data.get(
            "max_tokens", 4096 if data["task"] == "extract" else 1500
        ),
    }
    if data["task"] == "extract":
        doc_type = data["doc_type"]
        try:
            image_b64 = prepare_image(base64.b64decode(data["image_base64"]))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            yield {"error": f"Could not read image: {exc}"}
            return
        payload.update(
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Transcribe this {doc_type}."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": doc_type, "schema": json_schema(doc_type)},
            },
        )
    elif data["task"] == "memo":
        payload.update(
            messages=[
                {"role": "system", "content": MEMO_SYSTEM},
                {"role": "user", "content": json.dumps(data["analysis"])},
            ],
            stream=True,
            stream_options={"include_usage": True},
        )

    else:
        content = [{"type": "text", "text": data["prompt"]}]
        if mime:
            try:
                image_b64 = prepare_image(base64.b64decode(data["image_base64"]))
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                yield {"error": f"Could not read image: {exc}"}
                return
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                }
            )
        payload.update(
            messages=[
                {"role": "system", "content": data.get("system", GENERATE_SYSTEM)},
                {"role": "user", "content": content},
            ],
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 512),
            stream=data.get("stream", False),
        )
        if payload["stream"]:
            payload["stream_options"] = {"include_usage": True}

    def model_name():
        return (
            ENGINE.get("model")
            or os.getenv("MODEL_PATH")
            or os.getenv("MODEL_NAME", "Qwen/Qwen3-VL-8B-Instruct")
        )

    def timing():
        return {
            "inference_s": time.monotonic() - started,
            "engine_load_s": ENGINE["engine_load_seconds"],
            "cold_start": cold,
        }

    try:
        if payload.get("stream"):
            usage = {}
            text = ""
            buffered = ""
            last_yield = time.monotonic()
            for event in vllm_client(payload):
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices", []):
                    if delta := choice.get("delta", {}).get("content"):
                        text += delta
                        buffered += delta
                        now = time.monotonic()
                        if (
                            len(buffered) >= STREAM_FLUSH_CHARS
                            or now - last_yield >= STREAM_FLUSH_SECONDS
                        ):
                            last_yield = now
                            yield {"delta": buffered}
                            buffered = ""
            if buffered:
                yield {"delta": buffered}
            final = {
                "done": True,
                "usage": usage,
                "timing": timing(),
                "gpu": gpu_name(),
                "model": ENGINE.get("model"),
            }
            if data["task"] == "generate":
                final.update(task="generate", text=text, model=model_name())
            yield final
            return
        response = next(iter(vllm_client(payload)))
        raw = response["choices"][0]["message"]["content"]
        if data["task"] == "generate":
            yield {
                "task": "generate",
                "text": raw,
                "usage": response.get("usage") or {},
                "timing": timing(),
                "gpu": gpu_name(),
                "model": model_name(),
            }
            return
        result = {
            "task": "extract",
            "doc_type": doc_type,
            "document": None,
            "valid": False,
            "validation_error": None,
            "usage": response.get("usage") or {},
            "timing": timing(),
            "model": ENGINE.get("model")
            or os.getenv("MODEL_PATH")
            or os.getenv("MODEL_NAME", "Qwen/Qwen3-VL-8B-Instruct"),
            "gpu": gpu_name(),
        }
        try:
            document = MODELS[doc_type].model_validate_json(raw)
            result.update(document=document.model_dump(mode="json"), valid=True)
        except ValidationError as exc:
            result.update(validation_error=str(exc), raw=raw)
        yield result
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        StopIteration,
    ) as exc:
        yield {"error": f"Model request failed: {exc}"}


async def handler(job):
    data = job.get("input") if isinstance(job, dict) else None
    if isinstance(data, dict) and (
        data.get("task") in ("agent", "read")
        or ("task" not in data and "prompt" in data)
    ):
        from worker.agent import Engine, run_agent, run_read

        if ENGINE["status"] == "failed":
            yield {
                "error": f"Model engine failed to start: {ENGINE.get('error', 'unknown error')}"
            }
            return
        data = dict(data)
        if "task" not in data:
            data.update(task="agent", message=data.get("prompt"))
            if isinstance(data.get("image_base64"), str):
                data["new_pages"] = [
                    {"page_id": "p1", "image_base64": data["image_base64"]}
                ]
        async with Engine() as engine:
            run = run_read if data["task"] == "read" else run_agent
            async for event in run(data, engine):
                yield event
        return
    iterator = sync_handler(job)
    sentinel = object()
    try:
        while True:
            item = await asyncio.to_thread(next, iterator, sentinel)
            if item is sentinel:
                break
            yield item
    finally:
        await asyncio.to_thread(iterator.close)


if os.getenv("LEDGERLENS_NO_ENGINE") != "1":
    try:
        start_engine()
    except Exception as exc:
        logging.exception("Model engine failed to start")
        ENGINE.update(status="failed", error=str(exc)[-2000:])

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.modules.setdefault("worker.handler", sys.modules[__name__])
    import runpod

    from worker.agent import MAX_CONCURRENCY

    runpod.serverless.start(
        {
            "handler": handler,
            "concurrency_modifier": lambda current: MAX_CONCURRENCY,
            "return_aggregate_stream": True,
        }
    )
