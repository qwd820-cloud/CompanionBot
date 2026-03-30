"""Prompt 组装器 — 极简版，适配 4B 小模型"""

import logging
from datetime import datetime

from server.memory.episodic_memory import EpisodicMemory
from server.memory.long_term_profile import LongTermProfile
from server.memory.semantic_memory import SemanticMemory
from server.personality.engine import PersonalityEngine

logger = logging.getLogger("companion_bot.prompt_builder")


class PromptBuilder:
    def __init__(self, personality: PersonalityEngine, episodic: EpisodicMemory,
                 semantic: SemanticMemory, profile: LongTermProfile):
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

        profile_prompt = await self._profile_prompt(person_id)
        if profile_prompt:
            parts.append(profile_prompt)

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
                    query=recent_query, person_id=person_id, top_k=5)
                if semantic_results:
                    parts.append("相关记忆:")
                    for mem in semantic_results:
                        if mem.get("score", 0) > 0.3:
                            parts.append(f"  - {mem[text]}")
            except Exception:
                pass
        if not parts:
            return ""
        return "\n".join(parts)
