#!/usr/bin/env python3
"""
天天训练数据集 — 质量保障 Harness (规划-执行-评估闭环)

核心循环:
    PLAN    → 分析当前数据缺口，生成下一批目标配置
    EXECUTE → 定向生成数据补充缺口
    EVALUATE→ 全维度质量评估，判定是否达标
    → 未达标则回到 PLAN，已达标则输出最终数据集

用法:
    python harness.py                      # 自动闭环直到达标
    python harness.py --evaluate-only      # 仅评估，不生成
    python harness.py --plan-only          # 仅输出计划，不执行
    python harness.py --batch-size 500     # 每轮生成批次大小
    python harness.py --max-rounds 20      # 最大迭代轮数
    python harness.py --workers 4          # 并发线程数
"""

from __future__ import annotations

import json
import re
import sys
import os
import argparse
import hashlib
import random
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
from copy import deepcopy
from typing import Optional

from quality_check import quality_check

# 从 generate.py 导入核心组件
from generate import (
    create_client, call_kimi, load_prompt_template, render_tiantian_prompt,
    generate_conversation, generate_family_member_line,
    FAMILY_MEMBERS, EMOTIONS, TIME_PERIODS,
    TOPICS_PERSONALITY, TOPICS_ADAPTATION, TOOL_CALL_EXPANSIONS,
    SAFETY_EXPANSIONS, INTENT_EXPANSIONS,
    SCRIPT_DIR, OUTPUT_FILE, MODEL,
    _file_lock, _progress_lock, append_to_jsonl, scene_id,
    load_progress, save_progress,
    DIRECTOR_SYSTEM_PROMPT, MAX_TOKENS_DIRECTOR,
)

# ═══════════════════════════════════════
# 质量标准 (达标线)
# ═══════════════════════════════════════

TARGET_COUNTS = {
    "personality": 5000,
    "adaptation": 3000,
    "tool_call": 1000,
    "safety": 1000,
    "intent": 2000,
}
TOTAL_TARGET = sum(TARGET_COUNTS.values())

THRESHOLDS = {
    # 类别
    "min_category_fill": 0.80,                # 每类至少达到目标的 80%

    # 多样性
    "max_first_line_repeat": 50,              # 单条开场白最多重复 N 次
    "min_unique_reply_starts_ratio": 0.20,    # 天天回复独立开头占比 >= 20%

    # 长度分布
    "min_short_ratio": 0.15,                  # 短回复(<50字)占比 >= 15%
    "max_long_ratio": 0.30,                   # 长回复(>80字)占比 <= 30%

    # 均衡性
    "max_emotion_skew": 5.0,                  # 最多/最少情绪比值 <= 5x

    # 质检
    "min_quality_pass_rate": 0.95,            # 质检通过率 >= 95%

    # 去重
    "max_near_dup_ratio": 0.03,               # 近似重复 <= 3%

    # 自然度
    "max_naturalness_issue_ratio": 0.10,      # 自然度问题 <= 10%
}

REPORT_FILE = SCRIPT_DIR / "harness_report.txt"
PLAN_LOG = SCRIPT_DIR / "harness_plan_log.jsonl"


# ═══════════════════════════════════════
# EVALUATE — 评估
# ═══════════════════════════════════════

class Metric:
    """单项评估指标"""
    def __init__(self, name: str, passed: bool, score: float, detail: str, gap: str = ""):
        self.name = name
        self.passed = passed
        self.score = score      # 0~1
        self.detail = detail
        self.gap = gap          # 差距描述，供 PLAN 阶段消费

    def __str__(self):
        icon = "PASS" if self.passed else "FAIL"
        s = f"  [{icon}] {self.name}: {self.detail}"
        if self.gap:
            s += f"  | 缺口: {self.gap}"
        return s


def load_data() -> list[dict]:
    if not OUTPUT_FILE.exists():
        return []
    data = []
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return data


def evaluate(data: list[dict]) -> tuple[list[Metric], bool]:
    """全面评估数据集，返回 (指标列表, 是否全部达标)"""
    metrics = []
    total = len(data)
    if total == 0:
        return [Metric("数据量", False, 0, "0条", f"需要{TOTAL_TARGET}条")], False

    # ── 1. 类别充足率 ──
    cats = Counter(d.get("metadata", {}).get("category", "unknown") for d in data)
    cat_details = []
    cat_gaps = []
    min_fill = 1.0
    for cat, target in TARGET_COUNTS.items():
        have = cats.get(cat, 0)
        fill = have / target if target > 0 else 1
        min_fill = min(min_fill, fill)
        cat_details.append(f"{cat}:{have}/{target}({fill:.0%})")
        if fill < THRESHOLDS["min_category_fill"]:
            need = int(target * THRESHOLDS["min_category_fill"]) - have
            cat_gaps.append(f"{cat}缺{max(0,need)}条")
    cat_pass = min_fill >= THRESHOLDS["min_category_fill"]
    metrics.append(Metric("类别充足", cat_pass, min_fill,
                          "  ".join(cat_details), "; ".join(cat_gaps)))

    # ── 2. 情绪均衡 ──
    emotions = Counter(d.get("metadata", {}).get("emotion", "") for d in data)
    if emotions:
        e_counts = list(emotions.values())
        e_skew = max(e_counts) / min(e_counts) if min(e_counts) > 0 else 99
        e_pass = e_skew <= THRESHOLDS["max_emotion_skew"]
        e_detail = "  ".join(f"{e}:{c}({c/total:.0%})" for e, c in emotions.most_common())
        e_gap = ""
        if not e_pass:
            least = emotions.most_common()[-1]
            most = emotions.most_common()[0]
            e_gap = f"'{least[0]}'需增加, 偏斜{e_skew:.1f}x"
        metrics.append(Metric("情绪均衡", e_pass, min(1, THRESHOLDS["max_emotion_skew"]/e_skew),
                              f"偏斜{e_skew:.1f}x | {e_detail}", e_gap))

    # ── 3. 开场白多样性 ──
    first_lines = Counter()
    for d in data:
        for m in d.get("messages", []):
            if m["role"] == "user":
                first_lines[m["content"][:30]] += 1
                break
    fl_max = first_lines.most_common(1)[0][1] if first_lines else 0
    fl_pass = fl_max <= THRESHOLDS["max_first_line_repeat"]
    fl_score = min(1, THRESHOLDS["max_first_line_repeat"] / fl_max) if fl_max > 0 else 1
    fl_gap = ""
    if not fl_pass:
        over = [(l, c) for l, c in first_lines.most_common(5) if c > THRESHOLDS["max_first_line_repeat"]]
        fl_gap = f"{len(over)}条开场白过度重复, 最高{fl_max}次"
    metrics.append(Metric("开场白多样性", fl_pass, fl_score,
                          f"独立:{len(first_lines)}, 最高重复:{fl_max}", fl_gap))

    # ── 4. 长度分布 ──
    lengths = [len(m["content"]) for d in data for m in d.get("messages", []) if m["role"] == "assistant"]
    if lengths:
        total_r = len(lengths)
        short_r = sum(1 for l in lengths if l < 50) / total_r
        long_r = sum(1 for l in lengths if l > 80) / total_r
        len_pass = short_r >= THRESHOLDS["min_short_ratio"] and long_r <= THRESHOLDS["max_long_ratio"]
        len_score = (min(1, short_r / THRESHOLDS["min_short_ratio"]) +
                     min(1, THRESHOLDS["max_long_ratio"] / long_r if long_r > 0 else 1)) / 2
        len_gap = ""
        if short_r < THRESHOLDS["min_short_ratio"]:
            len_gap += f"短回复仅{short_r:.0%}(需{THRESHOLDS['min_short_ratio']:.0%}) "
        if long_r > THRESHOLDS["max_long_ratio"]:
            len_gap += f"长回复{long_r:.0%}(上限{THRESHOLDS['max_long_ratio']:.0%})"
        metrics.append(Metric("长度分布", len_pass, len_score,
                              f"短{short_r:.0%} 中{1-short_r-long_r:.0%} 长{long_r:.0%} 均值{sum(lengths)/total_r:.0f}字",
                              len_gap))

    # ── 5. 质检通过率 ──
    qc_total = 0
    qc_fail = 0
    for d in data:
        meta = d.get("metadata", {})
        scene = {"category": meta.get("category", ""), "severity": meta.get("severity", "")}
        for m in d.get("messages", []):
            if m["role"] == "assistant":
                qc_total += 1
                p, _ = quality_check(m["content"], scene)
                if not p:
                    qc_fail += 1
    qc_rate = (qc_total - qc_fail) / qc_total if qc_total > 0 else 0
    qc_pass = qc_rate >= THRESHOLDS["min_quality_pass_rate"]
    metrics.append(Metric("质检通过率", qc_pass, qc_rate,
                          f"{qc_total-qc_fail}/{qc_total} ({qc_rate:.1%})",
                          f"{qc_fail}条需清洗" if not qc_pass else ""))

    # ── 6. 近似重复 ──
    hashes = Counter()
    for d in data:
        replies = [m["content"][:20] for m in d.get("messages", []) if m["role"] == "assistant"]
        h = hashlib.md5("|".join(replies).encode()).hexdigest()[:16]
        hashes[h] += 1
    dups = sum(c - 1 for c in hashes.values() if c > 1)
    dup_r = dups / total if total > 0 else 0
    dup_pass = dup_r <= THRESHOLDS["max_near_dup_ratio"]
    metrics.append(Metric("去重", dup_pass, min(1, THRESHOLDS["max_near_dup_ratio"]/dup_r) if dup_r > 0 else 1,
                          f"重复{dups}条({dup_r:.1%})",
                          f"需去除{dups}条" if not dup_pass else ""))

    # ── 7. 自然度 ──
    nat_issues = 0
    for d in data:
        msgs = [m for m in d.get("messages", []) if m["role"] != "system"]
        for i, m in enumerate(msgs):
            if m["role"] == "assistant" and i > 0 and msgs[i-1]["role"] == "user":
                user_msg = msgs[i-1]["content"]
                distress_words = ["疼", "不舒服", "难受", "哭", "害怕"]
                respond_words = ["疼", "怎么了", "没事", "小心", "哎呀", "啊", "关心", "陪"]
                if any(w in user_msg for w in distress_words):
                    if not any(w in m["content"] for w in respond_words):
                        nat_issues += 1
    nat_r = nat_issues / total if total > 0 else 0
    nat_pass = nat_r <= THRESHOLDS["max_naturalness_issue_ratio"]
    metrics.append(Metric("自然度", nat_pass, max(0, 1 - nat_r),
                          f"问题{nat_issues}条({nat_r:.0%})",
                          f"需改善{nat_issues}条" if not nat_pass else ""))

    all_pass = all(m.passed for m in metrics)
    return metrics, all_pass


# ═══════════════════════════════════════
# PLAN — 规划
# ═══════════════════════════════════════

def plan(metrics: list[Metric], data: list[dict], batch_size: int) -> list[dict]:
    """
    根据评估结果，生成下一批待执行的场景列表。
    返回 scene 列表，每个 scene 可直接传入 generate_conversation。
    """
    scenes = []
    total = len(data)

    # 统计当前分布
    cats = Counter(d.get("metadata", {}).get("category", "unknown") for d in data)
    emotions = Counter(d.get("metadata", {}).get("emotion", "") for d in data)
    first_lines_count = Counter()
    reply_lens = []
    for d in data:
        for m in d.get("messages", []):
            if m["role"] == "user":
                first_lines_count[m["content"][:30]] += 1
                break
            if m["role"] == "assistant":
                reply_lens.append(len(m["content"]))

    short_ratio = sum(1 for l in reply_lens if l < 50) / len(reply_lens) if reply_lens else 0

    # ── 计算各类别缺口 ──
    cat_needs = {}
    for cat, target in TARGET_COUNTS.items():
        have = cats.get(cat, 0)
        need = max(0, int(target * THRESHOLDS["min_category_fill"]) - have)
        cat_needs[cat] = need

    total_need = sum(cat_needs.values())
    if total_need == 0:
        # 所有类别已满，但可能其他指标未达标
        # 补充短回复 / 弱情绪
        total_need = batch_size

    # 按缺口比例分配 batch_size
    allocations = {}
    for cat, need in cat_needs.items():
        if total_need > 0:
            alloc = max(1, int(batch_size * need / total_need)) if need > 0 else 0
        else:
            alloc = 0
        allocations[cat] = alloc

    # 如果所有类别都满了，分配给长度/情绪修复
    if sum(allocations.values()) == 0:
        # 短回复不足 → 补 personality 的短对话
        if short_ratio < THRESHOLDS["min_short_ratio"]:
            allocations["personality"] = batch_size // 2
        # 情绪不均 → 补弱情绪
        if emotions:
            least_emotion = emotions.most_common()[-1][0]
            allocations["personality"] = allocations.get("personality", 0) + batch_size // 2

    # ── 确定弱情绪 ──
    weak_emotions = []
    if emotions:
        avg_emotion = sum(emotions.values()) / len(emotions)
        for e in EMOTIONS:
            if emotions.get(e, 0) < avg_emotion * 0.5:
                weak_emotions.append(e)
    if not weak_emotions:
        weak_emotions = EMOTIONS

    # ── 是否需要补短回复 ──
    need_short = short_ratio < THRESHOLDS["min_short_ratio"]

    # ── 获取过度重复的开场白（避免再用） ──
    overused_first_lines = {fl for fl, c in first_lines_count.items()
                            if c > THRESHOLDS["max_first_line_repeat"]}

    print(f"\n  [PLAN] 分配: {allocations}")
    print(f"  [PLAN] 弱情绪: {weak_emotions}")
    print(f"  [PLAN] 需补短回复: {need_short}")
    print(f"  [PLAN] 过度重复开场白: {len(overused_first_lines)}条")

    # ── 生成场景 ──
    # 每轮使用唯一前缀，避免 scene_id 碰撞
    round_uid = uuid.uuid4().hex[:8]

    def pick_member(prefer_type=None):
        if prefer_type and prefer_type in FAMILY_MEMBERS:
            return random.choice(FAMILY_MEMBERS[prefer_type])
        all_members = [m for members in FAMILY_MEMBERS.values() for m in members]
        return random.choice(all_members)

    def pick_emotion():
        # 偏向弱情绪
        if weak_emotions and random.random() < 0.6:
            return random.choice(weak_emotions)
        return random.choice(EMOTIONS)

    def pick_time():
        return random.choice(TIME_PERIODS)

    # --- personality ---
    n = allocations.get("personality", 0)
    if n > 0:
        all_topics = []
        for group, topics in TOPICS_PERSONALITY.items():
            for t in topics:
                all_topics.append((group, t))
        for _ in range(n):
            group, topic = random.choice(all_topics)
            # 所有开场白都生成 LLM 变体，最大化多样性
            first_line = f"__GENERATE_VARIANT__|{topic['first_line']}"

            # 儿童话题配儿童，老人话题配老人
            if group in ("child_study", "child_play"):
                member = pick_member("child")
            elif group == "hobby":
                member = pick_member(random.choice(["elder_male", "elder_female"]))
            else:
                member = pick_member()

            turns = 2 if (need_short and random.random() < 0.4) else random.choice([3, 4, 5])

            scenes.append({
                "id": f"h_p_{round_uid}_{len(scenes):05d}",
                "category": "personality",
                "role": member["role"],
                "audience": member["audience"],
                "scene": f"{pick_time()}，{topic['scene']}",
                "emotion": pick_emotion(),
                "emotion_arc": f"{pick_emotion()}全程",
                "goal": f"天天围绕'{group}'话题自然互动",
                "turns": turns,
                "first_line": first_line,
                "short_mode": turns <= 2,
            })

    # --- adaptation ---
    n = allocations.get("adaptation", 0)
    if n > 0:
        for _ in range(n):
            topic = random.choice(TOPICS_ADAPTATION)
            variant = random.choice(["elder", "child", "adult"])
            if variant == "elder":
                member = pick_member(random.choice(["elder_male", "elder_female"]))
                fl = topic["elder_line"]
                scene_desc = topic["scene_elder"]
                goal_prefix = "对老人：温和关怀"
            elif variant == "child":
                member = pick_member("child")
                fl = topic["child_line"]
                scene_desc = topic["scene_child"]
                goal_prefix = "对小孩：活泼互动"
            else:
                member = pick_member("adult")
                fl = topic["adult_line"]
                scene_desc = topic["scene_adult"]
                goal_prefix = "对成年人：汇报式但保持小孩语气"

            turns = 2 if (need_short and random.random() < 0.3) else random.choice([3, 4])
            scenes.append({
                "id": f"h_a_{round_uid}_{len(scenes):05d}",
                "category": "adaptation",
                "role": member["role"],
                "audience": member["audience"],
                "scene": f"{pick_time()}，{scene_desc}",
                "emotion": pick_emotion(),
                "emotion_arc": f"{pick_emotion()}→concerned",
                "goal": f"{goal_prefix}，围绕'{topic['topic']}'",
                "turns": turns,
                "first_line": f"__GENERATE_VARIANT__|{fl}",
                "short_mode": turns <= 2,
            })

    # --- tool_call ---
    n = allocations.get("tool_call", 0)
    if n > 0:
        for _ in range(n):
            tool = random.choice(TOOL_CALL_EXPANSIONS)
            member = pick_member()
            scenes.append({
                "id": f"h_t_{round_uid}_{len(scenes):05d}",
                "category": "tool_call",
                "role": member["role"],
                "audience": member["audience"],
                "scene": f"{pick_time()}，{tool['scene']}",
                "emotion": pick_emotion(),
                "goal": f"天天调用 {tool['expected_tool']}，保持小孩语气",
                "turns": random.choice([2, 3]),
                "first_line": tool["first_line"],
                "expected_tool": tool["expected_tool"],
                "short_mode": True,
            })

    # --- safety ---
    n = allocations.get("safety", 0)
    if n > 0:
        for _ in range(n):
            safety = random.choice(SAFETY_EXPANSIONS)
            if "老人" in safety["scene"] or "爷爷" in safety.get("first_line", ""):
                member = pick_member(random.choice(["elder_male", "elder_female"]))
            else:
                member = pick_member("child")
            scenes.append({
                "id": f"h_s_{round_uid}_{len(scenes):05d}",
                "category": "safety",
                "role": member["role"],
                "audience": member["audience"],
                "scene": f"{pick_time()}，{safety['scene']}",
                "emotion": random.choice(["happy", "sleepy"]),
                "emotion_arc": "→concerned",
                "goal": safety["goal"],
                "turns": random.choice([3, 4, 5]),
                "severity": safety["severity"],
                "first_line": safety["first_line"],
            })

    # --- intent ---
    n = allocations.get("intent", 0)
    if n > 0:
        for _ in range(n):
            intent = random.choice(INTENT_EXPANSIONS)
            role_type = intent["role_type"]
            if role_type == "elder":
                member = pick_member(random.choice(["elder_male", "elder_female"]))
            elif role_type == "child":
                member = pick_member("child")
            else:
                member = pick_member("adult")
            seq = intent["intent_sequence"]
            scenes.append({
                "id": f"h_i_{round_uid}_{len(scenes):05d}",
                "category": "intent",
                "role": member["role"],
                "audience": member["audience"],
                "scene": f"{pick_time()}，连续问不同类型问题",
                "emotion": pick_emotion(),
                "goal": "展示天天对不同意图的处理方式",
                "turns": len(seq) + 1,
                "first_line": seq[0],
                "intent_sequence": seq,
            })

    # 记录计划
    plan_entry = {
        "timestamp": datetime.now().isoformat(),
        "data_count": total,
        "allocations": allocations,
        "weak_emotions": weak_emotions,
        "need_short": need_short,
        "scenes_planned": len(scenes),
    }
    with open(PLAN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(plan_entry, ensure_ascii=False) + "\n")

    return scenes


# ═══════════════════════════════════════
# EXECUTE — 执行
# ═══════════════════════════════════════

def generate_first_line_variant(client, original: str) -> str:
    """用 LLM 为重复开场白生成变体"""
    prompt = f"""你是一个对话场景导演。请为以下家庭成员的台词生成一个意思相近但措辞不同的变体。

原句: "{original}"

要求:
- 保持相同的意图和情境
- 换一种口语化的说法
- 符合家庭日常对话
- 只输出变体台词，不要解释
- 不要用英文或 emoji"""

    try:
        reply = call_kimi(client, "你是一个对话导演。",
                          [{"role": "user", "content": prompt}],
                          max_tokens=80)
        reply = reply.strip().strip('"').strip("'").strip(""").strip(""")
        if reply and 3 < len(reply) < 80:
            return reply
    except Exception:
        pass
    return original  # fallback 到原句


def execute(scenes: list[dict], workers: int = 4) -> tuple[int, int]:
    """
    执行一批场景生成。
    返回 (成功数, 失败数)
    """
    prompt_template = load_prompt_template()
    success = 0
    failed = 0
    total = len(scenes)

    if total == 0:
        return 0, 0

    def worker_fn(scene):
        client = create_client()

        # 处理需要生成变体的开场白
        fl = scene.get("first_line", "")
        if fl.startswith("__GENERATE_VARIANT__|"):
            original = fl.split("|", 1)[1]
            fl = generate_first_line_variant(client, original)
            scene = deepcopy(scene)
            scene["first_line"] = fl

        # 短回复模式：强制追加简短指令 + 降低 max_tokens
        pt = prompt_template
        if scene.get("short_mode"):
            pt += "\n\n## 特别要求\n这是快问快答场景。回复必须控制在1~2句话、30字以内。像家人之间随口一答，不要展开。"
            # 通过修改 scene 传递 max_tokens 限制
            scene = deepcopy(scene)
            scene["_max_tokens"] = 100

        return generate_conversation(client, scene, pt)

    # harness 每轮场景都带 uuid，不需要 progress 跳过
    if workers <= 1:
        for i, scene in enumerate(scenes):
            result = worker_fn(scene)
            if result:
                append_to_jsonl(result)
                success += 1
            else:
                failed += 1
            if (i + 1) % 100 == 0:
                print(f"    执行中: {i+1}/{total} (成功:{success} 失败:{failed})")
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker_fn, scene): scene for scene in scenes}
            done = 0
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    failed += 1
                    done += 1
                    continue

                if result:
                    append_to_jsonl(result)
                    success += 1
                else:
                    failed += 1

                done += 1
                if done % 100 == 0:
                    print(f"    执行中: {done}/{total} (成功:{success} 失败:{failed})")

    return success, failed


# ═══════════════════════════════════════
# 数据清洗 (每轮评估后)
# ═══════════════════════════════════════

LONG_FILE = SCRIPT_DIR / "training_data_long.jsonl"


def clean_data(data: list[dict]) -> tuple[list[dict], dict]:
    """清洗：质检不通过的分流到 long 数据集 + 去重 + 开场白降采样"""
    stats = {"before": len(data), "qc_removed": 0, "dup_removed": 0,
             "oversample_removed": 0, "split_to_long": 0, "after": 0}

    # 质检 — 不通过的分流到 training_data_long.jsonl
    clean = []
    long_items = []
    for item in data:
        meta = item.get("metadata", {})
        scene_info = {"category": meta.get("category", ""), "severity": meta.get("severity", "")}
        ok = True
        is_length_issue = False
        for m in item.get("messages", []):
            if m["role"] == "assistant":
                p, reason = quality_check(m["content"], scene_info)
                if not p:
                    ok = False
                    if "过长" in reason:
                        is_length_issue = True
                    break
        if ok:
            clean.append(item)
        elif is_length_issue:
            long_items.append(item)
            stats["split_to_long"] += 1
        else:
            stats["qc_removed"] += 1

    # 分流的长回复追加写入 long 文件
    if long_items:
        with open(LONG_FILE, "a", encoding="utf-8") as f:
            for item in long_items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # 去重
    seen = {}
    deduped = []
    for item in clean:
        replies = [m["content"][:20] for m in item.get("messages", []) if m["role"] == "assistant"]
        h = hashlib.md5("|".join(replies).encode()).hexdigest()[:16]
        if h not in seen:
            seen[h] = True
            deduped.append(item)
        else:
            stats["dup_removed"] += 1

    # 开场白降采样
    fl_groups = defaultdict(list)
    for item in deduped:
        for m in item.get("messages", []):
            if m["role"] == "user":
                fl_groups[m["content"][:30]].append(item)
                break
    max_per = THRESHOLDS["max_first_line_repeat"]
    final = []
    for fl, items in fl_groups.items():
        if len(items) > max_per:
            final.extend(random.sample(items, max_per))
            stats["oversample_removed"] += len(items) - max_per
        else:
            final.extend(items)

    stats["after"] = len(final)
    return final, stats


def write_data(data: list[dict]):
    """覆写输出文件"""
    with _file_lock:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════

def print_report(round_num: int, metrics: list[Metric], all_pass: bool,
                 exec_stats: dict = None, clean_stats: dict = None):
    """打印一轮的报告"""
    total_score = sum(m.score for m in metrics) / len(metrics) if metrics else 0
    grade = "A" if total_score >= 0.9 else "B" if total_score >= 0.75 else "C" if total_score >= 0.6 else "D"

    print(f"\n{'═' * 60}")
    print(f"  第 {round_num} 轮评估  |  {datetime.now().strftime('%H:%M:%S')}  |  {grade} ({total_score:.0%})")
    print(f"{'═' * 60}")

    for m in metrics:
        print(m)

    if exec_stats:
        print(f"\n  [EXECUTE] 本轮生成: 成功{exec_stats['success']} 失败{exec_stats['failed']}")
    if clean_stats:
        print(f"  [CLEAN] 分流长回复:{clean_stats.get('split_to_long',0)} 质检移除:{clean_stats['qc_removed']} "
              f"去重:{clean_stats['dup_removed']} 降采样:{clean_stats['oversample_removed']} "
              f"| {clean_stats['before']}→{clean_stats['after']}")

    if all_pass:
        print(f"\n  所有指标达标! 数据集就绪。")
    else:
        failed_names = [m.name for m in metrics if not m.passed]
        print(f"\n  未达标: {', '.join(failed_names)}")

    print(f"{'═' * 60}\n")

    # 追加到报告文件
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n--- 第{round_num}轮 {datetime.now().isoformat()} ---\n")
        f.write(f"得分: {total_score:.0%} 等级: {grade}\n")
        for m in metrics:
            f.write(str(m) + "\n")


# ═══════════════════════════════════════
# 主循环
# ═══════════════════════════════════════

def run_loop(batch_size: int = 500, max_rounds: int = 30, workers: int = 4):
    """规划-执行-评估闭环"""

    print(f"\n{'━' * 60}")
    print(f"  天天训练数据 Harness — 闭环启动")
    print(f"  目标: {TOTAL_TARGET} 条  批次: {batch_size}  线程: {workers}")
    print(f"{'━' * 60}")

    # 清空报告
    REPORT_FILE.write_text(f"Harness 闭环报告 — {datetime.now().isoformat()}\n", encoding="utf-8")

    for round_num in range(1, max_rounds + 1):
        # ── EVALUATE ──
        data = load_data()
        print(f"\n{'─' * 40}")
        print(f"  第 {round_num} 轮 | 当前数据: {len(data)} 条")
        print(f"{'─' * 40}")

        metrics, all_pass = evaluate(data)

        if all_pass:
            print_report(round_num, metrics, True)
            # 最终清洗
            cleaned, cstats = clean_data(data)
            if cstats["before"] != cstats["after"]:
                write_data(cleaned)
                print(f"  最终清洗: {cstats['before']} → {cstats['after']}")
            print(f"\n  最终数据: {len(cleaned)} 条，保存在 {OUTPUT_FILE}")
            return True

        # ── PLAN ──
        print(f"\n  [PLAN] 分析缺口...")
        scenes = plan(metrics, data, batch_size)
        print(f"  [PLAN] 本轮计划生成 {len(scenes)} 条")

        if len(scenes) == 0:
            # 只需要清洗
            print(f"  [PLAN] 无新数据需生成，执行清洗...")
            cleaned, cstats = clean_data(data)
            write_data(cleaned)
            print_report(round_num, metrics, False, clean_stats=cstats)
            continue

        # ── EXECUTE ──
        print(f"\n  [EXECUTE] 开始生成 ({workers} 线程)...")
        t0 = time.time()
        ok, fail = execute(scenes, workers=workers)
        elapsed = time.time() - t0
        exec_stats = {"success": ok, "failed": fail, "elapsed": elapsed}
        print(f"  [EXECUTE] 完成: 成功{ok} 失败{fail} 用时{elapsed/60:.1f}分钟")

        # ── CLEAN ──
        data = load_data()
        cleaned, cstats = clean_data(data)
        if cstats["before"] != cstats["after"]:
            write_data(cleaned)

        # ── REPORT ──
        data = load_data()
        metrics, all_pass = evaluate(data)
        print_report(round_num, metrics, all_pass, exec_stats, cstats)

        if all_pass:
            print(f"\n  最终数据: {len(data)} 条，保存在 {OUTPUT_FILE}")
            return True

    print(f"\n  达到最大轮数 {max_rounds}，未完全达标。")
    return False


# ═══════════════════════════════════════
# 入口
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="天天训练数据集 Harness 闭环")
    parser.add_argument("--evaluate-only", action="store_true", help="仅评估，不生成")
    parser.add_argument("--plan-only", action="store_true", help="仅输出计划，不执行")
    parser.add_argument("--batch-size", type=int, default=500, help="每轮生成批次大小")
    parser.add_argument("--max-rounds", type=int, default=30, help="最大迭代轮数")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--clean", action="store_true", help="仅清洗现有数据")
    args = parser.parse_args()

    if args.evaluate_only:
        data = load_data()
        metrics, all_pass = evaluate(data)
        print_report(0, metrics, all_pass)
        return

    if args.plan_only:
        data = load_data()
        metrics, _ = evaluate(data)
        scenes = plan(metrics, data, args.batch_size)
        print(f"\n计划生成 {len(scenes)} 条场景")
        cats = Counter(s["category"] for s in scenes)
        for c, n in cats.most_common():
            print(f"  {c}: {n}")
        return

    if args.clean:
        data = load_data()
        cleaned, stats = clean_data(data)
        write_data(cleaned)
        print(f"清洗完成: {stats}")
        return

    # 主闭环
    run_loop(
        batch_size=args.batch_size,
        max_rounds=args.max_rounds,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
