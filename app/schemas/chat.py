from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RECIPE_SET_DISPLAY_LABELS = {
    "BEST_MATCH": "냉장고 재료와 가장 잘 맞아요",
    "MORE_OPTIONS": "이런 식단도 있어요",
}
_DEFAULT_RECIPE_SET_DISPLAY_LABELS = tuple(RECIPE_SET_DISPLAY_LABELS.values())
_RECIPE_SET_KEYS_BY_DISPLAY_LABEL = {
    label: set_id for set_id, label in RECIPE_SET_DISPLAY_LABELS.items()
}


def _normalize_recipe_set_key(value: str, index: int) -> str:
    """표시 문구와 기존 내부 키를 서버 내부 선택 키로 정규화합니다."""

    stripped_value = value.strip()
    display_key = _RECIPE_SET_KEYS_BY_DISPLAY_LABEL.get(stripped_value)
    if display_key:
        return display_key

    normalized_value = stripped_value.upper().replace("-", "_").replace(" ", "_")
    if normalized_value in RECIPE_SET_DISPLAY_LABELS:
        return normalized_value
    return stripped_value or f"SET{index + 1:03d}"


def _recipe_set_display_label(set_id: str, index: int) -> str:
    """세트 내부 ID를 FE에 표시할 안정적인 문구로 변환합니다."""

    normalized_set_id = _normalize_recipe_set_key(set_id, index)
    return RECIPE_SET_DISPLAY_LABELS.get(
        normalized_set_id,
        _DEFAULT_RECIPE_SET_DISPLAY_LABELS[index],
    )


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
    cooking_steps: list[str] = Field(default_factory=list)
    owned_ingredients: list[str] = Field(default_factory=list)
    optional_ingredients: list[str] = Field(
        default_factory=list,
        description="원본 레시피에서 선택적으로 넣을 수 있는 재료",
    )
    substitutable_ingredients: list[str] = Field(
        default_factory=list,
        description="원본 레시피에서 다른 재료로 대체할 수 있는 재료",
    )
    missing_ingredients: list[MissingIngredient] = Field(default_factory=list)
    shopping_list: list[ShoppingItem] = Field(default_factory=list)
    nutrition: Nutrition


class RecipeSet(BaseModel):
    """추천 레시피 묶음과 FE 표시용 문구를 담습니다."""

    set_id: str = Field(min_length=1)
    set_key: str | None = Field(
        default=None,
        min_length=1,
        description="서버 내부에서 세트를 선택할 때 사용하는 키",
    )
    label: str | None = Field(default=None, min_length=1, description="FE 표시용 세트 문구")
    recipes: list[Recipe] = Field(min_length=5, max_length=5)


class RecommendationData(BaseModel):
    recipe_sets: list[RecipeSet] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def populate_recipe_set_labels(self) -> "RecommendationData":
        """API의 set_id는 표시 문구로, set_key는 내부 선택 키로 정규화합니다."""

        # LLM·Tool Hub가 어떤 ID를 반환하더라도 두 세트의 화면 문구가 달라지지
        # 않도록 위치 fallback을 둔다. canonical ID가 있으면 ID 매핑을 우선한다.
        self.recipe_sets = [
            recipe_set.model_copy(
                update={
                    "set_id": _recipe_set_display_label(
                        recipe_set.set_key or recipe_set.set_id,
                        index,
                    ),
                    "set_key": _normalize_recipe_set_key(
                        recipe_set.set_key or recipe_set.set_id,
                        index,
                    ),
                    "label": _recipe_set_display_label(
                        recipe_set.set_key or recipe_set.set_id,
                        index,
                    ),
                }
            )
            for index, recipe_set in enumerate(self.recipe_sets)
        ]
        return self


def get_recipe_set_key(recipe_set: RecipeSet, index: int = 0) -> str:
    """정규화된 추천 세트에서 서버 내부 선택 키를 반환합니다."""

    return _normalize_recipe_set_key(recipe_set.set_key or recipe_set.set_id, index)


class ChatSuccessResponse(BaseModel):
    status: Literal["SUCCESS"]
    step: Literal["COMPLETED"]
    response: str
    data: RecommendationData
    next_action: Literal["FEEDBACK_OR_SET_SELECTION", "PDF_READY"] | None = None
    available_set_ids: list[str] | None = None
    selected_set_id: str | None = None
    pdf_url: str | None = None


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


class ChatInputRequirementsResponse(BaseModel):
    """이미지와 자연어 추천 조건을 한 번에 수집하는 응답입니다."""

    status: Literal["NEED_MORE_INFO"]
    step: Literal["INPUT_REQUIREMENTS"]
    response: str
    questions: list[str] = Field(min_length=1)


class ChatIngredientConfirmResponse(BaseModel):
    status: Literal["NEED_MORE_INFO"]
    step: Literal["INGREDIENT_CONFIRM"]
    response: str
    ingredients: list[IngredientConfirmation] = Field(min_length=1)
    confirmed_ingredients: list[IngredientConfirmation] = Field(default_factory=list)


class ChatErrorResponse(BaseModel):
    status: Literal["ERROR"]
    response: str


ChatResponse = (
    ChatSuccessResponse
    | ChatConditionInputResponse
    | ChatImageInputResponse
    | ChatInputRequirementsResponse
    | ChatIngredientConfirmResponse
    | ChatErrorResponse
)


class ChatStreamEvent(BaseModel):
    """SSE로 전달하는 Chat API 스트림 프레임입니다."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["start", "progress", "delta", "complete", "error", "done"]
    data: dict[str, object] = Field(default_factory=dict)
