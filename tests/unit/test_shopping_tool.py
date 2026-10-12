"""Shopping Tool의 장보기 개수 제한을 독립적으로 검증합니다."""

from app.agent.tools.shopping_tool import ShoppingTool
from app.agent.tools.tool_models import (
    CatalogRecipe,
    NutritionValues,
    RecipeIngredient,
    RecipeMatch,
)


def test_shopping_tool_limits_all_visible_missing_ingredients_to_five() -> None:
    """FE에 전달되는 부족 재료와 장보기 목록은 모두 5개 이하여야 한다."""

    owned = RecipeIngredient(name="두부", amount="1모", importance="필수")
    missing = [
        RecipeIngredient(name="생략재", amount="1개", importance="생략 가능"),
        RecipeIngredient(name="대체재", amount="1개", importance="대체 가능"),
        RecipeIngredient(name="권장1", amount="1개", importance="권장"),
        RecipeIngredient(name="필수1", amount="1개", importance="필수"),
        RecipeIngredient(name="필수2", amount="1개", importance="필수"),
        RecipeIngredient(name="권장2", amount="1개", importance="권장"),
        RecipeIngredient(name="권장3", amount="1개", importance="권장"),
    ]
    recipe = CatalogRecipe(
        recipe_id="shopping-limit",
        title="장보기 제한 테스트",
        cook_time=10,
        ingredients=[owned, *missing],
        nutrition=NutritionValues(calories=300, protein=20, carbohydrate=30, fat=10),
        source="unit-test",
    )

    plan = ShoppingTool().build(
        RecipeMatch(
            recipe=recipe,
            owned_ingredients=[owned],
            missing_ingredients=missing,
            score=0,
        )
    )

    assert [item["name"] for item in plan.missing_ingredients] == [
        "필수1",
        "필수2",
        "권장1",
        "권장2",
        "권장3",
    ]
    assert len(plan.missing_ingredients) <= 5
    assert len(plan.shopping_list) <= 5
