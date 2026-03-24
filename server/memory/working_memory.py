"""工作记忆 — 当前对话上下文管理"""

import logging
import time
from dataclasses import dataclass, field

from server.utils.keywords import WAKE_WORDS

logger = logging.getLogger("companion_bot.working_memory")

MAX_TURNS = 20


@dataclass
class Turn:
    """对话轮次"""
    person_id: str
    text: str
    role: str  # "user" 或 "assistant"
    timestamp: float = field(default_factory=time.time)


@dataclass
class Session:
    """对话会话"""
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    latest_face: dict | None = None
    latest_face_time: float = 0.0
    active_person_ids: set[str] = field(default_factory=set)


class WorkingMemory:
    """工作记忆管理器，维护所有活跃会话的对话上下文"""

    def __init__(self, max_turns: int = MAX_TURNS):
        self.max_turns = max_turns
        self.sessions: dict[str, Session] = {}

    def start_session(self, session_id: str):
        """开始新的对话会话"""
        self.sessions[session_id] = Session(session_id=session_id)
        logger.info(f"新会话开始: {session_id}")

    def end_session(self, session_id: str) -> dict | None:
        """结束会话，返回会话数据用于记忆沉淀"""
        session = self.sessions.pop(session_id, None)
        if session is None:
            return None

        return {
            "session_id": session.session_id,
            "start_time": session.start_time,
            "end_time": time.time(),
            "turns": [self._turn_to_dict(t) for t in session.turns],
            "person_ids": list(session.active_person_ids),
        }

    def add_turn(
        self, session_id: str, person_id: str, text: str, role: str
    ):
        """添加一轮对话"""
        session = self.sessions.get(session_id)
        if session is None:
            self.start_session(session_id)
            session = self.sessions[session_id]

        turn = Turn(
            person_id=person_id, text=text, role=role
        )
        session.turns.append(turn)
        session.active_person_ids.add(person_id)

        # 保留最近 max_turns 轮
        if len(session.turns) > self.max_turns:
            session.turns = session.turns[-self.max_turns:]

    def get_context(self, session_id: str) -> dict:
        """获取当前会话的上下文信息"""
        session = self.sessions.get(session_id)
        if session is None:
            return {"turns": [], "person_ids": []}

        return {
            "session_id": session.session_id,
            "turns": [self._turn_to_dict(t) for t in session.turns],
            "person_ids": list(session.active_person_ids),
            "latest_face": session.latest_face,
        }

    def get_latest_face(
        self, session_id: str, max_age_sec: float = 5.0
    ) -> dict | None:
        """
        获取最近的人脸识别结果。
        max_age_sec: 结果的最大有效期 (秒)，超时则视为过期不参与融合。
        """
        session = self.sessions.get(session_id)
        if session and session.latest_face:
            age = time.time() - session.latest_face_time
            if age <= max_age_sec:
                return session.latest_face
        return None

    def update_face_result(self, session_id: str, face_result: dict):
        """更新最近的人脸识别结果 (带时间戳)"""
        session = self.sessions.get(session_id)
        if session:
            session.latest_face = face_result
            session.latest_face_time = time.time()

    def is_addressed_to_bot(self, session_id: str, text: str) -> bool:
        """判断当前说话是否在对机器人说话"""
        text_lower = text.lower()

        # 检查唤醒词
        for wake_word in WAKE_WORDS:
            if wake_word in text_lower:
                return True

        # 如果最近一轮是机器人的回复，默认认为在与机器人对话
        session = self.sessions.get(session_id)
        if session and session.turns:
            recent = session.turns[-1]
            if recent.role == "assistant":
                return True

        # 如果只有一个人在说话 (没有其他人), 默认对机器人说话
        if session and len(session.active_person_ids) <= 1:
            return True

        return False

    @staticmethod
    def _turn_to_dict(t: "Turn") -> dict:
        return {
            "person_id": t.person_id,
            "text": t.text,
            "role": t.role,
            "timestamp": t.timestamp,
        }

    def get_recent_text(self, session_id: str, n: int = 5) -> str:
        """获取最近 n 轮的文本，用于插话决策"""
        session = self.sessions.get(session_id)
        if not session:
            return ""
        recent = session.turns[-n:]
        return "\n".join(f"{t.person_id}: {t.text}" for t in recent)
