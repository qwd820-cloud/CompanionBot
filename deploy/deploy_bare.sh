#!/bin/bash
# CompanionBot DGX Spark 裸机部署脚本 (无 Docker)
#
# 适配环境:
#   - DGX Spark (ARM aarch64, Grace + Blackwell GB10, 128GB UMA)
#   - 无 Docker 权限，直接在宿主机运行
#   - Python 3.12 + venv
#
# 使用方法:
#   chmod +x deploy_bare.sh
#   ./deploy_bare.sh setup         # 首次: 安装依赖 + 下载模型
#   ./deploy_bare.sh start         # 启动所有服务
#   ./deploy_bare.sh stop          # 停止所有服务
#   ./deploy_bare.sh status        # 查看运行状态
#   ./deploy_bare.sh logs          # 查看日志
#   ./deploy_bare.sh start-llm     # 仅启动 LLM 推理服务
#   ./deploy_bare.sh start-bot     # 仅启动 CompanionBot 后端

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${PROJECT_DIR}/venv"
DATA_DIR="${PROJECT_DIR}/server/data"
LOG_DIR="${PROJECT_DIR}/logs"
PID_DIR="${PROJECT_DIR}/run"

# 模型配置
LLM_MODEL="${LLM_MODEL:-Qwen/Qwen3.5-27B}"
LLM_PORT="${LLM_PORT:-8000}"
BOT_PORT="${BOT_PORT:-8765}"
HF_HOME="${HF_HOME:-${HOME}/.cache/huggingface}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[CompanionBot]${NC} $1"; }
warn() { echo -e "${YELLOW}[警告]${NC} $1"; }
err()  { echo -e "${RED}[错误]${NC} $1"; }
info() { echo -e "${CYAN}[信息]${NC} $1"; }

# ============ 环境检测 ============

check_gpu() {
    log "检测 GPU..."
    if ! command -v nvidia-smi &>/dev/null; then
        err "未检测到 nvidia-smi，请确认 CUDA 驱动已安装"
        return 1
    fi
    nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader
    echo ""

    local mem_total mem_avail
    mem_total=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
    mem_avail=$(awk '/MemAvailable/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
    log "系统内存: ${mem_avail}GB 可用 / ${mem_total}GB 总计 (UMA 共享)"
}

check_python() {
    local py_cmd
    if [ -f "${VENV_DIR}/bin/python" ]; then
        py_cmd="${VENV_DIR}/bin/python"
    elif command -v python3 &>/dev/null; then
        py_cmd="python3"
    else
        err "未找到 Python3"
        return 1
    fi
    local ver
    ver=$($py_cmd --version 2>&1)
    log "Python: $ver ($py_cmd)"
}

# ============ 安装配置 ============

setup() {
    log "====== CompanionBot DGX Spark 裸机部署 ======"
    echo ""

    check_gpu || true
    check_python

    cd "$PROJECT_DIR"
    mkdir -p "$DATA_DIR/chroma" "$DATA_DIR/voiceprints" "$LOG_DIR" "$PID_DIR"

    # 1. 创建/激活 venv
    if [ ! -f "${VENV_DIR}/bin/python" ]; then
        log "创建虚拟环境..."
        python3 -m venv "$VENV_DIR"
    fi
    log "激活虚拟环境: ${VENV_DIR}"
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"

    # 2. 安装基础依赖
    log "安装 Python 依赖..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q 2>&1 | tail -3
    log "Python 依赖安装完成"

    # 3. 安装 LLM 推理引擎 (SGLang)
    log "安装 SGLang (LLM 推理引擎)..."
    pip install "sglang[all]" -q 2>&1 | tail -3 || {
        warn "SGLang 安装失败，尝试 vLLM..."
        pip install vllm -q 2>&1 | tail -3 || warn "vLLM 也安装失败，后续需手动安装 LLM 推理引擎"
    }

    # 4. 安装 huggingface-cli
    pip install huggingface_hub -q

    # 5. 下载 LLM 模型
    download_model

    log "====== 安装完成 ======"
    echo ""
    log "启动服务:  ./deploy_bare.sh start"
    log "查看状态:  ./deploy_bare.sh status"
}

download_model() {
    log "下载 LLM 模型: ${LLM_MODEL}"
    info "模型缓存目录: ${HF_HOME}"
    info "模型较大 (~54GB)，首次下载需要较长时间..."
    echo ""

    export HF_HOME
    "${VENV_DIR}/bin/huggingface-cli" download "${LLM_MODEL}" \
        --local-dir-use-symlinks True \
        2>&1 | grep -E "(Downloading|Fetching|downloading|Download)" || true

    if [ $? -eq 0 ]; then
        log "模型下载完成: ${LLM_MODEL}"
    else
        warn "模型下载可能未完成，请检查网络后重试: ./deploy_bare.sh setup"
    fi
}

# ============ 服务管理 ============

start_llm() {
    if is_running "llm"; then
        warn "LLM 引擎已在运行 (PID: $(cat "$PID_DIR/llm.pid"))"
        return 0
    fi

    log "启动 LLM 推理引擎 (${LLM_MODEL} @ :${LLM_PORT})..."
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"

    export HF_HOME
    # UMA 内存优化: 分配 60% 内存给 LLM
    export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"

    # 判断用 sglang 还是 vllm
    if python -c "import sglang" 2>/dev/null; then
        log "使用 SGLang 引擎"
        nohup python -m sglang.launch_server \
            --model-path "$LLM_MODEL" \
            --port "$LLM_PORT" \
            --host 0.0.0.0 \
            --tp 1 \
            --mem-fraction-static 0.60 \
            --trust-remote-code \
            > "$LOG_DIR/llm.log" 2>&1 &
    elif python -c "import vllm" 2>/dev/null; then
        log "使用 vLLM 引擎"
        nohup python -m vllm.entrypoints.openai.api_server \
            --model "$LLM_MODEL" \
            --port "$LLM_PORT" \
            --host 0.0.0.0 \
            --tensor-parallel-size 1 \
            --gpu-memory-utilization 0.60 \
            --trust-remote-code \
            > "$LOG_DIR/llm.log" 2>&1 &
    else
        err "未找到 SGLang 或 vLLM，请先运行 ./deploy_bare.sh setup"
        return 1
    fi

    echo $! > "$PID_DIR/llm.pid"
    log "LLM 引擎已启动 (PID: $!, 日志: $LOG_DIR/llm.log)"

    # 等待 LLM 就绪
    log "等待 LLM 加载模型 (可能需要 2~5 分钟)..."
    local ready=false
    for i in $(seq 1 90); do
        if curl -sf "http://localhost:${LLM_PORT}/health" >/dev/null 2>&1 || \
           curl -sf "http://localhost:${LLM_PORT}/v1/models" >/dev/null 2>&1; then
            ready=true
            break
        fi
        # 每 10 秒打一次进度
        if (( i % 5 == 0 )); then
            info "已等待 $((i * 2)) 秒..."
        fi
        sleep 2
    done

    if $ready; then
        log "LLM 引擎就绪!"
    else
        warn "LLM 加载超时 (3分钟)，可能仍在加载中。查看日志: tail -f $LOG_DIR/llm.log"
    fi
}

start_bot() {
    if is_running "bot"; then
        warn "CompanionBot 已在运行 (PID: $(cat "$PID_DIR/bot.pid"))"
        return 0
    fi

    log "启动 CompanionBot 后端 (:${BOT_PORT})..."
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"

    export LOCAL_LLM_URL="http://localhost:${LLM_PORT}/v1"
    export CUDA_VISIBLE_DEVICES=0
    # UMA: 感知层限制 15% 显存
    export TORCH_CUDA_ALLOC_FRACTION=0.15
    export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"

    cd "$PROJECT_DIR"
    nohup python -m uvicorn server.main:app \
        --host 0.0.0.0 \
        --port "$BOT_PORT" \
        > "$LOG_DIR/bot.log" 2>&1 &

    echo $! > "$PID_DIR/bot.pid"
    log "CompanionBot 已启动 (PID: $!, 日志: $LOG_DIR/bot.log)"

    # 等待就绪
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:${BOT_PORT}/health" >/dev/null 2>&1; then
            log "CompanionBot 就绪!"
            log "  HTTP:      http://0.0.0.0:${BOT_PORT}"
            log "  WebSocket: ws://0.0.0.0:${BOT_PORT}/ws/{client_id}"
            log "  健康检查:  http://0.0.0.0:${BOT_PORT}/health"
            return 0
        fi
        sleep 2
    done

    warn "CompanionBot 启动超时，查看日志: tail -f $LOG_DIR/bot.log"
}

start_all() {
    log "====== 启动所有服务 ======"
    check_gpu || true

    # UMA 优化: 刷新 buffer cache
    flush_cache

    start_llm
    start_bot

    echo ""
    log "====== 所有服务已启动 ======"
    show_status
}

stop_service() {
    local name=$1
    local pid_file="$PID_DIR/${name}.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            log "停止 ${name} (PID: $pid)..."
            kill "$pid"
            # 等待进程退出
            for i in $(seq 1 10); do
                if ! kill -0 "$pid" 2>/dev/null; then
                    break
                fi
                sleep 1
            done
            # 如果还没退出，强制终止
            if kill -0 "$pid" 2>/dev/null; then
                warn "${name} 未响应 SIGTERM，发送 SIGKILL..."
                kill -9 "$pid" 2>/dev/null || true
            fi
            log "${name} 已停止"
        else
            warn "${name} 进程已不存在 (PID: $pid)"
        fi
        rm -f "$pid_file"
    else
        info "${name}: 未在运行"
    fi
}

stop_all() {
    log "停止所有服务..."
    stop_service "bot"
    stop_service "llm"
    log "所有服务已停止"
}

is_running() {
    local name=$1
    local pid_file="$PID_DIR/${name}.pid"
    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        return 0
    fi
    return 1
}

show_status() {
    echo ""
    echo "  服务状态:"
    echo "  ────────────────────────────────────────"

    if is_running "llm"; then
        echo -e "  LLM 引擎:    ${GREEN}运行中${NC} (PID: $(cat "$PID_DIR/llm.pid"))"
        # 尝试获取模型信息
        local models
        models=$(curl -sf "http://localhost:${LLM_PORT}/v1/models" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null || echo "加载中...")
        echo "                模型: $models"
    else
        echo -e "  LLM 引擎:    ${RED}未运行${NC}"
    fi

    if is_running "bot"; then
        echo -e "  CompanionBot: ${GREEN}运行中${NC} (PID: $(cat "$PID_DIR/bot.pid"))"
        local health
        health=$(curl -sf "http://localhost:${BOT_PORT}/health" 2>/dev/null || echo '{"status":"unreachable"}')
        echo "                健康: $health"
    else
        echo -e "  CompanionBot: ${RED}未运行${NC}"
    fi

    echo "  ────────────────────────────────────────"
    echo ""
}

show_logs() {
    local service="${1:-all}"
    case "$service" in
        llm)  tail -f "$LOG_DIR/llm.log" ;;
        bot)  tail -f "$LOG_DIR/bot.log" ;;
        *)    tail -f "$LOG_DIR/llm.log" "$LOG_DIR/bot.log" ;;
    esac
}

flush_cache() {
    if [ "$(id -u)" -eq 0 ]; then
        sync && echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
        log "Buffer cache 已刷新 (UMA 优化)"
    else
        sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null && \
            log "Buffer cache 已刷新 (UMA 优化)" || true
    fi
}

# ============ 主入口 ============

case "${1:-help}" in
    setup)
        setup
        ;;
    start)
        start_all
        ;;
    start-llm)
        start_llm
        ;;
    start-bot)
        start_bot
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        sleep 2
        start_all
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "${2:-all}"
        ;;
    download-model)
        # shellcheck disable=SC1091
        source "${VENV_DIR}/bin/activate"
        download_model
        ;;
    help|--help|-h)
        echo ""
        echo "CompanionBot DGX Spark 裸机部署脚本"
        echo ""
        echo "用法: ./deploy_bare.sh <命令>"
        echo ""
        echo "命令:"
        echo "  setup           首次部署: 安装依赖 + 下载模型"
        echo "  start           启动所有服务 (LLM + CompanionBot)"
        echo "  stop            停止所有服务"
        echo "  restart         重启所有服务"
        echo "  status          查看运行状态"
        echo "  logs [llm|bot]  查看日志 (默认全部)"
        echo "  start-llm       仅启动 LLM 推理引擎"
        echo "  start-bot       仅启动 CompanionBot 后端"
        echo "  download-model  重新下载模型"
        echo ""
        echo "环境变量:"
        echo "  LLM_MODEL       LLM 模型 (默认: Qwen/Qwen3.5-27B)"
        echo "  LLM_PORT        LLM 端口 (默认: 8000)"
        echo "  BOT_PORT        Bot 端口 (默认: 8765)"
        echo "  HF_HOME         HuggingFace 缓存目录"
        echo ""
        echo "示例:"
        echo "  ./deploy_bare.sh setup              # 首次安装"
        echo "  ./deploy_bare.sh start              # 启动服务"
        echo "  ./deploy_bare.sh logs llm           # 看 LLM 日志"
        echo "  LLM_MODEL=Qwen/Qwen3.5-9B ./deploy_bare.sh setup  # 用小模型"
        echo ""
        ;;
    *)
        err "未知命令: $1"
        echo "运行 ./deploy_bare.sh help 查看帮助"
        exit 1
        ;;
esac
