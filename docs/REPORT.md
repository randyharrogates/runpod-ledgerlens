# LedgerLens — final report

Written from `BUILD_LOG.md` (every step, with times and costs) and `DECISIONS.md` (every choice and
why). Only measured numbers appear here; estimates say so.

## 1. Summary

- **What:** a Southeast Asian lender's analyst uploads phone photos of a small business's bank
  statements and invoices. LedgerLens returns the rows, checked against the printed balance, a
  cash-flow analysis, red flags that cite their row, and a draft credit memo.
- **Where it runs:** open Qwen3-VL models on Runpod Serverless (vLLM inside the worker), so the
  documents never go to a US-hosted API and each page has a known price.
- **The ML question:** can a 4B model, LoRA fine-tuned on our own synthetic documents, match an 8B
  model with no training, so the lender can run a smaller, cheaper GPU?
- **Answer: yes, and by a wide margin on our documents.** The fine-tuned 4B scores a row F1 of 0.96
  (date, debit and credit of each row exactly right) on statement layouts it never saw in training (8B: 58%), 77% on real receipts (8B: 40%), and is
  2× faster per page on the same GPU. Its weak spot is heavy photo damage (87%). Served live, it
  costs about half the 8B per page.

## 2. What was built

| Part | What it does | Where |
|---|---|---|
| Serverless worker | Starts `vllm serve` inside the worker; tasks `extract` (strict JSON schema), `memo` (streamed), `health` | `worker/` |
| Endpoint (8B) | `szucrtrufff80a`, RTX 5090 / RTX PRO 4500 class, 0–2 workers, weights from Runpod's model cache | Runpod console + `runpodctl` |
| Synthetic data | 400 fictional businesses in SG/MY/ID, six statement layouts, two invoice layouts, phone-photo damage, planted red flags | `src/ledgerlens/datagen` |
| Training | Unsloth LoRA on Qwen3-VL-4B, then batch evaluation of all three models on one GPU Pod | `train/` |
| App | FastAPI + React: upload (also from a phone by QR), review table, analysis, memo, "under the hood" timings and cost | `app/` |

## 3. How Runpod is used

- **Queue endpoint (`/run` then `/status`).** A warm page takes ~14–15 s and a cold start ~3.5 min.
  `/runsync` holds one connection for the whole job, and Runpod dropped it during a cold start
  while the job still completed; polling `/status` is robust and also returns queue and execution
  time.
- **`/stream` for the memo.** The handler is a generator, so memo text appears as it is written.
- **Model cache for the 8B.** 16.3 GiB loaded in 5.7 s; no download at cold start.
- **Network volume for the fine-tuned 4B.** The training Pod writes the merged model to the volume
  and the same image serves it via `MODEL_PATH`. Trade-off: a volume pins the endpoint to one data
  centre (EU-RO-1). We saw the cost of that live: one request waited 13.5 min because EU-RO-1 had no
  free RTX 5090/4090 for Serverless. Two fixes: list more GPU types (we added the 4090 as a
  fallback), and keep one Active worker when someone is waiting — or bake the weights into the image
  so any data centre can serve it.
- **GitHub build from a `deploy` branch.** Runpod rebuilds on every push to the tracked branch, so
  ordinary commits go to `main` and `scripts/release-worker.sh` ships to `deploy` after the tests.
- **Scaling for cost.** 0 Active workers when idle, max 2, idle timeout 60 s, FlashBoot on. One
  Active worker only for the demo hour.
- **GPU Pods for training and evaluation.** RTX PRO 4500 (32 GB, $0.72/h) in EU-RO-1 next to the
  volume. Pods are deleted after use, not stopped: a stopped Pod could not restart because its host
  had no free GPU.

## 4. The ML experiment

### Setup
- **Models:** Qwen3-VL-8B-Instruct (no training), Qwen3-VL-4B-Instruct (no training), and the 4B after
  one epoch of LoRA (rank 16, language layers only, bf16, learning rate 2e-4, batch 8).
- **Training data:** 2,396 synthetic pages + 771 real CORD receipts (24% real). Validation 289 + 99.
- **Test sets, scored separately:**
  - A. 1,499 synthetic pages in two statement layouts the model never saw in training;
  - A′. 291 synthetic pages in the six training layouts, from businesses not in training;
  - B. 98 real CORD receipts (CORD's own test split);
  - C. phone photos of 20 printed test pages (_pending: needs the photos_).
- **Same conditions for all three:** same Pod GPU, same prompt, same pixel cap (2 MP), same
  structured-output schema, greedy decoding, pages batched by vLLM.
- **Headline = the model's own answer.** A second column adds the code's balance check.

### Training
396 steps in 86 min. Validation loss every 50 steps: 0.0199 → 0.0154 → 0.0113 → 0.0103 → 0.0098 →
0.0092 → 0.0085 → 0.0084, still falling at the end: no sign of overtraining. Peak GPU memory
13.1 GB of 32.

### Results (final run, the schema that is served)
Row F1 is the share of rows read exactly right, from the model's own answer. A statement row counts
when its date, debit and credit all match exactly; the description (free text) and the running balance
are not compared row by row (the balance is tested by "page adds up"). An invoice or receipt item
counts when its description, quantity, unit price and amount all match. Precision = of the rows the model
wrote, how many were right; recall = of the real rows, how many it got. F1 combines the two.

| | 8B, no training | 4B, no training | **4B fine-tuned** |
|---|---:|---:|---:|
| **A. Unseen layouts** (1,499 pages): row F1 | 0.578 | 0.614 | **0.965** |
| row precision / recall | 0.578 / 0.577 | 0.617 / 0.612 | 0.966 / 0.963 |
| after the balance check | 0.621 | 0.647 | 0.966 |
| header fields right | 0.930 | 0.918 | 0.984 |
| page adds up (reconciliation) | 0.30 | 0.20 | 0.75 |
| red flags found (recall / precision) | 0.66 / 0.17 | 0.63 / 0.15 | 0.96 / 0.51 |
| valid JSON | 0.998 | 0.991 | 0.997 |
| **A′. Seen layouts** (291 pages): row F1 | 0.823 | 0.745 | **0.960** |
| row precision / recall | 0.826 / 0.821 | 0.751 / 0.739 | 0.966 / 0.953 |
| **B. Real CORD receipts** (98): row F1 | 0.396 | 0.259 | **0.765** |
| row precision / recall | 0.403 / 0.389 | 0.262 / 0.255 | 0.768 / 0.763 |
| header fields right | 0.689 | 0.635 | 0.959 |
| **Speed, batched on one RTX PRO 4500** (A): seconds per page | 2.58 | 1.72 | **1.28** |
| cost per 1,000 pages at $0.72/h | $0.52 | $0.34 | **$0.26** |

Where the differences come from (row F1 on A, by layout and by damage; script over `results.json`):

| | 8B | 4B | 4B fine-tuned |
|---|---:|---:|---:|
| `stmt_e` (676 pages) | 0.857 | 0.545 | 0.960 |
| `stmt_f` (555 pages) | **0.198** | 0.676 | 0.969 |
| invoices (268 pages) | 0.936 | 0.837 | 0.976 |
| clean / light / medium photos | 0.61 / 0.63 / 0.58 | 0.68 / 0.75 / 0.70 | 1.00 / 1.00 / 1.00 |
| heavy photos | 0.51 | 0.36 | **0.87** |

- Precision and recall are within about 1.5 points of each other everywhere: the models rarely
  invent or drop rows. Their mistakes are misread fields inside real rows (debit vs credit, a digit).
- Red flags are the opposite: the fine-tuned 4B finds 96% of planted anomalies but only about half
  of its flags are real (precision 0.51). An analyst reviews every flag; the model decides nothing.
- The 8B's low score on A is mostly one layout: on `stmt_f` it leaves debit and credit empty.
- The fine-tune is near perfect on clean to medium photos. **Heavy damage is its weak spot.** Looking
  at its worst pages: it reads "Jun" as "Jan" on blurred pages (the header prints "1 Jan 2026"), and
  all 7 of its invalid answers are an impossible date (31 Jun, 29 Feb 2026). Rows are matched by date
  and amount, so one wrong month zeroes a page even when every amount is right.
- Receipts are real photos of a different document type, so they show the synthetic-to-real gap
  most clearly: 0.765 for the fine-tune (which also trained on CORD's own training receipts) vs
  0.96+ on synthetic pages.
- **Phone photos (test set C):** pending — needs the printed pages photographed.

### What the schema fix changed
The first full run allowed an amount to be a JSON number of any length; base models sometimes wrote
"17500.0000…" until they ran out of tokens. We bounded amounts to short decimal strings and re-ran
all three models.

| Row F1 (A / A′ / B) | First run (any number) | Final run (bounded string) |
|---|---|---|
| 8B | 0.592 / 0.842 / 0.609 | 0.578 / 0.823 / 0.396 |
| 4B | 0.626 / 0.773 / 0.333 | 0.614 / 0.745 / 0.259 |
| 4B fine-tuned | 0.965 / 0.962 / 0.784 | 0.965 / 0.960 / 0.765 |

The runaway numbers are gone (8B valid JSON on A: 0.981 → 0.998), but **the base models got worse
on receipts**. Counting their answers: as a string, the 8B copies rupiah as printed — "17.500",
which means 17.5 — 50 times (first run: once), and leaves the amount empty 53 times (first run: 12)
when the printed form doesn't fit. As a number it had normalised them. The lesson: the shape of the
output schema steers an untrained model's answers, not just their validity. The fine-tuned model
writes plain digits, as trained, and barely moved. Next step: tell the model in the prompt that in
rupiah the dot groups thousands, and re-score the 8B.

## 5. Problems found and how

Most serious bugs passed every test and were caught only by looking at the output.

| Problem | How it was found | Fix |
|---|---|---|
| The generator was almost a fixed template (same amounts and dates everywhere, no clean businesses) | Reading rendered pages | Rewritten with seeded variety and ~27% clean businesses |
| The worker crash-looped on start, billing while the job waited | `serverless logs --follow` showed `torchcodec` failing to load a CUDA 13 library in a CUDA 12.9 image | Removed `torchcodec`; the build now imports vLLM's media module, so this breaks the build instead |
| `/runsync` dropped during a cold start | Live app test | Always `/run` + `/status` |
| Training pages silently shrunk to 512 px | Rehearsal on a Pod | Collator set to keep our 2 MP cap: train and serve see the same pictures |
| Base models scored 0% | Rehearsal report looked wrong | They omitted `doc_type` (a schema default); filled in as the worker does |
| Every invoice was the same one | Screenshot of the app | Invoice generator with varied items, prices, dates, tax |
| Runaway zeros ("17500.0000…") until the token limit | Reading invalid answers during the run | Amounts are bounded decimal strings in the schema |
| The memo's citations pointed one row early | Clicking a memo citation in the live dry run | The model is given 1-based `[p1:r4]` references to copy |
| The memo model did its own arithmetic and got it wrong (total outflow 54,987.85; true 55,041.59), and invented a date for a cash deposit | Checking the live memo's numbers and dates against the data | Period totals and finding dates are computed in code and given to the model; every number in a live memo is checked against the analysis and any mismatch is highlighted |

## 6. Cost

| Item | Measured |
|---|---|
| Serverless, 8B, warm, one page at a time | 13.7–15.1 s per page ≈ $6.0–6.6 per 1,000 pages |
| Serverless, fine-tuned 4B, warm, one page at a time | 6.9 s on an RTX 5090, 10.3–11.4 s on an RTX 4090 (cheaper tier) ≈ $3 per 1,000 pages either way |
| Pod, batched (RTX PRO 4500, $0.72/h) | 8B $0.52, 4B $0.34, fine-tuned 4B **$0.26** per 1,000 pages |
| Training (one epoch, 86 min on the same Pod) | ≈ $1.0 |
| Total spent, whole project (to 11 Oct 00:41) | ≈ **$11.2** (balance $214.64 → $203.46) |

Batching is the biggest lever: the same 8B costs ~$6 per 1,000 pages one page at a time on
Serverless and ~$0.52 batched on a Pod. A lender processing month-end files in bulk should batch.

## 7. Limits and the synthetic-to-real gap

- Statements and invoices are synthetic. Real-document evidence is CORD (receipts, a different
  document type) and the phone photos (real camera and paper, synthetic content).
- The fine-tune saw real CORD training receipts; the base models saw none.
- A real deployment would start with about 50 labelled real statements from the lender, then run
  next to analysts (shadow mode) before anyone relies on it.

## 8. What's next

1. **Phone photos (test set C):** score both endpoints on 20 printed, photographed test pages.
2. **Heavy damage:** train with more heavily damaged pages, and accept a page with one impossible
   date instead of rejecting it (flag that row for the analyst).
3. **Rupiah amounts for the base 8B:** one prompt line on thousands grouping, then re-score CORD.
4. **Real statements:** about 50 labelled real statements from the lender to measure the real gap,
   then shadow mode next to analysts.
5. **Cost:** batch pages per request on Serverless; try FP8 weights.
