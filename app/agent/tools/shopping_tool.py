"""보유 재료와 레시피를 비교해 부족 재료·장보기 목록을 만드는 Tool입니다."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools.tool_models import IngredientGuide, RecipeMatch

_MAX_SHOPPING_ITEMS = 5


class ShoppingPlan(BaseModel):
    """기존 Chat API의 missing_ingredients·shopping_list와 호환되는 계획입니다."""

    model_config = ConfigDict(extra="forbid")

    # FE에는 ``missing_ingredients``도 장보기 재료로 노출된다. 따라서 두 목록 모두
    # 한 레시피당 5개를 초과하지 않게 계약으로 보장한다.
    missing_ingredients: list[dict[str, str | None]] = Field(
        default_factory=list,
        max_length=_MAX_SHOPPING_ITEMS,
    )
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
        candidates: list[tuple[int, int, dict[str, str | None], dict[str, str] | None]] = []
        seen: set[str] = set()
        importance_order = {
            "필수": 0,
            "권장": 1,
            "대체 가능": 2,
            "생략 가능": 3,
        }

        for index, ingredient in enumerate(match.missing_ingredients):
            # 동일 재료가 원본 레시피에 반복돼도 FE에는 한 항목만 보여준다.
            if ingredient.name in seen:
                continue
            seen.add(ingredient.name)
            guide = guides.get(ingredient.name)
            alternative = guide.substitutes[0] if guide and guide.substitutes else None
            missing_item = {
                "name": ingredient.name,
                "importance": ingredient.importance,
                "alternative": alternative,
            }
            shopping_item = (
                None
                if ingredient.importance == "생략 가능"
                else {"ingredient": ingredient.name, "amount": ingredient.amount}
            )
            candidates.append(
                (
                    importance_order[ingredient.importance],
                    index,
                    missing_item,
                    shopping_item,
                )
            )

        # 필수 → 권장 → 대체 가능 → 생략 가능 순서로 고르고, 같은 중요도에서는
        # 원본 레시피 순서를 유지한다. 이 선택 결과를 두 API 필드에 함께 사용해야
        # 부족 재료가 6개 이상 화면에 표시되는 일이 없다.
        visible = sorted(candidates, key=lambda item: (item[0], item[1]))[
            :_MAX_SHOPPING_ITEMS
        ]
        missing = [item[2] for item in visible]
        shopping = [item[3] for item in visible if item[3] is not None]

        return ShoppingPlan(
            missing_ingredients=missing,
            shopping_list=shopping,
        )
