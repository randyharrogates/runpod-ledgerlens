# FLUX image worker

Separate Runpod Serverless endpoint: a text prompt in, a base64 image out.
Build branch `deploy-image` with Runpod's GitHub integration, Dockerfile
`image_worker/Dockerfile`, and the repository root as build context.

48 GB GPUs run fully on GPU. 32 GB GPUs use CPU offload, which is slower.
Weights come from Runpod's model cache at
`/runpod-volume/huggingface-cache/hub/models--black-forest-labs--FLUX.1-dev/snapshots/<hash>/`.
FLUX.1-dev is gated and has a non-commercial licence: accept its terms and set
HF_TOKEN on the endpoint. Missing cache weights download through diffusers.
MODEL_PATH overrides the cache; MODEL_NAME overrides the download/cache model.

Input (optional fields shown with defaults; seed is random when omitted):
```json
{"input":{"prompt":"A paper lantern above a quiet lake", "width":1024,"height":576,"steps":28,"guidance":3.5,"format":"png"}}
```
Use `format: "jpeg"` with optional `quality` (50–95, default 90).
Output includes `image_base64`, `mime`, `seed`, `width`, `height`, `steps`,
`guidance`, `prompt`, `model`, `gpu`, and
`timing: {"generate_s": ..., "load_s": ..., "cold_start": ...}`.
Errors return `{"error":"..."}`. Send `{"input":{"task":"health"}}` for engine state.

Submit, then poll using the returned job ID until status is COMPLETED:
```sh
curl -sS -X POST "https://api.runpod.ai/v2/$ENDPOINT_ID/run" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"input":{"prompt":"A paper lantern above a quiet lake"}}'

curl -sS "https://api.runpod.ai/v2/$ENDPOINT_ID/status/$JOB_ID" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" > result.json
cat result.json | python3 -c "import json,sys,base64;o=json.load(sys.stdin)['output'];open('cover.png','wb').write(base64.b64decode(o['image_base64']))"
```

Endpoint: `qj707h1pdzzpez`, container disk 30 GB, max 1 worker. Runpod builds the image from the
`deploy-image` branch. If that builder is down, `.github/workflows/image-worker.yml` builds the same
Dockerfile as `ghcr.io/randyharrogates/ledgerlens-flux:<commit>`; point the template at that image.

Measured on 17 Oct 2026 (1024×576, 28 steps, guidance 3.5, no CPU offload):

| | NVIDIA A40 48 GB ($1.22/h) | RTX PRO 6000 Blackwell MIG 2g.48gb ($1.75/h) |
|---|---|---|
| Cold start, fresh worker (queue time) | | 123 s, model load 11.7 s of it |
| Model load from Runpod's model cache | 14.4 s | 11.7 s |
| One image | 16.9–18.7 s | 10.4 s |
| Paused worker resumed by FlashBoot | 0.1–1.1 s queue | |
| Cost per image, warm | ≈ $0.006 | ≈ $0.005 |

Details: `docs/REPORT.md`.
