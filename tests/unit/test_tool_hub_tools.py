"""Orchestrator 코드 변경 없이 실행 가능한 Tool Hub 단위 테스트입니다."""

import asyncio
import json

from langchain_core.messages import AIMessage

from app.agent.tools.tool_models import (
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
    build_tool_hub_call_node,
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
from app.integrations.vector_store.chroma_recipe_catalog import ChromaRecipeRepository
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


class FakeChromaCollection:
    """Chroma 서버 없이 적재·검색 어댑터 경계를 검증하는 테스트 대역입니다."""

    def __init__(self, recipe_ids: list[str]) -> None:
        self._recipe_ids = recipe_ids
        self.upserted_ids: list[str] = []

    def upsert(self, *, ids, documents, metadatas) -> None:
        self.upserted_ids.extend(ids)

    def query(self, *, query_texts, n_results, include) -> dict[str, list[list[str]]]:
        return {"ids": [self._recipe_ids[:n_results]]}


def _local_tool_hub() -> PlanEatToolHub:
    """외부 I/O 없이 ToolCall과 LangGraph 노드를 검증할 로컬 Tool Hub입니다."""

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
        "session_id": "tool-hub-tool-call-test",
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


def test_recipe_tool_prioritizes_recipes_that_use_confirmed_ingredients() -> None:
    repository = StaticRecipeRepository(
        [
            CatalogRecipe(
                recipe_id="generic",
                title="간단한 반찬",
                cook_time=5,
                ingredients=[RecipeIngredient(name="소금", amount="1꼬집")],
                nutrition=NutritionValues(calories=30, protein=1, carbohydrate=2, fat=1),
                source="test-recipe-source",
            ),
            CatalogRecipe(
                recipe_id="uses-tofu",
                title="두부 듬뿍 찜",
                cook_time=20,
                ingredients=[
                    RecipeIngredient(name="두부", amount="1모"),
                    RecipeIngredient(name="간장", amount="1큰술"),
                    RecipeIngredient(name="대파", amount="1대"),
                    RecipeIngredient(name="참기름", amount="1작은술"),
                ],
                nutrition=NutritionValues(calories=260, protein=18, carbohydrate=10, fat=16),
                source="test-recipe-source",
            ),
        ]
    )

    results = asyncio.run(
        RecipeTool(repository).recommend(
            RecipeSearchQuery(
                confirmed_ingredients=[ToolIngredient(name="두부", amount="1모")],
                user_conditions={"message": "간단한 메뉴"},
            ),
            limit=2,
        )
    )

    assert [result.recipe.recipe_id for result in results] == ["uses-tofu", "generic"]
    assert results[0].owned_ingredients[0].name == "두부"


def test_tool_hub_returns_existing_chat_recommendation_shape() -> None:
    local_hub = _local_tool_hub()
    request = ToolRequest(
        session_id="tool-hub-unit-test",
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


def test_tool_hub_places_owned_ingredient_recipes_in_best_match_set() -> None:
    recipes = _catalog_recipes()
    recipes[0] = recipes[0].model_copy(
        update={"ingredients": [RecipeIngredient(name="소금", amount="1꼬집")]}
    )
    hub = PlanEatToolHub(
        recipe_tool=RecipeTool(StaticRecipeRepository(recipes)),
        nutrition_tool=NutritionTool(),
        shopping_tool=ShoppingTool(),
        recipe_guide_tool=RecipeGuideTool(InMemoryRecipeGuideRetriever()),
    )
    request = ToolRequest(
        session_id="best-match-owned-first",
        tool_name="recipe_recommendation",
        confirmed_ingredients=({"name": "두부", "amount": "1모"},),
        user_conditions={"message": "간단한 메뉴"},
    )

    result = asyncio.run(hub.execute(request))

    assert result.result is not None
    best_match = result.result["data"]["recipe_sets"][0]["recipes"]
    assert all(recipe["owned_ingredients"] == ["두부"] for recipe in best_match)


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


def test_tool_hub_call_node_executes_langchain_ai_message_tool_call() -> None:
    node = build_tool_hub_call_node(_local_tool_hub())
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


def test_recipe_recommendation_node_exposes_result_for_orchestrator_state_graph() -> None:
    node = build_recipe_recommendation_node(_local_tool_hub())

    result = asyncio.run(node(_tool_call_input()))

    assert result["tool_error"] is None
    assert isinstance(result["tool_result"], dict)
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
    assert failed_result["tool_result"] is None
    assert route_after_recipe_recommendation(failed_result) == "tool_failed"


def test_meal_planning_node_exposes_new_tool_hub_result_without_changing_orchestrator_state() -> None:
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


def test_internal_csv_repository_prioritizes_recipes_matching_confirmed_ingredients(
    tmp_path,
) -> None:
    catalog_path = tmp_path / "recipe-catalog.csv"
    catalog_path.write_text(
        "RCP_SEQ,RCP_NM,RCP_PAT2,RCP_PARTS_DTLS,REQUIRED_INGREDIENTS,"
        "OPTIONAL_INGREDIENTS,SUBSTITUTABLE_INGREDIENTS,SUBSTITUTABLE_SEASONINGS,"
        "INFO_ENG,INFO_PRO,INFO_CAR,INFO_FAT\n"
        "1,닭고기 요리,반찬,닭고기 1개,닭고기,,,,320,20,18,15\n"
        "2,두부 찜,반찬,두부 1모,두부,,,,280,18,16,12\n",
        encoding="utf-8",
    )
    repository = CsvRecipeRepository(catalog_path)
    query = RecipeSearchQuery(
        confirmed_ingredients=[ToolIngredient(name="두부", amount="1모")],
        user_conditions={"purpose": "다이어트"},
    )

    recipes = asyncio.run(repository.search(query, limit=1))

    assert [recipe.recipe_id for recipe in recipes] == ["2"]


def test_chroma_recipe_repository_merges_semantic_and_exact_candidates(tmp_path) -> None:
    catalog_path = tmp_path / "recipe-catalog.csv"
    catalog_path.write_text(
        "recipe_id,title,image,cook_time,ingredients,calories,protein,carbohydrate,fat,source\n"
        'exact,두부 찜,,10,"[{""name"": ""두부"", ""amount"": ""1모""}]",200,20,10,10,test\n'
        'semantic,만두국,,15,"[{""name"": ""대파"", ""amount"": ""1대""}]",250,15,25,8,test\n',
        encoding="utf-8",
    )
    catalog = CsvRecipeRepository(catalog_path)
    collection = FakeChromaCollection(["semantic"])
    repository = ChromaRecipeRepository(collection, catalog)
    query = RecipeSearchQuery(
        confirmed_ingredients=[ToolIngredient(name="두부", amount="1모")],
        user_conditions={"message": "만두 같은 따뜻한 메뉴"},
    )

    count = asyncio.run(repository.upsert_catalog())
    recipes = asyncio.run(repository.search(query, limit=2))

    assert count == 2
    assert collection.upserted_ids == ["exact", "semantic"]
    assert [recipe.recipe_id for recipe in recipes] == ["exact", "semantic"]


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
