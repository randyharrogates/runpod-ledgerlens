# LedgerLens interactive demo

From the repository root:

```sh
uv sync --extra app
uv run python app/make_sample.py
cd app/frontend
npm install && npm run build
cd ../..
DEMO_MODE=1 uv run uvicorn app.backend.main:app --host 0.0.0.0 --port 8090
```

Open http://localhost:8090. For live extraction and chat, unset DEMO_MODE and configure the endpoint IDs below. The server
reads the key from runpodctl config unless `RUNPOD_API_KEY` is set; the browser never receives it. Vite development mode (`npm run dev`) proxies API calls to port 8090. Regenerate the sample before presenting it. Generation uses ledgerlens.datagen with its headless Chromium renderer and requires a working local Chromium installation. Seed 4 selects an SGD business in the non-held-out stmt_b layout: four consecutive statements (clean, light, medium, heavy) and one invoice, with ground truth and rendering metadata. It prints the seed and selected anomalies. Rendering failures exit clearly and preserve existing samples; there is no text-dump fallback.

Environment variables:

| Variable | Purpose |
| --- | --- |
| RUNPOD_ENDPOINT_8B | Base model endpoint ID; optional |
| RUNPOD_ENDPOINT_AGENT | Credit assistant endpoint ID: `cuvgn5z2av09my` |
| RUNPOD_WORKER_LOGS | Runpod worker-log polling is on by default; set to 0 to turn it off |
| RUNPOD_ENDPOINT_4B | Base small-model endpoint ID; optional |
| TRAINING_ENABLED | Set to 1 to permit real GPU rentals; unset uses simulation |
| LEDGERLENS_DATA | Directory of rendered dataset pages, answer keys, metadata and splits; unset uses bundled samples and recorded summaries |
| RUNPOD_ENDPOINT_FT | Fine-tuned model endpoint ID; optional |
| RUNPOD_API_KEY | Server-only credential; falls back to worker.client.runpodctl_key() |
| GPU_PRICE_PER_SEC_8B / GPU_PRICE_PER_SEC_FT | Optional override, dollars per second. By default each request is priced by the GPU the worker reports: RTX 5090 or PRO 4500 $1.58/h, RTX 4090 $1.10/h, RTX 3090 $0.69/h (Serverless flex, Oct 2026) |
| DEMO_MODE | Set to 1 to force offline behavior |
| PUBLIC_BASE_URL | Phone-facing origin, e.g. http://192.168.1.20:8090; otherwise LAN IP detected via UDP |

Phone and laptop need the same Wi-Fi and port 8090 reachable. iOS Safari converts camera photos to JPEG for file inputs. The server rejects HEIC files that arrive without conversion. Sessions are bearer capabilities in URLs, held only in memory; restarting clears them. This is a demo with no persistent storage or login. Do not use real confidential documents on an unprotected public connection.

Extraction failures fall back only for bundled sample pages. Cached ground truth is explicitly marked and carries no fabricated GPU cost. Uploaded pages remain retryable. Memo streaming polls Runpod /run and /stream incrementally, forwards worker deltas, and closes with usage/timing. The browser retains the session across refreshes, polls for phone uploads, and allows retry after connection failures. Memo retries start a fresh memo.

## Workspace pages and exports

Assistant (`/assistant`): chat with live steps, attached pages and Stop. A memo arrives as a
report: header (borrower, bank, period, evidence), key figures, and five numbered sections whose
tables (monthly cash flow, red flags, reconciliation) come from code, with the model's commentary
beside them; "Open full report" shows it full width and "Copy memo" copies it as Markdown.
Demo mode replays recorded agent turns, labelled "Recorded run". Stop cancels the Runpod job;
in-flight worker work can take ~2–2.5 min to drain before the worker idles.

The live Logs drawer combines app, browser, worker and vLLM logs for the current session, with
filters and a "Logs for this turn" link. Each turn's **Trace** tab draws the path the run
took (flow graph) and a timeline of every step; clicking a step lists the log lines from that
moment. Recorded turns are drawn with their recorded times. Runpod worker logs are polled by default;
`RUNPOD_WORKER_LOGS=0` disables that feed.

Read statements: add pages, check editable rows, review cash flow and cited flags, and write a memo. Download the current page or all checked pages as CSV. Save as PDF opens the browser print dialog and prints only the memo with plain-text citations.

Compare models: run a page on three models explicitly, or inspect recorded test outputs offline. Explore data: browse group, layout, damage and planted flags; preview sandboxed HTML or PNG beside its answer key; upload and validate a dataset ZIP. Results: held-out scores, breakdowns and hardest pages, with a scores CSV export. Cost calculator: batch estimates and measured live costs, with Copy this estimate. Training: reported history, learning curves and a guarded new-run flow.

## Guarded training

Choose a validated dataset, quick check or full run, and a spending limit (at most $25). Review checks supported GPU stock in Romania, price and shared-volume availability. The confirmation token expires after ten minutes. Only the explicit Start action can launch a run; Presenter mode never launches training or calls a model. Real training requires TRAINING_ENABLED=1 and server credentials. Cancellation and spending limits trigger Pod deletion; if deletion cannot be verified, the app shows a persistent Pod warning with instructions to delete it in the Runpod console.

“Simulated” means no GPU is rented and no new training or measurements take place. Stages, logs and spending progress are simulated; full-run scores replay the recorded evaluation and are labeled accordingly. Quick checks prove the flow and produce no scores. The reported run is historical evidence, so it has no live progress, log or deletion status.

## Three-minute demo script

Prepare before starting: load the sample business, select the trained small model, explicitly read every page, and explicitly generate the memo. Presenter starts on Read step 2; Back and Next navigate the seven steps without making model calls. Arrow keys work with focus inside the panel. Step 3 opens cash flow, with an Open credit memo button for Read step 4. Collapse keeps the countdown visible; Exit ends the tour. The countdown starts at 3:00 and turns orange after the current step’s time window.


1. Read statements · 0:00–0:40 · route / · step 2
"This is a small business's bank statement, photographed on a phone. I load the sample business, pick our trained small model and read every page. Each line lands in the table on the right, and the app re-adds every running balance: a tick means the printed balance matches. The orange line is a returned cheque, flagged on its own."

2. Read statements · 0:40–1:00 · route / · step 2 (point at an editable cell)
"If a line is wrong, the analyst fixes the cell and everything after it recomputes. Nothing the model reads is final until a person has had the chance to check it."

3. Read statements · 1:00–1:30 · route / · steps 3 then 4
"From those lines, plain code, not the model, works out cash flow, the biggest customers and the red flags, each linked to its line. The model only writes the memo's wording, and every claim carries a reference I can click to jump back to the evidence."

4. Compare models · 1:30–2:00 · route /compare
"Same page, three models at once. On a layout none of them had seen, both untrained models read incoming payments as money going out on 7 of the 18 lines. Our trained small model got all 18 right."

5. Results · 2:00–2:25 · route /results
"Across 1,499 pages in held-back layouts, the trained small model reads 96.5% of lines right, meaning the date and the amount in or out are exactly correct, against 57.8% for a model twice its size. It is weakest on heavily damaged photos, at 86.8%, and on real shop receipts, at 76.5%. These are the pages it got most wrong."

6. Cost calculator · 2:25–2:45 · route /cost
"Reading in batches costs 26 cents per 1,000 pages on the trained small model, half the big model's 52. The serverless GPUs sleep at no cost when nobody is using them, and we pay per second of reading."

7. Training · 2:45–3:00 · route /training
"And the whole pipeline can be rerun from here. The recorded run trained for 86 minutes for about a dollar, and a new run shows its price and a spending limit before any GPU is rented."
