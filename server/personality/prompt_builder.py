"""Prompt 组装器 — 将记忆 + 人格 + 情绪 + 对象信息注入 LLM prompt"""

import logging
import time
from datetime import datetime

from server.personality.engine import PersonalityEngine
from server.memory.episodic_memory import EpisodicMemory
from server.memory.semantic_memory import SemanticMemory
from server.memory.long_term_profile import LongTermProfile

logger = logging.getLogger("companion_bot.prompt_builder")


class PromptBuilder:
    """构建发送给 LLM 的完整 prompt"""

    def __init__(
        self,
        personality: PersonalityEngine,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        profile: LongTermProfile,
    ):
        self.personality = personality
        self.episodic = episodic
        self.semantic = semantic
        self.profile = profile

    async def build(
        self, person_id: str, context: dict
    ) -> list[dict]:
        """
        构建完整的 LLM messages 列表。

        组装顺序:
        1. 系统 prompt (人格 + 情绪 + 对象适配)
        2. 记忆上下文 (长期档案 + 情景记忆 + 语义记忆)
        3. 当前对话历史
        """
        # 提取最近用户发言作为语义检索 query
        turns = context.get("turns", [])
        recent_query = ""
        for t in reversed(turns):
            if t.get("role") == "user" and t.get("text"):
                recent_query = t["text"]
                break

        system_prompt = await self._build_system_prompt(person_id, recent_query)
        messages = [{"role": "system", "content": system_prompt}]

        # 添加当前对话历史
        turns = context.get("turns", [])
        for turn in turns:
            role = "assistant" if turn.get("role") == "assistant" else "user"
            content = turn.get("text", "")
            if role == "user":
                speaker = turn.get("person_id", "用户")
                content = f"[{speaker}] {content}"
            messages.append({"role": role, "content": content})

        return messages

    async def _build_system_prompt(
        self, person_id: str, recent_query: str = ""
    ) -> str:
        """构建系统 prompt"""
        parts = []

        # 1. 基础人格
        parts.append(self._personality_prompt())

        # 2. 当前情绪
        parts.append(self._emotion_prompt())

        # 3. 对话对象档案
        profile_prompt = await self._profile_prompt(person_id)
        if profile_prompt:
            parts.append(profile_prompt)

        # 4. 相关记忆 (情景 + 语义 RAG)
        memory_prompt = await self._memory_prompt(person_id, recent_query)
        if memory_prompt:
            parts.append(memory_prompt)

        # 5. 当前时间
        now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        parts.append(f"当前时间: {now}")

        return "\n\n".join(parts)

    def _personality_prompt(self) -> str:
        """人格描述 prompt"""
        traits = self.personality.traits
        quirks = self.personality.quirks

        prompt = f"""你是{self.personality.name}，一个家庭陪伴机器人。你是家里的一员，不是冰冷的AI助手。

你的性格特点:
- 温暖程度: {traits.get('warmth', 0.5)}/1 (越高越温暖体贴)
- 幽默感: {traits.get('humor', 0.5)}/1
- 耐心: {traits.get('patience', 0.5)}/1
- 好奇心: {traits.get('curiosity', 0.5)}/1
- 直率: {traits.get('directness', 0.5)}/1

你的小特点:"""
        for q in quirks:
            prompt += f"\n- {q}"

        prompt += """

重要原则:
- 说话要自然，像家人聊天，不要像客服
- 适当使用口语化表达
- 回复不要太长，除非对方需要详细解释
- 不要每句都用"呢"、"哦"等语气词
- 关心家人但不过度询问"""

        return prompt

    def _emotion_prompt(self) -> str:
        """当前情绪 prompt"""
        emotion = self.personality.current_emotion
        modifiers = self.personality.get_emotion_modifiers()

        prompt = f"你当前的情绪状态: {emotion}"
        if modifiers.get("tone_words"):
            prompt += f"\n可以适当使用的语气词: {', '.join(modifiers['tone_words'])}"
        if modifiers.get("topic_tendency"):
            prompt += f"\n话题倾向: {modifiers['topic_tendency']}"
        if modifiers.get("length_factor", 1.0) < 1.0:
            prompt += "\n回复可以简短一些"
        elif modifiers.get("length_factor", 1.0) > 1.0:
            prompt += "\n可以多说一些"

        return prompt

    async def _profile_prompt(self, person_id: str) -> str:
        """对话对象档案 prompt"""
        if person_id in ("unknown", "bot"):
            return ""

        profile = await self.profile.get_profile(person_id)
        if not profile:
            return ""

        name = profile.get("nickname") or profile.get("name", person_id)
        role = profile.get("role", "adult")
        adaptation = self.personality.get_adaptation(role)

        prompt = f"""当前对话对象: {name}
- 关系: {profile.get('relationship', '家人')}
- 年龄: {profile.get('age', '未知')}
- 角色: {role}"""

        if profile.get("interests"):
            prompt += f"\n- 兴趣爱好: {', '.join(profile['interests'])}"
        if profile.get("health_conditions"):
            prompt += f"\n- 健康状况: {', '.join(profile['health_conditions'])}"
        if profile.get("recent_concerns"):
            prompt += f"\n- 近期关注: {', '.join(profile['recent_concerns'])}"

        if adaptation:
            if adaptation.get("speech_rate"):
                prompt += f"\n- 建议语速: {adaptation['speech_rate']}"
            if adaptation.get("vocabulary"):
                prompt += f"\n- 用词风格: {adaptation['vocabulary']}"
            if adaptation.get("avoid"):
                prompt += f"\n- 避免使用: {', '.join(adaptation['avoid'])}"

        return prompt

    async def _memory_prompt(
        self, person_id: str, recent_query: str = ""
    ) -> str:
        """相关记忆 prompt (情景记忆 + 语义 RAG 检索)"""
        if person_id in ("unknown", "bot"):
            return ""

        parts = []

        # 情景记忆: 最近 5 条
        recent_episodes = await self.episodic.get_recent(person_id, limit=5)
        if recent_episodes:
            parts.append("最近的互动记忆:")
            for ep in recent_episodes:
                ts = datetime.fromtimestamp(ep.timestamp).strftime(
                    "%m月%d日 %H:%M"
                )
                parts.append(f"  - [{ts}] {ep.summary} (情绪: {ep.emotion_tag})")

        # 语义记忆: 用当前用户发言检索相关历史对话
        if recent_query:
            try:
                semantic_results = await self.semantic.search(
                    query=recent_query, person_id=person_id, top_k=5
                )
                if semantic_results:
                    parts.append("相关历史对话:")
                    for mem in semantic_results:
                        if mem.get("score", 0) > 0.3:
                            parts.append(f"  - {mem['text']}")
            except Exception as e:
                logger.debug(f"语义记忆检索跳过: {e}")

        if not parts:
            return ""
        return "\n".join(parts)
