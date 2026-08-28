from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.llm import LLMService, get_llm_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    llm: LLMService = Depends(get_llm_service),
) -> ChatResponse:
    return llm.chat(request.messages)
