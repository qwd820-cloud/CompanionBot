package com.companionbot

import android.util.Base64
import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.*
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit

/**
 * WebSocket 客户端 — 与 DGX Spark 后端实时通信
 *
 * 消息协议:
 * - 二进制: [1字节类型] + [payload] (音频=1, 视频=2, TTS=4)
 * - 文本: JSON 消息 (控制指令、回复、注册等)
 */
class WebSocketClient(
    private val listener: WebSocketListener
) {
    companion object {
        private const val TAG = "CompanionBot.WS"
        const val MSG_TYPE_AUDIO: Byte = 1
        const val MSG_TYPE_VIDEO: Byte = 2
        const val MSG_TYPE_TTS: Byte = 4
    }

    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private val gson = Gson()

    var isConnected = false
        private set

    fun connect(serverUrl: String, clientId: String) {
        val url = "$serverUrl/ws/$clientId"
        Log.i(TAG, "连接到 $url")

        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(request, object : okhttp3.WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                isConnected = true
                Log.i(TAG, "WebSocket 已连接")
                listener.onConnected()
            }

            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    val json = gson.fromJson(text, JsonObject::class.java)
                    listener.onJsonMessage(json)
                } catch (e: Exception) {
                    Log.e(TAG, "JSON 解析失败: $text", e)
                }
            }

            override fun onMessage(ws: WebSocket, bytes: ByteString) {
                if (bytes.size < 1) return
                val msgType = bytes[0]
                val payload = bytes.substring(1).toByteArray()
                listener.onBinaryMessage(msgType, payload)
            }

            override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                isConnected = false
                ws.close(1000, null)
                listener.onDisconnected(reason)
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                isConnected = false
                Log.e(TAG, "WebSocket 错误", t)
                listener.onError(t.message ?: "未知错误")
            }
        })
    }

    fun sendAudio(pcmData: ByteArray) {
        if (!isConnected) return
        val buffer = ByteArray(1 + pcmData.size)
        buffer[0] = MSG_TYPE_AUDIO
        System.arraycopy(pcmData, 0, buffer, 1, pcmData.size)
        webSocket?.send(buffer.toByteString())
    }

    fun sendVideoFrame(jpegData: ByteArray) {
        if (!isConnected) return
        val buffer = ByteArray(1 + jpegData.size)
        buffer[0] = MSG_TYPE_VIDEO
        System.arraycopy(jpegData, 0, buffer, 1, jpegData.size)
        webSocket?.send(buffer.toByteString())
    }

    fun sendTextInput(personId: String, text: String) {
        if (!isConnected) return
        val msg = JsonObject().apply {
            addProperty("type", "text_input")
            addProperty("person_id", personId)
            addProperty("text", text)
        }
        webSocket?.send(gson.toJson(msg))
    }

    // ============= 注册相关 =============

    /**
     * 发送声纹注册请求。
     * 将 PCM 音频以 Base64 编码通过 JSON 发送到后端。
     * @param personId 成员 ID
     * @param audioSamples 多段 PCM 音频字节
     */
    fun sendEnrollVoice(personId: String, audioSamples: List<ByteArray>) {
        if (!isConnected) return
        val samplesBase64 = audioSamples.map { Base64.encodeToString(it, Base64.NO_WRAP) }
        val msg = JsonObject().apply {
            addProperty("type", "enroll_voice")
            addProperty("person_id", personId)
            addProperty("audio_count", audioSamples.size)
            add("audio_samples", gson.toJsonTree(samplesBase64))
        }
        webSocket?.send(gson.toJson(msg))
    }

    /**
     * 发送人脸注册请求。
     * 将 JPEG 照片以 Base64 编码通过 JSON 发送到后端。
     * @param personId 成员 ID
     * @param photos 多张 JPEG 字节
     */
    fun sendEnrollFace(personId: String, photos: List<ByteArray>) {
        if (!isConnected) return
        val photosBase64 = photos.map { Base64.encodeToString(it, Base64.NO_WRAP) }
        val msg = JsonObject().apply {
            addProperty("type", "enroll_face")
            addProperty("person_id", personId)
            addProperty("photo_count", photos.size)
            add("photos", gson.toJsonTree(photosBase64))
        }
        webSocket?.send(gson.toJson(msg))
    }

    /**
     * 发送成员档案信息。
     */
    fun sendEnrollProfile(
        personId: String,
        name: String,
        nickname: String,
        role: String,
        age: Int,
        relationship: String
    ) {
        if (!isConnected) return
        val msg = JsonObject().apply {
            addProperty("type", "enroll_profile")
            addProperty("person_id", personId)
            addProperty("name", name)
            addProperty("nickname", nickname)
            addProperty("role", role)
            addProperty("age", age)
            addProperty("relationship", relationship)
        }
        webSocket?.send(gson.toJson(msg))
    }

    fun disconnect() {
        webSocket?.close(1000, "客户端关闭")
        isConnected = false
    }

    interface WebSocketListener {
        fun onConnected()
        fun onDisconnected(reason: String)
        fun onJsonMessage(json: JsonObject)
        fun onBinaryMessage(type: Byte, data: ByteArray)
        fun onError(message: String)
    }
}
