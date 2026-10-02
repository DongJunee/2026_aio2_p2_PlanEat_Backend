from fastapi import APIRouter, Response

from app.schemas.chat import ChatErrorResponse, ChatRequest, ChatResponse
from app.services.chat_service import chat_service

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={400: {"model": ChatErrorResponse}, 500: {"model": ChatErrorResponse}},
)
async def chat(request: ChatRequest, response: Response) -> dict[str, object]:
    """세션별 LangGraph 워크플로우를 실행하고 다음 대화 단계를 반환합니다."""

    payload, status_code = await chat_service.handle(request)
    response.status_code = status_code
    return payload
