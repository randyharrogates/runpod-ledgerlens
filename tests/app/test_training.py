"""Budget guardrails and Pod lifecycle, with every subprocess mocked."""

import copy
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.backend import datasets, main, pod_runner, training
from ledgerlens.insights import expand_page_index


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(training, "ROOT", tmp_path / "runs")
    monkeypatch.setattr(datasets, "ROOT", tmp_path / "uploads")
    monkeypatch.setenv("DEMO_MODE", "1")
    training.TOKENS.clear()
    monkeypatch.setattr(
        pod_runner.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "[]", ""),
    )
    monkeypatch.setattr(
        training,
        "stock",
        lambda: ("NVIDIA RTX PRO 4500 Blackwell", 0.72, "Available", True),
    )
    yield TestClient(main.app)
    runners = list(training.RUNNERS.values())
    for runner in runners:
        runner.cancel()
    deadline = time.monotonic() + 2
    while training.RUNNERS and time.monotonic() < deadline:
        time.sleep(0.01)


def make_plan(client, **kwargs):
    return client.post(
        "/api/training/plan", json={"preset": "full", "cap_usd": 10, **kwargs}
    )


def wait_done(client, identity):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = client.get(f"/api/training/runs/{identity}").json()
        if value["status"] in pod_runner.TERMINAL:
            return value
        time.sleep(0.01)
    pytest.fail("Runner did not finish")


def test_math_and_index(client):
    full, quick = client.get("/api/training/presets").json()
    assert full["total_minutes"] == 351
    assert full["total_cost"] == pytest.approx(4.212)
    assert full["suggested_cap_usd"] == 7
    assert quick["total_minutes"] == 180
    assert quick["total_cost"] == 2.70
    assert quick["env"] == {
        "N_BUSINESSES": "20",
        "STOP_AFTER": "predict-ft",
        "PREDICT_LIMIT": "3",
    }
    assert "9 Oct rehearsal" in quick["measurement_note"]
    assert "report" not in [s["id"] for s in quick["stages"]]
    assert all(
        p["estimated"] and all(s["estimated"] for s in p["stages"])
        for p in (full, quick)
    )
    path = Path("eval/page_index.json")
    assert path.stat().st_size < 600_000
    assert len(expand_page_index(json.loads(path.read_text()))["pages"]) == 4475
    assert client.get("/api/dataset/pages").json()["total"] == 4475


@pytest.mark.parametrize("cap", [0, -1, 25.01, "NaN"])
def test_caps(client, cap):
    assert make_plan(client, cap_usd=cap).status_code == 422


def test_tokens_and_active(client):
    assert (
        client.post("/api/training/runs", json={"confirm_token": "bad"}).status_code
        == 422
    )
    response = make_plan(client).json()
    token = response["confirm_token"]
    expires, value = training.TOKENS[token]
    training.TOKENS[token] = (time.time() - 1, value)
    assert (
        client.post("/api/training/runs", json={"confirm_token": token}).status_code
        == 422
    )
    token = make_plan(client).json()["confirm_token"]
    started = client.post("/api/training/runs", json={"confirm_token": token})
    assert started.status_code == 200
    assert started.json()["simulated"]
    assert (
        client.post("/api/training/runs", json={"confirm_token": token}).status_code
        == 422
    )
    assert make_plan(client).status_code == 409
    identity = started.json()["id"]
    assert client.post(f"/api/training/runs/{identity}/cancel").json()["simulated"]
    assert wait_done(client, identity)["status"] == "cancelled"
    history = client.get("/api/training/runs").json()
    assert history[0]["id"] == "reported"
    assert history[1]["simulated"]
    assert client.post("/api/training/runs/reported/cancel").status_code == 409


def test_fake_done_and_cap(client, monkeypatch):
    original = pod_runner.FakeRunner
    monkeypatch.setattr(
        training, "FakeRunner", lambda *args: original(*args, speed=0.001)
    )
    for cap, status in [(10, "done"), (0.00001, "cap_reached")]:
        token = make_plan(client, cap_usd=cap).json()["confirm_token"]
        started = client.post(
            "/api/training/runs", json={"confirm_token": token}
        ).json()
        value = wait_done(client, started["id"])
        assert value["status"] == status
        assert value["simulated"]
        if status == "done":
            assert value["result_summary"]["simulated"]
            assert (
                training.ROOT / started["id"] / "eval/training_summary.json"
            ).is_file()
        else:
            assert any("spending limit reached" in line for line in value["logs"])


def test_dataset_and_mode_validation(client, monkeypatch):
    assert make_plan(client, dataset="missing").status_code == 404
    monkeypatch.delenv("DEMO_MODE")
    monkeypatch.delenv("TRAINING_ENABLED", raising=False)
    assert make_plan(client).json()["simulated"]
    token = make_plan(client).json()["confirm_token"]
    monkeypatch.setenv("TRAINING_ENABLED", "1")
    assert (
        client.post("/api/training/runs", json={"confirm_token": token}).status_code
        == 409
    )
    monkeypatch.setattr(
        training, "stock", lambda: (None, 0.72, "GPU stock could not be read", False)
    )
    value = make_plan(client).json()
    assert "could not be read" in value["stock"]
    assert (
        client.post(
            "/api/training/runs", json={"confirm_token": value["confirm_token"]}
        ).status_code
        == 409
    )
    monkeypatch.setenv("DEMO_MODE", "1")
    assert make_plan(client).json()["simulated"]


def test_stock(monkeypatch):
    def gpu(identity, status, price):
        return {
            "id": identity,
            "securePricePerHr": price,
            "dataCenterAvailability": [
                {"dataCenterId": "EU-RO-1", "stockStatus": status}
            ],
        }

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                [
                    gpu("NVIDIA RTX 5090", "High", 0.9),
                    gpu("NVIDIA RTX PRO 4500 Blackwell", "Low", 0.72),
                ]
            ),
            "",
        )

    monkeypatch.setattr(training.subprocess, "run", run)
    assert training.stock()[:2] == ("NVIDIA RTX PRO 4500 Blackwell", 0.72)
    assert calls == [["runpodctl", "gpu", "list", "--include-unavailable"]]
    monkeypatch.setattr(
        training.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "broken", ""),
    )
    assert not training.stock()[3]


def runner_state():
    return {
        "id": "test123",
        "preset": "full",
        "dataset": "default",
        "cap_usd": 10,
        "gpu": "NVIDIA RTX PRO 4500 Blackwell",
        "price_per_hour": 0.72,
        "env": {"N_BUSINESSES": "400"},
        "simulated": False,
        "status": "queued",
        "spend": 0,
        "estimate": training.presets()[0],
    }


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "quick",
        "failure",
        "cap",
        "cancel",
        "delete-failure",
        "disk",
        "create-failure",
        "timeout",
    ],
)
def test_real_runner(tmp_path, monkeypatch, mode):
    state = runner_state()
    if mode == "quick":
        state["env"] = training.presets()[1]["env"]
    folder = tmp_path / "run"
    folder.mkdir()
    calls = []
    runner = pod_runner.RealRunner(
        state, folder, lambda **values: state.update(values), poll_seconds=0.001
    )

    def run(args, **kwargs):
        assert isinstance(args, list)
        assert "shell" not in kwargs
        assert kwargs["timeout"] > 0
        calls.append(copy.deepcopy(args))
        output, code = "", 0
        if args[:3] == ["runpodctl", "pod", "create"]:
            if mode == "timeout":
                raise subprocess.TimeoutExpired(args, 660, output='{"id":"pod123456"}')
            output = json.dumps(
                {
                    "id": "pod123456",
                    "publicIp": "192.0.2.1",
                    "portMappings": {"22": 12345},
                }
            )
            code = 1 if mode == "create-failure" else 0
        elif args[:3] == ["runpodctl", "pod", "list"]:
            output = json.dumps(
                [{"id": "pod123456", "status": "EXITED"}]
                if mode == "delete-failure"
                else []
            )
        elif args[0] == "ssh":
            command = args[-1]
            if command == "df -BG /workspace":
                output = f"Filesystem 1G-blocks Used Available Use% Mounted on\n/dev/x 100G 10G {'10' if mode == 'disk' else '90'}G 10% /workspace\n"
            elif "setsid nohup" in command:
                if mode == "cap":
                    runner.created_at = time.time() - 100_000
                elif mode == "cancel":
                    runner.cancel()
            elif command.startswith("ls "):
                output = (
                    " ".join(pod_runner.STAGES[:-1])
                    if mode == "quick"
                    else " ".join(pod_runner.STAGES)
                    if mode in ("success", "delete-failure")
                    else "generate"
                )
            elif command.startswith("pgrep"):
                code = 1
            elif command.startswith("tail"):
                output = "token=do-not-show-this\nprogress"
        elif args[0] == "scp" and args[-2].endswith("/eval"):
            (folder / "eval").mkdir()
            (folder / "eval/results.json").write_text("{}")
        return subprocess.CompletedProcess(args, code, output, "")

    monkeypatch.setattr(pod_runner.subprocess, "run", run)
    runner.run()
    expected = {
        "success": "done",
        "quick": "done",
        "failure": "failed",
        "cap": "cap_reached",
        "cancel": "cancelled",
        "disk": "failed",
        "create-failure": "failed",
        "timeout": "failed",
    }
    if mode == "delete-failure":
        assert (
            state["status"]
            == "POD MAY STILL BE RUNNING: delete pod123456 in the Runpod console"
        )
        assert sum(a[:3] == ["runpodctl", "pod", "delete"] for a in calls) == 3
    else:
        assert state["status"] == expected[mode]
        assert state["pod_deleted"]
    assert ["runpodctl", "pod", "delete", "pod123456"] in calls
    assert ["runpodctl", "pod", "list", "--all", "-o", "json"] in calls
    assert calls[0] == ["runpodctl", "pod", "list", "-o", "json"]
    assert calls[1] == [
        "runpodctl",
        "pod",
        "create",
        "--name",
        "ledgerlens-train-test123",
        "--gpu-id",
        "NVIDIA RTX PRO 4500 Blackwell",
        "--data-center-ids",
        "EU-RO-1",
        "--network-volume-id",
        "0kelufi0wo",
        "--volume-mount-path",
        "/workspace",
        "--image",
        "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
        "--container-disk-in-gb",
        "60",
        "--min-cuda-version",
        "13.0",
        "--ports",
        "22/tcp",
        "--wait",
        "--wait-timeout",
        "10m",
    ]
    if mode not in ("disk", "create-failure", "timeout"):
        detached = next(
            a[-1] for a in calls if a[0] == "ssh" and "setsid nohup" in a[-1]
        )
        assert (
            "RUN=/workspace/runs/test123 GPU_PRICE=0.72 N_BUSINESSES=20 STOP_AFTER=predict-ft PREDICT_LIMIT=3 setsid nohup"
            if mode == "quick"
            else "RUN=/workspace/runs/test123 GPU_PRICE=0.72 N_BUSINESSES=400 setsid nohup"
        ) in detached
        assert "RUN=/workspace/ledgerlens " not in detached
    if mode == "quick":
        assert state["result_summary"] == {
            "simulated": False,
            "quick_check": True,
            "stages_completed": list(pod_runner.STAGES[:-1]),
            "predictions_copied": True,
        }
        assert not any(a[0] == "ssh" and a[-1].startswith("pgrep") for a in calls)
        assert any(a[0] == "scp" and a[-2].endswith("/predictions") for a in calls)
        assert any(
            a[0] == "scp" and a[-2].endswith("/main/train_summary.json") for a in calls
        )
    assert "do-not-show-this" not in (folder / "log.txt").read_text()


def test_uploaded_marker(tmp_path, monkeypatch):
    folder = tmp_path / "run"
    folder.mkdir()
    upload = tmp_path / "uploads/abc"
    upload.mkdir(parents=True)
    for name in ("page.json", "page.meta.json", "page.html"):
        (upload / name).write_text("{}")
    (upload / ".validation.json").write_text('{"valid":true}')
    monkeypatch.setattr(datasets, "ROOT", tmp_path / "uploads")
    state = runner_state()
    state["dataset"] = "abc"
    runner = pod_runner.RealRunner(state, folder, lambda **v: state.update(v))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        text = ""
        if args[:3] == ["runpodctl", "pod", "create"]:
            text = (
                '{"id":"pod123456","publicIp":"192.0.2.1","portMappings":{"22":1234}}'
            )
        elif args[:3] == ["runpodctl", "pod", "list"]:
            text = "[]"
        elif args[0] == "ssh":
            if args[-1] == "df -BG /workspace":
                text = "header\n/dev/x 100G 10G 90G 10% /workspace"
            elif "setsid nohup" in args[-1]:
                runner.cancel()
        return subprocess.CompletedProcess(args, 0, text, "")

    monkeypatch.setattr(pod_runner.subprocess, "run", run)
    runner.run()
    commands = [a[-1] for a in calls if a[0] == "ssh"]
    assert (
        "mkdir -p /workspace/runs/test123/done && touch /workspace/runs/test123/done/generate"
        in commands
    )
    assert not any("train.render_pod" in c for c in commands)
    assert any(a[0] == "scp" and a[-1].endswith("/synth/page.json") for a in calls)
    assert state["status"] == "cancelled"
    assert state["pod_deleted"]


def test_sanitizer(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "short-secret")
    text = pod_runner.sanitize(
        "short-secret token=abc Bearer hidden rpa_abcdefghijklmnopqrstuvwxyz123456789 192.0.2.1:1234 -----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----"
    )
    assert "short-secret" not in text and "hidden" not in text and "secret" not in text
    assert "192.0.2.1:1234" in text


def test_active_index_and_dataset_validation(client):
    state = runner_state()
    state.update(id="orphan", created="2026-10-11", status="starting")
    training.atomic_json(training.ROOT / "orphan/run.json", state)
    training.atomic_json(training.ROOT / "index.json", ["orphan"])
    assert make_plan(client).status_code == 409
    state["status"] = "POD MAY STILL BE RUNNING: delete pod123456 in the Runpod console"
    training.atomic_json(training.ROOT / "orphan/run.json", state)
    assert make_plan(client).status_code == 409
    state["status"] = "failed"
    training.atomic_json(training.ROOT / "orphan/run.json", state)
    assert make_plan(client, cap_usd=25).status_code == 200
    training.atomic_json(datasets.ROOT / "bad/.validation.json", {"valid": False})
    assert make_plan(client, dataset="bad").status_code == 422


def test_two_plans_cannot_start_two_runs(client):
    first = make_plan(client).json()["confirm_token"]
    second = make_plan(client).json()["confirm_token"]
    assert (
        client.post("/api/training/runs", json={"confirm_token": first}).status_code
        == 200
    )
    assert (
        client.post("/api/training/runs", json={"confirm_token": second}).status_code
        == 409
    )


def test_log_tail_and_secret_redaction(client, monkeypatch):
    state = runner_state()
    state.update(id="example", created="2026-10-11", status="done")
    training.atomic_json(training.ROOT / "example/run.json", state)
    secret = "test-sensitive-value"
    monkeypatch.setenv("RUNPOD_API_KEY", secret)
    (training.ROOT / "example/log.txt").write_text(
        "\n".join([str(i) for i in range(250)] + [secret, '"token": "tiny"'])
    )
    logs = client.get("/api/training/runs/example").json()["logs"]
    assert len(logs) == 200
    assert secret not in " ".join(logs)
    assert "tiny" not in " ".join(logs)


def test_stock_fallback_region_and_price(monkeypatch):
    gpus = [
        {
            "id": "NVIDIA RTX PRO 4500 Blackwell",
            "securePricePerHr": 0.72,
            "dataCenterAvailability": [
                {"dataCenterId": "US-CA-1", "stockStatus": "High"}
            ],
        },
        {
            "id": "NVIDIA RTX 5090",
            "securePricePerHr": 1.0,
            "dataCenterAvailability": [
                {"dataCenterId": "EU-RO-1", "stockStatus": "Medium"}
            ],
        },
    ]
    monkeypatch.setattr(
        training.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, json.dumps({"gpus": gpus}), ""
        ),
    )
    assert training.stock()[:2] == ("NVIDIA RTX 5090", 1)
    gpus[1]["dataCenterAvailability"][0]["stockStatus"] = "None"
    assert not training.stock()[3]


def test_shutdown_cleanup(tmp_path, monkeypatch):
    state = runner_state()
    runner = pod_runner.RealRunner(state, tmp_path, lambda **v: state.update(v))
    runner.pod_id = "pod123456"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(pod_runner.subprocess, "run", run)
    pod_runner.LIVE.add(runner)
    try:
        pod_runner.shutdown()
    finally:
        pod_runner.LIVE.discard(runner)
    assert runner.cancelled.is_set()
    assert runner.cleaned
    assert ["runpodctl", "pod", "delete", "pod123456"] in calls


def test_cap_during_blocking_copy(tmp_path, monkeypatch):
    state = runner_state()
    state["cap_usd"] = 0.000001
    runner = pod_runner.RealRunner(state, tmp_path, lambda **v: state.update(v))
    runner.pod_id = "pod123456"
    runner.created_at = time.time() - 10
    runner.create_attempted = True
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(pod_runner.subprocess, "run", run)
    runner.guard()
    assert runner.cleaned
    assert runner.outcome == "cap_reached"
    assert "spending limit reached" in (tmp_path / "log.txt").read_text()
    assert ["runpodctl", "pod", "delete", "pod123456"] in calls


@pytest.mark.parametrize(
    "output",
    [
        [
            {
                "id": "other123",
                "name": "other-job",
                "desiredStatus": "RUNNING",
                "networkVolumeId": "0kelufi0wo",
            }
        ],
        [{"id": "other123", "name": "ledgerlens-rehearsal", "status": "STARTING"}],
        "unreadable",
        [{"id": "unknown"}],
    ],
)
def test_volume_refusal_in_plan_and_runner(client, tmp_path, monkeypatch, output):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args == ["runpodctl", "pod", "list", "-o", "json"]
        return subprocess.CompletedProcess(args, 0, json.dumps(output), "")

    monkeypatch.setattr(pod_runner.subprocess, "run", run)
    response = make_plan(client)
    assert response.status_code == 409
    if isinstance(output, list) and "name" in output[0]:
        assert output[0]["name"] in response.json()["detail"]
        assert "Wait for it to finish" in response.json()["detail"]
    else:
        assert "could not be read" in response.json()["detail"]
    state = runner_state()
    runner = pod_runner.RealRunner(state, tmp_path, lambda **v: state.update(v))
    runner.run()
    assert state["status"] == "failed"
    assert not runner.create_attempted
    assert len(calls) == 2


def test_volume_list_failure(client, monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(pod_runner.subprocess, "run", run)
    assert make_plan(client).status_code == 409


def test_fake_quick_and_measured_plan(client, monkeypatch):
    original = pod_runner.FakeRunner
    monkeypatch.setattr(training, "FakeRunner", lambda *args: original(*args, speed=0))
    monkeypatch.setattr(training, "stock", lambda: ("GPU", 1.5, "Available", True))
    plan = make_plan(client, preset="quick").json()
    assert plan["estimate"]["total_cost"] == 2.70
    assert "rejects" not in plan["warning"]
    started = client.post(
        "/api/training/runs", json={"confirm_token": plan["confirm_token"]}
    ).json()
    value = wait_done(client, started["id"])
    assert value["stage"] == "predict-ft"
    assert value["result_summary"] == {
        "simulated": True,
        "quick_check": True,
        "stages_completed": list(pod_runner.STAGES[:-1]),
        "predictions_copied": False,
    }
    assert not (training.ROOT / started["id"] / "eval").exists()


def test_uploaded_plan_dataset_note(client):
    training.atomic_json(datasets.ROOT / "uploaded/.validation.json", {"valid": True})
    value = make_plan(client, dataset="uploaded").json()
    assert "CORD v2" in value["dataset_note"]
    assert "always mixed in" in value["estimate"]["dataset_note"]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /workspace/ledgerlens/main/merged",
        "ls /workspace/ledgerlens",
        "cp x /workspace/ledgerlens;",
        "root@host:/workspace/ledgerlens/eval",
    ],
)
def test_live_model_folder_is_protected(command):
    with pytest.raises(RuntimeError, match="Refusing to touch"):
        pod_runner.protect(command)


def test_run_bundle_and_runs_folder_are_allowed():
    pod_runner.protect("git fetch /workspace/ledgerlens-test123.bundle HEAD")
    pod_runner.protect("ls /workspace/runs/test123/done")


def test_run_folder_must_be_fresh(tmp_path):
    state = runner_state()
    state["id"] = "../ledgerlens"
    with pytest.raises(ValueError):
        pod_runner.RealRunner(state, tmp_path, lambda **_: None)
