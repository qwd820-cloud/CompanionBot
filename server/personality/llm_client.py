"""LLM 调用客户端 — MiniCPM-o / 本地 Qwen3.5 / 云端 Kimi Code 三引擎"""

import asyncio
import logging
import os

import httpx

# 在任何网络库 import 之前强制清除代理
for _pv in [
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
]:
    os.environ.pop(_pv, None)

logger = logging.getLogger("companion_bot.llm_client")


class LLMClient:
    """
    LLM 推理客户端，支持三引擎:

    - MiniCPM-o 4.5: 全模态本地引擎 (优先，文本模式)
    - 本地: Qwen3.5 via Unsloth/llama-server (端口 57847，备选)
    - 云端: Kimi Code API (OpenAI 兼容, 复杂推理/记忆沉淀)

    路由策略:
    - 日常对话: MiniCPM-o → Qwen3.5 本地 → 云端
    - 记忆沉淀/复杂推理: 云端优先 → MiniCPM-o → Qwen3.5 本地
    """

    # 不同任务类型的推理参数
    TASK_PARAMS = {
        "consolidation": {"temperature": 0.3, "max_tokens": 1024, "prefer": "cloud"},
        "summary": {"temperature": 0.3, "max_tokens": 1024, "prefer": "cloud"},
        "complex_reasoning": {
            "temperature": 0.4,
            "max_tokens": 1024,
            "prefer": "cloud",
        },
    }

    def __init__(
        self,
        local_base_url: str | None = None,
        local_model: str = "Qwen3.5-4B-UD-Q4_K_XL.gguf",
        cloud_base_url: str | None = None,
        cloud_api_key: str | None = None,
        cloud_model: str = "kimi-for-coding",
        minicpm_engine=None,
    ):
        # MiniCPM-o 全模态引擎 (优先本地引擎)
        self._minicpm_engine = minicpm_engine

        # 本地 LLM: Unsloth llama-server
        self.local_base_url = local_base_url or os.environ.get(
            "LOCAL_LLM_URL", "http://localhost:57847/v1"
        )
        self.local_model = local_model

        # 云端 LLM: Kimi Code API
        self.cloud_base_url = cloud_base_url or os.environ.get(
            "KIMI_BASE_URL", "https://api.kimi.com/coding/v1"
        )
        self.cloud_api_key = cloud_api_key or os.environ.get("KIMI_API_KEY", "")
        self.cloud_model = cloud_model

        self._local_client = None
        self._cloud_client = None
        self._local_available = False
        self._cloud_available = False

    async def check_health(self) -> bool:
        """探测 LLM 引擎是否可用"""
        local_ok = await self._check_local()
        cloud_ok = await self._check_cloud()
        return local_ok or cloud_ok

    async def _check_local(self) -> bool:
        """探测本地 LLM — 用原生 httpx 绕过 openai 代理问题"""
        try:
            async with httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(), timeout=5
            ) as client:
                resp = await client.get(f"{self.local_base_url}/models")
                if resp.status_code == 200:
                    self._local_available = True
                    logger.info(f"本地 LLM 可用: {self.local_base_url}")
                    return True
            self._local_available = False
            return False
        except Exception as e:
            self._local_available = False
            logger.warning(f"本地 LLM 不可用 ({self.local_base_url}): {e}")
            return False

    async def _check_cloud(self) -> bool:
        """探测云端 Kimi Code API"""
        if not self.cloud_api_key:
            logger.info("云端 Kimi Code API 未配置 (无 API key)")
            return False
        try:
            client = self._get_cloud_client()
            await client.models.list()
            self._cloud_available = True
            logger.info(f"云端 Kimi Code API 可用: {self.cloud_base_url}")
            return True
        except Exception as e:
            self._cloud_available = False
            logger.warning(f"云端 Kimi Code API 不可用: {e}")
            return False

    def _get_local_client(self):
        """懒加载本地 LLM 客户端"""
        if self._local_client is None:
            import httpx
            from openai import AsyncOpenAI

            # 创建无代理的 httpx 客户端，防止 ALL_PROXY 干扰本地连接
            self._local_client = AsyncOpenAI(
                base_url=self.local_base_url,
                api_key="local",
                http_client=httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(),
                ),
            )
        return self._local_client

    def _get_cloud_client(self):
        """懒加载云端 Kimi Code 客户端"""
        if self._cloud_client is None:
            from openai import AsyncOpenAI

            self._cloud_client = AsyncOpenAI(
                base_url=self.cloud_base_url,
                api_key=self.cloud_api_key,
                default_headers={
                    "User-Agent": "claude-code/1.0",
                    "X-Client-Name": "claude-code",
                },
            )
        return self._cloud_client

    async def chat(
        self,
        messages: list[dict],
        task_type: str = "daily",
        temperature: float = 0.7,
        max_tokens: int = 100,
    ) -> dict:
        """
        根据任务类型自动路由到本地或云端。
        返回: {"content": str, "model": str, "usage": dict}
        """
        prefer = "local"
        if task_type in self.TASK_PARAMS:
            params = self.TASK_PARAMS[task_type]
            temperature = params["temperature"]
            max_tokens = params["max_tokens"]
            prefer = params.get("prefer", "local")

        # 路由: 优先引擎 → 备选引擎 → 兜底
        if prefer == "cloud" and self._cloud_available:
            result = await self._call_cloud(messages, temperature, max_tokens)
            if result:
                return result
            result = await self._call_local(messages, temperature, max_tokens)
            if result:
                return result
        else:
            result = await self._call_local(messages, temperature, max_tokens)
            if result:
                return result
            if self._cloud_available:
                result = await self._call_cloud(messages, temperature, max_tokens)
                if result:
                    return result
        return self._fallback_response()

    async def _call_minicpm(
        self, messages: list[dict], temperature: float, max_tokens: int
    ) -> dict | None:
        """调用 MiniCPM-o 4.5 文本模式 (无音频输入)"""
        if not self._minicpm_engine or not self._minicpm_engine.available:
            return None
        try:
            result = await self._minicpm_engine.chat_and_speak(
                audio_segment=None,
                messages=messages,
                temperature=temperature,
                max_new_tokens=max_tokens,
            )
            content = (result or {}).get("content", "")
            if content:
                return {
                    "content": content,
                    "model": "minicpm-o-4.5-local",
                    "usage": {},
                }
            return None
        except Exception as e:
            logger.error(f"MiniCPM-o 文本调用失败: {e}")
            return None

    async def _call_local(
        self, messages: list[dict], temperature: float, max_tokens: int
    ) -> dict | None:
        """调用本地 LLM — MiniCPM-o 优先，回退 llama-server"""
        # 优先尝试 MiniCPM-o
        result = await self._call_minicpm(messages, temperature, max_tokens)
        if result:
            return result

        if not self._local_available:
            # 每 60 秒重试一次，允许恢复
            import time

            last_fail = getattr(self, "_local_last_fail", 0)
            if time.time() - last_fail < 60:
                return None
            logger.info("本地 LLM 重试连接...")
        client = self._get_local_client()
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.local_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                ),
                timeout=30,
            )
            # 调用成功，恢复可用标记
            if not self._local_available:
                logger.info("本地 LLM 恢复连接")
                self._local_available = True

            choice = response.choices[0]
            # Qwen3.5 thinking mode: content may be empty, actual reply in reasoning_content
            reply_content = choice.message.content or ""
            if (
                not reply_content
                and hasattr(choice.message, "reasoning_content")
                and choice.message.reasoning_content
            ):
                # 从 reasoning_content 提取最后的实际回复
                rc = choice.message.reasoning_content.strip()
                # reasoning_content 末尾通常是实际回复
                reply_content = rc.split(chr(10))[-1].strip() if rc else ""
                print(f"[LLM] 从reasoning_content提取: {reply_content[:50]}")
            if not reply_content:
                print(
                    f"[LLM] content为空, reasoning_content={getattr(choice.message, 'reasoning_content', 'N/A')[:100] if hasattr(choice.message, 'reasoning_content') else 'N/A'}"
                )
            return {
                "content": reply_content,
                "model": f"{self.local_model}-local",
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens
                    if response.usage
                    else 0,
                    "completion_tokens": response.usage.completion_tokens
                    if response.usage
                    else 0,
                },
            }
        except TimeoutError:
            logger.warning("本地 LLM 调用超时，标记为暂不可用")
            self._local_available = False
            self._local_last_fail = __import__("time").time()
            return None
        except Exception as e:
            logger.error(f"本地 LLM 调用失败: {e}")
            self._local_available = False
            self._local_last_fail = __import__("time").time()
            return None

    async def _call_cloud(
        self, messages: list[dict], temperature: float, max_tokens: int
    ) -> dict | None:
        """调用云端 Kimi Code API"""
        if not self._cloud_available or not self.cloud_api_key:
            return None
        client = self._get_cloud_client()
        try:
            response = await client.chat.completions.create(
                model=self.cloud_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
            choice = response.choices[0]
            # Qwen3.5 thinking mode: content may be empty, actual reply in reasoning_content
            reply_content = choice.message.content or ""
            if (
                not reply_content
                and hasattr(choice.message, "reasoning_content")
                and choice.message.reasoning_content
            ):
                # 从 reasoning_content 提取最后的实际回复
                rc = choice.message.reasoning_content.strip()
                # reasoning_content 末尾通常是实际回复
                reply_content = rc.split(chr(10))[-1].strip() if rc else ""
                print(f"[LLM] 从reasoning_content提取: {reply_content[:50]}")
            if not reply_content:
                print(
                    f"[LLM] content为空, reasoning_content={getattr(choice.message, 'reasoning_content', 'N/A')[:100] if hasattr(choice.message, 'reasoning_content') else 'N/A'}"
                )
            return {
                "content": reply_content,
                "model": f"{self.cloud_model}-cloud",
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens
                    if response.usage
                    else 0,
                    "completion_tokens": response.usage.completion_tokens
                    if response.usage
                    else 0,
                },
            }
        except Exception as e:
            logger.error(f"云端 Kimi Code 调用失败: {e}")
            return None

    @staticmethod
    def _fallback_response() -> dict:
        """双引擎都失败时的兜底回复"""
        return {
            "content": "抱歉，我现在有点反应不过来，等我缓缓。",
            "model": "fallback",
            "usage": {},
        }
