# vLLM's official image. cu129 (CUDA 12.9) rather than the CUDA 13 default, so it runs on more
# Runpod hosts' drivers. Qwen3-VL needs vLLM >= 0.11.
ARG VLLM_IMAGE=vllm/vllm-openai:v0.31.0-cu129
FROM ${VLLM_IMAGE}

# The base image's entrypoint starts the vLLM server; our handler starts it instead.
ENTRYPOINT []
WORKDIR /app

# Pin everything already installed, so adding runpod/httpx can never upgrade a package vLLM
# depends on. If they conflict, the build fails here instead of the worker failing later.
RUN pip list --format=freeze > /tmp/constraints.txt \
 && pip install --no-cache-dir -c /tmp/constraints.txt runpod httpx
COPY pyproject.toml /app/pyproject.toml
COPY src/ledgerlens /app/src/ledgerlens
RUN pip install --no-cache-dir --no-deps /app
COPY worker /app/worker

# Optional fine-tuned weights: enable these lines after supplying MODEL_DIR.
ARG MODEL_DIR=
# COPY ${MODEL_DIR}/ /models/ft/
# ENV MODEL_PATH=/models/ft

CMD ["python3", "-u", "/app/worker/handler.py"]
