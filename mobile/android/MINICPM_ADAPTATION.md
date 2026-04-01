# Android 端 MiniCPM-o 4.5 适配指南

## 背景

后端已完成 MiniCPM-o 4.5 全模态引擎集成，核心变化：
- **端到端语音对话**: 音频直接送入 MiniCPM-o，一次返回文本+语音（不再走 ASR→LLM→TTS 三步）
- **TTS 输出格式**: 从 Edge-TTS 的 MP3 变为 MiniCPM-o CosyVoice2 的 **WAV 24kHz**
- **思考模式**: 复杂问题（"怎么办"/"为什么"等）会启用 `enable_thinking`，响应时间更长
- **情绪检测**: 情绪标签不变（neutral/happy/concerned/tired/curious/slightly_annoyed）

## 当前兼容性分析

| 方面 | 状态 | 说明 |
|------|------|------|
| 音频采集格式 | ✅ 无需改 | 16kHz PCM 16-bit mono，与 MiniCPM-o 输入一致 |
| WebSocket 协议 | ✅ 无需改 | 二进制消息头格式不变 (1byte type + payload) |
| JSON 消息格式 | ✅ 基本兼容 | `reply`/`notification`/`alert` 等类型不变 |
| 视频帧发送 | ✅ 无需改 | JPEG 640x480 @ 2fps 不变 |
| 注册流程 | ✅ 无需改 | 声纹/人脸/档案流程不变 |

## 需要适配的改动

### 1. TTS 音频格式适配 (必须)

**问题**: MiniCPM-o TTS 输出为 **WAV 格式 (24kHz)**，当前 `AudioPlayer.kt` 用 `.mp3` 临时文件。

**改动文件**: `AudioPlayer.kt`

```kotlin
// 改动点: 临时文件后缀从 .mp3 改为自适应
// Android MediaPlayer 可以自动处理 WAV 和 MP3，但文件后缀需要正确

// 当前代码:
val tempFile = File.createTempFile("tts_", ".mp3", cacheDir)

// 改为:
val tempFile = File.createTempFile("tts_", ".wav", cacheDir)
// 注意: MediaPlayer 对 WAV 和 MP3 都支持，但 WAV 后缀更通用
// 如果后端同时可能返回 MP3 (Edge-TTS fallback)，可以用无后缀:
// val tempFile = File.createTempFile("tts_", ".audio", cacheDir)
```

**验证方法**: 播放 24kHz WAV 音频，确认 MediaPlayer 正常播放不报错。

### 2. 思考模式等待指示 (推荐)

**问题**: 复杂问题启用思考模式后，响应时间可能从 1s 增加到 5-15s，用户需要知道机器人在"思考"。

**改动文件**: `MainActivity.kt`

**方案 A (简单)**: 在发送音频后显示"思考中..."状态
```kotlin
// 在 onAudioData 回调发送音频后:
runOnUiThread { statusText.text = "天天正在思考..." }

// 在收到 reply 后恢复:
runOnUiThread { statusText.text = "" }
```

**方案 B (后端配合)**: 后端新增 `thinking_start`/`thinking_end` JSON 消息
```kotlin
// ws_handler.py 中可以在调用 MiniCPM-o 前后发送:
// await manager.send_json_message(client_id, {"type": "thinking_start"})
// ... MiniCPM-o 推理 ...
// await manager.send_json_message(client_id, {"type": "thinking_end"})

// Android 端处理:
"thinking_start" -> runOnUiThread { showThinkingIndicator() }
"thinking_end" -> runOnUiThread { hideThinkingIndicator() }
```

### 3. `stop_tts` 消息处理 (推荐)

**问题**: 后端在检测到新语音输入时会发送 `{"type": "stop_tts"}`，用于中断当前播放。

**改动文件**: `MainActivity.kt` + `AudioPlayer.kt`

```kotlin
// MainActivity.kt 中处理 stop_tts:
"stop_tts" -> audioPlayer.stopAndClear()

// AudioPlayer.kt 新增方法:
fun stopAndClear() {
    mediaPlayer?.stop()
    mediaPlayer?.release()
    mediaPlayer = null
    playbackQueue.clear()
    // 清除所有临时文件
}
```

### 4. 连接设置中 bot_id 配置 (可选)

**问题**: 当前 bot_id 硬编码为路径的一部分，多 Bot 实例需要可配置。

**改动文件**: `MainActivity.kt`

```kotlin
// 当前 WebSocket URL:
"ws://$serverIp:8765/ws/$clientId"

// 改为支持 bot_id:
val botId = botIdInput.text.toString().ifEmpty { "default" }
"ws://$serverIp:8765/ws/$botId/$clientId"
```

UI 中新增一个 bot_id 输入框（默认值 "default"）。

### 5. 新增 reply_done 消息处理 (推荐)

**问题**: 后端在回复完成（文本+音频都发送完）后发送 `{"type": "reply_done"}`。

**改动文件**: `MainActivity.kt`

```kotlin
"reply_done" -> runOnUiThread {
    // 恢复 UI 状态（清除"思考中"指示等）
    hideThinkingIndicator()
}
```

### 6. 新增消息类型处理 (必须)

**问题**: 后端新增了两种 JSON 消息类型。

**改动文件**: `MainActivity.kt`

#### `state_change` — 交互状态变更
后端在对话流程的不同阶段发送状态通知:
```json
{"type": "state_change", "state": "listening"}     // 检测到语音
{"type": "state_change", "state": "processing"}    // 正在处理/思考
{"type": "state_change", "state": "responding"}    // 生成回复
{"type": "state_change", "state": "speaking"}      // 播放语音
{"type": "state_change", "state": "idle"}          // 空闲
```

```kotlin
"state_change" -> {
    val state = json.get("state")?.asString ?: "idle"
    runOnUiThread {
        stateIndicator.text = when(state) {
            "listening" -> "聆听中..."
            "processing" -> "思考中..."
            "responding" -> "回复中..."
            "speaking" -> "说话中..."
            else -> ""
        }
    }
}
```

#### `proactive` — 主动关怀消息
机器人主动发起的消息 (定时问候、用药提醒等):
```json
{"type": "proactive", "person_id": "妈妈", "text": "妈妈早上好！", "action_type": "greeting"}
```

```kotlin
"proactive" -> {
    val text = json.get("text")?.asString ?: ""
    val actionType = json.get("action_type")?.asString ?: ""
    runOnUiThread { addMessage("天天", text, "proactive: $actionType") }
}
```

## 不需要改动的部分

- `AudioCaptureService.kt` — 16kHz PCM 采集不变
- `CameraManager.kt` — 视频帧采集不变
- `SmsNotifier.kt` — 短信发送不变
- `EnrollActivity.kt` — 注册流程不变
- WebSocket 二进制消息协议 — 不变

## 优先级排序

| 优先级 | 改动 | 工作量 |
|--------|------|--------|
| P0 必须 | TTS 音频格式 WAV 适配 | 1 行 |
| P0 必须 | state_change 消息处理 | 15 行 |
| P0 必须 | proactive 消息处理 | 10 行 |
| P1 推荐 | stop_tts 处理 | 10 行 |
| P1 推荐 | reply_done 处理 | 5 行 |
| P2 可选 | bot_id 可配置 | 10 行 |

## 测试要点

1. 连接 `ws://192.168.0.127:8765/ws/default/android_client_01`
2. 说话后验证：文本回复 + WAV 音频播放正常
3. 长问题（"我膝盖疼怎么办"）验证思考等待体验
4. 连续说话验证 stop_tts 中断播放
5. 注册新成员验证流程完整
