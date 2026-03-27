"""LLM 调用客户端 — 本地 Qwen3.5 + 云端 Kimi Code 双引擎"""

import asyncio
import logging
import os

logger = logging.getLogger("companion_bot.llm_client")


class LLMClient:
    """
    LLM 推理客户端，支持本地和云端双引擎:

    - 本地: Qwen3.5 via Unsloth/llama-server (端口 57847)
    - 云端: Kimi Code API (OpenAI 兼容, https://api.kimi.com/coding/v1)

    路由策略:
    - 日常对话优先走本地，本地失败自动 fallback 云端
    - 记忆沉淀/复杂推理可配置优先走云端 (Kimi 推理能力更强)
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
    ):
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
        """探测本地 LLM"""
        try:
            client = self._get_local_client()
            models = await client.models.list()
            model_ids = [m.id for m in models.data]
            self._local_available = True
            logger.info(f"本地 LLM 可用: {self.local_base_url}, 模型: {model_ids}")
            return True
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
            from openai import AsyncOpenAI

            self._local_client = AsyncOpenAI(
                base_url=self.local_base_url,
                api_key="local",
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
        max_tokens: int = 512,
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

        # 路由: 优先引擎 → 备选引擎
        if prefer == "cloud" and self._cloud_available:
            result = await self._call_cloud(messages, temperature, max_tokens)
            if result:
                return result
            # 云端失败，fallback 本地
            return await self._call_local(messages, temperature, max_tokens)
        else:
            result = await self._call_local(messages, temperature, max_tokens)
            if result:
                return result
            # 本地失败，fallback 云端
            if self._cloud_available:
                return await self._call_cloud(messages, temperature, max_tokens)
            return self._fallback_response()

    async def _call_local(
        self, messages: list[dict], temperature: float, max_tokens: int
    ) -> dict | None:
        """调用本地 LLM"""
        if not self._local_available:
            return None
        client = self._get_local_client()
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.local_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=10,
            )
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
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
            logger.warning("本地 LLM 调用超时 (10s)，标记为不可用")
            self._local_available = False
            return None
        except Exception as e:
            logger.error(f"本地 LLM 调用失败: {e}")
            self._local_available = False  # 标记不可用，后续直接走云端
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
            return {
                "content": choice.message.content or "",
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
