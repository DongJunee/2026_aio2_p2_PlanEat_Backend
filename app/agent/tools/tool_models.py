"""Tool Hub 내부에서 사용하는 정규화 모델입니다.

외부 레시피 소스의 느슨한 필드를 Tool Hub 내부 모델로 정규화한 뒤, 마지막에 기존 Chat API가 요구하는 recipe_sets
형태로 변환하기 위한 독립 경계다.
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolIngredient(BaseModel):
    """Tool Hub 도구가 받거나 반환하는 재료와 수량입니다."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    amount: str = Field(min_length=1)


class RecipeIngredient(ToolIngredient):
    """레시피 원재료와 장보기 중요도입니다."""

    required: bool = True
    importance: Literal["필수", "권장", "대체 가능", "생략 가능"] = "필수"


class NutritionValues(BaseModel):
    """한 레시피 기준의 열량과 3대 영양소입니다."""

    model_config = ConfigDict(extra="forbid")

    calories: int | float = Field(ge=0)
    protein: int | float = Field(ge=0)
    carbohydrate: int | float = Field(ge=0)
    fat: int | float = Field(ge=0)


class CatalogRecipe(BaseModel):
    """외부 Recipe Source를 정규화한 Tool Hub 내부 레시피입니다."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    image: str | None = None
    cook_time: int = Field(ge=0)
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    nutrition: NutritionValues | None = None
    source: str = Field(min_length=1)
    source_metadata: Mapping[str, str] = Field(default_factory=dict)


class RecipeSearchQuery(BaseModel):
    """Recipe Source 검색에 필요한 사용자 확정 입력입니다."""

    model_config = ConfigDict(extra="forbid")

    confirmed_ingredients: list[ToolIngredient] = Field(min_length=1)
    user_conditions: Mapping[str, object] = Field(min_length=1)


class RecipeMatch(BaseModel):
    """보유 재료 기준으로 순위가 계산된 레시피입니다."""

    model_config = ConfigDict(extra="forbid")

    recipe: CatalogRecipe
    owned_ingredients: list[RecipeIngredient] = Field(default_factory=list)
    missing_ingredients: list[RecipeIngredient] = Field(default_factory=list)
    score: float


class IngredientGuide(BaseModel):
    """RAG가 반환하는 재료 활용·대체·보관 정보입니다."""

    model_config = ConfigDict(extra="forbid")

    ingredient: str = Field(min_length=1)
    usage_tip: str | None = None
    substitutes: list[str] = Field(default_factory=list)
    storage_tip: str | None = None
    flavor_note: str | None = None
    sources: list[str] = Field(default_factory=list)


class ToolRecipeCard(BaseModel):
    """기존 Chat API ``Recipe`` 객체와 호환되는 Tool Hub 결과 카드입니다."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    image: str | None = None
    cook_time: int = Field(ge=0)
    owned_ingredients: list[str] = Field(default_factory=list)
    missing_ingredients: list[dict[str, str | None]] = Field(default_factory=list)
    shopping_list: list[dict[str, str]] = Field(default_factory=list)
    nutrition: NutritionValues


class ToolRecipeSet(BaseModel):
    """FE 계약의 5개 레시피 묶음입니다."""

    model_config = ConfigDict(extra="forbid")

    set_id: str = Field(min_length=1)
    recipes: list[ToolRecipeCard] = Field(min_length=5, max_length=5)


class ToolRecommendationData(BaseModel):
    """FE의 ``RecommendationData``와 동일한 형태의 Tool Hub 완료 데이터입니다."""

    model_config = ConfigDict(extra="forbid")

    recipe_sets: list[ToolRecipeSet] = Field(min_length=2, max_length=2)
