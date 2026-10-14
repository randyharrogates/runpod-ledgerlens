# LedgerLens Pod training and evaluation

Run from the repository root with `bash train/run_pod.sh`. The repo must be at
`/workspace/runpod-ledgerlens`, with persistent network storage at `/workspace`,
`uv` installed, internet access, and an A100 80 GB or L40S 48 GB with bf16 support.
Local tests require neither CUDA nor Unsloth/vLLM. No GPU API was verified offline.

- `build_dataset.py`: validates synthetic targets, checks business/ID separation,
  optionally downloads CORD v2, writes the five JSONL splits and CORD PNGs.
  `--max-cord-train` caps successfully mapped training examples, not source rows.
  CORD is CC BY 4.0 (naver-clova-ix/cord-v2); retain attribution when distributing.
- `common.py`: imports the worker system prompt; builds both training and prediction
  conversations; validates and serializes targets using schema field order and
  `model_dump_json(exclude_none=False)`; shares the image pixel cap (`ledgerlens.images.DEFAULT_MAX_PIXELS`, 2,000,000).
- `sft.py`: lazy image loading, language LoRA by default, bf16, validation/checkpointing
  every 50 steps, response-only loss, best eval-loss checkpoint, adapter and merged
  16-bit export, summary including loss history, settings, GPU, memory and duration.
  `--finetune-vision` also trains vision layers. Smoke evaluates at step 30.
- `predict.py`: bounded offline vLLM batches, schema decoding, canonical validated
  files or explicit invalid envelopes, per-set timing and coverage manifests.
- `report.py`: rebuilds gold plus synthetic anomaly metadata, invokes
  `ledgerlens.eval.score.score_batch`, writes Markdown/JSON tables and ten lowest
  scoring pages per model/set, classified by invalid/header/rows/reconciliation.
  Anomaly fields and reconciliation are N/A for receipts. Partial `--limit` runs
  are smoke checks and are deliberately rejected by full-set reporting.
- `render_pod.py`: Linux Playwright rendering and seeded degradation. The existing
  datagen Chromium locator searches macOS paths, so the Pod script runs datagen
  with `--no-render`, then this Linux renderer, without modifying datagen.
- `run_pod.sh`: two independent environments (Unsloth/TRL versus vLLM 0.31.0),
  persistent cache, package snapshots, timestamped stage logs and completion markers.

## Parity and missing CORD values

The prompt imports `worker.prompts.EXTRACT_SYSTEM`; user text is exactly
`Transcribe this {doc_type}.`. Training appends the canonical assistant JSON;
inference uses the same builder without an assistant, then the Qwen generation
prompt. Structured output imports `ledgerlens.schema.json_schema`, never a copy.
Both scripts resize RGB pixels before tokenization and request the same processor
pixel ceiling. Qwen may round dimensions to patch multiples; inspect the smoke
processor output to confirm equal effective resolution on both paths. Pass the
same `--max-pixels` to both scripts when changing it. Serving must likewise use
this cap through its processor settings for complete image preprocessing parity.

CORD `price` maps to item amount, `unitprice` to unit price, `cnt` to quantity,
`nm` to description. Numbers lose grouping commas/spaces, never currency scaling.
No unit price, tax, quantity or total is inferred. The current Receipt schema
requires non-null fields, unlike the prompt's null instruction. Consequently many
CORD examples may be skipped: each failure and per-source accepted/failed counts
are logged. Missing fields remain null in the mapper; schema-invalid samples are
never trained. Check those counts before interpreting real-receipt evaluation.

The Unsloth response-only helper is attempted first. Versions requiring a
Hugging Face `.map` dataset cannot operate on the lazy image dataset; the fallback
masks everything through the single Qwen ChatML assistant delimiter in the actual
collated token IDs. It preserves existing padding/image masks. Both paths enforce
that boundary and abort if no response tokens survive. This assumes one assistant
turn and the Qwen `<|im_start|>assistant\n` delimiter; verify in smoke output.

## Time, cost and resume

The plan budgets 1–2 hours for the main run; rendering 400 six-month businesses
and evaluating three models can add hours. These are planning estimates, not GPU
measurements. At an assumed $2/hour Pod price, 3–6 hours costs $6–12 plus storage.
Record actual console rates: report's `--price NAME=USD_PER_HOUR` numbers are
comparison inputs, not current tariffs. Cost/1,000 = seconds/page × 1,000 × price/3,600.
Batch seconds/page includes image preparation and disk writes, excludes model
initialization (separately recorded as `load_seconds`), and includes the first batch's
warm-up. It is not an isolated steady-state latency or a serverless cold-start claim.

Re-running skips stages with `/workspace/ledgerlens/done/<stage>`. Failed stages
have no marker. For main training, add `--resume-from-checkpoint` with the last
checkpoint path to its command; an unmodified retry starts training afresh.
A failed data-generation retry archives its incomplete directory. Rendering alone
can resume with `python -m train.render_pod --data /workspace/ledgerlens/synth`.
To regenerate predictions or reports after changing settings, remove their markers
and affected output directories first. Logs are overwritten on a stage retry;
archive them if needed. Stop the Pod yourself after reviewing results.

## GPU API checks (rehearsal, 9 Oct, RTX PRO 4500 32 GB)

A 20-business rehearsal ran every stage up to `predict-ft` with `PREDICT_LIMIT=3`. Installed:
TRL 1.13, transformers 5.17, torch 2.14.1+cu130, vLLM 0.31.0.

1. Unsloth loads Qwen3-VL-4B in bf16 (the 4-bit path is not used) and applies the layer flags.
2. `UnslothVisionDataCollator` takes the messages, but its default `resize="min"` shrank pages to
   512 px; it now uses `resize="max"` and `max_seq_length=8192` (2,048 truncated images mid-way).
3. `train_on_responses_only` works on the lazy dataset; the masking preflight passed
   (1,040 supervised tokens on the first example).
4. TRL 1.x requires a Hugging Face `Dataset`; a lazy `Dataset.with_transform` is used.
5. The merged 16-bit export (8.3 GB) loads in vLLM and gave valid JSON on every test page.
6. and 7. `StructuredOutputsParams`, `mm_processor_kwargs`, per-request params: all work.
   But `doc_type` is optional in the schema, so base models omitted it;
   `predict.parse_prediction` fills it from the requested type.
8. The chat template with an image marker plus PIL `multi_modal_data` works.
9. Peak training memory 12.8 GB at batch 2 × 8,192 tokens. One base-4B CORD receipt
   returned JSON broken at character 4,264 (16 s/page; counted as invalid); the fine-tune's were valid.

Measured stage times (20 businesses): smoke 16 min (30 steps, eval, merged export); main 25 min
for 108 steps (one epoch, ~860 examples, ~11 s/step); each prediction stage spends 10–20 min loading and
compiling before the first page.

## Test set C: phone photos of printed pages

Run locally from the repo root (Pillow is required; `prepare` also needs local Chromium):

```sh
uv run python -m train.phone_set prepare --synth /path/to/synth --out phone --n 20 --seed 7
# Print phone/print.pdf at A4, then photograph each page in printed order in normal room light.
uv run python -m train.phone_set ingest --phone phone --photos /path/to/exported/photos --out phone-data
# Open phone-data/pairs.html and confirm every photo matches the adjacent clean page.
# Supply RUNPOD_API_KEY through your environment; this tool never reads runpodctl's config.
uv run python -m train.phone_set score --data phone-data \
  --endpoint baseline=ENDPOINT_ID --price baseline=1.58 --out phone-results
```

The seeded test-only selection uses 10 unseen-layout statements, 6 seen-layout statements
and 4 invoices, cycling businesses and favoring less-used layouts/currencies. Other `--n`
values scale those proportions with largest-remainder rounding. Insufficient buckets or
business diversity fail explicitly. Clean HTML is rendered again; degraded dataset PNGs
are never printed. The A4 PDF includes a small numbered label outside the page content;
`manifest.json` records the exact order and source directory.

Export HEIC as JPEG first. Ingest accepts JPEG, PNG and WEBP, checks dimensions before
pixel decoding (60 million pixel upload limit), applies EXIF orientation and requires
exactly one photo per manifest entry. EXIF DateTimeOriginal orders dated photos, then
undated photos follow in filename order; filenames also break timestamp ties. Keep
exports in one directory without extra photos. The contact sheet is a local HTML file
referencing the clean PNGs in `phone/clean`; keep that directory available for review.
If pairs are wrong, fix the photo timestamps/names and ingest again before scoring.

Repeat `--endpoint NAME=ID` and `--price NAME=USD_PER_HOUR` for each model. Calls are
sequential. Invalid extraction answers are scored as invalid; failed jobs stop the run
instead of producing a misleading complete report. Predictions and `_timing.json` live
under `phone-results/predictions/NAME/test_phone`; Markdown/JSON reports are in `eval`.
Seconds/page and cost use mean Runpod `executionTime` (including cold jobs), excluding
queue delay and client wall time. Timing separately records mean delay in seconds,
worker-reported cold-start count, engine load time and wall time. The hourly prices are
user-supplied comparison inputs. This measures a real camera gap on known synthetic
answers; it does not measure accuracy on real customer documents.

## Concurrent serving benchmark

With `vllm serve` already running on the Pod, use its vLLM environment (httpx and
Pillow are required; the client imports neither torch nor vLLM):

```sh
python -m train.serve_bench --model merged-model --data /workspace/ledgerlens/data \
  --out /workspace/ledgerlens/bench-merged --concurrency 8 --label merged
python -m train.serve_bench --model adapter-name --data /workspace/ledgerlens/data \
  --out /workspace/ledgerlens/bench-lora --concurrency 8 --label LoRA
```

Use the served model name or registered LoRA module name for `--model`, starting
the appropriate server before each run. The default URL is
`http://127.0.0.1:8000/v1`; override it with `--base-url`. `--sets` accepts comma-separated
splits (default `test_unseen_layout`); `--limit` provides a partial smoke run.
Keep `--max-pixels` (default 2,000,000), `--max-tokens` (4096), and concurrency equal
for comparisons; match the server's processor pixel cap too. `--timeout` is seconds
per request (600 by default).

Predictions use the same split folders as `predict.py` and can be scored by
`report.py`. Invalid answers and failed requests are retained as invalid pages.
`_timing.json` records wall throughput, latency percentiles, token usage, page IDs,
and settings; its `requests` list retains each page's latency and token counts.
Request latency excludes semaphore waiting and image preparation. Split wall time
includes preparation, requests and prediction writes, including any server warm-up;
failed requests count in both `invalid` and `errors`. This client measures a running
server, not model initialization. Use separate output directories for each model.
