"""LLM 调用客户端 — 本地 Qwen3.5 + 云端 Kimi K2.5"""

import logging
import os

logger = logging.getLogger("companion_bot.llm_client")

# 任务类型 → 路由
LOCAL_TASKS = {"daily", "greeting", "chitchat"}
CLOUD_TASKS = {"consolidation", "summary", "complex_reasoning"}


class LLMClient:
    """
    LLM 推理客户端，自动路由到本地或云端模型。

    - 日常对话: 本地 Qwen3.5 via SGLang/vLLM (OpenAI 兼容接口)
    - 复杂推理/记忆沉淀: 云端 Kimi K2.5 API
    """

    def __init__(
        self,
        local_base_url: str = "http://localhost:8000/v1",
        cloud_base_url: str = "https://api.moonshot.ai/v1",
        kimi_api_key: str | None = None,
    ):
        self.local_base_url = local_base_url
        self.cloud_base_url = cloud_base_url
        self.kimi_api_key = kimi_api_key or os.environ.get("KIMI_API_KEY", "")

        self._local_client = None
        self._cloud_client = None

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
        """懒加载云端 LLM 客户端"""
        if self._cloud_client is None:
            from openai import AsyncOpenAI
            self._cloud_client = AsyncOpenAI(
                base_url=self.cloud_base_url,
                api_key=self.kimi_api_key,
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
        根据任务类型自动路由到本地或云端 LLM。
        返回: {"content": str, "model": str, "usage": dict}
        """
        if task_type in CLOUD_TASKS and self.kimi_api_key:
            return await self._cloud_inference(
                messages, temperature, max_tokens, thinking=(task_type == "consolidation")
            )
        return await self._local_inference(messages, temperature, max_tokens)

    async def _local_inference(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> dict:
        """本地 Qwen3.5 推理"""
        client = self._get_local_client()
        try:
            response = await client.chat.completions.create(
                model="qwen3.5",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "model": "qwen3.5-local",
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                },
            }
        except Exception as e:
            logger.error(f"本地 LLM 调用失败: {e}")
            # 尝试 fallback 到云端
            if self.kimi_api_key:
                logger.info("Fallback 到 Kimi K2.5 云端")
                return await self._cloud_inference(
                    messages, temperature, max_tokens
                )
            return {"content": "抱歉，我现在有点反应不过来，等我缓缓。", "model": "fallback", "usage": {}}

    async def _cloud_inference(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        thinking: bool = False,
    ) -> dict:
        """云端 Kimi K2.5 推理"""
        client = self._get_cloud_client()
        try:
            model = "kimi-k2.5-thinking" if thinking else "kimi-k2.5"
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "model": f"kimi-k2.5-{'thinking' if thinking else 'instant'}",
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                },
            }
        except Exception as e:
            logger.error(f"云端 LLM 调用失败: {e}")
            return {"content": "抱歉，我现在有点反应不过来，等我缓缓。", "model": "fallback", "usage": {}}
