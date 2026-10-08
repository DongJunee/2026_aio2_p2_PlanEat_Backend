"""로컬 CSV 레시피 카탈로그를 BE2 내부 모델로 읽는 어댑터입니다.

외부 Recipe API를 요청하지 않는다. 검토된 CSV를 프로젝트 안에 두고, 필요하면 같은
열 구조를 가진 파일 경로를 생성자에 전달해 교체한다.
"""

import asyncio
import csv
import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from app.agent.tools.be2_models import (
    CatalogRecipe,
    NutritionValues,
    RecipeIngredient,
    RecipeSearchQuery,
)

_NORMALIZED_REQUIRED_COLUMNS = frozenset(
    {
        "recipe_id",
        "title",
        "image",
        "cook_time",
        "ingredients",
        "calories",
        "protein",
        "carbohydrate",
        "fat",
        "source",
    }
)
_INTERNAL_RAW_REQUIRED_COLUMNS = frozenset({"RCP_SEQ", "RCP_NM", "RCP_PARTS_DTLS"})


class LocalRecipeCatalogError(RuntimeError):
    """로컬 레시피 CSV가 없거나 계약에 맞지 않을 때 발생합니다."""


class CsvRecipeRepository:
    """검토된 로컬 CSV를 읽는 ``RecipeRepository`` 구현체입니다.

    CSV는 두 내부 형식을 지원한다. 개발용 정규화 형식은 ``ingredients``에
    ``RecipeIngredient`` JSON 배열을 저장하고, 보강 원본 형식은 ``RCP_*`` 및
    ``REQUIRED_*`` 열로 재료 중요도를 제공한다. 파일은 첫 검색 때 한 번만 읽는다.
    """

    def __init__(self, catalog_path: str | Path) -> None:
        self._catalog_path = Path(catalog_path)
        self._recipes: tuple[CatalogRecipe, ...] | None = None

    @classmethod
    def from_internal_catalog(cls) -> "CsvRecipeRepository":
        """프로젝트 ``data/``의 보강 레시피 CSV를 사용하는 기본 저장소를 만듭니다."""

        return cls(internal_catalog_path())

    async def search(self, query: RecipeSearchQuery, *, limit: int) -> list[CatalogRecipe]:
        """카탈로그를 로드하고, 순위 계산에 필요한 후보 수만 반환합니다.

        재료 보유율·조리시간·식단 조건의 최종 필터와 정렬은 ``RecipeTool``이 담당한다.
        """

        del query  # RecipeTool이 정규화된 조건으로 후보를 재정렬한다.
        if limit < 1:
            return []
        if self._recipes is None:
            self._recipes = await asyncio.to_thread(self._load_recipes)
        return list(self._recipes[:limit])

    def _load_recipes(self) -> tuple[CatalogRecipe, ...]:
        """CSV 전체를 검증해 불완전한 카탈로그가 부분 사용되지 않게 합니다."""

        try:
            with self._catalog_path.open("r", encoding="utf-8-sig", newline="") as catalog_file:
                reader = csv.DictReader(catalog_file)
                fieldnames = set(reader.fieldnames or ())
                if _NORMALIZED_REQUIRED_COLUMNS <= fieldnames:
                    recipes = tuple(
                        _recipe_from_row(row, row_number)
                        for row_number, row in enumerate(reader, start=2)
                    )
                elif _INTERNAL_RAW_REQUIRED_COLUMNS <= fieldnames:
                    recipes = tuple(
                        _recipe_from_internal_row(row, row_number)
                        for row_number, row in enumerate(reader, start=2)
                    )
                else:
                    expected = _NORMALIZED_REQUIRED_COLUMNS | _INTERNAL_RAW_REQUIRED_COLUMNS
                    missing = ", ".join(sorted(expected - fieldnames))
                    raise LocalRecipeCatalogError(
                        f"지원하는 레시피 CSV 형식의 열이 없습니다: {missing}"
                    )
        except OSError as error:
            raise LocalRecipeCatalogError("로컬 레시피 CSV를 읽을 수 없습니다.") from error
        except csv.Error as error:
            raise LocalRecipeCatalogError("로컬 레시피 CSV 형식이 올바르지 않습니다.") from error

        if not recipes:
            raise LocalRecipeCatalogError("로컬 레시피 CSV에 레시피가 없습니다.")
        return recipes


def internal_catalog_path() -> Path:
    """내부에서 관리하는 보강 레시피 CSV 기본 경로를 반환합니다."""

    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "COOKRCP01_FINAL_WITH_INGREDIENT_GROUPS_REVISED_V2.csv"
    )


def _recipe_from_row(row: Mapping[str, str | None], row_number: int) -> CatalogRecipe:
    try:
        return CatalogRecipe(
            recipe_id=_required_value(row, "recipe_id", row_number),
            title=_required_value(row, "title", row_number),
            image=_optional_value(row, "image"),
            cook_time=_number_value(row, "cook_time", row_number),
            ingredients=_ingredients_value(row, row_number),
            nutrition=_nutrition_value(row, row_number),
            source=_required_value(row, "source", row_number),
            source_metadata=_source_metadata(row),
        )
    except ValidationError as error:
        raise LocalRecipeCatalogError(f"레시피 CSV {row_number}행 값이 올바르지 않습니다.") from error


def _recipe_from_internal_row(
    row: Mapping[str, str | None], row_number: int
) -> CatalogRecipe:
    """보강 원본 CSV의 재료 분류를 BE2 표준 모델로 정규화합니다."""

    try:
        title = _required_value(row, "RCP_NM", row_number)
        nutrition_fields = ("INFO_ENG", "INFO_PRO", "INFO_CAR", "INFO_FAT")
        nutrition_values = {field: _optional_value(row, field) for field in nutrition_fields}
        nutrition = (
            NutritionValues(
                calories=_nonnegative_number(row, "INFO_ENG", row_number),
                protein=_nonnegative_number(row, "INFO_PRO", row_number),
                carbohydrate=_nonnegative_number(row, "INFO_CAR", row_number),
                fat=_nonnegative_number(row, "INFO_FAT", row_number),
            )
            if any(nutrition_values.values())
            else None
        )
        return CatalogRecipe(
            recipe_id=_required_value(row, "RCP_SEQ", row_number),
            title=title,
            image=_optional_value(row, "ATT_FILE_NO_MAIN"),
            cook_time=0,
            ingredients=_internal_ingredients(row),
            nutrition=nutrition,
            source="internal:recipe-catalog",
            source_metadata=_internal_source_metadata(row),
        )
    except ValidationError as error:
        raise LocalRecipeCatalogError(f"내부 레시피 CSV {row_number}행 값이 올바르지 않습니다.") from error


def _ingredients_value(row: Mapping[str, str | None], row_number: int) -> list[RecipeIngredient]:
    raw_ingredients = _required_value(row, "ingredients", row_number)
    try:
        parsed = json.loads(raw_ingredients)
    except json.JSONDecodeError as error:
        raise LocalRecipeCatalogError(
            f"레시피 CSV {row_number}행 ingredients는 JSON 배열이어야 합니다."
        ) from error
    if not isinstance(parsed, list):
        raise LocalRecipeCatalogError(
            f"레시피 CSV {row_number}행 ingredients는 JSON 배열이어야 합니다."
        )
    try:
        return [RecipeIngredient.model_validate(ingredient) for ingredient in parsed]
    except ValidationError as error:
        raise LocalRecipeCatalogError(
            f"레시피 CSV {row_number}행 ingredients 값이 올바르지 않습니다."
        ) from error


def _nutrition_value(
    row: Mapping[str, str | None], row_number: int
) -> NutritionValues | None:
    fields = ("calories", "protein", "carbohydrate", "fat")
    values = {field: _optional_value(row, field) for field in fields}
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise LocalRecipeCatalogError(
            f"레시피 CSV {row_number}행 영양 정보는 모두 입력하거나 모두 비워야 합니다."
        )
    return NutritionValues(
        **{field: _number_value(row, field, row_number) for field in fields}
    )


def _required_value(row: Mapping[str, str | None], field: str, row_number: int) -> str:
    value = _optional_value(row, field)
    if value is None:
        raise LocalRecipeCatalogError(f"레시피 CSV {row_number}행 {field} 값이 필요합니다.")
    return value


def _optional_value(row: Mapping[str, str | None], field: str) -> str | None:
    value = row.get(field)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _source_metadata(row: Mapping[str, str | None]) -> dict[str, str]:
    """선택 열을 CatalogRecipe 메타데이터로 전달합니다.

    ``meal_role``은 MealPlanningSubgraph가 메인·반찬을 데이터 기반으로 나눌 때
    사용한다. 열이 없어도 기존 CSV 카탈로그와 호환되며, 이 경우 서브그래프의 순위
    기반 fallback이 적용된다.
    """

    meal_role = _optional_value(row, "meal_role")
    return {"meal_role": meal_role} if meal_role else {}


def _internal_ingredients(row: Mapping[str, str | None]) -> list[RecipeIngredient]:
    """보강 CSV의 필수·대체·생략 재료 열을 중요도와 함께 보존합니다."""

    groups = (
        ("REQUIRED_INGREDIENTS", "필수", True),
        ("REQUIRED_SEASONINGS", "필수", True),
        ("SUBSTITUTABLE_INGREDIENTS", "대체 가능", True),
        ("SUBSTITUTABLE_SEASONINGS", "대체 가능", True),
        ("OPTIONAL_INGREDIENTS", "생략 가능", False),
    )
    ingredients: list[RecipeIngredient] = []
    seen: set[str] = set()
    for field, importance, required in groups:
        for name in _split_pipe_values(_optional_value(row, field) or ""):
            key = _ingredient_key(name)
            if key in seen:
                continue
            seen.add(key)
            ingredients.append(
                RecipeIngredient(
                    name=name,
                    amount="필요량",
                    importance=importance,  # type: ignore[arg-type]
                    required=required,
                )
            )
    return ingredients or _fallback_internal_ingredients(row)


def _fallback_internal_ingredients(row: Mapping[str, str | None]) -> list[RecipeIngredient]:
    """보강 열이 비어 있는 행은 원본 표시 재료에서 보수적으로 이름만 읽습니다."""

    import re

    ingredients: list[RecipeIngredient] = []
    for line in re.split(r"[\n,]", _optional_value(row, "RCP_PARTS_DTLS") or ""):
        cleaned = re.sub(r"\[[^\]]+\]", "", line).strip(" -•")
        match = re.match(r"([가-힣A-Za-z]+)", cleaned)
        if match:
            ingredients.append(RecipeIngredient(name=match.group(1), amount="필요량"))
    return ingredients


def _internal_source_metadata(row: Mapping[str, str | None]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    role = _internal_meal_role(_optional_value(row, "RCP_PAT2") or "")
    if role:
        metadata["meal_role"] = role
    recipe_tip = _optional_value(row, "RCP_NA_TIP")
    if recipe_tip:
        metadata["recipe_tip"] = recipe_tip
    return metadata


def _internal_meal_role(category: str) -> str | None:
    if category in {"반찬", "국&찌개", "후식"}:
        return "side"
    if category:
        return "main"
    return None


def _split_pipe_values(value: str) -> list[str]:
    return [name.strip() for name in value.split("|") if name.strip()]


def _number_value(row: Mapping[str, str | None], field: str, row_number: int) -> float:
    value = _required_value(row, field, row_number)
    try:
        return float(value)
    except ValueError as error:
        raise LocalRecipeCatalogError(
            f"레시피 CSV {row_number}행 {field} 값은 숫자여야 합니다."
        ) from error


def _nonnegative_number(row: Mapping[str, str | None], field: str, row_number: int) -> float:
    value = _optional_value(row, field)
    if value is None:
        return 0
    try:
        return max(float(value), 0)
    except ValueError as error:
        raise LocalRecipeCatalogError(
            f"내부 레시피 CSV {row_number}행 {field} 값은 숫자여야 합니다."
        ) from error


def _ingredient_key(name: str) -> str:
    return "".join(name.split()).lower()
