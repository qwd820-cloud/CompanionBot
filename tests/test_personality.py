"""人格系统测试"""

from server.personality.engine import PersonalityEngine


def make_engine():
    config = {
        "personality": {
            "name": "小伴",
            "traits": {"warmth": 0.85, "humor": 0.6, "patience": 0.9},
            "quirks": ["喜欢比喻"],
        },
        "adaptation": {
            "elder": {"speech_rate": "slow"},
            "child": {"speech_rate": "normal"},
        },
    }
    return PersonalityEngine(config=config)


class TestEmotionStateMachine:
    def test_initial_emotion(self):
        engine = make_engine()
        assert engine.current_emotion == "neutral"

    def test_health_concern(self):
        engine = make_engine()
        engine.update_emotion(
            {"turns": [{"role": "user", "text": "我头好疼啊"}]}, ""
        )
        assert engine.current_emotion == "concerned"

    def test_happy_trigger(self):
        engine = make_engine()
        engine.update_emotion(
            {"turns": [{"role": "user", "text": "太好了，考试通过了！"}]}, ""
        )
        assert engine.current_emotion == "happy"

    def test_curious_trigger(self):
        engine = make_engine()
        engine.update_emotion(
            {"turns": [{"role": "user", "text": "你知道吗，隔壁老王种了一棵桂花树"}]}, ""
        )
        assert engine.current_emotion == "curious"

    def test_emotion_decay(self):
        engine = make_engine()
        engine.current_emotion = "happy"
        # 模拟多轮对话后情绪衰减
        for _ in range(6):
            engine.update_emotion(
                {"turns": [{"role": "user", "text": "嗯嗯"}]}, ""
            )
        # 经过足够轮次后应回到 happy 的推断结果或 neutral
        # 因为 "嗯嗯" 不匹配任何关键词，情绪会保持并最终衰减
        assert engine.current_emotion == "neutral"

    def test_adaptation(self):
        engine = make_engine()
        elder_adapt = engine.get_adaptation("elder")
        assert elder_adapt.get("speech_rate") == "slow"

        child_adapt = engine.get_adaptation("child")
        assert child_adapt.get("speech_rate") == "normal"

    def test_emotion_modifiers(self):
        engine = make_engine()
        engine.current_emotion = "happy"
        mods = engine.get_emotion_modifiers()
        assert "太好了" in mods["tone_words"]
        assert mods["length_factor"] > 1.0
