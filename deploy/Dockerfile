# CompanionBot DGX Spark 部署 Dockerfile
# 使用 NGC 预构建 PyTorch 镜像 — 已针对 ARM (Grace) + Blackwell GPU 优化
#
# 为什么用 NGC PyTorch 镜像而不是裸 CUDA 镜像:
#   1. 预编译的 PyTorch/CUDA/cuDNN，省去 ARM 上的漫长编译
#   2. 包含 TensorRT、NCCL 等已针对 Blackwell 优化的库
#   3. NVIDIA 官方维护，与 DGX Spark 兼容性有保证

FROM nvcr.io/nvidia/pytorch:25.04-py3

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
# 禁用代理 — Docker daemon 的 HTTP 代理太慢
ENV http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY="" ALL_PROXY="" all_proxy="" no_proxy="*"
ENV CUDA_VISIBLE_DEVICES=all
# Grace Blackwell GB10 的 compute capability
ENV TORCH_CUDA_ARCH_LIST="10.0"

# 使用清华 apt 镜像加速（仅构建阶段，不影响运行时）
RUN sed -i 's|http://ports.ubuntu.com/ubuntu-ports|https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || \
    sed -i 's|http://ports.ubuntu.com/ubuntu-ports|https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports|g' /etc/apt/sources.list 2>/dev/null || true

# 额外系统依赖 (NGC PyTorch 镜像已有 Python 3.11 + 常用库)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 ffmpeg libgl1-mesa-dri libglib2.0-0 libglx-mesa0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖 (PyTorch/torchaudio 已在基础镜像中，跳过重装)
COPY requirements.txt .
RUN echo "" > /etc/pip/constraint.txt && \
    pip install --no-cache-dir \
    -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple --trusted-host mirrors.tuna.tsinghua.edu.cn \
    fastapi uvicorn websockets \
    speechbrain insightface onnxruntime \
    chromadb silero-vad edge-tts funasr openai \
    pyyaml "numpy<2" opencv-python pillow aiofiles python-multipart pydantic

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
