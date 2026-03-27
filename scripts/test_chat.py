"""快速测试 — 通过 WebSocket 与 CompanionBot 文本对话"""

import asyncio
import json

import websockets

SERVER_URL = "ws://localhost:8765/ws/test-client"


async def chat(text: str, person_id: str = "test_user"):
    async with websockets.connect(SERVER_URL) as ws:
        # 发送文本消息
        await ws.send(
            json.dumps(
                {
                    "type": "text_input",
                    "text": text,
                    "person_id": person_id,
                }
            )
        )
        print(f"[你] {text}")

        # 等待回复 (文本 + 可能的 TTS 音频)
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "reply":
                        print(f"[小伴] {data['text']}")
                        print(f"  (情绪: {data.get('emotion', '?')})")
                        return
                # 跳过 TTS 音频二进制数据
        except TimeoutError:
            print("(超时无回复)")


async def interactive():
    print("=== CompanionBot 文本测试 ===")
    print("输入文字与小伴对话，输入 q 退出\n")
    while True:
        text = input("> ")
        if text.strip().lower() == "q":
            break
        if text.strip():
            await chat(text)
            print()


if __name__ == "__main__":
    asyncio.run(interactive())
