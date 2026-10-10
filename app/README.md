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

Open http://localhost:8090. For live extraction, unset DEMO_MODE and configure the endpoints. Vite development mode (`npm run dev`) proxies API calls to port 8090. Regenerate the sample before presenting it. Generation uses ledgerlens.datagen with its headless Chromium renderer and requires a working local Chromium installation. Seed 4 selects an SGD business in the non-held-out stmt_b layout: four consecutive statements (clean, light, medium, heavy) and one invoice, with ground truth and rendering metadata. It prints the seed and selected anomalies. Rendering failures exit clearly and preserve existing samples; there is no text-dump fallback. The legacy bundled images have not been replaced in this sandbox because Chromium is blocked here.

Environment variables:

| Variable | Purpose |
| --- | --- |
| RUNPOD_ENDPOINT_8B | Base model endpoint ID; optional |
| RUNPOD_ENDPOINT_FT | Fine-tuned model endpoint ID; optional |
| RUNPOD_API_KEY | Server-only credential; falls back to worker.client.runpodctl_key() |
| GPU_PRICE_PER_SEC_8B / GPU_PRICE_PER_SEC_FT | Optional override, dollars per second. By default each request is priced by the GPU the worker reports: RTX 5090 or PRO 4500 $1.58/h, RTX 4090 $1.10/h, RTX 3090 $0.69/h (Serverless flex, Oct 2026) |
| DEMO_MODE | Set to 1 to force offline behavior |
| PUBLIC_BASE_URL | Phone-facing origin, e.g. http://192.168.1.20:8090; otherwise LAN IP detected via UDP |

Phone and laptop need the same Wi-Fi and port 8090 reachable. iOS Safari converts camera photos to JPEG for file inputs. The server rejects HEIC files that arrive without conversion. Sessions are bearer capabilities in URLs, held only in memory; restarting clears them. This is a demo with no persistent storage or login. Do not use real confidential documents on an unprotected public connection.

Extraction failures fall back only for bundled sample pages. Cached ground truth is explicitly marked and carries no fabricated GPU cost. Uploaded pages remain retryable. Memo streaming polls Runpod /run and /stream incrementally, forwards worker deltas, and closes with usage/timing. The browser retains the session across refreshes, polls for phone uploads, and allows retry after connection failures. Memo retries start a fresh memo.

