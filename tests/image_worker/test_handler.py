"""CPU-only checks for the separate image worker."""

import ast
import base64
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["IMAGE_WORKER_NO_ENGINE"] = "1"
from image_worker import handler as worker  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("MODEL_PATH", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.setenv("RUNPOD_GPU_TYPE", "test GPU")
    monkeypatch.setattr(worker, "ENGINE", dict(worker.ENGINE))
    worker.ENGINE.update(status="disabled", error=None)
    monkeypatch.setattr(worker, "PIPE", None)
    monkeypatch.setattr(worker, "_FIRST_JOB", True)


def test_defaults():
    result = worker.parse_input({"prompt": "  a lake  ", "unknown": True})
    assert result == {
        "task": "image",
        "prompt": "a lake",
        "width": 1024,
        "height": 576,
        "steps": 28,
        "guidance": 3.5,
        "seed": result["seed"],
        "format": "png",
    }
    assert 0 <= result["seed"] < 2**32
    assert worker.parse_input({"prompt": "x", "format": "jpeg"})["quality"] == 90


@pytest.mark.parametrize(
    "data,field",
    [
        ({"prompt": ""}, "prompt"),
        ({"prompt": " "}, "prompt"),
        ({"prompt": "x" * 1001}, "prompt"),
        ({"prompt": 1}, "prompt"),
        ({"width": 257}, "multiple"),
        ({"width": 255}, "width"),
        ({"height": 1552}, "height"),
        ({"height": True}, "height"),
        ({"width": 1536, "height": 1040}, "width * height"),
        ({"steps": 0}, "steps"),
        ({"steps": 51}, "steps"),
        ({"steps": True}, "steps"),
        ({"steps": 2.5}, "steps"),
        ({"format": "webp"}, "format"),
        ({"format": []}, "format"),
        ({"seed": -1}, "seed"),
        ({"seed": 2**32}, "seed"),
        ({"seed": True}, "seed"),
        ({"seed": None}, "seed"),
        ({"guidance": True}, "guidance"),
        ({"guidance": -1}, "guidance"),
        ({"guidance": 11}, "guidance"),
        ({"guidance": float("nan")}, "guidance"),
        ({"guidance": float("inf")}, "guidance"),
        ({"format": "jpeg", "quality": True}, "quality"),
        ({"format": "jpeg", "quality": 49}, "quality"),
        ({"format": "jpeg", "quality": 96}, "quality"),
        ({"task": "other"}, "task"),
    ],
)
def test_rejections(data, field):
    with pytest.raises(ValueError) as exc:
        worker.parse_input({"prompt": "lake", **data})
    assert field in str(exc.value)


@pytest.mark.parametrize("data", [None, [], {}, {"input": []}, {"input": {}}])
def test_bad_job(data):
    assert set(worker.handler(data)) == {"error"}


def test_health():
    assert worker.parse_input({"task": "health"}) == {"task": "health"}
    result = worker.handler({"input": {"task": "health"}})
    assert set(result) == {
        "status",
        "model",
        "model_source",
        "load_seconds",
        "offload",
        "gpu",
        "cold_start",
        "error",
    }
    assert result["cold_start"] is True
    assert worker.handler({"input": {"task": "health"}})["cold_start"] is False


@pytest.mark.parametrize("fmt", ["png", "jpeg"])
def test_generate(monkeypatch, fmt):
    calls = []

    def pipe(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(images=[Image.new("RGB", (256, 272), "blue")])

    worker.ENGINE.update(status="ready", offload=False)
    monkeypatch.setattr(worker, "PIPE", pipe)
    monkeypatch.setattr(worker, "_generator", lambda seed: ("seed", seed))
    job = {
        "input": {
            "prompt": "lake",
            "width": 256,
            "height": 272,
            "seed": 123,
            "format": fmt,
        }
    }
    result = worker.handler(job)
    with Image.open(io.BytesIO(base64.b64decode(result["image_base64"]))) as image:
        assert image.size == (256, 272)
        assert image.format == fmt.upper()
        image.load()
    assert result["seed"] == 123
    assert result["mime"] == f"image/{fmt}"
    assert result["timing"]["cold_start"] is True
    assert worker.handler(job)["timing"]["cold_start"] is False
    assert calls[0]["generator"] == ("seed", 123)
    assert calls[0]["max_sequence_length"] == 512
    assert calls[0]["num_inference_steps"] == 28


def test_load_failure(monkeypatch):
    calls = []

    def fail():
        calls.append(1)
        raise RuntimeError("broken " + "x" * 600)

    monkeypatch.setattr(worker, "resolve_model", fail)
    worker.load_pipeline()
    assert len(worker.ENGINE["error"]) == 500
    for _ in range(3):
        result = worker.handler({"input": {"prompt": "lake"}})
        assert result == {
            "error": "Image model failed to load: " + worker.ENGINE["error"]
        }
    assert calls == [1]


@pytest.mark.parametrize("gib,offload", [(32, True), (40, False), (48, False)])
def test_load_once(monkeypatch, gib, offload):
    calls = []
    pipe = SimpleNamespace(
        to=lambda device: calls.append(device),
        enable_model_cpu_offload=lambda: calls.append("offload"),
    )
    monkeypatch.setattr(worker, "resolve_model", lambda: ("snapshot", "cache"))
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            bfloat16="bf16",
            cuda=SimpleNamespace(
                get_device_properties=lambda _: SimpleNamespace(
                    total_memory=gib * 1024**3
                )
            ),
        ),
    )

    def load(path, **kwargs):
        calls.append((path, kwargs))
        return pipe

    monkeypatch.setitem(
        sys.modules,
        "diffusers",
        SimpleNamespace(FluxPipeline=SimpleNamespace(from_pretrained=load)),
    )
    assert worker.load_pipeline() is pipe
    assert worker.load_pipeline() is pipe
    assert calls == [
        ("snapshot", {"torch_dtype": "bf16"}),
        "offload" if offload else "cuda",
    ]
    assert worker.ENGINE["offload"] is offload
    assert worker.ENGINE["model_source"] == "cache"
    assert worker.ENGINE["load_seconds"] >= 0


def test_resolve_model(tmp_path, monkeypatch, caplog):
    assert worker.resolve_model(tmp_path) == (worker.MODEL, "download")
    assert "download" in caplog.text
    snapshots = tmp_path / "models--black-forest-labs--FLUX.1-dev" / "snapshots"
    for name, stamp in [("old", 10), ("new", 20)]:
        path = snapshots / name
        path.mkdir(parents=True)
        os.utime(path, (stamp, stamp))
    (snapshots / "file").write_text("ignored")
    assert worker.resolve_model(tmp_path) == (str(snapshots / "new"), "cache")
    monkeypatch.setenv("MODEL_PATH", str(tmp_path))
    assert worker.resolve_model(tmp_path) == (str(tmp_path), "baked")
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="MODEL_PATH"):
        worker.resolve_model(tmp_path)
    monkeypatch.setenv("MODEL_PATH", "/runpod-volume/weights")
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    assert worker.resolve_model(tmp_path) == ("/runpod-volume/weights", "volume")


def test_custom_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "fictional/model")
    path = tmp_path / "models--fictional--model" / "snapshots" / "hash"
    path.mkdir(parents=True)
    assert worker.resolve_model(tmp_path) == (str(path), "cache")


@pytest.mark.parametrize(
    "error,message",
    [
        (RuntimeError("private prompt"), "Image generation failed: RuntimeError"),
        (
            type("OutOfMemoryError", (Exception,), {})("private prompt"),
            "out of GPU memory",
        ),
    ],
)
def test_generation_failure(monkeypatch, caplog, error, message):
    def fail(params):
        raise error

    monkeypatch.setattr(worker, "generate", fail)
    result = worker.handler({"input": {"prompt": "private prompt"}})
    assert message in result["error"]
    assert "private prompt" not in caplog.text


def test_docker_and_lazy_imports():
    root = Path(__file__).resolve().parents[2]
    docker = (root / "image_worker/Dockerfile").read_text()
    assert "FROM pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime" in docker
    for pin in (
        "diffusers==0.35.2",
        "transformers==4.56.2",
        "accelerate==1.10.1",
        "sentencepiece==0.2.1",
        "protobuf==5.29.5",
        "runpod==1.7.13",
    ):
        assert pin in docker
    tree = ast.parse((root / "image_worker/handler.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(
                alias.name.split(".")[0] not in {"torch", "diffusers", "runpod"}
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] not in {"torch", "diffusers", "runpod"}


@pytest.mark.parametrize("offload,device", [(True, "cpu"), (False, "cuda")])
def test_generator_device(monkeypatch, offload, device):
    calls = []

    def generator(target):
        calls.append(target)
        return SimpleNamespace(manual_seed=lambda seed: (target, seed))

    worker.ENGINE["offload"] = offload
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(Generator=generator))
    assert worker._generator(42) == (device, 42)
    assert calls == [device]
