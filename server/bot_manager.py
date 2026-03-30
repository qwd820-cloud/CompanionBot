"""BotManager — 管理多个机器人实例的创建/加载/销毁

启动时自动扫描 data/bots/ 目录加载已有实例。
首次启动时创建默认实例 "default"。
"""

import json
import logging
from pathlib import Path

from server.bot_instance import BotConfig, BotInstance

logger = logging.getLogger("companion_bot.bot_manager")


class BotManager:
    """机器人实例管理器"""

    CONFIG_FILE = "bot_config.json"  # 每个 bot 目录下的配置文件

    def __init__(self, data_dir: Path, shared_state):
        self.data_dir = data_dir
        self.shared_state = shared_state
        self.bots: dict[str, BotInstance] = {}
        self._bots_dir = data_dir / "bots"
        self._bots_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self, personality_cfg: dict, notification_cfg: dict):
        """启动时加载所有已有的 bot 实例"""
        self._personality_cfg = personality_cfg
        self._notification_cfg = notification_cfg

        loaded = 0
        for bot_dir in sorted(self._bots_dir.iterdir()):
            if not bot_dir.is_dir():
                continue
            config_path = bot_dir / self.CONFIG_FILE
            if config_path.exists():
                try:
                    config = self._load_config(config_path)
                    await self._create_and_init(config)
                    loaded += 1
                except Exception as e:
                    logger.error(f"加载 bot [{bot_dir.name}] 失败: {e}")

        # 如果没有任何实例，创建默认实例
        if not self.bots:
            logger.info("无已有 bot 实例，创建默认实例 'default'")
            await self.create_bot("default", name="天天")

        logger.info(f"BotManager 初始化完成: {len(self.bots)} 个实例")

    async def create_bot(
        self,
        bot_id: str,
        name: str = "天天",
        personality_overrides: dict | None = None,
    ) -> BotInstance:
        """创建新的机器人实例"""
        if bot_id in self.bots:
            raise ValueError(f"Bot [{bot_id}] 已存在")

        config = BotConfig(
            bot_id=bot_id,
            name=name,
            personality_overrides=personality_overrides or {},
        )

        instance = await self._create_and_init(config)

        # 持久化配置
        self._save_config(config)

        logger.info(f"创建 bot [{bot_id}]: name={name}")
        return instance

    async def get_bot(self, bot_id: str) -> BotInstance | None:
        """获取 bot 实例，不存在返回 None"""
        return self.bots.get(bot_id)

    async def get_or_default(self, bot_id: str) -> BotInstance:
        """获取 bot 实例，不存在则返回 default"""
        return self.bots.get(bot_id) or self.bots.get("default")

    async def update_bot(
        self,
        bot_id: str,
        name: str | None = None,
        personality_overrides: dict | None = None,
    ) -> BotInstance:
        """更新 bot 配置 (需要重新初始化人格模块)"""
        instance = self.bots.get(bot_id)
        if not instance:
            raise ValueError(f"Bot [{bot_id}] 不存在")

        if name is not None:
            instance.config.name = name
        if personality_overrides is not None:
            instance.config.personality_overrides = personality_overrides

        # 重新初始化人格层
        merged_cfg = instance._merge_personality_config(self._personality_cfg)
        from server.personality.engine import PersonalityEngine
        from server.personality.prompt_builder import PromptBuilder

        instance.personality = PersonalityEngine(config=merged_cfg)
        instance.prompt_builder = PromptBuilder(
            personality=instance.personality,
            episodic=instance.episodic_memory,
            semantic=instance.semantic_memory,
            profile=instance.long_term_profile,
        )

        self._save_config(instance.config)
        logger.info(f"更新 bot [{bot_id}]")
        return instance

    async def delete_bot(self, bot_id: str) -> bool:
        """删除 bot 实例"""
        if bot_id == "default":
            raise ValueError("不能删除默认实例")

        instance = self.bots.pop(bot_id, None)
        if not instance:
            return False

        await instance.shutdown()
        # 注意: 不删除数据目录，保留数据以防误删
        logger.info(f"删除 bot [{bot_id}] (数据保留在 {instance.data_dir})")
        return True

    def list_bots(self) -> list[dict]:
        """列出所有 bot 实例"""
        return [bot.to_dict() for bot in self.bots.values()]

    async def shutdown_all(self):
        """关闭所有实例"""
        for bot in self.bots.values():
            await bot.shutdown()
        self.bots.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _create_and_init(self, config: BotConfig) -> BotInstance:
        instance = BotInstance(config, self.data_dir, self.shared_state)
        await instance.initialize(self._personality_cfg, self._notification_cfg)
        self.bots[config.bot_id] = instance
        return instance

    def _save_config(self, config: BotConfig):
        bot_dir = self._bots_dir / config.bot_id
        bot_dir.mkdir(parents=True, exist_ok=True)
        config_path = bot_dir / self.CONFIG_FILE
        config_path.write_text(
            json.dumps(
                {
                    "bot_id": config.bot_id,
                    "name": config.name,
                    "personality_overrides": config.personality_overrides,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def _load_config(self, config_path: Path) -> BotConfig:
        data = json.loads(config_path.read_text())
        return BotConfig(
            bot_id=data["bot_id"],
            name=data.get("name", "天天"),
            personality_overrides=data.get("personality_overrides", {}),
        )
