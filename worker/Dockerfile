# vLLM's official image. cu129 (CUDA 12.9) rather than the CUDA 13 default, so it runs on more
# Runpod hosts' drivers. Qwen3-VL needs vLLM >= 0.11.
ARG VLLM_IMAGE=vllm/vllm-openai:v0.31.0-cu129
FROM ${VLLM_IMAGE}

# The base image's entrypoint starts the vLLM server; our handler starts it instead.
ENTRYPOINT []
WORKDIR /app

# Constraints keep httpx and other installed packages at the vLLM image's versions.
RUN pip list --format=freeze > /tmp/constraints.txt \
 && pip install --no-cache-dir -c /tmp/constraints.txt runpod==1.12.0 httpx
# The cu129 image ships a CUDA 13 torchcodec (needs libcudart.so.13), which raises OSError when vLLM
# imports it and kills the server at start. We only send images, so remove it; vLLM treats a missing
# torchcodec as "no audio decoding". The import check fails the build, not the worker, if this recurs.
RUN pip uninstall -y torchcodec \
 && python3 -c "import vllm.multimodal.media"
COPY pyproject.toml /app/pyproject.toml
COPY src/ledgerlens /app/src/ledgerlens
RUN pip install --no-cache-dir --no-deps /app
COPY worker /app/worker

# For weights inside the image, use worker/Dockerfile.baked.

CMD ["python3", "-u", "/app/worker/handler.py"]
