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
