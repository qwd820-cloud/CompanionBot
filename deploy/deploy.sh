#!/bin/bash
# CompanionBot DGX Spark 一键部署脚本
#
# 使用方法:
#   chmod +x deploy.sh
#   ./deploy.sh              # 完整部署 (含 LLM)
#   ./deploy.sh --no-llm     # 仅部署主服务 (使用云端 Kimi API)
#   ./deploy.sh --dev        # 开发模式 (不用 Docker)

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

# 检查 NVIDIA GPU
check_gpu() {
    if ! command -v nvidia-smi &>/dev/null; then
        error "未检测到 nvidia-smi，请确认 CUDA 驱动已安装"
    fi
    log "GPU 信息:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
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
    local no_llm=$1

    if ! command -v docker &>/dev/null; then
        error "Docker 未安装"
    fi

    check_gpu
    cd "$PROJECT_DIR"

    if [ "$no_llm" = true ]; then
        log "部署主服务 (不含本地 LLM)..."
        if [ -z "${KIMI_API_KEY:-}" ]; then
            warn "未设置 KIMI_API_KEY，LLM 功能将不可用"
            warn "设置方法: export KIMI_API_KEY=your-api-key"
        fi
        docker compose -f deploy/docker-compose.yml up -d companion-bot
    else
        log "完整部署 (含本地 Qwen3.5 LLM)..."
        docker compose -f deploy/docker-compose.yml up -d
    fi

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
    --no-llm)
        docker_deploy true
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
        docker_deploy false
        ;;
esac
