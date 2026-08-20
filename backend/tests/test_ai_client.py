from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.ai_client import AIServiceError, chat_completion


@pytest.mark.asyncio
async def test_chat_completion_requires_api_key():
    with patch.object(settings, "DEEPSEEK_API_KEY", ""):
        with pytest.raises(AIServiceError, match="DEEPSEEK_API_KEY"):
            await chat_completion(
                [{"role": "user", "content": "hello"}],
                model="deepseek-v4-flash",
                max_tokens=100,
                temperature=0.2,
            )


@pytest.mark.asyncio
async def test_chat_completion_uses_v4_payload_and_official_endpoint():
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}]
    }
    client = AsyncMock()
    client.post.return_value = response

    with patch.object(settings, "DEEPSEEK_API_KEY", "test-key"), \
         patch.object(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"), \
         patch("app.services.ai_client.httpx.AsyncClient") as client_class:
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        result = await chat_completion(
            [{"role": "user", "content": "hello"}],
            model="deepseek-v4-pro",
            max_tokens=100,
            temperature=0.2,
            json_mode=True,
            thinking=True,
        )

    assert result == "ok"
    call = client.post.call_args
    assert call.args[0] == "https://api.deepseek.com/chat/completions"
    assert call.kwargs["json"]["model"] == "deepseek-v4-pro"
    assert call.kwargs["json"]["response_format"] == {"type": "json_object"}
    assert call.kwargs["json"]["thinking"] == {"type": "enabled"}
