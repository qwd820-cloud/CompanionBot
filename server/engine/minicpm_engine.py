"""MiniCPM-o 4.5 统一引擎 — ASR + LLM + TTS 三合一"""

import asyncio
import logging
import tempfile
from functools import partial
from pathlib import Path

import numpy as np

logger = logging.getLogger("companion_bot.minicpm")

# MiniCPM-o TTS 输出采样率
OUT_SAMPLE_RATE = 24000
# 输入音频采样率
IN_SAMPLE_RATE = 16000

# 情绪到 CosyVoice2 风格标签的映射
EMOTION_STYLE_MAP = {
    "neutral": "",
    "happy": "Please speak in a happy and cheerful tone.",
    "concerned": "Please speak in a gentle and caring tone.",
    "tired": "Please speak in a calm and slow tone.",
    "curious": "Please speak in a curious and interested tone.",
    "slightly_annoyed": "Please speak in a slightly impatient tone.",
}


class MiniCPMEngine:
    """MiniCPM-o 4.5 全模态引擎封装

    提供三个核心接口:
    - transcribe(): ASR 快速转写
    - chat_and_speak(): 统一对话 (音频+context → 文本+语音)
    - synthesize(): 纯 TTS 合成
    """

    def __init__(self, config: dict):
        self._config = config
        self._model = None
        self._ref_audio = None
        self._available = False
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._available

    async def initialize(self):
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._load_model)
            self._available = True
            logger.info("MiniCPM-o 4.5 引擎初始化完成")
        except Exception as e:
            logger.error(f"MiniCPM-o 4.5 加载失败: {e}")
            self._available = False

    def _load_model(self):
        import os

        import torch
        from transformers import AutoModel

        # 清除代理，避免 from_pretrained 检查远程时卡住
        for k in [
            "ALL_PROXY",
            "all_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
        ]:
            os.environ.pop(k, None)

        model_path = self._config.get("model_path", "openbmb/MiniCPM-o-4_5")
        dtype_str = self._config.get("dtype", "bfloat16")
        device = self._config.get("device", "cuda")

        dtype_map = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }
        torch_dtype = dtype_map.get(dtype_str, torch.bfloat16)

        logger.info(f"加载 MiniCPM-o 4.5: {model_path} ({dtype_str})")

        self._model = AutoModel.from_pretrained(
            model_path,
            trust_remote_code=True,
            attn_implementation="sdpa",
            torch_dtype=torch_dtype,
            init_vision=True,
            init_audio=True,
            init_tts=True,
        )
        self._model.eval()

        if device == "cuda":
            import torch as _torch

            if _torch.cuda.is_available():
                self._model = self._model.cuda()
            else:
                logger.warning("CUDA 不可用，回退 CPU")
                self._model = self._model.cpu()
        else:
            self._model = self._model.to(device)

        # 初始化 TTS 模块
        self._model.init_tts()

        # 加载声音克隆参考音频
        voice_ref_path = self._config.get("voice_ref_path")
        if voice_ref_path and Path(voice_ref_path).exists():
            self._load_voice_ref(voice_ref_path)
        else:
            logger.info("未配置参考语音，使用模型默认声音")

    def _load_voice_ref(self, path: str):
        import librosa

        logger.info(f"加载参考语音: {path}")
        self._ref_audio, _ = librosa.load(path, sr=IN_SAMPLE_RATE, mono=True)
        self._model.init_token2wav_cache(self._ref_audio)

    # ====================== ASR ======================

    async def transcribe(self, audio: np.ndarray) -> dict:
        """ASR 快速转写 — 仅文本输出，不生成语音

        Args:
            audio: float32 numpy array, 16kHz 单声道

        Returns:
            {"text": str, "timestamps": []}
        """
        if not self._available:
            return {"text": "", "timestamps": []}

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, partial(self._transcribe_sync, audio)
            )
            return result
        except Exception as e:
            logger.error(f"MiniCPM-o ASR 失败: {e}")
            return {"text": "", "timestamps": []}

    def _transcribe_sync(self, audio: np.ndarray) -> dict:
        task_prompt = "请仔细听这段音频并转写内容。\n"
        msgs = [{"role": "user", "content": [task_prompt, audio]}]

        text = self._model.chat(
            msgs=msgs,
            do_sample=False,
            max_new_tokens=512,
            use_tts_template=True,
            generate_audio=False,
            temperature=0.1,
        )
        return {"text": text.strip() if isinstance(text, str) else "", "timestamps": []}

    # ====================== Chat + TTS ======================

    async def chat_and_speak(
        self,
        audio_segment: np.ndarray | None,
        messages: list[dict],
        emotion: str = "neutral",
        temperature: float = 0.7,
        max_new_tokens: int | None = None,
    ) -> dict:
        """统一对话: 音频+文本context → 文本回复+语音回复

        Args:
            audio_segment: 用户语音 (float32, 16kHz)，None 则为纯文本对话
            messages: OpenAI 格式消息列表 (PromptBuilder 输出)
            emotion: 当前情绪状态
            temperature: 采样温度
            max_new_tokens: 最大生成 token 数

        Returns:
            {"content": str, "audio": bytes|None, "model": "minicpm-o-4.5"}
        """
        if not self._available:
            return {"content": "", "audio": None, "model": "minicpm-o-4.5"}

        if max_new_tokens is None:
            max_new_tokens = self._config.get("max_new_tokens", 200)

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                partial(
                    self._chat_and_speak_sync,
                    audio_segment,
                    messages,
                    emotion,
                    temperature,
                    max_new_tokens,
                ),
            )
            return result
        except Exception as e:
            logger.error(f"MiniCPM-o chat_and_speak 失败: {e}")
            return {"content": "", "audio": None, "model": "minicpm-o-4.5"}

    def _chat_and_speak_sync(
        self,
        audio_segment: np.ndarray | None,
        messages: list[dict],
        emotion: str,
        temperature: float,
        max_new_tokens: int,
    ) -> dict:
        minicpm_msgs = self._convert_messages(messages, audio_segment, emotion)

        generate_audio = audio_segment is not None or self._config.get(
            "always_generate_audio", False
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            text = self._model.chat(
                msgs=minicpm_msgs,
                do_sample=True,
                max_new_tokens=max_new_tokens,
                use_tts_template=True,
                generate_audio=generate_audio,
                output_audio_path=tmp_path if generate_audio else None,
                temperature=temperature,
            )

            audio_bytes = None
            if generate_audio:
                audio_bytes = self._read_audio_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return {
            "content": text.strip() if isinstance(text, str) else "",
            "audio": audio_bytes,
            "model": "minicpm-o-4.5",
        }

    # ====================== TTS Only ======================

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes | None:
        """纯 TTS 合成 — 文本输入，语音输出

        用于主动问候、文本输入等无音频触发的场景。
        """
        if not self._available or not text.strip():
            return None

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, partial(self._synthesize_sync, text, emotion)
            )
            return result
        except Exception as e:
            logger.error(f"MiniCPM-o TTS 失败: {e}")
            return None

    def _synthesize_sync(self, text: str, emotion: str) -> bytes | None:
        emotion_instruction = EMOTION_STYLE_MAP.get(emotion, "")
        prompt = f"请用语音朗读以下文字。{emotion_instruction}\n{text}"

        msgs = [{"role": "user", "content": [prompt]}]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            self._model.chat(
                msgs=msgs,
                do_sample=True,
                max_new_tokens=1024,
                use_tts_template=True,
                generate_audio=True,
                output_audio_path=tmp_path,
                temperature=0.1,
            )
            return self._read_audio_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ====================== 端到端语音对话 ======================

    async def chat_with_audio(
        self,
        audio: np.ndarray,
        system_text: str,
        history: list[dict],
        emotion: str = "neutral",
        enable_thinking: bool = False,
        temperature: float = 0.7,
        max_new_tokens: int | None = None,
    ) -> dict:
        """端到端语音对话: 原始音频+context → 文本+语音

        MiniCPM-o 直接理解音频内容，无需预先 ASR 转写。

        Args:
            audio: 用户原始语音 (float32, 16kHz)
            system_text: 人格+记忆+档案文本 (PromptBuilder.build_system_text 输出)
            history: 对话历史 [{"role":"user","content":"..."}, ...]
            emotion: 当前情绪状态
            enable_thinking: 是否启用思考模式 (复杂问题)
            temperature: 采样温度
            max_new_tokens: 最大生成 token 数
        """
        if not self._available:
            return {"content": "", "audio": None, "model": "minicpm-o-4.5"}

        if max_new_tokens is None:
            max_new_tokens = self._config.get("max_new_tokens", 200)

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                partial(
                    self._chat_with_audio_sync,
                    audio,
                    system_text,
                    history,
                    emotion,
                    enable_thinking,
                    temperature,
                    max_new_tokens,
                ),
            )
            return result
        except Exception as e:
            logger.error(f"MiniCPM-o chat_with_audio 失败: {e}")
            return {"content": "", "audio": None, "model": "minicpm-o-4.5"}

    def _chat_with_audio_sync(
        self,
        audio: np.ndarray,
        system_text: str,
        history: list[dict],
        emotion: str,
        enable_thinking: bool,
        temperature: float,
        max_new_tokens: int,
    ) -> dict:
        # 构建 system message: 声音参考 + 人格记忆
        sys_msg = self._model.get_sys_prompt(
            ref_audio=self._ref_audio,
            mode="audio_assistant",
            language="zh",
        )
        # 追加情绪指令和人格/记忆文本
        emotion_instruction = EMOTION_STYLE_MAP.get(emotion, "")
        if emotion_instruction:
            sys_msg["content"].append(emotion_instruction)
        sys_msg["content"].append(system_text)

        # 组装消息: system + 历史对话 + 当前音频
        msgs = [sys_msg]
        for turn in history:
            msgs.append({"role": turn["role"], "content": [turn["content"]]})
        msgs.append({"role": "user", "content": [audio]})

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            text = self._model.chat(
                msgs=msgs,
                do_sample=True,
                max_new_tokens=max_new_tokens,
                use_tts_template=True,
                generate_audio=True,
                output_audio_path=tmp_path,
                enable_thinking=enable_thinking,
                temperature=temperature,
            )

            audio_bytes = self._read_audio_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return {
            "content": text.strip() if isinstance(text, str) else "",
            "audio": audio_bytes,
            "model": "minicpm-o-4.5",
        }

    # ====================== 内部工具 ======================

    def _convert_messages(
        self,
        messages: list[dict],
        audio_segment: np.ndarray | None,
        emotion: str,
    ) -> list[dict]:
        """将 OpenAI 格式消息转换为 MiniCPM-o 原生格式

        MiniCPM-o 的 content 字段是一个列表，混合文本和音频。
        """
        minicpm_msgs = []

        # 构建 system message (含参考语音和情绪指令)
        system_parts = []
        if self._ref_audio is not None:
            system_parts.append(self._ref_audio)

        emotion_instruction = EMOTION_STYLE_MAP.get(emotion, "")
        if emotion_instruction:
            system_parts.append(emotion_instruction)

        # 找到原始 system message
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg["content"])
                break

        if system_parts:
            minicpm_msgs.append({"role": "system", "content": system_parts})

        # 转换 user/assistant 消息
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue

            content = msg.get("content", "")
            minicpm_msgs.append({"role": role, "content": [content]})

        # 将音频注入最后一条 user message
        if audio_segment is not None and minicpm_msgs:
            last_user_idx = None
            for i in range(len(minicpm_msgs) - 1, -1, -1):
                if minicpm_msgs[i]["role"] == "user":
                    last_user_idx = i
                    break

            if last_user_idx is not None:
                minicpm_msgs[last_user_idx]["content"].append(audio_segment)
            else:
                minicpm_msgs.append({"role": "user", "content": [audio_segment]})

        return minicpm_msgs

    @staticmethod
    def _read_audio_file(path: str) -> bytes | None:
        """读取音频文件为 bytes"""
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return p.read_bytes()
        return None
