package com.companionbot

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.IBinder
import android.util.Log
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.companionbot.databinding.ActivityMainBinding
import com.google.gson.JsonObject
import kotlinx.coroutines.*

/**
 * CompanionBot 主界面
 *
 * 功能:
 * - 连接/断开 DGX Spark 后端
 * - 开始/停止音频采集和摄像头
 * - 显示机器人回复和状态
 * - 文本对话测试模式
 * - 接收并执行短信通知指令
 */
class MainActivity : AppCompatActivity(), WebSocketClient.WebSocketListener {
    companion object {
        private const val TAG = "CompanionBot"
        private const val PERMISSION_REQUEST_CODE = 1001
        private const val DEFAULT_SERVER = "ws://192.168.1.100:8765"
        private const val CLIENT_ID = "android_client_01"
    }

    private lateinit var binding: ActivityMainBinding
    private lateinit var wsClient: WebSocketClient
    private lateinit var audioPlayer: AudioPlayer
    private lateinit var smsNotifier: SmsNotifier
    private var cameraManager: CameraManager? = null

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var isListening = false

    private val requiredPermissions = arrayOf(
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.CAMERA,
        Manifest.permission.SEND_SMS,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        wsClient = WebSocketClient(this)
        audioPlayer = AudioPlayer()
        smsNotifier = SmsNotifier(this)

        setupUI()
        checkPermissions()
    }

    private fun setupUI() {
        binding.etServerUrl.setText(DEFAULT_SERVER)

        binding.btnConnect.setOnClickListener {
            if (wsClient.isConnected) {
                disconnect()
            } else {
                connect()
            }
        }

        binding.btnListen.setOnClickListener {
            if (isListening) {
                stopListening()
            } else {
                startListening()
            }
        }

        binding.btnSendText.setOnClickListener {
            val text = binding.etTextInput.text.toString().trim()
            if (text.isNotEmpty() && wsClient.isConnected) {
                wsClient.sendTextInput("android_user", text)
                appendChat("我: $text")
                binding.etTextInput.text?.clear()
            }
        }

        updateUI()
    }

    private fun connect() {
        val url = binding.etServerUrl.text.toString().trim()
        if (url.isEmpty()) {
            Toast.makeText(this, "请输入服务器地址", Toast.LENGTH_SHORT).show()
            return
        }
        appendStatus("连接到 $url ...")
        wsClient.connect(url, CLIENT_ID)
    }

    private fun disconnect() {
        stopListening()
        wsClient.disconnect()
        updateUI()
        appendStatus("已断开连接")
    }

    private fun startListening() {
        if (!wsClient.isConnected) {
            Toast.makeText(this, "请先连接服务器", Toast.LENGTH_SHORT).show()
            return
        }

        isListening = true

        // 启动音频采集前台服务
        val intent = Intent(this, AudioCaptureService::class.java)
        ContextCompat.startForegroundService(this, intent)

        // 启动摄像头
        cameraManager = CameraManager(this).apply {
            onFrameCapture = { jpegData ->
                wsClient.sendVideoFrame(jpegData)
            }
            startCapture()
        }

        updateUI()
        appendStatus("开始监听 (音频 + 视频)")
    }

    private fun stopListening() {
        isListening = false
        stopService(Intent(this, AudioCaptureService::class.java))
        cameraManager?.stopCapture()
        cameraManager = null
        updateUI()
        appendStatus("停止监听")
    }

    private fun updateUI() {
        runOnUiThread {
            binding.btnConnect.text = if (wsClient.isConnected) "断开" else "连接"
            binding.btnListen.text = if (isListening) "停止监听" else "开始监听"
            binding.btnListen.isEnabled = wsClient.isConnected
            binding.btnSendText.isEnabled = wsClient.isConnected
            binding.tvStatus.text = when {
                isListening -> "状态: 监听中"
                wsClient.isConnected -> "状态: 已连接"
                else -> "状态: 未连接"
            }
        }
    }

    private fun appendChat(text: String) {
        runOnUiThread {
            binding.tvChatLog.append("$text\n")
            binding.scrollView.fullScroll(android.view.View.FOCUS_DOWN)
        }
    }

    private fun appendStatus(text: String) {
        Log.i(TAG, text)
        runOnUiThread {
            binding.tvStatusLog.append("$text\n")
        }
    }

    // WebSocket callbacks

    override fun onConnected() {
        updateUI()
        appendStatus("WebSocket 已连接")
    }

    override fun onDisconnected(reason: String) {
        updateUI()
        appendStatus("WebSocket 断开: $reason")
    }

    override fun onJsonMessage(json: JsonObject) {
        val type = json.get("type")?.asString ?: return

        when (type) {
            "reply" -> {
                val text = json.get("text")?.asString ?: ""
                val emotion = json.get("emotion")?.asString ?: "neutral"
                appendChat("小伴 [$emotion]: $text")
            }

            "notification" -> {
                val action = json.get("action")?.asString
                if (action == "send_sms") {
                    val phone = json.get("phone")?.asString ?: return
                    val message = json.get("message")?.asString ?: return
                    val success = smsNotifier.sendSms(phone, message)
                    appendStatus("短信${if (success) "已发送" else "发送失败"}: $phone")
                }
            }

            "alert" -> {
                val severity = json.get("severity")?.asString ?: ""
                val message = json.get("message")?.asString ?: ""
                appendStatus("[警报 $severity] $message")
                if (json.get("action")?.asString == "play_alarm") {
                    // TODO: 播放报警铃声
                }
            }
        }
    }

    override fun onBinaryMessage(type: Byte, data: ByteArray) {
        if (type == WebSocketClient.MSG_TYPE_TTS) {
            audioPlayer.enqueue(data)
        }
    }

    override fun onError(message: String) {
        appendStatus("错误: $message")
        updateUI()
    }

    // Permissions

    private fun checkPermissions() {
        val missing = requiredPermissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), PERMISSION_REQUEST_CODE)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE) {
            val denied = permissions.zip(grantResults.toList())
                .filter { it.second != PackageManager.PERMISSION_GRANTED }
                .map { it.first }
            if (denied.isNotEmpty()) {
                appendStatus("部分权限被拒绝: ${denied.joinToString()}")
            }
        }
    }

    override fun onDestroy() {
        disconnect()
        audioPlayer.release()
        scope.cancel()
        super.onDestroy()
    }
}
