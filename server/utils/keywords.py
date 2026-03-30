"""集中管理的关键词列表 — 健康、情绪、安全等

支持精确匹配和模糊匹配 (编辑距离)。
"""

import re

# 健康相关关键词 (用于情绪推断、重要性评估、异常检测)
HEALTH_KEYWORDS = [
    "疼",
    "痛",
    "不舒服",
    "头晕",
    "难受",
    "生病",
    "住院",
    "血压",
    "吃药",
    "医院",
    "检查",
    "发烧",
    "咳嗽",
]

# 紧急健康关键词 (直接触发 P1 通知)
HEALTH_URGENT_KEYWORDS = [
    "胸闷",
    "喘不上气",
    "喘不过气",
    "呼吸困难",
    "很晕",
    "眼前发黑",
    "心脏",
    "心口疼",
    "透不过气",
    "浑身没劲",
    "站不住",
    "手脚发麻",
]

# 呼救/求助关键词 (直接触发 P0 安全预警)
DISTRESS_KEYWORDS = [
    "救命",
    "帮帮我",
    "快来人",
    "摔倒了",
    "不行了",
    "很痛",
    "出事了",
    "受伤了",
    "流血了",
    "起不来",
    "动不了",
]

# 跌倒相关表达 (模糊匹配模式 — 正则)
FALL_PATTERNS = [
    r"摔.{0,2}(了|倒|跤|一跤)",
    r"(滑|绊|磕).{0,2}(了|倒|到)",
    r"跌.{0,2}(了|倒)",
    r"崴.{0,2}(了|脚)",
    r"(掉|栽).{0,2}(了|倒|下)",
    r"(腿|脚).{0,2}(软|没劲)",
    r"站不.{0,2}(稳|住|起来)",
    r"爬不起来",
]

# 身体不适模糊模式
HEALTH_FUZZY_PATTERNS = [
    r"(头|胸|肚子|胃|心).{0,3}(疼|痛|闷|难受|不舒服)",
    r"(眼|耳|手|脚|腿|腰|背|脖子).{0,2}(疼|痛|麻|酸)",
    r"(恶心|想吐|吐了|呕吐)",
    r"(拉肚子|腹泻|便血)",
    r"(看不清|听不见|说不出话)",
    r"(冒冷汗|浑身发抖|打寒战)",
    r"(突然|忽然).{0,4}(疼|痛|晕|黑)",
]

# 严重情绪异常 (触发 P1 通知)
EMOTIONAL_DISTRESS_KEYWORDS = [
    "不想活",
    "活着没意思",
    "太痛苦了",
    "想死",
    "活够了",
    "没意思",
    "不如死了",
    "受不了了",
]

# 正面情绪关键词
POSITIVE_EMOTION_KEYWORDS = [
    "开心",
    "高兴",
    "太好了",
    "好消息",
    "哈哈",
    "生日",
]

# 负面情绪关键词
NEGATIVE_EMOTION_KEYWORDS = [
    "难过",
    "伤心",
    "孤独",
    "无聊",
    "想",
    "念",
    "担心",
]

# 好奇触发关键词
CURIOUS_KEYWORDS = [
    "你知道吗",
    "有意思",
    "听说",
    "真的吗",
]

# 唤醒词
WAKE_WORDS = ["天天你好", "天天", "小伴", "xiaoban", "机器人"]


# 告别词 (退出 ACTIVE 模式)
FAREWELL_WORDS = ["再见", "拜拜", "不聊了", "下次再聊"]

# 机器人名字
BOT_NAME = "小伴"

# 预编译正则
_FALL_RE = [re.compile(p) for p in FALL_PATTERNS]
_HEALTH_FUZZY_RE = [re.compile(p) for p in HEALTH_FUZZY_PATTERNS]


def match_any_keyword(text: str, keywords: list[str]) -> str | None:
    """精确匹配: 检查文本中是否包含关键词列表中的任何一个"""
    for kw in keywords:
        if kw in text:
            return kw
    return None


def match_fall_pattern(text: str) -> str | None:
    """模糊匹配: 检查文本是否描述跌倒场景"""
    for pattern in _FALL_RE:
        m = pattern.search(text)
        if m:
            return m.group()
    return None


def match_health_fuzzy(text: str) -> str | None:
    """模糊匹配: 检查文本是否描述身体不适"""
    for pattern in _HEALTH_FUZZY_RE:
        m = pattern.search(text)
        if m:
            return m.group()
    return None
