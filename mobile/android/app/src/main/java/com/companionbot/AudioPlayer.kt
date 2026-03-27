package com.companionbot

import android.media.*
import android.util.Log
import kotlinx.coroutines.*
import java.io.File
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * TTS 音频播放器 — 接收后端返回的 MP3 音频并播放
 */
class AudioPlayer {
    companion object {
        private const val TAG = "CompanionBot.Player"
    }

    private var mediaPlayer: MediaPlayer? = null
    private val playbackQueue = ConcurrentLinkedQueue<ByteArray>()
    private var isPlaying = false
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    fun enqueue(audioData: ByteArray) {
        playbackQueue.add(audioData)
        if (!isPlaying) {
            playNext()
        }
    }

    private fun playNext() {
        val data = playbackQueue.poll() ?: run {
            isPlaying = false
            return
        }

        isPlaying = true
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
                isPlaying = false
                playNext()
            }
        }
    }

    fun stop() {
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
