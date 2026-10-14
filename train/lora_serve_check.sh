#!/usr/bin/env bash
# Serving check on a Pod (same vLLM 0.31 as the worker): is the reader as a LoRA adapter on the
# base 4B as accurate and fast as the merged fine-tune, and what do the cold-start settings cost?
# Run from /workspace/runpod-ledgerlens with the network volume at /workspace (see train/README.md).
#   PAGES   pages from test_unseen_layout for the batched comparison (default 200)
#   STAGES  space-separated subset of: merged lora lora_nospec lora_warm lora_sizes (default all)
set -euo pipefail
cd /workspace/runpod-ledgerlens
export HF_HOME=/workspace/hf-cache
PY=.venv-infer/bin/python
VLLM=.venv-infer/bin/vllm
RUN=/workspace/ledgerlens
OUT=$RUN/p0
PAGES=${PAGES:-200}
STAGES=${STAGES:-merged lora lora_nospec lora_warm lora_sizes}
BASE=Qwen/Qwen3-VL-4B-Instruct
ADAPTER=$RUN/main/adapter
mkdir -p "$OUT/data" "$OUT/pred" "$OUT/logs"
head -n "$PAGES" "$RUN/data/test_unseen_layout.jsonl" >"$OUT/data/test_unseen_layout.jsonl"
COMMON=(--host 127.0.0.1 --port 8000 --max-model-len 8192 --gpu-memory-utilization 0.92
    --limit-mm-per-prompt '{"image": 1, "video": 0}' --mm-processor-kwargs '{"max_pixels": 2000000}')
LORA=(--enable-lora --max-loras 2 --max-lora-rank 16 --lora-modules "reader=$ADAPTER")

serve() {
    local name=$1
    shift
    local started
    started=$(date +%s.%N)
    "$VLLM" serve "$@" "${COMMON[@]}" >"$OUT/logs/$name.log" 2>&1 &
    PID=$!
    until curl -sf http://127.0.0.1:8000/health >/dev/null; do
        if ! kill -0 "$PID" 2>/dev/null; then
            tail -60 "$OUT/logs/$name.log"
            exit 1
        fi
        sleep 1
    done
    echo "$name startup_seconds $(python3 -c "import time; print(round(time.time() - $started, 1))")" | tee -a "$OUT/startup.txt"
    grep -E "torch.compile takes|Graph capturing finished|Loading weights took|Model loading took|init engine" "$OUT/logs/$name.log" | sed "s/^/$name: /" >>"$OUT/startup_detail.txt" || true
}
halt() {
    kill "$PID"
    wait "$PID" 2>/dev/null || true
}
bench() { # model label concurrency pages
    "$PY" -m train.serve_bench --model "$1" --data "$OUT/data" --out "$OUT/pred/$2" \
        --concurrency "$3" --limit "$4" --label "$2" | tee -a "$OUT/bench.txt"
}
want() { [[ " $STAGES " == *" $1 "* ]]; }

if want merged; then
    serve merged "$RUN/main/merged" --served-model-name ledgerlens
    bench ledgerlens merged-c1 1 20
    bench ledgerlens merged-c8 8 "$PAGES"
    halt
fi
if want lora; then
    serve lora "$BASE" --served-model-name ledgerlens "${LORA[@]}"
    bench reader lora-c1 1 20
    bench reader lora-c8 8 "$PAGES"
    bench ledgerlens base-c8 8 40
    halt
fi
export VLLM_CACHE_ROOT=/workspace/vllm-cache-p0
if want lora_nospec; then
    rm -rf "$VLLM_CACHE_ROOT"
    serve lora_nospec "$BASE" --served-model-name ledgerlens "${LORA[@]}" \
        --compilation-config '{"cudagraph_specialize_lora": false}'
    bench reader lora_nospec-c8 8 "$PAGES"
    halt
fi
if want lora_warm; then
    serve lora_warm "$BASE" --served-model-name ledgerlens "${LORA[@]}" \
        --compilation-config '{"cudagraph_specialize_lora": false}'
    halt
fi
if want lora_sizes; then
    serve lora_sizes "$BASE" --served-model-name ledgerlens "${LORA[@]}" \
        --compilation-config '{"cudagraph_specialize_lora": false, "cudagraph_capture_sizes": [1, 2, 4, 8, 16]}'
    bench reader lora_sizes-c8 8 "$PAGES"
    halt
fi
echo "done; results in $OUT"
