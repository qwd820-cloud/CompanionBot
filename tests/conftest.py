"""共享测试 fixtures — 为所有测试提供统一的基础设施"""

import hashlib

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Temp directories
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_data_dir(tmp_path):
    """临时数据目录，测试结束后自动清理"""
    (tmp_path / "chroma").mkdir()
    (tmp_path / "voiceprints").mkdir()
    return tmp_path


@pytest.fixture
def tmp_db_path(tmp_path):
    """临时 SQLite 数据库路径"""
    return str(tmp_path / "test_companion.db")


# ---------------------------------------------------------------------------
# Mock embedding function (no network / no model needed)
# ---------------------------------------------------------------------------
class HashEmbeddingFunction:
    """基于字符 n-gram hash 的确定性 embedding，用于测试。
    相似文本产生相近向量。实现 ChromaDB EmbeddingFunction 协议。"""

    DIMS = 384  # 与 all-MiniLM-L6-v2 维度一致

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in input]

    @staticmethod
    def name() -> str:
        return "test_hash_embedding"

    @classmethod
    def build_from_config(cls, config):
        return cls()

    def get_config(self):
        return {}

    def _embed(self, text: str) -> list[float]:
        vec = np.zeros(self.DIMS, dtype=np.float32)
        for i in range(len(text) - 2):
            ngram = text[i : i + 3]
            h = int(hashlib.md5(ngram.encode()).hexdigest(), 16)
            idx = h % self.DIMS
            vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()


@pytest.fixture
def hash_embedding_fn():
    """提供 HashEmbeddingFunction 实例"""
    return HashEmbeddingFunction()


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------
class MockLLMClient:
    """模拟 LLM 客户端，返回预设回复，不需要真实 LLM 服务"""

    def __init__(self):
        self.call_log: list[dict] = []
        self._responses: dict[str, str] = {}

    def set_response(self, task_type: str, response: str):
        self._responses[task_type] = response

    async def chat(self, messages, task_type="daily", **kwargs):
        self.call_log.append(
            {
                "messages": messages,
                "task_type": task_type,
                "kwargs": kwargs,
            }
        )
        if task_type in self._responses:
            return self._responses[task_type]
        # 默认回复
        return "这是一个测试回复。"

    async def chat_json(self, messages, task_type="daily", **kwargs):
        self.call_log.append(
            {
                "messages": messages,
                "task_type": task_type,
                "kwargs": kwargs,
            }
        )
        if task_type in self._responses:
            import json

            return json.loads(self._responses[task_type])
        return {"summary": "测试摘要", "importance": 0.5}


@pytest.fixture
def mock_llm():
    """提供 MockLLMClient 实例"""
    return MockLLMClient()


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_audio_float32():
    """生成 1 秒 16kHz 正弦波测试音频 (float32)"""
    sr = 16000
    t = np.linspace(0, 1.0, sr, dtype=np.float32)
    audio = 0.5 * np.sin(2 * np.pi * 440 * t)  # 440Hz
    return audio


@pytest.fixture
def sample_audio_pcm(sample_audio_float32):
    """生成 1 秒 16kHz PCM 字节流 (16-bit signed)"""
    pcm = (sample_audio_float32 * 32767).astype(np.int16)
    return pcm.tobytes()
