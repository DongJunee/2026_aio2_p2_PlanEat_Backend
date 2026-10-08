"""BE1 코드 변경 없이 실행 가능한 BE2 Tool Hub 단위 테스트입니다."""

import asyncio
import json

from langchain_core.messages import AIMessage

from app.agent.tools.be2_models import (
    CatalogRecipe,
    IngredientGuide,
    NutritionValues,
    RecipeIngredient,
    RecipeSearchQuery,
    ToolIngredient,
)
from app.agent.tools.contracts import ToolRequest
from app.agent.tools.ingredient_validation_tool import IngredientValidationTool
from app.agent.tools.nodes import (
    build_be2_tool_call_node,
    build_meal_planning_node,
    build_recipe_recommendation_node,
    route_after_meal_planning,
    route_after_recipe_recommendation,
)
from app.agent.tools.nutrition_tool import NutritionTool
from app.agent.tools.recipe_guide_tool import InMemoryRecipeGuideRetriever, RecipeGuideTool
from app.agent.tools.recipe_tool import RecipeTool
from app.agent.tools.shopping_tool import ShoppingTool
from app.agent.tools.tool_hub import PlanEatToolHub
from app.agent.tools.tool_calls import (
    create_meal_planning_tool,
    create_recipe_recommendation_tool,
)
from app.integrations.recipe_source.csv_catalog import CsvRecipeRepository
from app.integrations.vector_store.chroma_recipe_guide import _split_front_matter, load_markdown_documents
from app.integrations.vision.openai_vision import VisionToolError, _validated_image_data
from app.schemas.chat import RecommendationData


class StaticRecipeRepository:
    """외부 API 없이 Recipe Tool의 정렬과 Hub 조합을 검증하는 저장소입니다."""

    def __init__(self, recipes: list[CatalogRecipe]) -> None:
        self._recipes = recipes
        self.queries: list[RecipeSearchQuery] = []

    async def search(self, query: RecipeSearchQuery, *, limit: int) -> list[CatalogRecipe]:
        self.queries.append(query)
        return self._recipes[:limit]


def _local_tool_hub() -> PlanEatToolHub:
    """외부 I/O 없이 ToolCall과 LangGraph 노드를 검증할 로컬 BE2 Hub입니다."""

    return PlanEatToolHub(
        recipe_tool=RecipeTool(StaticRecipeRepository(_catalog_recipes())),
        nutrition_tool=NutritionTool(),
        shopping_tool=ShoppingTool(),
        recipe_guide_tool=RecipeGuideTool(
            InMemoryRecipeGuideRetriever(
                [
                    IngredientGuide(
                        ingredient="간장",
                        substitutes=["된장"],
                        sources=["reviewed-guide.md"],
                    )
                ]
            )
        ),
    )


def _tool_call_input() -> dict[str, object]:
    return {
        "session_id": "be2-tool-call-test",
        "confirmed_ingredients": [
            {"name": "두부", "amount": "1모"},
            {"name": "양배추", "amount": "반 통"},
        ],
        "user_conditions": {"message": "다이어트 식단으로 30분 안에 만들고 싶어요."},
    }


def _catalog_recipes(count: int = 10) -> list[CatalogRecipe]:
    return [
        CatalogRecipe(
            recipe_id=f"recipe-{index}",
            title=f"두부 양배추 메뉴 {index}",
            cook_time=10 + index,
            ingredients=[
                RecipeIngredient(name="두부", amount="1모"),
                RecipeIngredient(name="양배추", amount="200g"),
                RecipeIngredient(
                    name="간장",
                    amount="1큰술",
                    importance="대체 가능",
                ),
            ],
            nutrition=NutritionValues(
                calories=300 + index,
                protein=20 + index,
                carbohydrate=15,
                fat=10,
            ),
            source="test-recipe-source",
            source_metadata={"meal_role": "main" if index < 5 else "side"},
        )
        for index in range(count)
    ]


def test_recipe_tool_uses_confirmed_ingredients_and_condition_time() -> None:
    repository = StaticRecipeRepository(_catalog_recipes())
    tool = RecipeTool(repository)
    query = RecipeSearchQuery(
        confirmed_ingredients=[ToolIngredient(name="두부", amount="1모")],
        user_conditions={"message": "고단백 메뉴를 15분 이내에 만들고 싶어요."},
    )

    results = asyncio.run(tool.recommend(query, limit=10))

    assert repository.queries == [query]
    assert [result.recipe.recipe_id for result in results] == ["recipe-5", "recipe-4", "recipe-3", "recipe-2", "recipe-1", "recipe-0"]
    assert all(result.owned_ingredients[0].name == "두부" for result in results)
    assert all(result.recipe.cook_time <= 15 for result in results)


def test_tool_hub_returns_existing_chat_recommendation_shape() -> None:
    local_hub = _local_tool_hub()
    request = ToolRequest(
        session_id="be2-unit-test",
        tool_name="recipe_recommendation",
        confirmed_ingredients=(
            {"name": "두부", "amount": "1모"},
            {"name": "양배추", "amount": "반 통"},
        ),
        user_conditions={"message": "다이어트 식단으로 30분 안에 만들고 싶어요."},
    )

    result = asyncio.run(local_hub.execute(request))

    assert result.error is None
    assert result.result is not None
    data = RecommendationData.model_validate(result.result["data"])
    assert [len(recipe_set.recipes) for recipe_set in data.recipe_sets] == [5, 5]
    assert data.recipe_sets[0].recipes[0].missing_ingredients[0].alternative == "된장"
    assert result.source_metadata == {
        "recipe_sources": ["test-recipe-source"],
        "recipe_guide_sources": ["reviewed-guide.md"],
        "recipe_count": 10,
        "planning_set_count": 5,
    }


def test_recipe_recommendation_tool_call_returns_json_payload() -> None:
    tool = create_recipe_recommendation_tool(_local_tool_hub())

    raw_result = asyncio.run(tool.ainvoke(_tool_call_input()))

    payload = json.loads(raw_result)
    assert payload["ok"] is True
    assert payload["error"] is None
    assert len(payload["result"]["data"]["recipe_sets"]) == 2
    assert payload["source_metadata"]["recipe_count"] == 10


def test_meal_planning_tool_call_returns_five_main_side_sets() -> None:
    tool = create_meal_planning_tool(_local_tool_hub())
    tool_input = _tool_call_input()
    tool_input["excluded_ingredients"] = ["간장"]

    raw_result = asyncio.run(tool.ainvoke(tool_input))

    payload = json.loads(raw_result)
    assert payload["ok"] is True
    assert len(payload["result"]["recipe_sets"]) == 5
    assert all(
        meal_set["main_recipe"]["recipe_id"] != meal_set["side_recipe"]["recipe_id"]
        for meal_set in payload["result"]["recipe_sets"]
    )
    assert payload["source_metadata"]["planning_set_count"] == 5


def test_be2_tool_call_node_executes_langchain_ai_message_tool_call() -> None:
    node = build_be2_tool_call_node(_local_tool_hub())
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "recipe_recommendation",
                "args": _tool_call_input(),
                "id": "recipe-call-1",
            }
        ],
    )

    result = asyncio.run(node.ainvoke({"messages": [message]}))

    tool_message = result["messages"][0]
    payload = json.loads(tool_message.content)
    assert tool_message.tool_call_id == "recipe-call-1"
    assert payload["ok"] is True


def test_recipe_recommendation_node_exposes_result_for_be1_state_graph() -> None:
    node = build_recipe_recommendation_node(_local_tool_hub())

    result = asyncio.run(node(_tool_call_input()))

    assert result["be2_tool_error"] is None
    assert isinstance(result["be2_tool_result"], dict)
    assert route_after_recipe_recommendation(result) == "tool_succeeded"

    failed_result = asyncio.run(
        node(
            {
                "session_id": "missing-conditions",
                "confirmed_ingredients": [{"name": "두부", "amount": "1모"}],
                "user_conditions": {},
            }
        )
    )
    assert failed_result["be2_tool_result"] is None
    assert route_after_recipe_recommendation(failed_result) == "tool_failed"


def test_meal_planning_node_exposes_new_be2_result_without_changing_be1_state() -> None:
    node = build_meal_planning_node(_local_tool_hub())
    tool_input = _tool_call_input()
    tool_input["excluded_ingredients"] = ["간장"]

    result = asyncio.run(node(tool_input))

    assert result["meal_plan_error"] is None
    assert len(result["meal_plan_result"]["recipe_sets"]) == 5
    assert route_after_meal_planning(result) == "tool_succeeded"


def test_tool_hub_does_not_fill_missing_recipes_with_fake_duplicates() -> None:
    hub = PlanEatToolHub(
        recipe_tool=RecipeTool(StaticRecipeRepository(_catalog_recipes(count=9))),
        nutrition_tool=NutritionTool(),
        shopping_tool=ShoppingTool(),
        recipe_guide_tool=RecipeGuideTool(InMemoryRecipeGuideRetriever()),
    )
    request = ToolRequest(
        session_id="insufficient-candidates",
        tool_name="recipe_recommendation",
        confirmed_ingredients=({"name": "두부", "amount": "1모"},),
        user_conditions={"message": "20분 이내"},
    )

    result = asyncio.run(hub.execute(request))

    assert result.result is None
    assert result.error == "조건에 맞는 레시피 후보가 충분하지 않습니다."


def test_internal_csv_repository_preserves_enriched_ingredient_importance(tmp_path) -> None:
    catalog_path = tmp_path / "recipe-catalog.csv"
    catalog_path.write_text(
        "RCP_SEQ,RCP_NM,RCP_PAT2,RCP_PARTS_DTLS,REQUIRED_INGREDIENTS,"
        "OPTIONAL_INGREDIENTS,SUBSTITUTABLE_INGREDIENTS,SUBSTITUTABLE_SEASONINGS,"
        "INFO_ENG,INFO_PRO,INFO_CAR,INFO_FAT\n"
        "1,두부 찜,반찬,두부 1모,두부|달걀,후추,생크림,간장,320,20,18,15\n",
        encoding="utf-8",
    )
    repository = CsvRecipeRepository(catalog_path)
    query = RecipeSearchQuery(
        confirmed_ingredients=[ToolIngredient(name="두부", amount="1모")],
        user_conditions={"message": "20분 이내"},
    )

    recipes = asyncio.run(repository.search(query, limit=10))

    assert recipes[0].source_metadata["meal_role"] == "side"
    assert recipes[0].source == "internal:recipe-catalog"
    assert recipes[0].nutrition == NutritionValues(calories=320, protein=20, carbohydrate=18, fat=15)
    assert [(item.name, item.importance) for item in recipes[0].ingredients] == [
        ("두부", "필수"),
        ("달걀", "필수"),
        ("생크림", "대체 가능"),
        ("간장", "대체 가능"),
        ("후추", "생략 가능"),
    ]


def test_markdown_guide_loader_and_front_matter(tmp_path) -> None:
    guide_file = tmp_path / "soy-sauce.md"
    guide_file.write_text(
        "---\ningredient: 간장\nsource: reviewed-source\nsubstitutes: 된장, 액젓\n"
        "storage_tip: 서늘한 곳\nflavor_note: 감칠맛이 달라집니다\n---\n볶음 요리에 소량 사용합니다.",
        encoding="utf-8",
    )

    metadata, content = _split_front_matter(guide_file.read_text(encoding="utf-8"))
    documents = load_markdown_documents(tmp_path)

    assert metadata["ingredient"] == "간장"
    assert content == "볶음 요리에 소량 사용합니다."
    assert documents[0].substitutes == ("된장", "액젓")


def test_vision_rejects_untrusted_remote_image_url_by_default() -> None:
    try:
        _validated_image_data(
            {"type": "image", "data": "https://untrusted.example/fridge.png"}, frozenset()
        )
    except VisionToolError:
        pass
    else:  # pragma: no cover - 보안 경계가 풀리면 테스트가 실패해야 한다.
        raise AssertionError("allowlist 없는 원격 URL을 거절해야 합니다.")

    assert _validated_image_data(
        {"type": "image", "data": "data:image/png;base64,AAAA"}, frozenset()
    ).startswith("data:image/")


def test_ingredient_validation_normalizes_only_clear_aliases_and_duplicates() -> None:
    normalized = IngredientValidationTool().normalize(
        [
            ToolIngredient(name="달걀", amount="수량 미상"),
            ToolIngredient(name="계란", amount="4개"),
            ToolIngredient(name="  두부 ", amount="1모"),
        ]
    )

    assert normalized == [
        ToolIngredient(name="계란", amount="4개"),
        ToolIngredient(name="두부", amount="1모"),
    ]
