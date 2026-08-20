import httpx

from app.config import settings


class AIServiceError(RuntimeError):
    """A safe, user-facing error raised when the configured AI service fails."""


async def chat_completion(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    thinking: bool = False,
) -> str:
    if not settings.DEEPSEEK_API_KEY:
        raise AIServiceError("AI 服务尚未配置，请先设置 DEEPSEEK_API_KEY")

    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AIServiceError(
            f"AI 服务请求失败（HTTP {exc.response.status_code}）"
        ) from exc
    except httpx.RequestError as exc:
        raise AIServiceError("暂时无法连接 AI 服务，请稍后重试") from exc

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIServiceError("AI 服务返回了无法识别的数据") from exc
    if not isinstance(content, str) or not content.strip():
        raise AIServiceError("AI 服务未返回有效内容")
    return content
