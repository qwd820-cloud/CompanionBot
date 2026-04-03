# Android 端适配指南 — MiniCPM-o 4.5 + 记忆增强 + 五态状态机

## 1. 背景

后端已完成以下重大升级：
- **MiniCPM-o 4.5 端到端语音对话**: 音频直接送入模型，一次返回文本+语音
- **TTS 输出格式变化**: MiniCPM-o CosyVoice2 输出 **WAV 24kHz**（不再是 Edge-TTS 的 MP3）
- **五态状态机**: idle → listening → processing → responding → speaking
- **主动关怀消息**: 定时问候、用药提醒、空闲关怀
- **思考模式**: 复杂问题响应时间可能 5-15s
- **记忆增强**: 习惯记忆、情感绑定、记忆剪枝

## 2. 当前 Android 代码与后端的差距分析

### 后端发送的消息类型 vs Android 处理情况

| 消息类型 | 描述 | Android 当前 | 需要 |
|---------|------|-------------|------|
| `reply` | 文本回复+情绪 | ✅ 已处理 | — |
| `notification` | 短信指令 | ✅ 已处理 | — |
| `alert` | 安全告警 | ✅ 已处理 | 补充 `play_alarm` |
| `enroll_result` | 注册结果 | ✅ 已处理 | — |
| **`stop_tts`** | 中断当前播放 | ❌ 未处理 | **P0** |
| **`state_change`** | 五态状态通知 | ❌ 未处理 | **P0** |
| **`reply_done`** | 回复完成信号 | ❌ 未处理 | **P1** |
| **`proactive`** | 主动关怀消息 | ❌ 未处理 | **P0** |
| `members_list` | 成员列表响应 | ❌ 未处理 | P2 |
| `member_detail` | 成员详情响应 | ❌ 未处理 | P2 |
| `member_updated` | 成员更新确认 | ❌ 未处理 | P2 |
| `member_deleted` | 成员删除确认 | ❌ 未处理 | P2 |

### 关键协议问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| WebSocket URL 缺少 bot_id | **严重** | 当前 `/ws/{client_id}`，应为 `/ws/{bot_id}/{client_id}` |
| TTS 临时文件后缀 .mp3 | **高** | 后端输出 WAV，需改为 `.wav` 或去后缀 |
| 无 stop_tts 支持 | **高** | 用户连续说话时旧回复会叠加播放 |

## 3. 改动清单 (按优先级排序)

---

### P0 — 必须改动 (功能破损)

#### 3.1 WebSocket URL 加入 bot_id

**文件**: `WebSocketClient.kt`  
**当前代码** (约第 42 行):
```kotlin
val url = "$serverUrl/ws/$clientId"
```

**改为**:
```kotlin
val url = "$serverUrl/ws/$botId/$clientId"
```

`botId` 从 `MainActivity` 传入，默认值 `"default"`。

**文件**: `MainActivity.kt`  
新增 bot_id 输入框或配置项:
```kotlin
// 连接设置区域新增
private val botId: String get() = botIdInput.text.toString().ifEmpty { "default" }

// 连接时传入
webSocketClient = WebSocketClient(serverUrl, botId, clientId, this)
```

**文件**: `WebSocketClient.kt` 构造函数更新:
```kotlin
class WebSocketClient(
    private val serverUrl: String,
    private val botId: String,      // 新增
    private val clientId: String,
    private val listener: Listener
)
```

#### 3.2 TTS 音频格式适配

**文件**: `AudioPlayer.kt`  
**当前代码** (约第 38 行):
```kotlin
val tempFile = File.createTempFile("tts_", ".mp3", cacheDir)
```

**改为**:
```kotlin
val tempFile = File.createTempFile("tts_", ".audio", cacheDir)
```

说明: 后端可能返回 WAV (MiniCPM-o) 或 MP3 (Edge-TTS fallback)。Android `MediaPlayer` 根据内容自动识别格式，不依赖文件后缀。用 `.audio` 通用后缀兼容两种格式。

#### 3.3 stop_tts 消息处理

**文件**: `AudioPlayer.kt` — 新增方法:
```kotlin
fun stopAndClear() {
    playbackQueue.clear()
    currentPlayer?.let {
        if (it.isPlaying) it.stop()
        it.release()
    }
    currentPlayer = null
    // 清理所有临时音频文件
    cacheDir?.listFiles { f -> f.name.startsWith("tts_") }?.forEach { it.delete() }
}
```

**文件**: `MainActivity.kt` — 消息分发中新增:
```kotlin
"stop_tts" -> {
    audioPlayer.stopAndClear()
}
```

#### 3.4 state_change 消息处理 (五态状态机)

**文件**: `MainActivity.kt`

后端在对话流程的不同阶段发送:
```json
{"type": "state_change", "state": "listening"}      // VAD 检测到语音
{"type": "state_change", "state": "processing"}     // 开始处理 (含思考)
{"type": "state_change", "state": "responding"}     // 生成回复
{"type": "state_change", "state": "speaking"}       // 发送语音
{"type": "state_change", "state": "idle"}           // 空闲
```

```kotlin
"state_change" -> {
    val state = json.get("state")?.asString ?: "idle"
    runOnUiThread {
        stateIndicator.text = when (state) {
            "listening" -> "🎤 聆听中..."
            "processing" -> "💭 思考中..."
            "responding" -> "✍️ 回复中..."
            "speaking" -> "🔊 说话中..."
            "idle" -> ""
            else -> ""
        }
        // 可选: 状态动画
        stateIndicator.visibility = if (state == "idle") View.GONE else View.VISIBLE
    }
}
```

**UI 布局**: 在对话区域上方添加 `stateIndicator` (TextView 或自定义动画 View)。

#### 3.5 proactive 主动关怀消息

**文件**: `MainActivity.kt`

后端主动发起的消息（非用户请求触发）:
```json
{
    "type": "proactive",
    "person_id": "妈妈",
    "text": "妈妈早上好！今天天气不错哦。",
    "action_type": "greeting"      // greeting / medication / idle_care / followup
}
```

```kotlin
"proactive" -> {
    val text = json.get("text")?.asString ?: return
    val actionType = json.get("action_type")?.asString ?: "care"
    runOnUiThread {
        // 显示为特殊消息 (与普通回复区分，可用不同气泡颜色)
        addProactiveMessage("天天", text, actionType)
    }
    // proactive 消息也会附带 TTS 音频 (后续收到的 binary MSG_TYPE_TTS)
}
```

`action_type` 可能的值:
| action_type | 场景 | UI 建议 |
|-------------|------|---------|
| `greeting` | 早安/晚安 | 绿色气泡 |
| `medication` | 用药提醒 | 橙色气泡+图标 |
| `idle_care` | 久未互动关怀 | 蓝色气泡 |
| `followup` | 延迟关心 (如昨天问膝盖) | 紫色气泡 |

---

### P1 — 推荐改动 (体验优化)

#### 3.6 reply_done 消息处理

```kotlin
"reply_done" -> {
    runOnUiThread {
        stateIndicator.visibility = View.GONE
    }
}
```

#### 3.7 alert 增强 — P0 报警动作

当前 alert 已处理，但后端对 P0 级别(跌倒/呼救)会附带 `"action": "play_alarm"`。

```kotlin
"alert" -> {
    val severity = json.get("severity")?.asString ?: "P3"
    val action = json.get("action")?.asString ?: ""
    val message = json.get("message")?.asString ?: ""

    runOnUiThread { showAlert(severity, message) }

    if (action == "play_alarm") {
        // 播放紧急报警音
        playAlarmSound()
    }
}
```

`playAlarmSound()`: 使用 Android `RingtoneManager.TYPE_ALARM` 或自定义报警音。

---

### P2 — 可选改动 (功能扩展)

#### 3.8 成员管理 UI

后端支持通过 WebSocket 管理成员:
- 请求 `{"type": "list_members"}` → 响应 `{"type": "members_list", "members": [...]}`
- 请求 `{"type": "update_member", "person_id": "...", ...}` → 响应 `{"type": "member_updated", ...}`

当前只在 `EnrollActivity` 中处理注册。可以新增一个成员管理页面。

#### 3.9 情绪显示增强

当前情绪映射 (已有):
```kotlin
fun emotionLabel(emotion: String): String = when (emotion) {
    "happy" -> "开心"
    "concerned" -> "担心"
    "curious" -> "好奇"
    "tired" -> "疲倦"
    "slightly_annoyed" -> "有点烦"
    else -> "平静"
}
```

建议: 配合不同背景色/表情图标显示。

---

## 4. 不需要改动的文件

| 文件 | 原因 |
|------|------|
| `AudioCaptureService.kt` | 16kHz PCM 采集格式不变 |
| `CameraManager.kt` | JPEG 640x480 @ 2fps 不变 |
| `SmsNotifier.kt` | 短信发送逻辑不变 |
| `EnrollActivity.kt` | 注册流程不变 |
| 二进制消息协议 | MSG_TYPE_AUDIO(1)/VIDEO(2)/TTS(4) 不变 |

## 5. 改动量估算

| 优先级 | 改动项 | 涉及文件 | 代码量 |
|--------|--------|---------|--------|
| P0 | WebSocket URL bot_id | WebSocketClient.kt + MainActivity.kt | ~15 行 |
| P0 | TTS 格式适配 | AudioPlayer.kt | 1 行 |
| P0 | stop_tts 处理 | AudioPlayer.kt + MainActivity.kt | ~15 行 |
| P0 | state_change 五态 | MainActivity.kt + 布局 XML | ~20 行 |
| P0 | proactive 消息 | MainActivity.kt | ~15 行 |
| P1 | reply_done | MainActivity.kt | 5 行 |
| P1 | alert play_alarm | MainActivity.kt | 10 行 |
| P2 | 成员管理 UI | 新 Activity | ~200 行 |

**P0 总计: ~66 行改动**，P1 总计: ~15 行。核心改动非常小。

## 6. 测试清单

### 基本连接
- [ ] 连接 `ws://192.168.0.127:8765/ws/default/android_client_01` 成功
- [ ] 断开后重连正常

### 语音对话
- [ ] 说话后收到 `state_change` 状态转换 (listening → processing → responding → speaking → idle)
- [ ] 收到文本回复 (`reply`) 显示正常
- [ ] 收到 WAV 音频正常播放 (24kHz)
- [ ] 连续说话时 `stop_tts` 中断旧播放

### 思考模式
- [ ] 说"我膝盖疼怎么办"等复杂问题，UI 显示"思考中..."
- [ ] 等待 5-15s 后正常收到回复

### 主动消息
- [ ] 长时间不说话后收到 `proactive` 关怀消息
- [ ] 主动消息附带 TTS 音频正常播放
- [ ] 主动消息与普通回复在 UI 上可区分

### 情绪
- [ ] 说健康相关内容，情绪显示"担心"
- [ ] 说开心内容，情绪显示"开心"

### 安全告警
- [ ] 模拟紧急场景，P0 告警触发报警音

### 注册
- [ ] 新成员注册 (声纹+人脸+档案) 流程完整

## 7. 后端 WebSocket 消息速查

### 客户端 → 后端 (发送)

| 类型 | 格式 | 说明 |
|------|------|------|
| 音频 | Binary: `0x01` + PCM bytes | 16kHz 16-bit mono |
| 视频 | Binary: `0x02` + JPEG bytes | 640x480 |
| 文本输入 | `{"type": "text_input", "text": "...", "person_id": "..."}` | 纯文本对话 |
| 注册语音 | `{"type": "enroll_voice", "person_id": "...", "audio_data": "base64..."}` | 声纹注册 |
| 注册人脸 | `{"type": "enroll_face", "person_id": "...", "image_data": "base64..."}` | 人脸注册 |
| 注册档案 | `{"type": "enroll_profile", "person_id": "...", "name": "...", ...}` | 档案注册 |
| 查询成员 | `{"type": "list_members"}` | 获取成员列表 |

### 后端 → 客户端 (接收)

| 类型 | 格式 | 触发条件 |
|------|------|---------|
| TTS 音频 | Binary: `0x04` + WAV/MP3 bytes | 每次回复后 |
| 文本回复 | `{"type": "reply", "text": "...", "emotion": "...", "person_id": "..."}` | 每次回复 |
| 状态变更 | `{"type": "state_change", "state": "listening\|processing\|responding\|speaking\|idle"}` | 对话流程中 |
| 停止播放 | `{"type": "stop_tts"}` | 检测到新语音输入 |
| 回复完成 | `{"type": "reply_done"}` | 文本+音频都发送完 |
| 主动消息 | `{"type": "proactive", "text": "...", "person_id": "...", "action_type": "..."}` | 定时/关怀 |
| 安全告警 | `{"type": "alert", "severity": "P0\|P1\|P2", "message": "...", "action": "play_alarm"}` | 异常检测 |
| 通知指令 | `{"type": "notification", "action": "send_sms", "phone": "...", "message": "..."}` | 紧急通知 |
| 注册结果 | `{"type": "enroll_result", "step": "...", "success": true\|false}` | 注册操作后 |
| 成员列表 | `{"type": "members_list", "members": [...]}` | 查询后 |
