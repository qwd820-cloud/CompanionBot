# CompanionBot DGX Spark 部署 Dockerfile
# 适配 DGX Spark (Grace Blackwell GB10, ARM aarch64, CUDA 12.8+)

FROM nvcr.io/nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=all
# Grace Blackwell GB10 的 compute capability
ENV TORCH_CUDA_ARCH_LIST="10.0"

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    libsndfile1 ffmpeg libgl1-mesa-glx libglib2.0-0 \
    git build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

WORKDIR /app

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
