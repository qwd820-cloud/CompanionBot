"""共享工具函数 — 相似度计算、音频处理、embedding 操作"""

import numpy as np


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度"""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    """L2 归一化 embedding 向量"""
    norm = np.linalg.norm(embedding)
    if norm == 0:
        return embedding
    return embedding / norm


def mean_normalize_embeddings(embeddings: list[np.ndarray]) -> np.ndarray:
    """计算 embedding 列表的均值并归一化"""
    mean_emb = np.mean(embeddings, axis=0)
    return normalize_embedding(mean_emb)


def decode_pcm_to_float32(audio_bytes: bytes) -> np.ndarray:
    """将 16-bit PCM 字节流解码为 float32 numpy 数组 (-1.0 ~ 1.0)"""
    pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    return pcm / 32768.0
