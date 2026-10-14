# Build log

A running record of every step, decision and dollar, kept as we go so the final report and the demo
walkthrough can be written from facts rather than memory. Times are SGT (UTC+8). Newest entries at
the bottom of each day.

## Fri 9 Oct 2026

### Choosing the project
- **13:45 — Goal.** A Runpod Serverless endpoint running an open model, with `handler.py`, a
  Docker image, a live demo, code and docs: use the platform well, integrate handler and model
  cleanly, deploy something that works, and document it clearly.
- **14:00 — Rejected: "verified credit memo"** (writer LLM + a fine-tuned claim checker).
  MiniCheck (EMNLP 2024) already showed a 770M checker at GPT-4 accuracy, numeric claims are better
  checked by lookup than by a classifier, it needed about a week, and the demo was flat.
- **14:20 — Chosen: LedgerLens.** Phone photos of messy SME bank statements and invoices → extracted
  rows → computed analysis → a cited credit memo. Real buyer: Southeast Asian SME lenders whose
  documents can't leave for a US API.
- **14:35 — ML component added.** LoRA fine-tune Qwen3-VL-4B on synthetic documents and compare it
  with Qwen3-VL-8B zero-shot on accuracy, latency and cost per 1,000 pages. Three held-out test sets:
  unseen layouts, real CORD v2 receipts, phone photos.
- **14:45 — Data decision.** No public real bank statements exist, so synthetic data with answers
  known by construction, plus real CORD receipts (CC BY 4.0, verified) for the synthetic-to-real gap.
  Fine-tuning only on our own templates would flatter the score, so held-out layouts and real
  receipts are the guard.
- **15:00 — Budget.** $200; planned ~$50–80. Rules: stop Pods after use, min workers 0,
  cap max workers, weights cached once.

### Accounts and infrastructure
- **15:16 — Repo created locally**; project plan written.
- **15:28 — Runpod CLI.** Installed `runpodctl` 2.14 (Homebrew, then `runpodctl update`). Randy saved
  his API key with `runpodctl doctor`; the key file was then restricted to his user (`chmod 600`).
  The key is never typed into chat, code or logs.
- **15:30 — Account checked.** Balance $214.64; nothing running.
- **15:32 — Data centre chosen: EU-RO-1 (Romania).** From `runpodctl datacenter list` / `gpu list`:
  RTX 4090 stock High, RTX PRO 4500 (32 GB) Medium, RTX PRO 6000 (96 GB) Low. No Asian data centre
  listed these GPUs (India and Japan showed H100 only) — noted for the "data stays in region" question.
- **15:33 — Network volume** `ledgerlens` (id `0kelufi0wo`), 100 GB, EU-RO-1, ~$7/month.
- **15:35 — GitHub repo** `randyharrogates/runpod-ledgerlens`, **private**, created with the personal
  account (the machine's active `gh` account is a work account, so the personal token is scoped to
  each command and the global login is left alone).
- **15:50 — Image build route.** No Docker on the Mac and only 16 GB free disk (the vLLM base image
  is 11 GB), so the image is built by **Runpod's GitHub integration**. Randy connected GitHub in the
  Runpod console (16:10). Runpod has no API/CLI for GitHub-built endpoints (checked), so the endpoint
  itself is created in the console.

### Code (Codex implements, Claude reviews)
- **15:20 — Phase 1 (offline):** schema, synthetic generator, analytics, scorer. Review found the
  generator was close to a fixed template: identical amounts and dates for every business, the same
  anomalies everywhere, no clean businesses, whole-dollar amounts, the business number inside every
  name, and layouts changing page to page. A model would have learned the template, and anomaly
  detection could cheat by keyword. **Rewritten** with seeded variety, optional anomalies (~27% clean
  businesses), and leakage-safe splits by business and layout; anomaly precision added to the scorer.
- **15:43 — Commit `57c0afd`:** generator, analytics, scorer and the Serverless worker.
  Worker review fixes: Python 3.12 compatibility (the vLLM image ships 3.12), base image pinned to
  `vllm/vllm-openai:v0.31.0-cu129` (verified on Docker Hub), installs constrained so they can't
  upgrade vLLM's packages, extraction token budget 4,096, client reads the `runpodctl` key file.
- **15:46 — Commit `59cbd55`:** realism fixes from looking at rendered pages — whole-rupiah IDR,
  country-correct payment rails (PayNow/FAST/GIRO, DuitNow/IBG/JomPAY, BI-FAST/SKN/RTGS) with FROM on
  credits and TO on debits, natural Chinese names, headers in the country's language.
  Known nit: JomPAY should not appear on incoming money or payroll.
- **15:59 — Commits `befdf74`, `b4624bc`:** training and evaluation code (dataset builder with CORD v2,
  Unsloth LoRA SFT, vLLM batch prediction, report, resumable `run_pod.sh`, parallel rendering).
  Review fixes: one shared pixel cap (2 MP) in `ledgerlens.images` used by training, prediction and
  the live worker (train/serve parity); bf16 LoRA instead of 4-bit to match bf16 serving.
- **16:01 — Commit `e90cb39`:** security fix from an automated review — the worker now checks an
  image's declared size before decoding (60 MP cap) so a decompression bomb can't stall a job.
- **16:05 — Interactive app, first version** (FastAPI backend + React/Vite frontend, 114 tests).
  Review: one TypeScript error fixed so it builds; port moved from 8080 to **8090** (8080 is taken
  by a local Jenkins on the Mac) and made configurable via `PORT`.
- **16:18 — Clicked through every screen in a headless browser** (Playwright script, demo mode,
  no console errors). Found: the bundled sample "statements" were plain-text dumps drawn with Pillow,
  not our rendered layouts; the review table cut off the balance column and showed unformatted
  amounts; red flags named the type but did not explain it; a raw JSON debug line under the memo;
  the "under the hood" drawer was one long key/value table per request; the frontend was one
  267-line file with minified CSS (not walkthrough-friendly); uploads needed the same
  decompression-bomb cap as the worker. All sent back to Codex (16:20).

### Endpoint
- **16:15 — Workers decided:** Active (min) **0** so nothing bills while idle (cold starts are shown,
  not hidden); Max **2**. One Active worker only for the demo hour.
- **16:15 — Asian data centre question.** Runpod's only Asian capacity is AP-JP-1 (Japan) and India,
  H100/H200 only (~$0.00155/s ≈ $5.60/h running vs ~$1.10/h for a 4090 in EU-RO-1). Latency is not
  the issue (≈0.2 s extra per request against seconds of inference). Plan: build and test in
  EU-RO-1; before the demo add a second endpoint in AP-JP-1 from the same repo so the cost panel shows
  the real in-region vs out-of-region trade-off — a data-residency answer backed by measured numbers.
- **16:23 — Console settings reviewed before deploy.** Allowed CUDA restricted to 12.9+ (the image is
  CUDA 12.9; older host drivers would fail to start it). GPU types: kept RTX 5090 (32 GB) and RTX 3090
  (24 GB); removed L4 (≈⅓ of a 3090's memory bandwidth, slow for decoding), PRO 6000 MIG 24GB (a
  compute slice) and RTX A5000 (slower). All data centres left allowed: with no volume attached
  nothing pins the endpoint, and wider choice finds GPUs faster. No exposed ports (queue endpoint).
- **16:24 — Runpod MCP installer declined.** `npx @runpod/mcp-server@latest add` is an interactive
  installer that rewrites every detected AI client's config and, in local mode, stores the API key in
  plain text; it would also need the local agent restarted. `runpodctl` already covers the needed commands
  (including `serverless logs`).
- **16:26 — Endpoint `szucrtrufff80a` created** (console, GitHub build of `main`, Dockerfile
  `worker/Dockerfile`, template `ej6398fg30`). Checked with `runpodctl serverless get` /
  `template get`: workers 0/2, idle 60 s, FlashBoot on, queue-delay scaling 4 s, execution timeout
  600 s, env `MODEL_NAME`, `MAX_MODEL_LEN=8192`, `GPU_MEM_UTIL=0.92` correct. Fixed from the CLI:
  container disk 5 → **40 GB** (`runpodctl template update ej6398fg30 --container-disk-in-gb 40`),
  and the 8B model attached to Runpod's model cache (`runpodctl serverless update szucrtrufff80a
  --model-reference https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct:main`, pinned to commit
  `0c351dd`) so workers don't download 17 GB at start-up.

- **16:33 — Deploy branch.** Runpod's GitHub integration rebuilds on every push to the tracked
  branch, so the endpoint will track **`deploy`** instead of `main`. Created `deploy` at `7d15fed`
  (the commit currently building, so the switch changes nothing deployed). Added
  `scripts/release-worker.sh`: runs the tests, shows the image-affecting diff since the last release,
  and fast-forwards `deploy` to `main` only on confirmation. Docs and app commits no longer cost a
  rebuild.
- **16:37 — First live request queued, never ran.** `worker/client.py extract` on `biz_0000_p002`;
  the client gave up after ~6 min with a generic "Request failed". Health showed workers "ready" and
  "idle" but the job stayed `IN_QUEUE`.
- **17:03 — Root cause: vLLM crash loop on start.** Stored logs showed only repeated system
  "start container" lines; `runpodctl serverless logs szucrtrufff80a --follow` caught the container
  output: `vllm serve` dies importing `torchcodec` — `OSError: Could not load this library:
  .../torchcodec/libtorchcodec_image.so`. The handler re-raised, the container exited, Runpod
  restarted it, the job never got a worker. Billing hit $1.59/h while it looped.
- **17:05 — Why.** The PyPI torchcodec wheel (0.14+, no cu129 build exists on the PyTorch index)
  links `libcudart.so.13` / `libnvrtc.so.13`, but the image is `vllm/vllm-openai:v0.31.0-cu129`
  (CUDA 12.9). vLLM 0.31.0 guards that import with `except (ImportError, RuntimeError)` — not
  `OSError` — so it is fatal. Checked with `llvm-objdump -p` on the wheel and the vLLM v0.31.0 source.
- **17:06 — Fix (Dockerfile).** `pip uninstall -y torchcodec` (vLLM then treats audio decoding as
  unavailable; we only send images; every other torchcodec import in vLLM is guarded or model-specific)
  plus a build-time `python3 -c "import vllm.multimodal.media"` so this class of failure breaks the
  build instead of crash-looping paid workers. Not yet deployed.
- **17:10 — Fail fast instead of crash-looping (Codex, reviewed).** If vLLM fails to start, the
  handler logs the full error, keeps the worker up, and answers every job at once with
  `Model engine failed to start: <last 2,000 chars>`; health reports it. stderr tail kept for the
  error: 20 → 80 lines. Client: `--timeout`, prints job id + status + error on failure, exit 1 when the
  job failed or its output carries an error; key redaction on every print. Review fixes: keep the
  *end* of the error (root cause is last), non-zero exit for COMPLETED-with-error. 137 tests pass.
- **17:10 — Model cache path checked.** `runpodctl serverless model-status` shows the host mount
  `/runpod/model-store/...`; Runpod's docs say workers see it at
  `/runpod-volume/huggingface-cache/hub/`, which is what `resolve_model()` reads. Confirm on the
  first successful job (`model_source` should be `cache`).
- **17:28 — Endpoint switched to `deploy`** (console: Manage → Edit → Repository configuration →
  Branch). `scripts/release-worker.sh` now pushes with the personal token (`git_personal`).
- **17:30 — Released `f6dbfd4`** (`main:deploy`). Build passed, including the new import check;
  template image `…-deploy-worker-dockerfile:f6dbfd480` at 17:36.
- **17:40 — vLLM up.** Weights read from `/runpod-volume/huggingface-cache/hub/…/0c351dd…` (model
  cache path confirmed, 16.3 GiB loaded in 5.7 s). Cold start ≈ 3.5 min, container start → handler
  ready: torch.compile 27 s and CUDA-graph capture 63 s are the big parts (worth caching later).
  The job queued since 16:37 then ran.
- **17:41 — Gate 1, first live result (warm, RTX 5090):** `biz_0000_p002` (light) — valid JSON,
  queue 0.13 s, execution 15.1 s, 1,710 prompt + 1,359 output tokens. Every description, amount and
  balance right, but row F1 **0** — two systematic errors: dates mangled (`29 Jan 2026` →
  `2901-01-20`: the model starts day-first and the ISO date grammar forces the digits into the wrong
  slots) and 6/9 incoming payments filed as debits (layout prints one signed Amount column). The
  prompt said "exactly as printed" with no rule for either.
- **17:44 — Prompt fix (`a0bb907`):** dates as YYYY-MM-DD with day-first examples; money out = debit,
  in = credit; how to read a single signed column. Training imports the same prompt.
- **17:57 — Re-test after rebuild (image `a0bb907`):** `p002` light (cold start, ~5 min end to end):
  header 9/9, rows **16/18**, both misses are credits filed as debits. `p004` heavy (warm, 14.0 s):
  header 8/9 (closing balance), rows **7/18**: 9 credit/debit swaps, 4 digit misreads
  (`933846`→`633846`, `1997.93`→`1967.93`, a balance), 2 dropped CJK/Malay characters, 1 row missing.
  Dates now right on both. Reading: zero-shot 8B reads digits well on clean pages but not the
  accounting convention — the main thing the fine-tune has to teach. The balance column is read
  correctly, so debit/credit can also be checked deterministically from balance changes.
- **18:10 — Debit/credit check from the running balance (Codex, reviewed).**
  `ledgerlens.direction.check_direction` moves an amount between debit and credit only when the
  printed balance changes by exactly that amount; anything else is left alone. Evaluation keeps raw
  model accuracy as the headline and adds "Row F1 (after balance check)". 149 tests.
- **18:20 — Live app test exposed three bugs, all fixed.** (1) `/runsync` holds one connection for
  the whole job; on a cold start Runpod dropped it ("Server disconnected without sending a
  response") although the job completed — the client now always uses `/run` and polls `/status`
  (which also returns delay/execution time), retrying dropped polls. (2) The app silently showed
  cached ground truth when a live call failed — now logged and recorded as `live_error`.
  (3) After a backend restart the browser kept a dead session and a cached `index.html` — the app
  now starts a fresh session and serves `index.html` with `no-cache`.
- **18:43 — Live heavy page through the app (warm, RTX 5090):** queue 1.4 s, execution 13.7 s. The
  balance check moved 7 rows debit→credit; 4 rows stay red because the model misread a digit
  (e.g. 6,673.00 read as 673.00) — exactly the rows an analyst should check against the photo.
- **18:48 — Measured serverless cost, 8B on RTX 5090 (warm):** 13.7–15.1 s per statement page ×
  $0.000439/s (32 GB PRO flex, $1.58/h in the endpoint editor) ≈ **$6.0–6.6 per 1,000 pages**.
  App default price corrected from $0.00031/s.

### App (review of Codex's polish pass)
- **17:07 — Samples regenerated** with `app/make_sample.py` outside the sandbox (Chromium): four
  consecutive `stmt_b` pages (clean → heavy) and one invoice, seed 4, fictional "Cloudpetal Bank".
  The committed-before samples were text dumps. Looked at the pages: the heavy page reads like a real
  phone photo.
- **17:10 — Clicked through every screen** (demo mode, headless Chromium). Works: cached results are
  labelled "Cached demo · ground truth" and "no model call"; review table fits and formats money;
  flags cite rows (`p1:r4`). Follow-ups: review shows the page above the rows (side by side would let
  a reviewer check a row against the source); March is a partial month and reads as a collapse on the
  chart, net line stops at Feb; counterparties appear lowercased in flags and the memo ("cedar petal
  reed holdings"); memo headings unstyled; invoice sample has one line, right-aligned description and
  8% tax (SG GST is 9%); zoom slider unstyled.

### Training (preparation)
- **18:30 — GPU stock in EU-RO-1 (where the network volume lives):** no A100 80 GB or L40S at first
  check; RTX PRO 4500 (32 GB, $0.72/h) Medium, RTX 4090 Medium, A100 SXM ($1.79/h) appeared as
  Low at 18:45. Stock moves minute to minute. Plan: A100 SXM if available tomorrow, else RTX PRO
  4500 (32 GB holds the 4B LoRA and the 8B for prediction — the 8B served from 32 GB today).
- **18:45 — `train/run_pod.sh` made configurable** (`RUN`, `N_BUSINESSES`, `STOP_AFTER`,
  `PREDICT_LIMIT`, `GPU_PRICE`); installs `uv` if missing; HF and uv caches on the volume so the
  real run reuses tonight's downloads. All three models are costed at the price of the GPU they ran
  on (the old script mixed in prices of other GPUs).
- **18:45 — Rehearsal Pod `xvj2ohnvkpwa4f`** (RTX PRO 4500, EU-RO-1, volume `0kelufi0wo` at
  `/workspace`, image `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`, min CUDA 13.0; driver
  580.173, 48 vCPU, 251 GB RAM). Repo copied as a git bundle over SSH (no GitHub token on the Pod).
  Running `RUN=/workspace/rehearsal N_BUSINESSES=20 STOP_AFTER=predict-ft PREDICT_LIMIT=3` to check
  the nine untested GPU-library assumptions in `train/README.md` tonight.

- **19:08–20:14 — The rehearsal found seven bugs that tests could not**, each fixed, committed and
  re-run on the Pod:
  - `render_pod` sliced the `splits.json` page dict (`9a0bca6`).
  - Real CORD receipts: dict-valued menus crashed the mapper; required receipt fields would have
    dropped ~75% of real receipts (CORD often omits unit price, tax, subtotal); rupiah grouping read
    "60.000" as 60. Receipt fields are now optional (null when not printed), sub-items flattened,
    rupiah parsed (`293a2fb`, `27d19ab`). Accepted receipts: 720/96/91 of 800/100/100 with the first
    fix; the later number formats will raise this in tomorrow's fresh dataset.
  - transformers 5.17 made `max_pixels` a read-only view of `size["longest_edge"]` (`27d19ab`).
  - TRL 1.13 requires a Hugging Face `Dataset` (`d7ad2c6`).
  - `UnslothVisionDataCollator`'s default `resize="min"` shrank pages to 512 px: a silent
    train/serve mismatch, since evaluation and serving use our own pixel cap (`d7ad2c6`).
  - Unsloth's default 2,048-token limit cut examples inside the image ("Mismatch in image token
    count"); now 8,192, matching serving (`ad350e7`).
  - The stopped Pod `xvj2ohnvkpwa4f` could not restart ("not enough free GPUs on host"); replaced by
    `fgufhcarsat2sw` on the same volume. Lesson: delete, don't stop; everything lives on the volume.
- **21:42 — Rehearsal complete** (20 businesses, every stage through `predict-ft`, 3 pages per test
  set). Smoke 16 min; main 25 min for 108 steps (train loss 0.020, eval loss 0.021; peak GPU memory
  12.8 GB of 32); prediction stages 24 / 12 / 10 min, mostly model load and compile. Masking
  preflight passed; merged 16-bit export (8.3 GB) served by vLLM with valid JSON on every page.
- **21:51 — Bug found in the rehearsal report: base models scored 0%.** Their answers were good JSON
  but omitted `doc_type`, which has a schema default and so is optional under structured decoding;
  `predict.py` rejected every one as "Wrong document type". Fixed by filling it from the requested
  type, as the worker's validation does (`070d419`); re-scoring the saved raw answers fixed all 12
  statement pages. Smoke-size numbers (3 pages per set; not results): row F1 after balance check on
  unseen layouts 8B 0.93, 4B 0.59, fine-tuned 4B 0.96. One base-4B receipt returned broken JSON.
- **22:00 — Volume nearly full** (~89 of 100 GB: HF cache 38 GB, venvs 23 GB, uv cache 9 GB,
  rehearsal weights 18 GB). Deleted the rehearsal weights and data (kept logs, predictions and
  report, 25 MB); `run_pod.sh` now deletes the smoke run's merged copy after the export succeeds
  (`818ac37`). Tomorrow needs ~20 GB. Pod `fgufhcarsat2sw` deleted; no Pods running.
- **22:00 — Sizes for tomorrow** (400 businesses, seed 7, generated locally): 2,396 train, 289 val,
  1,790 test pages (1,499 unseen-layout, 291 seen-layout) plus CORD 800/100/100. Train steps
  ≈ (2,396 + ~720) / 8 ≈ 390.

### Spend so far
| Item | Cost |
|---|---|
| Network volume (100 GB, prorated) | ~$0.25/day |
| GPU time (crash loop 16:37–17:10, builds/tests to 17:57) | ~$0.52 total (balance $214.64 → $214.12) |
| Rehearsal Pods (RTX PRO 4500, $0.72/h, 18:45–22:00) and endpoint tests | ~$2.7 (balance $214.12 → $211.45 at 21:51) |

## Sat 10 Oct 2026

### Training (main run)
- **14:54 — GPU stock, EU-RO-1:** RTX PRO 4500 High ($0.72/h), RTX 5090 Medium, A100 SXM none.
- **14:56 — Pod `evli8q47at2gm6`** (RTX PRO 4500 Blackwell, 32 GB, driver 580.159; SSH ready in 25 s)
  on volume `0kelufi0wo`. Code at `8ba2185` by git bundle; both environments loaded from the volume
  (TRL 1.13, vLLM 0.31.0), so setup was skipped.
- **15:00 — Main run started:** `RUN=/workspace/ledgerlens GPU_PRICE=0.72 bash train/run_pod.sh`
  (400 businesses, seed 7, all stages through the report).
- **15:02 — Run stopped at `generate`:** Playwright's Chromium and its apt libraries were on the
  deleted Pod's own disk, while the `setup-train` marker is on the volume, so a fresh Pod skipped the
  install. The browser now lives on the volume and its libraries install before each render
  (`3acecdf`). Relaunched 15:09.
- **15:12 — Stopped again on purpose: every invoice was the same.** A screenshot of the app's invoice
  sample showed one "Office supplies" line, 5 × 12.50, tax 5.00, total 67.50 — and the generator
  printed that for every business (~800 identical invoices). The fine-tune would have memorised them
  and scored falsely well on invoices. Now 1–7 items from the business's trade, varied quantities
  (some by weight), prices, dates and numbering, the country's tax (GST 9% / SST 8% / PPN 11%,
  half-up), exact totals, and a structurally different second layout (`80c4471`). Statement pages
  are byte-identical (the app samples regenerated with only the invoice changing). Relaunched 15:15.

### App (second polish pass, Codex; reviewed with screenshots)
- **15:22 —** Counterparties keep their printed casing (grouping still case-insensitive, so anomaly
  detection is unchanged); partial months marked on the chart and in the memo ("3 months (2 full)");
  memo headings and bullets rendered (plain, `##` or `**` headings from the live model);
  row-number column no longer wraps; slider styled; a failed live call now shows "Live call failed —
  showing cached result" with the error, on the document card and in Under the hood. Reviewed the
  diff; one follow-up fixed directly: the generator's concentration label compares case-insensitive
  keys. 207 tests; Node render test of the memo, review cell and warning chip.
- **15:30 — Test set C tool (`train/phone_set.py`, Codex; reviewed).** `prepare` picks 20 held-out
  test pages (10 unseen-layout and 6 seen-layout statement pages, 4 invoices; all six statement
  layouts, three currencies, never the same business twice in a row) and writes a clean, numbered
  `print.pdf`; `ingest` pairs phone photos with gold answers by capture time (size check before
  decoding, EXIF orientation, HEIC refused) and writes a contact sheet to confirm the pairing;
  `score` runs the photos through the deployed endpoints and reports Runpod execution time and
  cost. Review fixes: key from `runpodctl` config, a failed call counts as a failed page instead of
  aborting, errors shown. Prepared locally from an identical seed-7 dataset (gold JSON hashes match
  the Pod's): `data/phone/print.pdf` (gitignored). 214 tests.
- **15:35 — Japan endpoint attempted, not created.** AP-JP-1 stock: H100 SXM and H200 only (both
  Low). `runpodctl serverless create` from the same template (H100, model cache, 0/1 workers)
  returned nothing on the first try and a retry was blocked by the session's permission policy for
  creating shared resources; `serverless list` shows only `szucrtrufff80a`. Needs Randy's go-ahead.
- **15:39 — Dataset built** (10 min). Real CORD receipts accepted: **771 / 99 / 98** of
  800 / 100 / 100 (rehearsal: 720 / 96 / 91), after the extra number formats. Train is therefore
  2,396 synthetic + 771 real = 3,167 examples (24% real); validation 289 + 99.
- **15:56 — Smoke passed** (16 min): masking preflight 1,040 supervised tokens; loss 0.114 → 0.024
  over 30 steps; eval loss 0.039 (388 validation pages, 136 s); peak GPU memory 13.1 GB of 32;
  merged export succeeded and was deleted as designed. **Main training started 15:56** — about
  396 steps for one epoch (batch 2 × accumulation 4), evaluation every 50 steps.
- **15:58 — Worker released `af39be4`** (`scripts/release-worker.sh`; first release since `a0bb907`):
  optional receipt fields in the extraction schema (now matching training), health reports
  volume-loaded weights as `volume`. Runpod's GitHub build finished in 5 min (template image
  `…:af39be437` at 16:03). Live check on the 8B endpoint follows.
- **16:10 — Live check after the release** (`app/sample/biz_0000_p002`, cold start, RTX 5090,
  weights from the model cache): valid JSON; rows **17/18** exact (16/18 on 9 Oct), header 7/8;
  queue delay 235.6 s (engine load 209 s), execution 15.8 s.
- **17:26 — Main training done** (396 steps, one epoch, 86 min on the RTX PRO 4500). Validation loss
  at each check (every 50 steps): 0.0199, 0.0154, 0.0113, 0.0103, 0.0098, 0.0092, 0.0085, 0.0084 —
  still falling at the end, so no sign of overtraining. Final train loss 0.0061 (average 0.0136).
  Peak GPU memory 13.1 GB. Merged 16-bit model (8.3 GB) at `/workspace/ledgerlens/main/merged`.
  Predictions (8B, 4B, fine-tuned 4B on 1,790 synthetic + 98 CORD test pages) started 17:26.
- **18:00 — 8B failure mode seen in the predictions: runaway numbers.** 9 of the first 368
  unseen-layout pages were invalid; 8 of them end in "…0000000…" at ~4,300 characters — the base 8B
  starts an amount as a JSON number and keeps writing zeros until the 4,096-token limit (mostly
  rupiah pages). The schema allowed a number of any length. The fine-tuned 4B writes amounts as short
  strings, as in its training targets. The running evaluation is left as is (all three models use
  the same schema); the fix is for serving and a follow-up 8B re-run.
- **18:03 — Fix: amounts are bounded decimal strings in the extraction schema**
  (`^-?[0-9]{1,15}(\.[0-9]{1,4})?$`); Pydantic still parses them to exact Decimals. 215 tests.
- **18:25 — Released `bf3053a` and checked live (8B, RTX 5090).** Lesson: the first two test calls
  (18:10) ran on workers still holding the old image — Runpod was still pulling the new one at
  18:15 (worker logs: "Downloading … Extracting"); a raw answer with JSON numbers gave it away. On a
  new-image worker: the page that had run away (`biz_0096_p004`, rupiah, held-out `stmt_f`) now
  returns valid JSON in 14 s (1,157 tokens instead of 4,096); the clean sample page still gets
  17/18 rows. On `stmt_f` pages the zero-shot 8B leaves debit and credit empty — it did the same
  with the old schema — a reading failure on that unseen layout, not caused by the fix.
- **19:13 — 8B predictions done** (1h47m incl. 10 min model load). Invalid JSON: 28 / 1,499
  unseen-layout, 1 / 291 seen-layout, 4 / 98 CORD — 29 of 33 are broken JSON (mostly the runaway
  numbers). Batched throughput on the Pod: 3.06 / 2.62 / 3.09 s per page — about $0.61 per 1,000
  pages at $0.72/h, against ~$6 on Serverless one page at a time: batching is the biggest cost lever.
- **20:19 — Base 4B predictions done** (55 min incl. 5 min model load). Invalid JSON: 13 / 1,499
  unseen-layout, 3 / 291 seen-layout, **34 / 98 CORD**. Every invalid receipt opened ends in
  "…000000…" — the same runaway-number bug: the Pod runs code from before the 18:03 fix (`80c4471`).
  Batched: 1.63 / 1.69 / 4.22 s per page. Decision: after the run, re-run both base models with the
  bounded schema (~3 h, ~$2) so the comparison matches what is served; the first results are kept
  as "old schema" to show what the fix changed.
- **21:10 — First full evaluation done (old schema; kept in `oldschema/` on the volume).** Fine-tuned
  4B predictions 50 min incl. load; report written. Row F1, raw model answer (A = unseen layouts,
  1,499 pages; A′ = seen layouts, 291; B = CORD, 98):

  | Model | A | A′ | B | Valid JSON A / A′ / B | s/page A | $/1,000 pages A |
  |---|---:|---:|---:|---|---:|---:|
  | 8B | 0.592 | 0.842 | 0.609 | 0.981 / 0.997 / 0.959 | 3.06 | 0.61 |
  | 4B | 0.626 | 0.773 | 0.333 | 0.991 / 0.990 / 0.653 | 1.63 | 0.33 |
  | 4B fine-tuned | **0.965** | **0.962** | **0.784** | 0.997 / 0.990 / 1.000 | **1.27** | **0.25** |

  Breakdown (scratch script over `results.json` + gold metadata): the 8B's unseen-layout score is
  dragged down by `stmt_f` (row F1 0.20 — it leaves debit and credit empty), while it reads `stmt_e`
  well (0.88). The fine-tune is 0.96–0.97 on every layout and 1.00 on clean pages; its weak spot is
  **heavy photo damage** (0.87 unseen, 0.84 seen). Looked at its failures: all 7 invalid answers are
  complete JSON with an impossible date (31 Jun, 29 Feb 2026, 31 Feb) on heavy pages; the lowest
  valid page (`biz_0083_p009`, `stmt_e`, heavy) has every amount right but reads "Jun" as "Jan" on
  every row — looked at the image: the blur makes them hard to tell apart, and the header prints
  "1 Jan 2026". Rows are matched by date and amount, so a month misread zeroes the page.
- **21:11 — Re-run of all three models started** on code `9a6450e` (bounded amounts), same Pod and
  data, so every reported number comes from the code that is served. Done markers for the three
  prediction stages and the report removed; train/data stages skipped.
- **23:50 — Fine-tuned 4B endpoint: first attempt failed** (Randy approved creating it; the Japan
  endpoint is dropped). `runpodctl serverless create --template-id ej6398fg30` refused: "This
  template is already bound to an endpoint" — one template per endpoint — and `--env` is ignored with
  a template. Made template `lj49qs9fw3` on the same image (`…:bf3053aaa`, `MODEL_PATH` set) and
  endpoint **`dckc0m4e4tjgk2`** (RTX PRO 4500 class, EU-RO-1, volume `0kelufi0wo`, 0–2 workers).
  Its workers never started: worker logs repeat "Failed to get Hub registry auth" — a hand-made
  template can't pull an image built by Runpod's GitHub integration. Scaled it to 0 workers at 00:05;
  account spend stayed at the Pod's $0.74/h, so the stuck workers did not bill. Fix: create the
  endpoint in the console from the GitHub repo (`deploy` branch), the way the 8B endpoint was made
  — there is no CLI/API for GitHub-built endpoints — then set `MODEL_PATH` and the volume.
- **00:20 — Fine-tuned 4B endpoint created in the console** (Claude drove the console in Randy's
  browser at his request): **`5078uudfkeaw2v`** `ledgerlens-4b-ft`, GitHub repo, branch `deploy`,
  `worker/Dockerfile` — it reused the existing build (`bf3053a`), no rebuild. Template `avnw8y6zs7`:
  `MODEL_PATH=/runpod-volume/ledgerlens/main/merged`, `MAX_MODEL_LEN=8192`, `GPU_MEM_UTIL=0.92`,
  container disk 40 GB. Volume `0kelufi0wo` (pins EU-RO-1). GPUs: 32 GB PRO first (only RTX 5090
  offered there, Low supply), 24 GB PRO second (RTX 4090) — the 8.3 GB model fits 24 GB. Workers
  0/2, idle 60 s, FlashBoot, queue delay 4 s, timeout 600 s, CUDA 12.9–13.4; checked with
  `runpodctl serverless get` / `template get`. Deleted the failed `dckc0m4e4tjgk2` and template
  `lj49qs9fw3`.
- **00:31 — Live check, fine-tuned 4B** (`app/sample/biz_0000_p004`, heavy damage, cold start,
  RTX 5090, weights from the volume — `model` reported as the volume path): valid JSON; execution
  **6.9 s** (8B on the same page, 9 Oct: 14.0 s); queue delay 214 s, of which engine load 151 s.
  Header 7/8 (closing balance wrong); rows **14/18** exact (8B zero-shot, same page: 7/18) — one
  digit misread, one reference digit, and one row cut short ("FAST FROM Goust", wrong amount).
  Serverless cost warm ≈ 6.9 s × $0.000439/s ≈ **$3.0 per 1,000 pages**, about half the 8B's.
- **00:33 — Re-run done; final evaluation (code `9a6450e`, bounded amounts — what is served).**
  Row F1, raw model answer:

  | Model | A unseen (1,499) | A′ seen (291) | B CORD (98) | Valid JSON A / A′ / B | s/page A | $/1,000 pages A |
  |---|---:|---:|---:|---|---:|---:|
  | 8B | 0.578 | 0.823 | 0.396 | 0.998 / 0.997 / 0.990 | 2.58 | 0.52 |
  | 4B | 0.614 | 0.745 | 0.259 | 0.991 / 0.986 / 1.000 | 1.72 | 0.34 |
  | 4B fine-tuned | **0.965** | **0.960** | **0.765** | 0.997 / 0.990 / 1.000 | **1.28** | **0.26** |

  Header accuracy A: 0.930 / 0.918 / 0.984. Reconciliation A: 0.30 / 0.20 / 0.75. Anomaly recall A:
  0.66 / 0.63 / 0.96. Model load 197 / 192 / 169 s. Results copied to `data/eval-final/`
  (gitignored), with every prediction from both runs.
- **00:40 — What the schema fix changed (looked at the answers).** Runaway numbers are gone (8B
  valid JSON on unseen layouts 0.981 → 0.998), but the base models got *worse* on real receipts
  (8B row F1 0.609 → 0.396, 4B 0.333 → 0.259). Counted over the 98 receipts: with amounts as
  strings, the 8B copies rupiah as printed — "17.500", which parses as 17.5 — 50 times (old run: 1),
  and leaves the amount null 53 times (old: 12) when the printed form doesn't fit the pattern. As a
  JSON number it had normalised them. The prompt says "no thousands separators" but doesn't say that
  the dot groups thousands in rupiah. The fine-tune writes plain digits as trained (no "x.xxx"
  amounts, nulls 15 → 18) and is unaffected: 111 of 1,888 of its answers differ between runs, net
  row F1 change ≤ 0.005 — within what batched GPU decoding varies run to run. Next step (not run,
  no Pod): a prompt line on rupiah grouping, then re-score the 8B on CORD.
- **00:41 — Pod `evli8q47at2gm6` deleted** after copying results. Spend now $0.01/h (volume only).
  Balance $203.46: whole project ≈ **$11.2** of $214.64.
- **00:52–01:07 — Live app test, fine-tuned 4B, sample business (5 pages, "Extract all").** First
  page waited **13.5 min** in the queue: EU-RO-1 had no free RTX 5090/4090 for Serverless (workers
  "throttled", console "100% of max workers busy"), then one RTX 4090 started (engine load 78 s).
  Warm on the 4090: statement pages 10.3–11.4 s, invoice 3.0 s, queue ~0.1 s; all five valid. The
  same heavy page took 6.9 s on a 5090 at 00:31. Cost is about the same either way (4090 tier is
  cheaper): ≈ $3 per 1,000 pages. Two consequences: the app priced every request at the 32 GB rate
  — now priced by the GPU the worker reports (5090/PRO 4500 $1.58/h, 4090 $1.10/h, 3090 $0.69/h);
  and the volume pin is a live demo risk — for the demo, set 1 Active worker on the fine-tuned
  endpoint well before it starts. Results tab rebuilt to show `eval/summary.json` (screenshots
  checked: headline set first, empty rows hidden, best values bold).
- **01:10–01:18 — Live memo, 8B (demo dry run).** The 8B replaced its FlashBoot-cached worker and
  cold-started (~5 min end to end), then streamed a full memo: sections, numbers from the computed
  analysis, row citations that jump to the evidence (e.g. the unreconciled heavy page `p4:r16`,
  SGD 499.00 off — the fine-tune's misread). 907 output tokens, 106 s in the worker. Two app bugs
  found by looking, fixed: memo cost showed $0.0000 (Runpod's `/stream` carries no
  `executionTime`; now read from `/status`), and a bold title line showed its asterisks. Still open
  (needs a worker rebuild): the memo doesn't report its GPU.
- **01:30–01:45 — Third endpoint, base 4B, approved by Randy** (so the app can compare all three
  models live): **`cuvgn5z2av09my`** `ledgerlens-4b-base`, created in the console from the GitHub
  repo (`deploy`, `worker/Dockerfile`, same build `bf3053a`); template `nygdqtrllt`
  (`MODEL_NAME=Qwen/Qwen3-VL-4B-Instruct`, 40 GB disk); model cache pinned to `ebb281e`; no volume,
  so any data centre; GPUs 32 GB PRO then 24 GB PRO (RTX 5090/4090); workers 0/2, idle 60 s,
  FlashBoot, queue delay 4 s, timeout 600 s, CUDA 12.9–13.4. App env var: `RUNPOD_ENDPOINT_4B`.
  Also: the fine-tuned endpoint's max workers had become 5 (account ceiling is 10 workers across
  endpoints; the console warned "only 3 remain") — set back to 2. The 8B endpoint was "waiting for
  GPU" (RTX 5090/3090 none free): added the RTX 4090 tier as a third choice.
- **01:52 — Live check, base 4B** (`app/sample/biz_0000_p004`, heavy, cold start on fresh hosts):
  queue delay 612 s (image pull on new machines, then engine load 295 s), execution 26.1 s
  (inference 11.1 s). Valid JSON, header 7/8, rows **0/18** exact: it files incoming payments as
  debits and leaves the balance empty — the accounting-convention failure the fine-tune fixed
  (fine-tuned 4B on the same page: 14/18; 8B zero-shot: 7/18). Weights from the model cache
  (snapshot `ebb281e`). The worker reported no GPU name on this host (neither `RUNPOD_GPU_TYPE` nor
  `nvidia-smi` answered); noted for the next worker fix.
- **01:55–02:04 — Worker GPU-name fix released** (`82efa15`): when neither `RUNPOD_GPU_TYPE` nor
  `nvidia-smi` answers, the worker reads the model name from `/proc/driver/nvidia/gpus/*/information`;
  the memo's final chunk now carries `gpu` too. `release-worker.sh` → `deploy`; all three templates
  showed the new image at 02:04. Handoff notes rewritten for the current state.
- **02:06 — Row precision and recall shown, not just F1.** Randy asked whether we only measure
  accuracy. The headline was already row F1 (precision and recall combined), but only F1 was shown,
  and the docs said "rows read right", which sounds like accuracy. Added row precision and recall to
  `eval/summary.json` (regenerated from the same `results.json`; only the two fields were added), the
  Results tab and `docs/REPORT.md`; README and report now say "row F1". Finding: precision and
  recall are within ~1.5 points on every set and model (fine-tuned 4B, unseen layouts: 0.966 /
  0.963), so the models rarely invent or drop rows; their errors are misread fields. Red flags are
  where they split (fine-tuned 4B recall 0.96, precision 0.51).
- **02:12 — Corrected what "a correct row" means in the docs.** Checking the scorer
  (`src/ledgerlens/eval/score.py:row_metrics`) before a live score showed a statement row is matched
  on **date, debit and credit** only; description and balance are not compared row by row (balance is
  covered by reconciliation). Invoice/receipt items match on description, quantity, unit price and
  amount. The report had said "(date, description, amounts, balance)" and the 02:06 edit said "every
  field" — both overclaimed. README and REPORT now say exactly what is compared.
- **02:04–02:10 — Live check after the worker release** (`app/sample/biz_0000_p004`, all three
  endpoints at once, all cold): every reply now names its GPU.

  | Endpoint | GPU reported | Queue delay | Execution | Valid JSON |
  |---|---|---:|---:|---|
  | 4B fine-tuned (volume weights) | NVIDIA GeForce RTX 5090 | 148 s | 6.8 s | yes |
  | 4B base (model cache `ebb281e`) | NVIDIA GeForce RTX 5090 | 218 s | 8.2 s | yes |
  | 8B (model cache `0c351dd`) | NVIDIA GeForce RTX 5090 | 317 s | 26.0 s | yes |

  Rows on the page (18 in the gold; scored with `score_document`): fine-tuned 4B **15/18**
  (P 0.88, R 0.83), 8B 11/18, base 4B 7/18; header 8/9 for all three; none reconciles. Same page,
  temperature 0, yet earlier runs gave 14, 7 and 0: GPU kernels aren't bit-exact across hosts and
  GPUs, and one wrong token early in a page changes the rows after it. **One-page live checks are
  smoke tests, not evidence of quality** — the 1,499-page batch is. The ranking (fine-tuned 4B first)
  held in every run.
- **02:12–02:18 — Live demo dry run on the released build** (app live: `RUNPOD_ENDPOINT_8B`,
  `RUNPOD_ENDPOINT_FT`; model picker on the fine-tuned 4B; sample business, 5 pages). Screenshots in
  `../.playwright-mcp/dryrun-*.png`.
  - Extract: p1 cold (queue 145 s), then p2–p5 warm (queue ~0.1 s), 6.3–7.1 s per statement page,
    1.7 s for the invoice; ≈ $3 per 1,000 statement pages; RTX 5090 reported on every page.
  - Review and Results render; Results now shows row precision/recall.
  - Analysis flagged "unreconciled page p4, off by SGD 499.00". Not planted: p4's printed closing
    balance equals the true one. On this heavy-damage page the model misread one digit (1,426.01 for
    1,425.01), row 17's credit and balance, and missed the last row. The balance check caught the
    model's mistake — a good demo moment ("the arithmetic catches the model; the analyst fixes the
    row in Review").
  - Memo (fine-tuned 4B): 50.7 s, $0.0224, 4,538 tokens, GPU reported — the $0 cost and missing GPU
    are fixed. Three problems found by looking:
    1. **Citations one row early.** The analysis gives 0-based `row_index`; the model copies it into
       `[p1:r3]`, but the UI reads r as 1-based (Analysis shows the same cheque at p1:r4).
    2. Inline `**bold**` and `---` lines shown raw.
    3. Grouped `[p1:r2; p2:r1]` and range `[p1:r1–r18]` citations not clickable.
    Sent to Codex (backend: send 1-based `"p1:r4"` strings to the worker, no worker rebuild needed;
    frontend: inline bold, rules, grouped/range citations, numbered lists).
  - Spend: balance $202.45, $0.01/h idle.
- **02:20–02:25 — Memo citation and rendering fix (Codex), reviewed and checked live.** Backend sends
  the worker 1-based `"p1:r4"` strings instead of `{page_id,row_index}` objects (analysis and the
  Analysis tab unchanged; checked that every such object in the analysis is a pure citation, so
  nothing else is collapsed). `MemoContent`: inline bold, `---` rules dropped, grouped and range
  citations as one button each, numbered lists. 220 tests pass; frontend test run outside the
  sandbox. Live memo: citations now match the Analysis tab (returned cheque p1:r4, cash deposit
  p2:r8, unreconciled p4:r17, round trip p3:r17); clicking [p1:r4] lands on "RETURNED CHEQUE
  1,499.51" (`dryrun-5-memo-fixed.png`, `dryrun-6-citation-jump.png`).
- **02:27 — The memo model does arithmetic, and gets it wrong.** Checking the live memo's numbers by
  hand: "Total Outflow SGD 54,987.85" — the three months sum to 55,041.59 (the 02:17 memo said
  54,539.59, also wrong), and its net was derived from it. The analysis has no period totals, so the
  model added them despite "Do not calculate". The UI's "the model only writes the prose" was not
  true. Fix sent to Codex: period totals computed in `analytics.py`; after a live memo the backend
  checks every money-like number and percentage against the analysis it was given, and the UI
  highlights any that don't match. No worker rebuild (prompt unchanged).
- **02:28–02:40 — Number check reviewed and verified live; two more model slips fixed.**
  - Codex's change reviewed: `period_totals` in `analytics.py` (Decimal); `unverified_memo_numbers()`
    compares every money-like number and percentage in a live memo (citations ignored) with the
    values and finding texts the model was given, rounded forms allowed; the UI highlights misses
    and shows a warning. 224 tests pass; frontend test and type check run outside the sandbox.
  - Live (02:30): totals now right (outflow 55,041.59, net 34,177.07), nothing flagged. The check run
    on the live memo text returns `[]`; on the old wrong total it returns `['54,987.85']`.
  - New slip 1: that memo wrote citations as `(p1:r2)` and `(p1:r1–p1:r18)`, not clickable. Fixed
    directly: the payload now carries `"[p1:r4]"` (brackets, which the model copies) and the renderer
    also accepts round brackets, bare refs and page-qualified ranges. Live (02:36): all citations
    are buttons.
  - New slip 2: the 02:36 memo dated the large cash deposit "6 Mar"; the row is 30 Jan (CASH
    LODGEMENT). The finding gave no date, so the model invented one; the number check covers
    amounts, not dates. Fixed: the finding now reads "SGD 12,071.87 · 30 Jan · CASH LODGEMENT ·
    6.3× the median credit", and the median is rounded to cents (the memo had copied "1,929.095").
  - Lesson: each fix was found by reading the live output against the data, not by
    tests. Give the model every fact it needs (totals, dates) so it has nothing to make up, and
    check what it writes.
- **02:40 — Number check: gap closed, limit stated.** An automated commit security review flagged an
  "integrity check bypass" in `app/backend/main.py` without details. Reviewing the check myself:
  (1) plain whole-number amounts ("SGD 55041") were not checked at all — now they are (4+ digits,
  years 1900–2100 skipped; a number ending a sentence is handled), and a whole number may drop the
  cents ("55,041" for 55,041.59); (2) a wrong number that equals some other real figure (January's
  outflow given as the total) still passes — the check proves a number came from the analysis, not
  that it is used in the right place. Stated as a limit. 226 tests pass; the 02:30 live memo still
  checks clean.
- **02:41–03:05 — Phone-set pipeline checked end to end; clean baseline measured.**
  - Ingest dry run with the 20 clean pages as stand-in photos: 20 pairs, `pairs.html`, test file.
    iPhones save HEIC by default; the error now gives the `sips` command, and the handoff says so.
  - Scoring dry run (fine-tuned 4B only) worked end to end: report, summary, GPU name, precision and
    recall. Not a phone result.
  - Then scored the same 20 clean pages on all three endpoints as the **baseline for test set C**
    (`data/phone_clean_eval`, gitignored; RTX 5090 on all three, so $1.58/h is the right price):
    row F1 8B 0.664, 4B 0.682, fine-tuned 4B 0.984; seconds per page (live execution) 12.0 / 6.9 /
    5.6; $ per 1,000 pages 5.26 / 3.04 / 2.48. Base models reconcile 0 of 20 pages. In `docs/REPORT.md`.
  - Results tab: phone-set labels, a note per set (the phone sets are live, not batched), a
    `train.report merge` command to add them to `eval/summary.json`; "(batched)" dropped from the
    seconds label since not every set is batched. Checked by merging the baseline into a copy and
    screenshotting (`results-phone-clean-merge.png`); the committed summary is unchanged until the
    photos are scored. Spend for the baseline ≈ $0.3.

- **11:47–12:32 — Phone photos (test set C) scored; results in the report, README and Results tab.**
  - Randy's 20 photos arrived in `data/phone/scanned` as chat-app JPEGs: 1280 px long side (about
    1.1 MP; clean pages are 2 MP) and no EXIF time. The first ingest paired by file name and every
    pair was wrong; caught by looking at photo/clean side by side.
  - Read each photo's printed footer ("LedgerLens phone test NN/20") instead: 18 paper photos map to
    18 different pages; page 01 was shot twice off a laptop screen and page 17 is missing. Both left
    out. Copied to `data/phone/photos_by_page/NN.jpg` and re-checked every pair by eye.
  - `phone_set ingest` now pairs `NN.jpg` files by footer number and allows gaps (prints the missing
    pages); other names keep the EXIF-time rule. Codex implemented, reviewed; 232 tests pass.
  - Clean baseline on the same 18 pages: re-scored the stored clean predictions (no new calls).
  - Live run 11:51–12:10, all three endpoints, workers 0 (one cold start each, as in the clean run),
    RTX 5090, no failed calls. Row F1 photo (clean): 8B 0.411 (0.671), 4B 0.258 (0.723), fine-tuned
    4B **0.672 (0.984)**. No model reconciles a photographed page (fine-tune clean: 79%). Seconds per
    page 11.5 / 8.6 / 5.4; $ per 1,000 pages 5.03 / 3.77 / 2.37.
  - Looked at the fine-tune's three zero pages: p07 months read as Jan instead of May; p12 rupiah
    "8,383,500" read as "8383.500"; p16 impossible date 30 Feb (invalid). Elsewhere single-digit
    misreads, which also break reconciliation.
  - Honest reading: ranking and cost hold on real photos; near-perfect accuracy does not; camera and
    chat-app compression are measured together. Next: re-shoot at full resolution.
  - Merged `test_phone` and `test_phone_clean` into `eval/summary.json`; Results tab checked by
    screenshot (`results-phone-photos.png`). Spend: balance $201.39 → $200.73 (≈ $0.66); back to
    $0.01/h at 12:11:46.

- **UI revamp on branch `ui-revamp` (worktree `runpod-ledgerlens-ui`), for review before merge.**
  Planned and checked by Claude, built by Codex in seven cycles; each cycle was screenshotted at
  1440px and 390px and the problems found went into the next cycle.
  - Design: five rounds of mockups; Randy chose "Showcase × Bento" with a left icon rail, dark only,
    plain words for non-technical users, no marketing taglines.
  - Backend: dataset, training and results insights API; the base 4B as a third endpoint
    (`RUNPOD_ENDPOINT_4B`); compare one page across all three models; full page index
    (4,475 pages); dataset upload checked to the pipeline's own rules (leakage between groups blocks).
  - Training from the UI: simulated unless `TRAINING_ENABLED=1` and not `DEMO_MODE`. A real run needs
    a plan review, a one-time confirm token, a spending limit (≤ $25), a guard on the shared volume,
    and a cleanup that always runs and checks the Pod is deleted; if it can't confirm, the UI shows
    "POD MAY STILL BE RUNNING". Every command refuses to touch `/workspace/ledgerlens`, where the live
    model and the reported run are. The quick preset uses the 9 Oct rehearsal: ~3 h, ~$2.70.
  - Pages: Read statements (the old flow, rebuilt), Compare (live, or recorded test examples
    offline), Explore data (EDA, page browser with preview and answer key, upload), Training (replay
    of the reported run, history, guarded new run), Results (all scores, hardest pages), Cost
    calculator. Presenter mode runs the 3-minute tour. Exports: rows CSV, memo as PDF, scores CSV.
  - Rebased onto main's memo fixes (number check, 1-based and grouped citations); the citation
    buttons keep main's `[p1:r4]` text, with "p1 · line 4" as the tooltip.
  - Last fixes from the screenshots: tour step 4 opens the recorded example biz_0007_p006 even when
    pages are loaded; amounts always show two decimals (16.20, not 16.2). 312 tests pass, ruff
    clean; first-load JS 181 kB (59.8 kB gzip) after route splitting.
  - Polish pass (2026-10-11), from a second look at the screenshots:
    - The loss chart's log axis now ticks at powers of ten, so its caption ("each labelled line is
      ten times lower") is true; before, ticks read 0.1/0.06/0.03 under the same caption.
    - One header pill when all models share a state ("Live models off · recorded results still
      work") instead of three "offline" pills on every page.
    - Compare opens on the recorded example unless a live model is reachable; the duplicate model
      state tile became a short "two ways to compare" explainer; page headers share one top offset.
    - Results: the CSV download is a small outline button; the hardest-pages gallery (20 pages per
      test set, 60 in all) shows one set at a time, 8 cards, "Show all 20".
    - Phone: Read rows stack per line so Balance and the "adds up" tick are visible at 390 px.
    - Runs are numbered ("Run 2 · simulated, no GPU used · 11 Oct, 02:28 · done"), the id in a
      tooltip; "cached demo" reads "Recorded answer (demo)"; damage labels are capitalised; the
      period tile reads "1 Jan to 30 Jun 2026".
    - Cost: monthly figures use the rate as displayed ($0.26 × 50 = $13.00, not $12.77); the live
      big-model line reads "$6.00 to $6.60 per 1,000 pages … $300 to $330 per month".
    - 312 tests pass, ruff clean; first-load JS 181.8 kB (60.1 kB gzip).
  - Rebased onto main 82d76e4 (phone photos scored). Results renders both phone sets with their
    notes; checked by screenshot at 1440 and 390 px. The page footer said every set ran in one batch
    on the RTX PRO 4500, which is no longer true, so it now says the phone sets ran live on Serverless
    (RTX 5090, $1.58/h). "What the test tells us" gains a phone finding computed from summary.json
    (67.2% vs 41.1% and 25.8%, down from 98.4% clean; no photographed page adds up). 317 tests pass.

- **14:40–15:00 — ui-revamp merged (Randy approved); walked every page live; fixes from looking.**
  - Fast-forwarded `main` to the `ui-revamp` branch (04b53ec, 10 commits): new shell, Compare, Data,
    Training (simulated unless `TRAINING_ENABLED=1`), Results, Cost, Presenter tour. 316/317 tests:
    a frontend test read `docs/ui-mockups/tour-script.md`, untracked in the branch's worktree; now tracked.
  - Live walk-through with all three endpoints: sample business read on the fine-tuned 4B (first page
    cold, ~2.5 min; then ~6 s per page; RTX 5090), p1 18/18 balances add up, p4 1 to check; flags
    and memo correct (outflow 55,041.59; no unverified numbers); Compare 18/18 vs 11/18 vs 11/18.
  - Fixed from looking (not caught by tests):
    - Header said "All 3 models ready" and the first read still cold-started: Runpod counts
      scaled-down workers as idle. "Ready" now needs a running worker; idle shows "on standby · the
      first read can take a few minutes". Before the first status poll it says "Checking live
      models…" instead of "Live models off".
    - Tour step 1 called the synthetic sample "photographed on a phone"; now "made to look like a
      phone photo; the business and bank are fictional".
    - Results: on screens under ~1,400 px the side-by-side cards hid the base-4B column behind a
      sideways scroll; cards now stack there. Checked at 1200 and 1512 px: no hidden columns.
    - Cost: live rates for the fine-tuned and base 4B said "not measured yet"; they now come from the
      clean phone-set run (all three live on one RTX 5090): $2.48 / $5.26 / $3.04 per 1,000 pages.
    - Training: "Both keep falling, so it is learning the skill rather than memorising" overclaimed
      (training loss is flat after ~step 50); now "the held-out check is still falling at the last
      step, so there is no sign of overtraining".

### 2026-10-11 16:30 SGT: Presenter button cut off on short windows
- Randy saw the bottom of the left rail cut off, Presenter included. Measured in a test browser: the rail is a fixed 100vh with no overflow, and its content needs about 570 px, so on a shorter window (a laptop at 125% zoom) Presenter is pushed off screen and can't be reached.
- Fix (`app/frontend/src/style.css`): the rail scrolls if it must (`overflow-y:auto`), and below 720 px tall the rail items are tighter. At 480 px the rail needs 465 px; Presenter is visible. Tall windows are unchanged. 317 tests pass.

### 2026-10-13 21:58 SGT: README gets a Serverless checklist, explains unbaked weights, adds curl
- Review before publishing found three gaps. (1) The repo was private. (2) A common Serverless pattern bakes the model into the image; we load it from the model cache (8B) and a network volume (fine-tuned 4B). Now stated in the README as a deliberate choice, with the reasons and how to bake instead (`MODEL_PATH` plus the commented Dockerfile lines). (3) There was no copy-paste request for Postman or curl users.
- README: new "Runpod Serverless checklist" table near the top, and a "Call the endpoint with curl" section under Run it (`/run` then `/status`, `/stream` for the memo; not `/runsync`, which dropped during a cold start). The payload recipe was checked locally: valid JSON, and the sample PNG decodes. No live call was made, so no spend.
- Secret scan before going public: 221 tracked files and the full history; only the fake token in `tests/app/test_training.py`.

### 2026-10-14 09:48 SGT: MIT licence and credits; repo made public
- Licences before going public. Added `LICENSE` (MIT, Randy Chan) and a README "Licence and credits" section. Each upstream licence was checked at its source (Hugging Face API, GitHub licence API): Qwen3-VL-8B/4B-Instruct Apache 2.0; CORD v2 CC BY 4.0 (citation copied from clovaai/cord); vLLM, Unsloth, TRL, Transformers, Datasets and Playwright Apache 2.0; Runpod SDK MIT. App fonts (Sora, IBM Plex Mono) load from Google Fonts under OFL 1.1 and are not bundled. No CORD files are tracked; the fine-tuned weights stay on the volume, unpublished.
- Before the switch: reviewed every tracked non-code file and grepped the full history for secrets. The only token is the fake one in the tests.
- Visibility switched with `gh repo edit --visibility public` (personal token, scoped per command). `deploy` untouched, so no rebuild and no spend. 317 tests pass.

### 2026-10-14 10:47 SGT: Repo tidied before going public
- Made private again while tidying. Full history saved first as a git bundle (outside the repo).
- History rewritten with `git filter-repo` (both branches, 84 → 82 commits; two only touched removed files): planning and handoff notes (`docs/PLAN.md`, `docs/HANDOFF.md`) and the old agent instructions removed from every commit; personal notes and an absolute local path in `scripts/build_insights.py` (now relative to the repo) rewritten in every version. Worker code, `src/`, `pyproject.toml` and `uv.lock` are byte-identical on `deploy` before and after.
- Agent setup republished clean: `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json` and the `build-log`, `codex-workflow`, `runpod-ops` skills. Planning notes stay local (gitignored).
- After the force-push, GitHub still served the old commits through the repo's Activity view, so the GitHub repo was deleted and recreated with the clean history (same name). Broad tool approvals (`uv run:*`, `runpodctl:*`) moved from the committed `.claude/settings.json` to the local settings file.
- Runpod rebuilt all three templates from the clean `deploy` (image tag `9268e8075`). Health check on `szucrtrufff80a`: ready, model from cache, cold start 222.5 s (engine load 214 s), ≈ $0.10.

### 2026-10-14 15:29 SGT: Plain text prompts and a baked-model image
- Live check of the README's curl recipe (8B `szucrtrufff80a`, `app/sample/biz_0000_p001.png`): COMPLETED, 18 valid rows, RTX 5090; delay 225.6 s (cold start), execution 16.9 s.
- So the endpoint takes a plain text prompt as Runpod's Serverless quick-start does: new `generate` task. `{"input":{"prompt":"..."}}` (no task) returns generated text; optional `system`, `image_base64` (same checks as extract), `max_tokens` ≤ 4096 (default 512), `temperature` 0–2 (default 0.7), `stream`. Extract and memo unchanged (temperature 0). `worker/client.py generate` added. Implemented by Codex from a written brief; reviewed diff, 345 tests pass outside the sandbox.
- `worker/Dockerfile.baked`: same steps as `worker/Dockerfile` plus the 8B weights downloaded at build time (pinned revision `0c351dd`), `MODEL_PATH=/models/model`, `HF_HUB_OFFLINE=1`. No network volume, so not pinned to one data centre. Not yet built or measured; needs its own endpoint (console: GitHub build, Dockerfile path `worker/Dockerfile.baked`). A test keeps its shared steps in step with `worker/Dockerfile`.

### 2026-10-14 19:43 SGT: Chat agent design; streamed text sent in batches
- Design for the next stage: a chat agent on one GPU. One Qwen3-VL-4B engine serves every model step, with the trained reader as a LoRA adapter switched on per request; arithmetic, data lookups and checks stay in code. Agents: router + planner, page triage, readers, analyst (code), data agent with five read-only tools, writer (memo in five sections written at once), checker (code). Page reading starts when pages are attached, before the user sends a message. A model survey (Qwen3.5, Gemma 4, GLM-OCR, dots.mocr, PaddleOCR-VL and others) found no open model that clearly beats this setup under our limits (one 24–32 GB GPU, vLLM 0.31, multi-LoRA, Apache/MIT); on the one independent photographed-document benchmark Qwen3.5-9B scored below Qwen3-VL-8B.
- Root cause of slow memos found in code: the Runpod SDK (1.12.0, `rp_job.handle_job`) sends one awaited HTTP POST per value a generator handler yields, and the handler yielded every token. That is why a live memo took 106 s for 907 tokens (8B) and 50.7 s (fine-tuned 4B).
- Fix (`worker/handler.py`): buffer streamed text and yield every 64 characters or 200 ms (`STREAM_FLUSH_CHARS`, `STREAM_FLUSH_MS`), flushing the rest before `done`; output format unchanged. The app polls `/stream` every 0.25 s instead of 1 s. Implemented by Codex from a written brief; diff reviewed; 192 worker and app tests pass. Not yet released or timed live.

### 2026-10-14 20:16 SGT: Chat agent inside the worker
- New tasks on the worker. `read`: pages (≤ 8) are read at once, each on its own: triage on a small image (is it a financial document, which type, readable) → reader with the exact extract request → schema check → debit/credit fix for statements. A slow page doesn't hold up a fast one; an unreadable page is reported and the rest carry on. `agent`: the router (JSON plan) runs while pages are read and the analysis is computed in code; then a fixed reply (no documents, off-topic), a short general answer, a memo (five sections streamed at once, each checked in code for unsupported figures, bad row references, length and heading, and rewritten once if it fails), a memo edit (only the named sections), or a data answer (five read-only tools, ≤ 3 rounds, several calls per round run together, answer checked against the tool results).
- One event stream per job: `step`, `triage`, `rows`, `route`, `deltas`, `section`, `tool`, `final`, `error`. Text from all sections goes through one queue and is sent in batches (64 characters / 200 ms), so the SDK's one-POST-per-yield cost stays small.
- `worker/handler.py`: the handler is now async; old tasks run unchanged in a thread; Runpod's `concurrency_modifier` allows `MAX_CONCURRENCY` (4) jobs per worker. A plain `prompt` now goes to the agent (`task: "generate"` keeps the old path). vLLM starts with `--enable-auto-tool-choice --tool-call-parser hermes` for the data tools. Model names per role come from env (`BASE_MODEL`, `READER_MODEL`, `MEMO_MODEL`) so a LoRA engine can switch the reader adapter on per request.
- Implemented by Codex from a written brief. Review fixes: tool flags added; the assistant turn sent back in the tool loop is rebuilt from content + tool calls only; one bad page no longer fails the whole job; a plain prompt with an image becomes page `p1`. 460 tests pass. Not yet released; no live calls.

### 2026-10-14 20:45 SGT: Assistant chat page in the app
- New first page, `/assistant`: a chat thread plus an evidence panel. Pages are read the moment they are attached or the sample business is loaded (`POST /api/sessions/{s}/read`, the worker's `read` task), and the rows land on the same page records the Read tab uses, so a person can still check and fix them. Opening the page sends one `health` job to wake a worker (at most once a minute per session).
- `POST /api/sessions/{s}/chat` sends the cached rows (not the images) plus any unread pages, the last 10 turns and the last memo to the worker's `agent` task, and relays its events as server-sent events. If a read is still running, the turn waits for it rather than reading the pages twice. Pages are shrunk to the reader's pixel cap as JPEG before sending, so 8 pages fit Runpod's 10 MB limit. Stop aborts the request and cancels the Runpod job.
- Each turn shows the step trail with times (router, triage and reader per page, analyst, writer per section, checker, tool calls), the route and its reason, then either the five memo sections filling in at once (with "rewritten once" when the checker sent one back) or the answer, and a footer with total time, queue/cold start, execution time, tokens, GPU and cost. Citations open the row in the Read tab.
- Offline demo: sample pages "read" from their ground truth; suggested prompts replay event streams recorded from the live agent (`app/sample/agent/`), labelled "Recorded run". The Presenter tour starts on this page (now 8 steps, 3:30).
- Implemented by Codex from a written brief. Review fixes: the original Presenter made generic instead of copied, demo notice labelled as such, metrics footer shows only measured values, sample button hidden once pages exist, compact page chips. Checked in a browser at 1280 and 400 px (no sideways scroll). 480 tests pass; frontend builds.

### 2026-10-14 21:08 SGT: Reader as a LoRA adapter matches the merged model (gate E1)
- Why: the chat agent should run on one engine, the base Qwen3-VL-4B with the trained reader switched on per request as a LoRA adapter, instead of the merged fine-tune. That only works if the adapter reads pages as well as the merged weights, at a similar speed.
- Pod `02h4ri0q6a44u1`: RTX PRO 4500 Blackwell 32 GB, $0.72/h, EU-RO-1, network volume at `/workspace`, vLLM 0.31.0. Runbook `train/lora_serve_check.sh`; benchmark `train/serve_bench.py` (the worker's exact extract request). Data: the first 200 pages of `test_unseen_layout`. Started 19:46 SGT; SSH took ~10 min to reach because port 22 wasn't exposed at create (`runpodctl pod update --ports "22/tcp"`).
- Accuracy (scored with `ledgerlens.eval.score`):

| Reader | Pages | Row F1 (micro) | Mean page F1 | Header fields | Invalid JSON |
|---|---|---|---|---|---|
| Merged fine-tune | 200 | 0.9647 | 0.9684 | 0.9872 | 0 |
| Base 4B + reader LoRA | 200 | 0.9643 | 0.9681 | 0.9872 | 0 |

  187 of 200 outputs are byte-identical; the difference is 0.0004 against a 0.01 limit. **E1 passes: one base engine with adapters (topology A).**
- Speed, same GPU:

| Run | Pages | Seconds | Per page | Output tokens/s |
|---|---|---|---|---|
| Merged, one at a time | 20 | 205.7 | 10.3 | 84 |
| LoRA, one at a time | 20 | 229.7 | 11.5 | 75 |
| Merged, 8 at once | 200 | 343.6 | 1.72 | 513 |
| LoRA, 8 at once | 200 | 372.5 | 1.86 | 473 |
| Base 4B (no adapter), 8 at once | 40 | 96.6 | 2.41 | 469 |

  The adapter costs ~11% one at a time and ~8% at 8 concurrent pages.
- Server start (to `/health`), weights read from the network volume: merged 849.8 s (first start: cold volume plus first compile), LoRA 547.6 s (probably reused the merged model's compile cache, same architecture), LoRA with `cudagraph_specialize_lora: false` and an empty compile cache 752.7 s. These are first-read numbers on a cold network volume, not the Serverless cold start; the warm-restart and trimmed-graph stages are still running.
- Merged model logs a transformers warning about an "incorrect regex pattern" in its tokenizer. Checked: the tokenizer files differ only in format; vocab, added tokens and chat template are identical, and sample strings (dates, amounts, Chinese, Bahasa, JSON) encode to the same ids. Harmless.
- Spend so far: Pod ≈ 1.4 h ≈ $1.0. Balance $198.38.

### 2026-10-14 21:25 SGT: What a saved compile cache buys at start-up
- Same Pod, base 4B + reader LoRA, `cudagraph_specialize_lora: false`, compile cache at `VLLM_CACHE_ROOT=/workspace/vllm-cache-p0`. Engine numbers from the vLLM log:

| Start | Weights load | torch.compile | CUDA graphs | Engine init total | To `/health` |
|---|---|---|---|---|---|
| Empty cache | 39.0 s | 119.7 s | 32 + 17 s | 278.1 s | 752.7 s |
| Saved cache | 43.9 s | 1.8 s | 13 + 15 s | 96.5 s | 534.6 s |

- A saved compile cache cuts engine start by ~180 s. The rest of the ~535 s is Python importing vLLM from a virtualenv on the network volume, a Pod-only cost: on Serverless vLLM is in the image on local disk. Decision: the agent endpoint keeps its compile cache on the network volume (`VLLM_CACHE_ROOT`); it is only reused on the same GPU type.
- Throughput with specialisation off: 200 pages in 379.6 s at 8 concurrent (vs 372.5 s with it on), so turning it off costs ~2% speed and saves a second set of CUDA graphs.

### 2026-10-14 21:55 SGT: Fewer CUDA graph sizes: small gain; P0 serving check finished
- Capture sizes trimmed to `[1, 2, 4, 8, 16]` (from vLLM's default 51 sizes): graph capture 14 + 3 s (vs 13 + 15 s), memory for graphs 0.18 GiB (vs 0.92 GiB). It changes the compile settings, so the first start recompiles (106.9 s). Reading speed unchanged: 200 pages in 380.0 s at 8 concurrent. Worth keeping for an endpoint that serves at most a few users at once; small saving (~11 s).
- Serving settings chosen for the agent endpoint: base 4B, `--enable-lora --max-loras 2 --max-lora-rank 16`, reader adapter from the network volume, `cudagraph_specialize_lora: false`, capture sizes `[1, 2, 4, 8, 16]`, compile cache on the volume.
- Next on the same Pod: the live chat-agent check (`train/agent_live_check.sh`).

### 2026-10-14 22:20 SGT: First live run of the chat agent; two router bugs fixed
- Same Pod, one vLLM engine: base 4B + reader LoRA + tool calling (`train/agent_live_check.sh`, 9 scenarios on the sample business).
- Worked: `read` of 6 pages in 18.1 s total (triage of all 6 in ~2.4 s, at once; the invoice's rows at 6.9 s, the four statements at 17–18 s). The non-document photo was rejected at triage; all 5 real pages valid. General answer 2.0 s, off-topic reply 1.1 s, a one-section memo 2.3 s.
- Bug 1: with 5 pages loaded the router answered "needs documents" ("No documents are currently loaded"). It was given the page counts as raw JSON after the message and ignored them. Fix: code alone decides "no documents" (it already knows); `needs_documents` removed from the router's choices; the context is now plain sentences before the message ("Documents loaded: 4 statement page(s), 1 invoice page(s). A memo has been written: no.").
- Bug 2: memo requests failed with "Expecting ',' delimiter": the router repeated section names and padded its JSON with blank lines until it hit 400 tokens. Fix: section lists capped at 5, router `max_tokens` 600, and vLLM started with `--structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'` (vLLM refuses the option unless the backend is named; xgrammar is what `auto` had been picking). The reader was trained on compact JSON, so the ban on free whitespace matches its format.
- Unit tests pass (479). Second live run started.

### 2026-10-14 22:40 SGT: Second live run: no errors, but reading the answers found four quality bugs
- All 9 scenarios ran without errors. Times (warm engine): memo from pages already read 3.7 s (first text at 2.3 s); data questions 1.6–2.1 s; memo edit 1.9 s; general answer 1.3 s; off-topic 0.8 s; reading 6 pages 20.7 s; memo sent together with 2 new pages 17.4 s (reading is most of it, which is why the app reads pages on attach).
- Read every answer instead of trusting "no errors"; four were wrong:
  1. The memo had 2 of 5 sections: the router's plan listed `red_flags` four times. Fix: a new memo always gets all five sections; the router picks sections only for edits.
  2. "Write a memo" with pages still being read, and with no pages, got a general answer ("No documents are loaded, so a memo cannot be written"). The router was still judging document status. Fix: the router no longer sees it at all; code alone turns a memo/data request without statements into the fixed "please attach documents" reply.
  3. "Who is the largest payer?" called `top_counterparties` with `direction: "out"` and named the payroll account, the largest *outgoing* payment, and printed the share as `0.2409794121…`. Fix: the tool description says what in/out mean, results carry `share_percent` (rounded, 1 dp), `currency` and `direction`, and the data agent is told to use them.
  4. "Lowest net cash flow" answered "2026-03, 935.99" with no currency and no row reference. Fix: every tool result carries the currency; the data agent must write the currency and cite up to three rows.
- Also: the Red flags section now has to cite each finding's rows. 480 tests pass. Third live run started.

### 2026-10-14 22:55 SGT: Third live run is right; Pod deleted; real recordings drive the demo
- All 9 scenarios correct on routing: memo, data questions, edit, general answer, "please attach documents" (code override from `memo`), off-topic. Warm times: memo from read pages 3.9 s (first text 2.1 s, five sections written at once, 1.6–2.3 s each); memo with 2 new pages 19.1 s (reading ~16 s of it); "largest payer" 1.8 s (`top_counterparties` direction `in`: Senja Hijau Damai Niaga, 20.1%, three row references); "lowest net month" 2.0 s (2026-03, SGD 935.99); memo edit 2.5 s; general answer 1.3 s; fixed replies 0.8 s.
- Writer weaknesses still visible (base 4B, no memo adapter yet): Cash flow wrote "$" because its slice of the analysis had no currency (fixed: every section now gets the currency); it once echoed its instructions ("*(49 words — under 120.)*") and the "shorter" edit was not shorter (prompt now forbids mentioning instructions); a few unsupported phrases ("unrecorded in reconciliation"). The checker passes them because they carry no new figures. This is what the memo adapter and test E5 are for. Checker steps now name their section.
- Pod `02h4ri0q6a44u1` deleted at 22:50 SGT after copying results. Runtime ≈ 3 h 05 min at $0.72/h. Balance $199.43 → $197.16 (−$2.27, against the ~$2 estimate). Spend now $0.01/h (volume).
- Demo: the five suggested prompts now replay these real event streams (`app/sample/agent/*.json`, run 3), labelled "Recorded run". Checked in a browser: step trail with times, five memo sections with clickable references, footer "3.9 s total · 4056 tokens in / 702 out · RTX PRO 4500 Blackwell · Recorded run". The recordings predate the three small fixes above, so they still show "$" in Cash flow.
