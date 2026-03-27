"""CompanionBot — FastAPI 入口"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path


# 加载 .env 文件 (deploy/.env 或项目根 .env)
def _load_env():
    for env_path in [
        Path(__file__).parent.parent / "deploy" / ".env",
        Path(__file__).parent.parent / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ[key.strip()] = value.strip()
            break
    # 清理代理设置，避免干扰本地和云端 LLM 连接
    for proxy_var in [
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ]:
        os.environ.pop(proxy_var, None)
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")


_load_env()

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.memory.consolidation import MemoryConsolidation
from server.memory.episodic_memory import EpisodicMemory
from server.memory.long_term_profile import LongTermProfile
from server.memory.semantic_memory import SemanticMemory
from server.memory.working_memory import WorkingMemory
from server.output.notification import NotificationManager
from server.output.tts import TTSEngine
from server.perception.asr import ASRProcessor
from server.perception.face_id import FaceIdentifier
from server.perception.identity_fusion import IdentityFusion
from server.perception.speaker_id import SpeakerIdentifier
from server.perception.vad import VADProcessor
from server.personality.engine import PersonalityEngine
from server.personality.intervention import InterventionDecider
from server.personality.llm_client import LLMClient
from server.personality.prompt_builder import PromptBuilder
from server.safety.alert_manager import AlertManager
from server.safety.anomaly_detector import AnomalyDetector
from server.ws_handler import router as ws_router

logger = logging.getLogger("companion_bot")

CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent / "data"


def load_config(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _configure_gpu_memory():
    """
    配置 GPU 内存分配策略 — DGX Spark UMA 适配。

    UMA 架构下 GPU 和 CPU 共享 128GB 内存，感知层模型 (SpeechBrain,
    InsightFace, FunASR) 与 LLM 推理引擎 (SGLang) 需要协调内存使用。
    限制本进程的 GPU 内存占比，避免与 SGLang 争抢。
    """
    try:
        import torch

        if torch.cuda.is_available():
            # 限制本进程最多使用 GPU 可见内存的 15%
            # 感知层模型总共约需 4~6GB，128GB 的 15% ≈ 19GB 足够
            fraction = float(os.environ.get("TORCH_CUDA_ALLOC_FRACTION", "0.15"))
            torch.cuda.set_per_process_memory_fraction(fraction)
            device = torch.cuda.get_device_name(0)
            logger.info(f"CUDA 设备: {device}, 内存占比限制: {fraction:.0%}")
    except Exception as e:
        logger.warning(f"GPU 内存配置跳过: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化和清理所有子系统"""
    logger.info("CompanionBot 启动中...")

    _configure_gpu_memory()

    personality_cfg = load_config("personality.yaml")
    _family_cfg = load_config("family_members.yaml")
    notification_cfg = load_config("notification_contacts.yaml")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "chroma").mkdir(exist_ok=True)
    (DATA_DIR / "voiceprints").mkdir(exist_ok=True)

    db_path = str(DATA_DIR / "companion.db")

    # 感知层
    app.state.vad = VADProcessor()
    app.state.speaker_id = SpeakerIdentifier(
        voiceprint_dir=str(DATA_DIR / "voiceprints")
    )
    app.state.face_id = FaceIdentifier()
    app.state.asr = ASRProcessor()
    app.state.identity_fusion = IdentityFusion()

    # 记忆层
    app.state.episodic_memory = EpisodicMemory(db_path=db_path)
    app.state.semantic_memory = SemanticMemory(persist_dir=str(DATA_DIR / "chroma"))
    app.state.long_term_profile = LongTermProfile(db_path=db_path)
    app.state.working_memory = WorkingMemory()
    app.state.llm_client = LLMClient()
    app.state.consolidation = MemoryConsolidation(
        episodic=app.state.episodic_memory,
        semantic=app.state.semantic_memory,
        profile=app.state.long_term_profile,
        llm_client=app.state.llm_client,
    )

    # 人格层
    app.state.personality = PersonalityEngine(config=personality_cfg)
    app.state.intervention = InterventionDecider()
    app.state.prompt_builder = PromptBuilder(
        personality=app.state.personality,
        episodic=app.state.episodic_memory,
        semantic=app.state.semantic_memory,
        profile=app.state.long_term_profile,
    )

    # 输出层
    app.state.tts = TTSEngine()
    app.state.notification = NotificationManager(config=notification_cfg)

    # 安全模块
    app.state.anomaly_detector = AnomalyDetector()
    app.state.alert_manager = AlertManager(notification=app.state.notification)

    await asyncio.gather(
        app.state.vad.initialize(),
        app.state.speaker_id.initialize(),
        app.state.face_id.initialize(),
        app.state.asr.initialize(),
        app.state.episodic_memory.initialize(),
        app.state.semantic_memory.initialize(),
        app.state.long_term_profile.initialize(),
    )

    # LLM 引擎健康检查 (非阻塞，启动后可能还在加载模型)
    llm_ok = await app.state.llm_client.check_health()
    if not llm_ok:
        logger.warning("LLM 引擎尚未就绪，对话功能暂不可用。引擎启动后将自动恢复。")

    logger.info("CompanionBot 所有子系统初始化完成")
    yield

    logger.info("CompanionBot 关闭中...")


app = FastAPI(
    title="CompanionBot",
    description="家庭陪伴机器人大脑系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_router)


@app.get("/health")
async def health_check():
    """健康检查 — 返回各模块状态"""
    modules = {}

    # 感知层
    modules["vad"] = (
        "loaded"
        if getattr(app.state, "vad", None) and app.state.vad.model is not None
        else "fallback"
    )
    modules["speaker_id"] = (
        "loaded"
        if getattr(app.state, "speaker_id", None)
        and app.state.speaker_id.model is not None
        else "fallback"
    )
    modules["face_id"] = (
        "loaded"
        if getattr(app.state, "face_id", None) and app.state.face_id.model is not None
        else "fallback"
    )
    modules["asr"] = (
        "loaded"
        if getattr(app.state, "asr", None) and app.state.asr.model is not None
        else "not_loaded"
    )

    # 记忆层
    modules["memory"] = (
        "ok" if getattr(app.state, "working_memory", None) is not None else "not_loaded"
    )

    # 人格层
    modules["personality"] = (
        "ok" if getattr(app.state, "personality", None) is not None else "not_loaded"
    )

    # LLM
    llm = getattr(app.state, "llm_client", None)
    if llm:
        if llm._local_available and llm._cloud_available:
            modules["llm"] = "local+cloud"
        elif llm._local_available:
            modules["llm"] = "local"
        elif llm._cloud_available:
            modules["llm"] = "cloud"
        else:
            modules["llm"] = "unavailable"
    else:
        modules["llm"] = "not_loaded"

    return {"status": "ok", "service": "CompanionBot", "modules": modules}
