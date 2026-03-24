# CompanionBot DGX Spark 部署 Dockerfile
# 使用 NGC 预构建 PyTorch 镜像 — 已针对 ARM (Grace) + Blackwell GPU 优化
#
# 为什么用 NGC PyTorch 镜像而不是裸 CUDA 镜像:
#   1. 预编译的 PyTorch/CUDA/cuDNN，省去 ARM 上的漫长编译
#   2. 包含 TensorRT、NCCL 等已针对 Blackwell 优化的库
#   3. NVIDIA 官方维护，与 DGX Spark 兼容性有保证

FROM nvcr.io/nvidia/pytorch:24.12-py3

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=all
# Grace Blackwell GB10 的 compute capability
ENV TORCH_CUDA_ARCH_LIST="10.0"

# 额外系统依赖 (NGC PyTorch 镜像已有 Python 3.11 + 常用库)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 ffmpeg libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖 (PyTorch/torchaudio 已在基础镜像中，跳过重装)
COPY requirements.txt .
RUN pip install --no-cache-dir --no-deps \
    fastapi uvicorn websockets \
    speechbrain insightface onnxruntime-gpu \
    chromadb silero-vad edge-tts funasr openai \
    pyyaml numpy opencv-python pillow aiofiles python-multipart pydantic

# 应用代码
COPY config/ config/
COPY server/ server/

# 数据目录
RUN mkdir -p server/data/chroma server/data/voiceprints

EXPOSE 8765

# 启动
CMD ["python", "-m", "uvicorn", "server.main:app", \
     "--host", "0.0.0.0", "--port", "8765", \
     "--workers", "1", "--log-level", "info"]
