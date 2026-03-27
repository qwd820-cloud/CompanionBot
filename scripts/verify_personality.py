"""P1 人格系统验证 — 在真实 LLM 环境下测试人格引擎

验证项:
1. 同一问题在 happy/concerned 状态下回复是否不同
2. 对老人和小孩的回复风格差异
3. 记忆注入是否生效
4. TTS 情感参数映射

用法:
    python scripts/verify_personality.py [--llm-url http://localhost:8000/v1]
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.memory.episodic_memory import EpisodicMemory
from server.memory.long_term_profile import LongTermProfile
from server.memory.semantic_memory import SemanticMemory
from server.personality.engine import PersonalityEngine
from server.personality.llm_client import LLMClient
from server.personality.prompt_builder import PromptBuilder

PERSONALITY_CONFIG = {
    "personality": {
        "name": "小伴",
        "traits": {
            "warmth": 0.85,
            "humor": 0.6,
            "patience": 0.9,
            "curiosity": 0.7,
            "directness": 0.5,
        },
        "quirks": [
            "对天气话题特别感兴趣",
            "喜欢用比喻来解释事情",
            "对家人做的菜总是很感兴趣",
        ],
    },
    "adaptation": {
        "elder": {
            "speech_rate": "slow",
            "vocabulary": "simple",
            "topics": ["健康关怀", "回忆", "家常"],
            "avoid": ["网络用语", "复杂术语"],
        },
        "child": {
            "speech_rate": "normal",
            "vocabulary": "lively",
            "topics": ["学习鼓励", "兴趣引导", "故事"],
            "avoid": ["恐怖内容", "过于复杂的概念"],
        },
    },
}

TEST_QUESTION = "今天天气真好，适合做点什么呢？"


async def setup_memory(tmpdir: str):
    """初始化测试用记忆系统"""
    db_path = f"{tmpdir}/test.db"
    chroma_dir = f"{tmpdir}/chroma"

    episodic = EpisodicMemory(db_path=db_path)
    semantic = SemanticMemory(persist_dir=chroma_dir)
    profile = LongTermProfile(db_path=db_path)

    await episodic.initialize()
    await semantic.initialize()
    await profile.initialize()

    # 注册老人
    await profile.add_member(
        person_id="grandpa",
        name="王爷爷",
        nickname="爷爷",
        role="elder",
        age=75,
        relationship="爷爷",
        interests=["下棋", "听戏曲", "种花"],
        health_conditions=["高血压", "膝盖不好"],
    )
    # 注册小孩
    await profile.add_member(
        person_id="xiaoming",
        name="小明",
        nickname="小明",
        role="child",
        age=8,
        relationship="孙子",
        interests=["画画", "看动画片", "踢足球"],
    )
    # 添加一些情景记忆
    await episodic.add_episode(
        "grandpa", "爷爷说膝盖又疼了，情绪有点低落", "concerned", 0.8
    )
    await episodic.add_episode(
        "grandpa", "爷爷下午和邻居下了一盘棋，赢了，很开心", "happy", 0.5
    )

    return episodic, semantic, profile


async def test_emotion_difference(llm: LLMClient, episodic, semantic, profile):
    """验证 1: 同一问题在 happy/concerned 状态下回复不同"""
    print("=" * 60)
    print("验证 1: 情绪状态影响回复风格")
    print("=" * 60)

    for emotion in ["happy", "concerned"]:
        engine = PersonalityEngine(config=PERSONALITY_CONFIG)
        engine.current_emotion = emotion

        builder = PromptBuilder(engine, episodic, semantic, profile)
        context = {
            "turns": [{"role": "user", "text": TEST_QUESTION, "person_id": "爷爷"}]
        }
        messages = await builder.build("grandpa", context)

        print(f"\n--- 情绪: {emotion} ---")
        print(f"System prompt 摘要: ...{messages[0]['content'][-200:]}")

        result = await llm.chat(messages, task_type="daily")
        print(f"回复: {result['content']}")
        print(f"模型: {result['model']}")

    print()


async def test_role_adaptation(llm: LLMClient, episodic, semantic, profile):
    """验证 2: 对老人和小孩的回复风格差异"""
    print("=" * 60)
    print("验证 2: 对话对象适配 (老人 vs 小孩)")
    print("=" * 60)

    for person_id, label in [("grandpa", "老人(爷爷)"), ("xiaoming", "小孩(小明)")]:
        engine = PersonalityEngine(config=PERSONALITY_CONFIG)
        builder = PromptBuilder(engine, episodic, semantic, profile)
        context = {
            "turns": [
                {"role": "user", "text": "给我讲个故事吧", "person_id": person_id}
            ]
        }
        messages = await builder.build(person_id, context)

        print(f"\n--- 对象: {label} ---")
        result = await llm.chat(messages, task_type="daily")
        print(f"回复: {result['content']}")

    print()


async def test_memory_injection(llm: LLMClient, episodic, semantic, profile):
    """验证 3: 记忆注入是否生效"""
    print("=" * 60)
    print("验证 3: 记忆注入 (提及健康问题时是否引用历史)")
    print("=" * 60)

    engine = PersonalityEngine(config=PERSONALITY_CONFIG)
    builder = PromptBuilder(engine, episodic, semantic, profile)

    # 用户提到膝盖 → 应该能结合之前的情景记忆
    context = {
        "turns": [
            {"role": "user", "text": "我膝盖今天又有点不舒服了", "person_id": "爷爷"}
        ]
    }
    messages = await builder.build("grandpa", context)

    print("\nSystem prompt 中的记忆部分:")
    system = messages[0]["content"]
    if "记忆" in system:
        # 找到记忆相关段落
        for line in system.split("\n"):
            if "记忆" in line or "互动" in line or "膝盖" in line:
                print(f"  {line}")

    result = await llm.chat(messages, task_type="daily")
    print(f"\n回复: {result['content']}")
    print()


async def test_tts_emotion_mapping():
    """验证 4: TTS 情感参数映射"""
    print("=" * 60)
    print("验证 4: TTS 情感参数映射")
    print("=" * 60)

    try:
        from server.output.tts import TTSEngine

        tts = TTSEngine()

        for emotion in ["happy", "concerned", "neutral", "tired"]:
            params = tts.get_emotion_params(emotion)
            print(
                f"  {emotion}: voice={params.get('voice', 'N/A')}, "
                f"rate={params.get('rate', 'N/A')}, pitch={params.get('pitch', 'N/A')}"
            )
    except Exception as e:
        print(f"  TTS 模块跳过: {e}")

    print()


async def main(llm_url: str):
    print(f"LLM URL: {llm_url}\n")

    # 检查 LLM 可用性
    llm = LLMClient(local_base_url=llm_url)
    available = await llm.check_health()
    if not available:
        print("LLM 引擎不可用！请先启动 SGLang/vLLM:")
        print("  python -m sglang.launch_server --model Qwen/Qwen3.5-27B --port 8000")
        print("\n仍然运行非 LLM 依赖的测试...\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        episodic, semantic, profile = await setup_memory(tmpdir)

        if available:
            await test_emotion_difference(llm, episodic, semantic, profile)
            await test_role_adaptation(llm, episodic, semantic, profile)
            await test_memory_injection(llm, episodic, semantic, profile)

        await test_tts_emotion_mapping()

    print("=== 人格系统验证完成 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-url", default="http://localhost:8000/v1")
    args = parser.parse_args()
    asyncio.run(main(args.llm_url))
