"""WebSocket 处理器 — 音频流/视频帧的实时通信"""

import asyncio
import json
import logging
import struct
from enum import IntEnum

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("companion_bot.ws")

router = APIRouter()


class MessageType(IntEnum):
    """WebSocket 消息类型"""
    AUDIO = 1       # 音频数据 (16kHz PCM)
    VIDEO = 2       # 视频帧 (JPEG)
    TEXT = 3        # 文本消息 (JSON)
    TTS_AUDIO = 4   # TTS 回放音频
    COMMAND = 5     # 控制指令
    NOTIFICATION = 6  # 通知指令 (发短信等)


class ConnectionManager:
    """管理所有活跃的 WebSocket 连接"""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"客户端连接: {client_id}")

    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
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

    async def send_notification_command(
        self, client_id: str, phone: str, message: str
    ):
        ws = self.active_connections.get(client_id)
        if ws:
            await ws.send_json({
                "type": "notification",
                "action": "send_sms",
                "phone": phone,
                "message": message,
            })


manager = ConnectionManager()


@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """主 WebSocket 端点，处理音频流和视频帧"""
    app = websocket.app
    await manager.connect(client_id, websocket)

    # 为此连接创建工作记忆会话
    app.state.working_memory.start_session(client_id)

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data:
                await _handle_binary(app, client_id, data["bytes"])
            elif "text" in data:
                await _handle_text(app, client_id, data["text"])
    except WebSocketDisconnect:
        logger.info(f"客户端 {client_id} 正常断开")
    except Exception as e:
        logger.error(f"WebSocket 错误 ({client_id}): {e}")
    finally:
        # 对话结束，触发记忆沉淀
        session_data = app.state.working_memory.end_session(client_id)
        if session_data and session_data.get("turns"):
            asyncio.create_task(
                app.state.consolidation.consolidate(session_data)
            )
        manager.disconnect(client_id)


async def _handle_binary(app, client_id: str, raw: bytes):
    """处理二进制消息 (音频/视频)"""
    if len(raw) < 1:
        return

    msg_type = raw[0]
    payload = raw[1:]

    if msg_type == MessageType.AUDIO:
        await _process_audio(app, client_id, payload)
    elif msg_type == MessageType.VIDEO:
        await _process_video(app, client_id, payload)


async def _handle_text(app, client_id: str, text: str):
    """处理文本消息 (JSON 指令)"""
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"无效 JSON: {text[:100]}")
        return

    msg_type = msg.get("type")
    if msg_type == "enroll_voice":
        await _handle_enroll_voice(app, msg)
    elif msg_type == "enroll_face":
        await _handle_enroll_face(app, msg)
    elif msg_type == "text_input":
        await _handle_text_input(app, client_id, msg)


async def _process_audio(app, client_id: str, audio_data: bytes):
    """音频处理管线: VAD → 声纹 → ASR → 对话处理"""
    # VAD 检测
    speech_segments = await app.state.vad.process(audio_data)
    if not speech_segments:
        return

    for segment in speech_segments:
        # 声纹识别
        speaker_result = await app.state.speaker_id.identify(segment)
        person_id = speaker_result.get("person_id", "unknown")
        voice_score = speaker_result.get("score", 0.0)

        # ASR 转写
        asr_result = await app.state.asr.transcribe(segment)
        text = asr_result.get("text", "")
        if not text.strip():
            continue

        # 异常检测 (呼救等)
        anomaly = await app.state.anomaly_detector.check_audio(
            text=text, person_id=person_id
        )
        if anomaly:
            await app.state.alert_manager.handle_anomaly(
                anomaly, client_id, manager
            )

        # 身份融合 (如果同时有视频人脸结果)
        face_result = app.state.working_memory.get_latest_face(client_id)
        if face_result:
            fused = app.state.identity_fusion.fuse(
                voice_id=person_id,
                voice_score=voice_score,
                face_id=face_result.get("person_id"),
                face_score=face_result.get("score", 0.0),
            )
            person_id = fused["person_id"]

        # 更新工作记忆
        app.state.working_memory.add_turn(
            session_id=client_id,
            person_id=person_id,
            text=text,
            role="user",
        )

        # 判断是否需要回复 (直接对话 or 插话决策)
        should_respond = app.state.working_memory.is_addressed_to_bot(
            client_id, text
        )
        if not should_respond:
            context = app.state.working_memory.get_context(client_id)
            decision = app.state.intervention.should_intervene(context)
            should_respond = decision[0]

        if should_respond:
            await _generate_and_respond(app, client_id, person_id)


async def _process_video(app, client_id: str, frame_data: bytes):
    """视频帧处理: 人脸检测 → 人脸识别"""
    face_result = await app.state.face_id.identify(frame_data)
    if face_result:
        app.state.working_memory.update_face_result(client_id, face_result)

        # 长时间无活动检测
        anomaly = await app.state.anomaly_detector.check_presence(
            person_id=face_result.get("person_id"),
            client_id=client_id,
        )
        if anomaly:
            await app.state.alert_manager.handle_anomaly(
                anomaly, client_id, manager
            )


async def _generate_and_respond(app, client_id: str, person_id: str):
    """生成 LLM 回复并通过 TTS 返回"""
    # 构建 prompt
    context = app.state.working_memory.get_context(client_id)
    messages = await app.state.prompt_builder.build(
        person_id=person_id, context=context
    )

    # LLM 推理
    reply = await app.state.llm_client.chat(messages, task_type="daily")
    reply_text = reply.get("content", "")
    if not reply_text:
        return

    # 更新人格情绪
    app.state.personality.update_emotion(context, reply_text)

    # 更新工作记忆
    app.state.working_memory.add_turn(
        session_id=client_id,
        person_id="bot",
        text=reply_text,
        role="assistant",
    )

    # TTS 合成
    audio_data = await app.state.tts.synthesize(
        text=reply_text,
        emotion=app.state.personality.current_emotion,
    )

    # 发送回复
    await manager.send_json_message(client_id, {
        "type": "reply",
        "person_id": person_id,
        "text": reply_text,
        "emotion": app.state.personality.current_emotion,
    })
    if audio_data:
        await manager.send_tts_audio(client_id, audio_data)


async def _handle_enroll_voice(app, msg: dict):
    """处理声纹注册请求"""
    person_id = msg.get("person_id")
    audio_data = msg.get("audio_data")
    if person_id and audio_data:
        await app.state.speaker_id.enroll(person_id, audio_data)
        logger.info(f"声纹注册完成: {person_id}")


async def _handle_enroll_face(app, msg: dict):
    """处理人脸注册请求"""
    person_id = msg.get("person_id")
    image_data = msg.get("image_data")
    if person_id and image_data:
        await app.state.face_id.enroll(person_id, image_data)
        logger.info(f"人脸注册完成: {person_id}")


async def _handle_text_input(app, client_id: str, msg: dict):
    """处理文本输入 (调试/测试用)"""
    text = msg.get("text", "")
    person_id = msg.get("person_id", "unknown")
    if not text:
        return

    app.state.working_memory.add_turn(
        session_id=client_id,
        person_id=person_id,
        text=text,
        role="user",
    )
    await _generate_and_respond(app, client_id, person_id)
