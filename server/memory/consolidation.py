"""记忆沉淀 — 对话结束后自动提取和存储关键信息"""

import logging

from server.memory.episodic_memory import EpisodicMemory
from server.memory.semantic_memory import SemanticMemory
from server.memory.long_term_profile import LongTermProfile

logger = logging.getLogger("companion_bot.consolidation")

# 重要性阈值
IMPORTANCE_THRESHOLD = 0.3


class MemoryConsolidation:
    """
    记忆沉淀流程:
    1. LLM 总结对话要点
    2. 评估 importance_score
    3. 高于阈值的写入情景记忆
    4. 对话摘要向量化写入语义记忆
    5. 发现新信息时更新长期档案
    """

    def __init__(
        self,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        profile: LongTermProfile,
    ):
        self.episodic = episodic
        self.semantic = semantic
        self.profile = profile

    async def consolidate(self, session_data: dict):
        """
        对话结束后的记忆沉淀主流程。
        session_data: WorkingMemory.end_session() 的返回值
        """
        turns = session_data.get("turns", [])
        if not turns:
            return

        person_ids = session_data.get("person_ids", [])

        # 构建对话文本
        conversation_text = self._format_conversation(turns)

        for person_id in person_ids:
            if person_id in ("bot", "unknown"):
                continue

            # 提取该人相关的对话
            person_turns = [
                t for t in turns
                if t["person_id"] == person_id or t["role"] == "assistant"
            ]
            if not person_turns:
                continue

            person_text = self._format_conversation(person_turns)

            # 生成摘要和评估重要性
            # 注意: 实际使用时通过 LLM 生成，这里提供基于规则的回退
            summary, importance, emotion = await self._analyze_conversation(
                person_id, person_text
            )

            # 写入情景记忆 (重要性 > 阈值)
            if importance >= IMPORTANCE_THRESHOLD:
                await self.episodic.add_episode(
                    person_id=person_id,
                    summary=summary,
                    emotion_tag=emotion,
                    importance_score=importance,
                )

            # 写入语义记忆 (所有对话)
            await self.semantic.add(
                person_id=person_id,
                text=summary,
                metadata={
                    "importance": importance,
                    "emotion": emotion,
                    "session_id": session_data.get("session_id", ""),
                },
            )

            # 检查是否需要更新长期档案
            await self._update_profile_if_needed(person_id, person_text)

        logger.info(
            f"记忆沉淀完成: session={session_data.get('session_id')}, "
            f"涉及 {len(person_ids)} 人"
        )

    async def _analyze_conversation(
        self, person_id: str, text: str
    ) -> tuple[str, float, str]:
        """
        分析对话，返回 (摘要, 重要性评分, 情绪标签)。
        实际使用时调用 LLM，此处为基于规则的回退。
        """
        # 基于关键词的重要性评估
        importance = 0.3  # 默认
        emotion = "neutral"

        health_keywords = [
            "疼", "痛", "不舒服", "头晕", "血压", "吃药", "医院",
            "检查", "发烧", "咳嗽",
        ]
        emotion_keywords_negative = [
            "难过", "伤心", "孤独", "无聊", "想", "念", "担心",
        ]
        emotion_keywords_positive = [
            "开心", "高兴", "好消息", "太好了", "哈哈",
        ]

        text_lower = text.lower()

        for kw in health_keywords:
            if kw in text_lower:
                importance = max(importance, 0.8)
                emotion = "concerned"
                break

        for kw in emotion_keywords_negative:
            if kw in text_lower:
                importance = max(importance, 0.6)
                emotion = "concerned"
                break

        for kw in emotion_keywords_positive:
            if kw in text_lower:
                importance = max(importance, 0.4)
                emotion = "happy"
                break

        # 简单摘要 (取前200字)
        summary = text[:200].replace("\n", " ")
        if len(text) > 200:
            summary += "..."

        return summary, importance, emotion

    async def _update_profile_if_needed(self, person_id: str, text: str):
        """检查对话中是否有需要更新到长期档案的信息"""
        profile = await self.profile.get_profile(person_id)
        if profile is None:
            return

        # 简单的兴趣检测 (实际使用时通过 LLM)
        interest_patterns = {
            "下棋": "下棋", "象棋": "下棋", "围棋": "围棋",
            "种花": "种花", "养花": "种花",
            "钓鱼": "钓鱼", "跳舞": "跳舞", "唱歌": "唱歌",
            "听戏": "听戏曲", "戏曲": "听戏曲",
            "太极": "太极拳", "散步": "散步",
        }

        new_interests = []
        for keyword, interest in interest_patterns.items():
            if keyword in text and interest not in profile["interests"]:
                new_interests.append(interest)

        if new_interests:
            await self.profile.update_interests(person_id, new_interests)

        # 健康信息检测
        health_patterns = {
            "高血压": "高血压", "糖尿病": "糖尿病",
            "膝盖": "膝盖不好", "腰疼": "腰疼",
            "失眠": "失眠", "血糖": "血糖问题",
        }

        new_health = []
        for keyword, condition in health_patterns.items():
            if (
                keyword in text
                and condition not in profile["health_conditions"]
            ):
                new_health.append(condition)

        if new_health:
            await self.profile.update_health(person_id, new_health)

    def _format_conversation(self, turns: list[dict]) -> str:
        """格式化对话为文本"""
        lines = []
        for t in turns:
            speaker = t.get("person_id", "unknown")
            if t.get("role") == "assistant":
                speaker = "小伴"
            lines.append(f"{speaker}: {t['text']}")
        return "\n".join(lines)
