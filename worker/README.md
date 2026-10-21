# LedgerLens Runpod worker

The worker starts a local vLLM server at import time, transcribes one document
image into a validated LedgerLens schema, and streams a short credit memo from
computed analysis. The chat orchestrator computes cash-flow analytics in Python. Tests need neither a
GPU nor vLLM: set `LEDGERLENS_NO_ENGINE=1` and replace `vllm_client`.

## Tasks at a glance

All three LLM endpoints run this same image, so each accepts every task; the right column says
which endpoint the app sends it to. Send with `/run`, then poll `/status` (one result) or `/stream`
(events as they happen).

| `task` | Input (inside `input`) | Output | Poll | Used on |
|---|---|---|---|---|
| `health` | nothing else | engine status, model, `model_source` (`cache`, `volume`, `baked`, `download`), `engine_load_seconds`, `cold_start` | `/status` | every endpoint |
| `generate` | `prompt`, optional `system`, `image_base64`, `max_tokens`, `temperature`, `stream` | `text`, `usage`, `timing`, `gpu`, `model` | `/status`, or `/stream` with `stream: true` | any |
| `extract` | `doc_type` (`statement`, `invoice`, `receipt`), `image_base64` | validated `document` (rows and header), `valid`, `usage`, `timing` | `/status` | 8B, fine-tuned 4B, base 4B (Read and Compare pages) |
| `memo` | `analysis` (computed figures with page/row citations) | `delta` text chunks, then `done` with `usage`, `timing` | `/stream` | the model picked on the Read page (8B by default) |
| `read` | `pages: [{page_id, image_base64}]` | per-page `triage` and `rows` events, then `final` | `/stream` | agent |
| `agent` | `message`, optional `documents`, `new_pages`, `history`, `last_memo` | `step`, `route`, `deltas`, `section`, `tool`, `log` events, then `final` | `/stream` | agent |
| *(none)* | `prompt`, optional `image_base64` | runs as `agent` with `message` = `prompt` | `/stream` | agent |

Details for each are below. Bad input always returns one `{"error": "..."}` saying what was wrong.

## Request examples

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

## Plain prompts

A plain `prompt` without a task routes to the chat agent as its `message` (an
`image_base64` alongside it becomes page `p1`).
Set `task: "generate"` explicitly for the plain generation interface described below. `prompt` must be a string with 1–20000 characters
after trimming whitespace. Optional `system` is a string of at most 4000 characters;
it defaults to a short LedgerLens assistant instruction. `max_tokens` defaults to
512 and must be an integer from 1 to 4096. `temperature` defaults to 0.7 and must
be a number from 0 to 2 (not a boolean). Extract and memo still use temperature 0.

Optional `image_base64` uses the same PNG/JPEG/WEBP validation, 15 MiB size limit,
dimension check and resizing as extract. The image follows the prompt in the user message.
`stream` must be a boolean and defaults to false. Without streaming, one result has
`task`, `text`, `usage`, `timing`, `gpu` and `model`; `/run` then `/status` returns
it in a one-item `output` list. With streaming, `delta` chunks are followed by a
result with `done: true` and those same fields, including the full `text`.

```sh
python worker/client.py generate --endpoint-id ID --prompt "Explain a returned cheque briefly."
python worker/client.py generate --endpoint-id ID --prompt "Describe this page." --image page.png --system "Answer briefly." --max-tokens 256 --temperature 0.5 --stream
```

## Baked model image

Use `worker/Dockerfile.baked` to include the handler and pinned 8B weights in one
image. Shared steps must stay in step with `worker/Dockerfile`. The weights layer
comes before code copies so code changes reuse it. Build arguments `MODEL_ID` and
`MODEL_REVISION` select the model and exact revision; defaults match the 8B cache
endpoint. It sets `MODEL_PATH=/models/model` and `HF_HUB_OFFLINE=1`.

No network volume is needed, so the endpoint is not pinned to one data centre.
The trade-off is about 16 GiB more in the image and slower builds and pulls;
cold start to be measured. Runpod's GitHub builder allows 30 minutes per build
and an 80 GB image. The live endpoints keep using `worker/Dockerfile`.
The base image's `huggingface_hub` import still needs verification by building
this Dockerfile; the download step will fail the build if it is unavailable.

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
| RUNPOD_API_KEY | REST client credential; otherwise read from `runpodctl`'s config |

The cache is `/runpod-volume/huggingface-cache/hub/models--ORG--NAME/snapshots/`.
The newest directory by modification time wins (name breaks ties). Cache hits
set `HF_HUB_OFFLINE=1` in the engine subprocess. Cache misses log a warning and
pass the HF identifier to vLLM for download. Baked weights use MODEL_PATH.

## How the LLM endpoints were first deployed (9–11 Oct 2026)

Current settings for all four endpoints, including the agent and FLUX endpoints, are in the main
README's [Deploy it yourself](../README.md#deploy-it-yourself).

- **Image:** Runpod's GitHub integration builds `worker/Dockerfile` from the **`deploy`** branch of
  this repo. Develop on `main`; `scripts/release-worker.sh` runs the tests, shows the
  image-affecting diff and fast-forwards `deploy`, which triggers the rebuild. The Dockerfile
  removes the CUDA-13 `torchcodec` wheel (it crash-looped vLLM on this CUDA 12.9 image) and imports
  `vllm.multimodal.media` at build time so that class of failure breaks the build, not paid workers.
- **8B endpoint** `szucrtrufff80a` (template `ej6398fg30`): `MODEL_NAME=Qwen/Qwen3-VL-8B-Instruct`
  served from Runpod's model cache (`--model-reference
  https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct:main`, pinned commit); container disk 40 GB;
  workers 0/2, idle 60 s, FlashBoot, queue-delay scaling 4 s, execution timeout 600 s, CUDA ≥ 12.9.
- **Fine-tuned 4B endpoint** `5078uudfkeaw2v` (template `avnw8y6zs7`, created 11 Oct): same repo,
  branch and Dockerfile, created in the console (New endpoint → GitHub repository), so it reuses the
  same build. Env `MODEL_PATH=/runpod-volume/ledgerlens/main/merged`, `MAX_MODEL_LEN=8192`,
  `GPU_MEM_UTIL=0.92`; network volume `0kelufi0wo` (pins EU-RO-1); GPUs 32 GB PRO then 24 GB PRO
  (RTX 5090, RTX 4090); the rest as the 8B. Two things that don't work: one template can't serve two
  endpoints (`runpodctl serverless create --template-id` refuses), and a hand-made template can't
  pull an image built by the GitHub integration (workers loop on "Failed to get Hub registry auth").
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

## Chat and page reading

`task: "read"` accepts `pages: [{"page_id": "p1", "image_base64": "..."}]`.
Each page runs triage and, when accepted, schema-validated extraction independently.
A page that cannot be read is reported as a `rows` event with `valid: false` and left
out; the other pages carry on.
Statements receive deterministic debit/credit corrections in `balance_fixes`.

`task: "agent"` accepts `message`, optional `documents` (a list of
`{page_id, doc_type, document}`), `new_pages` (the same shape as read pages),
`history` (`{role, text}` turns), and `last_memo` (section id to text).
The router runs alongside page reading and Python analysis. Memo sections stream
concurrently; each section gets at most one rewrite after checking figures,
citations, headings, and word limits. Edits retain unchanged sections. Data
questions use read-only tools (vLLM runs with `--enable-auto-tool-choice
--tool-call-parser hermes`) for at most three rounds, with a final tool-free
answer when the budget is exhausted. Missing statements and off-topic requests
receive fixed replies.

Chat messages are limited to 2,000 characters, requests to eight uniquely named
pages and 9 MiB of JSON, including base64. Inputs and model output remain data.
The dimension check runs before image decoding for both triage and extraction.

Every chat/read event has a `type`: `step`, `triage`, `rows`, `route`, `deltas`,
`section_reset`, `section`, `tool`, `log`, `final`, or `error`. Deltas contain
`parts: [{part, text}]`; `part` is a memo section id or `answer`. Concurrent text
shares one queue and is batched with `STREAM_FLUSH_CHARS` (64) and
`STREAM_FLUSH_MS` (200). Non-text events flush buffered text first.
`section_reset` replaces the previous text for that part. `final` includes the
answer or ordered memo, remaining checker problems, summed token usage, step and
total timings, GPU label, and agent model names. An exception cancels other work
and produces an `error` event without a final result. Engine startup failures use
the existing plain `error` response.

```sh
python worker/client.py read --endpoint-id ID --image page1.png --image page2.png
python worker/client.py agent --endpoint-id ID --message "Write a credit memo" --image page.png
python worker/client.py agent --endpoint-id ID --message "Who is the largest payer?" --documents documents.json
```

These commands poll streaming output, print one JSON event per line, and finish
with intent, total seconds, Runpod `delayTime`, and `executionTime`.
`documents.json` contains the document list described above.

Agent environment variables are read once at import: `BASE_MODEL=ledgerlens`,
`READER_MODEL=ledgerlens`, `MEMO_MODEL` defaults to `BASE_MODEL`,
`TRIAGE_MAX_PIXELS=500000`, and `MAX_CONCURRENCY=4`. All roles use the current
served model by default; a configured LoRA engine can use `READER_MODEL=reader`
and `MEMO_MODEL=memo`. Runpod's concurrency modifier caps concurrent jobs at
`MAX_CONCURRENCY`; page and section requests within each job run concurrently.
Offline tests inject an async `Engine` substitute; the legacy tasks continue via
`sync_handler` in a thread.

`log` events carry `entries: [{level, source, message}]` (source `worker` or
`vllm`, optional `page_id`, `section`, `ms`, `model`): model call timings and
token counts, the route, checker rewrites, tool errors, reader validation
failures (location and type only) and exceptions. They never contain prompts,
images, documents or answers. Each yield is one HTTP request to Runpod, so log
lines are batched: they are sent with the next text flush, on the same 200 ms
deadline, at 20 lines, or just before `final`/`error`; ordinary progress events
do not flush them.

vLLM's stdout and stderr go to a file that a thread copies to the container's
stderr, so Runpod's Logs tab shows them; the last 80 lines still form the
startup-failure message. While a job runs, vLLM lines with `WARNING`, `ERROR`
or `Traceback` (at most 50) are added to its `log` events. The engine is
shared, so with concurrent jobs such a line may belong to another job.

For evaluations, `task: "agent"` accepts `stop_after: "route"`: it runs the normal
router and document/memo overrides, emits `route`, then a final intent, plan and
reason without reading, analysis, tools or writing. `task: "read"` accepts
`stop_after: "triage"`: each page emits triage only, and the final response contains
`documents: []` and a `triage` list. Both include normal usage and timing. Other
`stop_after` values are input errors.
