"""集中管理的关键词列表 — 健康、情绪、安全等"""

# 健康相关关键词 (用于情绪推断、重要性评估、异常检测)
HEALTH_KEYWORDS = [
    "疼", "痛", "不舒服", "头晕", "难受", "生病", "住院",
    "血压", "吃药", "医院", "检查", "发烧", "咳嗽",
]

# 紧急健康关键词 (直接触发 P1 通知)
HEALTH_URGENT_KEYWORDS = [
    "胸闷", "喘不上气", "很晕", "眼前发黑", "心脏",
]

# 呼救/求助关键词 (直接触发 P0 安全预警)
DISTRESS_KEYWORDS = [
    "救命", "帮帮我", "快来人", "摔倒了", "不行了", "很痛",
]

# 严重情绪异常 (触发 P1 通知)
EMOTIONAL_DISTRESS_KEYWORDS = [
    "不想活", "活着没意思", "太痛苦了",
]

# 正面情绪关键词
POSITIVE_EMOTION_KEYWORDS = [
    "开心", "高兴", "太好了", "好消息", "哈哈", "生日",
]

# 负面情绪关键词
NEGATIVE_EMOTION_KEYWORDS = [
    "难过", "伤心", "孤独", "无聊", "想", "念", "担心",
]

# 好奇触发关键词
CURIOUS_KEYWORDS = [
    "你知道吗", "有意思", "听说", "真的吗",
]

# 唤醒词
WAKE_WORDS = ["小伴", "xiaoban", "机器人"]

# 机器人名字
BOT_NAME = "小伴"


def match_any_keyword(text: str, keywords: list[str]) -> str | None:
    """检查文本中是否包含关键词列表中的任何一个，返回匹配的关键词或 None"""
    for kw in keywords:
        if kw in text:
            return kw
    return None
