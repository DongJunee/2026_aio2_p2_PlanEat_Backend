from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from app.schemas.chat import ChatErrorResponse, ChatRequest, ChatResponse
from app.services.chat_service import chat_service
from app.services.pdf_service import recipe_pdf_service

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
    responses={400: {"model": ChatErrorResponse}, 500: {"model": ChatErrorResponse}},
)
async def chat(request: ChatRequest, response: Response) -> dict[str, object]:
    """세션별 LangGraph 워크플로우를 실행하고 다음 대화 단계를 반환합니다."""

    payload, status_code = await chat_service.handle(request)
    response.status_code = status_code
    return payload


@router.get("/pdfs/{filename}", response_class=FileResponse, tags=["chat"])
async def download_recipe_pdf(filename: str) -> FileResponse:
    """선택 식단 PDF를 안전한 파일명으로만 내려줍니다."""

    path = recipe_pdf_service.resolve_download_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="PDF를 찾을 수 없습니다.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
