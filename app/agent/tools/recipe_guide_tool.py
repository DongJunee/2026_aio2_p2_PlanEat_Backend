"""재료 활용법·대체재·보관법을 검색하는 Recipe Guide RAG Tool입니다."""

import asyncio
from collections.abc import Iterable
from typing import Protocol

from app.agent.tools.be2_models import IngredientGuide


class RecipeGuideRetriever(Protocol):
    """Vector Store에 의존하지 않는 Recipe Guide 검색 경계입니다."""

    async def retrieve(self, ingredient: str) -> IngredientGuide | None:
        """재료 하나에 가장 적합한 가이드 문서를 반환합니다."""


class RecipeGuideTool:
    """여러 부족 재료의 가이드를 병렬 조회해 Shopping Tool에 전달합니다."""

    def __init__(self, retriever: RecipeGuideRetriever) -> None:
        self._retriever = retriever

    async def find_guides(self, ingredients: Iterable[str]) -> dict[str, IngredientGuide]:
        """검색 실패 또는 미등록 재료는 전체 추천 실패로 만들지 않고 생략합니다."""

        unique_names = list(dict.fromkeys(name for name in ingredients if name.strip()))
        results = await asyncio.gather(
            *(self._retriever.retrieve(name) for name in unique_names), return_exceptions=True
        )
        return {
            name: guide
            for name, guide in zip(unique_names, results, strict=True)
            if isinstance(guide, IngredientGuide)
        }


class InMemoryRecipeGuideRetriever:
    """개발·단위 테스트에서 ChromaDB 없이 사용하는 가이드 Retriever입니다."""

    def __init__(self, guides: Iterable[IngredientGuide] = ()) -> None:
        self._guides = {guide.ingredient: guide for guide in guides}

    async def retrieve(self, ingredient: str) -> IngredientGuide | None:
        return self._guides.get(ingredient)
