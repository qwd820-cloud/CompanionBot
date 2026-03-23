"""插话决策模块 — 判断机器人是否应该主动开口"""

import logging
import time

logger = logging.getLogger("companion_bot.intervention")

INTERVENTION_THRESHOLD = 0.6
COOLDOWN_SECONDS = 120  # 被忽略后 2 分钟冷却
FREQUENCY_WINDOW = 300   # 5 分钟窗口


class InterventionDecider:
    """插话决策引擎"""

    def __init__(self, threshold: float = INTERVENTION_THRESHOLD):
        self.threshold = threshold
        self._recent_interventions: list[float] = []
        self._last_ignored_time: float = 0.0

    def should_intervene(self, context: dict) -> tuple[bool, str]:
        """
        决定是否应该插话。

        输入: 当前对话上下文
        输出: (是否插话, 原因)

        决策维度:
        1. relevance_score — 内容与机器人的相关度
        2. timing_score — 是否处于自然停顿
        3. role_score — 机器人插话的角色价值
        4. frequency_penalty — 最近插话频率的惩罚
        """
        turns = context.get("turns", [])
        if not turns:
            return False, ""

        latest_text = turns[-1].get("text", "")

        # 硬规则: 安全预警直接触发
        safety_result = self._check_safety_trigger(latest_text)
        if safety_result[0]:
            return safety_result

        # 硬规则: 冷却期内不插话
        if time.time() - self._last_ignored_time < COOLDOWN_SECONDS:
            return False, "冷却期内"

        # 计算各维度评分
        relevance = self._relevance_score(latest_text, context)
        timing = self._timing_score(turns)
        role = self._role_score(latest_text)
        freq_penalty = self._frequency_penalty()

        score = (
            relevance * 0.3
            + timing * 0.2
            + role * 0.4
            - freq_penalty * 0.3
        )

        should = score >= self.threshold
        reason = (
            f"relevance={relevance:.2f}, timing={timing:.2f}, "
            f"role={role:.2f}, freq_penalty={freq_penalty:.2f}, "
            f"total={score:.2f}"
        )

        if should:
            self._recent_interventions.append(time.time())
            logger.info(f"决定插话: {reason}")

        return should, reason

    def mark_ignored(self):
        """标记插话被忽略"""
        self._last_ignored_time = time.time()

    def _check_safety_trigger(self, text: str) -> tuple[bool, str]:
        """检查安全预警触发 (绕过所有评分)"""
        safety_keywords = [
            "救命", "帮帮我", "摔倒了", "很痛", "胸闷", "喘不上气",
            "头很晕", "不行了", "快来",
        ]
        for kw in safety_keywords:
            if kw in text:
                return True, f"安全预警: 检测到'{kw}'"
        return False, ""

    def _relevance_score(self, text: str, context: dict) -> float:
        """评估对话内容与机器人的相关度"""
        score = 0.0

        # 提到机器人名字
        if "小伴" in text:
            return 1.0

        # 健康相关话题
        health_keywords = [
            "血压", "吃药", "不舒服", "医院", "检查",
        ]
        for kw in health_keywords:
            if kw in text:
                score = max(score, 0.7)

        # 问句 (可能需要帮助)
        if "?" in text or "？" in text or "吗" in text:
            score = max(score, 0.4)

        return min(score, 1.0)

    def _timing_score(self, turns: list[dict]) -> float:
        """评估当前是否是自然停顿"""
        if len(turns) < 2:
            return 0.5

        # 如果最后两轮之间有较长间隔，说明有自然停顿
        last_ts = turns[-1].get("timestamp", 0)
        prev_ts = turns[-2].get("timestamp", 0)
        gap = last_ts - prev_ts

        if gap > 5.0:  # 5 秒以上的停顿
            return 0.8
        elif gap > 2.0:
            return 0.5
        return 0.2

    def _role_score(self, text: str) -> float:
        """评估机器人插话的角色价值"""
        # 安全相关
        if any(kw in text for kw in ["摔", "痛", "救"]):
            return 1.0

        # 可以提供有用信息
        if any(kw in text for kw in ["天气", "几点", "提醒"]):
            return 0.8

        # 表达关心
        if any(kw in text for kw in ["不舒服", "难过", "累"]):
            return 0.5

        return 0.1

    def _frequency_penalty(self) -> float:
        """最近 5 分钟内的插话频率惩罚"""
        now = time.time()
        # 清理过期记录
        self._recent_interventions = [
            t for t in self._recent_interventions
            if now - t < FREQUENCY_WINDOW
        ]
        count = len(self._recent_interventions)
        if count == 0:
            return 0.0
        elif count == 1:
            return 0.2
        elif count == 2:
            return 0.5
        return 1.0
