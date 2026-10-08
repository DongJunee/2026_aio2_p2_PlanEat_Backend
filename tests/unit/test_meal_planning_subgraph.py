"""Orchestrator을 수정하지 않는 MealPlanningSubgraph 단위 테스트입니다."""

import asyncio
from collections.abc import Mapping, Sequence

from app.agent.tools.tool_models import (
    CatalogRecipe,
    IngredientGuide,
    NutritionValues,
    RecipeIngredient,
    RecipeSearchQuery,
)
from app.agent.tools.meal_planning_subgraph import (
    ExcludedIngredientDecision,
    MealPlanningRequest,
    MealPlanningSubgraph,
    MealSetCandidate,
)
from app.agent.tools.nutrition_tool import NutritionTool
from app.agent.tools.recipe_guide_tool import InMemoryRecipeGuideRetriever, RecipeGuideTool
from app.agent.tools.recipe_tool import RecipeTool
from app.agent.tools.shopping_tool import ShoppingTool


class SequencedRecipeRepository:
    """호출 순서와 재계획 여부를 확인하는 외부 I/O 없는 Recipe Source입니다."""

    def __init__(self, responses: list[list[CatalogRecipe]], events: list[str]) -> None:
        self._responses = responses
        self._events = events
        self.search_count = 0

    async def search(self, query: RecipeSearchQuery, *, limit: int) -> list[CatalogRecipe]:
        del query, limit
        self._events.append("recipe")
        response = self._responses[min(self.search_count, len(self._responses) - 1)]
        self.search_count += 1
        return response


class RecordingComposer:
    """테스트가 조합 단계가 Jev보다 먼저 실행됐음을 확인하게 합니다."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def compose(self, candidates):
        self._events.append("combine")
        pairs = []
        for offset in (0, 1):
            for index in range(5):
                pairs.append(
                    MealSetCandidate(
                        set_id=f"SET-{len(pairs) + 1:03d}",
                        main_match=candidates[index],
                        side_match=candidates[5 + ((index + offset) % 5)],
                    )
                )
        return pairs


class RecordingJev:
    """TypeSafe Jev를 호출하지 않고 선택·제외 판정을 재현하는 fake입니다."""

    def __init__(
        self,
        events: list[str],
        decisions: Mapping[str, str] | None = None,
    ) -> None:
        self._events = events
        self._decisions = decisions or {}
        self.classification_count = 0

    async def select_sets(self, candidates, user_conditions):
        del user_conditions
        self._events.append("jev")
        return [candidate.set_id for candidate in candidates[:5]]

    async def classify_excluded_ingredients(self, excluded_ingredients, selected_sets):
        del selected_sets
        self._events.append("jev_exclusion")
        self.classification_count += 1
        return [
            ExcludedIngredientDecision(
                ingredient=ingredient,
                disposition=self._decisions[ingredient],  # type: ignore[arg-type]
            )
            for ingredient in excluded_ingredients
            if ingredient in self._decisions
        ]


class RecordingShoppingTool(ShoppingTool):
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def build(self, match, guides_by_ingredient=None):
        self._events.append("shopping")
        return super().build(match, guides_by_ingredient)


class RecordingNutritionTool(NutritionTool):
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def calculate(self, recipe):
        self._events.append("nutrition")
        return super().calculate(recipe)


def _recipes(*, required_exclusion: bool = False) -> list[CatalogRecipe]:
    """5개 메인·5개 반찬 후보를 만든다. 각 레시피는 장보기 6개를 요구한다."""

    exclusion = RecipeIngredient(
        name="새우" if required_exclusion else "간장",
        amount="1큰술",
        importance="필수" if required_exclusion else "대체 가능",
    )
    shopping_ingredients = [
        exclusion,
        RecipeIngredient(name="양파", amount="1개", importance="권장"),
        RecipeIngredient(name="당근", amount="1/2개", importance="권장"),
        RecipeIngredient(name="마늘", amount="1쪽", importance="권장"),
        RecipeIngredient(name="참기름", amount="1작은술", importance="권장"),
        RecipeIngredient(name="깨", amount="1작은술", importance="권장"),
    ]
    recipes: list[CatalogRecipe] = []
    for index in range(10):
        role = "main" if index < 5 else "side"
        recipes.append(
            CatalogRecipe(
                recipe_id=f"recipe-{index}",
                title=f"{role}-{index}",
                cook_time=10 + index,
                ingredients=[
                    RecipeIngredient(name="두부", amount="1모", importance="필수"),
                    *shopping_ingredients,
                ],
                nutrition=NutritionValues(
                    calories=100 + index,
                    protein=10 + index,
                    carbohydrate=20,
                    fat=5,
                ),
                source="unit-test",
                source_metadata={"meal_role": role},
            )
        )
    return recipes


def _request(excluded_ingredients: list[str] | None = None) -> MealPlanningRequest:
    return MealPlanningRequest(
        confirmed_ingredients=[{"name": "두부", "amount": "1모"}],
        user_conditions={"message": "20분 안에 고단백 식단"},
        excluded_ingredients=excluded_ingredients or [],
    )


def _subgraph(
    repository: SequencedRecipeRepository,
    jev: RecordingJev,
    events: list[str],
) -> MealPlanningSubgraph:
    return MealPlanningSubgraph(
        recipe_tool=RecipeTool(repository),
        shopping_tool=RecordingShoppingTool(events),
        nutrition_tool=RecordingNutritionTool(events),
        jev=jev,
        recipe_guide_tool=RecipeGuideTool(
            InMemoryRecipeGuideRetriever(
                [IngredientGuide(ingredient="간장", substitutes=["된장"])]
            )
        ),
        pair_composer=RecordingComposer(events),
    )


def test_subgraph_runs_recipe_combination_jev_shopping_nutrition_in_order() -> None:
    events: list[str] = []
    repository = SequencedRecipeRepository([_recipes()], events)
    subgraph = _subgraph(repository, RecordingJev(events), events)

    result = asyncio.run(subgraph.execute(_request()))

    # 단계 내부에서 세트별 Shopping/Nutrition 호출은 반복되지만, 최초 순서는 고정된다.
    condensed = [event for index, event in enumerate(events) if index == 0 or event != events[index - 1]]
    assert condensed == ["recipe", "combine", "jev", "shopping", "nutrition"]
    assert len(result.recipe_sets) == 5
    assert all(meal_set.main_recipe.recipe_id != meal_set.side_recipe.recipe_id for meal_set in result.recipe_sets)
    assert len({meal_set.main_recipe.recipe_id for meal_set in result.recipe_sets}) == 5
    assert len({meal_set.side_recipe.recipe_id for meal_set in result.recipe_sets}) == 5
    assert all(len(meal_set.shopping_list) == 5 for meal_set in result.recipe_sets)
    first_set = result.recipe_sets[0]
    assert first_set.nutrition.calories == (
        first_set.main_recipe.nutrition.calories + first_set.side_recipe.nutrition.calories
    )


def test_replaceable_exclusion_changes_only_shopping_with_jev_judgement() -> None:
    events: list[str] = []
    repository = SequencedRecipeRepository([_recipes()], events)
    subgraph = _subgraph(
        repository,
        RecordingJev(events, {"간장": "대체 가능"}),
        events,
    )

    result = asyncio.run(subgraph.execute(_request(["간장"])))

    assert repository.search_count == 1
    assert result.replanned is False
    assert all(
        all(item.ingredient != "간장" for item in meal_set.shopping_list)
        for meal_set in result.recipe_sets
    )
    assert any(
        action.ingredient == "간장"
        and action.action == "shopping_only"
        and action.replacement == "된장"
        for action in result.exclusion_actions
    )


def test_required_exclusion_replans_recipe_and_shopping_before_nutrition() -> None:
    events: list[str] = []
    repository = SequencedRecipeRepository(
        [_recipes(required_exclusion=True), _recipes(required_exclusion=False)], events
    )
    subgraph = _subgraph(
        repository,
        RecordingJev(events, {"새우": "필수"}),
        events,
    )

    result = asyncio.run(subgraph.execute(_request(["새우"])))

    assert repository.search_count == 2
    assert result.replanned is True
    assert all(
        all(ingredient.name != "새우" for ingredient in meal_set.main_recipe.ingredients)
        and all(ingredient.name != "새우" for ingredient in meal_set.side_recipe.ingredients)
        for meal_set in result.recipe_sets
    )
    assert any(
        action.ingredient == "새우" and action.action == "recipe_and_shopping_replanned"
        for action in result.exclusion_actions
    )
    assert events.index("nutrition") > events.index("shopping")
