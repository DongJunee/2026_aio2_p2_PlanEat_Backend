"""Recipe·Nutrition·Shopping·RAG Tool을 조합하는 독립 Tool Hub입니다."""

from collections.abc import Mapping

from pydantic import ValidationError

from app.agent.tools.be2_models import (
    IngredientGuide,
    RecipeMatch,
    ToolRecommendationData,
    ToolRecipeCard,
    ToolRecipeSet,
)
from app.agent.tools.contracts import ToolRequest, ToolResult
from app.agent.tools.nutrition_tool import NutritionTool
from app.agent.tools.recipe_guide_tool import RecipeGuideTool
from app.agent.tools.recipe_tool import RecipeTool, query_from_tool_request
from app.agent.tools.shopping_tool import ShoppingTool

_SUPPORTED_TOOL_NAME = "recipe_recommendation"
_REQUIRED_RECIPE_COUNT = 10


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
    ) -> None:
        self._recipe_tool = recipe_tool
        self._nutrition_tool = nutrition_tool
        self._shopping_tool = shopping_tool
        self._recipe_guide_tool = recipe_guide_tool

    async def execute(self, request: ToolRequest) -> ToolResult:
        """확정 재료만 사용해 FE 호환 추천 데이터와 가이드 출처를 반환합니다."""

        if request.tool_name != _SUPPORTED_TOOL_NAME:
            return ToolResult(error="지원하지 않는 Tool 요청입니다.")

        try:
            query = query_from_tool_request(
                request.confirmed_ingredients, dict(request.user_conditions)
            )
            matches = await self._recipe_tool.recommend(query, limit=_REQUIRED_RECIPE_COUNT)
        except (ValidationError, ValueError, TypeError, RuntimeError):
            # 외부 서비스 오류·세부 재료값을 API로 유출하지 않는다.
            return ToolResult(error="레시피 후보를 준비하지 못했습니다.")

        if len(matches) < _REQUIRED_RECIPE_COUNT:
            # FE 계약은 항상 2세트 × 5개다. 부족한 결과를 복제·생성하지 않는다.
            return ToolResult(error="조건에 맞는 레시피 후보가 충분하지 않습니다.")

        guides = await self._recipe_guide_tool.find_guides(
            ingredient.name for match in matches for ingredient in match.missing_ingredients
        )
        cards = [self._to_card(match, guides) for match in matches]
        data = ToolRecommendationData(
            recipe_sets=[
                ToolRecipeSet(set_id="BEST_MATCH", recipes=cards[:5]),
                ToolRecipeSet(set_id="MORE_OPTIONS", recipes=cards[5:10]),
            ]
        )
        source_names = sorted({match.recipe.source for match in matches})
        guide_sources = sorted(
            {source for guide in guides.values() for source in guide.sources if source.strip()}
        )
        return ToolResult(
            result={
                "response": "확정한 재료와 조건에 맞는 레시피를 추천했습니다.",
                "data": data.model_dump(),
            },
            source_metadata={
                "recipe_sources": source_names,
                "recipe_guide_sources": guide_sources,
                "recipe_count": len(cards),
            },
        )

    def _to_card(self, match: RecipeMatch, guides: Mapping[str, IngredientGuide]) -> ToolRecipeCard:
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
