"""主动行为调度器 — 让机器人不只是被动回复，而是主动关怀

支持:
- 定时问候 (早安/晚安)
- 用药提醒
- 久未互动主动关怀
- 基于记忆的关心 (如: 知道老人膝盖疼，第二天主动问)
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("companion_bot.proactive")


@dataclass
class ScheduledAction:
    """一个待执行的主动行为"""

    action_id: str
    person_id: str
    message: str
    trigger_time: float  # Unix timestamp
    action_type: str  # "greeting", "medication", "care", "followup"
    priority: str = "P3"  # 默认低优先级
    repeating: bool = False  # 是否重复
    interval_seconds: float = 0  # 重复间隔
    executed: bool = False


class ProactiveScheduler:
    """主动行为调度器 — 后台运行，定时触发关怀行为"""

    def __init__(
        self,
        idle_threshold_minutes: float = 60,
        greeting_hours: tuple[int, int] = (8, 21),
    ):
        self.idle_threshold = idle_threshold_minutes * 60
        self.morning_hour, self.evening_hour = greeting_hours
        self._actions: list[ScheduledAction] = []
        self._last_activity: dict[str, float] = {}  # person_id → timestamp
        self._last_greeting: dict[str, float] = {}  # person_id → timestamp
        self._last_idle_care: dict[str, float] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._send_callback = None  # 发送消息的回调

    def set_send_callback(self, callback):
        """设置发送消息的回调函数: async def callback(person_id, message, action_type)"""
        self._send_callback = callback

    def update_activity(self, person_id: str):
        """记录用户活动 (有人说话/操作时调用)"""
        self._last_activity[person_id] = time.time()

    # ------------------------------------------------------------------
    # 定时问候
    # ------------------------------------------------------------------
    def schedule_greeting(self, person_id: str, nickname: str):
        """为家庭成员安排每日问候"""
        import datetime

        now = datetime.datetime.now()

        # 早安
        morning = now.replace(hour=self.morning_hour, minute=0, second=0, microsecond=0)
        if morning <= now:
            morning += datetime.timedelta(days=1)

        self._actions.append(
            ScheduledAction(
                action_id=f"morning_{person_id}",
                person_id=person_id,
                message=f"{nickname}，早上好呀！昨晚睡得怎么样？今天有什么安排吗？",
                trigger_time=morning.timestamp(),
                action_type="greeting",
                repeating=True,
                interval_seconds=86400,
            )
        )

        # 晚安
        evening = now.replace(hour=self.evening_hour, minute=0, second=0, microsecond=0)
        if evening <= now:
            evening += datetime.timedelta(days=1)

        self._actions.append(
            ScheduledAction(
                action_id=f"evening_{person_id}",
                person_id=person_id,
                message=f"{nickname}，今天辛苦了，早点休息吧。晚安！",
                trigger_time=evening.timestamp(),
                action_type="greeting",
                repeating=True,
                interval_seconds=86400,
            )
        )

        logger.info(
            f"已为 {nickname} 安排每日问候 ({self.morning_hour}:00, {self.evening_hour}:00)"
        )

    # ------------------------------------------------------------------
    # 用药提醒
    # ------------------------------------------------------------------
    def schedule_medication(
        self, person_id: str, nickname: str, medication: str, hours: list[int]
    ):
        """安排用药提醒"""
        import datetime

        now = datetime.datetime.now()
        for hour in hours:
            t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if t <= now:
                t += datetime.timedelta(days=1)

            self._actions.append(
                ScheduledAction(
                    action_id=f"med_{person_id}_{medication}_{hour}",
                    person_id=person_id,
                    message=f"{nickname}，该吃{medication}了，别忘了哦。吃完跟我说一声，我帮您记着。",
                    trigger_time=t.timestamp(),
                    action_type="medication",
                    priority="P2",
                    repeating=True,
                    interval_seconds=86400,
                )
            )
        logger.info(f"已为 {nickname} 安排 {medication} 提醒: {hours}")

    # ------------------------------------------------------------------
    # 一次性关怀 (如: 明天问膝盖好了没)
    # ------------------------------------------------------------------
    def schedule_followup(self, person_id: str, message: str, delay_hours: float = 24):
        """安排延迟关怀 (一次性)"""
        self._actions.append(
            ScheduledAction(
                action_id=f"followup_{person_id}_{int(time.time())}",
                person_id=person_id,
                message=message,
                trigger_time=time.time() + delay_hours * 3600,
                action_type="followup",
                repeating=False,
            )
        )
        logger.info(f"已安排 {delay_hours}h 后关怀: {message[:30]}...")

    # ------------------------------------------------------------------
    # 久未互动检测
    # ------------------------------------------------------------------
    def _check_idle_persons(self) -> list[tuple[str, str]]:
        """检查久未互动的家庭成员，返回 [(person_id, message), ...]"""
        results = []
        now = time.time()
        for person_id, last_time in self._last_activity.items():
            if person_id in ("unknown", "bot"):
                continue
            idle_time = now - last_time
            last_care = self._last_idle_care.get(person_id, 0)

            # 超过阈值且距上次关怀至少 2 小时
            if idle_time > self.idle_threshold and (now - last_care) > 7200:
                hours = idle_time / 3600
                if hours < 4:
                    msg = "好久没听到您说话了，在忙什么呢？要不要聊聊天？"
                else:
                    msg = "已经好几个小时没听到您了，一切都好吗？有什么需要帮忙的吗？"
                results.append((person_id, msg))
                self._last_idle_care[person_id] = now
        return results

    # ------------------------------------------------------------------
    # 后台循环
    # ------------------------------------------------------------------
    async def start(self):
        """启动后台调度循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("主动行为调度器已启动")

    async def stop(self):
        """停止调度"""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("主动行为调度器已停止")

    async def _loop(self):
        """主循环 — 每 30 秒检查一次"""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"主动行为调度异常: {e}")
            await asyncio.sleep(30)

    async def _tick(self):
        """单次检查 — 触发到期的行为 + 久未互动关怀"""
        now = time.time()

        # 1. 检查定时行为
        for action in self._actions:
            if action.executed and not action.repeating:
                continue
            if now >= action.trigger_time:
                await self._execute_action(action)
                if action.repeating:
                    action.trigger_time = now + action.interval_seconds
                else:
                    action.executed = True

        # 清理已执行的一次性行为
        self._actions = [a for a in self._actions if not a.executed or a.repeating]

        # 2. 检查久未互动
        idle_msgs = self._check_idle_persons()
        for person_id, msg in idle_msgs:
            await self._send_message(person_id, msg, "idle_care")

    async def _execute_action(self, action: ScheduledAction):
        """执行一个主动行为"""
        logger.info(
            f"触发主动行为: [{action.action_type}] {action.person_id}: {action.message[:30]}..."
        )
        await self._send_message(action.person_id, action.message, action.action_type)

    async def _send_message(self, person_id: str, message: str, action_type: str):
        """通过回调发送消息"""
        if self._send_callback:
            try:
                await self._send_callback(person_id, message, action_type)
            except Exception as e:
                logger.error(f"发送主动消息失败: {e}")
        else:
            logger.warning(f"主动消息未发送 (无回调): {message[:30]}...")
