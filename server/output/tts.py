"""TTS 语音合成模块 — Edge-TTS 在线 + pyttsx3 离线 fallback"""

import asyncio
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

# pyttsx3 情绪 → 语速/音调比例
EMOTION_PYTTSX3_PARAMS = {
    "neutral": {"rate_factor": 1.0, "volume": 0.9},
    "happy": {"rate_factor": 1.1, "volume": 1.0},
    "concerned": {"rate_factor": 0.9, "volume": 0.85},
    "tired": {"rate_factor": 0.85, "volume": 0.8},
    "curious": {"rate_factor": 1.05, "volume": 0.95},
    "slightly_annoyed": {"rate_factor": 1.0, "volume": 0.9},
}

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class TTSEngine:
    """语音合成引擎 — Edge-TTS 在线优先，pyttsx3 离线 fallback"""

    def __init__(self, voice: str = DEFAULT_VOICE):
        self.voice = voice
        self._edge_available = True  # 首次失败后标记为不可用
        self._pyttsx3_engine = None

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes | None:
        """
        文字转语音。优先 Edge-TTS (高质量)，失败时 fallback pyttsx3 (离线)。
        返回: 音频字节，或 None
        """
        if not text.strip():
            return None

        # 优先 Edge-TTS
        if self._edge_available:
            audio = await self._synthesize_edge(text, emotion)
            if audio:
                return audio
            # Edge-TTS 失败，标记不可用，后续直接走 fallback
            self._edge_available = False
            logger.warning("Edge-TTS 不可用，切换到离线 pyttsx3")

        # Fallback: pyttsx3 离线合成
        return await self._synthesize_pyttsx3(text, emotion)

    async def _synthesize_edge(self, text: str, emotion: str) -> bytes | None:
        """Edge-TTS 在线合成"""
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
                    f"Edge-TTS 合成完成: {len(audio_data)} bytes, emotion={emotion}"
                )
                return audio_data
        except Exception as e:
            logger.warning(f"Edge-TTS 合成失败: {e}")
        return None

    async def _synthesize_pyttsx3(self, text: str, emotion: str) -> bytes | None:
        """pyttsx3 离线合成 (在线程池中执行，避免阻塞事件循环)"""
        try:
            loop = asyncio.get_event_loop()
            audio = await loop.run_in_executor(None, self._pyttsx3_sync, text, emotion)
            return audio
        except Exception as e:
            logger.error(f"pyttsx3 离线合成失败: {e}")
            return None

    def _pyttsx3_sync(self, text: str, emotion: str) -> bytes | None:
        """pyttsx3 同步合成 — 在线程池中调用"""
        try:
            import tempfile

            import pyttsx3

            if self._pyttsx3_engine is None:
                self._pyttsx3_engine = pyttsx3.init()
                # 尝试设置中文语音
                for voice in self._pyttsx3_engine.getProperty("voices"):
                    if "chinese" in voice.name.lower() or "zh" in voice.id.lower():
                        self._pyttsx3_engine.setProperty("voice", voice.id)
                        break

            engine = self._pyttsx3_engine
            params = EMOTION_PYTTSX3_PARAMS.get(
                emotion, EMOTION_PYTTSX3_PARAMS["neutral"]
            )

            base_rate = engine.getProperty("rate") or 150
            engine.setProperty("rate", int(base_rate * params["rate_factor"]))
            engine.setProperty("volume", params["volume"])

            # 合成到临时文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name

            engine.save_to_file(text, tmp_path)
            engine.runAndWait()

            with open(tmp_path, "rb") as f:
                audio_data = f.read()

            import os

            os.unlink(tmp_path)

            if audio_data:
                logger.debug(
                    f"pyttsx3 离线合成完成: {len(audio_data)} bytes, emotion={emotion}"
                )
                return audio_data
        except ImportError:
            logger.warning("pyttsx3 未安装，无法离线合成 (pip install pyttsx3)")
        except Exception as e:
            logger.error(f"pyttsx3 合成异常: {e}")
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
