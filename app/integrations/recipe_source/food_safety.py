"""식품안전나라 조리식품 레시피 API를 BE2 내부 모델로 정규화하는 어댑터입니다.

API 키와 HTTP client는 생성자에서 주입한다. 따라서 이 모듈은 설정 파일이나 BE1의
애플리케이션 조립 코드를 수정하지 않으며, 테스트에서 외부 네트워크를 사용하지 않는다.
"""

import re
from collections.abc import Mapping

import httpx

from app.agent.tools.be2_models import CatalogRecipe, NutritionValues, RecipeIngredient, RecipeSearchQuery


class RecipeSourceError(RuntimeError):
    """Recipe Source의 네트워크·응답 형식 오류를 호출자에게 전달합니다."""


class FoodSafetyRecipeClient:
    """식품안전나라 ``COOKRCP01`` 응답을 ``CatalogRecipe``로 변환합니다."""

    def __init__(
        self,
        *,
        service_key: str,
        base_url: str = "https://openapi.foodsafetykorea.go.kr/api",
        timeout_seconds: float = 5.0,
    ) -> None:
        if not service_key.strip():
            raise ValueError("식품안전나라 API 키가 필요합니다.")
        self._service_key = service_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def search(self, query: RecipeSearchQuery, *, limit: int) -> list[CatalogRecipe]:
        """공개 레시피 목록을 가져온 뒤 사용자 재료와 제목을 기준으로 1차 필터링합니다."""

        url = f"{self._base_url}/{self._service_key}/COOKRCP01/json/1/{limit}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RecipeSourceError("식품안전나라 레시피를 불러오지 못했습니다.") from error

        rows = _extract_rows(payload)
        recipes = [_to_catalog_recipe(row) for row in rows]
        ingredient_names = {ingredient.name for ingredient in query.confirmed_ingredients}
        # API가 범용 목록만 반환하는 경우에도 제목·재료가 전혀 무관한 결과는 일찍 제외한다.
        relevant = [recipe for recipe in recipes if _is_related(recipe, ingredient_names)]
        return relevant or recipes


def _extract_rows(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Mapping):
        raise RecipeSourceError("식품안전나라 응답 형식이 올바르지 않습니다.")
    container = payload.get("COOKRCP01")
    if not isinstance(container, Mapping):
        raise RecipeSourceError("식품안전나라 레시피 목록이 없습니다.")
    rows = container.get("row")
    if not isinstance(rows, list):
        raise RecipeSourceError("식품안전나라 레시피 목록이 없습니다.")
    return [row for row in rows if isinstance(row, Mapping)]


def _to_catalog_recipe(row: Mapping[str, object]) -> CatalogRecipe:
    title = _text(row.get("RCP_NM")) or "이름 없는 레시피"
    nutrition_fields = ("INFO_ENG", "INFO_PRO", "INFO_CAR", "INFO_FAT")
    has_nutrition = any(_text(row.get(field)) for field in nutrition_fields)
    return CatalogRecipe(
        recipe_id=_text(row.get("RCP_SEQ")) or title,
        title=title,
        image=_text(row.get("ATT_FILE_NO_MAIN")) or None,
        cook_time=0,
        ingredients=_parse_ingredients(_text(row.get("RCP_PARTS_DTLS"))),
        nutrition=(
            NutritionValues(
                calories=_number(row.get("INFO_ENG")),
                protein=_number(row.get("INFO_PRO")),
                carbohydrate=_number(row.get("INFO_CAR")),
                fat=_number(row.get("INFO_FAT")),
            )
            if has_nutrition
            else None
        ),
        source="foodsafetykorea:COOKRCP01",
        source_metadata={"recipe_tip": _text(row.get("RCP_NA_TIP"))},
    )


def _parse_ingredients(raw_ingredients: str) -> list[RecipeIngredient]:
    """표시용 재료 설명에서 명확한 첫 재료명만 보수적으로 추출합니다.

    원문 형식이 공급원마다 달라 완전한 정규화는 하지 않는다. 이 값은 추천 후보의
    보조 신호이며, 정확한 수량·동의어 정규화는 이후 BE2 재료 정규화 단계의 교체 지점이다.
    """

    names: list[RecipeIngredient] = []
    for line in re.split(r"[\n,]", raw_ingredients):
        cleaned = re.sub(r"\[[^\]]+\]", "", line).strip(" -•")
        match = re.match(r"([가-힣A-Za-z]+)", cleaned)
        if match:
            names.append(RecipeIngredient(name=match.group(1), amount="필요량"))
    return names


def _is_related(recipe: CatalogRecipe, ingredient_names: set[str]) -> bool:
    searchable = " ".join([recipe.title, *(item.name for item in recipe.ingredients)])
    return any(name in searchable for name in ingredient_names)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(float(value), 0)
    if isinstance(value, str):
        try:
            return max(float(value.strip()), 0)
        except ValueError:
            pass
    return 0
