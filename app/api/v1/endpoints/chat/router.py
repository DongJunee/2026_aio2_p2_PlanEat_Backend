import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.schemas.chat import (
    ChatErrorResponse,
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
)
from app.services.chat_service import chat_service
from app.services.pdf_service import recipe_pdf_service

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "JSON 응답 또는 text/event-stream SSE 응답",
            "content": {
                "text/event-stream": {"schema": {"type": "string"}},
            },
        },
        400: {"model": ChatErrorResponse},
        500: {"model": ChatErrorResponse},
    },
)
async def chat(
    request: ChatRequest,
    response: Response,
    accept: str | None = Header(default=None),
) -> dict[str, object] | StreamingResponse:
    """세션별 LangGraph 워크플로우를 JSON 또는 SSE로 반환합니다.

    기존 클라이언트의 호환성을 위해 기본 응답은 JSON으로 유지한다. 클라이언트가
    ``Accept: text/event-stream``을 보내면 같은 ``/chat`` 경로에서 SSE 이벤트를 사용한다.
    """

    if _accepts_event_stream(accept):
        return StreamingResponse(
            _encode_sse(chat_service.stream(request)),
            status_code=200,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Vary": "Accept",
                "X-Accel-Buffering": "no",
            },
        )

    payload, status_code = await chat_service.handle(request)
    response.status_code = status_code
    response.headers["Vary"] = "Accept"
    return payload


def _accepts_event_stream(accept: str | None) -> bool:
    """Accept 헤더에서 SSE 응답을 명시적으로 요청했는지 확인합니다."""

    if not accept:
        return False
    for media_range in accept.split(","):
        media_type, *parameters = media_range.split(";")
        if media_type.strip().lower() != "text/event-stream":
            continue
        if any(
            parameter.strip().lower() in {"q=0", "q=0.0", "q=0.00"}
            for parameter in parameters
        ):
            return False
        return True
    return False


async def _encode_sse(events: AsyncIterator[ChatStreamEvent]) -> AsyncIterator[str]:
    """검증된 스트림 이벤트를 SSE wire format으로 직렬화합니다."""

    async for event in events:
        yield (
            f"event: {event.event}\n"
            f"data: {json.dumps(event.data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )


@router.get("/pdfs/{filename}", response_class=FileResponse, tags=["chat"])
async def download_recipe_pdf(filename: str) -> FileResponse:
    """선택 식단 PDF를 안전한 파일명으로만 내려줍니다."""

    path = recipe_pdf_service.resolve_download_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="PDF를 찾을 수 없습니다.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
