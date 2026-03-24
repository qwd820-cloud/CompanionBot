"""LLM 调用客户端 — 全部使用本地 Qwen3.5"""

import logging
import os

logger = logging.getLogger("companion_bot.llm_client")


class LLMClient:
    """
    LLM 推理客户端，所有任务均通过本地 Qwen3.5 via SGLang/vLLM 完成。

    DGX Spark 算力充裕，无需依赖云端 API。
    复杂推理任务 (记忆沉淀、摘要等) 通过提高 max_tokens 和调整 temperature 来适配。
    """

    # 不同任务类型的推理参数
    TASK_PARAMS = {
        "consolidation": {"temperature": 0.3, "max_tokens": 1024},
        "summary": {"temperature": 0.3, "max_tokens": 1024},
        "complex_reasoning": {"temperature": 0.4, "max_tokens": 1024},
    }

    def __init__(
        self,
        local_base_url: str | None = None,
        model_name: str = "qwen3.5",
    ):
        # 优先读环境变量 (Docker 中 companion-bot → llm-engine)
        self.local_base_url = (
            local_base_url
            or os.environ.get("LOCAL_LLM_URL", "http://localhost:8000/v1")
        )
        self.model_name = model_name
        self._client = None
        self._available = False

    async def check_health(self) -> bool:
        """探测 LLM 引擎是否可用"""
        client = self._get_client()
        try:
            models = await client.models.list()
            model_ids = [m.id for m in models.data]
            self._available = True
            logger.info(f"LLM 引擎可用: {self.local_base_url}, 模型: {model_ids}")
            return True
        except Exception as e:
            self._available = False
            logger.warning(f"LLM 引擎不可用 ({self.local_base_url}): {e}")
            return False

    def _get_client(self):
        """懒加载本地 LLM 客户端"""
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                base_url=self.local_base_url,
                api_key="local",
            )
        return self._client

    async def chat(
        self,
        messages: list[dict],
        task_type: str = "daily",
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> dict:
        """
        所有任务均通过本地 Qwen3.5 推理。
        复杂任务自动使用更保守的参数 (低 temperature, 高 max_tokens)。
        返回: {"content": str, "model": str, "usage": dict}
        """
        # 复杂任务覆盖默认参数
        if task_type in self.TASK_PARAMS:
            params = self.TASK_PARAMS[task_type]
            temperature = params["temperature"]
            max_tokens = params["max_tokens"]

        client = self._get_client()
        try:
            response = await client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "model": f"{self.model_name}-local",
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                },
            }
        except Exception as e:
            logger.error(f"本地 LLM 调用失败: {e}")
            return {"content": "抱歉，我现在有点反应不过来，等我缓缓。", "model": "fallback", "usage": {}}
