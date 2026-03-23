# CompanionBot — 家庭陪伴机器人大脑系统

## 项目概述

CompanionBot 是一个家庭陪伴机器人的"大脑"软件系统，面向老人看护和小孩陪伴场景。系统以"像人"为核心设计原则——不追求完美助手，而是构建一个有记忆、有性格、懂得察言观色的家庭成员。

### 原型架构

```
┌─────────────────────┐         WebSocket / HTTP          ┌──────────────────────────┐
│       手机端          │  ◄──────────────────────────►    │     DGX Spark 后端        │
│  (眼睛/耳朵/嘴巴)     │    音频流 / 视频帧 / TTS音频      │   (大脑 / 算力中心)        │
│                     │                                   │                          │
│  - 麦克风采集音频      │                                   │  - 声纹识别 (SpeechBrain)  │
│  - 摄像头采集视频帧    │                                   │  - 人脸识别 (InsightFace)  │
│  - 扬声器播放回复      │                                   │  - LLM 推理               │
│  - 简单状态显示        │                                   │  - 记忆系统               │
│                     │                                   │  - 人格引擎               │
└─────────────────────┘                                   └──────────────────────────┘
```

### 硬件环境

- **算力后端**: NVIDIA DGX Spark, Ubuntu + CUDA 已就绪
- **感知终端**: 手机（麦克风 + 摄像头 + 扬声器），通过局域网连接 DGX Spark
- **存储**: DGX Spark 本地 SSD

---

## 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| 后端框架 | FastAPI + WebSocket | 手机端和后端的实时通信 |
| 语音活动检测 (VAD) | Silero VAD | 轻量，支持流式，PyTorch 原生 |
| 声纹识别 | SpeechBrain ECAPA-TDNN | 预训练模型，few-shot 注册，API 简洁 |
| 声纹识别(备选) | 3D-Speaker CAM++ | 阿里达摩院，20万中文说话人训练，中文场景更优 |
| 人脸识别 | InsightFace (buffalo_l) | 开源，精度高，支持增量注册 |
| 语音识别 (ASR) | FunASR (Paraformer) 或 Whisper | 语音转文字，FunASR 中文更优 |
| LLM 推理 | 本地: Qwen3.5 / 云端: Kimi K2.5 API | 本地优先，复杂任务 fallback 云端 |
| 语音合成 (TTS) | CosyVoice 或 Edge-TTS | 自然度高，支持情感语音 |
| 记忆存储 | SQLite + ChromaDB | 结构化数据 + 向量检索 |
| 推送通知 | 手机原生短信 API | 紧急通知通过手机直接发送短信 |
| 手机端 | Android 原生 (Kotlin) + iOS 原生 (Swift) | 深度硬件访问，后台保活 |

---

## 系统架构 — 四层设计

### Layer 1: 感知层 (Perception)

负责回答"谁在说什么，在做什么"。

#### 1.1 音频感知管线

```
手机麦克风 → WebSocket音频流 → VAD检测 → 语音分段
                                            ├→ 声纹识别 → person_id
                                            └→ ASR转写 → text
```

**VAD (Silero VAD)**
- 输入: 16kHz 单声道 PCM 音频流
- 输出: 语音段起止时间戳
- 作用: 过滤静音，触发下游处理，节省算力
- 配置: 阈值 0.5，最小语音段 250ms，最小静音段 100ms

**声纹识别 (SpeechBrain ECAPA-TDNN)**
- 输入: VAD 切出的语音段 (1~3秒)
- 输出: 192 维 speaker embedding
- 匹配: 与已注册声纹做余弦相似度，阈值 0.25，返回 person_id 或 "unknown"
- 注册: 每个家庭成员录制 3~5 段自然对话(每段5~10秒)，取 embedding 均值
- 更新: 识别置信度 > 0.8 时，用当前 embedding 加权更新存储模板 (α=0.05)

**ASR 语音转文字**
- 推荐 FunASR Paraformer (中文场景) 或 Whisper large-v3
- 输入: 语音段
- 输出: 带时间戳的文字

#### 1.2 视觉感知管线

```
手机摄像头 → WebSocket视频帧(JPEG, 2~5fps) → 人脸检测 → 人脸识别 → person_id
```

**人脸识别 (InsightFace)**
- 模型: buffalo_l (检测 + 识别一体)
- 输入: JPEG 帧
- 输出: 人脸 bounding box + 512 维 face embedding
- 匹配: 余弦相似度，阈值 0.4
- 注册: 每人采集 5~10 张不同角度/光照的照片

#### 1.3 身份融合

当声纹和人脸同时有结果时，进行多模态融合:
```python
def fuse_identity(voice_id, voice_score, face_id, face_score):
    if voice_id == face_id:
        return voice_id, max(voice_score, face_score)  # 一致，增强置信度
    elif face_score > voice_score:
        return face_id, face_score  # 人脸通常更可靠
    else:
        return voice_id, voice_score
```

---

### Layer 2: 记忆层 (Memory)

核心目标: 让机器人"记住"家庭成员的一切，并能在对话中自然运用。

#### 2.1 四层记忆架构

```
┌─────────────────────────────────────────────────────────┐
│  工作记忆 (Working Memory)                                │
│  - 当前对话的上下文窗口，纯 in-memory                       │
│  - 最近 20 轮对话 + 当前说话人信息                           │
│  - 每次对话开始时从长期记忆加载相关上下文                      │
└──────────────────────┬──────────────────────────────────┘
                       │ 对话结束后，memory_consolidation 决定沉淀什么
                       ▼
┌─────────────────────────────────────────────────────────┐
│  情景记忆 (Episodic Memory)                    SQLite     │
│  - 关键事件的结构化摘要                                     │
│  - 字段: event_id, person_id, timestamp, summary,        │
│          emotion_tag, importance_score                    │
│  - 示例: "2025-03-20 爷爷说膝盖又疼了，情绪有点低落"          │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  语义记忆 (Semantic Memory)                   ChromaDB    │
│  - 对话内容的向量化索引，用于 RAG 检索                       │
│  - 每次对话结束后，将对话摘要 embedding 化并存入              │
│  - 检索时取 top-5 相关记忆，注入 LLM prompt                 │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  长期档案 (Long-term Profile)                  SQLite     │
│  - 每个家庭成员的持久化画像                                  │
│  - 基础信息: 姓名、称呼、年龄、关系                          │
│  - 兴趣爱好: ["下棋", "听戏曲", "种花"]                     │
│  - 健康状况: ["高血压", "膝盖不好"]                          │
│  - 沟通偏好: {"语速": "慢", "话题": "怀旧", "忌讳": "..."}  │
│  - 近期关注: ["孙子要高考了", "老伴住院"]                     │
└─────────────────────────────────────────────────────────┘
```

#### 2.2 记忆沉淀流程 (Memory Consolidation)

每次对话结束后自动触发:
```
1. LLM 总结本次对话要点 → 生成 event summary
2. LLM 评估 importance_score (0~1)
   - 健康相关 → 高分 (0.8+)
   - 情绪变化 → 中高分 (0.6+)
   - 日常闲聊 → 低分 (0.2~0.4)
3. importance_score > 0.3 的事件写入情景记忆
4. 对话摘要向量化，写入语义记忆
5. 如果对话中发现了新的兴趣/健康信息，更新长期档案
```

#### 2.3 记忆检索流程

每次 LLM 生成回复前:
```
1. 根据当前 person_id 加载长期档案
2. 用当前对话内容在语义记忆中检索 top-5 相关历史
3. 加载该 person_id 最近 5 条情景记忆
4. 将以上信息组装成 system prompt 的一部分
```

---

### Layer 3: 人格与决策层 (Personality & Decision)

#### 3.1 人格引擎

**人格参数** (在配置文件中定义，可调整):
```yaml
personality:
  name: "小伴"           # 机器人的名字
  traits:
    warmth: 0.85         # 温暖程度 (0~1)
    humor: 0.6           # 幽默感
    patience: 0.9        # 耐心
    curiosity: 0.7       # 好奇心
    directness: 0.5      # 直率程度
    stubbornness: 0.3    # 固执程度 (偶尔坚持自己观点)
  quirks:                # 让机器人更像"人"的小特点
    - "对天气话题特别感兴趣"
    - "喜欢用比喻来解释事情"
    - "被打断时会有点小委屈但很快恢复"
    - "记性偶尔会'不太好'(故意设计的，让互动更自然)"
    - "对家人做的菜总是很感兴趣"
```

**情绪状态机**:
```
状态: neutral | happy | concerned | tired | curious | slightly_annoyed
转换规则:
  - 家人开心 → happy
  - 检测到健康问题 → concerned
  - 长时间无互动后被唤醒 → slightly groggy → neutral
  - 被频繁打断 → slightly_annoyed → neutral (几轮后自动恢复)
  - 听到有趣的事 → curious
```

情绪影响回复的:
- 语气词选择 (happy: "哈哈"、"太好了"; concerned: "嗯..."、"您注意...")
- 回复长度 (tired 时更简短)
- 话题倾向 (curious 时会追问)

#### 3.2 对话对象适配

根据 person_id 查询长期档案中的年龄/关系，自动调整:
```yaml
对老人:
  - 语速建议: 慢
  - 用词: 简单直白，避免网络用语
  - 话题: 健康关怀、回忆、家常
  - 称呼: 按注册的称呼 (爷爷/奶奶/王阿姨)

对小孩:
  - 语速建议: 正常
  - 用词: 活泼，可以用简单的比喻
  - 话题: 学习鼓励、兴趣引导、故事
  - 称呼: 按注册的称呼 (小明/宝贝)
```

#### 3.3 插话决策模块

当 VAD 检测到有人在对话(但不是在和机器人说话)时:

```python
class InterventionDecider:
    def should_intervene(self, context) -> (bool, str):
        """
        输入: 当前对话转写文本、说话人、历史上下文
        输出: (是否插话, 插话原因)

        决策维度:
        1. relevance_score    — 对话内容与机器人知识/记忆的相关度 (0~1)
        2. timing_score       — 是否处于自然停顿 (0~1)
        3. role_score         — 机器人插话的角色价值 (0~1)
           - 提供有用信息: 0.8
           - 安全预警: 1.0 (直接触发，不等待)
           - 表达关心: 0.5
           - 纯凑热闹: 0.1
        4. frequency_penalty  — 最近 5 分钟内已插话次数的惩罚

        综合分 = relevance * 0.3 + timing * 0.2 + role * 0.4 - frequency_penalty * 0.3
        阈值 = 0.6

        安全预警场景绕过所有评分，直接触发。
        """
```

**不插话的场景** (硬规则):
- 家人明显在打私人电话
- 最近 2 分钟内已经插过话且被忽略
- 对话内容与机器人完全无关 (比如讨论工作细节)

**应该插话的场景**:
- 听到"小伴"被提及 (唤醒词)
- 听到健康相关讨论且有有用信息可以提供
- 听到老人说感到不舒服
- 有人问了一个机器人知道答案的问题

---

### Layer 4: 输出层 (Output)

#### 4.1 语音合成 (TTS)

- 推荐: CosyVoice (阿里开源，支持情感控制) 或 Edge-TTS (微软，免费，质量好)
- 原型阶段先用 Edge-TTS，效果好且零部署成本
- 将 LLM 的文字回复转语音，通过 WebSocket 推送到手机播放
- 情绪状态影响 TTS 参数: happy → 语速略快、音调略高; concerned → 语速略慢

#### 4.2 推送通知系统

```
事件分级:
  P0 紧急 (生命安全): 手机直接发短信 + 拨打电话，立即触发，不限流
    - 跌倒检测
    - 呼救声检测
    - 长时间无活动 (可配置阈值，如白天 4 小时)

  P1 重要: 手机发短信通知，5 分钟内
    - 老人情绪持续低落
    - 错过用药时间
    - 异常生活规律

  P2/P3 一般: 聚合为每日摘要 (短信或 App 内消息)
    - 今天聊了什么
    - 心情怎么样
    - 活动量如何

通知实现 (原型阶段):
  后端 → WebSocket 指令 → 手机端原生 SMS API 发送短信
  Android: SmsManager.sendTextMessage() 直接发送，无需用户确认
  iOS: 受系统限制需用户确认，或通过 Shortcuts 自动化绕过

通道降级: 短信失败 → App 内推送通知 → 本地日志记录
防骚扰: P1+ 每小时最多 3 条，P0 不限
联系人权限: 不同角色收到不同级别通知
```

---

## 项目结构

```
companion-bot/
├── CLAUDE.md                          # 本文件 - 项目上下文
├── README.md                          # 项目说明
├── config/
│   ├── personality.yaml               # 人格配置
│   ├── family_members.yaml            # 家庭成员初始配置
│   └── notification_contacts.yaml     # 紧急联系人配置
├── server/                            # DGX Spark 后端
│   ├── main.py                        # FastAPI 入口
│   ├── ws_handler.py                  # WebSocket 处理 (音频/视频流)
│   ├── perception/                    # 感知层
│   │   ├── vad.py                     # Silero VAD 封装
│   │   ├── speaker_id.py             # 声纹识别 (SpeechBrain)
│   │   ├── face_id.py                # 人脸识别 (InsightFace)
│   │   ├── asr.py                    # 语音转文字
│   │   └── identity_fusion.py        # 多模态身份融合
│   ├── memory/                        # 记忆层
│   │   ├── working_memory.py          # 工作记忆 (in-memory)
│   │   ├── episodic_memory.py         # 情景记忆 (SQLite)
│   │   ├── semantic_memory.py         # 语义记忆 (ChromaDB)
│   │   ├── long_term_profile.py       # 长期档案 (SQLite)
│   │   └── consolidation.py           # 记忆沉淀
│   ├── personality/                   # 人格与决策层
│   │   ├── engine.py                  # 人格引擎 + 情绪状态机
│   │   ├── intervention.py            # 插话决策
│   │   ├── prompt_builder.py          # LLM Prompt 组装
│   │   └── llm_client.py             # LLM 调用 (本地/云端)
│   ├── output/                        # 输出层
│   │   ├── tts.py                     # 语音合成
│   │   └── notification.py            # 推送通知
│   ├── safety/                        # 安全模块
│   │   ├── anomaly_detector.py        # 异常行为检测
│   │   └── alert_manager.py           # 预警管理 + 通道降级
│   └── data/                          # 本地数据
│       ├── companion.db               # SQLite (情景记忆 + 长期档案)
│       ├── chroma/                    # ChromaDB 向量库
│       └── voiceprints/               # 已注册的声纹向量
├── mobile/
│   ├── android/                       # Android 原生 (Kotlin) — 优先开发
│   │   ├── app/src/main/java/.../
│   │   │   ├── AudioCaptureService.kt # 前台服务，持续采集麦克风音频
│   │   │   ├── CameraManager.kt      # 摄像头帧采集
│   │   │   ├── WebSocketClient.kt    # 与 DGX Spark 通信
│   │   │   ├── AudioPlayer.kt        # TTS 音频播放
│   │   │   ├── SmsNotifier.kt        # 调用 SmsManager 发送短信
│   │   │   └── MainActivity.kt       # 主界面 + 状态显示
│   │   └── ...
│   └── ios/                           # iOS 原生 (Swift) — Phase 2 跟进
│       ├── CompanionBot/
│       │   ├── AudioCaptureManager.swift
│       │   ├── CameraManager.swift
│       │   ├── WebSocketClient.swift
│       │   ├── SmsNotifier.swift      # MFMessageCompose / Shortcuts
│       │   └── ContentView.swift
│       └── ...
├── scripts/
│   ├── enroll_member.py               # 注册新家庭成员 (声纹+人脸)
│   ├── test_pipeline.py               # 端到端管线测试
│   └── simulate_conversation.py       # 用音频文件模拟对话
├── tests/
│   ├── test_speaker_id.py
│   ├── test_memory.py
│   ├── test_personality.py
│   └── test_intervention.py
└── requirements.txt
```

---

## 开发路线 — 四个 Phase

### Phase 1: 感知基座 (Week 1~2)
**目标**: 机器人能听到声音、看到人脸、知道"谁在说什么"

1. 搭建 FastAPI + WebSocket 服务器
2. 实现 VAD → 声纹识别 → ASR 音频管线
3. 实现人脸检测 → 人脸识别视频管线
4. 实现 `enroll_member.py` 注册脚本
5. 实现身份融合模块
6. 编写测试: 用预录音频/视频验证识别准确率

**Phase 1 验收标准**:
- 能通过 WebSocket 接收音频流，识别出说话人是谁
- 能通过 WebSocket 接收视频帧，识别出画面中是谁
- 注册一个新家庭成员只需 3~5 段语音 + 5~10 张照片
- 中文语音识别准确率 > 90%

### Phase 2: 记忆系统 (Week 2~3)
**目标**: 机器人能记住和家人的每一次互动

1. 实现 SQLite schema (情景记忆 + 长期档案)
2. 实现 ChromaDB 向量存储和检索
3. 实现工作记忆 (对话上下文管理)
4. 实现记忆沉淀流程 (consolidation)
5. 实现记忆检索 → prompt 注入
6. 编写测试: 多轮对话后验证记忆检索准确性

**Phase 2 验收标准**:
- 对话中提到的信息能在后续对话中被召回
- 家庭成员档案能随对话自动更新
- 向量检索能找到相关历史对话

### Phase 3: 人格系统 (Week 3~4)
**目标**: 机器人有自己的性格，不同情绪下表现不同

1. 实现人格引擎 (traits → prompt 调制)
2. 实现情绪状态机
3. 实现对话对象适配 (老人/小孩不同风格)
4. 实现 LLM prompt 组装 (记忆 + 人格 + 情绪 + 对象)
5. 接入 TTS 输出
6. 编写测试: 验证不同情绪下的回复风格差异

**Phase 3 验收标准**:
- 同一个问题，机器人 happy 和 concerned 状态下回复不同
- 对老人和小孩的回复风格明显不同
- 人格特质在多轮对话中保持一致

### Phase 4: 主动交互 + 安全 (Week 4~5)
**目标**: 机器人能主动开口、选择性插话、危险预警

1. 实现插话决策模块
2. 实现异常检测 (跌倒/呼救/长时间无活动)
3. 实现推送通知系统 (分级 + 通道降级)
4. 实现主动关怀逻辑 (定时问候、用药提醒)
5. 端到端集成测试
6. 手机端 App 开发 (Android 原生 Kotlin 优先，iOS Swift 跟进)

**Phase 4 验收标准**:
- 家人对话时，机器人只在合适时机插话
- 模拟危险场景时，紧急联系人能在 30 秒内收到通知
- 通知不会骚扰 (P1+ 限流生效)

---

## 关键设计决策

### LLM 选型建议

原型阶段推荐 **混合方案**:
- **日常对话**: 本地跑 Qwen3.5-27B (dense, 26.5B 参数，DGX Spark 算力充裕) 或 Qwen3.5-9B (轻量快速，适合低延迟场景)
  - Qwen3.5 系列于 2026 年 2 月发布，采用 Gated Delta Networks + 稀疏 MoE 架构
  - 原生多模态、262K 上下文、支持 201 种语言
  - 推荐用 SGLang 或 vLLM 部署本地推理服务
- **复杂推理/记忆沉淀**: 调用 Kimi K2.5 API (Moonshot AI)
  - OpenAI SDK 兼容，base_url: `https://api.moonshot.ai/v1`
  - 支持 Thinking 模式 (深度推理) 和 Instant 模式 (快速响应)
  - 定价: $0.60/百万输入 token, $2.50/百万输出 token，性价比极高
  - 记忆沉淀、事件总结等需要强推理能力的任务走 Kimi K2.5 Thinking 模式
- 接口统一为 `llm_client.py`，上层不感知具体模型

```python
# llm_client.py 核心接口设计
class LLMClient:
    def __init__(self, config):
        # 本地模型: Qwen3.5 via SGLang/vLLM (OpenAI 兼容接口)
        self.local_client = OpenAI(base_url="http://localhost:8000/v1", api_key="local")
        # 云端模型: Kimi K2.5 API
        self.cloud_client = OpenAI(base_url="https://api.moonshot.ai/v1", api_key=config.kimi_api_key)

    async def chat(self, messages, task_type="daily"):
        """根据任务类型自动路由到本地或云端"""
        if task_type in ("daily", "greeting", "chitchat"):
            return await self._local_inference(messages)  # Qwen3.5 本地
        elif task_type in ("consolidation", "summary", "complex_reasoning"):
            return await self._cloud_inference(messages, thinking=True)  # Kimi K2.5 Thinking
        else:
            return await self._local_inference(messages)  # 默认本地
```

### 中文优化

- 声纹识别如果 SpeechBrain 中文效果不佳，切换到 3D-Speaker CAM++
- ASR 优先选 FunASR Paraformer (中文专项优化)
- TTS 推荐 CosyVoice (中文自然度好) 或 Edge-TTS 中文语音

### 手机端架构 — 为什么用原生开发

选择 Android (Kotlin) + iOS (Swift) 原生开发而非跨平台框架，原因:
1. **深度硬件访问**: 麦克风流式采集 (AudioRecord/AVAudioEngine) 需要低延迟访问原始 PCM 数据
2. **后台保活**: 机器人需要持续监听环境音，Android 前台服务 + iOS 后台音频模式是必需的
3. **原生短信 API**: Android `SmsManager` 可以静默发送短信 (无需用户确认)，这对紧急通知至关重要
4. **摄像头实时帧**: 需要直接访问 Camera2 API / AVCaptureSession 获取低延迟视频帧
5. **音频播放控制**: TTS 回放需要精确控制音频焦点和输出设备

**开发顺序**: Android 先行 (权限更灵活，后台限制少，短信 API 更开放)，iOS 跟进。

**手机端核心职责** (纯感知终端，不做推理):
- 采集: 麦克风 16kHz PCM 流 + 摄像头 JPEG 帧 (2~5fps)
- 上传: 通过 WebSocket 实时推送到 DGX Spark 后端
- 播放: 接收后端返回的 TTS 音频并播放
- 通知: 接收后端的短信指令，调用系统 API 发送短信
- 显示: 简单 UI 显示当前状态 (谁在说话、机器人情绪、连接状态)

### 推送通知

原型阶段直接通过手机原生能力发短信，不依赖第三方云服务:
- **Android**: 通过 `SmsManager` API 直接发送短信，无需第三方服务
- **iOS**: 通过 `MFMessageComposeViewController` 或 Shortcuts 触发短信
- 后端向手机端发送 "请发送短信" 的 WebSocket 指令，手机端执行实际发送
- 后续量产可迁移到云端短信/语音 API (阿里云、腾讯云等)

### 隐私设计

- 所有音视频处理在 DGX Spark 本地完成，不上传云端
- 声纹和人脸 embedding 只存本地
- LLM 如果调用云端 API，不传递原始音视频，只传文字
- 推送通知只发送摘要文字，不含原始对话

---

## 依赖安装

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate

# 核心依赖
pip install fastapi uvicorn websockets
pip install torch torchaudio  # PyTorch (DGX Spark 应已有 CUDA 版本)
pip install speechbrain       # 声纹识别
pip install insightface onnxruntime-gpu  # 人脸识别
pip install chromadb           # 向量数据库
pip install silero-vad         # 或通过 torch.hub 加载
pip install edge-tts           # TTS (原型阶段)
pip install funasr             # 中文 ASR (可选，或用 whisper)
pip install openai-whisper     # Whisper ASR (可选)

# LLM 推理
pip install sglang[all]        # 本地部署 Qwen3.5 (推荐)
# 或
pip install vllm               # 备选本地推理引擎
pip install openai             # Kimi K2.5 云端 API (OpenAI 兼容)

# 工具依赖
pip install pyyaml numpy opencv-python pillow
```

---

## 快速验证命令

```bash
# 1. 启动后端服务
cd server && uvicorn main:app --host 0.0.0.0 --port 8765

# 2. 注册家庭成员
python scripts/enroll_member.py --name "爷爷" --role elder \
  --audio-dir ./samples/grandpa_audio/ \
  --photo-dir ./samples/grandpa_photos/

# 3. 模拟对话测试
python scripts/simulate_conversation.py --audio ./test_audio.wav

# 4. 运行测试
pytest tests/ -v
```
