"""模拟对话测试 — 用文本模拟与机器人的对话"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def simulate_text(person_id: str):
    """文本模式模拟对话"""
    import websockets

    uri = "ws://localhost:8765/ws/simulator"
    print(f"连接到 {uri}...")
    print(f"当前身份: {person_id}")
    print("输入对话内容 (输入 'quit' 退出):\n")

    async with websockets.connect(uri) as ws:
        # 启动接收任务
        async def receiver():
            try:
                async for message in ws:
                    data = json.loads(message)
                    if data.get("type") == "reply":
                        emotion = data.get("emotion", "neutral")
                        print(f"\n小伴 [{emotion}]: {data.get('text', '')}\n> ", end="", flush=True)
                    elif data.get("type") == "alert":
                        print(f"\n[警报] {data.get('message', '')}\n> ", end="", flush=True)
            except Exception:
                pass

        recv_task = asyncio.create_task(receiver())

        try:
            while True:
                text = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("> ")
                )
                if text.strip().lower() == "quit":
                    break
                if not text.strip():
                    continue

                await ws.send(json.dumps({
                    "type": "text_input",
                    "person_id": person_id,
                    "text": text,
                }))
        finally:
            recv_task.cancel()

    print("对话结束")


async def simulate_audio(audio_path: str):
    """音频模式模拟对话"""
    import struct
    import websockets

    uri = "ws://localhost:8765/ws/simulator"
    print(f"连接到 {uri}...")
    print(f"播放音频: {audio_path}")

    audio_file = Path(audio_path)
    if not audio_file.exists():
        print(f"错误: 文件不存在: {audio_path}")
        return

    # 读取 WAV 文件
    import wave
    with wave.open(str(audio_file), "rb") as wf:
        audio_data = wf.readframes(wf.getnframes())

    async with websockets.connect(uri) as ws:
        # 接收回复
        async def receiver():
            try:
                async for message in ws:
                    if isinstance(message, str):
                        data = json.loads(message)
                        if data.get("type") == "reply":
                            print(f"小伴: {data.get('text', '')}")
            except Exception:
                pass

        recv_task = asyncio.create_task(receiver())

        # 分块发送音频 (模拟实时流)
        chunk_size = 3200  # 100ms @ 16kHz 16-bit
        msg_type = 1  # AUDIO
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i:i + chunk_size]
            header = struct.pack("!B", msg_type)
            await ws.send(header + chunk)
            await asyncio.sleep(0.1)  # 模拟实时

        # 等待回复
        await asyncio.sleep(5)
        recv_task.cancel()

    print("模拟完成")


def main():
    parser = argparse.ArgumentParser(description="模拟对话测试")
    parser.add_argument("--audio", help="音频文件路径 (WAV)")
    parser.add_argument("--person-id", default="test_user", help="模拟的人物 ID")
    parser.add_argument("--text", action="store_true", help="文本对话模式")

    args = parser.parse_args()

    if args.audio:
        asyncio.run(simulate_audio(args.audio))
    else:
        asyncio.run(simulate_text(args.person_id))


if __name__ == "__main__":
    main()
