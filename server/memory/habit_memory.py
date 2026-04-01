"""习惯记忆 (Procedural Memory) — 追踪用户重复行为模式

识别并记录家庭成员的行为习惯:
- 时间模式: "每天 7 点问天气"、"周末下午聊孙子"
- 话题偏好: "经常聊健康"、"喜欢讨论做菜"
- 交互模式: "喜欢简短回复"、"爱追问细节"
"""

import logging
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger("companion_bot.habit_memory")


@dataclass
class Habit:
    habit_id: int
    person_id: str
    pattern: str  # 习惯描述
    category: str  # time_based / topic / interaction_style
    frequency: int  # 观察到的次数
    confidence: float  # 0.0-1.0
    last_observed: float  # Unix timestamp
    created_at: float


class HabitMemory:
    """习惯记忆存储 — SQLite 后端"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    async def initialize(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS habits (
                habit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT NOT NULL,
                pattern TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'topic',
                frequency INTEGER NOT NULL DEFAULT 1,
                confidence REAL NOT NULL DEFAULT 0.1,
                last_observed REAL NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(person_id, pattern)
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_habits_person ON habits(person_id)"
        )
        self.conn.commit()
        logger.info("习惯记忆初始化完成")

    async def observe(self, person_id: str, pattern: str, category: str = "topic"):
        """记录一次习惯观察 — 已有则累加频率+更新置信度，否则新建"""
        if not self.conn:
            return
        now = time.time()
        existing = self.conn.execute(
            "SELECT habit_id, frequency, confidence FROM habits WHERE person_id=? AND pattern=?",
            (person_id, pattern),
        ).fetchone()

        if existing:
            new_freq = existing["frequency"] + 1
            # 置信度随频率增长: 快速到 0.5，缓慢趋近 1.0
            new_conf = min(1.0, new_freq / (new_freq + 5))
            self.conn.execute(
                "UPDATE habits SET frequency=?, confidence=?, last_observed=? WHERE habit_id=?",
                (new_freq, new_conf, now, existing["habit_id"]),
            )
        else:
            self.conn.execute(
                """INSERT INTO habits (person_id, pattern, category, frequency, confidence, last_observed, created_at)
                   VALUES (?, ?, ?, 1, 0.1, ?, ?)""",
                (person_id, pattern, category, now, now),
            )
        self.conn.commit()

    async def get_habits(
        self, person_id: str, min_confidence: float = 0.3, limit: int = 10
    ) -> list[Habit]:
        """获取某人的高置信度习惯"""
        if not self.conn:
            return []
        rows = self.conn.execute(
            """SELECT * FROM habits
               WHERE person_id=? AND confidence>=?
               ORDER BY confidence DESC, frequency DESC
               LIMIT ?""",
            (person_id, min_confidence, limit),
        ).fetchall()
        return [
            Habit(
                habit_id=r["habit_id"],
                person_id=r["person_id"],
                pattern=r["pattern"],
                category=r["category"],
                frequency=r["frequency"],
                confidence=r["confidence"],
                last_observed=r["last_observed"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def decay(self, days_threshold: int = 60, decay_factor: float = 0.8):
        """衰减长期未观察到的习惯"""
        if not self.conn:
            return
        cutoff = time.time() - days_threshold * 86400
        self.conn.execute(
            """UPDATE habits SET confidence = confidence * ?
               WHERE last_observed < ? AND confidence > 0.05""",
            (decay_factor, cutoff),
        )
        # 删除极低置信度的习惯
        deleted = self.conn.execute(
            "DELETE FROM habits WHERE confidence < 0.05"
        ).rowcount
        self.conn.commit()
        if deleted:
            logger.info(f"清理 {deleted} 条低置信度习惯")

    async def get_all_for_prompt(self, person_id: str) -> str:
        """获取用于注入 prompt 的习惯摘要"""
        habits = await self.get_habits(person_id, min_confidence=0.3, limit=5)
        if not habits:
            return ""
        lines = ["已知习惯:"]
        for h in habits:
            lines.append(f"  - {h.pattern} (观察{h.frequency}次)")
        return "\n".join(lines)
