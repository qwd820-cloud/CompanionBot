"""多模态身份融合 — 声纹 + 人脸"""

import logging

logger = logging.getLogger("companion_bot.identity_fusion")


class IdentityFusion:
    """融合声纹识别和人脸识别的结果，确定说话人身份"""

    def fuse(
        self,
        voice_id: str | None,
        voice_score: float,
        face_id: str | None,
        face_score: float,
    ) -> dict:
        """
        多模态身份融合。

        规则:
        1. 声纹和人脸一致 → 返回该 ID，置信度取最高
        2. 不一致 → 返回置信度更高的一方 (人脸通常更可靠)
        3. 只有一方有结果 → 返回有结果的一方
        """
        voice_id = voice_id or "unknown"
        face_id = face_id or "unknown"

        # 两者都没有识别到
        if voice_id == "unknown" and face_id == "unknown":
            return {"person_id": "unknown", "score": 0.0, "source": "none"}

        # 只有声纹
        if face_id == "unknown":
            return {
                "person_id": voice_id,
                "score": voice_score,
                "source": "voice",
            }

        # 只有人脸
        if voice_id == "unknown":
            return {
                "person_id": face_id,
                "score": face_score,
                "source": "face",
            }

        # 两者一致
        if voice_id == face_id:
            return {
                "person_id": voice_id,
                "score": max(voice_score, face_score),
                "source": "fused",
            }

        # 两者不一致，取置信度更高的 (人脸通常更可靠)
        if face_score > voice_score:
            logger.info(
                f"身份融合冲突: voice={voice_id}({voice_score:.2f}) "
                f"vs face={face_id}({face_score:.2f}), 取人脸"
            )
            return {
                "person_id": face_id,
                "score": face_score,
                "source": "face",
            }

        logger.info(
            f"身份融合冲突: voice={voice_id}({voice_score:.2f}) "
            f"vs face={face_id}({face_score:.2f}), 取声纹"
        )
        return {
            "person_id": voice_id,
            "score": voice_score,
            "source": "voice",
        }
