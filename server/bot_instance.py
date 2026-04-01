"""BotInstance — 一个独立机器人实例的全部状态

感知层 (VAD/ASR/SpeechBrain/InsightFace) 全局共享，
每个 bot 实例只隔离: 记忆 + 人格 + 安全 + 主动行为。
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("companion_bot.bot_instance")


@dataclass
class BotConfig:
    """机器人实例配置"""

    bot_id: str
    name: str = "天天"  # 机器人名字
    personality_overrides: dict = field(default_factory=dict)
    # 例: {"name": "小伴", "traits": {"warmth": 0.9}}


class BotInstance:
    """一个独立的机器人实例 — 拥有独立的记忆、人格、安全模块"""

    def __init__(self, config: BotConfig, base_data_dir: Path, shared_state):
        """
        config: 机器人配置
        base_data_dir: 数据根目录 (如 server/data)
        shared_state: 全局共享状态 (感知层 + LLM client)
        """
        self.config = config
        self.bot_id = config.bot_id
        self.shared = shared_state

        # 每个 bot 有独立的数据目录
        self.data_dir = base_data_dir / "bots" / config.bot_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "chroma").mkdir(exist_ok=True)
        (self.data_dir / "voiceprints").mkdir(exist_ok=True)

        # 各模块在 initialize() 中创建
        self.episodic_memory = None
        self.semantic_memory = None
        self.long_term_profile = None
        self.working_memory = None
        self.consolidation = None
        self.personality = None
        self.intervention = None
        self.prompt_builder = None
        self.tts = None
        self.notification = None
        self.anomaly_detector = None
        self.alert_manager = None
        self.proactive = None

    async def initialize(self, personality_cfg: dict, notification_cfg: dict):
        """初始化所有独立模块"""
        from server.memory.consolidation import MemoryConsolidation
        from server.memory.episodic_memory import EpisodicMemory
        from server.memory.long_term_profile import LongTermProfile
        from server.memory.semantic_memory import SemanticMemory
        from server.memory.working_memory import WorkingMemory
        from server.output.notification import NotificationManager
        from server.output.tts import TTSEngine
        from server.personality.engine import PersonalityEngine
        from server.personality.intervention import InterventionDecider
        from server.personality.proactive import ProactiveScheduler
        from server.personality.prompt_builder import PromptBuilder
        from server.safety.alert_manager import AlertManager
        from server.safety.anomaly_detector import AnomalyDetector

        db_path = str(self.data_dir / "companion.db")

        # 记忆层 (独立)
        self.episodic_memory = EpisodicMemory(db_path=db_path)
        self.semantic_memory = SemanticMemory(persist_dir=str(self.data_dir / "chroma"))
        self.long_term_profile = LongTermProfile(db_path=db_path)
        self.working_memory = WorkingMemory()
        self.consolidation = MemoryConsolidation(
            episodic=self.episodic_memory,
            semantic=self.semantic_memory,
            profile=self.long_term_profile,
            llm_client=self.shared.llm_client,
        )

        # 人格层 (独立，可被 personality_overrides 覆盖，情绪持久化到 DB)
        merged_cfg = self._merge_personality_config(personality_cfg)
        self.personality = PersonalityEngine(config=merged_cfg, db_path=db_path)
        self.intervention = InterventionDecider()
        self.prompt_builder = PromptBuilder(
            personality=self.personality,
            episodic=self.episodic_memory,
            semantic=self.semantic_memory,
            profile=self.long_term_profile,
        )

        # 输出层 (MiniCPM-o TTS 优先，回退 Edge-TTS/pyttsx3)
        minicpm = getattr(self.shared, "minicpm_engine", None)
        self.tts = TTSEngine(minicpm_engine=minicpm)
        self.notification = NotificationManager(config=notification_cfg)

        # 安全模块
        self.anomaly_detector = AnomalyDetector()
        self.alert_manager = AlertManager(notification=self.notification)

        # 主动行为 — 接入 WebSocket 回调
        self.proactive = ProactiveScheduler()
        self.proactive.set_send_callback(self._proactive_send)

        # 初始化数据库
        await self.episodic_memory.initialize()
        await self.semantic_memory.initialize()
        await self.long_term_profile.initialize()

        await self.proactive.start()

        logger.info(
            f"Bot 实例 [{self.bot_id}] 初始化完成: name={merged_cfg.get('personality', {}).get('name', '天天')}"
        )

    def _merge_personality_config(self, base_cfg: dict) -> dict:
        """合并基础人格配置和实例覆盖"""
        import copy

        merged = copy.deepcopy(base_cfg)
        overrides = self.config.personality_overrides

        if not overrides:
            return merged

        personality = merged.setdefault("personality", {})

        # 覆盖顶层字段
        if "name" in overrides:
            personality["name"] = overrides["name"]
        if "traits" in overrides:
            personality.setdefault("traits", {}).update(overrides["traits"])
        if "quirks" in overrides:
            personality["quirks"] = overrides["quirks"]

        return merged

    async def _proactive_send(self, person_id: str, message: str, action_type: str):
        """主动行为回调 — 将消息和 TTS 音频发送到所有连接的客户端"""
        from server.ws_handler import manager

        clients = manager.get_clients_for_bot(self.bot_id)
        if not clients:
            logger.debug(f"主动消息未发送 (无在线客户端): {message[:30]}...")
            return

        # 生成 TTS 音频
        audio_data = None
        try:
            audio_data = await self.tts.synthesize(text=message, emotion="neutral")
        except Exception as e:
            logger.warning(f"主动消息 TTS 失败: {e}")

        for client_id in clients:
            await manager.send_json_message(
                client_id,
                {
                    "type": "proactive",
                    "person_id": person_id,
                    "text": message,
                    "action_type": action_type,
                },
            )
            if audio_data:
                await manager.send_tts_audio(client_id, audio_data)

        logger.info(
            f"主动消息已发送到 {len(clients)} 个客户端: [{action_type}] {message[:30]}..."
        )

    async def shutdown(self):
        """关闭实例"""
        if self.proactive:
            await self.proactive.stop()
        logger.info(f"Bot 实例 [{self.bot_id}] 已关闭")

    def to_dict(self) -> dict:
        """返回实例信息"""
        members = []
        if self.long_term_profile and self.long_term_profile.conn:
            try:
                cursor = self.long_term_profile.conn.execute(
                    "SELECT person_id, name, role FROM family_profiles"
                )
                members = [dict(row) for row in cursor.fetchall()]
            except Exception:
                pass

        return {
            "bot_id": self.bot_id,
            "name": self.config.name,
            "personality_overrides": self.config.personality_overrides,
            "members": members,
            "data_dir": str(self.data_dir),
        }
