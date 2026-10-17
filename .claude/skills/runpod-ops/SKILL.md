---
name: runpod-ops
description: Project-specific Runpod operations for LedgerLens — endpoint health and logs, test requests, releasing the worker, Pod training runs, and spend checks. Use for any Runpod action in this repo; Runpod's official `runpod`/`runpodctl` skills cover general CLI usage.
---

# Runpod operations (LedgerLens)

IDs: endpoint `szucrtrufff80a`, template `ej6398fg30`, network volume `0kelufi0wo` (EU-RO-1).
The key lives in `~/.runpod/config.toml`; never print it. Run `runpodctl <cmd> --help` before using an
unfamiliar flag — live help wins over any doc.

## Health and logs
```bash
runpodctl serverless health szucrtrufff80a          # workers idle/ready/initializing, jobs queued
runpodctl serverless logs szucrtrufff80a --since 30m --max-wait 25s   # JSON lines per worker
runpodctl serverless get szucrtrufff80a              # scaling settings
runpodctl template get ej6398fg30                    # env, disk
```
Repeated system "start container" lines with no container output = the container exits before the
handler runs (crash loop). "Pulling/Extracting" = image still downloading to a new worker.

## Test requests
```bash
uv run python worker/client.py extract --endpoint-id szucrtrufff80a \
  --image data/synth-r/<page>.png --doc-type statement > /tmp/r.json
```
Compare `result.output` (or the aggregated stream) with `<page>.json` using
`ledgerlens.eval.score`. Record delayTime (queue + cold start), executionTime, GPU and cost in the
build log. The first job after idle includes vLLM loading the model — that is the cold start to show.

## Releasing a worker change
`scripts/release-worker.sh` (tests → image-affecting diff → confirm → push `main:deploy`). The
endpoint must track the `deploy` branch (console: endpoint → Manage → Edit). A rebuild + image pull
takes ~10 min.

## Scaling rules
Min workers 0 (nothing bills idle); max 2. For the demo hour only: the app's GPU switch (left rail,
"All endpoints", 30 min–2 h timer; it turns itself off), or by hand `runpodctl serverless update
<id> --workers-min 1` and back to 0 straight after. After min goes to 0, Runpod keeps the worker
running and billed for 2–5 min: check `runpodctl user` shows ~$0.01/h before leaving. Model cache:
`--model-reference https://huggingface.co/<org>/<model>:main`.

## Training Pod
Create in EU-RO-1 with the network volume mounted at `/workspace`, Secure Cloud, a stop/terminate
deadline, SSH. Check flags with `runpodctl pod create --help` (e.g. `--network-volume-id 0kelufi0wo
--volume-mount-path /workspace --data-center-ids EU-RO-1 --terminate-after <iso time>`). Clone the
repo onto the volume, run `train/run_pod.sh` (resumable by stage markers), copy `eval/` results back
(`runpodctl send`/`receive` or git), then **stop the Pod** and log the cost.

## Spend
`runpodctl user` (balance, current spend per hour) and `runpodctl billing`. Check at the end of every
session; anything running unexpectedly → stop it and tell Randy.
