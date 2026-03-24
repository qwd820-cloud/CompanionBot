#!/bin/bash
# CompanionBot DGX Spark 一键部署脚本
#
# 使用方法:
#   chmod +x deploy.sh
#   ./deploy.sh              # 完整部署 (含本地 LLM)
#   ./deploy.sh --dev        # 开发模式 (不用 Docker)
#   ./deploy.sh --stop       # 停止服务
#   ./deploy.sh --logs       # 查看日志

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[CompanionBot]${NC} $1"; }
warn() { echo -e "${YELLOW}[警告]${NC} $1"; }
error() { echo -e "${RED}[错误]${NC} $1"; exit 1; }

# 检查 NVIDIA GPU 并显示 UMA 内存状态
check_gpu() {
    if ! command -v nvidia-smi &>/dev/null; then
        error "未检测到 nvidia-smi，请确认 CUDA 驱动已安装"
    fi
    log "GPU 信息:"
    nvidia-smi --query-gpu=name,memory.total,memory.free,memory.used --format=csv,noheader

    # UMA 架构下 cudaMemGetInfo 报告可能偏低，显示系统实际可用内存作为参考
    local mem_available
    mem_available=$(awk '/MemAvailable/ {printf "%.1f GB", $2/1024/1024}' /proc/meminfo 2>/dev/null)
    if [ -n "$mem_available" ]; then
        log "系统可用内存 (UMA 共享): ${mem_available}"
        log "提示: UMA 架构下 GPU 和 CPU 共享内存，nvidia-smi 报告可能低于实际可分配量"
    fi
}

# DGX Spark UMA 优化: 刷新系统 buffer cache 释放可用内存给 GPU
flush_buffer_cache() {
    log "刷新系统 buffer cache (UMA 优化，释放更多内存给 GPU)..."
    if [ "$(id -u)" -eq 0 ]; then
        sync && echo 3 > /proc/sys/vm/drop_caches
        log "Buffer cache 已刷新"
    else
        sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null && \
            log "Buffer cache 已刷新" || \
            warn "需要 sudo 权限刷新 buffer cache，跳过 (非必需，但建议)"
    fi
}

# 检查 NVIDIA Container Toolkit
check_container_toolkit() {
    if ! docker info 2>/dev/null | grep -q -i nvidia; then
        warn "Docker NVIDIA runtime 未检测到，尝试配置..."
        if command -v nvidia-ctk &>/dev/null; then
            sudo nvidia-ctk runtime configure --runtime=docker
            sudo systemctl restart docker
            log "NVIDIA Container Runtime 已配置"
        else
            error "未找到 nvidia-ctk，请安装 NVIDIA Container Toolkit"
        fi
    fi
}

# 开发模式: 直接运行
dev_mode() {
    log "开发模式启动..."
    cd "$PROJECT_DIR"

    if [ ! -d "venv" ]; then
        log "创建虚拟环境..."
        python3 -m venv venv
    fi
    source venv/bin/activate

    log "安装依赖..."
    pip install -q -r requirements.txt

    mkdir -p server/data/chroma server/data/voiceprints

    log "启动服务: http://0.0.0.0:8765"
    python -m uvicorn server.main:app --host 0.0.0.0 --port 8765 --reload
}

# Docker 部署
docker_deploy() {
    if ! command -v docker &>/dev/null; then
        error "Docker 未安装"
    fi

    check_gpu
    check_container_toolkit

    # UMA 优化: 部署前刷新 buffer cache
    flush_buffer_cache

    cd "$PROJECT_DIR"

    log "完整部署 (含本地 Qwen3.5 LLM)..."
    docker compose -f deploy/docker-compose.yml up -d

    log "等待服务启动..."
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8765/health >/dev/null 2>&1; then
            log "服务已就绪: http://0.0.0.0:8765"
            log "WebSocket: ws://0.0.0.0:8765/ws/{client_id}"
            log "健康检查: http://0.0.0.0:8765/health"
            return
        fi
        sleep 2
    done

    warn "服务启动超时，请检查日志: docker logs companion-bot"
}

# 主入口
case "${1:-}" in
    --dev)
        dev_mode
        ;;
    --stop)
        log "停止服务..."
        cd "$PROJECT_DIR"
        docker compose -f deploy/docker-compose.yml down
        log "已停止"
        ;;
    --logs)
        docker logs -f companion-bot
        ;;
    *)
        docker_deploy
        ;;
esac
