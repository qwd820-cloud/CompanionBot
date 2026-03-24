"""记忆沉淀 — 对话结束后通过 LLM 提取和存储关键信息"""

import json
import logging

from server.memory.episodic_memory import EpisodicMemory
from server.memory.semantic_memory import SemanticMemory
from server.memory.long_term_profile import LongTermProfile
from server.utils.keywords import (
    BOT_NAME,
    HEALTH_KEYWORDS,
    NEGATIVE_EMOTION_KEYWORDS,
    POSITIVE_EMOTION_KEYWORDS,
    match_any_keyword,
)

logger = logging.getLogger("companion_bot.consolidation")

# 重要性阈值
IMPORTANCE_THRESHOLD = 0.3

# LLM 对话分析 prompt
ANALYSIS_PROMPT = """\
你是一个记忆提取系统。分析以下对话，输出 JSON (不要输出其他内容):

{conversation}

请输出:
{{
  "summary": "用一两句话概括这段对话的核心内容",
  "importance": 0.0到1.0的数字 (健康相关>=0.8, 情绪变化>=0.6, 日常闲聊0.2~0.4),
  "emotion": "对话中{person_id}的情绪 (neutral/happy/concerned/sad/curious)",
  "new_interests": ["从对话中发现的新兴趣爱好，没有则为空数组"],
  "new_health": ["从对话中发现的新健康信息，没有则为空数组"],
  "new_concerns": ["对方近期关注的事情，没有则为空数组"]
}}

注意:
- summary 要抓住关键信息，不要只是复述
- 只提取明确提到的兴趣/健康/关注，不要猜测
- 如果对方说"不喜欢X"，不要把X加入兴趣"""


class MemoryConsolidation:
    """
    记忆沉淀流程:
    1. LLM 总结对话要点 → 生成 event summary
    2. LLM 评估 importance_score
    3. 高于阈值的写入情景记忆
    4. 对话摘要向量化写入语义记忆
    5. 发现新信息时更新长期档案
    """

    def __init__(
        self,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        profile: LongTermProfile,
        llm_client=None,
    ):
        self.episodic = episodic
        self.semantic = semantic
        self.profile = profile
        self.llm_client = llm_client

    async def consolidate(self, session_data: dict):
        """
        对话结束后的记忆沉淀主流程。
        session_data: WorkingMemory.end_session() 的返回值
        """
        turns = session_data.get("turns", [])
        if not turns:
            return

        person_ids = session_data.get("person_ids", [])

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

            # LLM 分析对话 (有 LLM 走 LLM，无 LLM 走规则回退)
            analysis = await self._analyze_conversation(person_id, person_text)
            summary = analysis["summary"]
            importance = analysis["importance"]
            emotion = analysis["emotion"]

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

            # 更新长期档案
            await self._update_profile(person_id, analysis)

        logger.info(
            f"记忆沉淀完成: session={session_data.get('session_id')}, "
            f"涉及 {len(person_ids)} 人"
        )

    async def _analyze_conversation(
        self, person_id: str, text: str
    ) -> dict:
        """
        分析对话，返回结构化结果。
        优先使用 LLM，LLM 不可用时回退到规则。
        """
        if self.llm_client is not None:
            result = await self._analyze_with_llm(person_id, text)
            if result is not None:
                return result

        # LLM 不可用或调用失败，规则回退
        return self._analyze_with_rules(person_id, text)

    async def _analyze_with_llm(
        self, person_id: str, text: str
    ) -> dict | None:
        """通过 LLM 分析对话"""
        prompt = ANALYSIS_PROMPT.format(
            conversation=text, person_id=person_id
        )
        try:
            result = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                task_type="consolidation",
            )
            content = result.get("content", "")
            parsed = self._parse_llm_response(content)
            if parsed:
                logger.info(
                    f"LLM 记忆分析完成: person={person_id}, "
                    f"importance={parsed['importance']}"
                )
                return parsed
        except Exception as e:
            logger.warning(f"LLM 记忆分析失败，回退到规则: {e}")
        return None

    def _parse_llm_response(self, content: str) -> dict | None:
        """解析 LLM 返回的 JSON"""
        # 尝试提取 JSON (LLM 可能返回 ```json ... ``` 包裹的内容)
        text = content.strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"LLM 返回非法 JSON: {content[:200]}")
            return None

        # 校验必要字段
        summary = data.get("summary", "")
        if not summary:
            return None

        importance = data.get("importance", 0.3)
        if not isinstance(importance, (int, float)):
            importance = 0.3
        importance = max(0.0, min(1.0, float(importance)))

        emotion = data.get("emotion", "neutral")
        valid_emotions = {"neutral", "happy", "concerned", "sad", "curious"}
        if emotion not in valid_emotions:
            emotion = "neutral"

        return {
            "summary": summary,
            "importance": importance,
            "emotion": emotion,
            "new_interests": data.get("new_interests", []),
            "new_health": data.get("new_health", []),
            "new_concerns": data.get("new_concerns", []),
        }

    def _analyze_with_rules(self, person_id: str, text: str) -> dict:
        """规则回退: LLM 不可用时的基础分析"""
        importance = 0.3
        emotion = "neutral"

        if match_any_keyword(text, HEALTH_KEYWORDS):
            importance = max(importance, 0.8)
            emotion = "concerned"
        elif match_any_keyword(text, NEGATIVE_EMOTION_KEYWORDS):
            importance = max(importance, 0.6)
            emotion = "concerned"
        elif match_any_keyword(text, POSITIVE_EMOTION_KEYWORDS):
            importance = max(importance, 0.4)
            emotion = "happy"

        # 规则摘要: 提取每个说话人的最后一句话
        lines = text.strip().split("\n")
        key_lines = []
        for line in lines:
            if not line.startswith(BOT_NAME):
                key_lines.append(line)
        summary = "; ".join(key_lines[-3:]) if key_lines else text[:200]
        if len(summary) > 300:
            summary = summary[:300] + "..."

        # 规则兴趣/健康检测
        new_interests = self._detect_interests_by_rules(text)
        new_health = self._detect_health_by_rules(text)

        return {
            "summary": summary,
            "importance": importance,
            "emotion": emotion,
            "new_interests": new_interests,
            "new_health": new_health,
            "new_concerns": [],
        }

    async def _update_profile(self, person_id: str, analysis: dict):
        """根据分析结果更新长期档案"""
        profile = await self.profile.get_profile(person_id)
        if profile is None:
            logger.warning(f"档案不存在，跳过更新: {person_id}")
            return

        new_interests = [
            i for i in analysis.get("new_interests", [])
            if i and i not in profile.get("interests", [])
        ]
        if new_interests:
            await self.profile.update_interests(person_id, new_interests)
            logger.info(f"档案更新兴趣: {person_id} += {new_interests}")

        new_health = [
            h for h in analysis.get("new_health", [])
            if h and h not in profile.get("health_conditions", [])
        ]
        if new_health:
            await self.profile.update_health(person_id, new_health)
            logger.info(f"档案更新健康: {person_id} += {new_health}")

        new_concerns = analysis.get("new_concerns", [])
        if new_concerns:
            await self.profile.update_concerns(person_id, new_concerns)
            logger.info(f"档案更新关注: {person_id} = {new_concerns}")

    def _detect_interests_by_rules(self, text: str) -> list[str]:
        """规则检测兴趣"""
        patterns = {
            "下棋": "下棋", "象棋": "下棋", "围棋": "围棋",
            "种花": "种花", "养花": "种花",
            "钓鱼": "钓鱼", "跳舞": "跳舞", "唱歌": "唱歌",
            "听戏": "听戏曲", "戏曲": "听戏曲",
            "太极": "太极拳", "散步": "散步",
        }
        found = set()
        for keyword, interest in patterns.items():
            if keyword in text:
                found.add(interest)
        return list(found)

    def _detect_health_by_rules(self, text: str) -> list[str]:
        """规则检测健康信息"""
        patterns = {
            "高血压": "高血压", "糖尿病": "糖尿病",
            "膝盖": "膝盖不好", "腰疼": "腰疼",
            "失眠": "失眠", "血糖": "血糖问题",
        }
        found = set()
        for keyword, condition in patterns.items():
            if keyword in text:
                found.add(condition)
        return list(found)

    def _format_conversation(self, turns: list[dict]) -> str:
        """格式化对话为文本"""
        lines = []
        for t in turns:
            speaker = t.get("person_id", "unknown")
            if t.get("role") == "assistant":
                speaker = BOT_NAME
            lines.append(f"{speaker}: {t['text']}")
        return "\n".join(lines)
