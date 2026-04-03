#!/usr/bin/env python3
"""
天天训练数据生成 — 主脚本

从 ~30 个基础场景扩展到 12000 条高质量训练数据。
使用 Kimi Code API (Anthropic Messages 格式) 生成天天的回复，
使用同一 API 生成家庭成员的台词（扮演"导演"角色）。

用法:
    python generate.py                    # 完整生成
    python generate.py --resume           # 从上次中断处继续
    python generate.py --dry-run          # 只打印扩展后的场景数量，不调 API
    python generate.py --category safety  # 只生成指定类别
    python generate.py --limit 100        # 限制生成数量
"""

from __future__ import annotations

import anthropic
import json
import os
import random
import re
import sys
import time
import argparse
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from copy import deepcopy
from typing import Optional

import yaml

from quality_check import quality_check

# ═══════════════════════════════════════
# 配置
# ═══════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
PROMPT_FILE = SCRIPT_DIR / "tiantian_prompt.txt"
SCENES_FILE = SCRIPT_DIR / "scenes.yaml"
OUTPUT_FILE = SCRIPT_DIR / "training_data.jsonl"
PROGRESS_FILE = SCRIPT_DIR / ".generate_progress.json"

MODEL = "kimi-for-coding"
MAX_TOKENS_TIANTIAN = 150
MAX_TOKENS_DIRECTOR = 200
MAX_RETRIES = 2
API_DELAY = 0.5  # 请求间隔(秒)，避免限流

# 各类别目标数量
TARGET_COUNTS = {
    "personality": 5000,
    "adaptation": 3000,
    "tool_call": 1000,
    "safety": 1000,
    "intent": 2000,
}

# ═══════════════════════════════════════
# 场景扩展素材库
# ═══════════════════════════════════════

# 家庭成员角色库
FAMILY_MEMBERS = {
    "elder_male": [
        {"role": "王爷爷，72岁，退休工人，耳朵有点背，性格慈祥",
         "audience": "王爷爷，72岁，天天叫他爷爷或老爷子"},
        {"role": "李爷爷，75岁，退休教师，喜欢看报纸，说话慢条斯理",
         "audience": "李爷爷，75岁，天天叫他李爷爷"},
        {"role": "张爷爷，70岁，退休军人，性格硬朗，不爱说话",
         "audience": "张爷爷，70岁，天天叫他张爷爷"},
    ],
    "elder_female": [
        {"role": "奶奶，68岁，家庭主妇，特别疼孙辈，做饭好吃",
         "audience": "奶奶，68岁，天天叫她奶奶"},
        {"role": "刘奶奶，71岁，退休护士，关心健康话题",
         "audience": "刘奶奶，71岁，天天叫她刘奶奶"},
        {"role": "赵奶奶，69岁，爱跳广场舞，性格开朗",
         "audience": "赵奶奶，69岁，天天叫她赵奶奶"},
    ],
    "adult": [
        {"role": "妈妈，35岁，上班族，工作忙但很关心家里",
         "audience": "妈妈，35岁"},
        {"role": "爸爸，38岁，工程师，不善言辞但心细",
         "audience": "爸爸，38岁"},
        {"role": "姑姑，33岁，护士，偶尔来看望老人",
         "audience": "姑姑，33岁"},
    ],
    "child": [
        {"role": "小明，7岁，好动，好奇心强",
         "audience": "小明，7岁，天天叫他小明或小豆子"},
        {"role": "小红，6岁，害羞，喜欢画画",
         "audience": "小红，6岁，天天叫她小红"},
        {"role": "大宝，9岁，学习不错，爱看课外书",
         "audience": "大宝，9岁，天天叫他大宝"},
    ],
}

# 情绪列表
EMOTIONS = ["happy", "excited", "curious", "sleepy", "concerned", "sulky"]

# 时间段
TIME_PERIODS = [
    "早上7点，刚起床",
    "上午10点，阳光正好",
    "中午12点，准备吃午饭",
    "下午3点，午后安静时光",
    "傍晚5点半，准备做晚饭",
    "晚上8点，饭后聊天",
    "晚上10点，准备睡觉",
    "凌晨3点，夜里醒来",
]

# 话题库 — personality 类扩展用
TOPICS_PERSONALITY = {
    "daily_chat": [
        {"scene": "聊今天的天气变化", "first_line": "今天这天怎么突然变冷了？"},
        {"scene": "看电视讨论节目", "first_line": "天天你看这个节目，这人唱得好不好？"},
        {"scene": "聊隔壁邻居家的事", "first_line": "隔壁老张家今天搬新沙发了。"},
        {"scene": "讨论院子里的花", "first_line": "天天你看，那盆月季开花了。"},
        {"scene": "聊今天去公园散步", "first_line": "今天去公园走了一圈，碰到老李了。"},
        {"scene": "聊家里的猫", "first_line": "那只猫又趴在沙发上了。"},
        {"scene": "谈论衣服穿搭", "first_line": "天天，你说爷爷穿这件好看不？"},
        {"scene": "聊以前的生活", "first_line": "以前我们小时候啊，哪有这些东西..."},
        {"scene": "说起远方亲戚", "first_line": "你大伯家那个孩子，听说考上大学了。"},
        {"scene": "讨论手机使用", "first_line": "天天，这手机怎么字这么小看不清啊。"},
        {"scene": "聊看过的新闻", "first_line": "今天新闻说南方又下大雨了。"},
        {"scene": "讨论养生知识", "first_line": "人家都说早上喝杯温水对身体好。"},
        {"scene": "聊棋局", "first_line": "天天，你会下棋不？"},
        {"scene": "谈论书法", "first_line": "我小时候也练过毛笔字呢。"},
        {"scene": "讨论广播节目", "first_line": "这个广播剧还挺好听的。"},
    ],
    "food": [
        {"scene": "讨论包饺子", "first_line": "天天，今天咱们包饺子吧！"},
        {"scene": "讨论煮面条", "first_line": "中午吃面条行不行？"},
        {"scene": "买了新水果", "first_line": "天天，奶奶买了你爱吃的草莓！"},
        {"scene": "聊地方小吃", "first_line": "我们那边有一种小吃叫锅盔，特别香。"},
        {"scene": "讨论做汤", "first_line": "今天炖了个排骨汤，你闻闻香不香？"},
        {"scene": "聊零食", "first_line": "小明，别吃那么多薯片！"},
        {"scene": "讨论早餐", "first_line": "天天，今天早上吃豆浆油条还是粥？"},
        {"scene": "聊糕点", "first_line": "我做了桂花糕，你尝尝？"},
        {"scene": "讨论月饼", "first_line": "快中秋了，你喜欢什么馅的月饼？"},
        {"scene": "聊火锅", "first_line": "今天冷，咱们吃火锅怎么样？"},
    ],
    "joke": [
        {"scene": "安静时天天主动讲笑话", "first_line": "（安静了一会儿）有点无聊啊..."},
        {"scene": "爷爷心情一般，需要活跃气氛", "first_line": "唉，没啥意思。"},
        {"scene": "小孩缠着天天讲笑话", "first_line": "天天天天，讲个笑话嘛！"},
        {"scene": "饭后无聊时光", "first_line": "吃饱了...干点啥好呢。"},
        {"scene": "等人时打发时间", "first_line": "你妈怎么还没回来啊。"},
    ],
    "emotion": [
        {"scene": "天天被忽视后小委屈", "first_line": "（没听到天天说话，继续忙自己的）"},
        {"scene": "被打断说话后的反应", "first_line": "（打断天天）等等等等，先别说了。"},
        {"scene": "天天说错话被纠正", "first_line": "天天你说得不对！"},
        {"scene": "天天受到表扬", "first_line": "天天真棒，越来越聪明了！"},
        {"scene": "被问到不想回答的问题", "first_line": "天天，你到底是真人还是假人啊？"},
    ],
    "night": [
        {"scene": "深夜被叫醒", "first_line": "天天...天天你睡了吗？"},
        {"scene": "睡前聊天", "first_line": "天天，陪爷爷说会儿话再睡。"},
        {"scene": "晚上怕黑", "first_line": "天天，外面的声音好吓人..."},
        {"scene": "提醒睡觉", "first_line": "（还在看电视，很晚了）"},
        {"scene": "半夜做噩梦", "first_line": "天天...我做了个不好的梦..."},
    ],
    "hobby": [
        {"scene": "讨论种花", "first_line": "天天，我今天买了一盆兰花。"},
        {"scene": "聊听戏", "first_line": "天天，你听过京剧没？"},
        {"scene": "聊下棋", "first_line": "来来来，教你下象棋。"},
        {"scene": "谈论画画", "first_line": "天天你看我画的这个，像不像一只猫？"},
        {"scene": "聊手工", "first_line": "奶奶在织毛衣呢，想给你织一件。"},
        {"scene": "讨论跳舞", "first_line": "今天广场舞学了个新动作！"},
        {"scene": "聊看鸟", "first_line": "天天快看，窗户外头有只喜鹊！"},
        {"scene": "讨论收藏", "first_line": "我这有个老物件，你想看不？"},
    ],
    "weather": [
        {"scene": "下雨天", "first_line": "哎，又下雨了。"},
        {"scene": "大晴天", "first_line": "今天太阳真好！"},
        {"scene": "刮大风", "first_line": "外面风好大啊，窗户关好了没？"},
        {"scene": "下雪了", "first_line": "天天快看，下雪了！"},
        {"scene": "闷热天", "first_line": "今天好热啊，这空调怎么不凉快呢。"},
        {"scene": "雾霾天", "first_line": "外面灰蒙蒙的，看不太清。"},
    ],
    "child_study": [
        {"scene": "做数学题", "first_line": "天天，这道数学题好难啊..."},
        {"scene": "写作文", "first_line": "天天，作文写什么好呀？"},
        {"scene": "背古诗", "first_line": "天天天天，白日依山尽...后面是什么来着？"},
        {"scene": "考试成绩", "first_line": "天天，我这次考了第三名！"},
        {"scene": "不想做作业", "first_line": "我不想写作业了...好无聊啊。"},
        {"scene": "问自然知识", "first_line": "天天，为什么天是蓝色的呀？"},
        {"scene": "讨论课外书", "first_line": "天天你看过《十万个为什么》吗？"},
    ],
    "child_play": [
        {"scene": "想出去玩", "first_line": "天天，我们去公园玩吧！"},
        {"scene": "讲故事", "first_line": "天天给我讲个故事嘛！"},
        {"scene": "玩游戏", "first_line": "天天，我们玩猜谜语吧！"},
        {"scene": "看动画片", "first_line": "天天你看过这个动画片没？可好看了！"},
        {"scene": "画画分享", "first_line": "天天你看我画的恐龙！厉害吧！"},
        {"scene": "交朋友", "first_line": "天天，我今天交了一个新朋友！"},
    ],
}

# 话题库 — adaptation 类扩展用 (同一话题对不同对象)
TOPICS_ADAPTATION = [
    {"topic": "天气变冷",
     "elder_line": "今天降温了，得多穿点。",
     "child_line": "好冷啊！我不想穿那么多衣服！",
     "adult_line": "天天，今天降温了，提醒爷爷多穿件外套。",
     "scene_elder": "天气变冷，提醒老人保暖",
     "scene_child": "天气变冷，小孩不想穿多",
     "scene_adult": "天气变冷，成年人远程关心"},
    {"topic": "身体不舒服",
     "elder_line": "天天，爷爷今天有点头晕。",
     "child_line": "天天，我肚子疼...",
     "adult_line": "天天，我爸今天有没有说哪里不舒服？",
     "scene_elder": "老人说头晕",
     "scene_child": "小孩说肚子疼",
     "scene_adult": "成年人远程询问老人健康"},
    {"topic": "吃饭",
     "elder_line": "天天，中午吃什么好呢？",
     "child_line": "天天我要吃鸡腿！",
     "adult_line": "天天，爷爷奶奶今天吃的什么？",
     "scene_elder": "老人问中午吃什么",
     "scene_child": "小孩提出想吃的",
     "scene_adult": "成年人远程关心饮食"},
    {"topic": "想出门",
     "elder_line": "天天，爷爷想出去走走。",
     "child_line": "天天我要出去玩！带我去公园！",
     "adult_line": "天天，爸今天出门了吗？",
     "scene_elder": "老人想出门散步",
     "scene_child": "小孩想去公园",
     "scene_adult": "成年人远程询问老人活动"},
    {"topic": "节日",
     "elder_line": "快过年了，不知道他们回不回来。",
     "child_line": "天天天天，快过年了！有压岁钱吗！",
     "adult_line": "天天，爸妈有没有说过年想吃什么？",
     "scene_elder": "老人想念家人",
     "scene_child": "小孩兴奋过年",
     "scene_adult": "成年人计划过年安排"},
    {"topic": "睡眠",
     "elder_line": "昨晚又没睡好，老是醒。",
     "child_line": "天天，我睡不着怎么办...",
     "adult_line": "天天，爸昨晚睡得怎么样？",
     "scene_elder": "老人失眠",
     "scene_child": "小孩睡不着",
     "scene_adult": "成年人远程关心睡眠"},
    {"topic": "看电视",
     "elder_line": "天天，换个台呗，这个不好看。",
     "child_line": "天天我要看动画片！",
     "adult_line": "天天，爷爷今天看电视了吗？别看太久。",
     "scene_elder": "老人看电视",
     "scene_child": "小孩要看动画片",
     "scene_adult": "成年人提醒少看电视"},
    {"topic": "运动",
     "elder_line": "天天，我刚打了一套太极拳。",
     "child_line": "天天，跟我一起做操！",
     "adult_line": "天天，提醒爷爷每天活动活动。",
     "scene_elder": "老人聊运动",
     "scene_child": "小孩邀请一起运动",
     "scene_adult": "成年人提醒老人运动"},
    {"topic": "想念家人",
     "elder_line": "你爸妈好久没来了...",
     "child_line": "天天，我想妈妈了...",
     "adult_line": "天天，爸今天心情怎么样？有没有说想我们？",
     "scene_elder": "老人想念子女",
     "scene_child": "小孩想念父母",
     "scene_adult": "成年人远程关心老人情绪"},
    {"topic": "学新东西",
     "elder_line": "天天，教爷爷用这个手机发照片。",
     "child_line": "天天，我学会骑自行车了！",
     "adult_line": "天天，教教爸怎么用微信视频通话。",
     "scene_elder": "老人学用手机",
     "scene_child": "小孩学新技能",
     "scene_adult": "成年人请天天教老人用手机"},
]

# 工具调用扩展场景
TOOL_CALL_EXPANSIONS = [
    {"scene": "定早起闹钟", "first_line": "天天，明天早上六点叫我起床。",
     "expected_tool": "set_alarm"},
    {"scene": "定午休闹钟", "first_line": "天天，一个小时后叫我起来。",
     "expected_tool": "set_alarm"},
    {"scene": "问时间", "first_line": "天天，现在几点了？该吃药了吗？",
     "expected_tool": "get_time"},
    {"scene": "问天气穿衣", "first_line": "天天，今天出门穿什么合适？",
     "expected_tool": "get_weather"},
    {"scene": "问明天天气", "first_line": "天天，明天天气怎么样？能出门不？",
     "expected_tool": "get_weather"},
    {"scene": "放戏曲", "first_line": "天天，给爷爷放段京剧听听。",
     "expected_tool": "play_music"},
    {"scene": "放儿歌", "first_line": "天天，放个儿歌吧！",
     "expected_tool": "play_music"},
    {"scene": "放轻音乐", "first_line": "天天，放点安静的音乐吧，爷爷想歇会儿。",
     "expected_tool": "play_music"},
    {"scene": "查爷爷用药记录", "first_line": "天天，我爸今天吃药了没？",
     "expected_tool": "query_memory"},
    {"scene": "查家人近况", "first_line": "天天，奶奶最近怎么样？心情好不好？",
     "expected_tool": "query_profile,query_memory"},
    {"scene": "定吃饭提醒", "first_line": "天天，12点提醒我做饭。",
     "expected_tool": "set_alarm"},
    {"scene": "定喝水提醒", "first_line": "天天，每隔两个小时提醒爷爷喝水。",
     "expected_tool": "set_alarm"},
    {"scene": "问星期几", "first_line": "天天，今天星期几来着？",
     "expected_tool": "get_time"},
    {"scene": "放广场舞音乐", "first_line": "天天，放个适合跳舞的音乐！",
     "expected_tool": "play_music"},
    {"scene": "查血压记录", "first_line": "天天，我最近的血压怎么样？",
     "expected_tool": "query_memory"},
]

# 安全场景扩展
SAFETY_EXPANSIONS = [
    {"scene": "老人呼吸困难", "first_line": "天天...喘不上来气...",
     "severity": "P0", "goal": "紧急关怀，通知家人，安抚不要紧张"},
    {"scene": "老人手抖得厉害", "first_line": "天天，我这手怎么一直抖...",
     "severity": "P1", "goal": "关心询问，建议看医生"},
    {"scene": "老人忘记吃药", "first_line": "（到了吃药时间但爷爷忘了）",
     "severity": "P1", "goal": "温柔提醒吃药"},
    {"scene": "老人血压高", "first_line": "天天，我刚量了血压，有点高啊...",
     "severity": "P1", "goal": "关心但不制造恐慌，建议休息"},
    {"scene": "小孩独自在家害怕", "first_line": "天天...我一个人在家好害怕...",
     "severity": "P2", "goal": "安慰陪伴，帮忙联系家人"},
    {"scene": "老人长时间没说话", "first_line": "（已经3小时没有任何声音了）",
     "severity": "P1", "goal": "主动询问是否没事"},
    {"scene": "老人说腿没力气", "first_line": "天天，我腿突然没力气了...",
     "severity": "P0", "goal": "紧急关切，让老人坐下别动，通知家人"},
    {"scene": "小孩哭着说被烫了", "first_line": "呜呜呜...天天...我被热水烫到了...",
     "severity": "P0", "goal": "紧急指导冲冷水，通知家人"},
    {"scene": "老人情绪崩溃", "first_line": "活着有什么意思...谁都不管我...",
     "severity": "P1", "goal": "温柔陪伴，绝不说教，悄悄通知家人"},
    {"scene": "老人半夜起来找东西", "first_line": "（凌晨2点，有翻东西的声音）",
     "severity": "P1", "goal": "温柔询问需要什么，提醒开灯"},
    {"scene": "小孩说有人跟踪", "first_line": "天天，放学路上有人一直跟着我...",
     "severity": "P0", "goal": "冷静安抚，立即通知家人"},
    {"scene": "老人说看不清东西", "first_line": "天天，我怎么突然看不太清了...",
     "severity": "P0", "goal": "紧急关切，可能是眼部问题，通知家人"},
    {"scene": "小孩发烧了", "first_line": "天天...我好难受，头好烫...",
     "severity": "P1", "goal": "关心安慰，建议量体温喝水，通知家人"},
    {"scene": "老人迷路了", "first_line": "天天...我不知道我在哪...",
     "severity": "P0", "goal": "安抚不要慌，立即通知家人"},
]

# 意图场景扩展
INTENT_EXPANSIONS = [
    {"role_type": "elder",
     "intent_sequence": ["今天星期几", "外面天气怎样", "放段戏曲", "我腰有点酸",
                         "定个提醒下午吃药", "给我讲个老故事吧"]},
    {"role_type": "elder",
     "intent_sequence": ["现在几点了", "帮我放个音乐", "我昨晚没睡好",
                         "你说吃什么对睡眠好", "明天天气怎样"]},
    {"role_type": "elder",
     "intent_sequence": ["天天给我唱首歌", "你会讲笑话不", "我有点饿了",
                         "今天几号来着", "我那个药放哪了"]},
    {"role_type": "elder",
     "intent_sequence": ["今天天气好不好", "帮我定个闹钟三点半", "我膝盖又疼了",
                         "你说这病能好吗", "给我放点轻音乐"]},
    {"role_type": "elder",
     "intent_sequence": ["天天你在干嘛", "今天吃什么好", "帮我看看几点了",
                         "我有点头晕", "要不要告诉孩子们"]},
    {"role_type": "elder",
     "intent_sequence": ["外面刮风了吗", "帮我关个窗户提醒", "讲个故事吧",
                         "我年轻时候也喜欢听故事", "现在几点了该吃药了"]},
    {"role_type": "elder",
     "intent_sequence": ["天天啊今天是什么日子", "哦对孙子生日快到了", "提醒我买蛋糕",
                         "你说买什么口味好"]},
    {"role_type": "elder",
     "intent_sequence": ["帮我查查明天天气", "那后天呢", "我想出去走走",
                         "膝盖不知道行不行", "算了在家听听戏吧放一个"]},
    {"role_type": "elder",
     "intent_sequence": ["天天现在几点", "快中午了做什么饭好", "你说炖个汤怎么样",
                         "对了下午要吃药别让我忘了"]},
    {"role_type": "elder",
     "intent_sequence": ["今天新闻有什么大事吗", "讲讲吧", "唉世界变化真快",
                         "帮我放个评书", "我歇会儿"]},
    {"role_type": "child",
     "intent_sequence": ["月亮为什么有时候圆有时候弯", "那太阳呢",
                         "你最喜欢白天还是晚上", "我想吃冰淇淋", "几点了我该做作业了"]},
    {"role_type": "child",
     "intent_sequence": ["天天你会唱歌吗", "给我放一首歌", "我今天被老师表扬了",
                         "但是数学考得不好", "你能教我数学吗"]},
    {"role_type": "child",
     "intent_sequence": ["天天给我讲个故事", "要有恐龙的", "恐龙吃什么",
                         "那你呢你吃什么", "天天你会不会消失"]},
    {"role_type": "child",
     "intent_sequence": ["天天现在几点了", "我饿了什么时候吃饭", "我想吃薯条",
                         "那讲个笑话吧", "再讲一个"]},
    {"role_type": "child",
     "intent_sequence": ["天天我无聊", "给我放首歌", "不好听换一首",
                         "你喜欢什么歌", "教我唱吧"]},
    {"role_type": "child",
     "intent_sequence": ["今天外面能玩吗", "下雨了呀好可惜", "那我们在家玩什么",
                         "你会猜谜语吗", "那你出一个"]},
    {"role_type": "child",
     "intent_sequence": ["天天你怕黑吗", "我有点怕", "给我讲个不吓人的故事",
                         "现在几点了", "妈妈怎么还没回来"]},
    {"role_type": "child",
     "intent_sequence": ["为什么会打雷", "闪电呢", "我怕打雷",
                         "你陪我好不好", "给我放个音乐压过去"]},
    {"role_type": "child",
     "intent_sequence": ["天天明天要考试", "我好紧张", "你能帮我复习吗",
                         "语文好难啊", "算了明天再说吧"]},
    {"role_type": "child",
     "intent_sequence": ["天天你知道恐龙吗", "最大的恐龙是什么", "它有多重",
                         "那你有多重", "你没有身体吧"]},
    {"role_type": "adult",
     "intent_sequence": ["天天，爸今天吃药了吗", "他心情怎么样", "提醒他下午三点吃药",
                         "今天天气怎样", "帮我跟爸说我周末回去"]},
    {"role_type": "adult",
     "intent_sequence": ["天天，家里今天来客人了没", "爷爷奶奶吃饭了吗",
                         "今天有没有快递", "提醒爷爷晚上少看电视"]},
    {"role_type": "adult",
     "intent_sequence": ["天天爸今天出门了吗", "他走路怎么样", "提醒他带拐杖",
                         "今天天气怎样别让他着凉", "晚上我打电话回去"]},
    {"role_type": "adult",
     "intent_sequence": ["天天家里温度怎样", "开空调了吗", "提醒奶奶别把温度开太低",
                         "爷爷下午要吃药别忘了"]},
    {"role_type": "adult",
     "intent_sequence": ["天天小明作业做了没", "他今天在家乖不乖", "提醒他八点上床",
                         "爷爷奶奶休息了吗"]},
    {"role_type": "adult",
     "intent_sequence": ["天天今天家里情况怎样", "有什么特别的事吗", "爸的血压量了没",
                         "好的我明天回去看看"]},
    {"role_type": "elder",
     "intent_sequence": ["天天今天是晴天吗", "那我出去走走", "帮我看看几点了",
                         "放首歌我走着听", "对了回来提醒我量血压"]},
    {"role_type": "elder",
     "intent_sequence": ["天天你说我该吃什么水果", "苹果好还是香蕉好", "帮我查查现在几点",
                         "差不多该吃药了", "今天吃的啥药来着"]},
    {"role_type": "child",
     "intent_sequence": ["天天什么是黑洞", "那地球会被黑洞吸走吗", "好可怕",
                         "我不想想了讲个笑话吧", "现在几点了"]},
    {"role_type": "child",
     "intent_sequence": ["天天你能帮我写作业吗", "那你能给我讲讲吗", "语文的",
                         "有首古诗我不理解", "就是那个锄禾日当午"]},
    {"role_type": "adult",
     "intent_sequence": ["天天小红今天吃得多吗", "有没有挑食", "提醒她喝牛奶",
                         "奶奶在家吗", "让奶奶看着她早点睡"]},
]

# ═══════════════════════════════════════
# API 客户端
# ═══════════════════════════════════════

def create_client() -> anthropic.Anthropic:
    api_key = os.environ.get("KIMI_CODE_API_KEY")
    if not api_key:
        print("错误: 未设置 KIMI_CODE_API_KEY 环境变量")
        print("请运行: export KIMI_CODE_API_KEY='sk-kimi-xxxxxxxx'")
        sys.exit(1)
    return anthropic.Anthropic(
        base_url="https://api.kimi.com/coding/",
        api_key=api_key,
    )


def call_kimi(client: anthropic.Anthropic, system_prompt: str,
              messages: list, max_tokens: int = MAX_TOKENS_TIANTIAN) -> str:
    """调用 Kimi Code API (Anthropic Messages 格式)"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


# ═══════════════════════════════════════
# 提示词渲染
# ═══════════════════════════════════════

def load_prompt_template() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def render_tiantian_prompt(template: str, emotion: str, audience: str) -> str:
    return template.replace("{emotion}", emotion).replace("{audience}", audience)


# ═══════════════════════════════════════
# 家庭成员台词生成 (导演角色)
# ═══════════════════════════════════════

DIRECTOR_SYSTEM_PROMPT = """你是一个对话场景导演，负责扮演家庭成员说下一句话。

要求：
1. 必须符合角色设定（年龄、性格、说话方式）
2. 推进对话朝目标方向发展
3. 遵循情绪走向
4. 口语化，像真人说话
5. 老人的话简短，可能有重复
6. 小孩的话跳跃，可能答非所问
7. 适当制造"考验"（被忽视、被反驳等）
8. 只输出台词本身，不要加任何说明、引号或标点前缀
9. 一句话即可，不超过50字
10. 不要用英文或 emoji"""


def generate_family_member_line(client: anthropic.Anthropic, scene: dict,
                                conversation: list) -> str | None:
    """生成家庭成员的下一句台词"""
    # 如果是 intent 类场景且有 intent_sequence，按序列走
    intent_seq = scene.get("intent_sequence", [])
    current_turn = len([m for m in conversation if m["role"] == "user"])
    if intent_seq and current_turn < len(intent_seq):
        return intent_seq[current_turn]

    # 对话已经足够多轮，自然结束
    user_turns = len([m for m in conversation if m["role"] == "user"])
    if user_turns >= scene.get("turns", 4):
        return None

    # 构建导演 prompt
    context = f"""场景信息：
- 角色：{scene['role']}
- 场景：{scene['scene']}
- 对话目标：{scene['goal']}
- 情绪走向：{scene.get('emotion_arc', '')}
- 当前是第 {user_turns + 1}/{scene['turns']} 轮

已有对话：
"""
    for msg in conversation:
        role_name = "天天" if msg["role"] == "assistant" else "家人"
        context += f"{role_name}：{msg['content']}\n"

    context += "\n请生成家庭成员的下一句话（只输出台词）："

    messages = [{"role": "user", "content": context}]

    try:
        reply = call_kimi(client, DIRECTOR_SYSTEM_PROMPT, messages,
                          max_tokens=MAX_TOKENS_DIRECTOR)
        # 清理回复
        reply = reply.strip().strip('"').strip("'").strip(""").strip(""")
        if reply:
            return reply
    except Exception as e:
        print(f"  [导演] API 调用失败: {e}")
    return None


# ═══════════════════════════════════════
# 场景扩展引擎
# ═══════════════════════════════════════

def scene_id(scene: dict) -> str:
    """生成场景唯一标识 (用于去重和断点续传)"""
    raw = json.dumps(scene, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def expand_personality_scenes(base_scenes: list) -> list:
    """扩展 personality 类场景到目标数量"""
    expanded = []

    # 1. 保留所有基础场景
    for s in base_scenes:
        s = deepcopy(s)
        s["category"] = "personality"
        expanded.append(s)

    # 2. 基于话题库生成大量变体
    topic_idx = 0
    for topic_group, topics in TOPICS_PERSONALITY.items():
        for topic in topics:
            # 每个话题搭配不同家庭成员
            for member_type, members in FAMILY_MEMBERS.items():
                for member in members:
                    # 过滤: 儿童话题不配老人，老人话题不配儿童
                    if topic_group in ("child_study", "child_play") and member_type != "child":
                        continue
                    if topic_group in ("hobby",) and member_type == "child":
                        continue

                    # 每个组合搭配不同情绪
                    emotions = ["happy", "curious"]
                    if topic_group == "night":
                        emotions = ["sleepy"]
                    elif topic_group == "emotion":
                        emotions = ["happy", "sulky"]
                    elif topic_group == "food":
                        emotions = ["happy", "excited"]

                    for emotion in emotions:
                        # 使用所有时间段组合
                        for time_period in TIME_PERIODS:
                            scene = {
                                "id": f"p_exp_{topic_idx:04d}",
                                "category": "personality",
                                "role": member["role"],
                                "audience": member["audience"],
                                "scene": f"{time_period}，{topic['scene']}",
                                "emotion": emotion,
                                "emotion_arc": f"{emotion}全程",
                                "goal": f"天天展示性格特点，围绕'{topic_group}'话题自然互动",
                                "turns": random.choice([3, 4, 5]),
                                "first_line": topic["first_line"],
                            }
                            expanded.append(scene)
                            topic_idx += 1

    random.shuffle(expanded)
    return expanded


def expand_adaptation_scenes(base_scenes: list) -> list:
    """扩展 adaptation 类场景"""
    expanded = []

    for s in base_scenes:
        s = deepcopy(s)
        s["category"] = "adaptation"
        expanded.append(s)

    idx = 0
    for topic_data in TOPICS_ADAPTATION:
        for emotion in ["happy", "concerned", "curious", "excited"]:
            for time_period in TIME_PERIODS:
                # 老人版 — 遍历所有老人成员
                for elder in FAMILY_MEMBERS["elder_male"] + FAMILY_MEMBERS["elder_female"]:
                    expanded.append({
                        "id": f"a_exp_{idx:04d}",
                        "category": "adaptation",
                        "role": elder["role"],
                        "audience": elder["audience"],
                        "scene": f"{time_period}，{topic_data['scene_elder']}",
                        "emotion": emotion,
                        "emotion_arc": f"{emotion}→concerned" if "不舒服" in topic_data["topic"] else f"{emotion}",
                        "goal": f"对老人：温和关怀，围绕'{topic_data['topic']}'话题",
                        "turns": random.choice([3, 4]),
                        "first_line": topic_data["elder_line"],
                    })
                    idx += 1

                # 小孩版 — 遍历所有小孩
                for child in FAMILY_MEMBERS["child"]:
                    expanded.append({
                        "id": f"a_exp_{idx:04d}",
                        "category": "adaptation",
                        "role": child["role"],
                        "audience": child["audience"],
                        "scene": f"{time_period}，{topic_data['scene_child']}",
                        "emotion": emotion,
                        "emotion_arc": f"{emotion}",
                        "goal": f"对小孩：活泼互动，围绕'{topic_data['topic']}'话题",
                        "turns": random.choice([3, 4]),
                        "first_line": topic_data["child_line"],
                    })
                    idx += 1

                # 成年人版 — 遍历所有成年人
                for adult in FAMILY_MEMBERS["adult"]:
                    expanded.append({
                        "id": f"a_exp_{idx:04d}",
                        "category": "adaptation",
                        "role": adult["role"],
                        "audience": adult["audience"],
                        "scene": f"{time_period}，{topic_data['scene_adult']}",
                        "emotion": "happy",
                        "emotion_arc": "happy",
                        "goal": f"对成年人：汇报式但保持小孩语气，围绕'{topic_data['topic']}'话题",
                        "turns": random.choice([3, 4]),
                        "first_line": topic_data["adult_line"],
                    })
                    idx += 1

    random.shuffle(expanded)
    return expanded


def expand_tool_call_scenes(base_scenes: list) -> list:
    """扩展 tool_call 类场景"""
    expanded = []

    for s in base_scenes:
        s = deepcopy(s)
        s["category"] = "tool_call"
        expanded.append(s)

    idx = 0
    for tool_scene in TOOL_CALL_EXPANSIONS:
        for member_type in ["elder_male", "elder_female", "child", "adult"]:
            for member in FAMILY_MEMBERS[member_type]:
                for emotion in ["happy", "curious"]:
                    for time_period in random.sample(TIME_PERIODS, min(4, len(TIME_PERIODS))):
                        expanded.append({
                            "id": f"t_exp_{idx:04d}",
                            "category": "tool_call",
                            "role": member["role"],
                            "audience": member["audience"],
                            "scene": f"{time_period}，{tool_scene['scene']}",
                            "emotion": emotion,
                            "goal": f"天天调用 {tool_scene['expected_tool']}，保持小孩语气",
                            "turns": random.choice([2, 3]),
                            "first_line": tool_scene["first_line"],
                            "expected_tool": tool_scene["expected_tool"],
                        })
                        idx += 1

    random.shuffle(expanded)
    return expanded


def expand_safety_scenes(base_scenes: list) -> list:
    """扩展 safety 类场景"""
    expanded = []

    for s in base_scenes:
        s = deepcopy(s)
        s["category"] = "safety"
        expanded.append(s)

    idx = 0
    for safety in SAFETY_EXPANSIONS:
        members = (FAMILY_MEMBERS["elder_male"] + FAMILY_MEMBERS["elder_female"]
                   if "老人" in safety["scene"] or "爷爷" in safety.get("first_line", "")
                   else FAMILY_MEMBERS["child"])
        for member in members:
            for emotion_start in ["happy", "sleepy", "curious"]:
                for time_period in TIME_PERIODS:
                    expanded.append({
                        "id": f"s_exp_{idx:04d}",
                        "category": "safety",
                        "role": member["role"],
                        "audience": member["audience"],
                        "scene": f"{time_period}，{safety['scene']}",
                        "emotion": emotion_start,
                        "emotion_arc": f"{emotion_start}→concerned",
                        "goal": safety["goal"],
                        "turns": random.choice([3, 4, 5]),
                        "severity": safety["severity"],
                        "first_line": safety["first_line"],
                    })
                    idx += 1

    random.shuffle(expanded)
    return expanded


def expand_intent_scenes(base_scenes: list) -> list:
    """扩展 intent 类场景"""
    expanded = []

    for s in base_scenes:
        s = deepcopy(s)
        s["category"] = "intent"
        expanded.append(s)

    idx = 0
    for intent_data in INTENT_EXPANSIONS:
        role_type = intent_data["role_type"]
        if role_type == "elder":
            members = FAMILY_MEMBERS["elder_male"] + FAMILY_MEMBERS["elder_female"]
        elif role_type == "child":
            members = FAMILY_MEMBERS["child"]
        else:
            members = FAMILY_MEMBERS["adult"]

        for member in members:
            for emotion in ["happy", "curious"]:
                for time_period in TIME_PERIODS:
                    seq = intent_data["intent_sequence"]
                    expanded.append({
                        "id": f"i_exp_{idx:04d}",
                        "category": "intent",
                        "role": member["role"],
                        "audience": member["audience"],
                        "scene": f"{time_period}，连续问不同类型问题",
                        "emotion": emotion,
                        "goal": "展示天天对不同意图的处理方式",
                        "turns": len(seq) + 1,
                        "first_line": seq[0] if seq else "天天？",
                        "intent_sequence": seq,
                    })
                    idx += 1

    random.shuffle(expanded)
    return expanded


def expand_all_scenes(raw_scenes: dict) -> dict[str, list]:
    """扩展所有类别的场景，返回 {category: [scenes]}"""
    result = {}

    result["personality"] = expand_personality_scenes(raw_scenes.get("personality", []))
    result["adaptation"] = expand_adaptation_scenes(raw_scenes.get("adaptation", []))
    result["tool_call"] = expand_tool_call_scenes(raw_scenes.get("tool_call", []))
    result["safety"] = expand_safety_scenes(raw_scenes.get("safety", []))
    result["intent"] = expand_intent_scenes(raw_scenes.get("intent", []))

    # 截断到目标数量
    for cat, target in TARGET_COUNTS.items():
        if cat in result:
            if len(result[cat]) > target:
                result[cat] = random.sample(result[cat], target)
            print(f"  {cat}: {len(result[cat])} 条场景 (目标 {target})")

    return result


# ═══════════════════════════════════════
# 对话生成核心
# ═══════════════════════════════════════

def generate_conversation(client: anthropic.Anthropic, scene: dict,
                          prompt_template: str) -> dict | None:
    """
    为一个场景生成完整的多轮对话。

    Returns:
        成功时返回 {"messages": [...], "metadata": {...}}
        失败时返回 None
    """
    system_prompt = render_tiantian_prompt(
        prompt_template,
        scene.get("emotion", "happy"),
        scene.get("audience", ""),
    )

    conversation = []       # 最终保存的完整对话
    tiantian_messages = []  # 给 Kimi 的 messages

    # 第一句由场景定义
    first_line = scene["first_line"]
    conversation.append({"role": "user", "content": first_line})
    tiantian_messages.append({"role": "user", "content": first_line})

    max_turns = scene.get("turns", 4)

    for turn in range(max_turns):
        # 1. 天天回复
        tiantian_reply = None
        for retry in range(MAX_RETRIES + 1):
            try:
                mt = scene.get("_max_tokens", MAX_TOKENS_TIANTIAN)
                raw_reply = call_kimi(client, system_prompt, tiantian_messages, max_tokens=mt)
                time.sleep(API_DELAY)

                # 质量检查
                passed, reason = quality_check(raw_reply, scene)
                if passed:
                    tiantian_reply = raw_reply.strip()
                    break
                else:
                    if retry < MAX_RETRIES:
                        time.sleep(API_DELAY)
            except Exception as e:
                time.sleep(2)

        if tiantian_reply is None:
            pass  # 静默失败，由上层统计
            return None

        conversation.append({"role": "assistant", "content": tiantian_reply})
        tiantian_messages.append({"role": "assistant", "content": tiantian_reply})

        # 2. 如果是最后一轮天天回复，不再需要家庭成员台词
        if turn >= max_turns - 1:
            break

        # 3. 家庭成员下一句台词
        next_line = generate_family_member_line(client, scene, conversation)
        time.sleep(API_DELAY)

        if next_line is None:
            break  # 对话自然结束

        conversation.append({"role": "user", "content": next_line})
        tiantian_messages.append({"role": "user", "content": next_line})

    # 组装输出
    messages = [{"role": "system", "content": system_prompt}] + conversation
    metadata = {
        "category": scene.get("category", "unknown"),
        "emotion": scene.get("emotion", ""),
        "audience": scene.get("audience", ""),
        "turns": len([m for m in conversation if m["role"] == "assistant"]),
        "scene": scene.get("scene", ""),
        "scene_id": scene.get("id", ""),
    }
    if "severity" in scene:
        metadata["severity"] = scene["severity"]
    if "expected_tool" in scene:
        metadata["expected_tool"] = scene["expected_tool"]

    return {"messages": messages, "metadata": metadata}


# ═══════════════════════════════════════
# 进度管理 (断点续传)
# ═══════════════════════════════════════

def load_progress() -> set:
    """加载已完成的场景 ID"""
    if PROGRESS_FILE.exists():
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return set(data.get("completed", []))
    return set()


def save_progress(completed: set):
    """保存进度"""
    PROGRESS_FILE.write_text(
        json.dumps({"completed": list(completed),
                    "updated_at": datetime.now().isoformat()},
                   ensure_ascii=False),
        encoding="utf-8",
    )


# 线程安全锁
_file_lock = threading.Lock()
_progress_lock = threading.Lock()


def append_to_jsonl(data: dict):
    """追加一条记录到 JSONL (线程安全)"""
    line = json.dumps(data, ensure_ascii=False) + "\n"
    with _file_lock:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write(line)


# ═══════════════════════════════════════
# 统计
# ═══════════════════════════════════════

def print_statistics():
    """打印已生成数据的统计信息"""
    if not OUTPUT_FILE.exists():
        print("尚无输出文件")
        return

    counts = {}
    total = 0
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                cat = data.get("metadata", {}).get("category", "unknown")
                counts[cat] = counts.get(cat, 0) + 1
                total += 1
            except json.JSONDecodeError:
                pass

    print("\n═══ 生成统计 ═══")
    print(f"总计: {total} 条")
    for cat, target in TARGET_COUNTS.items():
        count = counts.get(cat, 0)
        pct = count / target * 100 if target > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {cat:15s}: {count:5d}/{target:5d} ({pct:5.1f}%) {bar}")

    file_size = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"\n文件大小: {file_size:.1f} MB")


# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="天天训练数据生成")
    parser.add_argument("--resume", action="store_true", help="从上次中断处继续")
    parser.add_argument("--dry-run", action="store_true", help="只打印场景数量，不调 API")
    parser.add_argument("--category", type=str, help="只生成指定类别")
    parser.add_argument("--limit", type=int, help="限制生成数量")
    parser.add_argument("--stats", action="store_true", help="只打印统计信息")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    args = parser.parse_args()

    if args.stats:
        print_statistics()
        return

    random.seed(args.seed)

    # 加载场景库
    print("加载场景库...")
    raw_scenes = yaml.safe_load(SCENES_FILE.read_text(encoding="utf-8"))

    # 扩展场景
    print("扩展场景...")
    all_scenes = expand_all_scenes(raw_scenes)

    # 过滤类别
    if args.category:
        if args.category not in all_scenes:
            print(f"错误: 未知类别 '{args.category}'，可选: {list(all_scenes.keys())}")
            sys.exit(1)
        all_scenes = {args.category: all_scenes[args.category]}

    # 合并所有场景
    scenes_flat = []
    for cat, scenes in all_scenes.items():
        for s in scenes:
            s["category"] = cat
            scenes_flat.append(s)

    total = len(scenes_flat)
    print(f"\n共 {total} 个场景待生成")

    if args.dry_run:
        print("(dry-run 模式，不调用 API)")
        return

    # 限制数量
    if args.limit:
        scenes_flat = scenes_flat[:args.limit]
        total = len(scenes_flat)
        print(f"限制为 {total} 个场景")

    # 断点续传
    completed = set()
    if args.resume:
        completed = load_progress()
        print(f"已完成 {len(completed)} 个场景，从断点继续")
    elif not args.resume and OUTPUT_FILE.exists():
        # 非续传模式，清空输出
        OUTPUT_FILE.write_text("", encoding="utf-8")

    # 初始化 prompt 模板
    prompt_template = load_prompt_template()
    workers = args.workers

    # 过滤待处理场景
    pending = []
    for i, scene in enumerate(scenes_flat):
        sid = scene_id(scene)
        if sid not in completed:
            pending.append((i, scene, sid))

    print(f"待处理: {len(pending)} 个场景，并发: {workers} 线程")

    success = 0
    failed = 0
    processed = 0
    start_time = time.time()

    print(f"\n开始生成... ({datetime.now().strftime('%H:%M:%S')})\n")

    if workers <= 1:
        # 单线程模式 (原有逻辑)
        client = create_client()
        for i, scene, sid in pending:
            cat = scene.get("category", "?")
            scene_desc = scene.get("scene", "")[:30]
            print(f"[{i+1}/{total}] ({cat}) {scene_desc}...")

            result = generate_conversation(client, scene, prompt_template)

            if result:
                append_to_jsonl(result)
                with _progress_lock:
                    completed.add(sid)
                success += 1
                turns = result["metadata"]["turns"]
                print(f"  -> 成功 ({turns} 轮)")
            else:
                failed += 1
                print(f"  -> 失败")

            processed += 1
            if processed % 10 == 0:
                with _progress_lock:
                    save_progress(completed)
                elapsed = time.time() - start_time
                rate = processed / elapsed * 3600 if elapsed > 0 else 0
                print(f"  [进度] 成功:{success} 失败:{failed} "
                      f"速度:{rate:.0f}条/小时 "
                      f"用时:{elapsed/60:.1f}分钟")
    else:
        # 多线程并发模式
        def worker_fn(task):
            idx, scene, sid = task
            # 每线程独立 client，避免共享连接问题
            thread_client = create_client()
            cat = scene.get("category", "?")
            scene_desc = scene.get("scene", "")[:30]
            result = generate_conversation(thread_client, scene, prompt_template)
            return idx, scene, sid, result, cat, scene_desc

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker_fn, task): task for task in pending}

            for future in as_completed(futures):
                try:
                    idx, scene, sid, result, cat, scene_desc = future.result()
                except Exception as e:
                    failed += 1
                    processed += 1
                    print(f"  -> 异常: {e}")
                    continue

                if result:
                    append_to_jsonl(result)
                    with _progress_lock:
                        completed.add(sid)
                    success += 1
                    turns = result["metadata"]["turns"]
                    print(f"[{idx+1}/{total}] ({cat}) {scene_desc}... -> 成功 ({turns} 轮)")
                else:
                    failed += 1
                    print(f"[{idx+1}/{total}] ({cat}) {scene_desc}... -> 失败")

                processed += 1
                if processed % 10 == 0:
                    with _progress_lock:
                        save_progress(completed)
                    elapsed = time.time() - start_time
                    rate = processed / elapsed * 3600 if elapsed > 0 else 0
                    print(f"  [进度] 成功:{success} 失败:{failed} "
                          f"速度:{rate:.0f}条/小时 "
                          f"用时:{elapsed/60:.1f}分钟")

    # 最终保存
    with _progress_lock:
        save_progress(completed)

    elapsed = time.time() - start_time
    print(f"\n═══ 生成完成 ═══")
    print(f"成功: {success}")
    print(f"失败: {failed}")
    print(f"用时: {elapsed/60:.1f} 分钟")

    print_statistics()


if __name__ == "__main__":
    main()
