"""Silero VAD 封装 — 语音活动检测"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("companion_bot.vad")

# VAD 配置
VAD_THRESHOLD = 0.5
MIN_SPEECH_DURATION_MS = 250
MIN_SILENCE_DURATION_MS = 100
SAMPLE_RATE = 16000
WINDOW_SIZE = 512  # Silero VAD 要求的窗口大小 (16kHz 下 32ms)


@dataclass
class SpeechSegment:
    """检测到的语音段"""
    audio: np.ndarray
    start_ms: float
    end_ms: float


class VADProcessor:
    """基于 Silero VAD 的语音活动检测"""

    def __init__(
        self,
        threshold: float = VAD_THRESHOLD,
        min_speech_ms: int = MIN_SPEECH_DURATION_MS,
        min_silence_ms: int = MIN_SILENCE_DURATION_MS,
        sample_rate: int = SAMPLE_RATE,
    ):
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.min_silence_ms = min_silence_ms
        self.sample_rate = sample_rate
        self.model = None
        self._buffer = np.array([], dtype=np.float32)
        self._speech_start: float | None = None
        self._speech_buffer: list[np.ndarray] = []
        self._silence_count = 0

    async def initialize(self):
        """加载 Silero VAD 模型"""
        try:
            import torch
            self.model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            self.model.eval()
            logger.info("Silero VAD 模型加载成功")
        except Exception as e:
            logger.warning(f"Silero VAD 加载失败，使用能量检测: {e}")
            self.model = None

    async def process(self, audio_bytes: bytes) -> list[SpeechSegment]:
        """
        处理音频数据，返回检测到的语音段。
        输入: 16kHz 单声道 16-bit PCM 字节流
        输出: 语音段列表
        """
        # PCM bytes → float32 numpy array
        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        pcm = pcm / 32768.0
        self._buffer = np.concatenate([self._buffer, pcm])

        segments: list[SpeechSegment] = []

        while len(self._buffer) >= WINDOW_SIZE:
            window = self._buffer[:WINDOW_SIZE]
            self._buffer = self._buffer[WINDOW_SIZE:]

            is_speech = self._detect_speech(window)
            window_ms = (WINDOW_SIZE / self.sample_rate) * 1000

            if is_speech:
                self._silence_count = 0
                if self._speech_start is None:
                    self._speech_start = 0.0
                self._speech_buffer.append(window)
            else:
                self._silence_count += 1
                silence_ms = self._silence_count * window_ms

                if (
                    self._speech_start is not None
                    and silence_ms >= self.min_silence_ms
                ):
                    speech_audio = np.concatenate(self._speech_buffer)
                    speech_ms = (
                        len(speech_audio) / self.sample_rate
                    ) * 1000

                    if speech_ms >= self.min_speech_ms:
                        segments.append(SpeechSegment(
                            audio=speech_audio,
                            start_ms=self._speech_start,
                            end_ms=self._speech_start + speech_ms,
                        ))

                    self._speech_start = None
                    self._speech_buffer = []

        return segments

    def _detect_speech(self, window: np.ndarray) -> bool:
        """检测窗口是否包含语音"""
        if self.model is not None:
            return self._silero_detect(window)
        return self._energy_detect(window)

    def _silero_detect(self, window: np.ndarray) -> bool:
        """使用 Silero VAD 模型检测"""
        import torch
        tensor = torch.from_numpy(window)
        prob = self.model(tensor, self.sample_rate).item()
        return prob >= self.threshold

    def _energy_detect(self, window: np.ndarray) -> bool:
        """能量检测回退方案"""
        energy = np.sqrt(np.mean(window ** 2))
        return energy > 0.01  # 简单能量阈值

    def reset(self):
        """重置 VAD 状态"""
        self._buffer = np.array([], dtype=np.float32)
        self._speech_start = None
        self._speech_buffer = []
        self._silence_count = 0
        if self.model is not None:
            self.model.reset_states()
