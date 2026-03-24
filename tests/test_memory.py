"""记忆系统测试 — Phase 2 完整覆盖"""

import asyncio
import json

import pytest

from server.memory.episodic_memory import EpisodicMemory
from server.memory.long_term_profile import LongTermProfile
from server.memory.semantic_memory import SemanticMemory
from server.memory.working_memory import WorkingMemory
from server.memory.consolidation import MemoryConsolidation


# ============ Fixtures ============

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


@pytest.fixture
def semantic(tmp_path):
    sm = SemanticMemory(persist_dir=str(tmp_path / "chroma"))
    asyncio.get_event_loop().run_until_complete(sm.initialize())
    return sm


class MockLLMClient:
    """测试用 LLM 客户端 — 返回预设的 JSON 结果"""

    def __init__(self, response: dict | None = None):
        self.response = response
        self.call_count = 0
        self.last_messages = None

    async def chat(self, messages, task_type="daily", **kwargs):
        self.call_count += 1
        self.last_messages = messages
        if self.response is not None:
            return {"content": json.dumps(self.response, ensure_ascii=False)}
        return {"content": ""}


@pytest.fixture
def mock_llm():
    return MockLLMClient(response={
        "summary": "爷爷说膝盖又疼了，情绪有点低落",
        "importance": 0.8,
        "emotion": "concerned",
        "new_interests": [],
        "new_health": ["膝盖不好"],
        "new_concerns": ["膝盖疼痛反复"],
    })


@pytest.fixture
def consolidation_with_llm(db_path, tmp_path, mock_llm):
    """带 LLM 的 consolidation 实例"""
    loop = asyncio.get_event_loop()
    ep = EpisodicMemory(db_path=db_path)
    sm = SemanticMemory(persist_dir=str(tmp_path / "chroma"))
    pf = LongTermProfile(db_path=db_path)
    loop.run_until_complete(ep.initialize())
    loop.run_until_complete(sm.initialize())
    loop.run_until_complete(pf.initialize())
    # 注册一个测试成员
    loop.run_until_complete(pf.add_member(
        person_id="grandpa", name="王爷爷", nickname="爷爷",
        role="elder", age=75, interests=["下棋"],
        health_conditions=["高血压"],
    ))
    return MemoryConsolidation(
        episodic=ep, semantic=sm, profile=pf, llm_client=mock_llm
    ), ep, sm, pf, mock_llm


@pytest.fixture
def consolidation_no_llm(db_path, tmp_path):
    """无 LLM 的 consolidation 实例 (规则回退)"""
    loop = asyncio.get_event_loop()
    ep = EpisodicMemory(db_path=db_path)
    sm = SemanticMemory(persist_dir=str(tmp_path / "chroma"))
    pf = LongTermProfile(db_path=db_path)
    loop.run_until_complete(ep.initialize())
    loop.run_until_complete(sm.initialize())
    loop.run_until_complete(pf.initialize())
    loop.run_until_complete(pf.add_member(
        person_id="grandpa", name="王爷爷", nickname="爷爷",
        role="elder", age=75, interests=["下棋"],
    ))
    return MemoryConsolidation(
        episodic=ep, semantic=sm, profile=pf, llm_client=None
    ), ep, sm, pf


# ============ 情景记忆测试 ============

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


# ============ 长期档案测试 ============

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


# ============ 工作记忆测试 ============

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

    def test_face_result_expiry(self):
        """人脸结果超时后不应被融合使用"""
        import time
        wm = WorkingMemory()
        wm.start_session("s1")
        wm.update_face_result("s1", {"person_id": "grandpa", "score": 0.9})

        # 立即获取应该有
        assert wm.get_latest_face("s1", max_age_sec=5.0) is not None

        # 模拟过期 (用极小的 max_age)
        assert wm.get_latest_face("s1", max_age_sec=0.0) is None


# ============ 语义记忆测试 ============

class TestSemanticMemory:
    def test_add_and_search(self, semantic):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            semantic.add("grandpa", "爷爷今天说膝盖有点疼")
        )
        loop.run_until_complete(
            semantic.add("grandpa", "爷爷和小明下了一盘棋")
        )
        results = loop.run_until_complete(
            semantic.search("膝盖疼痛", person_id="grandpa", top_k=2)
        )
        assert len(results) > 0
        # 膝盖相关的应排在前面
        assert "膝盖" in results[0]["text"]

    def test_search_empty(self, semantic):
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(
            semantic.search("完全不相关的查询", top_k=5)
        )
        assert isinstance(results, list)

    def test_person_filter(self, semantic):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            semantic.add("grandpa", "爷爷喜欢下棋")
        )
        loop.run_until_complete(
            semantic.add("xiaoming", "小明喜欢踢足球")
        )
        results = loop.run_until_complete(
            semantic.search("喜欢什么", person_id="grandpa", top_k=5)
        )
        for r in results:
            assert r["person_id"] == "grandpa"


# ============ 记忆沉淀测试 (LLM) ============

class TestConsolidationWithLLM:
    """测试通过 LLM 驱动的记忆沉淀"""

    def _make_session_data(self, turns):
        person_ids = list({t["person_id"] for t in turns if t["person_id"] != "bot"})
        return {
            "session_id": "test_session",
            "start_time": 1000.0,
            "end_time": 1100.0,
            "turns": turns,
            "person_ids": person_ids + ["bot"],
        }

    def test_llm_called_for_analysis(self, consolidation_with_llm):
        """验证 LLM 被调用进行对话分析"""
        cons, ep, sm, pf, mock_llm = consolidation_with_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "我膝盖又疼了", "role": "user"},
            {"person_id": "bot", "text": "爷爷您注意休息", "role": "assistant"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        # LLM 应该被调用
        assert mock_llm.call_count == 1
        assert mock_llm.last_messages is not None

    def test_episodic_memory_written(self, consolidation_with_llm):
        """验证情景记忆被正确写入"""
        cons, ep, sm, pf, mock_llm = consolidation_with_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "我膝盖又疼了", "role": "user"},
            {"person_id": "bot", "text": "爷爷您注意休息", "role": "assistant"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        episodes = loop.run_until_complete(ep.get_recent("grandpa"))
        assert len(episodes) == 1
        assert episodes[0].summary == "爷爷说膝盖又疼了，情绪有点低落"
        assert episodes[0].importance_score == 0.8
        assert episodes[0].emotion_tag == "concerned"

    def test_semantic_memory_written(self, consolidation_with_llm):
        """验证语义记忆被存入 ChromaDB"""
        cons, ep, sm, pf, mock_llm = consolidation_with_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "我膝盖又疼了", "role": "user"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        # 检索应该能找到
        results = loop.run_until_complete(
            sm.search("膝盖疼", person_id="grandpa")
        )
        assert len(results) > 0

    def test_profile_auto_update(self, consolidation_with_llm):
        """验证 LLM 分析结果自动更新档案"""
        cons, ep, sm, pf, mock_llm = consolidation_with_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "我膝盖又疼了", "role": "user"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        profile_data = loop.run_until_complete(pf.get_profile("grandpa"))
        # mock LLM 返回 new_health = ["膝盖不好"]
        assert "膝盖不好" in profile_data["health_conditions"]

    def test_skip_bot_and_unknown(self, consolidation_with_llm):
        """bot 和 unknown 不应触发 consolidation"""
        cons, ep, sm, pf, mock_llm = consolidation_with_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "unknown", "text": "你好", "role": "user"},
            {"person_id": "bot", "text": "你好!", "role": "assistant"},
        ])
        # 手动设置 person_ids 包含 unknown 和 bot
        session["person_ids"] = ["unknown", "bot"]

        loop.run_until_complete(cons.consolidate(session))
        assert mock_llm.call_count == 0


# ============ 记忆沉淀测试 (规则回退) ============

class TestConsolidationRuleFallback:
    """测试无 LLM 时的规则回退"""

    def _make_session_data(self, turns):
        person_ids = list({t["person_id"] for t in turns if t["person_id"] != "bot"})
        return {
            "session_id": "test_session",
            "turns": turns,
            "person_ids": person_ids + ["bot"],
        }

    def test_health_keyword_high_importance(self, consolidation_no_llm):
        """健康关键词应产生高重要性"""
        cons, ep, sm, pf = consolidation_no_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "我头好晕，不舒服", "role": "user"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        episodes = loop.run_until_complete(ep.get_recent("grandpa"))
        assert len(episodes) == 1
        assert episodes[0].importance_score >= 0.8
        assert episodes[0].emotion_tag == "concerned"

    def test_casual_chat_low_importance(self, consolidation_no_llm):
        """日常闲聊应产生低重要性"""
        cons, ep, sm, pf = consolidation_no_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "今天天气不错", "role": "user"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        episodes = loop.run_until_complete(ep.get_recent("grandpa"))
        assert len(episodes) == 1
        assert episodes[0].importance_score <= 0.4

    def test_rule_based_interest_detection(self, consolidation_no_llm):
        """规则回退也应检测兴趣"""
        cons, ep, sm, pf = consolidation_no_llm
        loop = asyncio.get_event_loop()

        session = self._make_session_data([
            {"person_id": "grandpa", "text": "我最近迷上钓鱼了", "role": "user"},
        ])

        loop.run_until_complete(cons.consolidate(session))

        profile_data = loop.run_until_complete(pf.get_profile("grandpa"))
        assert "钓鱼" in profile_data["interests"]

    def test_llm_failure_falls_back_to_rules(self):
        """LLM 返回无效内容时应回退到规则"""
        loop = asyncio.get_event_loop()
        import tempfile, os
        tmp = tempfile.mkdtemp()

        ep = EpisodicMemory(db_path=os.path.join(tmp, "test.db"))
        sm = SemanticMemory(persist_dir=os.path.join(tmp, "chroma"))
        pf = LongTermProfile(db_path=os.path.join(tmp, "test.db"))
        loop.run_until_complete(ep.initialize())
        loop.run_until_complete(sm.initialize())
        loop.run_until_complete(pf.initialize())
        loop.run_until_complete(pf.add_member(
            person_id="grandpa", name="王爷爷",
        ))

        # LLM 返回垃圾
        bad_llm = MockLLMClient(response=None)
        bad_llm.response = None  # chat() 返回空 content
        cons = MemoryConsolidation(
            episodic=ep, semantic=sm, profile=pf, llm_client=bad_llm
        )

        session = {
            "session_id": "s1",
            "turns": [
                {"person_id": "grandpa", "text": "我头疼", "role": "user"},
            ],
            "person_ids": ["grandpa", "bot"],
        }
        loop.run_until_complete(cons.consolidate(session))

        # 应该走规则回退，仍然能检测到健康关键词
        episodes = loop.run_until_complete(ep.get_recent("grandpa"))
        assert len(episodes) == 1
        assert episodes[0].importance_score >= 0.8


# ============ 多轮记忆召回集成测试 ============

class TestMultiTurnRecall:
    """测试多轮对话的记忆存储与召回"""

    def test_conversation_recall_via_semantic(self, semantic):
        """第一次对话中的信息能在后续被语义检索到"""
        loop = asyncio.get_event_loop()

        # 模拟第一次对话后的记忆沉淀
        loop.run_until_complete(
            semantic.add("grandpa", "爷爷说他最近膝盖疼，走路不太方便")
        )
        loop.run_until_complete(
            semantic.add("grandpa", "爷爷提到孙子下周要高考了，很担心")
        )

        # 模拟第二次对话中检索
        results = loop.run_until_complete(
            semantic.search("膝盖怎么样了", person_id="grandpa", top_k=5)
        )
        assert len(results) > 0
        assert any("膝盖" in r["text"] for r in results)

        # 检索不同话题
        results = loop.run_until_complete(
            semantic.search("考试", person_id="grandpa", top_k=5)
        )
        assert any("高考" in r["text"] for r in results)

    def test_episodic_timeline(self, episodic):
        """情景记忆按时间线正确排序"""
        loop = asyncio.get_event_loop()
        import time

        loop.run_until_complete(
            episodic.add_episode("grandpa", "早上聊天", "neutral", 0.3)
        )
        time.sleep(0.01)  # 确保时间戳不同
        loop.run_until_complete(
            episodic.add_episode("grandpa", "下午说膝盖疼", "concerned", 0.8)
        )
        time.sleep(0.01)
        loop.run_until_complete(
            episodic.add_episode("grandpa", "晚上心情好转", "happy", 0.5)
        )

        recent = loop.run_until_complete(episodic.get_recent("grandpa", limit=3))
        assert len(recent) == 3
        # 最新的在前
        assert recent[0].summary == "晚上心情好转"
        assert recent[-1].summary == "早上聊天"

    def test_profile_accumulates_across_sessions(self, consolidation_no_llm):
        """多次对话的档案更新应该累积"""
        cons, ep, sm, pf = consolidation_no_llm
        loop = asyncio.get_event_loop()

        # 第一次对话: 发现兴趣
        session1 = {
            "session_id": "s1",
            "turns": [
                {"person_id": "grandpa", "text": "我最近喜欢上种花了", "role": "user"},
            ],
            "person_ids": ["grandpa", "bot"],
        }
        loop.run_until_complete(cons.consolidate(session1))

        # 第二次对话: 发现健康信息
        session2 = {
            "session_id": "s2",
            "turns": [
                {"person_id": "grandpa", "text": "医生说我血糖有点高", "role": "user"},
            ],
            "person_ids": ["grandpa", "bot"],
        }
        loop.run_until_complete(cons.consolidate(session2))

        profile_data = loop.run_until_complete(pf.get_profile("grandpa"))
        # 原有兴趣 + 新兴趣
        assert "下棋" in profile_data["interests"]
        assert "种花" in profile_data["interests"]
        # 新健康信息
        assert "血糖问题" in profile_data["health_conditions"]

    def test_end_to_end_working_memory_to_recall(self, consolidation_no_llm, semantic):
        """端到端: 工作记忆 → 记忆沉淀 → 语义检索"""
        cons, ep, sm, pf = consolidation_no_llm
        loop = asyncio.get_event_loop()

        # 1. 模拟工作记忆中的对话
        wm = WorkingMemory()
        wm.start_session("test_client")
        wm.add_turn("test_client", "grandpa", "小伴，我孙子要高考了", "user")
        wm.add_turn("test_client", "bot", "爷爷别担心，小明一定能考好的", "assistant")
        wm.add_turn("test_client", "grandpa", "希望吧，我天天帮他烧好吃的", "user")

        # 2. 结束会话 → 触发沉淀
        session_data = wm.end_session("test_client")
        loop.run_until_complete(cons.consolidate(session_data))

        # 3. 验证情景记忆已写入
        episodes = loop.run_until_complete(ep.get_recent("grandpa"))
        assert len(episodes) >= 1

        # 4. 验证语义记忆可检索
        # 使用 consolidation 的 semantic 实例 (和 fixture 不同)
        results = loop.run_until_complete(
            cons.semantic.search("高考", person_id="grandpa")
        )
        assert len(results) > 0
