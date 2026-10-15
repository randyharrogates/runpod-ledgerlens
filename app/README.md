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

Choose a validated dataset, quick check or full run, and a spending limit (at most $25). Review checks supported GPU stock in Romania, price and shared-volume availability. The confirmation token expires after ten minutes. Only the explicit Start action can launch a run. Real training requires TRAINING_ENABLED=1 and server credentials. Cancellation and spending limits trigger Pod deletion; if deletion cannot be verified, the app shows a persistent Pod warning with instructions to delete it in the Runpod console.

“Simulated” means no GPU is rented and no new training or measurements take place. Stages, logs and spending progress are simulated; full-run scores replay the recorded evaluation and are labeled accordingly. Quick checks prove the flow and produce no scores. The reported run is historical evidence, so it has no live progress, log or deletion status.

