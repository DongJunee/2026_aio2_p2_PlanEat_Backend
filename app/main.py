from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.chat.router import router as chat_router
from app.schemas.chat import ChatErrorResponse

app = FastAPI(title="PlanEat API", version="0.1.0")
app.include_router(chat_router)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """요청 DTO 검증 실패도 FE가 약속된 ERROR 응답으로 처리하게 변환합니다."""

    # 검증 세부 정보에는 원본 입력 일부가 포함될 수 있으므로 외부로 노출하지 않는다.
    error_response = ChatErrorResponse(
        status="ERROR", response="요청 형식이 올바르지 않습니다."
    )
    return JSONResponse(status_code=400, content=error_response.model_dump())


def custom_openapi() -> dict[str, object]:
    """실제 검증 오류 처리와 일치하도록 자동 422 응답 스키마를 제거합니다."""

    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
