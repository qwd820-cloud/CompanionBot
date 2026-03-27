"""P2 安全预警端到端验证

验证项:
1. 跌倒/呼救/健康异常关键词检测
2. 长时间无活动告警
3. 通知分级 + 限流
4. 插话决策在多人对话场景下的表现

用法:
    python scripts/verify_safety.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.output.notification import NotificationManager, Priority
from server.personality.intervention import InterventionDecider
from server.safety.anomaly_detector import AnomalyDetector


async def test_anomaly_detection():
    """验证 1: 关键词检测 (P0 呼救 / P1 健康异常 / P1 情绪异常)"""
    print("=" * 60)
    print("验证 1: 异常行为检测")
    print("=" * 60)

    detector = AnomalyDetector()

    test_cases = [
        ("救命啊！", "P0"),
        ("我摔倒了", "P0"),
        ("快来人啊", "P0"),
        ("胸闷喘不上气", "P1"),
        ("眼前发黑了", "P1"),
        ("不想活了", "P1"),
        ("今天天气不错", None),
        ("我吃了药了", None),  # "吃药" 不在 DISTRESS/URGENT 中
    ]

    for text, expected_severity in test_cases:
        result = await detector.check_audio(text, "grandpa")
        actual = result.severity if result else None
        match = actual == expected_severity
        status = "✓" if match else "✗"
        print(
            f'  {status} "{text}" → {actual or "无异常"} (预期: {expected_severity or "无异常"})'
        )
        if result:
            print(f"    类型: {result.type}, 描述: {result.description}")
        # reset cooldown for next P0 test
        if result and result.severity == "P0":
            detector._distress_cooldown.clear()

    print()


async def test_inactivity():
    """验证 2: 长时间无活动检测"""
    print("=" * 60)
    print("验证 2: 长时间无活动检测")
    print("=" * 60)

    detector = AnomalyDetector(inactivity_threshold_hours=0.001)  # ~3.6 秒
    # 先记录一次活动
    detector._last_activity["grandpa"] = time.time() - 10  # 10 秒前

    result = await detector.check_presence("grandpa", "client1")
    if result:
        print(f"  ✓ 检测到无活动: {result.description} (severity={result.severity})")
    else:
        print("  ✗ 未检测到无活动 (应该检测到)")

    # unknown 不触发
    result = await detector.check_presence("unknown", "client1")
    print(f"  ✓ unknown 用户跳过: {result is None}")

    print()


async def test_notification_rate_limit():
    """验证 3: 通知分级 + 限流"""
    print("=" * 60)
    print("验证 3: 通知分级与限流")
    print("=" * 60)

    config = {
        "contacts": [
            {
                "name": "王小明",
                "phone": "13800138000",
                "relationship": "孙子",
                "notification_levels": ["P0", "P1", "P2"],
            },
        ],
        "rules": {},
    }

    nm = NotificationManager(config)

    # P0 不限流
    print("  P0 不限流测试 (发送 5 次):")
    for i in range(5):
        records = await nm.send(Priority.P0, f"紧急测试 {i + 1}")
        sent = len(records) > 0 and records[0].sent
        print(f"    第 {i + 1} 次: {'发送' if sent else '被限流'}")

    # P1 限流: 每小时 3 条
    print("\n  P1 限流测试 (每小时最多 3 条):")
    nm2 = NotificationManager(config)
    for i in range(5):
        records = await nm2.send(Priority.P1, f"重要测试 {i + 1}")
        sent = len(records) > 0 and records[0].sent
        expected = i < 3
        status = "✓" if sent == expected else "✗"
        print(
            f"    {status} 第 {i + 1} 次: {'发送' if sent else '被限流'} (预期: {'发送' if expected else '被限流'})"
        )

    # WebSocket 指令
    commands = nm2.get_pending_commands()
    print(f"\n  WebSocket 待发送指令数: {len(commands)}")

    print()


async def test_intervention_multiparty():
    """验证 4: 多人对话插话决策"""
    print("=" * 60)
    print("验证 4: 多人对话插话决策")
    print("=" * 60)

    def make_ctx(text):
        return {"turns": [{"text": text, "role": "user", "timestamp": time.time()}]}

    scenarios = [
        ("安全紧急 (呼救)", "爷爷摔倒了！快来人啊！", True),
        ("被唤醒 (小伴)", "小伴，你觉得明天会下雨吗？", True),
        ("无关工作对话", "这个季度的KPI我们得重新调整一下", False),
        ("健康讨论 (血压)", "爷爷最近血压好像又高了", True),
        ("闲聊不相关", "昨天那个电视剧你看了吗？剧情太狗血了", False),
    ]

    for name, text, expect in scenarios:
        decider = InterventionDecider()
        should, reason = decider.should_intervene(make_ctx(text))
        match = should == expect
        status = "✓" if match else "✗"
        print(f"  {status} {name}: 插话={should} (预期={expect})")
        if reason:
            print(f"    原因: {reason}")

    # 频率惩罚测试
    print("\n  频率惩罚测试 (连续插话后被抑制):")
    decider = InterventionDecider()
    for i in range(4):
        should, reason = decider.should_intervene(make_ctx("小伴你好"))
        if should:
            decider._recent_interventions.append(time.time())
        print(f"    第 {i + 1} 次: 插话={should}")

    print()


async def main():
    await test_anomaly_detection()
    await test_inactivity()
    await test_notification_rate_limit()
    await test_intervention_multiparty()
    print("=== 安全预警验证完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
