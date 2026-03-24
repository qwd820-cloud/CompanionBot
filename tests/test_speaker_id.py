"""声纹识别测试"""

import asyncio
import tempfile

import numpy as np
import pytest

from server.perception.speaker_id import SpeakerIdentifier


@pytest.fixture
def speaker_id(tmp_path):
    si = SpeakerIdentifier(voiceprint_dir=str(tmp_path / "voiceprints"))
    asyncio.get_event_loop().run_until_complete(si.initialize())
    return si


def test_enroll_and_identify(speaker_id):
    """测试声纹注册和识别"""
    # 生成模拟音频
    rng = np.random.RandomState(42)
    audio_samples = [rng.randn(16000).astype(np.float32) for _ in range(3)]

    # 注册
    asyncio.get_event_loop().run_until_complete(
        speaker_id.enroll("test_person", audio_samples)
    )
    assert "test_person" in speaker_id.enrolled

    # 识别 (使用相同种子生成的音频应有较高相似度)
    test_audio = rng.randn(16000).astype(np.float32)
    result = asyncio.get_event_loop().run_until_complete(
        speaker_id.identify(test_audio)
    )
    assert "person_id" in result
    assert "score" in result


def test_unknown_speaker(speaker_id):
    """测试未注册的说话人"""
    audio = np.random.randn(16000).astype(np.float32)
    result = asyncio.get_event_loop().run_until_complete(
        speaker_id.identify(audio)
    )
    # 无注册声纹时应返回 unknown
    assert result["person_id"] == "unknown" or result["score"] == 0.0


def test_cosine_similarity():
    """测试余弦相似度计算"""
    from server.utils.audio import cosine_similarity

    a = np.array([1.0, 0.0, 0.0])
    b = np.array([1.0, 0.0, 0.0])
    assert cosine_similarity(a, b) == pytest.approx(1.0)

    c = np.array([0.0, 1.0, 0.0])
    assert cosine_similarity(a, c) == pytest.approx(0.0)
