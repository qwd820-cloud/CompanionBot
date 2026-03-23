"""声纹识别模块 — SpeechBrain ECAPA-TDNN"""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("companion_bot.speaker_id")

SIMILARITY_THRESHOLD = 0.25
EMBEDDING_DIM = 192
UPDATE_ALPHA = 0.05  # 声纹模板在线更新权重


class SpeakerIdentifier:
    """基于 SpeechBrain ECAPA-TDNN 的声纹识别"""

    def __init__(
        self,
        voiceprint_dir: str,
        threshold: float = SIMILARITY_THRESHOLD,
    ):
        self.voiceprint_dir = Path(voiceprint_dir)
        self.threshold = threshold
        self.model = None
        self.enrolled: dict[str, np.ndarray] = {}  # person_id → embedding

    async def initialize(self):
        """加载模型和已注册声纹"""
        try:
            from speechbrain.inference.speaker import (
                EncoderClassifier,
            )
            self.model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                run_opts={"device": "cuda"},
            )
            logger.info("SpeechBrain ECAPA-TDNN 加载成功")
        except Exception as e:
            logger.warning(f"SpeechBrain 加载失败: {e}")
            self.model = None

        self._load_enrolled()

    def _load_enrolled(self):
        """从磁盘加载已注册的声纹"""
        self.voiceprint_dir.mkdir(parents=True, exist_ok=True)
        for npy_file in self.voiceprint_dir.glob("*.npy"):
            person_id = npy_file.stem
            self.enrolled[person_id] = np.load(str(npy_file))
            logger.info(f"加载声纹: {person_id}")

    async def identify(
        self, segment
    ) -> dict:
        """
        识别说话人。
        输入: SpeechSegment 或 numpy 音频数据
        输出: {"person_id": str, "score": float}
        """
        audio = segment.audio if hasattr(segment, "audio") else segment
        embedding = self._extract_embedding(audio)
        if embedding is None:
            return {"person_id": "unknown", "score": 0.0}

        best_id = "unknown"
        best_score = 0.0

        for person_id, ref_emb in self.enrolled.items():
            score = self._cosine_similarity(embedding, ref_emb)
            if score > best_score:
                best_score = score
                best_id = person_id

        if best_score >= self.threshold:
            # 高置信度时在线更新声纹模板
            if best_score > 0.8:
                self._update_template(best_id, embedding)
            return {"person_id": best_id, "score": float(best_score)}

        return {"person_id": "unknown", "score": float(best_score)}

    async def enroll(
        self, person_id: str, audio_samples: list[np.ndarray] | bytes
    ):
        """
        注册新声纹。
        输入: person_id, 3~5 段语音音频
        """
        if isinstance(audio_samples, bytes):
            pcm = np.frombuffer(audio_samples, dtype=np.int16).astype(
                np.float32
            ) / 32768.0
            audio_samples = [pcm]

        embeddings = []
        for audio in audio_samples:
            emb = self._extract_embedding(audio)
            if emb is not None:
                embeddings.append(emb)

        if not embeddings:
            logger.error(f"声纹注册失败: {person_id}, 无有效 embedding")
            return

        mean_embedding = np.mean(embeddings, axis=0)
        mean_embedding = mean_embedding / np.linalg.norm(mean_embedding)

        self.enrolled[person_id] = mean_embedding
        save_path = self.voiceprint_dir / f"{person_id}.npy"
        np.save(str(save_path), mean_embedding)
        logger.info(
            f"声纹注册成功: {person_id}, {len(embeddings)} 段样本"
        )

    def _extract_embedding(self, audio: np.ndarray) -> np.ndarray | None:
        """提取音频的 speaker embedding"""
        if self.model is None:
            return self._dummy_embedding(audio)

        try:
            import torch
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            tensor = torch.from_numpy(audio).unsqueeze(0)
            embedding = self.model.encode_batch(tensor)
            return embedding.squeeze().cpu().numpy()
        except Exception as e:
            logger.error(f"Embedding 提取失败: {e}")
            return None

    def _dummy_embedding(self, audio: np.ndarray) -> np.ndarray:
        """无模型时的虚拟 embedding (仅用于开发测试)"""
        rng = np.random.RandomState(
            int(np.abs(audio[:100].sum()) * 1000) % (2**31)
        )
        emb = rng.randn(EMBEDDING_DIM).astype(np.float32)
        return emb / np.linalg.norm(emb)

    def _cosine_similarity(
        self, a: np.ndarray, b: np.ndarray
    ) -> float:
        """计算余弦相似度"""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _update_template(self, person_id: str, new_embedding: np.ndarray):
        """高置信度时在线更新声纹模板"""
        old = self.enrolled[person_id]
        updated = (1 - UPDATE_ALPHA) * old + UPDATE_ALPHA * new_embedding
        updated = updated / np.linalg.norm(updated)
        self.enrolled[person_id] = updated
