from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Attachment(BaseModel):
    """Chat에 첨부된 이미지입니다."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image"]
    data: str = Field(min_length=1, description="Base64 이미지 또는 이미지 URL")


class ChatRequest(BaseModel):
    """POST /chat 요청 본문입니다."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=2_000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)


class IngredientConfirmation(BaseModel):
    """사용자 확인이 필요한 식재료 후보입니다."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    amount: str = Field(min_length=1)


class MissingIngredient(BaseModel):
    """레시피에 부족한 식재료와 대체 정보입니다."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    importance: Literal["필수", "권장", "대체 가능", "생략 가능"]
    alternative: str | None = None


class ShoppingItem(BaseModel):
    """레시피별 장보기 항목입니다."""

    model_config = ConfigDict(extra="forbid")

    ingredient: str = Field(min_length=1)
    amount: str = Field(min_length=1)


class Nutrition(BaseModel):
    """레시피 영양 정보입니다."""

    model_config = ConfigDict(extra="forbid")

    calories: int | float = Field(ge=0)
    protein: int | float = Field(ge=0)
    carbohydrate: int | float = Field(ge=0)
    fat: int | float = Field(ge=0)


class Recipe(BaseModel):
    """추천 레시피입니다."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    image: str | None = None
    cook_time: int = Field(ge=0)
    owned_ingredients: list[str] = Field(default_factory=list)
    missing_ingredients: list[MissingIngredient] = Field(default_factory=list)
    shopping_list: list[ShoppingItem] = Field(default_factory=list)
    nutrition: Nutrition


class RecipeSet(BaseModel):
    set_id: str = Field(min_length=1)
    recipes: list[Recipe] = Field(min_length=5, max_length=5)


class RecommendationData(BaseModel):
    recipe_sets: list[RecipeSet] = Field(min_length=2, max_length=2)


class ChatSuccessResponse(BaseModel):
    status: Literal["SUCCESS"]
    step: Literal["COMPLETED"]
    response: str
    data: RecommendationData


class ChatConditionInputResponse(BaseModel):
    status: Literal["NEED_MORE_INFO"]
    step: Literal["CONDITION_INPUT"]
    response: str
    questions: list[str] = Field(min_length=1)


class ChatImageInputResponse(BaseModel):
    status: Literal["NEED_MORE_INFO"]
    step: Literal["IMAGE_INPUT"]
    response: str
    questions: list[str] = Field(min_length=1)


class ChatIngredientConfirmResponse(BaseModel):
    status: Literal["NEED_MORE_INFO"]
    step: Literal["INGREDIENT_CONFIRM"]
    response: str
    ingredients: list[IngredientConfirmation] = Field(min_length=1)


class ChatErrorResponse(BaseModel):
    status: Literal["ERROR"]
    response: str


ChatResponse = (
    ChatSuccessResponse
    | ChatConditionInputResponse
    | ChatImageInputResponse
    | ChatIngredientConfirmResponse
    | ChatErrorResponse
)
