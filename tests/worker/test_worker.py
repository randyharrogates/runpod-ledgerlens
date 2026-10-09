import base64
import json
import os
import subprocess
import sys

import httpx
import pytest

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


def extract_input():
    return {
        "task": "extract",
        "doc_type": "statement",
        "image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(),
    }


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
    result = list(worker.handler({"input": data}))
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
    result = list(worker.handler({"input": extract_input()}))[0]
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
    result = list(worker.handler({"input": extract_input()}))[0]
    assert not result["valid"] and result["raw"] == raw
    assert result["document"] is None and result["validation_error"]


def test_memo(monkeypatch):
    def fake(payload):
        assert payload["stream"] and payload["stream_options"]["include_usage"]
        yield {"choices": [{"delta": {"content": "Summary"}}]}
        yield {"choices": [{"delta": {"content": " [p1:r2]"}}]}
        yield {"choices": [], "usage": {"total_tokens": 8}}

    monkeypatch.setattr(worker, "vllm_client", fake)
    result = list(worker.handler({"input": {"task": "memo", "analysis": {}}}))
    assert result[:2] == [{"delta": "Summary"}, {"delta": " [p1:r2]"}]
    assert result[2]["done"] and result[2]["usage"] == {"total_tokens": 8}


def test_cold_start_and_health(monkeypatch):
    monkeypatch.setattr(
        worker, "vllm_client", lambda _: pytest.fail("health calls no model")
    )
    assert list(worker.handler({"input": {"task": "health"}}))[0]["cold_start"]
    assert not list(worker.handler({"input": {"task": "health"}}))[0]["cold_start"]


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


def test_engine_early_exit(monkeypatch):
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


def test_rest_client_runsync(monkeypatch):
    def respond(request):
        assert request.url.path == "/v2/endpoint/runsync"
        assert json.loads(request.content)["input"]["task"] == "extract"
        return httpx.Response(
            200, json={"status": "COMPLETED", "output": {"valid": True}}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = client.call_endpoint("endpoint", {"task": "extract"}, "secret")
    assert result["output"]["valid"]
