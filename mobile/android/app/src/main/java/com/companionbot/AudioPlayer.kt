package com.companionbot

import android.media.*
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.*
import java.io.File
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * TTS 音频播放器 — 接收后端返回的 MP3 音频并播放
 *
 * 支持播放状态回调，用于协调麦克风静音（回声消除）。
 */
class AudioPlayer {
    companion object {
        private const val TAG = "CompanionBot.Player"
        private const val FINISH_DEBOUNCE_MS = 200L
    }

    private var mediaPlayer: MediaPlayer? = null
    private val playbackQueue = ConcurrentLinkedQueue<ByteArray>()
    private var isPlaying = false
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val mainHandler = Handler(Looper.getMainLooper())
    private var finishDebounceJob: Job? = null

    /** TTS 开始播放时回调（从静默→有声，首段音频开始时触发） */
    var onPlaybackStarted: (() -> Unit)? = null

    /** TTS 全部播放完毕回调（队列清空且最后一段播放完成，含 200ms debounce） */
    var onPlaybackFinished: (() -> Unit)? = null

    fun enqueue(audioData: ByteArray) {
        finishDebounceJob?.cancel()
        playbackQueue.add(audioData)
        if (!isPlaying) {
            isPlaying = true
            mainHandler.post { onPlaybackStarted?.invoke() }
            playNext()
        }
    }

    private fun playNext() {
        finishDebounceJob?.cancel()
        val data = playbackQueue.poll() ?: run {
            // 队列空了，启动 debounce — 如果 200ms 内没有新数据则触发 finished
            finishDebounceJob = scope.launch {
                delay(FINISH_DEBOUNCE_MS)
                isPlaying = false
                mainHandler.post { onPlaybackFinished?.invoke() }
            }
            return
        }

        scope.launch {
            try {
                val tempFile = File.createTempFile("tts_", ".mp3")
                tempFile.writeBytes(data)

                withContext(Dispatchers.Main) {
                    mediaPlayer?.release()
                    mediaPlayer = MediaPlayer().apply {
                        setDataSource(tempFile.absolutePath)
                        setAudioAttributes(
                            AudioAttributes.Builder()
                                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                                .build()
                        )
                        setOnCompletionListener {
                            tempFile.delete()
                            playNext()
                        }
                        setOnErrorListener { _, what, extra ->
                            Log.e(TAG, "播放错误: what=$what, extra=$extra")
                            tempFile.delete()
                            playNext()
                            true
                        }
                        prepare()
                        start()
                    }
                }
                Log.d(TAG, "播放 TTS 音频: ${data.size} bytes")
            } catch (e: Exception) {
                Log.e(TAG, "音频播放失败", e)
                playNext()
            }
        }
    }

    fun stop() {
        finishDebounceJob?.cancel()
        playbackQueue.clear()
        isPlaying = false
        mediaPlayer?.release()
        mediaPlayer = null
    }

    fun release() {
        stop()
        scope.cancel()
    }
}
