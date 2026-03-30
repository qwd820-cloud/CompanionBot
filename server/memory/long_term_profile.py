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
                custom_prompt TEXT DEFAULT '',
                voiceprint_enrolled INTEGER DEFAULT 0,
                face_enrolled INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            )
        """)
        # 兼容旧数据库: 如果表已存在但缺少 custom_prompt 列，自动添加
        try:
            self.conn.execute(
                "ALTER TABLE family_profiles ADD COLUMN custom_prompt TEXT DEFAULT ''"
            )
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # 列已存在
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
                person_id,
                name,
                nickname,
                role,
                age,
                relationship,
                json.dumps(interests or [], ensure_ascii=False),
                json.dumps(health_conditions or [], ensure_ascii=False),
                json.dumps(communication_preferences or {}, ensure_ascii=False),
                now,
                now,
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

        d = dict(row)
        return {
            "person_id": d["person_id"],
            "name": d["name"],
            "nickname": d["nickname"],
            "role": d["role"],
            "age": d["age"],
            "relationship": d["relationship"],
            "interests": json.loads(d["interests"]),
            "health_conditions": json.loads(d["health_conditions"]),
            "communication_preferences": json.loads(d["communication_preferences"]),
            "recent_concerns": json.loads(d["recent_concerns"]),
            "custom_prompt": d.get("custom_prompt", ""),
        }

    async def update_member(self, person_id: str, **fields) -> bool:
        """更新成员基本信息，只更新提供的字段

        支持的字段: name, nickname, role, age, relationship
        返回 True 表示已更新，False 表示未找到成员
        """
        allowed = {"name", "nickname", "role", "age", "relationship"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            logger.warning(f"update_member: 无有效字段可更新 ({person_id})")
            return False

        set_clauses = [f"{col} = ?" for col in updates]
        set_clauses.append("updated_at = ?")
        values = list(updates.values())
        values.append(time.time())
        values.append(person_id)

        sql = f"UPDATE family_profiles SET {', '.join(set_clauses)} WHERE person_id = ?"
        cursor = self.conn.execute(sql, values)
        self.conn.commit()

        if cursor.rowcount > 0:
            logger.info(f"成员信息已更新: {person_id} (字段: {list(updates.keys())})")
            return True
        else:
            logger.warning(f"更新失败，成员不存在: {person_id}")
            return False


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

    async def update_custom_prompt(self, person_id: str, custom_prompt: str):
        """更新成员的自定义提示词"""
        self.conn.execute(
            """UPDATE family_profiles
               SET custom_prompt = ?, updated_at = ?
               WHERE person_id = ?""",
            (custom_prompt, time.time(), person_id),
        )
        self.conn.commit()
        logger.info(f"更新自定义提示词: {person_id} ({len(custom_prompt)} 字)")

    async def delete_member(self, person_id: str) -> bool:
        """删除家庭成员，返回 True 表示已删除，False 表示未找到"""
        cursor = self.conn.execute(
            "DELETE FROM family_profiles WHERE person_id = ?",
            (person_id,),
        )
        self.conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"成员已删除: {person_id}")
        else:
            logger.warning(f"删除失败，成员不存在: {person_id}")
        return deleted

    async def get_all_members(self) -> list[dict]:
        """获取所有成员列表"""
        cursor = self.conn.execute("SELECT person_id, name, role FROM family_profiles")
        return [dict(row) for row in cursor.fetchall()]
