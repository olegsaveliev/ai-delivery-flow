from anthropic import Anthropic

from app.core.config import get_settings
from app.schemas.chat import ChatMessage, ChatResponse


class LLMService:
    """Thin wrapper around the Anthropic Messages API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def chat(self, messages: list[ChatMessage], max_tokens: int = 1024) -> ChatResponse:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return ChatResponse(content=text, model=self._model)


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Lazy singleton so the client is only built when first needed."""
    global _service
    if _service is None:
        _service = LLMService()
    return _service
