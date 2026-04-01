"""CompanionBot — FastAPI 入口 (多 Bot 实例架构)"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace


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

import json

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from server.bot_manager import BotManager
from server.perception.asr import ASRProcessor
from server.perception.face_id import FaceIdentifier
from server.perception.identity_fusion import IdentityFusion
from server.perception.speaker_id import SpeakerIdentifier
from server.perception.vad import VADProcessor
from server.personality.llm_client import LLMClient
from server.ws_handler import router as ws_router

logger = logging.getLogger("companion_bot")

CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent / "data"


def load_config(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _configure_gpu_memory():
    try:
        import torch

        if torch.cuda.is_available():
            fraction = float(os.environ.get("TORCH_CUDA_ALLOC_FRACTION", "0.30"))
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
    notification_cfg = load_config("notification_contacts.yaml")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "voiceprints").mkdir(exist_ok=True)

    # ===== MiniCPM-o 全模态引擎 (可选) =====
    minicpm_engine = None
    try:
        minicpm_cfg = load_config("minicpm.yaml")
    except Exception:
        minicpm_cfg = {}

    if minicpm_cfg.get("minicpm", {}).get("enabled", False):
        from server.engine.minicpm_engine import MiniCPMEngine

        minicpm_engine = MiniCPMEngine(minicpm_cfg["minicpm"])
        await minicpm_engine.initialize()
        if not minicpm_engine.available:
            logger.warning("MiniCPM-o 加载失败，回退到原有管线")
            minicpm_engine = None
        else:
            logger.info("MiniCPM-o 4.5 引擎已启用")

    # ===== 全局共享层 (感知 + LLM) =====
    shared = SimpleNamespace()
    shared.minicpm_engine = minicpm_engine
    shared.vad = VADProcessor()
    shared.speaker_id = SpeakerIdentifier(voiceprint_dir=str(DATA_DIR / "voiceprints"))
    shared.face_id = FaceIdentifier()
    shared.asr = ASRProcessor(minicpm_engine=minicpm_engine)
    shared.identity_fusion = IdentityFusion()
    shared.llm_client = LLMClient()

    init_tasks = [
        shared.vad.initialize(),
        shared.speaker_id.initialize(),
        shared.face_id.initialize(),
    ]
    # MiniCPM-o 启用时 ASR 委托给它，无需独立初始化
    if not minicpm_engine:
        init_tasks.append(shared.asr.initialize())
    await asyncio.gather(*init_tasks)

    llm_ok = await shared.llm_client.check_health()
    if not llm_ok:
        logger.warning("LLM 引擎尚未就绪，对话功能暂不可用。")

    app.state.shared = shared

    # ===== Bot 实例管理器 =====
    bot_manager = BotManager(DATA_DIR, shared)
    await bot_manager.initialize(personality_cfg, notification_cfg)
    app.state.bot_manager = bot_manager

    # 保留配置引用 (创建新 bot 时需要)
    app.state.personality_cfg = personality_cfg
    app.state.notification_cfg = notification_cfg

    logger.info(f"CompanionBot 启动完成: {len(bot_manager.bots)} 个 bot 实例")
    yield

    await bot_manager.shutdown_all()
    logger.info("CompanionBot 关闭中...")


app = FastAPI(
    title="CompanionBot",
    description="家庭陪伴机器人大脑系统 (多实例)",
    version="0.2.0",
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


# ===== Health =====
@app.get("/health")
async def health_check():
    shared = app.state.shared
    modules = {}
    modules["vad"] = "loaded" if shared.vad.model is not None else "fallback"
    modules["speaker_id"] = (
        "loaded" if shared.speaker_id.model is not None else "fallback"
    )
    modules["face_id"] = "loaded" if shared.face_id.model is not None else "fallback"
    modules["asr"] = "loaded" if shared.asr.model is not None else "not_loaded"
    modules["minicpm"] = (
        "loaded"
        if shared.minicpm_engine and shared.minicpm_engine.available
        else "disabled"
    )
    llm = shared.llm_client
    if llm._local_available and llm._cloud_available:
        modules["llm"] = "local+cloud"
    elif llm._local_available:
        modules["llm"] = "local"
    elif llm._cloud_available:
        modules["llm"] = "cloud"
    else:
        modules["llm"] = "unavailable"

    bots = app.state.bot_manager.list_bots()
    return {
        "status": "ok",
        "service": "CompanionBot",
        "modules": modules,
        "bots": len(bots),
    }


# ===== App Update API =====
@app.get("/api/app/version")
async def app_version():
    version_file = Path(__file__).parent / "static" / "version.json"
    if version_file.exists():
        with open(version_file) as f:
            info = json.load(f)
        info["url"] = "/api/app/download"
        return info
    return {"version": "0.0.0", "version_code": 0, "url": "/api/app/download"}


@app.get("/api/app/download")
async def app_download():
    apk_path = Path(__file__).parent / "static" / "companionbot.apk"
    if apk_path.exists():
        return FileResponse(
            apk_path,
            filename="companionbot.apk",
            media_type="application/vnd.android.package-archive",
        )
    return JSONResponse({"error": "APK not found"}, status_code=404)


# ===== Bot 管理 API =====
@app.get("/api/bots")
async def list_bots():
    return app.state.bot_manager.list_bots()


@app.post("/api/bots")
async def create_bot(body: dict):
    bot_id = body.get("bot_id")
    if not bot_id:
        return {"error": "bot_id is required"}, 400
    name = body.get("name", "天天")
    overrides = body.get("personality_overrides", {})
    try:
        instance = await app.state.bot_manager.create_bot(
            bot_id=bot_id, name=name, personality_overrides=overrides
        )
        return instance.to_dict()
    except ValueError as e:
        return {"error": str(e)}


@app.get("/api/bots/{bot_id}")
async def get_bot(bot_id: str):
    instance = await app.state.bot_manager.get_bot(bot_id)
    if not instance:
        return {"error": f"Bot [{bot_id}] 不存在"}
    return instance.to_dict()


@app.put("/api/bots/{bot_id}")
async def update_bot(bot_id: str, body: dict):
    try:
        instance = await app.state.bot_manager.update_bot(
            bot_id=bot_id,
            name=body.get("name"),
            personality_overrides=body.get("personality_overrides"),
        )
        return instance.to_dict()
    except ValueError as e:
        return {"error": str(e)}


@app.delete("/api/bots/{bot_id}")
async def delete_bot(bot_id: str):
    ok = await app.state.bot_manager.delete_bot(bot_id)
    return {"deleted": ok}


# ===== 成员管理 API (per bot) =====
@app.get("/api/bots/{bot_id}/members")
async def list_members(bot_id: str):
    instance = await app.state.bot_manager.get_bot(bot_id)
    if not instance:
        return {"error": f"Bot [{bot_id}] 不存在"}
    return await instance.long_term_profile.get_all_members()


@app.get("/api/bots/{bot_id}/members/{person_id}")
async def get_member(bot_id: str, person_id: str):
    instance = await app.state.bot_manager.get_bot(bot_id)
    if not instance:
        return {"error": f"Bot [{bot_id}] 不存在"}
    profile = await instance.long_term_profile.get_profile(person_id)
    if not profile:
        return {"error": f"成员 [{person_id}] 不存在"}
    return profile


@app.put("/api/bots/{bot_id}/members/{person_id}")
async def update_member(bot_id: str, person_id: str, body: dict):
    instance = await app.state.bot_manager.get_bot(bot_id)
    if not instance:
        return {"error": f"Bot [{bot_id}] 不存在"}

    profile = await instance.long_term_profile.get_profile(person_id)
    if not profile:
        return {"error": f"成员 [{person_id}] 不存在"}

    # 更新自定义提示词
    if "custom_prompt" in body:
        await instance.long_term_profile.update_custom_prompt(
            person_id, body["custom_prompt"]
        )

    # 更新其他字段
    if "interests" in body:
        await instance.long_term_profile.update_interests(person_id, body["interests"])
    if "health_conditions" in body:
        await instance.long_term_profile.update_health(
            person_id, body["health_conditions"]
        )
    if "recent_concerns" in body:
        await instance.long_term_profile.update_concerns(
            person_id, body["recent_concerns"]
        )

    return await instance.long_term_profile.get_profile(person_id)
