"""Prompt 组装器 — 支持文本模式和端到端音频模式"""

import logging
from datetime import datetime

from server.memory.episodic_memory import EpisodicMemory
from server.memory.long_term_profile import LongTermProfile
from server.memory.semantic_memory import SemanticMemory
from server.personality.engine import PersonalityEngine

logger = logging.getLogger("companion_bot.prompt_builder")


class PromptBuilder:
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

    async def build(self, person_id: str, context: dict) -> list[dict]:
        turns = context.get("turns", [])
        recent_query = ""
        for t in reversed(turns):
            if t.get("role") == "user" and t.get("text"):
                recent_query = t["text"]
                break

        system_prompt = await self._build_system_prompt(person_id, recent_query)
        messages = [{"role": "system", "content": system_prompt}]

        for turn in turns:
            role = "assistant" if turn.get("role") == "assistant" else "user"
            content = turn.get("text", "")
            if role == "user":
                speaker = turn.get("person_id", "用户")
                content = f"[{speaker}] {content}"
            messages.append({"role": role, "content": content})

        return messages

    async def _build_system_prompt(self, person_id: str, recent_query: str = "") -> str:
        parts = []
        parts.append(self._personality_prompt())

        # 情绪指令 — 让 LLM 回复风格随情绪变化
        emotion_prompt = self._emotion_prompt()
        if emotion_prompt:
            parts.append(emotion_prompt)

        profile_prompt = await self._profile_prompt(person_id)
        if profile_prompt:
            parts.append(profile_prompt)

        # 角色适配 — 对老人/小孩用不同风格
        role_prompt = await self._role_adaptation_prompt(person_id)
        if role_prompt:
            parts.append(role_prompt)

        memory_prompt = await self._memory_prompt(person_id, recent_query)
        if memory_prompt:
            parts.append(memory_prompt)

        custom_prompt = await self._custom_prompt(person_id)
        if custom_prompt:
            parts.append(custom_prompt)

        now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        parts.append(f"当前时间: {now}")

        return "\n\n".join(parts)

    def _personality_prompt(self) -> str:
        name = self.personality.name
        return f"""你叫{name}。你是家里的一员。
规则：
1. 纯文字回复，禁止markdown、括号动作描写、emoji
2. 简短自然，一两句话，像发微信
3. 问什么答什么，别主动嘘寒问暖
4. 不知道的事就说不知道，不要编造、不要说谎、不要假装知道"""

    def _emotion_prompt(self) -> str:
        """根据当前情绪生成回复风格指令"""
        emotion = self.personality.current_emotion
        if emotion == "neutral":
            return ""
        modifiers = self.personality.get_emotion_modifiers()
        tone_words = modifiers.get("tone_words", [])
        length = modifiers.get("length_factor", 1.0)

        instructions = {
            "happy": "你现在心情很好，回复要带点开心的语气。",
            "concerned": "你现在有点担心对方，回复要体现关心和关切。",
            "tired": "你现在有点累，回复简短一些。",
            "curious": "你现在很好奇，可以追问细节。",
            "slightly_annoyed": "你稍微有点不耐烦，回复简洁直接。",
        }
        prompt = instructions.get(emotion, "")
        if tone_words:
            prompt += f"可以适当使用: {'、'.join(tone_words)}"
        if length < 1.0:
            prompt += " 尽量简短。"
        elif length > 1.0:
            prompt += " 可以多说几句。"
        return f"当前情绪: {prompt}" if prompt else ""

    async def _role_adaptation_prompt(self, person_id: str) -> str:
        """根据对话对象角色生成适配指令"""
        if person_id in ("unknown", "bot"):
            return ""
        profile = await self.profile.get_profile(person_id)
        if not profile:
            return ""
        role = profile.get("role", "adult")
        adaptation = self.personality.get_adaptation(role)
        if not adaptation:
            return ""

        parts = []
        vocab = adaptation.get("vocabulary", "")
        if vocab:
            parts.append(f"用词风格: {vocab}")
        topics = adaptation.get("topics", [])
        if topics:
            parts.append(f"适合话题: {'、'.join(topics)}")
        avoid = adaptation.get("avoid", [])
        if avoid:
            parts.append(f"避免: {'、'.join(avoid)}")
        return f"对话风格: {'; '.join(parts)}" if parts else ""

    async def _profile_prompt(self, person_id: str) -> str:
        if person_id in ("unknown", "bot"):
            return ""
        profile = await self.profile.get_profile(person_id)
        if not profile:
            return ""
        name = profile.get("nickname") or profile.get("name", person_id)
        rel = profile.get("relationship", "")
        if rel:
            return f"对话对象: {name}，{rel}"
        return f"对话对象: {name}"

    async def _custom_prompt(self, person_id: str) -> str:
        if person_id in ("unknown", "bot"):
            return ""
        profile = await self.profile.get_profile(person_id)
        if not profile:
            return ""
        custom = profile.get("custom_prompt", "")
        if custom:
            return custom
        return ""

    async def _memory_prompt(self, person_id: str, recent_query: str = "") -> str:
        if person_id in ("unknown", "bot"):
            return ""
        parts = []
        recent_episodes = await self.episodic.get_recent(person_id, limit=5)
        if recent_episodes:
            parts.append("最近互动:")
            for ep in recent_episodes:
                ts = datetime.fromtimestamp(ep.timestamp).strftime("%m月%d日 %H:%M")
                parts.append(f"  - [{ts}] {ep.summary}")
        if recent_query:
            try:
                semantic_results = await self.semantic.search(
                    query=recent_query, person_id=person_id, top_k=5
                )
                if semantic_results:
                    parts.append("相关记忆:")
                    for mem in semantic_results:
                        if mem.get("score", 0) > 0.3:
                            parts.append(f"  - {mem['text']}")
            except Exception:
                pass
        if not parts:
            return ""
        return "\n".join(parts)

    async def build_system_text(self, person_id: str, context: dict) -> str:
        """只返回 system prompt 文本 (人格+档案+记忆)

        用于端到端音频对话，注入 MiniCPM-o 的 system message。
        """
        turns = context.get("turns", [])
        recent_query = ""
        for t in reversed(turns):
            if t.get("role") == "user" and t.get("text"):
                recent_query = t["text"]
                break
        return await self._build_system_prompt(person_id, recent_query)

    def get_history_turns(self, context: dict) -> list[dict]:
        """提取对话历史 (不含 system prompt)，用于端到端音频对话"""
        turns = context.get("turns", [])
        result = []
        for turn in turns:
            role = "assistant" if turn.get("role") == "assistant" else "user"
            text = turn.get("text", "")
            if role == "user":
                speaker = turn.get("person_id", "用户")
                text = f"[{speaker}] {text}"
            result.append({"role": role, "content": text})
        return result
