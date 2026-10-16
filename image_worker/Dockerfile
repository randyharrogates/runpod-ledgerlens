# CUDA 12.9 supports Blackwell and older GPUs on compatible Runpod hosts.
FROM pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime
WORKDIR /app

# Pin the inference stack; fail the build early if FLUX or Runpod cannot import.
RUN pip install --no-cache-dir diffusers==0.35.2 transformers==4.56.2 \
    accelerate==1.10.1 sentencepiece==0.2.1 protobuf==5.29.5 runpod==1.7.13 \
 && python -c "from diffusers import FluxPipeline; import runpod"

# Resolve the mounted cache explicitly rather than changing HF_HOME.
ENV HF_HUB_ENABLE_HF_TRANSFER=0 PYTHONUNBUFFERED=1
COPY image_worker /app/image_worker
CMD ["python", "-u", "/app/image_worker/handler.py"]
