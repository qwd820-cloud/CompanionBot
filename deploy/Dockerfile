# CompanionBot DGX Spark 部署 Dockerfile
# 基于 NVIDIA CUDA 基础镜像，适配 DGX Spark 环境

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=all

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    libsndfile1 ffmpeg libgl1-mesa-glx libglib2.0-0 \
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
