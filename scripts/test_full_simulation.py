"""端到端模拟测试 — 模拟真人与小伴的完整对话流程

测试内容:
1. 健康检查
2. 注册家庭成员档案
3. 多轮对话（日常闲聊、健康话题、情感话题）
4. 记忆沉淀验证
5. 新会话中记忆召回验证
"""

import asyncio
import json
import sys
import time

import websockets

SERVER = "ws://localhost:8765/ws"
HEALTH_URL = "http://localhost:8765/health"

# 模拟对话场景
SCENARIOS = [
    {
        "name": "场景1: 注册家庭成员 + 初次见面",
        "person_id": "grandpa_wang",
        "setup": {
            "type": "enroll_profile",
            "person_id": "grandpa_wang",
            "name": "王建国",
            "nickname": "爷爷",
            "role": "elder",
            "age": 72,
            "relationship": "爷爷",
        },
        "turns": [
            "你好呀小伴，我是爷爷",
            "今天早上在公园打了一套太极拳，感觉挺好的",
            "对了，我孙子小明下个月要考试了，你说我该怎么鼓励他？",
        ],
    },
    {
        "name": "场景2: 健康关怀对话",
        "person_id": "grandpa_wang",
        "turns": [
            "小伴，我今天膝盖又有点疼",
            "已经吃过了，就是下楼梯的时候不太舒服",
            "好的好的，我会注意的",
        ],
    },
    {
        "name": "场景3: 新会话 — 测试记忆召回",
        "person_id": "grandpa_wang",
        "turns": [
            "小伴，你还记得我之前跟你说过什么吗？",
            "小明考试的事你还记得不？",
        ],
    },
    {
        "name": "场景4: 小孩对话 — 测试对象适配",
        "person_id": "xiaoming",
        "setup": {
            "type": "enroll_profile",
            "person_id": "xiaoming",
            "name": "小明",
            "nickname": "小明",
            "role": "child",
            "age": 10,
            "relationship": "孙子",
        },
        "turns": [
            "小伴小伴！我今天数学考了100分！",
            "老师还表扬我了呢，说我进步很大",
        ],
    },
]


def print_divider(text=""):
    print(f"\n{'=' * 60}")
    if text:
        print(f"  {text}")
        print(f"{'=' * 60}")


def print_turn(role, text, extra=""):
    icon = "👤" if role == "user" else "🤖"
    print(f"  {icon} [{role}] {text}")
    if extra:
        print(f"      {extra}")


async def check_health():
    """检查服务健康状态"""
    import urllib.request

    try:
        resp = urllib.request.urlopen("http://localhost:8765/health", timeout=5)
        data = json.loads(resp.read())
        return data.get("status") == "ok"
    except Exception as e:
        print(f"  ✗ 健康检查失败: {e}")
        return False


async def run_scenario(scenario):
    """运行单个对话场景"""
    print_divider(scenario["name"])
    person_id = scenario["person_id"]
    client_id = f"sim-{person_id}-{int(time.time())}"

    async with websockets.connect(f"{SERVER}/{client_id}") as ws:
        # 注册档案（如果有）
        setup = scenario.get("setup")
        if setup:
            await ws.send(json.dumps(setup))
            print(f"  📋 注册成员: {setup.get('name')} ({setup.get('role')})")
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=10)
                if isinstance(resp, str):
                    data = json.loads(resp)
                    success = data.get("success", False)
                    print(f"     {'✓' if success else '✗'} {data.get('message', '')}")
            except TimeoutError:
                print("     (注册无响应，继续)")
            print()

        # 多轮对话
        for i, user_text in enumerate(scenario["turns"], 1):
            print(f"  --- 第 {i} 轮 ---")
            print_turn("user", user_text)

            await ws.send(
                json.dumps(
                    {
                        "type": "text_input",
                        "text": user_text,
                        "person_id": person_id,
                    }
                )
            )

            # 收集回复
            got_reply = False
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    if isinstance(msg, str):
                        data = json.loads(msg)
                        if data.get("type") == "reply":
                            print_turn(
                                "小伴",
                                data["text"],
                                f"(情绪: {data.get('emotion', '?')})",
                            )
                            got_reply = True
                            break
                    # 跳过 TTS 二进制数据
            except TimeoutError:
                if not got_reply:
                    print("  ⚠ 回复超时")
            print()

            # 模拟真人说话间隔
            await asyncio.sleep(1)

    # 断开后等待记忆沉淀
    print("  ⏳ 等待记忆沉淀 (3秒)...")
    await asyncio.sleep(3)


async def main():
    print_divider("CompanionBot 端到端模拟测试")
    print(f"  服务器: {SERVER}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 1. 健康检查
    print("  [1/2] 健康检查...", end=" ")
    if await check_health():
        print("✓ 服务正常")
    else:
        print("✗ 服务不可用，退出")
        sys.exit(1)

    # 2. LLM 连通性
    print("  [2/2] LLM 连通性...", end=" ")
    import urllib.request

    try:
        req = urllib.request.Request("http://localhost:57847/v1/models", method="GET")
        urllib.request.urlopen(req, timeout=5)
        print("✓ LLM 可用")
    except Exception as e:
        print(f"⚠ LLM 可能不可用: {e}")

    # 3. 运行所有场景
    for scenario in SCENARIOS:
        try:
            await run_scenario(scenario)
        except Exception as e:
            print(f"  ✗ 场景执行失败: {e}")

    print_divider("测试完成")
    print("  所有场景执行完毕。")
    print("  查看服务日志: tail -f /tmp/companion-bot.log")
    print()


if __name__ == "__main__":
    asyncio.run(main())
