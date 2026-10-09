#!/usr/bin/env bash
# Run on a Pod with >= 32 GB VRAM (RTX PRO 4500 / RTX 5090 in EU-RO-1, or A100/L40S elsewhere)
# with the repo at /workspace/runpod-ledgerlens and the network volume at /workspace.
#
# Settings (environment variables):
#   RUN           output folder (default /workspace/ledgerlens; use another for a rehearsal)
#   N_BUSINESSES  synthetic businesses to generate (default 400)
#   STOP_AFTER    stop after this stage, e.g. smoke (default: run everything)
#   PREDICT_LIMIT pages per test set for predictions (default: all; a limit is a smoke check only)
#   GPU_PRICE     USD/hour of this Pod's GPU, from the console, used for cost per 1,000 pages
set -euo pipefail
cd /workspace/runpod-ledgerlens
# Model weights and Python packages are cached on the network volume, so a second run reuses them.
export HF_HOME=/workspace/hf-cache UV_CACHE_DIR=/workspace/uv-cache
RUN=${RUN:-/workspace/ledgerlens}
N_BUSINESSES=${N_BUSINESSES:-400}
STOP_AFTER=${STOP_AFTER:-}
GPU_PRICE=${GPU_PRICE:?set GPU_PRICE to this Pod GPU price in USD/hour}
LIMIT=()
if [[ -n "${PREDICT_LIMIT:-}" ]]; then LIMIT=(--limit "$PREDICT_LIMIT"); fi
mkdir -p "$RUN"/{logs,done}
command -v uv >/dev/null || { pip install -q uv && hash -r; }
stage() {
    local name="$1"
    shift
    if [[ -f "$RUN/done/$name" ]]; then
        echo "$(date -Is) skipping $name"
        return
    fi
    echo "$(date -Is) starting $name"
    "$@" 2>&1 | tee "$RUN/logs/$name.log"
    touch "$RUN/done/$name"
    echo "$(date -Is) completed $name"
    if [[ "$name" == "$STOP_AFTER" ]]; then
        echo "$(date -Is) stopping after $name (STOP_AFTER)"
        exit 0
    fi
}
# Separate environments isolate torch pins.
setup_train() {
    uv venv --python 3.11 .venv-train
    uv pip install --python .venv-train/bin/python -e . unsloth trl datasets playwright
    .venv-train/bin/python -m playwright install --with-deps chromium
    uv pip freeze --python .venv-train/bin/python > "$RUN/logs/train-packages.txt"
}
setup_infer() {
    uv venv --python 3.11 .venv-infer
    uv pip install --python .venv-infer/bin/python -e . 'vllm==0.31.0'
    uv pip freeze --python .venv-infer/bin/python > "$RUN/logs/infer-packages.txt"
}
generate() {
    # Remove only an incomplete stage's generated data: datagen refuses existing splits.
    if [[ -d "$RUN/synth" ]]; then mv "$RUN/synth" "$RUN/synth-incomplete-$(date +%s)"; fi
    .venv-train/bin/python -m ledgerlens.datagen --out "$RUN/synth" --n-businesses "$N_BUSINESSES" --seed 7 --no-render
    .venv-train/bin/python -m train.render_pod --data "$RUN/synth"
}
stage setup-train setup_train
stage setup-infer setup_infer
stage generate generate
stage dataset .venv-train/bin/python -m train.build_dataset --synth "$RUN/synth" --out "$RUN/data" --cord
# Smoke includes eval, masking preflight, adapter save and merged-weight export.
stage smoke .venv-train/bin/python -m train.sft --data "$RUN/data" --out "$RUN/smoke" --max-steps 30 --eval-steps 30 --seed 7
# To resume an interrupted main run, add --resume-from-checkpoint /workspace/.../checkpoint-N.
stage main .venv-train/bin/python -m train.sft --data "$RUN/data" --out "$RUN/main" --epochs 1 --seed 7
stage predict-8b .venv-infer/bin/python -m train.predict --model Qwen/Qwen3-VL-8B-Instruct --data "$RUN/data" --out "$RUN/predictions/8b" "${LIMIT[@]}"
stage predict-4b .venv-infer/bin/python -m train.predict --model Qwen/Qwen3-VL-4B-Instruct --data "$RUN/data" --out "$RUN/predictions/4b" "${LIMIT[@]}"
stage predict-ft .venv-infer/bin/python -m train.predict --model "$RUN/main/merged" --data "$RUN/data" --out "$RUN/predictions/4b-ft" "${LIMIT[@]}"
# All three models ran on this Pod's GPU, so they share its measured price; the speed differs.
stage report .venv-infer/bin/python -m train.report --data "$RUN/data" --model "8b=$RUN/predictions/8b" --model "4b=$RUN/predictions/4b" --model "4b-ft=$RUN/predictions/4b-ft" --price "8b=$GPU_PRICE" --price "4b=$GPU_PRICE" --price "4b-ft=$GPU_PRICE" --out "$RUN/eval"
echo "$(date -Is) results in $RUN/eval; stop the Pod in the Runpod console."
