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
