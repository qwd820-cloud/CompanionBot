"""WebSocket 处理器 — 多 Bot 实例架构

路由: /ws/{bot_id}/{client_id}
感知层 (shared): VAD, ASR, SpeechBrain, InsightFace — 全局共享
Bot 实例 (bot): 记忆, 人格, 安全, TTS — 每个 bot 独立
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import struct
import time
from enum import IntEnum

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.utils.keywords import FAREWELL_WORDS, WAKE_WORDS

logger = logging.getLogger("companion_bot.ws")

router = APIRouter()


class MessageType(IntEnum):
    AUDIO = 1
    VIDEO = 2
    TEXT = 3
    TTS_AUDIO = 4
    COMMAND = 5
    NOTIFICATION = 6


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        # client_id → bot_id 映射
        self.client_bot_map: dict[str, str] = {}

    async def connect(self, client_id: str, bot_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.client_bot_map[client_id] = bot_id
        logger.info(f"客户端连接: {client_id} → bot [{bot_id}]")

    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
        self.client_bot_map.pop(client_id, None)
        logger.info(f"客户端断开: {client_id}")

    async def send_tts_audio(self, client_id: str, audio_data: bytes):
        ws = self.active_connections.get(client_id)
        if ws:
            header = struct.pack("!B", MessageType.TTS_AUDIO)
            await ws.send_bytes(header + audio_data)

    async def send_json_message(self, client_id: str, data: dict):
        ws = self.active_connections.get(client_id)
        if ws:
            await ws.send_json(data)

    async def send_notification_command(self, client_id: str, phone: str, message: str):
        ws = self.active_connections.get(client_id)
        if ws:
            await ws.send_json(
                {
                    "type": "notification",
                    "action": "send_sms",
                    "phone": phone,
                    "message": message,
                }
            )

    def get_clients_for_bot(self, bot_id: str) -> list[str]:
        """获取某个 bot 下的所有活跃 client_id"""
        return [cid for cid, bid in self.client_bot_map.items() if bid == bot_id]


manager = ConnectionManager()


# ===== 新路径: /ws/{bot_id}/{client_id} =====
@router.websocket("/ws/{bot_id}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, bot_id: str, client_id: str):
    """多实例 WebSocket 端点"""
    app = websocket.app
    bot = await app.state.bot_manager.get_or_default(bot_id)
    if not bot:
        await websocket.close(code=4004, reason=f"Bot [{bot_id}] not found")
        return

    shared = app.state.shared
    await manager.connect(client_id, bot_id, websocket)
    bot.working_memory.start_session(client_id)

    # Reset VAD for fresh connection
    with contextlib.suppress(Exception):
        shared.vad.reset()

    # 只对主客户端自动问候，管理连接不触发
    if client_id.startswith("android_client"):
        await _auto_greet(shared, bot, client_id)

    try:
        while True:
            data = await websocket.receive()
            if "bytes" in data:
                await _handle_binary(shared, bot, client_id, data["bytes"])
            elif "text" in data:
                await _handle_text(shared, bot, client_id, data["text"])
    except WebSocketDisconnect:
        logger.info(f"客户端 {client_id} 正常断开")
    except Exception as e:
        logger.error(f"WebSocket 错误 ({client_id}): {e}")
    finally:
        session_data = bot.working_memory.end_session(client_id)
        if session_data and session_data.get("turns"):
            try:
                await bot.consolidation.consolidate(session_data)
            except Exception as e:
                print(f"[ERROR] 记忆沉淀失败: {e}")
        manager.disconnect(client_id)


# ===== 兼容旧路径: /ws/{client_id} → 使用 default bot =====
@router.websocket("/ws/{client_id}")
async def websocket_endpoint_legacy(websocket: WebSocket, client_id: str):
    """兼容旧客户端 — 自动使用 default bot"""
    await websocket_endpoint(websocket, "default", client_id)


# ===== Handlers (shared=感知层, bot=Bot实例) =====


async def _auto_greet(shared, bot, client_id: str):
    try:
        bot_name = bot.personality.name
        members = await bot.long_term_profile.get_all_members()
        if not members:
            greet_prompt = [
                {
                    "role": "system",
                    "content": f"/no_think 你是{bot_name}，一个家庭陪伴机器人。你刚刚被第一次启动，面前是一个你还不认识的家人。请简短地做自我介绍（不超过两句话），然后自然地问对方叫什么。不要啰嗦，不要用比喻。",
                },
                {
                    "role": "user",
                    "content": "/no_think [系统] 有人连接了，请打个招呼并自我介绍。",
                },
            ]
        else:
            greet_prompt = [
                {
                    "role": "system",
                    "content": f"/no_think 你是{bot_name}，一个家庭陪伴机器人。家人上线了，简短打个招呼就行，一句话。",
                },
                {"role": "user", "content": "/no_think [系统] 家人连接了。"},
            ]

        reply = await shared.llm_client.chat(greet_prompt, task_type="daily")
        reply_text = reply.get("content", "") or f"嘿，我是{bot_name}，你好呀！"

        await manager.send_json_message(
            client_id,
            {
                "type": "reply",
                "person_id": "bot",
                "text": reply_text,
                "emotion": "happy",
            },
        )

        try:
            audio_data = await asyncio.wait_for(
                bot.tts.synthesize(text=reply_text, emotion="happy"),
                timeout=15,
            )
            if audio_data:
                await manager.send_tts_audio(client_id, audio_data)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"自动问候失败: {e}")


async def _handle_binary(shared, bot, client_id: str, raw: bytes):
    if len(raw) < 1:
        return
    msg_type = raw[0]
    payload = raw[1:]
    if msg_type == MessageType.AUDIO:
        await _process_audio(shared, bot, client_id, payload)
    elif msg_type == MessageType.VIDEO:
        await _process_video(shared, bot, client_id, payload)


async def _handle_text(shared, bot, client_id: str, text: str):
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"无效 JSON: {text[:100]}")
        return

    msg_type = msg.get("type")
    if msg_type == "enroll_voice":
        await _handle_enroll_voice(shared, bot, client_id, msg)
    elif msg_type == "enroll_face":
        await _handle_enroll_face(shared, bot, client_id, msg)
    elif msg_type == "enroll_profile":
        await _handle_enroll_profile(shared, bot, client_id, msg)
    elif msg_type == "list_members":
        await _handle_list_members(shared, bot, client_id)
    elif msg_type == "get_member":
        await _handle_get_member(shared, bot, client_id, msg)
    elif msg_type == "delete_member":
        await _handle_delete_member(shared, bot, client_id, msg)
    elif msg_type == "update_member":
        await _handle_update_member(shared, bot, client_id, msg)
    elif msg_type == "text_input":
        await _handle_text_input(shared, bot, client_id, msg)


async def _process_audio(shared, bot, client_id: str, audio_data: bytes):
    speech_segments = await shared.vad.process(audio_data)
    if speech_segments:
        await manager.send_json_message(client_id, {"type": "stop_tts"})
    if not speech_segments:
        return

    for segment in speech_segments:
        speaker_result, asr_result = await asyncio.gather(
            shared.speaker_id.identify(segment),
            shared.asr.transcribe(segment),
        )
        person_id = speaker_result.get("person_id", "unknown")
        voice_score = speaker_result.get("score", 0.0)
        text = asr_result.get("text", "")
        if not text.strip():
            continue

        anomaly = await bot.anomaly_detector.check_audio(text=text, person_id=person_id)
        if anomaly:
            await bot.alert_manager.handle_anomaly(anomaly, client_id, manager)

        face_result = bot.working_memory.get_latest_face(client_id)
        if face_result:
            fused = shared.identity_fusion.fuse(
                voice_id=person_id,
                voice_score=voice_score,
                face_id=face_result.get("person_id"),
                face_score=face_result.get("score", 0.0),
            )
            person_id = fused["person_id"]

        bot.working_memory.add_turn(
            session_id=client_id,
            person_id=person_id,
            text=text,
            role="user",
        )

        # --- Conversation mode: idle/active with wake word detection ---
        session = bot.working_memory.get_session(client_id)
        now = time.time()

        # Check timeout -> back to idle
        if (
            session.conversation_mode == "active"
            and now - session.last_interaction_time > 30
        ):
            session.conversation_mode = "idle"
            session.active_person_id = None
            logger.info("[MODE] 超时30秒，回到 IDLE")

        # Wake word / farewell detection
        has_wake = any(w in text for w in WAKE_WORDS)
        has_farewell = any(w in text for w in FAREWELL_WORDS)

        if has_farewell and session.conversation_mode == "active":
            session.conversation_mode = "idle"
            session.active_person_id = None
            logger.info("[MODE] 用户说再见，回到 IDLE")
            should_respond = False
        elif has_wake:
            session.conversation_mode = "active"
            session.active_person_id = person_id
            session.last_interaction_time = now
            logger.info(f"[MODE] 唤醒词检测到，进入 ACTIVE, person={person_id}")
            should_respond = True
        elif session.conversation_mode == "active":
            elapsed = now - session.last_interaction_time
            if elapsed < 5:
                should_respond = True
                logger.info(f"[MODE] ACTIVE模式 {elapsed:.1f}s内，直接回复")
            elif elapsed < 30 and person_id == session.active_person_id:
                should_respond = True
                logger.info(f"[MODE] ACTIVE模式 同一人 {elapsed:.1f}s，回复")
            else:
                should_respond = False
                logger.info(
                    f"[MODE] ACTIVE模式 但不匹配: elapsed={elapsed:.1f}s, person={person_id} vs active={session.active_person_id}"
                )
        else:
            should_respond = False
            # Don't log for idle mode - too noisy

        if should_respond:
            session.last_interaction_time = now

        # Fallback: if not responding via conversation mode, check intervention
        if not should_respond:
            context = bot.working_memory.get_context(client_id)
            decision = bot.intervention.should_intervene(context)
            should_respond = decision[0]

        if should_respond:
            await _generate_and_respond(
                shared,
                bot,
                client_id,
                person_id,
                audio_segment=segment.audio,
            )


async def _process_video(shared, bot, client_id: str, frame_data: bytes):
    face_result = await shared.face_id.identify(frame_data)
    if face_result:
        bot.working_memory.update_face_result(client_id, face_result)
        anomaly = await bot.anomaly_detector.check_presence(
            person_id=face_result.get("person_id"),
            client_id=client_id,
        )
        if anomaly:
            await bot.alert_manager.handle_anomaly(anomaly, client_id, manager)


def _needs_deep_thinking(context: dict) -> bool:
    """判断是否需要启用 MiniCPM-o 思考模式"""
    turns = context.get("turns", [])
    if not turns:
        return False
    last_text = turns[-1].get("text", "") if turns else ""
    thinking_triggers = ["怎么办", "为什么", "建议", "应该", "帮我分析", "你觉得"]
    return any(t in last_text for t in thinking_triggers)


async def _generate_and_respond(
    shared,
    bot,
    client_id: str,
    person_id: str,
    audio_segment=None,
):
    context = bot.working_memory.get_context(client_id)
    current_emotion = bot.personality.current_emotion

    reply_text = ""
    reply_audio = None

    minicpm = getattr(shared, "minicpm_engine", None)
    if minicpm and minicpm.available and audio_segment is not None:
        # === 端到端路径: MiniCPM-o 直接听音频回复 (无需预 ASR) ===
        try:
            system_text = await bot.prompt_builder.build_system_text(person_id, context)
            history = bot.prompt_builder.get_history_turns(context)
            enable_thinking = _needs_deep_thinking(context)

            result = await minicpm.chat_with_audio(
                audio=audio_segment,
                system_text=system_text,
                history=history,
                emotion=current_emotion,
                enable_thinking=enable_thinking,
            )
            reply_text = (result or {}).get("content", "")
            reply_audio = (result or {}).get("audio")
        except Exception as e:
            logger.error(f"MiniCPM-o 端到端调用异常: {e}")

    if not reply_text:
        # === 回退路径: 文本 LLM + 独立 TTS ===
        try:
            messages = await bot.prompt_builder.build(
                person_id=person_id, context=context
            )
            reply = await shared.llm_client.chat(messages, task_type="daily")
            reply_text = (reply or {}).get("content", "")
        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            return

    if not reply_text:
        return

    bot.personality.update_emotion(context, reply_text)

    bot.working_memory.add_turn(
        session_id=client_id,
        person_id="bot",
        text=reply_text,
        role="assistant",
    )

    current_emotion = bot.personality.current_emotion
    await manager.send_json_message(
        client_id,
        {
            "type": "reply",
            "person_id": person_id,
            "text": reply_text,
            "emotion": current_emotion,
        },
    )

    if reply_audio:
        await manager.send_tts_audio(client_id, reply_audio)
    else:
        try:
            audio_data = await asyncio.wait_for(
                bot.tts.synthesize(text=reply_text, emotion=current_emotion),
                timeout=15,
            )
            if audio_data:
                await manager.send_tts_audio(client_id, audio_data)
        except TimeoutError:
            logger.warning(f"TTS 超时: {reply_text[:30]}...")
        except Exception as e:
            logger.warning(f"TTS 失败: {e}")

    # 无论 TTS 成功与否，都发送完成信号，让客户端切换状态
    await manager.send_json_message(client_id, {"type": "reply_done"})


# ===== Enroll / Member management =====


async def _handle_enroll_voice(shared, bot, client_id: str, msg: dict):
    person_id = msg.get("person_id")
    if not person_id:
        return
    audio_samples_b64 = msg.get("audio_samples", [])
    if audio_samples_b64:
        import numpy as np

        audio_samples = []
        for b64_str in audio_samples_b64:
            raw = base64.b64decode(b64_str)
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            audio_samples.append(pcm)
        try:
            await shared.speaker_id.enroll(person_id, audio_samples)
            await manager.send_json_message(
                client_id,
                {
                    "type": "enroll_result",
                    "step": "voice",
                    "success": True,
                    "message": f"声纹注册成功 ({len(audio_samples)} 段音频)",
                },
            )
        except Exception as e:
            await manager.send_json_message(
                client_id,
                {
                    "type": "enroll_result",
                    "step": "voice",
                    "success": False,
                    "message": f"声纹注册失败: {e}",
                },
            )


async def _handle_enroll_face(shared, bot, client_id: str, msg: dict):
    person_id = msg.get("person_id")
    if not person_id:
        return
    photos_b64 = msg.get("photos", [])
    if photos_b64:
        image_data_list = [base64.b64decode(b64_str) for b64_str in photos_b64]
        try:
            await shared.face_id.enroll(person_id, image_data_list)
            await manager.send_json_message(
                client_id,
                {
                    "type": "enroll_result",
                    "step": "face",
                    "success": True,
                    "message": f"人脸注册成功 ({len(image_data_list)} 张照片)",
                },
            )
        except Exception as e:
            await manager.send_json_message(
                client_id,
                {
                    "type": "enroll_result",
                    "step": "face",
                    "success": False,
                    "message": f"人脸注册失败: {e}",
                },
            )


async def _handle_enroll_profile(shared, bot, client_id: str, msg: dict):
    person_id = msg.get("person_id")
    if not person_id:
        return
    try:
        await bot.long_term_profile.add_member(
            person_id=person_id,
            name=msg.get("name", ""),
            nickname=msg.get("nickname", ""),
            role=msg.get("role", "adult"),
            age=msg.get("age", 0),
            relationship=msg.get("relationship", ""),
        )
        await manager.send_json_message(
            client_id,
            {
                "type": "enroll_result",
                "step": "profile",
                "success": True,
                "message": f"成员 {msg.get('name', person_id)} 注册成功",
            },
        )
    except Exception as e:
        await manager.send_json_message(
            client_id,
            {
                "type": "enroll_result",
                "step": "profile",
                "success": False,
                "message": f"档案注册失败: {e}",
            },
        )


async def _handle_list_members(shared, bot, client_id: str):
    try:
        members = await bot.long_term_profile.get_all_members()
        enriched = []
        for m in members:
            profile = await bot.long_term_profile.get_profile(m["person_id"])
            enriched.append(
                {
                    "person_id": m["person_id"],
                    "name": m["name"],
                    "role": m["role"],
                    "age": profile.get("age") if profile else None,
                    "relationship": profile.get("relationship") if profile else None,
                }
            )
        await manager.send_json_message(
            client_id, {"type": "members_list", "members": enriched}
        )
    except Exception as e:
        logger.error(f"获取成员列表失败: {e}")
        await manager.send_json_message(
            client_id, {"type": "members_list", "members": []}
        )


async def _handle_get_member(shared, bot, client_id: str, msg: dict):
    person_id = msg.get("person_id")
    if not person_id:
        await manager.send_json_message(
            client_id, {"type": "member_detail", "profile": None, "episodes": []}
        )
        return
    try:
        profile = await bot.long_term_profile.get_profile(person_id)
        episodes_raw = await bot.episodic_memory.get_recent(person_id, limit=20)
        episodes = [
            {
                "summary": ep.summary,
                "emotion_tag": ep.emotion_tag,
                "timestamp": ep.timestamp,
            }
            for ep in episodes_raw
        ]
        await manager.send_json_message(
            client_id,
            {"type": "member_detail", "profile": profile, "episodes": episodes},
        )
    except Exception as e:
        logger.error(f"获取成员详情失败: {e}")
        await manager.send_json_message(
            client_id, {"type": "member_detail", "profile": None, "episodes": []}
        )


async def _handle_delete_member(shared, bot, client_id: str, msg: dict):
    person_id = msg.get("person_id")
    if not person_id:
        await manager.send_json_message(
            client_id, {"type": "member_deleted", "success": False, "person_id": None}
        )
        return
    try:
        profile_deleted = await bot.long_term_profile.delete_member(person_id)
        await bot.episodic_memory.delete_by_person(person_id)
        voiceprint_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "voiceprints",
            f"{person_id}.npy",
        )
        if os.path.exists(voiceprint_path):
            os.remove(voiceprint_path)
        await manager.send_json_message(
            client_id,
            {
                "type": "member_deleted",
                "success": profile_deleted,
                "person_id": person_id,
            },
        )
    except Exception as e:
        logger.error(f"删除成员失败: {e}")
        await manager.send_json_message(
            client_id,
            {"type": "member_deleted", "success": False, "person_id": person_id},
        )


async def _handle_update_member(shared, bot, client_id: str, msg: dict):
    person_id = msg.get("person_id")
    if not person_id:
        await manager.send_json_message(
            client_id,
            {"type": "member_updated", "success": False, "person_id": None},
        )
        return
    try:
        success = await bot.long_term_profile.update_member(
            person_id,
            name=msg.get("name"),
            nickname=msg.get("nickname"),
            role=msg.get("role"),
            age=msg.get("age"),
            relationship=msg.get("relationship"),
        )
        await manager.send_json_message(
            client_id,
            {"type": "member_updated", "success": success, "person_id": person_id},
        )
    except Exception as e:
        logger.error(f"更新成员失败: {e}")
        await manager.send_json_message(
            client_id,
            {"type": "member_updated", "success": False, "person_id": person_id},
        )


async def _handle_text_input(shared, bot, client_id: str, msg: dict):
    text = msg.get("text", "")
    person_id = msg.get("person_id", "unknown")
    if not text:
        return

    if bot.proactive:
        bot.proactive.update_activity(person_id)

    anomaly = await bot.anomaly_detector.check_audio(text=text, person_id=person_id)
    if anomaly:
        await bot.alert_manager.handle_anomaly(anomaly, client_id, manager)

    bot.working_memory.add_turn(
        session_id=client_id,
        person_id=person_id,
        text=text,
        role="user",
    )
    await _generate_and_respond(shared, bot, client_id, person_id)
