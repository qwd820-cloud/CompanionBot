"""异常行为检测 — 跌倒/呼救/长时间无活动 + 模糊匹配增强"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

import numpy as np

from server.utils.keywords import (
    DISTRESS_KEYWORDS,
    EMOTIONAL_DISTRESS_KEYWORDS,
    HEALTH_URGENT_KEYWORDS,
    match_any_keyword,
    match_fall_pattern,
    match_health_fuzzy,
)

logger = logging.getLogger("companion_bot.anomaly")

DISTRESS_COOLDOWN_SECONDS = 60
# 音频能量突变阈值 (可能跌倒/撞击)
ENERGY_SPIKE_THRESHOLD = 5.0  # 当前帧能量 / 平均能量 > 此值


class AnomalyType(str, Enum):
    FALL = "fall"
    DISTRESS_CALL = "distress_call"
    INACTIVITY = "inactivity"
    HEALTH_CONCERN = "health_concern"
    EMOTIONAL_DISTRESS = "emotional_distress"
    AUDIO_SPIKE = "audio_spike"


@dataclass
class Anomaly:
    """检测到的异常"""

    type: AnomalyType
    severity: str  # "P0", "P1", "P2"
    person_id: str
    description: str
    timestamp: float


class AnomalyDetector:
    """异常行为检测器 — 多层检测: 精确关键词 + 模糊模式 + 音频能量"""

    def __init__(self, inactivity_threshold_hours: float = 4.0):
        self.inactivity_threshold = inactivity_threshold_hours * 3600
        self._last_activity: dict[str, float] = {}
        self._distress_cooldown: dict[str, float] = {}
        # 音频能量历史 (用于突变检测)
        self._energy_history: list[float] = []
        self._energy_window = 50  # 保留最近 50 帧

    async def check_audio(self, text: str, person_id: str) -> Anomaly | None:
        """检查音频转写文本中的异常 — 三层检测"""
        self._last_activity[person_id] = time.time()

        # Layer 1: 精确关键词匹配 (最高优先级)
        anomaly = self._check_exact_keywords(text, person_id)
        if anomaly:
            return anomaly

        # Layer 2: 模糊模式匹配 (跌倒描述、身体不适)
        anomaly = self._check_fuzzy_patterns(text, person_id)
        if anomaly:
            return anomaly

        return None

    async def check_audio_energy(
        self, audio_data: bytes, person_id: str
    ) -> Anomaly | None:
        """检查音频能量突变 (可能跌倒/撞击)"""
        try:
            pcm = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
            if len(pcm) == 0:
                return None

            energy = float(np.sqrt(np.mean(pcm**2)))
            self._energy_history.append(energy)
            if len(self._energy_history) > self._energy_window:
                self._energy_history = self._energy_history[-self._energy_window :]

            if len(self._energy_history) < 10:
                return None

            avg_energy = np.mean(self._energy_history[:-1])
            if avg_energy > 0 and energy / avg_energy > ENERGY_SPIKE_THRESHOLD:
                # 能量突变 — 可能是撞击/跌倒
                cooldown = self._distress_cooldown.get(f"spike_{person_id}", 0)
                if time.time() - cooldown < DISTRESS_COOLDOWN_SECONDS:
                    return None
                self._distress_cooldown[f"spike_{person_id}"] = time.time()

                logger.warning(
                    f"音频能量突变: {energy:.0f} / avg {avg_energy:.0f} = {energy / avg_energy:.1f}x"
                )
                return Anomaly(
                    type=AnomalyType.AUDIO_SPIKE,
                    severity="P1",
                    person_id=person_id,
                    description=f"音频能量突变 ({energy / avg_energy:.1f}x)，可能发生撞击/跌倒",
                    timestamp=time.time(),
                )
        except Exception as e:
            logger.debug(f"音频能量检测异常: {e}")

        return None

    def _check_exact_keywords(self, text: str, person_id: str) -> Anomaly | None:
        """Layer 1: 精确关键词匹配"""
        # P0: 呼救
        kw = match_any_keyword(text, DISTRESS_KEYWORDS)
        if kw:
            if self._in_cooldown(person_id):
                return None
            self._distress_cooldown[person_id] = time.time()
            return Anomaly(
                type=AnomalyType.DISTRESS_CALL,
                severity="P0",
                person_id=person_id,
                description=f"检测到呼救: '{text}'",
                timestamp=time.time(),
            )

        # P1: 紧急健康
        kw = match_any_keyword(text, HEALTH_URGENT_KEYWORDS)
        if kw:
            return Anomaly(
                type=AnomalyType.HEALTH_CONCERN,
                severity="P1",
                person_id=person_id,
                description=f"检测到健康异常: '{text}'",
                timestamp=time.time(),
            )

        # P1: 情绪危机
        kw = match_any_keyword(text, EMOTIONAL_DISTRESS_KEYWORDS)
        if kw:
            return Anomaly(
                type=AnomalyType.EMOTIONAL_DISTRESS,
                severity="P1",
                person_id=person_id,
                description=f"检测到严重情绪异常: '{text}'",
                timestamp=time.time(),
            )

        return None

    def _check_fuzzy_patterns(self, text: str, person_id: str) -> Anomaly | None:
        """Layer 2: 模糊模式匹配"""
        # 跌倒相关描述
        match = match_fall_pattern(text)
        if match:
            if self._in_cooldown(person_id):
                return None
            self._distress_cooldown[person_id] = time.time()
            return Anomaly(
                type=AnomalyType.FALL,
                severity="P0",
                person_id=person_id,
                description=f"检测到疑似跌倒描述: '{match}' (原文: '{text}')",
                timestamp=time.time(),
            )

        # 身体不适描述
        match = match_health_fuzzy(text)
        if match:
            return Anomaly(
                type=AnomalyType.HEALTH_CONCERN,
                severity="P1",
                person_id=person_id,
                description=f"检测到身体不适: '{match}' (原文: '{text}')",
                timestamp=time.time(),
            )

        return None

    def _in_cooldown(self, person_id: str) -> bool:
        cooldown = self._distress_cooldown.get(person_id, 0)
        return time.time() - cooldown < DISTRESS_COOLDOWN_SECONDS

    async def check_presence(self, person_id: str, client_id: str) -> Anomaly | None:
        """检查长时间无活动"""
        if person_id in ("unknown", "bot"):
            return None

        now = time.time()
        last = self._last_activity.get(person_id, now)
        self._last_activity[person_id] = now

        idle_time = now - last
        if idle_time > self.inactivity_threshold:
            return Anomaly(
                type=AnomalyType.INACTIVITY,
                severity="P1",
                person_id=person_id,
                description=f"长时间无活动: {idle_time / 3600:.1f} 小时",
                timestamp=now,
            )

        return None

    def update_activity(self, person_id: str):
        """手动更新活动时间"""
        self._last_activity[person_id] = time.time()
