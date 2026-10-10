# LedgerLens Runpod worker

The worker starts a local vLLM server at import time, transcribes one document
image into a validated LedgerLens schema, and streams a short credit memo from
computed analysis. It performs no financial calculations. Tests need neither a
GPU nor vLLM: set `LEDGERLENS_NO_ENGINE=1` and replace `vllm_client`.

Runpod wraps each payload in `{"input": ...}`. Examples:

```json
{"input":{"task":"health"}}
{"input":{"task":"extract","doc_type":"statement","image_base64":"<base64 PNG/JPEG/WEBP>","max_tokens":2048}}
{"input":{"task":"memo","analysis":{"cash_flow":"Use your analytics output with page/row citations here"},"max_tokens":1024}}
```

Images must decode to at most 15 MiB. `doc_type` is statement, invoice, or receipt;
`max_tokens` must be a positive integer. Bad input yields one `{"error":"..."}`.
Extraction yields one result, for example:

```json
{"task":"extract","doc_type":"statement","document":null,"valid":false,"validation_error":"...","raw":"...","usage":{},"timing":{"inference_s":1.2,"engine_load_s":30.0,"cold_start":true},"model":"Qwen/Qwen3-VL-8B-Instruct","gpu":null}
```

Successful extraction has a validated `document`, `valid: true`, a null
`validation_error`, and no `raw`. Missing required printed fields can fail the
shared schema; the model is told to return null rather than guess. Memo yields
`{"delta":"..."}` chunks followed by `{"done":true,"usage":{},"timing":{...}}`.
Health returns engine status, resolved model, model source (`cache`, `volume`, `baked`, or
`download`), and `engine_load_seconds`. Cold start is true only for the first job,
including health or invalid jobs. Engine time is load/readiness time; inference
time excludes Runpod queueing. Runpod supplies its own job timing metadata.

## Environment

| Variable | Default / meaning |
| --- | --- |
| MODEL_PATH | Existing local model directory; takes precedence |
| MODEL_NAME | Qwen/Qwen3-VL-8B-Instruct |
| MAX_MODEL_LEN | 8192 |
| GPU_MEM_UTIL | 0.92 |
| VLLM_EXTRA_ARGS | Additional shell-split arguments, without a shell |
| ENGINE_START_TIMEOUT | 900 seconds |
| RUNPOD_GPU_TYPE | GPU label; otherwise query nvidia-smi if present |
| LEDGERLENS_NO_ENGINE | Set to 1 for offline tests only |
| RUNPOD_API_KEY | REST CLI credential; environment only |

The cache is `/runpod-volume/huggingface-cache/hub/models--ORG--NAME/snapshots/`.
The newest directory by modification time wins (name breaks ties). Cache hits
set `HF_HUB_OFFLINE=1` in the engine subprocess. Cache misses log a warning and
pass the HF identifier to vLLM for download. Baked weights use MODEL_PATH.

## How it is deployed (verified 9 Oct 2026)

- **Image:** Runpod's GitHub integration builds `worker/Dockerfile` from the **`deploy`** branch of
  this repo. Develop on `main`; `scripts/release-worker.sh` runs the tests, shows the
  image-affecting diff and fast-forwards `deploy`, which triggers the rebuild. The Dockerfile
  removes the CUDA-13 `torchcodec` wheel (it crash-looped vLLM on this CUDA 12.9 image) and imports
  `vllm.multimodal.media` at build time so that class of failure breaks the build, not paid workers.
- **8B endpoint** `szucrtrufff80a` (template `ej6398fg30`): `MODEL_NAME=Qwen/Qwen3-VL-8B-Instruct`
  served from Runpod's model cache (`--model-reference
  https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct:main`, pinned commit); container disk 40 GB;
  workers 0/2, idle 60 s, FlashBoot, queue-delay scaling 4 s, execution timeout 600 s, CUDA ≥ 12.9.
- **Fine-tuned 4B endpoint:** same template, created with the CLI:

  ```sh
  runpodctl serverless create --name ledgerlens-4b-ft --template-id ej6398fg30 \
    --gpu-id "NVIDIA RTX PRO 4500 Blackwell" --data-center-ids EU-RO-1 \
    --network-volume-id 0kelufi0wo --env MODEL_PATH=/runpod-volume/ledgerlens/main/merged \
    --workers-min 0 --workers-max 2 --idle-timeout 60 --min-cuda-version 12.9
  ```

  The merged weights stay on the network volume the training Pod wrote them to, so swapping models
  needs no rebuild or upload; the cost is that the endpoint is pinned to the volume's data centre.
- **Calling it:** `python worker/client.py extract --endpoint-id ID --image page.png --doc-type
  statement` and `python worker/client.py memo --endpoint-id ID --analysis analysis.json`. The client
  always uses `/run` and polls `/status` (or `/stream` for the memo): `/runsync` holds one connection
  for the whole job and Runpod dropped it during a cold start. It reads the key from `runpodctl`'s
  config (or `RUNPOD_API_KEY`), never from arguments, and never prints it.

Local SDK smoke input is `worker/test_input.json`; start from `worker/` with
`python handler.py` and an installed worker extra. This still starts vLLM unless
the engine is explicitly disabled.
