"""TTS 语音合成模块 — Edge-TTS (原型阶段)"""

import io
import logging

logger = logging.getLogger("companion_bot.tts")

# 情绪 → TTS 参数映射
EMOTION_TTS_PARAMS = {
    "neutral": {"rate": "+0%", "pitch": "+0Hz"},
    "happy": {"rate": "+10%", "pitch": "+20Hz"},
    "concerned": {"rate": "-10%", "pitch": "-10Hz"},
    "tired": {"rate": "-15%", "pitch": "-5Hz"},
    "curious": {"rate": "+5%", "pitch": "+10Hz"},
    "slightly_annoyed": {"rate": "+0%", "pitch": "-5Hz"},
}

# 默认中文语音
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class TTSEngine:
    """基于 Edge-TTS 的语音合成引擎"""

    def __init__(self, voice: str = DEFAULT_VOICE):
        self.voice = voice

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes | None:
        """
        文字转语音。
        返回: MP3/WAV 音频字节，或 None
        """
        if not text.strip():
            return None

        params = EMOTION_TTS_PARAMS.get(emotion, EMOTION_TTS_PARAMS["neutral"])

        try:
            import edge_tts

            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=params["rate"],
                pitch=params["pitch"],
            )

            audio_buffer = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buffer.write(chunk["data"])

            audio_data = audio_buffer.getvalue()
            if audio_data:
                logger.debug(
                    f"TTS 合成完成: {len(audio_data)} bytes, emotion={emotion}"
                )
                return audio_data

        except Exception as e:
            logger.error(f"TTS 合成失败: {e}")

        return None

    async def list_voices(self, language: str = "zh") -> list[dict]:
        """列出可用的中文语音"""
        try:
            import edge_tts

            voices = await edge_tts.list_voices()
            return [
                {"name": v["Name"], "gender": v["Gender"]}
                for v in voices
                if v["Locale"].startswith(language)
            ]
        except Exception as e:
            logger.error(f"获取语音列表失败: {e}")
            return []
