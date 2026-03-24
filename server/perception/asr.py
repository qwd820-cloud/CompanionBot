"""ASR 语音转文字模块 — FunASR / Whisper"""

import logging

import numpy as np

logger = logging.getLogger("companion_bot.asr")

SAMPLE_RATE = 16000


class ASRProcessor:
    """语音转文字处理器，支持 FunASR 和 Whisper"""

    def __init__(self, backend: str = "funasr", model_size: str = "large"):
        self.backend = backend
        self.model_size = model_size
        self.model = None

    async def initialize(self):
        """加载 ASR 模型"""
        if self.backend == "funasr":
            await self._init_funasr()
        else:
            await self._init_whisper()

    async def _init_funasr(self):
        """加载 FunASR Paraformer"""
        try:
            from funasr import AutoModel
            self.model = AutoModel(
                model="paraformer-zh",
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                device="cuda",
            )
            self.backend = "funasr"
            logger.info("FunASR Paraformer 加载成功")
        except Exception as e:
            logger.warning(f"FunASR 加载失败，尝试 Whisper: {e}")
            await self._init_whisper()

    async def _init_whisper(self):
        """加载 Whisper"""
        try:
            import whisper
            self.model = whisper.load_model(self.model_size, device="cuda")
            self.backend = "whisper"
            logger.info(f"Whisper {self.model_size} 加载成功")
        except Exception as e:
            logger.warning(f"Whisper 加载失败: {e}")
            self.model = None

    async def transcribe(self, segment) -> dict:
        """
        语音转文字。
        输入: SpeechSegment 或 numpy 音频数据
        输出: {"text": str, "timestamps": list}
        """
        audio = segment.audio if hasattr(segment, "audio") else segment

        if self.model is None:
            return {"text": "", "timestamps": []}

        if self.backend == "funasr":
            return self._transcribe_funasr(audio)
        return self._transcribe_whisper(audio)

    def _transcribe_funasr(self, audio: np.ndarray) -> dict:
        """FunASR 转写"""
        try:
            result = self.model.generate(input=audio)
            if result and len(result) > 0:
                text = result[0].get("text", "")
                timestamps = result[0].get("timestamp", [])
                return {"text": text, "timestamps": timestamps}
        except Exception as e:
            logger.error(f"FunASR 转写失败: {e}")
        return {"text": "", "timestamps": []}

    def _transcribe_whisper(self, audio: np.ndarray) -> dict:
        """Whisper 转写"""
        try:
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            result = self.model.transcribe(
                audio,
                language="zh",
                fp16=True,
            )
            text = result.get("text", "")
            segments = result.get("segments", [])
            timestamps = [
                {"start": s["start"], "end": s["end"], "text": s["text"]}
                for s in segments
            ]
            return {"text": text, "timestamps": timestamps}
        except Exception as e:
            logger.error(f"Whisper 转写失败: {e}")
        return {"text": "", "timestamps": []}
