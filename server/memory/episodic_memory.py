"""情景记忆 — 关键事件的结构化存储 (SQLite)"""

import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger("companion_bot.episodic_memory")


@dataclass
class Episode:
    """情景记忆事件"""
    event_id: str
    person_id: str
    timestamp: float
    summary: str
    emotion_tag: str
    importance_score: float


class EpisodicMemory:
    """情景记忆管理器 — SQLite 存储"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    async def initialize(self):
        """创建数据库表"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS episodic_memory (
                event_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                summary TEXT NOT NULL,
                emotion_tag TEXT DEFAULT 'neutral',
                importance_score REAL DEFAULT 0.5
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodic_person
            ON episodic_memory(person_id, timestamp DESC)
        """)
        self.conn.commit()
        logger.info("情景记忆初始化完成")

    async def add_episode(
        self,
        person_id: str,
        summary: str,
        emotion_tag: str = "neutral",
        importance_score: float = 0.5,
    ) -> str:
        """添加一条情景记忆"""
        event_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO episodic_memory
               (event_id, person_id, timestamp, summary, emotion_tag, importance_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, person_id, time.time(), summary, emotion_tag, importance_score),
        )
        self.conn.commit()
        logger.info(
            f"新情景记忆: person={person_id}, "
            f"importance={importance_score:.2f}, summary={summary[:50]}"
        )
        return event_id

    async def get_recent(
        self, person_id: str, limit: int = 5
    ) -> list[Episode]:
        """获取某人最近的情景记忆"""
        cursor = self.conn.execute(
            """SELECT * FROM episodic_memory
               WHERE person_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (person_id, limit),
        )
        rows = cursor.fetchall()
        return [
            Episode(
                event_id=row["event_id"],
                person_id=row["person_id"],
                timestamp=row["timestamp"],
                summary=row["summary"],
                emotion_tag=row["emotion_tag"],
                importance_score=row["importance_score"],
            )
            for row in rows
        ]

    async def search(
        self, person_id: str, keyword: str, limit: int = 10
    ) -> list[Episode]:
        """按关键词搜索情景记忆"""
        cursor = self.conn.execute(
            """SELECT * FROM episodic_memory
               WHERE person_id = ? AND summary LIKE ?
               ORDER BY timestamp DESC LIMIT ?""",
            (person_id, f"%{keyword}%", limit),
        )
        rows = cursor.fetchall()
        return [
            Episode(
                event_id=row["event_id"],
                person_id=row["person_id"],
                timestamp=row["timestamp"],
                summary=row["summary"],
                emotion_tag=row["emotion_tag"],
                importance_score=row["importance_score"],
            )
            for row in rows
        ]

    async def get_important(
        self, person_id: str, min_score: float = 0.6, limit: int = 10
    ) -> list[Episode]:
        """获取重要的情景记忆"""
        cursor = self.conn.execute(
            """SELECT * FROM episodic_memory
               WHERE person_id = ? AND importance_score >= ?
               ORDER BY importance_score DESC, timestamp DESC LIMIT ?""",
            (person_id, min_score, limit),
        )
        rows = cursor.fetchall()
        return [
            Episode(
                event_id=row["event_id"],
                person_id=row["person_id"],
                timestamp=row["timestamp"],
                summary=row["summary"],
                emotion_tag=row["emotion_tag"],
                importance_score=row["importance_score"],
            )
            for row in rows
        ]
