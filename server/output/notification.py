"""推送通知系统 — 分级通知 + 通道降级 + 防骚扰"""

import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger("companion_bot.notification")


class Priority(IntEnum):
    P0 = 0  # 紧急: 生命安全
    P1 = 1  # 重要: 健康/情绪异常
    P2 = 2  # 一般: 每日摘要
    P3 = 3  # 低: 普通信息


@dataclass
class NotificationRecord:
    """通知记录"""
    priority: Priority
    phone: str
    message: str
    timestamp: float = field(default_factory=time.time)
    sent: bool = False
    channel: str = "sms"


class NotificationManager:
    """通知管理器 — 分级推送 + 限流 + 降级"""

    def __init__(self, config: dict):
        self.contacts = config.get("contacts", [])
        self.rules = config.get("rules", {})
        self._sent_records: list[NotificationRecord] = []
        self._pending_ws_commands: list[dict] = []

    async def send(
        self,
        priority: Priority,
        message: str,
        target_levels: list[str] | None = None,
    ) -> list[NotificationRecord]:
        """
        发送通知。
        根据优先级匹配联系人，通过 WebSocket 指令让手机端发送短信。
        """
        records = []

        # 找到匹配的联系人
        contacts = self._get_contacts_for_priority(priority, target_levels)
        if not contacts:
            logger.warning(f"没有匹配的联系人: priority={priority.name}")
            return records

        for contact in contacts:
            # 限流检查
            if not self._rate_limit_check(priority, contact["phone"]):
                logger.info(
                    f"通知被限流: {contact['name']} ({priority.name})"
                )
                continue

            record = NotificationRecord(
                priority=priority,
                phone=contact["phone"],
                message=message,
            )

            # 生成 WebSocket 指令 (实际发送由手机端执行)
            self._pending_ws_commands.append({
                "type": "notification",
                "action": "send_sms",
                "phone": contact["phone"],
                "message": f"[{priority.name}] {message}",
                "contact_name": contact["name"],
            })

            record.sent = True
            records.append(record)
            self._sent_records.append(record)

            logger.info(
                f"通知已排队: {priority.name} → {contact['name']} "
                f"({contact['phone']})"
            )

        return records

    def get_pending_commands(self) -> list[dict]:
        """获取并清空待发送的 WebSocket 通知指令"""
        commands = self._pending_ws_commands.copy()
        self._pending_ws_commands.clear()
        return commands

    def _get_contacts_for_priority(
        self, priority: Priority, target_levels: list[str] | None = None
    ) -> list[dict]:
        """获取匹配优先级的联系人"""
        priority_str = priority.name
        matched = []
        for contact in self.contacts:
            levels = contact.get("notification_levels", [])
            if priority_str in levels:
                if target_levels is None or any(
                    l in levels for l in target_levels
                ):
                    matched.append(contact)
        return matched

    def _rate_limit_check(self, priority: Priority, phone: str) -> bool:
        """限流检查"""
        # P0 不限流
        if priority == Priority.P0:
            return True

        now = time.time()

        if priority == Priority.P1:
            # P1: 每小时最多 3 条
            hour_ago = now - 3600
            recent = [
                r for r in self._sent_records
                if r.phone == phone
                and r.priority == Priority.P1
                and r.timestamp > hour_ago
            ]
            return len(recent) < 3

        # P2/P3: 每天最多 1 条
        day_ago = now - 86400
        recent = [
            r for r in self._sent_records
            if r.phone == phone
            and r.priority.value >= Priority.P2
            and r.timestamp > day_ago
        ]
        return len(recent) < 1
