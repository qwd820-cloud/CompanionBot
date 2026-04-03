"""
天天训练数据 — 质量检查模块

纯本地规则检查，不调用任何 API。
检查天天的回复是否符合人格设定和场景要求。
"""

import re


# 禁忌短语列表
FORBIDDEN_PHRASES = [
    "我理解你的感受",
    "我理解您的感受",
    "我能理解你的感受",
    "作为一个人工智能",
    "作为人工智能",
    "作为一个AI",
    "作为AI",
    "作为机器人",
    "作为一个机器人",
    "我只是一个程序",
    "我是一个语言模型",
]

# 暴露身份的关键词组合: (触发词, 上下文词列表)
IDENTITY_PATTERNS = [
    ("作为", ["AI", "机器人", "人工智能", "程序", "语言模型", "助手"]),
    ("我是", ["AI", "机器人", "人工智能", "程序", "语言模型"]),
]

# 安全场景中不应淡化危险的表达
SAFETY_DOWNPLAY_PHRASES = [
    "不要紧",
    "没什么大不了",
    "没关系的",
]

# emoji 范围 (常见 emoji Unicode 区间)
EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # 表情符号
    "\U0001f300-\U0001f5ff"  # 符号和象形文字
    "\U0001f680-\U0001f6ff"  # 交通和地图
    "\U0001f900-\U0001f9ff"  # 补充表情符号
    "\U0001fa00-\U0001fa6f"  # 棋子符号
    "\U0001fa70-\U0001faff"  # 符号扩展
    "\U00002702-\U000027b0"  # 装饰符号
    "\U0000fe00-\U0000fe0f"  # 变体选择器
    "\U0000200d"             # 零宽连接符
    "\U000020e3"             # 键帽
    "\U00002600-\U000026ff"  # 杂项符号
    "]"
)

# 英文单词检测 (4个及以上连续英文字母视为英文词汇)
ENGLISH_WORD_PATTERN = re.compile(r'[a-zA-Z]{4,}')


def quality_check(reply: str, scene: dict) -> tuple[bool, str]:
    """
    检查天天的回复质量。

    Args:
        reply: 天天的回复文本
        scene: 场景字典，包含 category、severity 等字段

    Returns:
        (passed, reason): 是否通过，不通过时返回原因
    """
    # 空回复检查
    if not reply or not reply.strip():
        return False, "回复为空"

    reply = reply.strip()

    # ── 长度检查 ──
    if len(reply) < 5:
        return False, f"回复过短({len(reply)}字)"
    if len(reply) > 120:
        return False, f"回复过长({len(reply)}字)"

    # ── 禁忌词检查 ──
    if "您" in reply:
        return False, "使用了'您'"

    if "首先" in reply and "其次" in reply:
        return False, "使用了'首先其次'结构"

    if "首先" in reply and "最后" in reply:
        return False, "使用了'首先最后'结构"

    for phrase in FORBIDDEN_PHRASES:
        if phrase in reply:
            return False, f"包含禁忌短语: '{phrase}'"

    # ── 身份暴露检查 ──
    for trigger, contexts in IDENTITY_PATTERNS:
        if trigger in reply:
            for ctx in contexts:
                if ctx in reply:
                    return False, f"暴露身份: '{trigger}...{ctx}'"

    # ── emoji 检查 ──
    if EMOJI_PATTERN.search(reply):
        return False, "包含emoji"

    # ── 英文检查 ──
    english_matches = ENGLISH_WORD_PATTERN.findall(reply)
    if english_matches:
        return False, f"包含英文词汇: {english_matches[:3]}"

    # ── 格式检查 ──
    # 不应有 markdown 格式
    if re.search(r'^#{1,3}\s', reply, re.MULTILINE):
        return False, "包含markdown标题格式"
    if "**" in reply:
        return False, "包含markdown加粗格式"
    if re.search(r'^\d+\.\s', reply, re.MULTILINE):
        # 允许少量编号，但连续3个以上不行
        numbered = re.findall(r'^\d+\.\s', reply, re.MULTILINE)
        if len(numbered) >= 3:
            return False, "使用了编号列表（说教感）"

    # ── 说教检查 ──
    lecture_patterns = [
        "第一，", "第二，", "第三，",
        "一方面", "另一方面",
        "总而言之", "综上所述",
        "需要注意的是",
        "值得一提的是",
    ]
    for pattern in lecture_patterns:
        if pattern in reply:
            return False, f"说教语气: '{pattern}'"

    # ── 安全场景特殊检查 ──
    category = scene.get("category", "")
    if category == "safety":
        # 不应淡化危险
        if "别担心" in reply and "没事" in reply:
            return False, "安全场景不应淡化危险"
        for phrase in SAFETY_DOWNPLAY_PHRASES:
            if phrase in reply:
                return False, f"安全场景不应淡化危险: '{phrase}'"

        # P0 场景必须表现出紧迫感
        severity = scene.get("severity", "")
        if severity == "P0":
            concern_words = ["疼", "小心", "别动", "别乱动", "通知", "打电话",
                             "家人", "医生", "120", "急救", "怎么了", "哪里"]
            has_concern = any(w in reply for w in concern_words)
            if not has_concern:
                return False, "P0场景缺乏紧迫感/关切表达"

    return True, "通过"


def batch_quality_check(reply: str, scene: dict, strict: bool = False) -> tuple[bool, list[str]]:
    """
    批量检查，返回所有不通过的原因（用于统计分析）。

    Args:
        reply: 天天的回复文本
        scene: 场景字典
        strict: 严格模式，额外检查

    Returns:
        (passed, reasons): 是否全部通过，所有不通过原因的列表
    """
    reasons = []

    # 基础检查
    passed, reason = quality_check(reply, scene)
    if not passed:
        reasons.append(reason)

    if strict:
        reply = reply.strip()

        # 严格模式：检查回复是否过于模板化
        template_starts = ["好的，", "当然，", "没问题，", "嗯，好的，"]
        for start in template_starts:
            if reply.startswith(start):
                reasons.append(f"模板化开头: '{start}'")
                break

        # 严格模式：检查是否有重复句式
        sentences = re.split(r'[。！？]', reply)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) >= 2:
            for i in range(len(sentences) - 1):
                for j in range(i + 1, len(sentences)):
                    if sentences[i][:5] == sentences[j][:5] and len(sentences[i]) > 5:
                        reasons.append(f"重复句式: '{sentences[i][:10]}...'")

    return len(reasons) == 0, reasons
