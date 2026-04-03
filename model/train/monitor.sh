#!/bin/bash
# 天天训练数据生成 — 自动监控 & 断点续传脚本
# 每小时检查一次进程，如果中断则自动恢复

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export KIMI_CODE_API_KEY="sk-kimi-cN7M5wonZHfpvht2reL4nNpImA37Elx1zfR94tusNstRtRxgS4NQRp0aQptabksQ"
LOG_FILE="$SCRIPT_DIR/generate.log"
MONITOR_LOG="$SCRIPT_DIR/monitor.log"

echo "[$(date)] 监控启动" >> "$MONITOR_LOG"

while true; do
    # 检查 generate.py 是否在运行
    if pgrep -f "generate.py" > /dev/null 2>&1; then
        # 进程在运行，获取当前进度
        LINES=$(wc -l < "$SCRIPT_DIR/training_data.jsonl" 2>/dev/null || echo 0)
        echo "[$(date)] 正常运行中，已生成 ${LINES} 条" >> "$MONITOR_LOG"
    else
        # 进程不在了，检查是否已完成
        LINES=$(wc -l < "$SCRIPT_DIR/training_data.jsonl" 2>/dev/null || echo 0)
        LINES=$(echo "$LINES" | tr -d ' ')

        if [ "$LINES" -ge 11000 ]; then
            echo "[$(date)] 生成基本完成（${LINES} 条），停止监控" >> "$MONITOR_LOG"
            exit 0
        fi

        echo "[$(date)] 进程中断！已生成 ${LINES} 条，正在恢复..." >> "$MONITOR_LOG"

        # 断点续传
        nohup python3 -u "$SCRIPT_DIR/generate.py" --resume --workers 4 --seed 42 >> "$LOG_FILE" 2>&1 &
        NEW_PID=$!
        echo "[$(date)] 已重启，新 PID: $NEW_PID" >> "$MONITOR_LOG"
    fi

    # 每小时检查一次
    sleep 3600
done
