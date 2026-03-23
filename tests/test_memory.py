"""记忆系统测试"""

import asyncio
import tempfile

import pytest

from server.memory.episodic_memory import EpisodicMemory
from server.memory.long_term_profile import LongTermProfile
from server.memory.working_memory import WorkingMemory


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def episodic(db_path):
    em = EpisodicMemory(db_path=db_path)
    asyncio.get_event_loop().run_until_complete(em.initialize())
    return em


@pytest.fixture
def profile(db_path):
    p = LongTermProfile(db_path=db_path)
    asyncio.get_event_loop().run_until_complete(p.initialize())
    return p


class TestEpisodicMemory:
    def test_add_and_retrieve(self, episodic):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            episodic.add_episode("grandpa", "爷爷膝盖疼", "concerned", 0.8)
        )
        recent = loop.run_until_complete(episodic.get_recent("grandpa"))
        assert len(recent) == 1
        assert recent[0].summary == "爷爷膝盖疼"
        assert recent[0].importance_score == 0.8

    def test_importance_filter(self, episodic):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            episodic.add_episode("grandpa", "日常聊天", "neutral", 0.2)
        )
        loop.run_until_complete(
            episodic.add_episode("grandpa", "健康问题", "concerned", 0.9)
        )
        important = loop.run_until_complete(
            episodic.get_important("grandpa", min_score=0.6)
        )
        assert len(important) == 1
        assert important[0].summary == "健康问题"

    def test_search(self, episodic):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            episodic.add_episode("grandpa", "爷爷说想下棋", "happy", 0.4)
        )
        results = loop.run_until_complete(
            episodic.search("grandpa", "下棋")
        )
        assert len(results) == 1


class TestLongTermProfile:
    def test_add_and_get(self, profile):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(profile.add_member(
            person_id="grandpa",
            name="王爷爷",
            nickname="爷爷",
            role="elder",
            age=75,
            interests=["下棋"],
        ))
        p = loop.run_until_complete(profile.get_profile("grandpa"))
        assert p is not None
        assert p["name"] == "王爷爷"
        assert "下棋" in p["interests"]

    def test_update_interests(self, profile):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(profile.add_member(
            person_id="grandpa", name="王爷爷", interests=["下棋"]
        ))
        loop.run_until_complete(
            profile.update_interests("grandpa", ["种花"])
        )
        p = loop.run_until_complete(profile.get_profile("grandpa"))
        assert "下棋" in p["interests"]
        assert "种花" in p["interests"]

    def test_update_health(self, profile):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(profile.add_member(
            person_id="grandpa", name="王爷爷", health_conditions=["高血压"]
        ))
        loop.run_until_complete(
            profile.update_health("grandpa", ["膝盖不好"])
        )
        p = loop.run_until_complete(profile.get_profile("grandpa"))
        assert "高血压" in p["health_conditions"]
        assert "膝盖不好" in p["health_conditions"]


class TestWorkingMemory:
    def test_session_lifecycle(self):
        wm = WorkingMemory()
        wm.start_session("s1")
        wm.add_turn("s1", "grandpa", "你好", "user")
        wm.add_turn("s1", "bot", "爷爷好！", "assistant")

        ctx = wm.get_context("s1")
        assert len(ctx["turns"]) == 2

        data = wm.end_session("s1")
        assert data is not None
        assert len(data["turns"]) == 2

    def test_wake_word_detection(self):
        wm = WorkingMemory()
        wm.start_session("s1")
        assert wm.is_addressed_to_bot("s1", "小伴，今天天气好吗")
        assert wm.is_addressed_to_bot("s1", "机器人帮我查一下")

    def test_max_turns(self):
        wm = WorkingMemory(max_turns=5)
        wm.start_session("s1")
        for i in range(10):
            wm.add_turn("s1", "user1", f"消息{i}", "user")
        ctx = wm.get_context("s1")
        assert len(ctx["turns"]) == 5
