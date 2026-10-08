"""확정 재료와 조건으로 레시피 후보를 조회하고 순위를 계산하는 Tool입니다."""

import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.agent.tools.tool_models import (
    CatalogRecipe,
    RecipeMatch,
    RecipeSearchQuery,
    ToolIngredient,
)


class RecipeRepository(Protocol):
    """식품안전나라·내부 DB 등 Recipe Source의 공통 비동기 경계입니다."""

    async def search(self, query: RecipeSearchQuery, *, limit: int) -> list[CatalogRecipe]:
        """조건에 관련된 정규화 레시피를 반환합니다."""


class RecipeTool:
    """Recipe Source 결과를 보유 재료와 사용자 조건 기준으로 정렬합니다."""

    def __init__(self, repository: RecipeRepository) -> None:
        self._repository = repository

    async def recommend(
        self, query: RecipeSearchQuery, *, limit: int = 10
    ) -> list[RecipeMatch]:
        """후보를 가져와 조리시간·식단 제약·보유 재료 적합도를 반영해 정렬합니다."""

        # Source가 조건 검색을 지원하지 않는 경우도 있어 넉넉히 받은 뒤 서버에서 재정렬한다.
        candidates = await self._repository.search(query, limit=max(limit * 3, limit))
        max_cook_time = _max_cook_time(query.user_conditions)
        normalized_conditions = _condition_text(query.user_conditions)
        owned_names = {_normalize_name(item.name) for item in query.confirmed_ingredients}

        matches: list[RecipeMatch] = []
        for recipe in candidates:
            if max_cook_time is not None and recipe.cook_time > max_cook_time:
                continue
            if _violates_dietary_condition(recipe, normalized_conditions):
                continue

            owned = [
                ingredient
                for ingredient in recipe.ingredients
                if _normalize_name(ingredient.name) in owned_names
            ]
            missing = [
                ingredient
                for ingredient in recipe.ingredients
                if _normalize_name(ingredient.name) not in owned_names
            ]
            matches.append(
                RecipeMatch(
                    recipe=recipe,
                    owned_ingredients=owned,
                    missing_ingredients=missing,
                    score=_score_recipe(recipe, owned_count=len(owned), missing_count=len(missing), conditions=normalized_conditions),
                )
            )

        return sorted(matches, key=lambda item: (-item.score, item.recipe.cook_time, item.recipe.title))[
            :limit
        ]


def query_from_tool_request(
    confirmed_ingredients: Sequence[Mapping[str, object]],
    user_conditions: Mapping[str, object],
) -> RecipeSearchQuery:
    """기존 Orchestrator ``ToolRequest``의 느슨한 Mapping을 Tool Hub 검색 모델로 검증합니다."""

    return RecipeSearchQuery(
        confirmed_ingredients=[ToolIngredient.model_validate(item) for item in confirmed_ingredients],
        user_conditions=user_conditions,
    )


def _normalize_name(name: str) -> str:
    """단순한 표기 차이만 정규화하고 재료 추정을 하지 않습니다."""

    normalized = re.sub(r"\s+", "", name).lower()
    aliases = {"달걀": "계란", "파": "대파", "닭가슴": "닭가슴살"}
    return aliases.get(normalized, normalized)


def _condition_text(conditions: Mapping[str, object] | object) -> str:
    """Orchestrator가 보관한 자연어 조건 원문을 안전하게 읽습니다."""

    if not isinstance(conditions, Mapping):
        return ""
    message = conditions.get("message", "")
    return message.lower() if isinstance(message, str) else ""


def _max_cook_time(conditions: Mapping[str, object] | object) -> int | None:
    """선택적으로 입력된 조리 시간 제한을 분 단위로 반환합니다."""

    if isinstance(conditions, Mapping):
        stored_minutes = conditions.get("cooking_time_minutes")
        if isinstance(stored_minutes, int) and stored_minutes >= 0:
            return stored_minutes

    match = re.search(
        r"(?P<value>\d+)\s*(?P<unit>분|시간)(?:\s*(?:안|이내|내))?",
        _condition_text(conditions),
    )
    if not match:
        return None
    value = int(match.group("value"))
    return value * 60 if match.group("unit") == "시간" else value


def _violates_dietary_condition(recipe: CatalogRecipe, conditions: str) -> bool:
    """명시적인 채식·비건 조건과 육류 재료의 충돌만 보수적으로 제외합니다."""

    if not any(keyword in conditions for keyword in ("채식", "비건")):
        return False
    animal_keywords = ("소고기", "돼지", "닭", "생선", "새우", "멸치", "참치", "계란", "우유", "치즈")
    return any(
        keyword in ingredient.name
        for ingredient in recipe.ingredients
        for keyword in animal_keywords
    )


def _score_recipe(
    recipe: CatalogRecipe, *, owned_count: int, missing_count: int, conditions: str
) -> float:
    """보유 재료 활용을 우선하는 설명 가능한 점수로 후보 순위를 고정합니다."""

    # 보유 재료를 하나라도 쓰는 후보는 미사용 후보보다 항상 먼저 보여야 한다.
    # 이후에 부족 재료와 조리 시간으로 같은 그룹 안에서만 우선순위를 가른다.
    score = (1000 if owned_count else 0) + owned_count * 100 - missing_count * 3 - recipe.cook_time * 0.05
    if "고단백" in conditions and recipe.nutrition is not None:
        score += float(recipe.nutrition.protein) * 0.5
    if any(keyword in conditions for keyword in ("다이어트", "저칼로리")) and recipe.nutrition is not None:
        score -= float(recipe.nutrition.calories) * 0.01
    return score
