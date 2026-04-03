package com.companionbot

import android.Manifest
import android.animation.ObjectAnimator
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.IBinder
import android.util.Log
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.companionbot.databinding.ActivityMainBinding
import com.google.gson.JsonObject
import kotlinx.coroutines.*

/**
 * CompanionBot 语音交互主界面
 *
 * 极简设计：打开即自动连接、自动监听。
 * Lottie 动画反映对话状态和情绪，底部显示回复文本。
 */
class MainActivity : AppCompatActivity(), WebSocketClient.WebSocketListener {
    companion object {
        private const val TAG = "CompanionBot"
        private const val PERMISSION_REQUEST_CODE = 1001
    }

    private lateinit var binding: ActivityMainBinding
    private lateinit var wsClient: WebSocketClient
    private lateinit var audioPlayer: AudioPlayer
    private lateinit var smsNotifier: SmsNotifier
    private lateinit var stateManager: ConversationStateManager
    private lateinit var prefs: SharedPreferences
    private var cameraManager: CameraFrameCapture? = null

    private var audioService: AudioCaptureService? = null
    private var serviceBound = false
    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val localBinder = binder as AudioCaptureService.LocalBinder
            audioService = localBinder.service
            audioService?.onAudioData = { pcmData ->
                wsClient.sendAudio(pcmData)
            }
            serviceBound = true
            Log.i(TAG, "AudioCaptureService 已绑定")
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            audioService = null
            serviceBound = false
        }
    }

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var isListening = false
    private var replyFadeJob: Job? = null
    private var currentEmotion = "neutral"

    private val settingsLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            applyPreferences()
        }
    }

    private val requiredPermissions = arrayOf(
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.CAMERA,
        Manifest.permission.SEND_SMS,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = getSharedPreferences(SettingsActivity.PREFS_NAME, Context.MODE_PRIVATE)

        stateManager = ConversationStateManager(
            onMicMute = { muted -> audioService?.isMuted = muted },
            onReconnect = { wsClient.reconnect() }
        )

        stateManager.onStateChanged = { state -> updateStateUI(state) }
        stateManager.onEmotionChanged = { emotion ->
            currentEmotion = emotion
            updateLottieForEmotion(emotion)
            binding.tvEmotion.text = emotionLabel(emotion)
            binding.tvEmotion.visibility = if (emotion != "neutral") View.VISIBLE else View.GONE
        }
        stateManager.onReplyChanged = { reply ->
            if (reply.isNotEmpty()) showReply(reply)
        }

        wsClient = WebSocketClient(this)
        audioPlayer = AudioPlayer()
        smsNotifier = SmsNotifier(this)

        audioPlayer.onPlaybackStarted = { stateManager.onTtsStarted() }
        audioPlayer.onPlaybackFinished = { stateManager.onTtsFinished() }

        setupUI()
        checkPermissionsAndStart()
    }

    private fun setupUI() {
        binding.btnSettings.setOnClickListener {
            settingsLauncher.launch(Intent(this, SettingsActivity::class.java))
        }

        val dotDrawable = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(ContextCompat.getColor(this@MainActivity, R.color.status_disconnected))
        }
        binding.statusDot.background = dotDrawable

        // 初始 Lottie 动画
        updateLottieAnimation(R.raw.anim_disconnected)

        applyPreferences()
    }

    private fun applyPreferences() {
        val showChat = prefs.getBoolean(SettingsActivity.KEY_SHOW_CHAT, false)
        binding.chatScrollView.visibility = if (showChat) View.VISIBLE else View.GONE
        binding.divider.visibility = if (showChat) View.VISIBLE else View.GONE
    }

    // ========== Lottie 动画控制 ==========

    private fun updateLottieAnimation(rawRes: Int) {
        binding.lottieView.setAnimation(rawRes)
        binding.lottieView.repeatCount = com.airbnb.lottie.LottieDrawable.INFINITE
        binding.lottieView.playAnimation()
    }

    private fun updateLottieForState(state: ConversationState) {
        val animRes = when (state) {
            ConversationState.DISCONNECTED -> R.raw.anim_disconnected
            ConversationState.CONNECTING -> R.raw.anim_thinking
            ConversationState.LISTENING -> emotionToAnim(currentEmotion)
            ConversationState.PROCESSING -> R.raw.anim_thinking
            ConversationState.SPEAKING -> R.raw.anim_speaking
            ConversationState.RESUMING -> R.raw.anim_speaking
        }
        updateLottieAnimation(animRes)
    }

    private fun updateLottieForEmotion(emotion: String) {
        // 只在 LISTENING 状态时根据情绪切换动画
        if (stateManager.currentState == ConversationState.LISTENING) {
            updateLottieAnimation(emotionToAnim(emotion))
        }
    }

    private fun emotionToAnim(emotion: String): Int = when (emotion) {
        "happy" -> R.raw.anim_happy
        "concerned" -> R.raw.anim_concerned
        else -> R.raw.anim_listening
    }

    // ========== UI 更新 ==========

    private fun updateStateUI(state: ConversationState) {
        updateLottieForState(state)

        binding.stateLabel.text = when (state) {
            ConversationState.DISCONNECTED -> "未连接"
            ConversationState.CONNECTING -> "正在连接..."
            ConversationState.LISTENING -> "正在聆听..."
            ConversationState.PROCESSING -> "正在思考..."
            ConversationState.SPEAKING -> "正在说话..."
            ConversationState.RESUMING -> "正在说话..."
        }

        val dotColor = when (state) {
            ConversationState.DISCONNECTED -> R.color.status_disconnected
            ConversationState.CONNECTING -> R.color.status_connecting
            else -> R.color.status_connected
        }
        (binding.statusDot.background as? GradientDrawable)?.setColor(
            ContextCompat.getColor(this, dotColor)
        )
    }

    private fun showReply(text: String) {
        replyFadeJob?.cancel()
        binding.tvLastReply.text = text
        binding.tvLastReply.alpha = 1f

        replyFadeJob = scope.launch {
            delay(5000)
            ObjectAnimator.ofFloat(binding.tvLastReply, "alpha", 1f, 0f).apply {
                duration = 1000
                start()
            }
        }
    }

    // ========== 连接和监听 ==========

    private fun autoConnect() {
        val url = prefs.getString(SettingsActivity.KEY_SERVER_URL, SettingsActivity.DEFAULT_SERVER)!!
        val botId = prefs.getString(SettingsActivity.KEY_BOT_ID, SettingsActivity.DEFAULT_BOT_ID)!!
        val clientId = prefs.getString(SettingsActivity.KEY_CLIENT_ID, SettingsActivity.DEFAULT_CLIENT_ID)!!

        stateManager.transitionTo(ConversationState.CONNECTING)
        wsClient.connect(url, botId, clientId)
    }

    private fun startListening() {
        if (isListening) return
        isListening = true

        val intent = Intent(this, AudioCaptureService::class.java)
        ContextCompat.startForegroundService(this, intent)
        bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)

        cameraManager = CameraFrameCapture(this).apply {
            onFrameCapture = { jpegData -> wsClient.sendVideoFrame(jpegData) }
            startCapture()
        }

        Log.i(TAG, "开始监听 (音频 + 视频)")
    }

    private fun stopListening() {
        if (!isListening) return
        isListening = false

        if (serviceBound) {
            audioService?.onAudioData = null
            unbindService(serviceConnection)
            serviceBound = false
        }
        stopService(Intent(this, AudioCaptureService::class.java))

        cameraManager?.stopCapture()
        cameraManager = null
        Log.i(TAG, "停止监听")
    }

    // ========== WebSocket callbacks ==========

    override fun onConnected() {
        runOnUiThread {
            // 连接后先进入 PROCESSING 等待服务端主动问候
            stateManager.transitionTo(ConversationState.PROCESSING)
            startListening()
        }
    }

    override fun onDisconnected(reason: String) {
        runOnUiThread {
            stopListening()
            stateManager.onDisconnected()
            Log.i(TAG, "WebSocket 断开: $reason")
        }
    }

    override fun onJsonMessage(json: JsonObject) {
        val type = json.get("type")?.asString ?: return

        when (type) {
            "reply" -> {
                val personId = json.get("person_id")?.asString ?: ""
                val text = json.get("text")?.asString ?: ""
                val emotion = json.get("emotion")?.asString ?: "neutral"
                audioService?.isMuted = true
                stateManager.onReplyReceived(personId, text, emotion)
                runOnUiThread { appendChat("天天: $text") }
            }

            "thinking" -> {
                stateManager.transitionTo(ConversationState.PROCESSING)
            }

            "stop_tts" -> {
                // 打断 TTS — 清除播放回调防止竞争条件
                audioPlayer.onPlaybackStarted = null
                audioPlayer.onPlaybackFinished = null
                audioPlayer.stop()
                audioService?.isMuted = false
                stateManager.transitionTo(ConversationState.LISTENING)
                // 重新绑定回调
                audioPlayer.onPlaybackStarted = { stateManager.onTtsStarted() }
                audioPlayer.onPlaybackFinished = { stateManager.onTtsFinished() }
            }

            "notification" -> {
                val action = json.get("action")?.asString
                if (action == "send_sms") {
                    val phone = json.get("phone")?.asString ?: return
                    val message = json.get("message")?.asString ?: return
                    val success = smsNotifier.sendSms(phone, message)
                    Log.i(TAG, "短信${if (success) "已发送" else "发送失败"}: $phone")
                }
            }

            "alert" -> {
                val message = json.get("message")?.asString ?: ""
                runOnUiThread { appendChat("[警报] $message") }
            }

            "enroll_result" -> {
                val success = json.get("success")?.asBoolean ?: false
                val message = json.get("message")?.asString ?: ""
                Log.i(TAG, "注册: ${if (success) "成功" else "失败"} - $message")
            }
        }
    }

    override fun onBinaryMessage(type: Byte, data: ByteArray) {
        if (type == WebSocketClient.MSG_TYPE_TTS) {
            audioService?.isMuted = true
            audioPlayer.enqueue(data)
        }
    }

    override fun onError(message: String) {
        runOnUiThread {
            Log.e(TAG, "WS 错误: $message")
            stopListening()
            stateManager.onDisconnected()
        }
    }

    // ========== Helpers ==========

    private fun appendChat(text: String) {
        binding.tvChatLog.append("$text\n")
        binding.chatScrollView.fullScroll(View.FOCUS_DOWN)
    }

    private fun emotionLabel(emotion: String): String = when (emotion) {
        "happy" -> "开心"
        "concerned" -> "关心"
        "curious" -> "好奇"
        "tired" -> "有点累"
        "slightly_annoyed" -> "小委屈"
        "neutral" -> ""
        else -> emotion
    }

    // ========== Permissions ==========

    private fun checkPermissionsAndStart() {
        val missing = requiredPermissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), PERMISSION_REQUEST_CODE)
        } else {
            autoConnect()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE) {
            val allGranted = grantResults.all { it == PackageManager.PERMISSION_GRANTED }
            if (allGranted) {
                autoConnect()
            } else {
                val audioGranted = ContextCompat.checkSelfPermission(
                    this, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED
                if (audioGranted) {
                    autoConnect()
                } else {
                    Toast.makeText(this, "需要麦克风权限才能使用", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    override fun onPause() {
        super.onPause()
        // 切到其他页面时停止发送音频（注册/设置等）
        audioService?.isMuted = true
    }

    override fun onResume() {
        super.onResume()
        // 回到主界面恢复音频发送
        if (isListening && stateManager.currentState != ConversationState.SPEAKING) {
            audioService?.isMuted = false
        }
    }

    override fun onDestroy() {
        stateManager.setAutoReconnect(false)
        stopListening()
        wsClient.disconnect()
        audioPlayer.release()
        stateManager.destroy()
        scope.cancel()
        super.onDestroy()
    }
}
