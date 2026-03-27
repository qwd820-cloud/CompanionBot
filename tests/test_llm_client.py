"""LLM 客户端测试 — 双引擎路由、fallback、参数覆盖"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.personality.llm_client import LLMClient


class TestLLMClientRouting:
    """测试本地/云端双引擎路由策略"""

    def _make_client(self, local_available=True, cloud_available=True):
        client = LLMClient(
            local_base_url="http://localhost:57847/v1",
            cloud_api_key="sk-test-key" if cloud_available else "",
        )
        client._local_available = local_available
        client._cloud_available = cloud_available
        return client

    @pytest.mark.asyncio
    async def test_daily_chat_uses_local(self):
        """日常对话优先走本地"""
        client = self._make_client()
        mock_response = self._mock_completion("你好！", "local-model")

        with patch.object(client, "_get_local_client") as mock_get:
            mock_get.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            result = await client.chat(
                [{"role": "user", "content": "你好"}], task_type="daily"
            )

        assert result["content"] == "你好！"
        assert "local" in result["model"]

    @pytest.mark.asyncio
    async def test_consolidation_prefers_cloud(self):
        """记忆沉淀任务优先走云端"""
        client = self._make_client()
        mock_response = self._mock_completion('{"summary": "测试"}', "cloud-model")

        with patch.object(client, "_get_cloud_client") as mock_get:
            mock_get.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            result = await client.chat(
                [{"role": "user", "content": "总结对话"}],
                task_type="consolidation",
            )

        assert result["content"] == '{"summary": "测试"}'
        assert "cloud" in result["model"]

    @pytest.mark.asyncio
    async def test_local_failure_fallback_to_cloud(self):
        """本地失败自动 fallback 到云端"""
        client = self._make_client()
        mock_cloud_resp = self._mock_completion("云端回复", "cloud-model")

        with (
            patch.object(
                client, "_call_local", new_callable=AsyncMock, return_value=None
            ),
            patch.object(client, "_get_cloud_client") as mock_get,
        ):
            mock_get.return_value.chat.completions.create = AsyncMock(
                return_value=mock_cloud_resp
            )
            result = await client.chat(
                [{"role": "user", "content": "你好"}], task_type="daily"
            )

        assert result["content"] == "云端回复"
        assert "cloud" in result["model"]

    @pytest.mark.asyncio
    async def test_both_unavailable_returns_fallback(self):
        """双引擎都不可用时返回兜底回复"""
        client = self._make_client(local_available=False, cloud_available=False)
        result = await client.chat(
            [{"role": "user", "content": "你好"}], task_type="daily"
        )
        assert result["model"] == "fallback"
        assert "反应不过来" in result["content"]

    @pytest.mark.asyncio
    async def test_task_params_override(self):
        """复杂任务自动覆盖 temperature 和 max_tokens"""
        client = self._make_client(cloud_available=False)
        mock_response = self._mock_completion("摘要", "local-model")

        call_args = {}

        async def capture_create(**kwargs):
            call_args.update(kwargs)
            return mock_response

        with patch.object(client, "_get_local_client") as mock_get:
            mock_get.return_value.chat.completions.create = capture_create
            await client.chat(
                [{"role": "user", "content": "总结"}], task_type="summary"
            )

        assert call_args["temperature"] == 0.3
        assert call_args["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_cloud_only_mode(self):
        """只有云端可用时，日常对话也走云端"""
        client = self._make_client(local_available=False, cloud_available=True)
        mock_response = self._mock_completion("云端日常", "cloud-model")

        with patch.object(client, "_get_cloud_client") as mock_get:
            mock_get.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            result = await client.chat(
                [{"role": "user", "content": "你好"}], task_type="daily"
            )

        assert result["content"] == "云端日常"

    @staticmethod
    def _mock_completion(content, model):
        """构造模拟的 OpenAI completion 响应"""
        choice = MagicMock()
        choice.message.content = content
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 20
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = usage
        resp.model = model
        return resp
