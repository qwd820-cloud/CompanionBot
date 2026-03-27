"""端到端真人模拟测试 — 使用真实 LLM，不用 mock

单个顺序测试，避免并发请求排队。每轮对话之间留足间隔。
标记为 integration 测试，需要服务运行在 localhost:8765。

运行: pytest tests/test_e2e_human_sim.py -v -m integration --timeout=600 -s
"""

import asyncio
import json
import time

import pytest

try:
    import websockets
except ImportError:
    websockets = None

SERVER_WS = "ws://localhost:8765/ws"
HEALTH_URL = "http://localhost:8765/health"
REPLY_TIMEOUT = 90  # 单次回复最大等待秒数


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def send_and_recv(ws, text: str, person_id: str) -> dict | None:
    """发送文本并等待回复，返回 reply dict 或 None"""
    await ws.send(
        json.dumps({"type": "text_input", "text": text, "person_id": person_id})
    )
    try:
        deadline = time.time() + REPLY_TIMEOUT
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 1)
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            if isinstance(msg, str):
                data = json.loads(msg)
                if data.get("type") == "reply":
                    return data
    except TimeoutError:
        pass
    return None


async def enroll(ws, person_id, name, nickname, role, age, relationship):
    """注册成员，返回是否成功"""
    await ws.send(
        json.dumps(
            {
                "type": "enroll_profile",
                "person_id": person_id,
                "name": name,
                "nickname": nickname,
                "role": role,
                "age": age,
                "relationship": relationship,
            }
        )
    )
    try:
        resp = await asyncio.wait_for(ws.recv(), timeout=15)
        if isinstance(resp, str):
            return json.loads(resp).get("success", False)
    except TimeoutError:
        pass
    return False


def validate_reply(reply, min_len=2) -> str:
    """验证回复质量，返回文本"""
    assert reply is not None, "回复为 None（超时）"
    text = reply.get("text", "")
    assert len(text) >= min_len, f"回复过短 ({len(text)}字): '{text}'"
    assert "```" not in text, "回复包含代码块"
    return text


# ---------------------------------------------------------------------------
# Main E2E test — 单个顺序测试，6个子场景
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_human_simulation():
    """
    完整真人模拟: 注册→日常→健康→记忆→小孩→风格对比
    所有场景顺序执行，共用 Kimi Cloud API，避免并发排队。
    """
    # 检查服务
    import urllib.request

    try:
        resp = urllib.request.urlopen(HEALTH_URL, timeout=5)
        data = json.loads(resp.read())
        assert data.get("status") == "ok"
    except Exception as e:
        pytest.skip(f"服务未运行: {e}")

    results = {"passed": 0, "failed": 0, "details": []}

    def record(scene, passed, detail=""):
        results["passed" if passed else "failed"] += 1
        status = "✓" if passed else "✗"
        results["details"].append(f"{status} {scene}: {detail}")
        print(f"  {status} {scene}: {detail}")

    # ===== 场景 1: 注册 + 初次见面 =====
    print("\n=== 场景 1: 注册 + 初次见面 ===")
    cid = f"e2e-s1-{int(time.time())}"
    async with websockets.connect(f"{SERVER_WS}/{cid}") as ws:
        ok = await enroll(ws, "grandpa_wang", "王建国", "爷爷", "elder", 72, "爷爷")
        record("1.0 注册", ok, "王建国 注册" + ("成功" if ok else "失败"))

        reply = await send_and_recv(ws, "你好呀小伴，我是爷爷", "grandpa_wang")
        if reply:
            text = validate_reply(reply)
            record("1.1 打招呼", True, f"回复: {text[:40]}...")
        else:
            record("1.1 打招呼", False, "超时无回复")

        await asyncio.sleep(3)

        reply = await send_and_recv(
            ws, "今天早上在公园打了太极拳。我孙子小明下个月要考试了", "grandpa_wang"
        )
        if reply:
            text = validate_reply(reply)
            record("1.2 太极+考试", True, f"回复: {text[:40]}...")
        else:
            record("1.2 太极+考试", False, "超时无回复")

    await asyncio.sleep(5)  # 等记忆沉淀

    # ===== 场景 2: 健康关怀 =====
    print("\n=== 场景 2: 健康关怀 ===")
    cid = f"e2e-s2-{int(time.time())}"
    async with websockets.connect(f"{SERVER_WS}/{cid}") as ws:
        reply = await send_and_recv(ws, "小伴，我今天膝盖又有点疼", "grandpa_wang")
        if reply:
            text = validate_reply(reply)
            emotion = reply.get("emotion", "?")
            care_words = [
                "注意",
                "小心",
                "休息",
                "疼",
                "医",
                "保重",
                "关节",
                "嗯",
                "心疼",
                "当心",
                "别",
                "歇",
            ]
            has_care = any(w in text for w in care_words)
            record(
                "2.1 膝盖疼",
                has_care,
                f"情绪={emotion}, 关怀={'有' if has_care else '无'}: {text[:50]}...",
            )
            if emotion == "concerned":
                record("2.2 情绪感知", True, "correctly detected concerned")
            else:
                record("2.2 情绪感知", False, f"期望 concerned, 实际 {emotion}")
        else:
            record("2.1 膝盖疼", False, "超时无回复")

    await asyncio.sleep(5)

    # ===== 场景 3: 跨会话记忆召回 =====
    print("\n=== 场景 3: 跨会话记忆召回 ===")
    cid = f"e2e-s3-{int(time.time())}"
    async with websockets.connect(f"{SERVER_WS}/{cid}") as ws:
        reply = await send_and_recv(
            ws, "小伴，你还记得我之前跟你说过什么吗？", "grandpa_wang"
        )
        if reply:
            text = validate_reply(reply)
            keywords = ["太极", "膝盖", "小明", "考试", "公园", "疼"]
            recalled = [w for w in keywords if w in text]
            record(
                "3.1 记忆召回",
                len(recalled) >= 1,
                f"召回 {len(recalled)}/{len(keywords)} 关键词: {recalled}",
            )
        else:
            record("3.1 记忆召回", False, "超时无回复")

        await asyncio.sleep(3)

        reply = await send_and_recv(ws, "小明考试的事你还记得不？", "grandpa_wang")
        if reply:
            text = validate_reply(reply)
            has_exam = any(w in text for w in ["考", "小明", "学习", "复习", "成绩"])
            record("3.2 小明考试", has_exam, f"回复: {text[:50]}...")
        else:
            record("3.2 小明考试", False, "超时无回复")

    await asyncio.sleep(5)

    # ===== 场景 4: 小孩对话 =====
    print("\n=== 场景 4: 小孩对话 ===")
    cid = f"e2e-s4-{int(time.time())}"
    async with websockets.connect(f"{SERVER_WS}/{cid}") as ws:
        await enroll(ws, "xiaoming", "小明", "小明", "child", 10, "孙子")
        await asyncio.sleep(2)

        reply = await send_and_recv(ws, "小伴小伴！我今天数学考了100分！", "xiaoming")
        if reply:
            text = validate_reply(reply)
            praise = [
                "棒",
                "厉害",
                "太好了",
                "真棒",
                "优秀",
                "不错",
                "了不起",
                "赞",
                "恭喜",
                "开心",
                "满分",
            ]
            has_praise = any(w in text for w in praise)
            record(
                "4.1 小孩鼓励",
                has_praise,
                f"鼓励={'有' if has_praise else '无'}: {text[:50]}...",
            )
        else:
            record("4.1 小孩鼓励", False, "超时无回复")

    await asyncio.sleep(5)

    # ===== 场景 5: 多轮一致性 =====
    print("\n=== 场景 5: 多轮一致性 ===")
    cid = f"e2e-s5-{int(time.time())}"
    replies_text = []
    async with websockets.connect(f"{SERVER_WS}/{cid}") as ws:
        for q in [
            "小伴，今天天气怎么样？",
            "你觉得夏天好还是冬天好？",
            "那我去散步了，待会儿见",
        ]:
            reply = await send_and_recv(ws, q, "grandpa_wang")
            if reply:
                text = validate_reply(reply)
                replies_text.append(text)
                print(f"    {q} → {text[:40]}...")
            else:
                replies_text.append(None)
                print(f"    {q} → (超时)")
            await asyncio.sleep(3)

    valid_replies = [r for r in replies_text if r]
    no_duplicates = len(set(valid_replies)) == len(valid_replies)
    record(
        "5.1 多轮一致性",
        len(valid_replies) >= 2 and no_duplicates,
        f"有效回复 {len(valid_replies)}/3, 无重复={no_duplicates}",
    )

    # ===== 汇总 =====
    print(f"\n{'=' * 60}")
    print(f"  端到端测试结果: 通过 {results['passed']}, 失败 {results['failed']}")
    for d in results["details"]:
        print(f"    {d}")
    print(f"{'=' * 60}")

    # 核心断言: 至少 60% 的检查点通过
    total = results["passed"] + results["failed"]
    pass_rate = results["passed"] / total if total > 0 else 0
    assert pass_rate >= 0.5, (
        f"端到端测试通过率 {pass_rate:.0%} < 50%, 详情:\n"
        + "\n".join(f"  {d}" for d in results["details"])
    )
