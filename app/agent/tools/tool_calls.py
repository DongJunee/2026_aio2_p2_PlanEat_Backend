"""Tool Hub를 LangChain Tool Calling 인터페이스로 노출합니다.

이 모듈은 Orchestrator의 ChatService나 LangGraph 상태를 변경하지 않는다. 오케스트레이터는
``create_tool_hub_tools()``가 반환한 도구를 LLM에 bind하거나 ToolNode에 전달할 수 있다.
"""

import json
from typing import Any, Protocol

from langchain_core.tools import BaseTool, tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools.tool_models import ToolIngredient
from app.agent.tools.contracts import ToolRequest
from app.agent.tools.contracts import ToolResult
from app.agent.tools.meal_planning_subgraph import MealPlanningError, MealPlanningRequest
from app.agent.tools.tool_hub import PlanEatToolHub


class ToolRequestExecutor(Protocol):
    """Orchestrator 완료 단계에서 실행할 ToolRequest provider의 최소 계약입니다."""

    async def execute(
        self, request: ToolRequest, *, config: RunnableConfig | None = None
    ) -> ToolResult:
        """확정 재료와 조건으로 Tool 결과를 반환합니다."""


class RecipeRecommendationToolInput(BaseModel):
    """LLM이 ``recipe_recommendation`` ToolCall에 넘겨야 하는 확정 입력입니다."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    confirmed_ingredients: list[ToolIngredient] = Field(min_length=1)
    user_conditions: dict[str, Any] = Field(min_length=1)


class MealPlanningToolInput(RecipeRecommendationToolInput):
    """5개 메인·반찬 세트를 위한 Tool Hub의 정식 ToolCall 입력입니다."""

    excluded_ingredients: list[str] = Field(default_factory=list)


def create_recipe_recommendation_tool(tool_hub: ToolRequestExecutor) -> BaseTool:
    """Tool Hub의 레시피·영양·장보기·RAG 결과를 반환하는 비동기 Tool을 만듭니다.

    결과는 항상 JSON 문자열이다. ``ok``가 ``true``이면 ``result``가 기존 Chat API의
    RecommendationData와 호환되고, 실패하면 세부 예외 대신 안전한 ``error``만 담긴다.
    """

    @tool(
        "recipe_recommendation",
        args_schema=RecipeRecommendationToolInput,
        description=(
            "사용자가 확인한 재료와 식단 조건으로 레시피 10개, 영양 정보, "
            "장보기 목록, 재료 가이드를 조회한다. 이미지 후보 재료는 전달하지 않는다."
        ),
    )
    async def recipe_recommendation(
        session_id: str,
        confirmed_ingredients: list[ToolIngredient],
        user_conditions: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> str:
        """확정 재료가 있는 경우에만 Tool Hub를 실행합니다."""

        request = ToolRequest(
            session_id=session_id,
            tool_name="recipe_recommendation",
            confirmed_ingredients=tuple(
                ingredient.model_dump() for ingredient in confirmed_ingredients
            ),
            user_conditions=user_conditions,
        )
        result = await tool_hub.execute(request, config=config)
        return json.dumps(
            {
                "ok": result.error is None,
                "result": result.result if result.error is None else None,
                "source_metadata": result.source_metadata if result.error is None else None,
                "error": result.error,
            },
            ensure_ascii=False,
        )

    return recipe_recommendation


def create_meal_planning_tool(tool_hub: PlanEatToolHub) -> BaseTool:
    """제외 재료까지 반영한 5개 메인·반찬 세트를 반환하는 정식 Tool Hub입니다."""

    @tool(
        "meal_planning",
        args_schema=MealPlanningToolInput,
        description=(
            "사용자 확인 재료와 식단 조건으로 메인 1개·반찬 1개인 식단 5세트를 만든다. "
            "OpenAI가 사용자 자연어에서 추출한 제외 재료도 전달한다."
        ),
    )
    async def meal_planning(
        session_id: str,
        confirmed_ingredients: list[ToolIngredient],
        user_conditions: dict[str, Any],
        excluded_ingredients: list[str],
        config: RunnableConfig | None = None,
    ) -> str:
        """MealPlanningSubgraph의 결과를 Tool Calling JSON envelope로 감쌉니다."""

        del session_id  # 이 직접 Tool은 세션 저장을 담당하지 않고 호출 상관관계만 받는다.
        try:
            result = await tool_hub.execute_meal_plan(
                MealPlanningRequest(
                    confirmed_ingredients=confirmed_ingredients,
                    user_conditions=user_conditions,
                    excluded_ingredients=excluded_ingredients,
                ),
                config=config,
            )
        except (MealPlanningError, ValueError, TypeError, RuntimeError):
            return _tool_response(error="식단 세트를 준비하지 못했습니다.")

        recipe_sources = sorted(
            {
                recipe.source
                for meal_set in result.recipe_sets
                for recipe in (meal_set.main_recipe, meal_set.side_recipe)
            }
        )
        return _tool_response(
            result=result.model_dump(),
            source_metadata={
                "recipe_sources": recipe_sources,
                "planning_set_count": len(result.recipe_sets),
                "replanned": result.replanned,
            },
        )

    return meal_planning


def create_tool_hub_tools(tool_hub: PlanEatToolHub) -> list[BaseTool]:
    """호환용 추천 Tool과 정식 Meal Planning Tool을 함께 반환합니다."""

    return [
        create_recipe_recommendation_tool(tool_hub),
        create_meal_planning_tool(tool_hub),
    ]


def _tool_response(
    *,
    result: dict[str, object] | None = None,
    source_metadata: dict[str, object] | None = None,
    error: str | None = None,
) -> str:
    return json.dumps(
        {
            "ok": error is None,
            "result": result if error is None else None,
            "source_metadata": source_metadata if error is None else None,
            "error": error,
        },
        ensure_ascii=False,
    )
