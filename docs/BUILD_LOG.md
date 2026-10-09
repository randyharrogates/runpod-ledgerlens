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
- **16:20 — Clicked through every screen in a headless browser** (Playwright script, demo mode,
  no console errors). Found: the bundled sample "statements" were plain-text dumps drawn with Pillow,
  not our rendered layouts; the review table cut off the balance column and showed unformatted
  amounts; red flags named the type but did not explain it; a raw JSON debug line under the memo;
  the "under the hood" drawer was one long key/value table per request; the frontend was one
  267-line file with minified CSS (not walkthrough-friendly); uploads needed the same
  decompression-bomb cap as the worker. All sent back to Codex (16:25).

### Endpoint
- **16:15 — Workers decided:** Active (min) **0** so nothing bills while idle (cold starts are shown,
  not hidden); Max **2**. One Active worker only for the demo hour.
- **16:15 — Asian data centre question.** Runpod's only Asian capacity is AP-JP-1 (Japan) and India,
  H100/H200 only (~$0.00155/s ≈ $5.60/h running vs ~$1.10/h for a 4090 in EU-RO-1). Latency is not
  the issue (≈0.2 s extra per request against seconds of inference). Plan: build and test in
  EU-RO-1; before the demo add a second endpoint in AP-JP-1 from the same repo so the cost panel shows
  the real in-region vs out-of-region trade-off — a data-residency answer backed by measured numbers.
- **16:35 — Console settings reviewed before deploy.** Allowed CUDA restricted to 12.9+ (the image is
  CUDA 12.9; older host drivers would fail to start it). GPU types: kept RTX 5090 (32 GB) and RTX 3090
  (24 GB); removed L4 (≈⅓ of a 3090's memory bandwidth, slow for decoding), PRO 6000 MIG 24GB (a
  compute slice) and RTX A5000 (slower). All data centres left allowed: with no volume attached
  nothing pins the endpoint, and wider choice finds GPUs faster. No exposed ports (queue endpoint).
- **16:40 — Runpod MCP installer declined.** `npx @runpod/mcp-server@latest add` is an interactive
  installer that rewrites every detected AI client's config and, in local mode, stores the API key in
  plain text; it would also need the local agent restarted. `runpodctl` already covers the needed commands
  (including `serverless logs`).
- **16:45 — Endpoint `szucrtrufff80a` created** (console, GitHub build of `main`, Dockerfile
  `worker/Dockerfile`, template `ej6398fg30`). Checked with `runpodctl serverless get` /
  `template get`: workers 0/2, idle 60 s, FlashBoot on, queue-delay scaling 4 s, execution timeout
  600 s, env `MODEL_NAME`, `MAX_MODEL_LEN=8192`, `GPU_MEM_UTIL=0.92` correct. Fixed from the CLI:
  container disk 5 → **40 GB** (`runpodctl template update ej6398fg30 --container-disk-in-gb 40`),
  and the 8B model attached to Runpod's model cache (`runpodctl serverless update szucrtrufff80a
  --model-reference https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct:main`, pinned to commit
  `0c351dd`) so workers don't download 17 GB at start-up.

### Spend so far
| Item | Cost |
|---|---|
| Network volume (100 GB, prorated) | ~$0.25/day |
| GPU time | $0 |
