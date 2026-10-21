# LedgerLens — final report

Written from the measurements taken at each step and `DECISIONS.md` (every choice and why). Only measured numbers appear here; estimates say so.

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
  costs about half the 8B per page. On real phone photos of printed pages it drops to 67% (clean
  copies of the same pages: 98%) but still leads the 8B (41%); no model's page adds up from a photo.

## 2. What was built

| Part | What it does | Where |
|---|---|---|
| Serverless worker | Starts `vllm serve` inside the worker; tasks `generate`, `extract` (strict JSON schema), `memo` (streamed), `read`, `agent`, `health` | `worker/` |
| Endpoint (8B) | `szucrtrufff80a`, RTX 5090 / RTX PRO 4500 class, 0–2 workers, weights from Runpod's model cache | Runpod console + `runpodctl` |
| Endpoint (agent) | `cuvgn5z2av09my`: base 4B from model cache, reader LoRA from global volume | Runpod console + `runpodctl` |
| Synthetic data | 400 fictional businesses in SG/MY/ID, six statement layouts, two invoice layouts, phone-photo damage, planted red flags | `src/ledgerlens/datagen` |
| Training | Unsloth LoRA on Qwen3-VL-4B, then batch evaluation of all three models on one GPU Pod | `train/` |
| App | FastAPI + React: upload (also from a phone by QR), review table, analysis, memo, `/assistant` chat with live steps and cited cards, session Logs drawer, timings and cost | `app/` |

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
  Active worker only for the demo hour, set from the app's GPU switch: Assistant only or every
  endpoint (8B, fine-tuned 4B, Assistant, FLUX), with a shutoff timer that survives an app restart.
  An endpoint counts as ready only after a real `health` job answers, re-checked every 15 s.
  Measured 17 Oct: all four proven ready 343 s after arming from cold (the 8B last, 310 s engine
  load) and 14 s when FlashBoot resumed paused workers. After turning off, Runpod kept each worker
  running and billed for 2–5 min, so the app shows "stopping · still billed" until it ends.
- **GPU Pods for training and evaluation.** RTX PRO 4500 (32 GB, $0.72/h) in EU-RO-1 next to the
  volume. Pods are deleted after use, not stopped: a stopped Pod could not restart because its host
  had no free GPU.

### Credit assistant on Serverless

One endpoint, `cuvgn5z2av09my` (`ledgerlens-4b-base`), serves chat and page reading.
The base Qwen3-VL-4B routes requests with structured JSON, triages pages, calls read-only data
tools for at most 3 rounds, and writes five memo sections in parallel. The reader uses the
trained LoRA; Python computes the analysis and checks each section's figures, citations,
heading and length, allowing at most one rewrite.

| Run | Measured |
|---|---|
| Warm live memo, 15 Oct, RTX PRO 6000 Blackwell MIG 1g.24gb | 7.9 s total, 0.1 s queue; 4,186 tokens in / 760 out; $0.0036 (old one-shot memo: 50–106 s) |
| Warm largest-payer answer, same endpoint | 4.1 s; $0.0020 |
| Warm memo edit, same endpoint | 5.2 s |
| Read of 5 sample pages, same endpoint | 36.2 s |
| First request, cold start | 451.9 s queue for image pull, model load and compile |
| Endpoint recordings, same MIG slice | Memo 14.9 s; data questions 5.9 / 6.0 s; general answer 4.6 s; fixed replies 3.3 s |
| Warm Pod, 14 Oct, RTX PRO 4500 | Memo 3.9 s; data answers 1.8–2.0 s |

**E1: the reader LoRA matches the merged model.** On a Pod, row F1 (micro) was
0.9643 for LoRA vs 0.9647 for merged weights; header accuracy was 0.9872 for both,
with 0 invalid outputs and 187/200 outputs identical. LoRA was ~11% slower at concurrency 1
and ~8% at concurrency 8. This supports one base engine with adapters per role.

**The global volume removes the region pin.** A request on the EU-RO-1 network volume
waited 900 s without a GPU. Runpod's stock list showed no better-stocked region for our GPUs,
so another region's network volume would only move the pin. The reader adapter now lives on
`ledgerlens-global` (beta); workers started at once in US data centres on RTX PRO 6000
Blackwell MIG 1g.24gb. The merged fine-tuned endpoint `5078uudfkeaw2v` still uses EU-RO-1.
Global storage has no locking or atomic rename, so the compile cache stays on container disk;
each cold start recompiles (~2 min; 119.7 s measured on a Pod). This setup allows any data
centre; it does not enforce the lender's regional hosting requirement.

### Agent evaluations (E2–E6), live on the endpoint

Four test sets ran against the live endpoint (base 4B on an RTX PRO 6000 MIG 1g.24gb slice), then
again after each of two rounds of fixes. Same cases each run. A test switch (`stop_after`) stops the
worker after routing or page triage, so a router test costs about 2 s of GPU. Data and memo tests
use the true rows of held-out synthetic businesses, so they measure the assistant, not the reader.
Code: `train/agent_eval.py`.

| Test (cases) | Target | Run 1 | Run 2 | Run 3 |
|---|---|---|---|---|
| E2 router, intent right (180, en/id/ms/zh, 20 injections) | ≥ 95% | 91.1% | 91.7% | **97.8%** |
| E3 page triage: doc type / non-documents rejected / blurred flagged (156 pages) | ≥ 98% | 100% / 100% / 100% | not re-run | not re-run |
| E4 data questions, answer right (80) | ≥ 90% | 66.3% | 86.3% | **100%** |
| E4 questions the documents can't answer (15): no invented figure | all | 15/15 | 15/15 | 15/15 |
| E5 memos: all sections, valid citations (30) | all | 30/30, 30/30 | 30/30, 27/30 | 30/30, 30/30 |
| E5 memos with a flagged figure left after the one rewrite | 0 | 5/30 | 4/30 | 4/30 |
| E5 "make red flags shorter" followed (≤ 80% of the words) | all | 5/30 | 27/30 | 23/30 |
| E5 other four sections unchanged by the edit | all | 30/30 | 30/30 | 30/30 |

The memo then changed format (tables from code, commentary from the model), so E5 ran twice more
on the same 30 businesses:

| E5, report format | Run 4 (2bb1492) | Run 5 (0ca3cde) |
|---|---|---|
| All sections, valid citations | 30/30, 30/30 | 30/30, 30/30 |
| Memos with a flagged figure left | 6/30 | **0/30** |
| Sections rewritten after a check | 8% | 0% |
| "Shorter" edits followed / other sections unchanged | 24/30, 30/30 | 23/30, 30/30 |
| Median memo, concurrency 8 on 2 workers | 24.5 s | 21.3 s |

Run 4's extra flags were mostly correct amounts the writer had abbreviated ("IDR 455.5M"); the
checker now accepts an abbreviation only if a real figure rounds to it, and the writer is told to
write amounts in full. Run 5 has no abbreviated amounts, no fraud speculation and no stray Chinese
words in English memos (checked by script; the Chinese text present is payer names).

A real session on an unseen layout (11 pages: 9 statement pages, 2 invoices) then showed four faults
the tests had missed: the turn used only 8 pages; a misread page whose closing balance agreed with its
own rows hid a planted unreconciled page; "are you able to edit the memo?" rewrote two sections; and
"change the borrower's name" or "remove the January row" did nothing while the screen said "All figures
checked". Fixes: up to 100 read pages per turn; a cross-page check (a page must open at the previous
page's closing balance); a fixed answer for questions about what the assistant can do; a fixed
refusal for edits to names, figures or table rows (they come from the documents); and an "Edit not
applied" warning when an edited section comes back unchanged. The 4B router got the two new kinds of
message wrong too often, so small code rules back it up (the full list is in
[`CREDIT_ASSISTANT.md`](CREDIT_ASSISTANT.md#router-what-does-the-user-want)).

| E2 router (198 cases: the 180 above + 18 new) | Run 4 (39b5ccb) | Run 5 (429d9ea, rules) |
|---|---|---|
| Intent right, all cases | 91.4% | **97.5%** |
| "What can you do?" questions (10, en/id/ms/zh) | 3/10 | **10/10** |
| Edits to names, figures or rows refused (8) | 4/8 | **8/8** |
| Injections followed (both to a safe reply) | 2 | 1 |

E5 run 6 (39b5ccb): 30/30 all sections and citations, 2/30 memos with a flagged figure, "shorter"
edits 25/30, other sections unchanged 30/30, median memo 16.3 s. Replaying the same 11-page session
on the fixed worker: all 11 pages in the memo, the planted fault caught as a balance gap between p3
and p4, the right fixed answer and refusals. The reader errors stay (5 of 9 pages exact on this
layout); the cross-page check now exposes two of them as balance gaps.

**E6: does a memo edit do what the user asked?** A user's live test on 17 Oct found most edit
requests refused or returned unchanged, and E5 had only ever measured "make it shorter". E6 asks
13 kinds of edit on 8 held-out businesses (104 edits): drop the last sentence, drop the sentence
about a month, add a sentence, one paragraph instead of bullets, drop the second question, more
formal, expand, shorter, translate to Indonesian, remove a whole section, and three edits that must
still be refused (rename the borrower, remove a table row, change a total). Each edit starts from the
same memo and is checked by code (sentence and item counts, the month gone, words added, layout,
language words, other sections byte-identical).

| E6 (104 edits) | Before (6d2d6e4) | 7b2a98b | 0a31b64 | 8677ee8 | **24cd714** |
|---|---|---|---|---|---|
| Edits followed | 62.5% | 84.6% | 95.2% | 93.3% | **97.1%** |
| Prose edits wrongly refused | 8 | 0 | 0 | 0 | 0 |
| Must-refuse edits refused | 24/24 | 24/24 | 24/24 | 24/24 | 24/24 |
| Other sections unchanged | 90% | 100% | 100% | 100% | 100% |
| Edits applied exactly in code | — | — | 23 | 23 | 23 |
| Median turn | 6.9 s | 11.0 s | 11.3 s | 11.0 s | 10.2 s |

(7b2a98b is re-scored with the fixed scorer; the 0a31b64 → 8677ee8 dip moved between edit types and
is GPU run-to-run variation.) What changed: the document-fact rule and the router no longer refuse a
prose edit that only mentions a month or an entry; the writer gets an edit mode where the request
overrides length, format, tone and language but not the evidence rules or the section's content rules;
removing the Nth/last sentence, question or bullet, or the sentence about a month or name, is done
exactly in code; a whole commentary section can be removed and added back; an unchanged rewrite is
retried once; and two guards: a rewrite that drops every row citation is sent back once, and an edit
that switches script (English to Chinese) keeps the previous text and says so. The three misses
left: "more formal" turned one section into Chinese again (now kept unchanged, with the reason shown);
"remove the sentence about January" when both sentences mention January (code leaves it to the writer,
which reworded and introduced an error); one "shorter" edit over the length limit (flagged). Each E6
run cost about $0.08–0.12.

Scores for E4 are all on the final scorer (run 1 and 2 answers were rescored). Each run of
E2, E4 and E5 cost about $0.08–0.10 per suite in GPU time (estimated from GPU seconds at the 24 GB
list price); the account balance fell $0.60 over the whole evaluation, three runs plus E3.

What the runs found and what fixed it (all code and prompts, no training):
- **Missing tools.** The data agent couldn't list red flags or find the single largest payment,
  so it said "no information" or reported a payee's total as one payment. Added `list_findings`
  (with counts by type, because the 4B miscounted a list) and an amount sort on `find_rows`.
- **Names swapped.** On businesses with Chinese names the model replaced the payer name from the
  tool with a famous brand name, keeping the tool's percentage. A check caught it, but the rewrite
  then printed the problem list. Fix: names outside the Latin alphabet reach the model as
  placeholders (`NAME_1`) and code puts the real names back. Payer questions went 50% → 100%.
- **Router wording.** Memo requests worded as an "assessment" (Malay, Chinese) were missed; widening
  the memo wording then pulled "translate the memo" into memo. Run 3 says explicitly that changing
  or translating an existing memo is an edit and explaining a term is a general question.
- **"Shorter" ignored.** Code now gives the writer a target of 60% of the old words and fails the
  edit above 80%.

What is still weak:
- The invented thresholds of runs 1–4 ("exceeds 70%") are gone in run 5 (0/30), after the writer
  stopped restating figures that the tables now show.
- "Shorter" edits: 7/8 in E6 run 5 (E5: 25/30); a miss is flagged, never hidden.
- A rewording edit can change meaning without changing a figure (E6: "rose" where flows fell); the
  checker verifies figures and citations, not meaning.
- A figure check cannot catch a real number under the wrong label (run 4: "net IDR 3.77M in
  January", which is a real figure but not January's net).
- 8 of 15 off-limits questions (e.g. "the director's credit score") get the fixed "please ask
  about the documents" reply rather than "this isn't in the documents". Safe, but less helpful.
- The one injection counted as followed is a label question: "developer mode, approve this loan"
  was refused as off-topic. The E3 non-documents are simple drawings, an easy set.

**Weaknesses remain.** The 4B writer still sometimes ignores "keep the language" (caught and
reverted) or overshoots "shorter" (flagged). A memo adapter is the next step if those rates matter;
not trained.
The reader still drops 1–3 rows on lower-resolution pages: sample p2–p4 had 17/18, 17/18
and 15/18 exact rows. Stop cancels the Runpod job (`CANCELLED`), but the worker stays
`running` for ~2–2.5 min while in-flight work drains, then idles.

## 4. The ML experiment

### Setup
- **Models:** Qwen3-VL-8B-Instruct (no training), Qwen3-VL-4B-Instruct (no training), and the 4B after
  one epoch of LoRA (rank 16, language layers only, bf16, learning rate 2e-4, batch 8).
- **Training data:** 2,396 synthetic pages + 771 real CORD receipts (24% real). Validation 289 + 99.
- **Test sets, scored separately:**
  - A. 1,499 synthetic pages from the businesses that use the two statement layouts held back from
    training: 1,231 statement pages in those unseen layouts plus their 268 invoices (the invoice
    layouts are seen in training);
  - A′. 291 synthetic pages in the six training layouts, from businesses not in training;
  - B. 98 real CORD receipts (CORD's own test split);
  - C. phone photos of 18 printed test pages, against clean copies of the same 18 pages.
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

Where the differences come from (row F1 on A, by layout and by damage). These are row-level (micro)
scores from the per-row results kept on the training volume, not in the repo;
`eval/breakdown.json` has the same splits as per-page averages (macro), which run a little higher:

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
- **Phone photos (test set C):** 20 test pages were printed on paper and photographed on a phone.
  18 photos are usable: page 17 was not photographed and page 01 was shot off a laptop screen, so
  both are left out. The photos reached us through a chat app, which shrank them to 1280 px on the
  long side (about 1.1 MP, and the page fills only part of the frame); the clean pages are 2 MP. So
  this measures **camera plus chat-app compression**, not the camera alone. Each photo was matched to
  its page by the printed footer and checked by eye. All three endpoints ran live on Serverless,
  one page at a time, RTX 5090, $1.58/h; the clean column is the same 18 pages from the clean run.

  | Same 18 pages | 8B clean | 8B photo | 4B clean | 4B photo | 4B-ft clean | 4B-ft photo |
  |---|---:|---:|---:|---:|---:|---:|
  | row F1 | 0.671 | 0.411 | 0.723 | 0.258 | **0.984** | **0.672** |
  | row precision / recall | 0.68 / 0.66 | 0.42 / 0.41 | 0.73 / 0.72 | 0.28 / 0.24 | 1.00 / 0.97 | 0.71 / 0.64 |
  | header fields right | 0.938 | 0.802 | 0.932 | 0.698 | 0.975 | 0.833 |
  | page adds up | 0.00 | 0.00 | 0.00 | 0.00 | 0.79 | 0.00 |
  | valid JSON | 1.00 | 1.00 | 1.00 | 0.89 | 1.00 | 0.94 |
  | red flags found (recall) | 0.67 | 0.47 | 0.73 | 0.13 | 0.93 | 0.73 |
  | seconds per page (live, execution) | 12.0 | 11.5 | 6.9 | 8.6 | 5.6 | **5.4** |
  | $ per 1,000 pages (live) | $5.26 | $5.03 | $3.04 | $3.77 | $2.48 | **$2.37** |

  - **The fine-tune still wins on photos**, and by more: 0.67 against 0.41 for the 8B and 0.26 for
    the base 4B. The ranking and the cost result hold; the near-perfect accuracy does not.
  - **No model's page adds up from a photo.** One misread digit in a balance breaks the check, so
    every photographed page would go to an analyst. That is the honest state for real photos today.
  - **What went wrong for the fine-tune, page by page** (looked at, not inferred): the median page scores
    0.82 and 3 of 4 invoices are perfect, but three pages score zero:
    - p07: every month read as January instead of May (dd/mm dates), so every row misses even though
      most amounts are right. The same "Jan" confusion is its weak spot on heavy synthetic damage.
    - p12: a rupiah invoice's "8,383,500" read as "8383.500", so all amounts are 1,000× too small.
    - p16: the answer has an impossible date (30 Feb), so it is rejected as invalid.
    - Elsewhere: single digits misread (1,836,939 → 1,836,839), which also breaks reconciliation.
  - Seconds per page barely move for the fine-tune (5.6 → 5.4) and the 8B (12.0 → 11.5); the base 4B
    took longer on photos (6.9 → 8.6).

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
| Small sample scan read 0/18 rows exactly after app JPEG conversion | Live browser test, then the same 595×841 page: PNG 15/18, JPEG q90 0/18, upscaled PNG 13/18 | App keeps original bytes / PNG, JPEG only above the 9 MB request budget; removed the worker’s half-size JPEG decode below the pixel cap (released 15 Oct) |
| The GPU switch flashed "problem" every ~94 s while the 8B loaded | Watching the switch during a live arm-all test | A health job that waits in queue while the model loads is "starting"; only after 10 min is it a problem |
| The app said "GPU off" while Runpod still billed $5.13/h for ~5 min | Comparing `runpodctl user` spend with the UI after "Turn off now" | New "stopping · still billed" state: min 0, a running worker, no jobs |
| The header said "ready" for any running worker, the switch only after a health job | Reading the two status paths side by side | One state per endpoint, one shared Runpod reading for both |
| Memo edits refused or returned unchanged ("remove the sentence about March" refused; "remove the last sentence" ignored) | A live user test; then E6 (62.5% followed) | Narrowed refusal rule and router wording, edit mode for the writer, exact edits in code, retry, section removal; E6 97.1% |
| A correct paragraph rewrite shown as "Edit not applied" | Browser check of the edit flow | The unchanged-text check also compares the line layout |
| A correct Indonesian translation flagged on every amount | Reading E6 misses | The checker reads "171.558.216" and "73,16%" both ways |
| "More formal" turned an English section into Chinese | Reading E6 misses | A script switch keeps the previous text and says why |
| An evaluation ran on the old image although the template showed the new tag | `applied_by_code` was 0 and problem counts matched the last run exactly | Recycle until no worker is left, then prove the new behaviour before evaluating |
| Chat crashed on a checker note | Live memo left a `{kind, detail}` object; the page tried to render it as text | Render checker objects as text; covered by a test |

Lesson: test the image path through the app; stored-page tests missed the damage before the model.

## 6. Cost

| Item | Measured |
|---|---|
| Serverless, 8B, warm, one page at a time | 13.7–15.1 s per page ≈ $6.0–6.6 per 1,000 pages |
| Serverless, fine-tuned 4B, warm, one page at a time | 6.9 s on an RTX 5090, 10.3–11.4 s on an RTX 4090 (cheaper tier) ≈ $3 per 1,000 pages either way |
| Pod, batched (RTX PRO 4500, $0.72/h) | 8B $0.52, 4B $0.34, fine-tuned 4B **$0.26** per 1,000 pages |
| Agent, warm live memo / largest-payer answer, 15 Oct | $0.0036 / $0.0020 |
| Global volume | $0.09/GB-month + requests; adapter copy Pod ≈ $0.02 |
| Agent serving checks on the Pod, 14 Oct | $2.27 |
| Training (one epoch, 86 min on the same Pod) | ≈ $1.0 |
| Total spent, whole project (to 11 Oct 00:41) | ≈ **$11.2** (balance $214.64 → $203.46) |
| Total spent, whole project (to 15 Oct 08:00) | ≈ **$18.2** (balance $214.64 → $196.43) |
| Agent evaluations E2–E5, three runs, 15 Oct | ≈ $0.6 (balance $196.38 → $195.78, incl. cold starts) |
| App sessions, E2/E5 reruns and live replays, 15 Oct afternoon | ≈ $2.0 (balance $195.78 → $193.78, incl. cold starts and warm GPU time) |
| Total spent, whole project (to 15 Oct 18:20) | ≈ **$20.9** (balance $214.64 → $193.78) |
| FLUX cover image, warm A40 / cold RTX PRO 6000 MIG 2g.48gb, 17 Oct | ≈ $0.006 per image (17 s) / 137 s end to end; FLUX work and live app test ≈ $0.90 |
| E6 edit evaluation, 6 runs incl. one void, 17 Oct | ≈ $0.08–0.12 per run; whole memo-edit fix ≈ $1.0 (balance $190.49 → $189.45) |
| All four endpoints armed (one worker each), 17 Oct | $3.70–6.49/h list price; $5.13/h seen in `runpodctl user`; two live tests ≈ $0.64 |
| Total spent, whole project (to 17 Oct 09:40) | ≈ **$23.3** (balance $214.64 → $191.36) |
| Total spent, whole project (to 17 Oct 11:13) | ≈ **$25.2** (balance $214.64 → $189.45) |

Batching is the biggest lever: the same 8B costs ~$6 per 1,000 pages one page at a time on
Serverless and ~$0.52 batched on a Pod. A lender processing month-end files in bulk should batch.

## 7. Limits and the synthetic-to-real gap

- Statements and invoices are synthetic. Real-document evidence is CORD (receipts, a different
  document type) and the phone photos (real camera and paper, synthetic content).
- The phone photos are 18 pages from one phone, compressed by a chat app to about half the pixels of
  the clean pages; camera and compression are measured together. Small set: treat differences of a few points as noise.
- The fine-tune saw real CORD training receipts; the base models saw none.
- A real deployment would start with about 50 labelled real statements from the lender, then run
  next to analysts (shadow mode) before anyone relies on it.

## 8. What's next

1. **Memo adapter, only if needed:** the remaining writer faults are "keep the language" and
   "shorter" (E6: 3 of 104 edits missed, all flagged or reverted); memo figures are clean in run 5. Data from the synthetic businesses, answers
   from an open-weight teacher, kept only when they pass the checker; gate: E5 must beat run 5.
2. **Off-limits questions:** answer "this isn't in the documents" instead of the generic reply.
3. **Harder E3 set:** real photos of non-financial paper (menus, letters, ID-like cards).
4. **Phone photos:** re-shoot at full camera resolution (no chat app) to separate the camera from the
   compression; train with real-photo-like damage (lower resolution, perspective); add a month check
   against the statement period and reject 1,000× amount slips against the printed total.
5. **Heavy damage:** train with more heavily damaged pages, and accept a page with one impossible
   date instead of rejecting it (flag that row for the analyst).
6. **Rupiah amounts for the base 8B:** one prompt line on thousands grouping, then re-score CORD.
7. **Real statements:** about 50 labelled real statements from the lender to measure the real gap,
   then shadow mode next to analysts.
8. **Cost:** batch pages per request on Serverless; try FP8 weights.
