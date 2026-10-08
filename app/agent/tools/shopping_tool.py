"""보유 재료와 레시피를 비교해 부족 재료·장보기 목록을 만드는 Tool입니다."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools.be2_models import IngredientGuide, RecipeMatch


class ShoppingPlan(BaseModel):
    """기존 Chat API의 missing_ingredients·shopping_list와 호환되는 계획입니다."""

    model_config = ConfigDict(extra="forbid")

    missing_ingredients: list[dict[str, str | None]] = Field(default_factory=list)
    shopping_list: list[dict[str, str]] = Field(default_factory=list)


class ShoppingTool:
    """필수·권장 재료만 장보기 목록에 포함하는 결정적 계산기입니다."""

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

        return ShoppingPlan(missing_ingredients=missing, shopping_list=shopping)
