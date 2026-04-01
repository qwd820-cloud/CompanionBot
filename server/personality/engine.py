"""人格引擎 + 情绪状态机 (支持持久化)"""

import logging
import sqlite3
import time

from server.utils.keywords import (
    CURIOUS_KEYWORDS,
    HEALTH_KEYWORDS,
    POSITIVE_EMOTION_KEYWORDS,
    match_any_keyword,
)

logger = logging.getLogger("companion_bot.personality")

VALID_EMOTIONS = {
    "neutral",
    "happy",
    "concerned",
    "tired",
    "curious",
    "slightly_annoyed",
}

# 情绪自动恢复到 neutral 的轮次数
EMOTION_DECAY_TURNS = 5


class PersonalityEngine:
    """人格引擎 — 管理机器人的性格特质和情绪状态

    情绪状态支持持久化到 SQLite，跨会话延续。
    """

    def __init__(self, config: dict, db_path: str | None = None):
        personality = config.get("personality", {})
        self.name = personality.get("name", "小伴")
        self.traits = personality.get("traits", {})
        self.quirks = personality.get("quirks", [])
        self.adaptation = config.get("adaptation", {})

        self.current_emotion = "neutral"
        self._emotion_turns = 0
        self._last_interaction_time = time.time()
        self._interrupt_count = 0
        self._db_path = db_path

        # 从 DB 恢复上次状态
        if db_path:
            self._restore_emotion()
            self._restore_traits()

    def _restore_emotion(self):
        """从 SQLite 恢复上次保存的情绪状态"""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS emotion_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    emotion TEXT NOT NULL DEFAULT 'neutral',
                    updated_at REAL NOT NULL
                )"""
            )
            conn.commit()
            row = conn.execute(
                "SELECT emotion, updated_at FROM emotion_state WHERE id=1"
            ).fetchone()
            if row:
                emotion, updated_at = row
                age_hours = (time.time() - updated_at) / 3600
                if emotion in VALID_EMOTIONS and age_hours < 24:
                    self.current_emotion = emotion
                    logger.info(f"恢复情绪状态: {emotion} ({age_hours:.1f}h 前)")
                else:
                    logger.info(f"情绪状态已过期 ({age_hours:.0f}h)，重置为 neutral")
            conn.close()
        except Exception as e:
            logger.warning(f"恢复情绪状态失败: {e}")

    def _save_emotion(self):
        """保存当前情绪到 SQLite"""
        if not self._db_path:
            return
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """INSERT INTO emotion_state (id, emotion, updated_at)
                   VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET emotion=excluded.emotion, updated_at=excluded.updated_at""",
                (self.current_emotion, time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存情绪状态失败: {e}")

    def update_emotion(self, context: dict, reply_text: str):
        """根据对话上下文更新情绪状态"""
        turns = context.get("turns", [])
        if not turns:
            return

        latest_user_turn = None
        for t in reversed(turns):
            if t.get("role") == "user":
                latest_user_turn = t
                break

        if latest_user_turn is None:
            return

        text = latest_user_turn.get("text", "")
        new_emotion = self._infer_emotion(text)

        if new_emotion != self.current_emotion:
            logger.info(f"情绪变化: {self.current_emotion} → {new_emotion}")
            self.current_emotion = new_emotion
            self._emotion_turns = 0
        else:
            self._emotion_turns += 1

        # 情绪衰减: 非 neutral 情绪在若干轮后恢复
        if (
            self.current_emotion != "neutral"
            and self._emotion_turns >= EMOTION_DECAY_TURNS
        ):
            logger.info(f"情绪恢复: {self.current_emotion} → neutral")
            self.current_emotion = "neutral"
            self._emotion_turns = 0

        self._last_interaction_time = time.time()
        self._save_emotion()

    def _infer_emotion(self, text: str) -> str:
        """从用户文本推断应有的情绪反应"""
        if match_any_keyword(text, HEALTH_KEYWORDS):
            return "concerned"
        if match_any_keyword(text, POSITIVE_EMOTION_KEYWORDS):
            return "happy"
        if match_any_keyword(text, CURIOUS_KEYWORDS):
            return "curious"

        idle_seconds = time.time() - self._last_interaction_time
        if idle_seconds > 3600:
            return "tired"

        return self.current_emotion

    def register_interruption(self):
        """记录被打断"""
        self._interrupt_count += 1
        if self._interrupt_count >= 3:
            self.current_emotion = "slightly_annoyed"
            self._emotion_turns = 0
            self._save_emotion()

    def get_emotion_modifiers(self) -> dict:
        """获取当前情绪对回复的影响参数"""
        modifiers = {
            "neutral": {
                "tone_words": [],
                "length_factor": 1.0,
                "topic_tendency": "follow",
            },
            "happy": {
                "tone_words": ["哈哈", "太好了", "真棒"],
                "length_factor": 1.1,
                "topic_tendency": "enthusiastic",
            },
            "concerned": {
                "tone_words": ["嗯...", "您注意", "要小心"],
                "length_factor": 1.0,
                "topic_tendency": "caring",
            },
            "tired": {
                "tone_words": ["嗯", "好的"],
                "length_factor": 0.7,
                "topic_tendency": "brief",
            },
            "curious": {
                "tone_words": ["真的吗", "然后呢", "好有趣"],
                "length_factor": 1.2,
                "topic_tendency": "inquiring",
            },
            "slightly_annoyed": {
                "tone_words": ["好吧", "嗯"],
                "length_factor": 0.8,
                "topic_tendency": "reserved",
            },
        }
        return modifiers.get(self.current_emotion, modifiers["neutral"])

    def get_adaptation(self, role: str) -> dict:
        """获取对话对象适配参数"""
        return self.adaptation.get(role, {})

    # ====================== 人格进化 ======================

    def evolve_traits(self, interaction_signals: dict):
        """根据互动信号缓慢调整人格特质

        interaction_signals: {
            "humor_positive": True/False,  # 用户对幽默的反应
            "brevity_preferred": True/False,  # 用户喜欢简短回复
            "curiosity_engaged": True/False,  # 用户回应了追问
            "warmth_appreciated": True/False,  # 用户感谢关心
        }

        变化极慢 (α=0.01)，防止人格突变。
        """
        alpha = 0.01  # 每次调整幅度

        if interaction_signals.get("humor_positive"):
            self.traits["humor"] = min(1.0, self.traits.get("humor", 0.6) + alpha)
        elif interaction_signals.get("humor_positive") is False:
            self.traits["humor"] = max(0.0, self.traits.get("humor", 0.6) - alpha)

        if interaction_signals.get("brevity_preferred"):
            self.traits["directness"] = min(
                1.0, self.traits.get("directness", 0.5) + alpha
            )

        if interaction_signals.get("curiosity_engaged"):
            self.traits["curiosity"] = min(
                1.0, self.traits.get("curiosity", 0.7) + alpha
            )

        if interaction_signals.get("warmth_appreciated"):
            self.traits["warmth"] = min(1.0, self.traits.get("warmth", 0.85) + alpha)

        self._save_traits()

    def _save_traits(self):
        """持久化当前 traits 到 SQLite"""
        if not self._db_path:
            return
        try:
            import json

            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS personality_traits (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    traits_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            conn.execute(
                """INSERT INTO personality_traits (id, traits_json, updated_at)
                   VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET traits_json=excluded.traits_json, updated_at=excluded.updated_at""",
                (json.dumps(self.traits), time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存 traits 失败: {e}")

    def _restore_traits(self):
        """从 SQLite 恢复 traits (如果有)"""
        if not self._db_path:
            return
        try:
            import json

            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS personality_traits (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    traits_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            row = conn.execute(
                "SELECT traits_json FROM personality_traits WHERE id=1"
            ).fetchone()
            if row:
                saved_traits = json.loads(row[0])
                self.traits.update(saved_traits)
                logger.info(f"恢复 traits: {saved_traits}")
            conn.close()
        except Exception as e:
            logger.warning(f"恢复 traits 失败: {e}")
