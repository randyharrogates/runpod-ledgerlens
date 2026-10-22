"""Runpod image worker backed by diffusers FLUX."""

import base64
import io
import logging
import math
import os
import secrets
import threading
import time
import traceback
from pathlib import Path

MODEL = "black-forest-labs/FLUX.1-dev"
ENGINE = {
    "status": "disabled",
    "load_seconds": 0.0,
    "model_source": None,
    "model": MODEL,
    "offload": None,
    "error": None,
}
PIPE = None
_LOAD_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
_FIRST_JOB = True


def resolve_model(cache_root=Path("/runpod-volume/huggingface-cache/hub")):
    """Return the model argument and source; snapshot mtime defines newest."""
    if path := os.getenv("MODEL_PATH"):
        if not Path(path).is_dir():
            raise ValueError(f"MODEL_PATH is not a local directory: {path}")
        return path, "volume" if path.startswith("/runpod-volume/") else "baked"
    name = os.getenv("MODEL_NAME", MODEL)
    snapshots = Path(cache_root) / ("models--" + name.replace("/", "--")) / "snapshots"
    candidates = (
        [p for p in snapshots.iterdir() if p.is_dir()] if snapshots.is_dir() else []
    )
    if candidates:
        return str(max(candidates, key=lambda p: (p.stat().st_mtime, p.name))), "cache"
    logging.warning("Model cache missing for %s; diffusers will download weights", name)
    return name, "download"


def load_pipeline():
    """Load once, retaining failures so subsequent jobs fail quickly."""
    global PIPE
    with _LOAD_LOCK:
        if ENGINE["status"] in ("ready", "failed"):
            return PIPE
        started = time.monotonic()
        ENGINE["status"] = "loading"
        try:
            path, source = resolve_model()
            ENGINE.update(model=path, model_source=source)
            import torch
            from diffusers import FluxPipeline

            pipe = FluxPipeline.from_pretrained(path, torch_dtype=torch.bfloat16)
            offload = torch.cuda.get_device_properties(0).total_memory < 40 * 1024**3
            if offload:
                pipe.enable_model_cpu_offload()
            else:
                pipe.to("cuda")
            PIPE = pipe
            ENGINE.update(status="ready", offload=offload, error=None)
        except Exception as exc:
            logging.error("Image model failed to load: %s", type(exc).__name__)
            ENGINE.update(status="failed", error=str(exc)[:500])
        finally:
            ENGINE["load_seconds"] = time.monotonic() - started
        return PIPE


def parse_input(job_input):
    """Validate an image or health request without loading the model."""
    if not isinstance(job_input, dict):
        raise ValueError("job.input must be an object")
    task = job_input.get("task", "image" if "prompt" in job_input else None)
    if task == "health":
        return {"task": "health"}
    if task != "image":
        raise ValueError("task must be image or health")
    prompt = job_input.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 1000:
        raise ValueError("prompt must be a non-empty string of at most 1000 characters")
    params = {"task": task, "prompt": prompt.strip()}
    for field, default, low, high in (
        ("width", 1024, 256, 1536),
        ("height", 576, 256, 1536),
        ("steps", 28, 1, 50),
        ("seed", None, 0, 2**32 - 1),
    ):
        value = job_input.get(field, default)
        if field == "seed" and field not in job_input:
            value = secrets.randbits(32)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{field} must be an integer between {low} and {high}")
        if field in ("width", "height") and value % 16:
            raise ValueError(f"{field} must be a multiple of 16")
        params[field] = value
    if params["width"] * params["height"] > 1536 * 1024:
        raise ValueError("width * height must be at most 1536 * 1024")
    guidance = job_input.get("guidance", 3.5)
    if (
        type(guidance) not in (int, float)
        or not 0 <= guidance <= 10
        or not math.isfinite(guidance)
    ):
        raise ValueError("guidance must be a number between 0 and 10")
    params["guidance"] = float(guidance)
    fmt = job_input.get("format", "png")
    if fmt not in ("png", "jpeg"):
        raise ValueError("format must be png or jpeg")
    params["format"] = fmt
    if fmt == "jpeg":
        quality = job_input.get("quality", 90)
        if type(quality) is not int or not 50 <= quality <= 95:
            raise ValueError("quality must be an integer between 50 and 95")
        params["quality"] = quality
    return params


def gpu_name():
    if name := os.getenv("RUNPOD_GPU_TYPE"):
        return name
    try:
        import torch

        return torch.cuda.get_device_name(0)
    except Exception:
        return None


def _generator(seed):
    """Create a seeded generator on the pipeline's execution device."""
    import torch

    return torch.Generator("cpu" if ENGINE["offload"] else "cuda").manual_seed(seed)


def _take_cold_start():
    global _FIRST_JOB
    cold = _FIRST_JOB
    _FIRST_JOB = False
    return cold


def generate(params):
    """Generate and encode one image; caller serializes pipeline access."""
    started = time.monotonic()
    pipe = load_pipeline()
    if ENGINE["status"] == "failed":
        # refresh_worker: Runpod stops this worker after the job and starts a fresh one.
        return {
            "error": f"Image model failed to load: {ENGINE['error']}",
            "refresh_worker": True,
        }
    image = pipe(
        prompt=params["prompt"],
        width=params["width"],
        height=params["height"],
        num_inference_steps=params["steps"],
        guidance_scale=params["guidance"],
        generator=_generator(params["seed"]),
        max_sequence_length=512,
    ).images[0]
    if image.size != (params["width"], params["height"]):
        raise RuntimeError("Pipeline returned unexpected image dimensions")
    buffer = io.BytesIO()
    fmt = params["format"]
    if fmt == "jpeg":
        image.convert("RGB").save(buffer, format="JPEG", quality=params["quality"])
    else:
        image.save(buffer, format="PNG")
    return {
        "image_base64": base64.b64encode(buffer.getvalue()).decode(),
        "mime": f"image/{fmt}",
        **{
            key: params[key]
            for key in ("seed", "width", "height", "steps", "guidance", "prompt")
        },
        "model": MODEL,
        "gpu": gpu_name(),
        "timing": {
            "generate_s": time.monotonic() - started,
            "load_s": ENGINE["load_seconds"],
            "cold_start": _take_cold_start(),
        },
    }


def handler(job):
    """Return one Runpod response without logging request contents."""
    try:
        params = parse_input(job.get("input") if isinstance(job, dict) else None)
    except ValueError as exc:
        return {"error": str(exc)}
    with _JOB_LOCK:
        if params["task"] == "health":
            result = {**ENGINE, "gpu": gpu_name(), "cold_start": _take_cold_start()}
            if ENGINE["status"] == "failed":
                result["refresh_worker"] = True
            return result
        try:
            logging.info("Generating image; prompt length=%d", len(params["prompt"]))
            return generate(params)
        except Exception as exc:
            # Exception messages can contain prompts; retain stack frames and type only.
            logging.error(
                "Image generation failed: %s\n%s",
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            _take_cold_start()
            if type(exc).__name__ in ("OutOfMemoryError", "CudaOutOfMemoryError"):
                return {
                    "error": "Image generation ran out of GPU memory; reduce dimensions or steps"
                }
            return {"error": f"Image generation failed: {type(exc).__name__}"}


if os.getenv("IMAGE_WORKER_NO_ENGINE") != "1":
    load_pipeline()

if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
