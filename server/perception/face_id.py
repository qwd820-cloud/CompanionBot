"""人脸识别模块 — InsightFace buffalo_l"""

import io
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("companion_bot.face_id")

SIMILARITY_THRESHOLD = 0.4
EMBEDDING_DIM = 512


class FaceIdentifier:
    """基于 InsightFace 的人脸检测与识别"""

    def __init__(self, threshold: float = SIMILARITY_THRESHOLD):
        self.threshold = threshold
        self.model = None
        self.enrolled: dict[str, np.ndarray] = {}  # person_id → face embedding

    async def initialize(self):
        """加载 InsightFace buffalo_l 模型"""
        try:
            import insightface
            from insightface.app import FaceAnalysis
            self.model = FaceAnalysis(
                name="buffalo_l",
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            self.model.prepare(ctx_id=0, det_size=(640, 640))
            logger.info("InsightFace buffalo_l 加载成功")
        except Exception as e:
            logger.warning(f"InsightFace 加载失败: {e}")
            self.model = None

    async def identify(self, frame_data: bytes) -> dict | None:
        """
        识别视频帧中的人脸。
        输入: JPEG 图像字节
        输出: {"person_id": str, "score": float, "bbox": list} 或 None
        """
        image = self._decode_image(frame_data)
        if image is None:
            return None

        faces = self._detect_faces(image)
        if not faces:
            return None

        # 取最大人脸 (最近的人)
        face = max(faces, key=lambda f: self._face_area(f))
        embedding = self._get_embedding(face)
        if embedding is None:
            return None

        best_id = "unknown"
        best_score = 0.0

        for person_id, ref_emb in self.enrolled.items():
            score = self._cosine_similarity(embedding, ref_emb)
            if score > best_score:
                best_score = score
                best_id = person_id

        bbox = self._get_bbox(face)

        if best_score >= self.threshold:
            return {
                "person_id": best_id,
                "score": float(best_score),
                "bbox": bbox,
            }

        return {
            "person_id": "unknown",
            "score": float(best_score),
            "bbox": bbox,
        }

    async def enroll(self, person_id: str, image_data_list: list[bytes] | bytes):
        """
        注册人脸。
        输入: person_id, 5~10 张不同角度照片
        """
        if isinstance(image_data_list, bytes):
            image_data_list = [image_data_list]

        embeddings = []
        for img_data in image_data_list:
            image = self._decode_image(img_data)
            if image is None:
                continue
            faces = self._detect_faces(image)
            if not faces:
                continue
            face = max(faces, key=lambda f: self._face_area(f))
            emb = self._get_embedding(face)
            if emb is not None:
                embeddings.append(emb)

        if not embeddings:
            logger.error(f"人脸注册失败: {person_id}, 无有效 embedding")
            return

        mean_embedding = np.mean(embeddings, axis=0)
        mean_embedding = mean_embedding / np.linalg.norm(mean_embedding)
        self.enrolled[person_id] = mean_embedding
        logger.info(
            f"人脸注册成功: {person_id}, {len(embeddings)} 张照片"
        )

    def _decode_image(self, data: bytes) -> np.ndarray | None:
        """解码 JPEG 图像"""
        try:
            import cv2
            arr = np.frombuffer(data, dtype=np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return image
        except Exception as e:
            logger.error(f"图像解码失败: {e}")
            return None

    def _detect_faces(self, image: np.ndarray) -> list:
        """检测人脸"""
        if self.model is not None:
            return self.model.get(image)
        return []

    def _get_embedding(self, face) -> np.ndarray | None:
        """获取人脸 embedding"""
        if hasattr(face, "embedding") and face.embedding is not None:
            emb = face.embedding
            return emb / np.linalg.norm(emb)
        return None

    def _face_area(self, face) -> float:
        """计算人脸框面积"""
        if hasattr(face, "bbox"):
            bbox = face.bbox
            return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        return 0.0

    def _get_bbox(self, face) -> list:
        """获取人脸框坐标"""
        if hasattr(face, "bbox"):
            return [float(x) for x in face.bbox]
        return []

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
