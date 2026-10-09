#!/usr/bin/env bash
# Run on an A100 80 GB or L40S 48 GB Pod with the repo/network volume at /workspace.
set -euo pipefail
cd /workspace/runpod-ledgerlens
export HF_HOME=/workspace/hf-cache
mkdir -p /workspace/ledgerlens/{logs,done}
RUN=/workspace/ledgerlens
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
}
# uv must already be installed on the Pod. Separate environments isolate torch pins.
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
    .venv-train/bin/python -m ledgerlens.datagen --out "$RUN/synth" --n-businesses 400 --seed 7 --no-render
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
stage predict-8b .venv-infer/bin/python -m train.predict --model Qwen/Qwen3-VL-8B-Instruct --data "$RUN/data" --out "$RUN/predictions/8b"
stage predict-4b .venv-infer/bin/python -m train.predict --model Qwen/Qwen3-VL-4B-Instruct --data "$RUN/data" --out "$RUN/predictions/4b"
stage predict-ft .venv-infer/bin/python -m train.predict --model "$RUN/main/merged" --data "$RUN/data" --out "$RUN/predictions/4b-ft"
# Supplied prices are comparison inputs, not verified current Pod tariffs.
stage report .venv-infer/bin/python -m train.report --data "$RUN/data" --model "8b=$RUN/predictions/8b" --model "4b=$RUN/predictions/4b" --model "4b-ft=$RUN/predictions/4b-ft" --price 8b=0.72 --price 4b=0.34 --price 4b-ft=0.34 --out "$RUN/eval"
echo "$(date -Is) results in $RUN/eval; stop the Pod in the Runpod console."
