"""CSV 레시피 원본을 Chroma 검색 인덱스로 보완하는 어댑터입니다."""

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.agent.tools.recipe_tool import RecipeRepository
from app.agent.tools.tool_models import CatalogRecipe, RecipeSearchQuery
from app.integrations.recipe_source.csv_catalog import CsvRecipeRepository

_INDEX_BATCH_SIZE = 64


class ChromaRecipeRepository:
    """의미 검색 후보와 CSV 정확 일치 후보를 합치는 ``RecipeRepository``입니다.

    Chroma에는 검색에 필요한 문서와 ``recipe_id``만 저장한다. 레시피 상세는 항상
    검증된 CSV에서 읽으므로, 벡터 DB 메타데이터가 응답 DTO의 원본이 되지 않는다.
    """

    def __init__(self, collection: Any, catalog: CsvRecipeRepository) -> None:
        self._collection = collection
        self._catalog = catalog

    @classmethod
    def open(
        cls,
        *,
        persist_directory: str | Path,
        catalog: CsvRecipeRepository,
        collection_name: str = "recipe_catalog",
    ) -> "ChromaRecipeRepository":
        """지정 경로의 레시피 검색 컬렉션을 엽니다."""

        try:
            import chromadb
        except ImportError as error:  # pragma: no cover - 의존성 설치 환경에서만 발생
            raise RuntimeError("ChromaDB 의존성이 설치되어 있지 않습니다.") from error
        client = chromadb.PersistentClient(path=str(persist_directory))
        return cls(client.get_or_create_collection(name=collection_name), catalog)

    async def upsert_catalog(self) -> int:
        """현재 CSV 전체를 검색 문서로 적재하고 적재한 레시피 수를 반환합니다."""

        recipes = await self._catalog.list_recipes()
        if not recipes:
            return 0
        # 기본 임베딩 함수는 문서 전체를 한 번에 처리한다. 운영 카탈로그를 단일 upsert로
        # 넘기면 메모리 사용량이 급증해 프로세스가 종료될 수 있으므로 작은 배치로 적재한다.
        for offset in range(0, len(recipes), _INDEX_BATCH_SIZE):
            batch = recipes[offset : offset + _INDEX_BATCH_SIZE]
            await asyncio.to_thread(
                self._collection.upsert,
                ids=[recipe.recipe_id for recipe in batch],
                documents=[_recipe_document(recipe) for recipe in batch],
                metadatas=[
                    {"recipe_id": recipe.recipe_id, "source": recipe.source}
                    for recipe in batch
                ],
            )
        return len(recipes)

    async def search(self, query: RecipeSearchQuery, *, limit: int) -> list[CatalogRecipe]:
        """의미적으로 가까운 후보 뒤에 CSV 정확 일치 후보를 보충합니다."""

        if limit < 1:
            return []
        recipes = await self._catalog.list_recipes()
        by_id = {recipe.recipe_id: recipe for recipe in recipes}
        result = await asyncio.to_thread(
            self._collection.query,
            query_texts=[_query_text(query)],
            n_results=limit,
            include=["metadatas"],
        )
        semantic = [
            by_id[recipe_id]
            for recipe_id in _recipe_ids(result)
            if recipe_id in by_id
        ]

        # 빈 인덱스·낮은 검색 품질에서도 확정 재료의 정확 일치 후보는 보장한다.
        exact = await self._catalog.search(query, limit=limit)
        return _unique_recipes([*exact, *semantic])[:limit]


def _recipe_document(recipe: CatalogRecipe) -> str:
    """검색 품질에 필요한 제목·재료·분류만 하나의 안전한 문서로 구성합니다."""

    ingredient_names = ", ".join(ingredient.name for ingredient in recipe.ingredients)
    role = recipe.source_metadata.get("meal_role", "")
    return f"레시피: {recipe.title}\n재료: {ingredient_names}\n식사 분류: {role}"


def _query_text(query: RecipeSearchQuery) -> str:
    ingredients = ", ".join(ingredient.name for ingredient in query.confirmed_ingredients)
    condition = query.user_conditions.get("message", "")
    condition_text = condition if isinstance(condition, str) else ""
    return f"보유 재료: {ingredients}\n요청 조건: {condition_text}"


def _recipe_ids(result: object) -> list[str]:
    """Chroma의 중첩 결과에서 검증 가능한 recipe ID만 읽습니다."""

    if not isinstance(result, Mapping):
        return []
    ids = result.get("ids")
    if not isinstance(ids, list) or not ids or not isinstance(ids[0], list):
        return []
    return [recipe_id for recipe_id in ids[0] if isinstance(recipe_id, str)]


def _unique_recipes(recipes: Sequence[CatalogRecipe]) -> list[CatalogRecipe]:
    seen: set[str] = set()
    unique: list[CatalogRecipe] = []
    for recipe in recipes:
        if recipe.recipe_id not in seen:
            seen.add(recipe.recipe_id)
            unique.append(recipe)
    return unique


async def index_csv_recipe_catalog(
    *,
    persist_directory: str | Path,
    collection_name: str = "recipe_catalog",
    catalog_path: str | Path | None = None,
) -> int:
    """CSV 원본으로 Chroma 레시피 컬렉션을 생성하거나 최신 상태로 갱신합니다."""

    catalog = (
        CsvRecipeRepository(catalog_path)
        if catalog_path is not None
        else CsvRecipeRepository.from_internal_catalog()
    )
    repository = ChromaRecipeRepository.open(
        persist_directory=persist_directory,
        collection_name=collection_name,
        catalog=catalog,
    )
    return await repository.upsert_catalog()
