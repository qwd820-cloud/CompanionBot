"""异常行为检测 — 跌倒/呼救/长时间无活动"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("companion_bot.anomaly")


class AnomalyType(str, Enum):
    FALL = "fall"
    DISTRESS_CALL = "distress_call"
    INACTIVITY = "inactivity"
    HEALTH_CONCERN = "health_concern"
    EMOTIONAL_DISTRESS = "emotional_distress"


@dataclass
class Anomaly:
    """检测到的异常"""
    type: AnomalyType
    severity: str  # "P0", "P1", "P2"
    person_id: str
    description: str
    timestamp: float


class AnomalyDetector:
    """异常行为检测器"""

    def __init__(self, inactivity_threshold_hours: float = 4.0):
        self.inactivity_threshold = inactivity_threshold_hours * 3600
        self._last_activity: dict[str, float] = {}  # person_id → timestamp
        self._distress_cooldown: dict[str, float] = {}

    async def check_audio(
        self, text: str, person_id: str
    ) -> Anomaly | None:
        """检查音频转写文本中的异常"""
        # 更新活动时间
        self._last_activity[person_id] = time.time()

        # 呼救检测 (P0)
        distress_keywords = [
            "救命", "帮帮我", "快来人", "摔倒了", "不行了",
        ]
        for kw in distress_keywords:
            if kw in text:
                # 冷却期检查 (避免误报重复触发)
                cooldown = self._distress_cooldown.get(person_id, 0)
                if time.time() - cooldown < 60:  # 1 分钟内不重复触发
                    return None
                self._distress_cooldown[person_id] = time.time()
                return Anomaly(
                    type=AnomalyType.DISTRESS_CALL,
                    severity="P0",
                    person_id=person_id,
                    description=f"检测到呼救: '{text}'",
                    timestamp=time.time(),
                )

        # 健康异常 (P1)
        health_urgents = [
            "胸闷", "喘不上气", "很晕", "眼前发黑", "心脏",
        ]
        for kw in health_urgents:
            if kw in text:
                return Anomaly(
                    type=AnomalyType.HEALTH_CONCERN,
                    severity="P1",
                    person_id=person_id,
                    description=f"检测到健康异常: '{text}'",
                    timestamp=time.time(),
                )

        # 情绪异常 (P1) — 持续负面情绪
        emotional_keywords = [
            "不想活", "活着没意思", "太痛苦了",
        ]
        for kw in emotional_keywords:
            if kw in text:
                return Anomaly(
                    type=AnomalyType.EMOTIONAL_DISTRESS,
                    severity="P1",
                    person_id=person_id,
                    description=f"检测到严重情绪异常: '{text}'",
                    timestamp=time.time(),
                )

        return None

    async def check_presence(
        self, person_id: str, client_id: str
    ) -> Anomaly | None:
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
                description=f"长时间无活动: {idle_time/3600:.1f} 小时",
                timestamp=now,
            )

        return None

    def update_activity(self, person_id: str):
        """手动更新活动时间"""
        self._last_activity[person_id] = time.time()
