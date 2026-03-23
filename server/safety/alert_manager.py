"""预警管理 — 处理异常事件 + 通道降级"""

import logging

from server.safety.anomaly_detector import Anomaly
from server.output.notification import NotificationManager, Priority

logger = logging.getLogger("companion_bot.alert_manager")

SEVERITY_TO_PRIORITY = {
    "P0": Priority.P0,
    "P1": Priority.P1,
    "P2": Priority.P2,
}


class AlertManager:
    """预警管理器 — 接收异常事件，触发对应级别的通知"""

    def __init__(self, notification: NotificationManager):
        self.notification = notification
        self._alert_history: list[dict] = []

    async def handle_anomaly(
        self, anomaly: Anomaly, client_id: str, connection_manager
    ):
        """
        处理检测到的异常事件。
        1. 记录异常
        2. 发送通知
        3. 通过 WebSocket 通知手机端
        """
        priority = SEVERITY_TO_PRIORITY.get(anomaly.severity, Priority.P2)

        logger.warning(
            f"异常事件: type={anomaly.type}, severity={anomaly.severity}, "
            f"person={anomaly.person_id}, desc={anomaly.description}"
        )

        # 记录
        self._alert_history.append({
            "type": anomaly.type,
            "severity": anomaly.severity,
            "person_id": anomaly.person_id,
            "description": anomaly.description,
            "timestamp": anomaly.timestamp,
        })

        # 发送通知
        records = await self.notification.send(
            priority=priority,
            message=f"[{anomaly.person_id}] {anomaly.description}",
        )

        # 通过 WebSocket 发送短信指令到手机端
        commands = self.notification.get_pending_commands()
        for cmd in commands:
            await connection_manager.send_notification_command(
                client_id=client_id,
                phone=cmd["phone"],
                message=cmd["message"],
            )

        # P0 级别: 同时发送语音警报到手机端
        if priority == Priority.P0:
            await connection_manager.send_json_message(
                client_id,
                {
                    "type": "alert",
                    "severity": "P0",
                    "message": anomaly.description,
                    "action": "play_alarm",
                },
            )

        return records

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        """获取最近的预警记录"""
        return self._alert_history[-limit:]
