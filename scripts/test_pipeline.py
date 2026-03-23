"""端到端管线测试"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np


async def test_perception_pipeline():
    """测试感知层管线"""
    from server.perception.vad import VADProcessor
    from server.perception.speaker_id import SpeakerIdentifier
    from server.perception.asr import ASRProcessor
    from server.perception.identity_fusion import IdentityFusion

    print("=== 感知层管线测试 ===\n")

    data_dir = Path(__file__).parent.parent / "server" / "data"

    # 1. VAD
    print("1. VAD 初始化...")
    vad = VADProcessor()
    await vad.initialize()
    # 生成模拟音频 (1秒白噪声 + 1秒静音)
    noise = (np.random.randn(16000) * 0.1).astype(np.float32)
    silence = np.zeros(16000, dtype=np.float32)
    test_audio = np.concatenate([noise, silence])
    pcm_bytes = (test_audio * 32768).astype(np.int16).tobytes()
    segments = await vad.process(pcm_bytes)
    print(f"   VAD 检测到 {len(segments)} 个语音段")

    # 2. 声纹识别
    print("2. 声纹识别初始化...")
    speaker = SpeakerIdentifier(voiceprint_dir=str(data_dir / "voiceprints"))
    await speaker.initialize()
    if segments:
        result = await speaker.identify(segments[0])
        print(f"   识别结果: {result}")
    else:
        print("   (无语音段可供测试)")

    # 3. 身份融合
    print("3. 身份融合测试...")
    fusion = IdentityFusion()
    test_cases = [
        ("alice", 0.8, "alice", 0.9),
        ("alice", 0.8, "bob", 0.6),
        ("unknown", 0.0, "alice", 0.7),
    ]
    for v_id, v_s, f_id, f_s in test_cases:
        result = fusion.fuse(v_id, v_s, f_id, f_s)
        print(f"   voice={v_id}({v_s}) + face={f_id}({f_s}) → {result}")

    print("\n感知层测试完成\n")


async def test_memory_pipeline():
    """测试记忆层管线"""
    import tempfile
    from server.memory.episodic_memory import EpisodicMemory
    from server.memory.semantic_memory import SemanticMemory
    from server.memory.long_term_profile import LongTermProfile
    from server.memory.working_memory import WorkingMemory

    print("=== 记忆层管线测试 ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/test.db"

        # 1. 情景记忆
        print("1. 情景记忆测试...")
        episodic = EpisodicMemory(db_path=db_path)
        await episodic.initialize()
        await episodic.add_episode("grandpa", "爷爷说膝盖疼", "concerned", 0.8)
        await episodic.add_episode("grandpa", "爷爷和小伴聊了天气", "neutral", 0.3)
        recent = await episodic.get_recent("grandpa")
        print(f"   最近记忆: {len(recent)} 条")
        for ep in recent:
            print(f"   - {ep.summary} (importance={ep.importance_score})")

        # 2. 长期档案
        print("2. 长期档案测试...")
        profile = LongTermProfile(db_path=db_path)
        await profile.initialize()
        await profile.add_member(
            person_id="grandpa",
            name="王爷爷",
            nickname="爷爷",
            role="elder",
            age=75,
            interests=["下棋", "听戏曲"],
            health_conditions=["高血压"],
        )
        p = await profile.get_profile("grandpa")
        print(f"   档案: {p['name']}, 兴趣={p['interests']}")

        await profile.update_interests("grandpa", ["种花"])
        p = await profile.get_profile("grandpa")
        print(f"   更新后兴趣: {p['interests']}")

        # 3. 工作记忆
        print("3. 工作记忆测试...")
        wm = WorkingMemory()
        wm.start_session("test_session")
        wm.add_turn("test_session", "grandpa", "小伴，今天天气怎么样？", "user")
        wm.add_turn("test_session", "bot", "爷爷好！今天晴天，适合出去散步。", "assistant")

        is_bot = wm.is_addressed_to_bot("test_session", "小伴你真棒")
        print(f"   唤醒词检测: '小伴你真棒' → {is_bot}")

        context = wm.get_context("test_session")
        print(f"   上下文: {len(context['turns'])} 轮对话")

    print("\n记忆层测试完成\n")


async def test_personality():
    """测试人格引擎"""
    from server.personality.engine import PersonalityEngine
    from server.personality.intervention import InterventionDecider

    print("=== 人格与决策层测试 ===\n")

    config = {
        "personality": {
            "name": "小伴",
            "traits": {"warmth": 0.85, "humor": 0.6, "patience": 0.9},
            "quirks": ["喜欢用比喻"],
        },
        "adaptation": {
            "elder": {"speech_rate": "slow", "vocabulary": "simple"},
        },
    }

    # 1. 人格引擎
    print("1. 人格引擎测试...")
    engine = PersonalityEngine(config=config)
    print(f"   初始情绪: {engine.current_emotion}")

    engine.update_emotion(
        {"turns": [{"role": "user", "text": "我头好疼"}]}, ""
    )
    print(f"   听到'头好疼'后: {engine.current_emotion}")

    engine.update_emotion(
        {"turns": [{"role": "user", "text": "太好了，孙子考上大学了！"}]}, ""
    )
    print(f"   听到好消息后: {engine.current_emotion}")

    # 2. 插话决策
    print("2. 插话决策测试...")
    decider = InterventionDecider()

    result = decider.should_intervene({
        "turns": [{"text": "救命啊！", "role": "user", "timestamp": 0}]
    })
    print(f"   '救命啊' → 插话={result[0]}, 原因={result[1]}")

    result = decider.should_intervene({
        "turns": [{"text": "今天工作好累", "role": "user", "timestamp": 0}]
    })
    print(f"   '今天工作好累' → 插话={result[0]}")

    print("\n人格层测试完成\n")


async def main():
    await test_perception_pipeline()
    await test_memory_pipeline()
    await test_personality()
    print("=== 所有管线测试完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
