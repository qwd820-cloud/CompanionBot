"""注册新家庭成员 — 声纹 + 人脸"""

import argparse
import asyncio
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server.perception.speaker_id import SpeakerIdentifier
from server.perception.face_id import FaceIdentifier
from server.memory.long_term_profile import LongTermProfile


async def enroll(args):
    data_dir = Path(__file__).parent.parent / "server" / "data"
    db_path = str(data_dir / "companion.db")

    # 初始化模块
    speaker_id = SpeakerIdentifier(voiceprint_dir=str(data_dir / "voiceprints"))
    face_id = FaceIdentifier()
    profile = LongTermProfile(db_path=db_path)

    await speaker_id.initialize()
    await face_id.initialize()
    await profile.initialize()

    person_id = args.name.replace(" ", "_").lower()

    # 注册声纹
    if args.audio_dir:
        audio_dir = Path(args.audio_dir)
        if audio_dir.exists():
            import numpy as np
            audio_samples = []
            for audio_file in sorted(audio_dir.glob("*.wav")) + sorted(audio_dir.glob("*.pcm")):
                print(f"  加载音频: {audio_file.name}")
                if audio_file.suffix == ".wav":
                    import wave
                    with wave.open(str(audio_file), "rb") as wf:
                        pcm = np.frombuffer(
                            wf.readframes(wf.getnframes()), dtype=np.int16
                        ).astype(np.float32) / 32768.0
                        audio_samples.append(pcm)
                else:
                    pcm = np.fromfile(str(audio_file), dtype=np.int16).astype(
                        np.float32
                    ) / 32768.0
                    audio_samples.append(pcm)

            if audio_samples:
                await speaker_id.enroll(person_id, audio_samples)
                print(f"声纹注册完成: {len(audio_samples)} 段音频")
            else:
                print("警告: 未找到有效音频文件")
        else:
            print(f"警告: 音频目录不存在: {audio_dir}")

    # 注册人脸
    if args.photo_dir:
        photo_dir = Path(args.photo_dir)
        if photo_dir.exists():
            image_data_list = []
            for img_file in sorted(photo_dir.glob("*.jpg")) + sorted(photo_dir.glob("*.png")):
                print(f"  加载照片: {img_file.name}")
                image_data_list.append(img_file.read_bytes())

            if image_data_list:
                await face_id.enroll(person_id, image_data_list)
                print(f"人脸注册完成: {len(image_data_list)} 张照片")
            else:
                print("警告: 未找到有效照片文件")
        else:
            print(f"警告: 照片目录不存在: {photo_dir}")

    # 注册档案
    await profile.add_member(
        person_id=person_id,
        name=args.name,
        nickname=args.nickname or args.name,
        role=args.role,
        age=args.age or 0,
        relationship=args.relationship or "",
        interests=args.interests.split(",") if args.interests else [],
        health_conditions=args.health.split(",") if args.health else [],
    )
    print(f"\n注册完成: {args.name} (person_id={person_id})")


def main():
    parser = argparse.ArgumentParser(description="注册新家庭成员")
    parser.add_argument("--name", required=True, help="成员姓名")
    parser.add_argument("--nickname", help="称呼/昵称")
    parser.add_argument("--role", default="adult",
                        choices=["elder", "child", "adult"],
                        help="角色类型")
    parser.add_argument("--age", type=int, help="年龄")
    parser.add_argument("--relationship", help="与家庭的关系")
    parser.add_argument("--audio-dir", help="声纹样本目录 (WAV/PCM 文件)")
    parser.add_argument("--photo-dir", help="照片目录 (JPG/PNG 文件)")
    parser.add_argument("--interests", help="兴趣爱好 (逗号分隔)")
    parser.add_argument("--health", help="健康状况 (逗号分隔)")

    args = parser.parse_args()
    asyncio.run(enroll(args))


if __name__ == "__main__":
    main()
