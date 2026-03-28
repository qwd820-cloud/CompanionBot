"""主动行为调度器测试"""

import asyncio
import time

import pytest

from server.personality.proactive import ProactiveScheduler, ScheduledAction


class TestProactiveScheduler:
    """测试主动行为调度"""

    def test_activity_tracking(self):
        """记录用户活动时间"""
        scheduler = ProactiveScheduler()
        scheduler.update_activity("grandpa")
        assert "grandpa" in scheduler._last_activity
        assert time.time() - scheduler._last_activity["grandpa"] < 1

    def test_schedule_greeting(self):
        """安排每日问候"""
        scheduler = ProactiveScheduler()
        scheduler.schedule_greeting("grandpa", "爷爷")
        # 应该有 2 个行为: 早安 + 晚安
        assert len(scheduler._actions) == 2
        types = {a.action_id for a in scheduler._actions}
        assert "morning_grandpa" in types
        assert "evening_grandpa" in types
        # 都是重复的
        assert all(a.repeating for a in scheduler._actions)

    def test_schedule_medication(self):
        """安排用药提醒"""
        scheduler = ProactiveScheduler()
        scheduler.schedule_medication("grandpa", "爷爷", "降压药", [8, 20])
        assert len(scheduler._actions) == 2
        assert all(a.action_type == "medication" for a in scheduler._actions)
        assert all("降压药" in a.message for a in scheduler._actions)

    def test_schedule_followup(self):
        """安排延迟关怀"""
        scheduler = ProactiveScheduler()
        scheduler.schedule_followup("grandpa", "爷爷，膝盖好点了吗？", delay_hours=1)
        assert len(scheduler._actions) == 1
        a = scheduler._actions[0]
        assert a.action_type == "followup"
        assert not a.repeating
        # 触发时间应在 ~1 小时后
        assert a.trigger_time > time.time() + 3500

    def test_idle_detection(self):
        """检测久未互动"""
        scheduler = ProactiveScheduler(idle_threshold_minutes=0.01)  # 0.6 秒
        scheduler.update_activity("grandpa")
        # 刚活动过，不应触发
        idle = scheduler._check_idle_persons()
        assert len(idle) == 0

        # 模拟过了足够久
        scheduler._last_activity["grandpa"] = time.time() - 10
        idle = scheduler._check_idle_persons()
        assert len(idle) == 1
        assert idle[0][0] == "grandpa"

    def test_idle_cooldown(self):
        """久未互动关怀不应过于频繁"""
        scheduler = ProactiveScheduler(idle_threshold_minutes=0.01)
        scheduler._last_activity["grandpa"] = time.time() - 10
        # 第一次
        idle = scheduler._check_idle_persons()
        assert len(idle) == 1
        # 第二次 (已标记关怀过)
        scheduler._last_activity["grandpa"] = time.time() - 10
        idle = scheduler._check_idle_persons()
        assert len(idle) == 0  # cooldown 内不重复

    @pytest.mark.asyncio
    async def test_tick_executes_action(self):
        """到期行为应被执行"""
        scheduler = ProactiveScheduler()
        sent_messages = []

        async def mock_send(person_id, message, action_type):
            sent_messages.append((person_id, message, action_type))

        scheduler.set_send_callback(mock_send)

        # 添加一个已到期的行为
        scheduler._actions.append(
            ScheduledAction(
                action_id="test",
                person_id="grandpa",
                message="你好呀",
                trigger_time=time.time() - 1,  # 已到期
                action_type="greeting",
                repeating=False,
            )
        )

        await scheduler._tick()
        assert len(sent_messages) == 1
        assert sent_messages[0] == ("grandpa", "你好呀", "greeting")

    @pytest.mark.asyncio
    async def test_repeating_reschedules(self):
        """重复行为执行后应重新调度"""
        scheduler = ProactiveScheduler()
        scheduler.set_send_callback(lambda *a: asyncio.coroutine(lambda: None)())

        scheduler._actions.append(
            ScheduledAction(
                action_id="daily",
                person_id="grandpa",
                message="早安",
                trigger_time=time.time() - 1,
                action_type="greeting",
                repeating=True,
                interval_seconds=86400,
            )
        )

        await scheduler._tick()
        # 行为不应被清除 (repeating)
        assert len(scheduler._actions) == 1
        # trigger_time 应被更新到未来
        assert scheduler._actions[0].trigger_time > time.time()
