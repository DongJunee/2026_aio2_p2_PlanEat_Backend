"""보유 재료와 레시피를 비교해 부족 재료·장보기 목록을 만드는 Tool입니다."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools.tool_models import IngredientGuide, RecipeMatch

_MAX_SHOPPING_ITEMS = 5


class ShoppingPlan(BaseModel):
    """기존 Chat API의 missing_ingredients·shopping_list와 호환되는 계획입니다."""

    model_config = ConfigDict(extra="forbid")

    missing_ingredients: list[dict[str, str | None]] = Field(default_factory=list)
    shopping_list: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=_MAX_SHOPPING_ITEMS,
    )


class ShoppingTool:
    """부족 재료를 레시피 순서대로 최대 5개까지 장보기 목록에 담는 계산기입니다."""

    def build(
        self,
        match: RecipeMatch,
        guides_by_ingredient: Mapping[str, IngredientGuide] | None = None,
    ) -> ShoppingPlan:
        """RAG 대체재 정보가 있으면 부족 재료에만 보조 정보로 붙입니다."""

        guides = guides_by_ingredient or {}
        missing: list[dict[str, str | None]] = []
        shopping: list[dict[str, str]] = []
        seen: set[str] = set()

        for ingredient in match.missing_ingredients:
            # 동일 재료가 원본 레시피에 반복돼도 FE에는 한 항목만 보여준다.
            if ingredient.name in seen:
                continue
            seen.add(ingredient.name)
            guide = guides.get(ingredient.name)
            alternative = guide.substitutes[0] if guide and guide.substitutes else None
            missing.append(
                {
                    "name": ingredient.name,
                    "importance": ingredient.importance,
                    "alternative": alternative,
                }
            )
            if ingredient.importance != "생략 가능":
                shopping.append({"ingredient": ingredient.name, "amount": ingredient.amount})

        # ``missing_ingredients``는 대체·생략 정보까지 보존한다. 실제 구매 목록만
        # 원본 레시피 순서대로 잘라 5개를 넘지 않게 한다. 대체 가능 재료를 제외하면
        # Jev가 사용자의 기피 재료를 대체하는 후속 흐름을 판단할 수 없게 된다.
        return ShoppingPlan(
            missing_ingredients=missing,
            shopping_list=shopping[:_MAX_SHOPPING_ITEMS],
        )
