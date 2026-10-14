#!/usr/bin/env bash
# Live chat-agent check on a Pod: base 4B with the reader as a LoRA adapter and tool calling on,
# the same engine shape as the planned agent endpoint. Run from /workspace/runpod-ledgerlens.
set -euo pipefail
cd /workspace/runpod-ledgerlens
export HF_HOME=/workspace/hf-cache
RUN=/workspace/ledgerlens
OUT=$RUN/p0/agent
mkdir -p "$OUT"
.venv-infer/bin/vllm serve Qwen/Qwen3-VL-4B-Instruct --served-model-name ledgerlens \
    --host 127.0.0.1 --port 8000 --max-model-len 8192 --gpu-memory-utilization 0.92 \
    --limit-mm-per-prompt '{"image": 1, "video": 0}' --mm-processor-kwargs '{"max_pixels": 2000000}' \
    --enable-auto-tool-choice --tool-call-parser hermes \
    --enable-lora --max-loras 2 --max-lora-rank 16 --lora-modules "reader=$RUN/main/adapter" \
    --compilation-config '{"cudagraph_specialize_lora": false}' >"$OUT/vllm.log" 2>&1 &
PID=$!
trap 'kill $PID; wait $PID 2>/dev/null || true' EXIT
until curl -sf http://127.0.0.1:8000/health >/dev/null; do
    kill -0 "$PID" 2>/dev/null || { tail -60 "$OUT/vllm.log"; exit 1; }
    sleep 1
done
LEDGERLENS_NO_ENGINE=1 READER_MODEL=reader PYTHONPATH=src:. \
    .venv-infer/bin/python -m train.agent_live_check --out "$OUT" | tee "$OUT/summary.jsonl"
