import base64
import io
import json
import os
import subprocess
import sys

import httpx
import pytest
from PIL import Image

os.environ["LEDGERLENS_NO_ENGINE"] = "1"
from worker import client  # noqa: E402
from worker import handler as worker


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(worker, "_FIRST_JOB", True)
    monkeypatch.setattr(worker, "PROCESS", None)
    monkeypatch.setattr(
        worker, "ENGINE", {"status": "disabled", "engine_load_seconds": 0.0}
    )
    monkeypatch.setenv("RUNPOD_GPU_TYPE", "test GPU")


def png_bytes(size=(40, 60)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def extract_input():
    return {
        "task": "extract",
        "doc_type": "statement",
        "image_base64": base64.b64encode(png_bytes()).decode(),
    }


def test_prepare_image_applies_training_pixel_cap(monkeypatch):
    monkeypatch.setattr(worker, "MAX_PIXELS", 1000)
    out = Image.open(
        io.BytesIO(base64.b64decode(worker.prepare_image(png_bytes((100, 200)))))
    )
    assert out.width * out.height <= 1000
    assert abs(out.height / out.width - 2) < 0.1


def test_prepare_image_keeps_jpeg_size_within_cap(monkeypatch):
    """A JPEG already under the cap is not halved by the decoder."""
    monkeypatch.setattr(worker, "MAX_PIXELS", 2_000_000)
    buffer = io.BytesIO()
    Image.new("RGB", (595, 841), "white").save(buffer, format="JPEG", quality=90)
    out = Image.open(
        io.BytesIO(base64.b64decode(worker.prepare_image(buffer.getvalue())))
    )
    assert out.size == (595, 841)
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(big, format="JPEG", quality=90)
    out = Image.open(io.BytesIO(base64.b64decode(worker.prepare_image(big.getvalue()))))
    assert 1_500_000 < out.width * out.height <= 2_000_000


def test_prepare_image_rejects_decompression_bomb(monkeypatch):
    monkeypatch.setattr(worker, "MAX_INPUT_PIXELS", 10_000)
    with pytest.raises(ValueError, match="megapixels"):
        worker.prepare_image(png_bytes((200, 100)))


def test_extract_reports_oversized_image_as_error(monkeypatch):
    monkeypatch.setattr(worker, "MAX_INPUT_PIXELS", 1_000)
    job = {"input": extract_input()}
    assert "Could not read image" in next(worker.sync_handler(job))["error"]


def test_extract_rejects_undecodable_image():
    job = {
        "input": {
            **extract_input(),
            "image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(),
        }
    }
    assert "Could not read image" in next(worker.sync_handler(job))["error"]


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"task": []},
        {"task": "other"},
        {"task": "memo", "analysis": []},
        {"task": "memo", "analysis": {"x": float("nan")}},
        {**extract_input(), "doc_type": []},
        {**extract_input(), "doc_type": "other"},
        {**extract_input(), "image_base64": "%%%"},
        {**extract_input(), "image_base64": base64.b64encode(b"GIF89a").decode()},
        {**extract_input(), "max_tokens": True},
        {**extract_input(), "max_tokens": 0},
        {**extract_input(), "image_base64": "A" * (20 * 1024 * 1024 + 4)},
    ],
)
def test_invalid_input(data, monkeypatch):
    monkeypatch.setattr(
        worker, "vllm_client", lambda _: pytest.fail("must not call model")
    )
    result = list(worker.sync_handler({"input": data}))
    assert len(result) == 1 and "error" in result[0]


def test_extract(monkeypatch):
    document = {
        "doc_type": "statement",
        "bank_name": "Bank",
        "account_holder": "Ada",
        "account_number_masked": "**12",
        "period_start": "2025-01-01",
        "period_end": "2025-01-31",
        "currency": "EUR",
        "opening_balance": "10.00",
        "closing_balance": "10.00",
        "transactions": [],
    }

    def fake(payload):
        assert payload["response_format"]["json_schema"]["name"] == "statement"
        assert payload["temperature"] == 0
        assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        yield {
            "choices": [{"message": {"content": json.dumps(document)}}],
            "usage": {"total_tokens": 3},
        }

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": extract_input()}))[0]
    assert result["valid"] and result["document"] == document
    assert result["validation_error"] is None and "raw" not in result
    assert result["usage"] == {"total_tokens": 3}
    assert result["gpu"] == "test GPU"


@pytest.mark.parametrize("raw", ["not JSON", '{"doc_type":"statement"}'])
def test_invalid_model_json(monkeypatch, raw):
    monkeypatch.setattr(
        worker,
        "vllm_client",
        lambda _: iter([{"choices": [{"message": {"content": raw}}]}]),
    )
    result = list(worker.sync_handler({"input": extract_input()}))[0]
    assert not result["valid"] and result["raw"] == raw
    assert result["document"] is None and result["validation_error"]


def test_memo(monkeypatch):
    def fake(payload):
        assert payload["temperature"] == 0
        assert payload["stream"] and payload["stream_options"]["include_usage"]
        yield {"choices": [{"delta": {"content": "Summary"}}]}
        yield {"choices": [{"delta": {"content": " [p1:r2]"}}]}
        yield {"choices": [], "usage": {"total_tokens": 8}}

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": {"task": "memo", "analysis": {}}}))
    assert "".join(item["delta"] for item in result[:-1]) == "Summary [p1:r2]"
    assert result[-1]["done"] and result[-1]["usage"] == {"total_tokens": 8}
    assert result[-1]["gpu"] == "test GPU"


def test_gpu_name_falls_back_to_the_driver_file(monkeypatch, tmp_path):
    monkeypatch.delenv("RUNPOD_GPU_TYPE", raising=False)
    monkeypatch.setattr(worker.shutil, "which", lambda _: None)
    assert worker.gpu_name(tmp_path) is None
    gpu = tmp_path / "0000:01:00.0"
    gpu.mkdir()
    (gpu / "information").write_text(
        "Model: \t\t NVIDIA GeForce RTX 4090\nIRQ:   \t\t 16\n"
    )
    assert worker.gpu_name(tmp_path) == "NVIDIA GeForce RTX 4090"


def test_cold_start_and_health(monkeypatch):
    monkeypatch.setattr(
        worker, "vllm_client", lambda _: pytest.fail("health calls no model")
    )
    assert list(worker.sync_handler({"input": {"task": "health"}}))[0]["cold_start"]
    assert not list(worker.sync_handler({"input": {"task": "health"}}))[0]["cold_start"]


def test_resolve_model(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("MODEL_PATH", raising=False)
    monkeypatch.setenv("MODEL_NAME", "org/model")
    assert worker.resolve_model(tmp_path) == ("org/model", "download")
    assert "download" in caplog.text
    root = tmp_path / "models--org--model" / "snapshots"
    first = root / "first"
    first.mkdir(parents=True)
    assert worker.resolve_model(tmp_path) == (str(first), "cache")
    second = root / "second"
    second.mkdir()
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))
    assert worker.resolve_model(tmp_path) == (str(second), "cache")
    monkeypatch.setenv("MODEL_PATH", str(tmp_path))
    assert worker.resolve_model(tmp_path) == (str(tmp_path), "baked")
    # Weights on the network volume are reported as "volume" (the directory check is faked).
    monkeypatch.setenv("MODEL_PATH", "/runpod-volume/ledgerlens/main/merged")
    monkeypatch.setattr(type(tmp_path), "is_dir", lambda self: True)
    assert worker.resolve_model(tmp_path)[1] == "volume"


def test_engine_launch(monkeypatch, tmp_path):
    commands = []

    class Process:
        def poll(self):
            return None

    def launch(command, **kwargs):
        commands.append((command, kwargs))
        return Process()

    real_client = httpx.Client
    monkeypatch.setattr(worker, "resolve_model", lambda: (str(tmp_path), "cache"))
    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    monkeypatch.setenv("VLLM_EXTRA_ARGS", '--dtype "bfloat16"')
    monkeypatch.setattr(
        worker.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200)), **kwargs
        ),
    )
    worker.start_engine()
    assert commands[0][0][-2:] == ["--dtype", "bfloat16"]
    assert commands[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert worker.ENGINE["status"] == "ready"
    worker._STDERR.close()


def test_client_missing_key(monkeypatch, capsys):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setattr(client, "runpodctl_key", lambda: None)
    assert (
        client.main(
            [
                "extract",
                "--endpoint-id",
                "id",
                "--image",
                "missing.png",
                "--doc-type",
                "statement",
            ]
        )
        == 2
    )
    assert "RUNPOD_API_KEY" in capsys.readouterr().err


def test_client_never_echoes_key(monkeypatch, capsys, tmp_path):
    secret = "private-test-token"
    monkeypatch.setenv("RUNPOD_API_KEY", secret)
    analysis = tmp_path / "analysis.json"
    analysis.write_text("{}")
    monkeypatch.setattr(client, "call_endpoint", lambda *a, **kw: {"echo": secret})
    assert (
        client.main(["memo", "--endpoint-id", "id", "--analysis", str(analysis)]) == 0
    )
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err

    def fail(*args, **kwargs):
        raise httpx.HTTPError(secret)

    monkeypatch.setattr(client, "call_endpoint", fail)
    assert (
        client.main(["memo", "--endpoint-id", "id", "--analysis", str(analysis)]) == 1
    )
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_runpodctl_key_reads_saved_config(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'apikey = "saved-key"\napiurl = "https://api.runpod.io/graphql"\n'
    )
    assert client.runpodctl_key(config) == "saved-key"
    assert client.runpodctl_key(tmp_path / "missing.toml") is None


def test_cli_subprocess_without_key(tmp_path):
    env = dict(os.environ)
    env.pop("RUNPOD_API_KEY", None)
    env["HOME"] = str(tmp_path)  # never pick up the real runpodctl key
    result = subprocess.run(
        [
            sys.executable,
            "worker/client.py",
            "extract",
            "--endpoint-id",
            "id",
            "--image",
            "missing.png",
            "--doc-type",
            "statement",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_engine_early_exit(monkeypatch, capsys):
    class Process:
        def poll(self):
            return 1

    def launch(command, **kwargs):
        kwargs["stderr"].write(b"loading\nweights unavailable\n")
        kwargs["stderr"].flush()
        return Process()

    monkeypatch.setattr(worker, "resolve_model", lambda: ("org/model", "download"))
    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="weights unavailable"):
        worker.start_engine()
    assert worker.ENGINE["status"] == "failed"
    assert "loading\nweights unavailable\n" in capsys.readouterr().err
    worker._STDERR.close()


def test_engine_timeout(monkeypatch):
    class Process:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    process = Process()
    monkeypatch.setattr(worker, "resolve_model", lambda: ("org/model", "download"))
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setenv("ENGINE_START_TIMEOUT", "0")
    with pytest.raises(TimeoutError):
        worker.start_engine()
    assert process.terminated
    worker._STDERR.close()


def test_rest_client_stream(monkeypatch):
    requests = []
    responses = iter(
        [
            {"id": "job", "status": "IN_QUEUE"},
            {"status": "IN_PROGRESS", "stream": [{"output": {"delta": "Hello"}}]},
            {"status": "COMPLETED", "stream": [{"output": {"done": True}}]},
        ]
    )

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=next(responses))

    real_client = httpx.Client
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    result = client.call_endpoint(
        "endpoint", {"task": "memo", "analysis": {}}, "secret", stream=True
    )
    assert [r.url.path for r in requests] == [
        "/v2/endpoint/run",
        "/v2/endpoint/stream/job",
        "/v2/endpoint/stream/job",
    ]
    assert all(r.headers["Authorization"] == "Bearer secret" for r in requests)
    assert len(result["stream"]) == 2 and result["status"] == "COMPLETED"


def test_rest_client_runs_then_polls_status(monkeypatch):
    def respond(request):
        if request.url.path == "/v2/endpoint/run":
            assert json.loads(request.content)["input"]["task"] == "extract"
            return httpx.Response(200, json={"id": "job", "status": "IN_QUEUE"})
        assert request.url.path == "/v2/endpoint/status/job"
        return httpx.Response(
            200, json={"status": "COMPLETED", "output": {"valid": True}}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    result = client.call_endpoint("endpoint", {"task": "extract"}, "secret")
    assert result["output"]["valid"] and result["id"] == "job"


def test_engine_import_failure_still_starts_serverless(monkeypatch, caplog):
    import runpy
    from types import SimpleNamespace

    reason = "x" * 3000 + " torchcodec root cause"
    registered = []

    def fail(*args, **kwargs):
        raise OSError(reason)

    monkeypatch.delenv("LEDGERLENS_NO_ENGINE")
    monkeypatch.delenv("MODEL_PATH", raising=False)
    monkeypatch.setattr(subprocess, "Popen", fail)
    monkeypatch.setitem(
        sys.modules,
        "runpod",
        SimpleNamespace(serverless=SimpleNamespace(start=registered.append)),
    )
    module = runpy.run_path("worker/handler.py", run_name="__main__")
    assert registered[0]["handler"] is module["handler"]
    assert module["ENGINE"]["status"] == "failed"
    assert module["ENGINE"]["error"] == reason[-2000:]
    assert reason in caplog.text
    module["handler"].__globals__["vllm_client"] = lambda _: pytest.fail("no model")
    for data in (extract_input(), {"task": "memo", "analysis": {}}):
        assert list(module["sync_handler"]({"input": data})) == [
            {"error": "Model engine failed to start: " + reason[-2000:]}
        ]
    health = next(module["sync_handler"]({"input": {"task": "health"}}))
    assert health["status"] == "failed" and health["error"] == reason[-2000:]
    module["_STDERR"].close()


def test_failed_health_preserves_status_for_exited_process(monkeypatch):
    from types import SimpleNamespace

    worker.ENGINE.update(status="failed", error="root cause")
    monkeypatch.setattr(worker, "PROCESS", SimpleNamespace(poll=lambda: 1))
    health = next(worker.sync_handler({"input": {"task": "health"}}))
    assert health["status"] == "failed" and health["error"] == "root cause"


def test_engine_stderr_keeps_80_lines_and_64_kib(monkeypatch):
    from types import SimpleNamespace

    lines = [f"line {i}: " + "x" * 600 for i in range(100)]
    lines[30] = "torchcodec root cause" + "x" * 600

    def launch(command, **kwargs):
        kwargs["stderr"].write(("\n".join(lines) + "\n").encode())
        kwargs["stderr"].flush()
        return SimpleNamespace(poll=lambda: 1)

    monkeypatch.setattr(worker, "resolve_model", lambda: ("org/model", "download"))
    monkeypatch.setattr(subprocess, "Popen", launch)
    try:
        with pytest.raises(RuntimeError) as caught:
            worker.start_engine()
        assert str(caught.value).splitlines()[1:] == lines[-80:]
    finally:
        worker._STDERR.close()


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "TIMED_OUT"])
@pytest.mark.parametrize("error_location", ["error", "output", "stream"])
def test_client_terminal_failure(monkeypatch, capsys, tmp_path, status, error_location):
    secret = "private-test-token"
    monkeypatch.setenv("RUNPOD_API_KEY", secret)
    analysis = tmp_path / "analysis.json"
    analysis.write_text("{}")
    error = f"torchcodec failed {secret}"
    details = {
        "error": error,
        "output": [{"error": error}],
        "stream": [{"output": {"error": error}}],
    }
    monkeypatch.setattr(
        client,
        "call_endpoint",
        lambda *a, **kw: {
            "id": "job-123",
            "status": status,
            error_location: details[error_location],
        },
    )
    assert (
        client.main(["memo", "--endpoint-id", "endpoint", "--analysis", str(analysis)])
        == 1
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert all(
        value in output
        for value in ("job-123", status, "torchcodec failed", "[REDACTED]")
    )
    assert secret not in output


@pytest.mark.parametrize("task", ["extract", "memo"])
def test_client_timeout_details_and_option(monkeypatch, capsys, tmp_path, task):
    secret = "private-test-token"
    monkeypatch.setenv("RUNPOD_API_KEY", secret)
    source = tmp_path / "input.json"
    source.write_text("{}")
    real_client = httpx.Client
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "id": "job-123",
                        "status": "IN_QUEUE",
                        "output": {"error": secret},
                    },
                )
            ),
            **kwargs,
        ),
    )
    options = (
        ["--analysis", str(source)]
        if task == "memo"
        else ["--image", str(source), "--doc-type", "statement"]
    )
    assert (
        client.main([task, "--endpoint-id", "endpoint", "--timeout", "0", *options])
        == 1
    )
    output = capsys.readouterr().err
    assert "Runpod job job-123 still IN_QUEUE after 0.0s" in output
    assert "runpodctl serverless status endpoint job-123" in output
    assert "[REDACTED]" in output and secret not in output


def test_client_http_error_is_redacted(monkeypatch, capsys, tmp_path):
    secret = "private-test-token"
    monkeypatch.setenv("RUNPOD_API_KEY", secret)
    source = tmp_path / "analysis.json"
    source.write_text("{}")

    def fail(*args, **kwargs):
        request = httpx.Request(
            "POST", f"https://user:{secret}@example.com/v2/{secret}/run"
        )
        response = httpx.Response(403, request=request, text=secret)
        response.raise_for_status()

    monkeypatch.setattr(client, "call_endpoint", fail)
    assert (
        client.main(["memo", "--endpoint-id", "endpoint", "--analysis", str(source)])
        == 1
    )
    output = capsys.readouterr().err
    assert output == "HTTP 403 for /v2/[REDACTED]/run\n"
    assert secret not in output and "example.com" not in output


@pytest.mark.parametrize("terminal", ["FAILED", "http_timeout", "client_timeout"])
def test_polling_failure_retains_job_details(monkeypatch, terminal):
    responses = iter(
        [
            {"id": "job-123", "status": "IN_QUEUE"},
            {"status": "IN_PROGRESS", "stream": [{"output": {"error": "root cause"}}]},
        ]
    )

    def respond(request):
        result = next(responses, None)
        if result is not None:
            return httpx.Response(200, json=result)
        if terminal == "http_timeout":
            raise httpx.ReadTimeout("secret", request=request)
        return httpx.Response(200, json={"status": "FAILED", "error": "root cause"})

    real_client = httpx.Client
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    # A dropped poll (http_timeout) is retried until the deadline, then reported like a timeout.
    clocks = {"client_timeout": [0, 0, 901], "http_timeout": [0, 0, 0, 901]}
    clock_values = iter(clocks.get(terminal, [0, 0, 0]))
    monkeypatch.setattr(client.time, "monotonic", lambda: next(clock_values))
    if terminal == "FAILED":
        result = client.call_endpoint("endpoint", {}, "secret", stream=True)
        assert result["id"] == "job-123" and result["status"] == "FAILED"
        assert result["error"] == "root cause"
    else:
        with pytest.raises(TimeoutError) as caught:
            client.call_endpoint("endpoint", {}, "secret", stream=True)
        message = str(caught.value)
        assert "job-123 still IN_PROGRESS" in message and "root cause" in message
        assert "secret" not in message


def test_client_exits_nonzero_when_completed_job_carries_error(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNPOD_API_KEY", "private-test-token")
    analysis = tmp_path / "analysis.json"
    analysis.write_text("{}")
    monkeypatch.setattr(
        client,
        "call_endpoint",
        lambda *a, **kw: {
            "id": "job-1",
            "status": "COMPLETED",
            "output": [{"error": "Model engine failed to start: boom"}],
        },
    )
    assert (
        client.main(["memo", "--endpoint-id", "id", "--analysis", str(analysis)]) == 1
    )


def test_rest_client_cold_start_polls_status(monkeypatch):
    import httpx

    responses = iter(
        [
            {"id": "cold-job", "status": "IN_QUEUE"},
            {"id": "cold-job", "status": "IN_PROGRESS"},
            {
                "id": "cold-job",
                "status": "COMPLETED",
                "output": [{"document": {}}],
                "delayTime": 1234,
                "executionTime": 5678,
            },
        ]
    )
    paths = []

    def respond(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=next(responses))

    real_client = httpx.Client
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    result = client.call_endpoint("endpoint", {"task": "extract"}, "secret")
    assert paths == [
        "/v2/endpoint/run",
        "/v2/endpoint/status/cold-job",
        "/v2/endpoint/status/cold-job",
    ]
    assert result["output"] == [{"document": {}}]
    assert result["delayTime"] == 1234
    assert result["executionTime"] == 5678
    assert "stream" not in result


@pytest.mark.parametrize("explicit", [False, True])
def test_generate(monkeypatch, explicit):
    data = {"prompt": " hi "}
    if explicit:
        data.update(task="generate", system="Custom", temperature=1.2, max_tokens=99)

    def fake(payload):
        assert payload["messages"] == [
            {
                "role": "system",
                "content": "Custom" if explicit else worker.GENERATE_SYSTEM,
            },
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        ]
        assert payload["temperature"] == (1.2 if explicit else 0.7)
        assert payload["max_tokens"] == (99 if explicit else 512)
        assert not payload["stream"] and "response_format" not in payload
        yield {
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"total_tokens": 2},
        }

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": data}))
    assert len(result) == 1
    assert result[0]["task"] == "generate" and result[0]["text"] == "Hello"
    assert result[0]["usage"] == {"total_tokens": 2}
    assert result[0]["gpu"] == "test GPU"
    assert result[0]["model"] == "Qwen/Qwen3-VL-8B-Instruct"
    assert set(result[0]["timing"]) == {"inference_s", "engine_load_s", "cold_start"}


def test_generate_stream(monkeypatch):
    def fake(payload):
        assert payload["stream_options"] == {"include_usage": True}
        for delta in ("Hello", " world"):
            yield {"choices": [{"delta": {"content": delta}}]}
        yield {"choices": [], "usage": {"total_tokens": 3}}

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": {"prompt": "hi", "stream": True}}))
    assert "".join(item["delta"] for item in result[:-1]) == "Hello world"
    assert result[-1]["done"] and result[-1]["task"] == "generate"
    assert result[-1]["text"] == "Hello world"
    assert result[-1]["usage"] == {"total_tokens": 3}
    assert result[-1]["gpu"] == "test GPU" and result[-1]["model"]
    assert "timing" in result[-1]


def test_stream_flush_chars(monkeypatch):
    monkeypatch.setattr(worker, "STREAM_FLUSH_CHARS", 5)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 0)

    def fake(payload):
        for delta in ("", "Hello", " world", "!", ""):
            yield {"choices": [{"delta": {"content": delta}}]}

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": {"task": "memo", "analysis": {}}}))
    chunks = [item["delta"] for item in result[:-1]]
    assert chunks == ["Hello", " world", "!"]
    assert all(len(chunk) >= 5 for chunk in chunks[:-1])
    assert all(chunks)
    assert "".join(chunks) == "Hello world!"
    assert result[-1]["done"]


def test_stream_flush_time(monkeypatch):
    monkeypatch.setattr(worker, "STREAM_FLUSH_CHARS", 64)
    monkeypatch.setattr(worker, "STREAM_FLUSH_SECONDS", 0.2)
    clock_values = iter((0, 0, 0.1, 0.2, 0.3, 0.4, 0.4))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock_values))

    def fake(payload):
        for delta in ("a", "b", "c", "d"):
            yield {"choices": [{"delta": {"content": delta}}]}

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": {"task": "memo", "analysis": {}}}))
    assert result[:-1] == [{"delta": "ab"}, {"delta": "cd"}]
    assert result[-1]["done"]


def test_stream_flush_before_done(monkeypatch):
    monkeypatch.setattr(worker, "STREAM_FLUSH_CHARS", 64)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 0)

    def fake(payload):
        yield {"choices": [{"delta": {"content": "tail"}}]}
        yield {"choices": [], "usage": {"total_tokens": 1}}

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.sync_handler({"input": {"prompt": "hi", "stream": True}}))
    assert len(result) == 2
    assert result[0] == {"delta": "tail"}
    assert result[1]["done"] and result[1]["text"] == "tail"
    assert result[1]["usage"] == {"total_tokens": 1}


def test_generate_image(monkeypatch):
    def fake(payload):
        parts = payload["messages"][1]["content"]
        assert parts[0] == {"type": "text", "text": "hi"}
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        yield {"choices": [{"message": {"content": "A page"}}]}

    monkeypatch.setattr(worker, "vllm_client", fake)
    data = {"prompt": "hi", "image_base64": base64.b64encode(png_bytes()).decode()}
    assert next(worker.sync_handler({"input": data}))["text"] == "A page"
    monkeypatch.setattr(worker, "MAX_INPUT_PIXELS", 10)
    assert "Could not read image" in next(worker.sync_handler({"input": data}))["error"]


@pytest.mark.parametrize(
    "fields",
    [
        {"task": "generate"},
        {"prompt": ""},
        {"prompt": "  "},
        {"prompt": 3},
        {"prompt": "x" * 20001},
        {"max_tokens": 4097},
        {"max_tokens": True},
        {"max_tokens": 0},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"temperature": True},
        {"temperature": "1"},
        {"temperature": float("nan")},
        {"stream": 1},
        {"system": "x" * 4001},
        {"system": None},
        {"image_base64": "%%%"},
        {"image_base64": base64.b64encode(b"GIF89a").decode()},
        {"image_base64": "A" * (20 * 1024 * 1024 + 4)},
    ],
)
def test_generate_invalid(monkeypatch, fields):
    monkeypatch.setattr(worker, "vllm_client", lambda _: pytest.fail("no model call"))
    data = {"prompt": "hi", **fields}
    if fields == {"task": "generate"}:
        del data["prompt"]
    result = list(worker.sync_handler({"input": data}))
    assert len(result) == 1 and result[0]["error"]


@pytest.mark.parametrize("stream", [False, True])
def test_client_generate(monkeypatch, tmp_path, capsys, stream):
    monkeypatch.setenv("RUNPOD_API_KEY", "secret-key")
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes())

    def fake(endpoint, payload, key, **kwargs):
        assert endpoint == "id" and key == "secret-key"
        assert payload == {
            "task": "generate",
            "prompt": "hi",
            "system": "Custom",
            "temperature": 0.5,
            "max_tokens": 100,
            "stream": stream,
            "image_base64": base64.b64encode(png_bytes()).decode(),
        }
        assert kwargs["stream"] == stream
        return {"status": "COMPLETED", "output": [{"text": "secret-key"}]}

    monkeypatch.setattr(client, "call_endpoint", fake)
    args = [
        "generate",
        "--endpoint-id",
        "id",
        "--prompt",
        "hi",
        "--system",
        "Custom",
        "--image",
        str(image),
        "--temperature",
        "0.5",
        "--max-tokens",
        "100",
    ]
    assert client.main(args + (["--stream"] if stream else [])) == 0
    captured = capsys.readouterr()
    assert "secret-key" not in captured.out + captured.err
    assert json.loads(captured.out)["result"]["output"][0]["text"] == "[REDACTED]"


def test_baked_dockerfile_shared_steps():
    from pathlib import Path

    standard = Path("worker/Dockerfile").read_text()
    baked = Path("worker/Dockerfile.baked").read_text()
    for line in standard.splitlines():
        if line.startswith(("RUN ", "COPY ", "ENTRYPOINT ", "CMD ", "WORKDIR ")):
            assert line in baked.splitlines()
    assert "ENV MODEL_PATH=/models/model HF_HUB_OFFLINE=1" in baked
    assert baked.index("snapshot_download") < baked.index("COPY ")


@pytest.mark.parametrize("stream", [False, True])
def test_generate_engine_and_request_errors(monkeypatch, stream):
    data = {"prompt": "hi", "stream": stream}
    monkeypatch.setitem(worker.ENGINE, "status", "failed")
    monkeypatch.setitem(worker.ENGINE, "error", "unavailable")
    assert list(worker.sync_handler({"input": data})) == [
        {"error": "Model engine failed to start: unavailable"}
    ]
    monkeypatch.setitem(worker.ENGINE, "status", "disabled")

    def fail(payload):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(worker, "vllm_client", fail)
    assert list(worker.sync_handler({"input": data})) == [
        {"error": "Model request failed: unavailable"}
    ]


def test_follow_stderr():
    import tempfile
    import threading

    out = io.StringIO()
    lines = []
    stop = threading.Event()
    with tempfile.TemporaryFile() as file:
        thread = threading.Thread(
            target=worker.follow_stderr,
            args=(file.fileno(), out, stop, lines.append),
            kwargs={"poll": 0.01},
        )
        thread.start()
        file.write(b"first\nWARNING second\n")
        file.flush()
        position = file.tell()
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert file.tell() == position
    assert out.getvalue() == "first\nWARNING second\n"
    assert lines == ["first", "WARNING second"]


@pytest.mark.parametrize(
    "content",
    [
        b"".join(f"line {i}\n".encode() for i in range(100)),
        b"x" * 70000 + b"\nlast line\n",
    ],
)
def test_engine_failure_tail_is_bounded(monkeypatch, capsys, content):
    class Process:
        def poll(self):
            return 1

    def launch(command, **kwargs):
        kwargs["stderr"].write(content)
        kwargs["stderr"].flush()
        return Process()

    monkeypatch.setattr(worker, "resolve_model", lambda: ("org/model", "download"))
    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError) as exc:
        worker.start_engine()
    expected = "\n".join(content[-65536:].decode().splitlines()[-80:])
    assert str(exc.value) == "vLLM exited early:\n" + expected
    assert capsys.readouterr().err == content.decode()
    worker._STDERR.close()
