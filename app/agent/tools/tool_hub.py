"""Recipe·Nutrition·Shopping·RAG Tool을 조합하는 독립 Tool Hub입니다."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from app.agent.tools.be2_models import (
    CatalogRecipe,
    IngredientGuide,
    RecipeMatch,
    ToolIngredient,
    ToolRecommendationData,
    ToolRecipeCard,
    ToolRecipeSet,
)
from app.agent.tools.contracts import ToolRequest, ToolResult
from app.agent.tools.meal_planning_subgraph import (
    MealPairComposer,
    MealPlanningError,
    MealPlanningJev,
    MealPlanningRequest,
    MealPlanningResult,
    MealPlanningSubgraph,
    TypeSafeMealPlanningJev,
)
from app.agent.tools.nutrition_tool import NutritionTool
from app.agent.tools.recipe_guide_tool import RecipeGuideTool
from app.agent.tools.recipe_tool import RecipeTool
from app.agent.tools.shopping_tool import ShoppingTool
from app.core.config import get_settings

_LEGACY_TOOL_NAME = "recipe_recommendation"


class PlanEatToolHub:
    """BE1의 ``ToolHubProvider`` 프로토콜과 호환되는 BE2 구현체입니다.

    이 클래스는 아직 전역 ``chat_service``에 주입되지 않는다. 독립 테스트와 실제
    데이터 적재가 끝난 뒤 BE1이 생성자 주입만 하면 실행할 수 있도록 구성한다.
    """

    def __init__(
        self,
        *,
        recipe_tool: RecipeTool,
        nutrition_tool: NutritionTool,
        shopping_tool: ShoppingTool,
        recipe_guide_tool: RecipeGuideTool,
        jev: MealPlanningJev | None = None,
        pair_composer: MealPairComposer | None = None,
    ) -> None:
        self._recipe_tool = recipe_tool
        self._nutrition_tool = nutrition_tool
        self._shopping_tool = shopping_tool
        self._recipe_guide_tool = recipe_guide_tool
        self._meal_planning = MealPlanningSubgraph(
            recipe_tool=recipe_tool,
            nutrition_tool=nutrition_tool,
            shopping_tool=shopping_tool,
            recipe_guide_tool=recipe_guide_tool,
            jev=jev or TypeSafeMealPlanningJev(get_settings()),
            pair_composer=pair_composer,
        )

    @classmethod
    def from_local_catalog(
        cls,
        *,
        recipe_guide_tool: RecipeGuideTool,
        catalog_path: str | Path | None = None,
        jev: MealPlanningJev | None = None,
        pair_composer: MealPairComposer | None = None,
    ) -> "PlanEatToolHub":
        """내부 ``data/`` CSV 카탈로그를 사용하는 Tool Hub를 만듭니다.

        경로를 생략하면 보강된 내부 레시피 CSV를 사용한다. 단위 테스트처럼 작은
        fixture를 쓸 때는 ``catalog_path``에 별도 fixture CSV 경로를 명시한다.
        """

        from app.integrations.recipe_source.csv_catalog import CsvRecipeRepository

        repository = (
            CsvRecipeRepository(catalog_path)
            if catalog_path is not None
            else CsvRecipeRepository.from_internal_catalog()
        )
        return cls(
            recipe_tool=RecipeTool(repository),
            nutrition_tool=NutritionTool(),
            shopping_tool=ShoppingTool(),
            recipe_guide_tool=recipe_guide_tool,
            jev=jev,
            pair_composer=pair_composer,
        )

    async def execute_meal_plan(self, request: MealPlanningRequest) -> MealPlanningResult:
        """새 BE2 계약으로 5개의 메인·반찬 세트를 반환합니다."""

        return await self._meal_planning.execute(request)

    async def execute(self, request: ToolRequest) -> ToolResult:
        """기존 BE1 ``ToolRequest``를 Meal Planning 결과로 호환 변환합니다."""

        if request.tool_name != _LEGACY_TOOL_NAME:
            return ToolResult(error="지원하지 않는 Tool 요청입니다.")

        try:
            plan_request = MealPlanningRequest(
                confirmed_ingredients=[
                    ToolIngredient.model_validate(ingredient)
                    for ingredient in request.confirmed_ingredients
                ],
                user_conditions=dict(request.user_conditions),
            )
            meal_plan = await self.execute_meal_plan(plan_request)
            data, source_metadata = await self._legacy_response_data(meal_plan, plan_request)
        except MealPlanningError:
            return ToolResult(error="조건에 맞는 레시피 후보가 충분하지 않습니다.")
        except (ValidationError, ValueError, TypeError, RuntimeError):
            # Tool 경계에서 외부 API·Jev·카탈로그 세부 오류를 BE1 또는 FE로 노출하지 않는다.
            return ToolResult(error="조건에 맞는 레시피 후보를 준비하지 못했습니다.")
        return ToolResult(
            result={
                "response": "확정한 재료와 조건에 맞는 메인·반찬 식단을 추천했습니다.",
                "data": data.model_dump(),
            },
            source_metadata=source_metadata,
        )

    async def _legacy_response_data(
        self,
        meal_plan: MealPlanningResult,
        request: MealPlanningRequest,
    ) -> tuple[ToolRecommendationData, dict[str, object]]:
        """정식 Meal Planning 결과를 현행 BE1의 2×5 카드 DTO로만 변환합니다."""

        recipes = [
            recipe
            for meal_set in meal_plan.recipe_sets
            for recipe in (meal_set.main_recipe, meal_set.side_recipe)
        ]
        matches = [self._match_recipe(recipe, request.confirmed_ingredients) for recipe in recipes]
        guides = await self._recipe_guide_tool.find_guides(
            ingredient.name for match in matches for ingredient in match.missing_ingredients
        )
        cards = [self._to_legacy_card(match, guides) for match in matches]
        data = ToolRecommendationData(
            recipe_sets=[
                ToolRecipeSet(set_id="BEST_MATCH", recipes=cards[:5]),
                ToolRecipeSet(set_id="MORE_OPTIONS", recipes=cards[5:10]),
            ]
        )
        return data, {
            "recipe_sources": sorted({recipe.source for recipe in recipes}),
            "recipe_guide_sources": sorted(
                {
                    source
                    for guide in guides.values()
                    for source in guide.sources
                    if source.strip()
                }
            ),
            "recipe_count": len(cards),
            "planning_set_count": len(meal_plan.recipe_sets),
        }

    @staticmethod
    def _match_recipe(
        recipe: CatalogRecipe, confirmed_ingredients: Sequence[ToolIngredient]
    ) -> RecipeMatch:
        owned_names = {_ingredient_key(ingredient.name) for ingredient in confirmed_ingredients}
        owned = [
            ingredient
            for ingredient in recipe.ingredients
            if _ingredient_key(ingredient.name) in owned_names
        ]
        missing = [
            ingredient
            for ingredient in recipe.ingredients
            if _ingredient_key(ingredient.name) not in owned_names
        ]
        return RecipeMatch(
            recipe=recipe,
            owned_ingredients=owned,
            missing_ingredients=missing,
            score=0,
        )

    def _to_legacy_card(
        self, match: RecipeMatch, guides: Mapping[str, IngredientGuide]
    ) -> ToolRecipeCard:
        nutrition = self._nutrition_tool.calculate(match.recipe)
        shopping = self._shopping_tool.build(match, guides)
        return ToolRecipeCard(
            recipe_id=match.recipe.recipe_id,
            title=match.recipe.title,
            image=match.recipe.image,
            cook_time=match.recipe.cook_time,
            owned_ingredients=[item.name for item in match.owned_ingredients],
            missing_ingredients=shopping.missing_ingredients,
            shopping_list=shopping.shopping_list,
            nutrition=nutrition.values,
        )


def _ingredient_key(name: str) -> str:
    """호환 DTO 변환에서만 사용하는 표기 차이 제거입니다."""

    return "".join(name.split()).lower()
