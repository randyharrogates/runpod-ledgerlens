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
Health returns engine status, resolved model, model source (`cache`, `baked`, or
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

## Deployment checklist

These commands are **to verify against `runpodctl serverless create --help` /
`runpodctl template create --help`**. CLI flags, the image tag, Qwen3-VL support,
and vLLM structured-output flags have not been verified offline.

- [ ] Verify the vLLM image tag exists and supports your GPU/Qwen3-VL model.
- [ ] Build and push from the repo root:

  ```sh
  docker buildx build --platform linux/amd64 -f worker/Dockerfile -t <dockerhub-user>/ledgerlens-worker:<tag> --push .
  ```

- [ ] For fine-tuned weights, enable the commented COPY and MODEL_PATH lines in
  Dockerfile and supply `--build-arg MODEL_DIR=<local-weights-directory>`.
  Keep that directory inside the build context and outside excluded `data/`.
- [ ] Inspect the CLI help and create a serverless template (candidate syntax):

  ```sh
  runpodctl template create --name ledgerlens-worker --image-name <dockerhub-user>/ledgerlens-worker:<tag> --is-serverless --model-reference https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct:main
  ```

- [ ] Create an endpoint with EU-RO-1, min 0 / max 2 workers, FlashBoot enabled,
  and idle timeout 60 seconds (candidate syntax):

  ```sh
  runpodctl serverless create --name ledgerlens --template-id <template-id> --data-center-ids EU-RO-1 --workers-min 0 --workers-max 2 --flashboot --idle-timeout 60
  ```

- [ ] Set MODEL_NAME, choose a GPU with enough VRAM, and verify cached weights
  and `/health` readiness on a real worker. Never deploy LEDGERLENS_NO_ENGINE=1.
- [ ] Call the endpoint with RUNPOD_API_KEY set in your environment:

  ```sh
  python worker/client.py extract --endpoint-id ID --image page.png --doc-type statement
  python worker/client.py memo --endpoint-id ID --analysis analysis.json
  ```

The CLI uses `/runsync` for extraction and `/run` followed by `/stream/{job_id}`
for memo. It prints the JSON response plus round-trip seconds, collecting streamed
chunks until a terminal status. If `/runsync` returns a pending job, it also polls
`/stream/{job_id}`. Credentials are never accepted as arguments or printed.

Local SDK smoke input is `worker/test_input.json`; start from `worker/` with
`python handler.py` and an installed worker extra. This still starts vLLM unless
the engine is explicitly disabled.
