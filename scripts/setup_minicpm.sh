#!/usr/bin/env bash
# MiniCPM-o 4.5 环境准备脚本 — DGX Spark (aarch64 + CUDA 13.0)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "━━━ MiniCPM-o 4.5 环境准备 ━━━"
echo "平台: $(uname -m), CUDA: $(nvcc --version 2>/dev/null | grep release | awk '{print $5}' | tr -d ',')"
echo ""

# 清除代理 — DGX Spark 本地代理会严重限速模型下载
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy
export NO_PROXY="*"
export no_proxy="*"
echo "✓ 已清除代理环境变量"

# 激活虚拟环境
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
else
    echo "错误: venv 不存在，请先运行 python -m venv venv"
    exit 1
fi

# ===== Step 1: PyTorch CUDA 版本 =====
echo "━━━ Step 1: 检查 PyTorch CUDA =====━━━"
CUDA_OK=$(python -c "import torch; print('yes' if torch.cuda.is_available() else 'no')" 2>/dev/null || echo "no")
if [ "$CUDA_OK" = "yes" ]; then
    echo "✓ PyTorch CUDA 已可用: $(python -c 'import torch; print(torch.__version__, torch.version.cuda)')"
else
    echo "PyTorch 需要 CUDA 版本，正在安装..."
    pip install torch torchaudio --force-reinstall --index-url https://download.pytorch.org/whl/cu130
    # 验证
    CUDA_OK=$(python -c "import torch; print('yes' if torch.cuda.is_available() else 'no')" 2>/dev/null || echo "no")
    if [ "$CUDA_OK" = "yes" ]; then
        echo "✓ PyTorch CUDA 安装成功"
    else
        echo "✗ PyTorch CUDA 安装失败，请检查 CUDA 驱动"
        exit 1
    fi
fi

# ===== Step 2: 安装 MiniCPM-o 依赖 =====
echo ""
echo "━━━ Step 2: 安装 MiniCPM-o 依赖 ━━━"
pip install --quiet accelerate soundfile librosa 2>&1 | tail -3
# minicpmo-utils[all] 依赖 decord，aarch64 无预编译包，用 --no-deps 跳过
pip install --quiet "minicpmo-utils>=1.0.5" --no-deps 2>&1 | tail -3
# 尝试装 decord (视频解码，音频对话不需要)
pip install --quiet decord 2>/dev/null || echo "  ⚠ decord 不可用 (aarch64 无预编译包)，视频功能将用 ffmpeg 回退"
echo "✓ 依赖安装完成"

# ===== Step 3: 下载模型 =====
echo ""
echo "━━━ Step 3: 下载 MiniCPM-o 4.5 模型 ━━━"
MODEL_ID="openbmb/MiniCPM-o-4_5"
# 检查是否已缓存
CACHED=$(python -c "
from huggingface_hub import try_to_load_from_cache
result = try_to_load_from_cache('$MODEL_ID', 'config.json')
print('yes' if result is not None else 'no')
" 2>/dev/null || echo "no")

if [ "$CACHED" = "yes" ]; then
    echo "✓ 模型已缓存，跳过下载"
else
    echo "下载模型 $MODEL_ID (~18GB)，使用 hf-mirror.com 镜像..."
    HF_ENDPOINT=https://hf-mirror.com python -c "
import os
# 确保下载进程内也无代理
for k in ['ALL_PROXY','all_proxy','HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy']:
    os.environ.pop(k, None)
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from huggingface_hub import snapshot_download
snapshot_download('$MODEL_ID', resume_download=True)
print('✓ 模型下载完成')
"
fi

# ===== Step 4: 检查参考语音 =====
echo ""
echo "━━━ Step 4: 参考语音文件 ━━━"
VOICE_REF="config/voice_ref.wav"
if [ -f "$VOICE_REF" ]; then
    echo "✓ 参考语音已就绪: $VOICE_REF"
    python -c "
import librosa
y, sr = librosa.load('$VOICE_REF', sr=16000)
dur = len(y) / sr
print(f'  时长: {dur:.1f}秒, 采样率: {sr}Hz')
if dur < 5:
    print('  ⚠ 建议 10-15 秒以获得更好的声音克隆效果')
"
else
    echo "⚠ 未找到参考语音: $VOICE_REF"
    echo "  声音克隆需要一段 10-15 秒的"天天"角色语音样本"
    echo "  录制后放置到 $VOICE_REF (16kHz WAV 格式)"
    echo "  没有参考语音也可运行，将使用模型默认声音"
fi

# ===== Step 5: 快速验证 =====
echo ""
echo "━━━ Step 5: 快速验证模型加载 ━━━"
python -c "
import torch
print(f'PyTorch: {torch.__version__}, CUDA: {torch.version.cuda}')
print(f'GPU: {torch.cuda.get_device_name(0)}')
print(f'可用显存: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB')

print('加载 MiniCPM-o 4.5 (仅验证，不完整初始化)...')
from transformers import AutoConfig
config = AutoConfig.from_pretrained('$MODEL_ID', trust_remote_code=True)
print(f'✓ 模型配置加载成功: {config.model_type}')
print(f'  隐藏维度: {config.hidden_size}, 层数: {config.num_hidden_layers}')
"

# ===== Step 6: 启用配置 =====
echo ""
echo "━━━ Step 6: 配置状态 ━━━"
ENABLED=$(python -c "
import yaml
with open('config/minicpm.yaml') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('minicpm', {}).get('enabled', False))
")
if [ "$ENABLED" = "True" ]; then
    echo "✓ MiniCPM-o 已启用 (config/minicpm.yaml → enabled: true)"
else
    echo "⚠ MiniCPM-o 当前未启用"
    echo "  启用方法: 编辑 config/minicpm.yaml，将 enabled 改为 true"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  准备完成！"
echo ""
echo "  启用 MiniCPM-o:"
echo "    1. 编辑 config/minicpm.yaml → enabled: true"
echo "    2. (可选) 放置参考语音 config/voice_ref.wav"
echo "    3. 运行 ./scripts/start_server.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
