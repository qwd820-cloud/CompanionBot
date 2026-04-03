package com.companionbot

import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.*

/**
 * 对话状态机 — 驱动 UI 和麦克风行为的单一真相来源
 */
enum class ConversationState {
    DISCONNECTED,   // 未连接
    CONNECTING,     // 正在连接（含自动重连）
    LISTENING,      // 麦克风活跃，发送音频
    PROCESSING,     // 收到 reply JSON，等待 TTS
    SPEAKING,       // TTS 播放中，麦克风暂停
    RESUMING        // TTS 结束后 300ms 缓冲期
}

/**
 * 管理对话状态转换、自动重连、麦克风静音协调
 *
 * 所有状态转换通过 mainHandler 确保在主线程执行。
 * UI 更新通过 onStateChanged 回调直接驱动。
 */
class ConversationStateManager(
    private val onMicMute: (Boolean) -> Unit,
    private val onReconnect: () -> Unit
) {
    companion object {
        private const val TAG = "CompanionBot.State"
        private const val RESUME_DELAY_MS = 300L
        private const val MAX_RECONNECT_DELAY_MS = 30_000L
    }

    var currentState = ConversationState.DISCONNECTED
        private set
    var currentEmotion = "neutral"
        private set
    var lastReply = ""
        private set
    var lastSpeaker = ""
        private set

    /** UI 更新回调 — 每次状态变化时调用（主线程） */
    var onStateChanged: ((ConversationState) -> Unit)? = null
    var onEmotionChanged: ((String) -> Unit)? = null
    var onReplyChanged: ((String) -> Unit)? = null

    private val mainHandler = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var resumeJob: Job? = null
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private var autoReconnectEnabled = true

    fun transitionTo(newState: ConversationState) {
        mainHandler.post {
            val old = currentState
            if (old == newState) return@post

            Log.i(TAG, "状态转换: $old → $newState")
            currentState = newState

            // 麦克风控制
            when (newState) {
                ConversationState.SPEAKING, ConversationState.RESUMING -> onMicMute(true)
                ConversationState.LISTENING -> onMicMute(false)
                else -> {}
            }

            // 通知 UI
            onStateChanged?.invoke(newState)
        }
    }

    fun onConnected() {
        reconnectAttempt = 0
        reconnectJob?.cancel()
        transitionTo(ConversationState.LISTENING)
    }

    fun onDisconnected() {
        resumeJob?.cancel()
        transitionTo(ConversationState.DISCONNECTED)
        if (autoReconnectEnabled) {
            scheduleReconnect()
        }
    }

    fun onReplyReceived(personId: String, text: String, emotion: String) {
        mainHandler.post {
            lastSpeaker = personId
            lastReply = text
            currentEmotion = emotion
            onEmotionChanged?.invoke(emotion)
            onReplyChanged?.invoke(text)

            // reply JSON 到达 → PROCESSING（等待 TTS 音频）
            if (currentState == ConversationState.LISTENING) {
                transitionTo(ConversationState.PROCESSING)
            }
        }
    }

    fun onTtsStarted() {
        transitionTo(ConversationState.SPEAKING)
    }

    fun onTtsFinished() {
        resumeJob?.cancel()
        // 直接恢复到 LISTENING，确保麦克风解除静音
        resumeJob = scope.launch {
            delay(RESUME_DELAY_MS)
            transitionTo(ConversationState.LISTENING)
            // 双重保险：直接调用解除静音
            onMicMute(false)
        }
    }

    fun setAutoReconnect(enabled: Boolean) {
        autoReconnectEnabled = enabled
        if (!enabled) {
            reconnectJob?.cancel()
        }
    }

    private fun scheduleReconnect() {
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val delayMs = minOf(1000L * (1L shl minOf(reconnectAttempt, 14)), MAX_RECONNECT_DELAY_MS)
            Log.i(TAG, "将在 ${delayMs}ms 后重连 (第 ${reconnectAttempt + 1} 次)")
            delay(delayMs)
            reconnectAttempt++
            transitionTo(ConversationState.CONNECTING)
            onReconnect()
        }
    }

    fun destroy() {
        resumeJob?.cancel()
        reconnectJob?.cancel()
        scope.cancel()
    }
}
