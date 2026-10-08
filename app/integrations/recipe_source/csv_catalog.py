"""로컬 CSV 레시피 카탈로그를 Tool Hub 내부 모델로 읽는 어댑터입니다.

외부 Recipe API를 요청하지 않는다. 검토된 CSV를 프로젝트 안에 두고, 필요하면 같은
열 구조를 가진 파일 경로를 생성자에 전달해 교체한다.
"""

import asyncio
import csv
import json
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from app.agent.tools.tool_models import (
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
_UNKNOWN_AMOUNT = "수량 미상"
_AMOUNT_UNITS = (
    r"kg|mg|ml|g|l|개|마리|장|대|모|봉|팩|캔|컵|큰술|작은술|쪽|줄기|알|"
    r"뿌리|cm|인분|포기|단|통|송이"
)
_AMOUNT_RE = re.compile(
    rf"(?P<amount>"
    rf"(?:\d+\s*[×x]\s*\d+\s*cm|"
    rf"\d+(?:\.\d+)?\s*[½⅓⅔¼¾⅛⅜⅝⅞]|"
    rf"\d+\s*[/⁄]\s*\d+|"
    rf"\d+(?:\.\d+)?|"
    rf"[½⅓⅔¼¾⅛⅜⅝⅞])\s*(?:{_AMOUNT_UNITS})"
    rf"(?:\s*\([^)]*\))?"
    rf"|(?P<qualitative>약간|적당량|한줌|한 줌)"
    rf")",
    re.IGNORECASE,
)
_INGREDIENT_ALIASES: dict[str, tuple[str, ...]] = {
    "계란": ("달걀",),
    "달걀": ("계란",),
    "쇠고기": ("소고기",),
    "소고기": ("쇠고기",),
    "배추잎": ("배춧잎",),
    "배춧잎": ("배추잎",),
}


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

        확정 재료가 포함된 후보를 먼저 반환하되, 조리시간·식단 조건의 최종 필터와
        정렬은 ``RecipeTool``이 담당한다. 재료 검색까지 무시하면 카탈로그 앞부분에
        없는 재료가 요청되어도 항상 같은 후보만 전달될 수 있다.
        """

        if limit < 1:
            return []
        if self._recipes is None:
            self._recipes = await asyncio.to_thread(self._load_recipes)
        owned_names = {_normalize_name(item.name) for item in query.confirmed_ingredients}
        matching = [
            recipe
            for recipe in self._recipes
            if any(_normalize_name(item.name) in owned_names for item in recipe.ingredients)
        ]
        matching_ids = {recipe.recipe_id for recipe in matching}
        non_matching = [
            recipe for recipe in self._recipes if recipe.recipe_id not in matching_ids
        ]
        return [*matching, *non_matching][:limit]

    async def list_recipes(self) -> tuple[CatalogRecipe, ...]:
        """벡터 검색 인덱스 적재에 사용할 검증된 전체 카탈로그를 반환합니다."""

        if self._recipes is None:
            self._recipes = await asyncio.to_thread(self._load_recipes)
        return self._recipes

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
    """보강 원본 CSV의 재료 분류를 Tool Hub 표준 모델로 정규화합니다."""

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
    """보강 CSV의 재료 분류와 원본 조리 설명의 실제 필요량을 함께 보존합니다."""

    groups = (
        ("REQUIRED_INGREDIENTS", "필수", True),
        ("REQUIRED_SEASONINGS", "필수", True),
        ("SUBSTITUTABLE_INGREDIENTS", "대체 가능", True),
        ("SUBSTITUTABLE_SEASONINGS", "대체 가능", True),
        ("OPTIONAL_INGREDIENTS", "생략 가능", False),
    )
    detail_text = _optional_value(row, "RCP_PARTS_DTLS") or ""
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
                    amount=_find_ingredient_amount(name, detail_text),
                    importance=importance,  # type: ignore[arg-type]
                    required=required,
                )
            )
    return ingredients or _fallback_internal_ingredients(row)


def _fallback_internal_ingredients(row: Mapping[str, str | None]) -> list[RecipeIngredient]:
    """보강 열이 비어 있는 행은 원본 표시 재료에서 이름과 필요량을 읽습니다."""

    detail_text = _optional_value(row, "RCP_PARTS_DTLS") or ""
    ingredients: list[RecipeIngredient] = []
    for line in _split_detail_segments(detail_text):
        cleaned = re.sub(r"\[[^\]]+\]", "", line).strip(" -•")
        match = re.match(r"([가-힣A-Za-z]+)", cleaned)
        if match:
            name = match.group(1)
            ingredients.append(
                RecipeIngredient(name=name, amount=_find_ingredient_amount(name, detail_text))
            )
    return ingredients


def _find_ingredient_amount(name: str, detail_text: str) -> str:
    """원본 조리 재료 설명에서 이름에 대응하는 실제 필요량을 추출합니다.

    원본은 ``연두부 75g(3/4모)``처럼 자유 문장으로 저장되어 있어 완전한 수량
    정규화는 하지 않는다. 조리법에 표시된 표현을 그대로 반환하고, 숫자·단위가
    없는 재료는 ``약간`` 또는 ``수량 미상``으로 명시한다.
    """

    variants = (name, *_INGREDIENT_ALIASES.get(_ingredient_key(name), ()))
    for segment in _split_detail_segments(detail_text):
        for variant in variants:
            compact_variant = "".join(variant.split())
            pattern = re.compile(r"\s*".join(re.escape(char) for char in compact_variant))
            for match in pattern.finditer(segment):
                # 짧은 이름이 ``파프리카`` 같은 다른 재료의 접두어에 붙는 오탐을 줄인다.
                next_character = segment[match.end() : match.end() + 1]
                if next_character and "가" <= next_character <= "힣":
                    continue
                quantity = _AMOUNT_RE.search(segment[match.end() :])
                if quantity is not None:
                    return (
                        quantity.group("amount")
                        or quantity.group("qualitative")
                        or _UNKNOWN_AMOUNT
                    )
    return _UNKNOWN_AMOUNT


def _split_detail_segments(value: str) -> list[str]:
    """괄호 안 쉼표는 보존하면서 조리 재료 설명을 항목 단위로 나눕니다."""

    segments: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(depth - 1, 0)
        elif character in ",\n" and depth == 0:
            segment = value[start:index].strip()
            if segment:
                segments.append(segment)
            start = index + 1
    tail = value[start:].strip()
    if tail:
        segments.append(tail)
    return segments


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


def _normalize_name(name: str) -> str:
    """카탈로그 검색에서만 사용하는 재료명 표기 정규화입니다."""

    normalized = _ingredient_key(name)
    aliases = {"달걀": "계란", "파": "대파", "닭가슴": "닭가슴살"}
    return aliases.get(normalized, normalized)
