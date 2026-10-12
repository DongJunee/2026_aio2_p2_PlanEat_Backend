"""애플리케이션에서 사용할 Tool Hub 조립 지점입니다."""

from __future__ import annotations

import logging

from app.agent.tools.recipe_guide_tool import (
    InMemoryRecipeGuideRetriever,
    RecipeGuideTool,
)
from app.agent.tools.recipe_tool import RecipeTool
from app.agent.tools.tool_hub import PlanEatToolHub
from app.core.config import Settings, get_settings
from app.integrations.recipe_source.csv_catalog import CsvRecipeRepository, internal_catalog_path

logger = logging.getLogger(__name__)


def build_default_tool_hub(settings: Settings | None = None) -> PlanEatToolHub:
    """로컬 레시피 카탈로그와 선택적 Chroma RAG로 기본 Tool Hub를 조립합니다.

    Chroma 경로가 설정되지 않은 개발 환경에서는 빈 retriever를 사용한다. RAG DB의
    초기화 실패가 Recipe·Nutrition·Shopping Tool까지 막지 않도록, 설정된 경우에도
    연결 실패는 안전하게 빈 retriever로 fallback한다.
    """

    resolved_settings = settings or get_settings()
    guide_tool = RecipeGuideTool(InMemoryRecipeGuideRetriever())

    if resolved_settings.chroma_persist_directory:
        try:
            from app.integrations.vector_store.chroma_recipe_guide import (
                ChromaRecipeGuideRetriever,
            )

            retriever = ChromaRecipeGuideRetriever.open(
                persist_directory=resolved_settings.chroma_persist_directory,
                collection_name=resolved_settings.chroma_collection_name,
            )
            guide_tool = RecipeGuideTool(retriever)
        except (OSError, RuntimeError, ValueError):
            # RAG는 보조 정보이므로 기본 레시피 추천을 실패시키지 않는다.
            logger.warning(
                "Chroma Recipe Guide를 초기화하지 못해 빈 RAG retriever를 사용합니다."
            )

    catalog = CsvRecipeRepository(
        resolved_settings.tool_hub_catalog_path or internal_catalog_path()
    )
    recipe_tool = RecipeTool(catalog)
    if resolved_settings.chroma_persist_directory:
        try:
            from app.integrations.vector_store.chroma_recipe_catalog import (
                ChromaRecipeRepository,
            )

            recipe_tool = RecipeTool(
                ChromaRecipeRepository.open(
                    persist_directory=resolved_settings.chroma_persist_directory,
                    collection_name=resolved_settings.chroma_recipe_collection_name,
                    catalog=catalog,
                )
            )
        except (OSError, RuntimeError, ValueError):
            logger.warning("Chroma Recipe Catalog를 초기화하지 못해 CSV 검색을 사용합니다.")

    return PlanEatToolHub.from_local_catalog(
        recipe_guide_tool=guide_tool,
        catalog_path=resolved_settings.tool_hub_catalog_path or None,
        recipe_tool=recipe_tool,
    )
