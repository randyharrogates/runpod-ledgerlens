"""Training runners. Only RealRunner can mutate GPU resources."""

import atexit
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGES = (
    "generate",
    "dataset",
    "smoke",
    "main",
    "predict-8b",
    "predict-4b",
    "predict-ft",
    "report",
)
TERMINAL = {"done", "failed", "cancelled", "cap_reached"}
LIVE = set()
LIVE_LOCK = threading.Lock()


def sanitize(value):
    text = str(value)
    for name, secret in os.environ.items():
        if (
            any(k in name.upper() for k in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            and len(secret) >= 6
        ):
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
        "[REDACTED]",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"(?i)(?:bearer\s+|(?:api[_-]?key|token|password|secret)[\"']?\s*[=:]\s*[\"']?)[^\s\"',}]+",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"\brpa_[A-Za-z0-9_-]+", "[REDACTED]", text)
    return re.sub(r"\b[A-Za-z0-9_+/=-]{32,}\b", "[REDACTED]", text)


def sanitize_data(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.search(r"(?i)(token|secret|password|api.?key|private.?key)", k)
            else sanitize_data(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize_data(v) for v in value]
    return sanitize(value) if isinstance(value, str) else value


class FakeRunner:
    def __init__(self, state, folder, update, speed=1 / 60):
        self.state, self.folder, self.update, self.speed = state, folder, update, speed
        self.cancelled = threading.Event()

    def log(self, message):
        with (self.folder / "log.txt").open("a") as file:
            file.write(sanitize(message) + "\n")

    def cancel(self):
        self.cancelled.set()

    def run(self):
        self.update(status="starting")
        try:
            self._run()
        finally:
            self.log("Pod deletion verified (simulated)")

    def _run(self):
        if self.cancelled.is_set():
            self.update(status="cancelled")
            return
        self.update(status="running")
        elapsed = 0
        stages = self.state["estimate"]["stages"]
        stop = self.state["env"].get("STOP_AFTER")
        if stop:
            stages = stages[
                : next(i for i, s in enumerate(stages) if s["id"] == stop) + 1
            ]
        for index, stage in enumerate(stages):
            self.update(stage=stage["id"])
            self.log(f"starting {stage['id']} (simulated)")
            end = time.monotonic() + stage["minutes"] * self.speed
            while time.monotonic() < end:
                if self.cancelled.wait(0.01):
                    self.update(status="cancelled")
                    self.log("cancelled (simulated)")
                    return
                elapsed += 0.01 / max(self.speed, 0.000001) * 60
                spend = elapsed * self.state["price_per_hour"] / 3600
                self.update(elapsed=elapsed, spend=spend)
                if spend >= self.state["cap_usd"]:
                    self.log("spending limit reached (simulated)")
                    self.update(status="cap_reached")
                    return
            self.log(f"completed {stage['id']} (simulated)")
            self.update(progress=(index + 1) / len(stages))
        if stop:
            self.update(
                status="done",
                result_summary={
                    "simulated": True,
                    "quick_check": True,
                    "stages_completed": [s["id"] for s in stages if s["id"] in STAGES],
                    "predictions_copied": False,
                },
            )
            return
        (self.folder / "eval").mkdir(exist_ok=True)
        for name in ("summary.json", "training_summary.json"):
            shutil.copyfile(ROOT / "eval" / name, self.folder / "eval" / name)
        self.update(
            status="done",
            result_summary={
                "simulated": True,
                "evaluation": sanitize_data(
                    json.loads((self.folder / "eval/summary.json").read_text())
                ),
                "training": sanitize_data(
                    json.loads((self.folder / "eval/training_summary.json").read_text())
                ),
            },
        )


def json_output(text):
    # CLI may add a readiness message before its JSON object.
    for i, char in enumerate(text):
        if char in "[{":
            try:
                return json.JSONDecoder().raw_decode(text[i:])[0]
            except ValueError:
                pass
    raise ValueError("CLI did not return JSON")


def assert_volume_idle(command=None):
    try:
        result = (command or subprocess.run)(
            ["runpodctl", "pod", "list", "-o", "json"],
            timeout=20,
            **(
                {} if command else {"capture_output": True, "text": True, "check": True}
            ),
        )
        if result.returncode:
            raise ValueError
        output = json_output(result.stdout)
        pods = output if isinstance(output, list) else output["pods"]
        if not isinstance(pods, list):
            raise ValueError
        for pod in pods:
            if not isinstance(pod, dict) or not any(
                k in pod for k in ("desiredStatus", "status", "state")
            ):
                raise ValueError
            active = any(
                str(pod.get(k, "")).upper()
                in ("RUNNING", "STARTING", "PENDING", "CREATED")
                for k in ("desiredStatus", "status", "state")
            )
            volume = pod.get("networkVolumeId") or (pod.get("networkVolume") or {}).get(
                "id"
            )
            if active and (
                volume == "0kelufi0wo"
                or str(pod.get("name", "")).lower().startswith("ledgerlens")
            ):
                name = sanitize(pod.get("name") or pod.get("id") or "unknown Pod")
                raise RuntimeError(
                    f"Another GPU is using the training volume right now ({name}). Wait for it to finish."
                )
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(
            "The Pod list could not be read. Wait until the training volume can be checked."
        ) from None


def protect(command):
    # The live fine-tuned endpoint loads its weights from /workspace/ledgerlens/main/merged on the
    # shared volume, and that folder also holds the reported run: never touch it.
    if re.search(r"/workspace/ledgerlens(?:/|$|[\s'\";&|)])", command):
        raise RuntimeError(
            "Refusing to touch /workspace/ledgerlens (the live model and reported run)"
        )


class RealRunner(FakeRunner):
    def __init__(self, state, folder, update, poll_seconds=60):
        super().__init__(state, folder, update)
        self.poll_seconds = poll_seconds
        self.pod_id = None
        self.created_at = None
        self.host = self.port = None
        self.cleanup_lock = threading.Lock()
        self.cleaned = False
        self.finished = threading.Event()
        self.guard_thread = None
        self.outcome = "failed"
        self.create_attempted = False
        self.remote = f"/workspace/runs/{state['id']}"
        if not re.fullmatch(r"/workspace/runs/[A-Za-z0-9_-]+", self.remote):
            raise ValueError("Run folder must be a fresh folder under /workspace/runs")

    def command(self, args, timeout=120, check=True):
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, cwd=ROOT
        )
        if check and result.returncode:
            raise RuntimeError("Remote command failed")
        return result

    def account(self):
        if self.created_at is not None:
            elapsed = time.time() - self.created_at
            self.update(
                elapsed=elapsed, spend=elapsed * self.state["price_per_hour"] / 3600
            )

    def check(self):
        self.account()
        if self.cancelled.is_set():
            raise InterruptedError("cancelled")
        if self.state["spend"] >= self.state["cap_usd"]:
            raise InterruptedError("cap_reached")

    def guard(self):
        # Setup/copy commands can block too: enforce the cap while they run.
        while not self.finished.wait(0.1):
            try:
                self.check()
            except InterruptedError as exc:
                self.outcome = str(exc)
                self.update(status="cleaning-up")
                if str(exc) == "cap_reached":
                    self.log("spending limit reached")
                if not self.pod_id:
                    self.find_pod()
                self.cleanup()
                if self.cleaned:
                    return
                self.finished.wait(1)

    def ssh_options(self):
        return [
            "-i",
            str(Path.home() / ".runpod/ssh/runpodctl-ssh-key"),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={self.folder / 'known_hosts'}",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
        ]

    def ssh(self, command, timeout=120, check=True):
        protect(command)
        self.check()
        result = self.command(
            [
                "ssh",
                *self.ssh_options(),
                "-p",
                str(self.port),
                f"root@{self.host}",
                command,
            ],
            timeout,
            False,
        )
        self.check()
        if check and result.returncode:
            raise RuntimeError("Remote command failed")
        return result

    def scp(self, source, target):
        protect(source)
        protect(target)
        self.check()
        result = self.command(
            ["scp", *self.ssh_options(), "-P", str(self.port), "-r", source, target],
            timeout=600,
            check=False,
        )
        self.check()
        if result.returncode:
            raise RuntimeError("Copy failed")

    def record_pod(self, output):
        try:
            pod = json_output(output)
            pod = pod.get("pod", pod)
            identity = pod.get("id")
        except (ValueError, AttributeError):
            pod = {}
            match = re.search(r"(?i)pod(?:\s+id)?[\s:=]+([a-z0-9]{8,})", output)
            identity = match[1] if match else None
        if identity and re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            self.pod_id = identity
            if self.created_at is None:
                self.created_at = time.time()
            self.update(pod_id=identity, pod_created=self.created_at)
        return pod

    def connection(self, pod):
        host = pod.get("publicIp")
        mapping = pod.get("portMappings", {})
        port = mapping.get("22", mapping.get("22/tcp"))
        for item in (pod.get("runtime") or {}).get("ports", []):
            if item.get("privatePort") == 22 and item.get("isIpPublic", True):
                host, port = item.get("ip"), item.get("publicPort")
        if (
            not host
            or not re.fullmatch(r"[A-Za-z0-9.:_-]+", host)
            or not str(port).isdigit()
            or not 0 < int(port) < 65536
        ):
            raise ValueError("SSH connection was not available")
        self.host, self.port = host, int(port)

    def find_pod(self):
        try:
            output = json_output(
                self.command(
                    [
                        "runpodctl",
                        "pod",
                        "list",
                        "--all",
                        "--name",
                        f"ledgerlens-train-{self.state['id']}",
                        "-o",
                        "json",
                    ],
                    timeout=20,
                ).stdout
            )
            pods = output if isinstance(output, list) else output["pods"]
            if len(pods) == 1:
                return self.record_pod(json.dumps(pods[0]))
        except Exception:
            pass
        return None

    def cleanup(self):
        with self.cleanup_lock:
            if self.cleaned or not self.pod_id:
                return
            for _ in range(3):
                try:
                    self.command(
                        ["runpodctl", "pod", "delete", self.pod_id],
                        timeout=60,
                        check=False,
                    )
                    output = json_output(
                        self.command(
                            ["runpodctl", "pod", "list", "--all", "-o", "json"],
                            timeout=30,
                        ).stdout
                    )
                    pods = output if isinstance(output, list) else output["pods"]
                    if not isinstance(pods, list):
                        raise ValueError
                    if not any(p.get("id") == self.pod_id for p in pods):
                        self.cleaned = True
                        self.account()
                        self.update(pod_deleted=True)
                        self.log("Pod deletion verified")
                        return
                except Exception:
                    pass
            self.update(
                status=f"POD MAY STILL BE RUNNING: delete {self.pod_id} in the Runpod console"
            )
            self.log(self.state["status"])

    def run(self):
        with LIVE_LOCK:
            LIVE.add(self)
        try:
            self.update(status="starting", stage="pod-start-copy")
            self.log("starting pod-start-copy")
            self.check()
            args = [
                "runpodctl",
                "pod",
                "create",
                "--name",
                f"ledgerlens-train-{self.state['id']}",
                "--gpu-id",
                self.state["gpu"],
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
            assert_volume_idle(self.command)
            create_started = time.time()
            self.created_at = create_started
            self.create_attempted = True
            self.update(pod_creation_attempted=True, pod_created=create_started)
            self.guard_thread = threading.Thread(target=self.guard, daemon=True)
            self.guard_thread.start()
            try:
                result = self.command(args, timeout=660, check=False)
                pod = self.record_pod(result.stdout + "\n" + result.stderr)
            except subprocess.TimeoutExpired as exc:
                self.record_pod(str(exc.stdout or "") + str(exc.stderr or ""))
                raise RuntimeError("Pod creation timed out") from None
            if not self.pod_id:
                # A failed wait may still have created a paid Pod. Resolve by unique name.
                pod = self.find_pod()
                if not self.pod_id:
                    self.update(
                        status=f"POD MAY STILL BE RUNNING: check ledgerlens-train-{self.state['id']} in the Runpod console"
                    )
                    raise RuntimeError("Could not identify created Pod")
            self.created_at = create_started
            self.update(pod_created=create_started)
            if result.returncode:
                raise RuntimeError("Pod did not become ready")
            try:
                self.connection(pod)
            except ValueError:
                self.connection(
                    json_output(
                        self.command(
                            ["runpodctl", "pod", "get", self.pod_id, "-o", "json"]
                        ).stdout
                    )
                )
            disk = self.ssh("df -BG /workspace").stdout.splitlines()
            if len(disk) < 2 or int(disk[-1].split()[3].rstrip("G")) < 20:
                raise RuntimeError("Less than 20 GB free on /workspace")
            bundle = self.folder / "ledgerlens.bundle"
            self.command(["git", "bundle", "create", str(bundle), "HEAD"], timeout=120)
            remote_bundle = f"/workspace/ledgerlens-{self.state['id']}.bundle"
            self.scp(str(bundle), f"root@{self.host}:{remote_bundle}")
            self.ssh(
                f"cd /workspace/runpod-ledgerlens && git fetch {remote_bundle} HEAD && git checkout -B main FETCH_HEAD"
            )
            if self.state["dataset"] != "default":
                from app.backend.datasets import upload_path

                folder = upload_path(self.state["dataset"])
                self.ssh(f"mkdir -p {self.remote}/synth")
                # Copy files individually: no unwanted extra folder level.
                for file in sorted(folder.iterdir()):
                    if file.name != ".validation.json":
                        self.scp(
                            str(file),
                            f"root@{self.host}:{self.remote}/synth/{file.name}",
                        )
                self.ssh(
                    f"mkdir -p {self.remote}/done && touch {self.remote}/done/generate"
                )
            env = {
                "RUN": self.remote,
                "GPU_PRICE": str(self.state["price_per_hour"]),
                **self.state["env"],
            }
            variables = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
            self.ssh(
                f"cd /workspace/runpod-ledgerlens && mkdir -p {self.remote} && ({variables} setsid nohup bash train/run_pod.sh >> {self.remote}/run.out 2>&1 < /dev/null &)"
            )
            self.check()
            self.log("completed pod-start-copy")
            self.update(status="running")
            previous_stage = None
            completed = set()
            stop = self.state["env"].get("STOP_AFTER") or "report"
            expected_stages = STAGES[: STAGES.index(stop) + 1]
            while True:
                self.check()
                markers = self.ssh(f"ls {self.remote}/done", check=False).stdout.split()
                stage = next((s for s in expected_stages if s not in markers), stop)
                for marker in STAGES:
                    if marker in markers and marker not in completed:
                        self.log(f"completed {marker}")
                        completed.add(marker)
                if stage != previous_stage:
                    self.log(f"starting {stage}")
                    previous_stage = stage
                self.update(
                    stage=stage,
                    progress=len(set(markers) & set(expected_stages))
                    / len(expected_stages),
                )
                self.log(
                    self.ssh(
                        f"tail -n 50 {self.remote}/logs/{stage}.log", check=False
                    ).stdout
                )
                if stop in markers:
                    break
                if self.ssh(
                    'pgrep -f "^bash train/run_pod.sh"', check=False
                ).returncode:
                    # The stage may have finished between the marker read and pgrep.
                    markers = self.ssh(
                        f"ls {self.remote}/done", check=False
                    ).stdout.split()
                    if stop in markers:
                        completed.update(s for s in STAGES if s in markers)
                        self.update(stage=stop, progress=1)
                        break
                    raise RuntimeError(
                        f"Training process exited before {stop} completed"
                    )
                self.cancelled.wait(self.poll_seconds)
            quick = stop != "report"
            predictions_copied = False
            if quick:
                if (
                    self.ssh(
                        f"test -d {self.remote}/predictions", check=False
                    ).returncode
                    == 0
                ):
                    self.scp(
                        f"root@{self.host}:{self.remote}/predictions", str(self.folder)
                    )
                    predictions_copied = True
            else:
                self.scp(f"root@{self.host}:{self.remote}/eval", str(self.folder))
            if (
                self.ssh(
                    f"test -f {self.remote}/main/train_summary.json", check=False
                ).returncode
                == 0
            ):
                self.scp(
                    f"root@{self.host}:{self.remote}/main/train_summary.json",
                    str(self.folder / "train_summary.json"),
                )
            summary = self.folder / "eval/summary.json"
            results = self.folder / "eval/results.json"
            self.outcome = "done"
            self.update(
                status="cleaning-up",
                result_summary={
                    "simulated": False,
                    "quick_check": True,
                    "stages_completed": [s for s in STAGES if s in completed],
                    "predictions_copied": predictions_copied,
                }
                if quick
                else {
                    "simulated": False,
                    "evaluation": sanitize_data(
                        json.loads(
                            (summary if summary.exists() else results).read_text()
                        )
                    ),
                },
            )
        except InterruptedError as exc:
            if not self.state["status"].startswith("POD MAY"):
                self.outcome = str(exc)
                self.update(status="cleaning-up")
            self.log(
                "spending limit reached" if str(exc) == "cap_reached" else "cancelled"
            )
        except Exception as exc:
            if not self.create_attempted:
                self.log(str(exc))
                self.update(error=sanitize(str(exc)))
            if self.cancelled.is_set():
                self.outcome = "cancelled"
            elif self.state["spend"] >= self.state["cap_usd"]:
                self.outcome = "cap_reached"
            if not self.state["status"].startswith("POD MAY"):
                self.update(status="cleaning-up")
            self.log("Training failed; cleaning up Pod")
        finally:
            self.finished.set()
            if self.guard_thread:
                self.guard_thread.join(timeout=1)
            if self.create_attempted and not self.pod_id:
                self.find_pod()
            self.cleanup()
            if self.cleaned or not self.create_attempted:
                self.update(status=self.outcome)
            elif not self.pod_id:
                self.update(
                    status=f"POD MAY STILL BE RUNNING: check ledgerlens-train-{self.state['id']} in the Runpod console"
                )
            with LIVE_LOCK:
                LIVE.discard(self)


def shutdown():
    with LIVE_LOCK:
        runners = list(LIVE)
    for runner in runners:
        runner.cancel()
        runner.cleanup()


atexit.register(shutdown)
