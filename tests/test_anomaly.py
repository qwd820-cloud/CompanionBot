"""异常检测增强测试 — 模糊匹配、能量突变"""

import time

import numpy as np
import pytest

from server.safety.anomaly_detector import AnomalyDetector, AnomalyType
from server.utils.keywords import match_fall_pattern, match_health_fuzzy


class TestFuzzyPatterns:
    """测试模糊匹配规则"""

    @pytest.mark.parametrize(
        "text",
        [
            "我摔了一跤",
            "刚才摔倒了",
            "滑了一下",
            "绊倒了",
            "跌了一跤",
            "脚崴了",
            "站不稳",
            "爬不起来",
            "腿软了",
        ],
    )
    def test_fall_patterns_match(self, text):
        """跌倒相关描述应被匹配"""
        assert match_fall_pattern(text) is not None, f"未匹配: '{text}'"

    @pytest.mark.parametrize(
        "text",
        [
            "今天天气不错",
            "我去散步了",
            "吃过饭了",
            "打了太极拳",
        ],
    )
    def test_fall_patterns_no_false_positive(self, text):
        """正常对话不应触发跌倒匹配"""
        assert match_fall_pattern(text) is None, f"误匹配: '{text}'"

    @pytest.mark.parametrize(
        "text",
        [
            "头好疼",
            "胸口闷得慌",
            "肚子不舒服",
            "恶心想吐",
            "眼睛看不清了",
            "突然好晕",
            "冒冷汗",
            "腰痛得厉害",
        ],
    )
    def test_health_fuzzy_match(self, text):
        """身体不适描述应被匹配"""
        assert match_health_fuzzy(text) is not None, f"未匹配: '{text}'"


class TestAnomalyDetectorEnhanced:
    """测试增强后的异常检测器"""

    @pytest.mark.asyncio
    async def test_fall_fuzzy_triggers_p0(self):
        """模糊跌倒描述应触发 P0"""
        detector = AnomalyDetector()
        anomaly = await detector.check_audio("我摔了一跤", "grandpa")
        assert anomaly is not None
        assert anomaly.type == AnomalyType.FALL
        assert anomaly.severity == "P0"

    @pytest.mark.asyncio
    async def test_health_fuzzy_triggers_p1(self):
        """模糊健康描述应触发 P1"""
        detector = AnomalyDetector()
        anomaly = await detector.check_audio("头好疼啊", "grandpa")
        assert anomaly is not None
        assert anomaly.type == AnomalyType.HEALTH_CONCERN
        assert anomaly.severity == "P1"

    @pytest.mark.asyncio
    async def test_exact_keyword_takes_priority(self):
        """精确关键词优先于模糊匹配"""
        detector = AnomalyDetector()
        anomaly = await detector.check_audio("救命啊我摔倒了", "grandpa")
        assert anomaly is not None
        # "救命" 是精确匹配 → DISTRESS_CALL (P0)
        assert anomaly.type == AnomalyType.DISTRESS_CALL

    @pytest.mark.asyncio
    async def test_normal_text_no_alert(self):
        """正常对话不触发任何警报"""
        detector = AnomalyDetector()
        for text in ["今天天气不错", "吃过饭了", "小明考了100分", "打太极拳去了"]:
            anomaly = await detector.check_audio(text, "grandpa")
            assert anomaly is None, f"误报: '{text}'"

    @pytest.mark.asyncio
    async def test_audio_energy_spike(self):
        """音频能量突变检测"""
        detector = AnomalyDetector()
        # 先喂一些正常能量的帧
        normal_audio = np.zeros(1600, dtype=np.int16)
        normal_audio[:] = 100  # 低能量
        for _ in range(20):
            await detector.check_audio_energy(normal_audio.tobytes(), "grandpa")

        # 突然一个高能量帧
        spike_audio = np.zeros(1600, dtype=np.int16)
        spike_audio[:] = 10000  # 高能量 (100x)
        anomaly = await detector.check_audio_energy(spike_audio.tobytes(), "grandpa")
        assert anomaly is not None
        assert anomaly.type == AnomalyType.AUDIO_SPIKE

    @pytest.mark.asyncio
    async def test_cooldown_prevents_duplicate(self):
        """冷却期内不重复报警"""
        detector = AnomalyDetector()
        a1 = await detector.check_audio("我摔了一跤", "grandpa")
        assert a1 is not None
        a2 = await detector.check_audio("我又摔了", "grandpa")
        assert a2 is None  # cooldown 内

    @pytest.mark.asyncio
    async def test_emotional_distress_enhanced(self):
        """增强的情绪危机检测"""
        detector = AnomalyDetector()
        for text in ["不想活了", "活着没意思", "受不了了"]:
            anomaly = await detector.check_audio(text, f"test_{time.time()}")
            assert anomaly is not None, f"未检测到情绪危机: '{text}'"
            assert anomaly.type == AnomalyType.EMOTIONAL_DISTRESS
