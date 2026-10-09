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

## GPU API checks required tomorrow

1. Installed Unsloth supports Qwen3-VL-4B, the selected 4-bit path,
   `FastVisionModel.from_pretrained`, `get_peft_model` layer flags, and bf16.
2. `UnslothVisionDataCollator` accepts this system/text/PIL message structure,
   respects the processor cap without an additional resize, and preserves labels.
3. `train_on_responses_only` either supports the lazy dataset or raises a supported
   compatibility exception; ChatML delimiter tokenization matches collated IDs.
4. Installed TRL accepts `processing_class`, a lazy map-style Python dataset,
   `skip_prepare_dataset`, `max_length=None`, `eval_strategy`, best-checkpoint
   loading, and the listed SFTConfig arguments. The package snapshots pin the
   actual successful run; Unsloth/TRL are intentionally resolver-selected.
5. `save_pretrained_merged(..., processor, save_method="merged_16bit")` exports
   Qwen3-VL vision weights, processor and a vLLM-loadable model from 4-bit LoRA.
6. vLLM 0.31.0 exposes `StructuredOutputsParams(json=...)` through
   `vllm.sampling_params` and accepts `SamplingParams(structured_outputs=...)`;
   older installs use `GuidedDecodingParams`/`guided_decoding` via import fallback.
7. Offline `LLM` accepts `mm_processor_kwargs`, `limit_mm_per_prompt`, list-valued
   per-request SamplingParams, and returns ordered RequestOutput objects.
8. `LLM.get_tokenizer().apply_chat_template` accepts text plus image-marker
   content with the model's Qwen chat template; `multi_modal_data` accepts PIL.
9. Qwen3-VL, including the merged model, fits the chosen GPU at length 8192;
   4096 output tokens cover the longest pages without truncation. Check smoke
   output lengths, validation failures, and memory before the full evaluation.
