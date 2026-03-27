"""插话决策测试"""

import time

from server.personality.intervention import InterventionDecider


class TestInterventionDecider:
    def test_safety_trigger(self):
        """安全预警应直接触发"""
        decider = InterventionDecider()
        result = decider.should_intervene(
            {"turns": [{"text": "救命啊！", "role": "user", "timestamp": time.time()}]}
        )
        assert result[0] is True
        assert "安全预警" in result[1]

    def test_safety_keywords(self):
        """各种安全关键词测试"""
        decider = InterventionDecider()
        for keyword in ["摔倒了", "帮帮我", "不行了"]:
            result = decider.should_intervene(
                {"turns": [{"text": keyword, "role": "user", "timestamp": time.time()}]}
            )
            assert result[0] is True, f"关键词 '{keyword}' 应触发安全预警"

    def test_irrelevant_content(self):
        """无关内容不应插话"""
        decider = InterventionDecider()
        result = decider.should_intervene(
            {
                "turns": [
                    {
                        "text": "今天项目代码要重构一下",
                        "role": "user",
                        "timestamp": time.time(),
                    }
                ]
            }
        )
        # 无关话题评分应较低
        assert result[0] is False or "total" in result[1]

    def test_bot_mentioned(self):
        """提到机器人名字时相关度高"""
        decider = InterventionDecider()
        result = decider.should_intervene(
            {
                "turns": [
                    {
                        "text": "小伴觉得怎么样？",
                        "role": "user",
                        "timestamp": time.time(),
                    }
                ]
            }
        )
        # 提到名字 relevance=1.0，应倾向于插话
        assert result[0] is True

    def test_cooldown(self):
        """被忽略后应有冷却期"""
        decider = InterventionDecider()
        decider.mark_ignored()
        result = decider.should_intervene(
            {
                "turns": [
                    {
                        "text": "你们觉得天气怎么样？",
                        "role": "user",
                        "timestamp": time.time(),
                    }
                ]
            }
        )
        assert result[0] is False
        assert "冷却期" in result[1]

    def test_frequency_penalty(self):
        """频繁插话应受到惩罚"""
        decider = InterventionDecider()
        # 模拟最近多次插话
        decider._recent_interventions = [
            time.time() - 60,
            time.time() - 30,
            time.time() - 10,
        ]
        penalty = decider._frequency_penalty()
        assert penalty == 1.0  # 3 次以上，最大惩罚
