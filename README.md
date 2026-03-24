# CompanionBot — 家庭陪伴机器人大脑系统

面向老人看护和小孩陪伴的家庭陪伴机器人"大脑"软件系统。以"像人"为核心设计原则，构建一个有记忆、有性格、懂得察言观色的家庭成员。

## 架构

```
手机端 (感知终端)  ◄── WebSocket ──►  DGX Spark 后端 (大脑)
├── 麦克风采集音频                       ├── 声纹识别 (SpeechBrain)
├── 摄像头采集视频                       ├── 人脸识别 (InsightFace)
├── 扬声器播放回复                       ├── LLM 推理 (本地 Qwen3.5)
└── 短信发送通知                         ├── 四层记忆系统
                                         └── 人格引擎 + 安全预警
```

## 四层系统设计

| 层级 | 职责 | 核心模块 |
|------|------|---------|
| 感知层 | 谁在说什么、在做什么 | VAD, 声纹识别, 人脸识别, ASR, 身份融合 |
| 记忆层 | 记住每次互动 | 工作记忆, 情景记忆, 语义记忆, 长期档案 |
| 人格层 | 有性格、懂察言观色 | 人格引擎, 情绪状态机, 插话决策, Prompt构建 |
| 输出层 | 自然表达、安全预警 | TTS, 推送通知, 异常检测, 预警管理 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动后端
cd server && uvicorn main:app --host 0.0.0.0 --port 8765

# 注册家庭成员
python scripts/enroll_member.py --name "爷爷" --role elder \
  --audio-dir ./samples/grandpa_audio/ \
  --photo-dir ./samples/grandpa_photos/

# 模拟对话测试
python scripts/simulate_conversation.py --text --person-id grandpa

# 运行测试
pytest tests/ -v
```

## 技术栈

- **后端**: FastAPI + WebSocket
- **声纹识别**: SpeechBrain ECAPA-TDNN
- **人脸识别**: InsightFace buffalo_l
- **ASR**: FunASR Paraformer / Whisper
- **LLM**: 本地 Qwen3.5 (via SGLang)
- **TTS**: Edge-TTS
- **存储**: SQLite + ChromaDB
