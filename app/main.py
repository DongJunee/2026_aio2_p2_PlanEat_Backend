import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.chat.router import router as chat_router
from app.core.config import get_settings
from app.integrations.vector_store.chroma_recipe_catalog import index_csv_recipe_catalog
from app.schemas.chat import ChatErrorResponse

logger = logging.getLogger(__name__)


async def _index_recipe_catalog(
    *, persist_directory: str, collection_name: str, catalog_path: str | None
) -> None:
    """시작 이후 실행되는 선택적 Chroma 인덱스 작업의 실패를 기록합니다."""

    try:
        count = await index_csv_recipe_catalog(
            persist_directory=persist_directory,
            collection_name=collection_name,
            catalog_path=catalog_path,
        )
        logger.info("Chroma 레시피 인덱스 %d건을 준비했습니다.", count)
    except (OSError, RuntimeError, ValueError):
        # RAG는 보조 기능이다. 색인 실패가 CSV 기반 추천이나 이미 시작한 API를 멈추게 하면 안 된다.
        logger.exception("Chroma 레시피 인덱스를 준비하지 못해 CSV 검색을 사용합니다.")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """서버 기동을 막지 않고, 명시적으로 요청된 Chroma 색인을 시작합니다."""

    settings = get_settings()
    if settings.chroma_persist_directory and settings.chroma_auto_index_on_startup:
        # CSV 전체 임베딩은 수 분이 걸릴 수 있으므로 health endpoint의 가용성과 분리한다.
        asyncio.create_task(
            _index_recipe_catalog(
                persist_directory=settings.chroma_persist_directory,
                collection_name=settings.chroma_recipe_collection_name,
                catalog_path=settings.tool_hub_catalog_path,
            )
        )
    yield


app = FastAPI(title="PlanEat API", version="0.1.0", lifespan=lifespan)
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
