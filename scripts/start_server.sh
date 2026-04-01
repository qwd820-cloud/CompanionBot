#!/usr/bin/env bash
# CompanionBot 启动脚本 — 清除代理 + 启动 LLM + 启动服务
set -u
cd "$(dirname "$0")/.."

# 1. 清除所有代理
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy
export NO_PROXY="*"
export no_proxy="*"

# 2. 确保本地 LLM 在运行
if ! curl -s http://localhost:57847/health >/dev/null 2>&1; then
    echo "启动本地 LLM (llama-server)..."
    LLAMA_SERVER="$HOME/.unsloth/llama.cpp/build/bin/llama-server"
    MODEL="$HOME/models/tiantian-4b-gguf/Qwen3.5-4B.Q4_K_M.gguf"
    if [ -f "$LLAMA_SERVER" ] && [ -f "$MODEL" ]; then
        nohup "$LLAMA_SERVER" --model "$MODEL" \
            --host 0.0.0.0 --port 57847 \
            --ctx-size 4096 --n-gpu-layers 99 --threads 4 \
            > /tmp/llama-server.log 2>&1 &
        echo "等待 LLM 启动..."
        for i in $(seq 1 30); do
            sleep 1
            if curl -s http://localhost:57847/health >/dev/null 2>&1; then
                echo "LLM 就绪"
                break
            fi
        done
    else
        echo "⚠ llama-server 或模型文件不存在"
    fi
fi

# 3. 启动 CompanionBot
echo "启动 CompanionBot..."
exec venv/bin/python -m uvicorn server.main:app \
    --host 0.0.0.0 --port 8765 --workers 1 --log-level info
