"""长期档案 — 家庭成员持久化画像 (SQLite)"""

import json
import logging
import sqlite3
import time

logger = logging.getLogger("companion_bot.long_term_profile")


class LongTermProfile:
    """家庭成员长期档案管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    async def initialize(self):
        """创建数据库表"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS family_profiles (
                person_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                nickname TEXT,
                role TEXT DEFAULT 'adult',
                age INTEGER,
                relationship TEXT,
                interests TEXT DEFAULT '[]',
                health_conditions TEXT DEFAULT '[]',
                communication_preferences TEXT DEFAULT '{}',
                recent_concerns TEXT DEFAULT '[]',
                voiceprint_enrolled INTEGER DEFAULT 0,
                face_enrolled INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            )
        """)
        self.conn.commit()
        logger.info("长期档案初始化完成")

    async def add_member(
        self,
        person_id: str,
        name: str,
        nickname: str = "",
        role: str = "adult",
        age: int = 0,
        relationship: str = "",
        interests: list[str] | None = None,
        health_conditions: list[str] | None = None,
        communication_preferences: dict | None = None,
    ):
        """注册新家庭成员"""
        now = time.time()
        self.conn.execute(
            """INSERT OR REPLACE INTO family_profiles
               (person_id, name, nickname, role, age, relationship,
                interests, health_conditions, communication_preferences,
                recent_concerns, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)""",
            (
                person_id, name, nickname, role, age, relationship,
                json.dumps(interests or [], ensure_ascii=False),
                json.dumps(health_conditions or [], ensure_ascii=False),
                json.dumps(communication_preferences or {}, ensure_ascii=False),
                now, now,
            ),
        )
        self.conn.commit()
        logger.info(f"新成员注册: {person_id} ({name})")

    async def get_profile(self, person_id: str) -> dict | None:
        """获取成员档案"""
        cursor = self.conn.execute(
            "SELECT * FROM family_profiles WHERE person_id = ?",
            (person_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return {
            "person_id": row["person_id"],
            "name": row["name"],
            "nickname": row["nickname"],
            "role": row["role"],
            "age": row["age"],
            "relationship": row["relationship"],
            "interests": json.loads(row["interests"]),
            "health_conditions": json.loads(row["health_conditions"]),
            "communication_preferences": json.loads(
                row["communication_preferences"]
            ),
            "recent_concerns": json.loads(row["recent_concerns"]),
        }

    async def update_interests(self, person_id: str, new_interests: list[str]):
        """更新兴趣爱好 (合并)"""
        profile = await self.get_profile(person_id)
        if profile is None:
            return
        existing = set(profile["interests"])
        existing.update(new_interests)
        self.conn.execute(
            """UPDATE family_profiles
               SET interests = ?, updated_at = ?
               WHERE person_id = ?""",
            (json.dumps(list(existing), ensure_ascii=False), time.time(), person_id),
        )
        self.conn.commit()

    async def update_health(self, person_id: str, conditions: list[str]):
        """更新健康状况 (合并)"""
        profile = await self.get_profile(person_id)
        if profile is None:
            return
        existing = set(profile["health_conditions"])
        existing.update(conditions)
        self.conn.execute(
            """UPDATE family_profiles
               SET health_conditions = ?, updated_at = ?
               WHERE person_id = ?""",
            (json.dumps(list(existing), ensure_ascii=False), time.time(), person_id),
        )
        self.conn.commit()

    async def update_concerns(self, person_id: str, concerns: list[str]):
        """更新近期关注"""
        self.conn.execute(
            """UPDATE family_profiles
               SET recent_concerns = ?, updated_at = ?
               WHERE person_id = ?""",
            (json.dumps(concerns, ensure_ascii=False), time.time(), person_id),
        )
        self.conn.commit()

    async def get_all_members(self) -> list[dict]:
        """获取所有成员列表"""
        cursor = self.conn.execute("SELECT person_id, name, role FROM family_profiles")
        return [dict(row) for row in cursor.fetchall()]
