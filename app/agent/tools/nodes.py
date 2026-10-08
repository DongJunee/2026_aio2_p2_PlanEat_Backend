"""BE2 Tool Calling 결과를 LangGraph에 연결하기 위한 독립 노드입니다.

BE1의 기존 ``ChatState``를 수정하지 않는다. 표준 ToolNode용 메시지 상태와 현재 BE1
세션 값에서 직접 호출할 수 있는 추천 노드를 각각 제공한다.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from app.agent.tools.tool_calls import (
    create_be2_tools,
    create_meal_planning_tool,
    create_recipe_recommendation_tool,
)
from app.agent.tools.tool_hub import PlanEatToolHub


class BE2ToolNodeMessageState(TypedDict):
    """LangGraph ``ToolNode``가 요구하는 LangChain 메시지 상태입니다."""

    messages: list[BaseMessage]


class BE2RecommendationNodeState(TypedDict, total=False):
    """현재 BE1 상태에서 복사해 넣을 최소 입력과 BE2 노드의 출력 키입니다."""

    session_id: str
    confirmed_ingredients: list[dict[str, object]]
    user_conditions: dict[str, object]
    be2_tool_result: dict[str, object] | None
    be2_tool_source_metadata: dict[str, object] | None
    be2_tool_error: str | None


class BE2MealPlanningNodeState(TypedDict, total=False):
    """MealPlanningSubgraph를 호출하는 새 BE2 직접 노드의 상태입니다."""

    session_id: str
    confirmed_ingredients: list[dict[str, object]]
    user_conditions: dict[str, object]
    excluded_ingredients: list[str]
    meal_plan_result: dict[str, object] | None
    meal_plan_source_metadata: dict[str, object] | None
    meal_plan_error: str | None


def build_be2_tool_call_node(tool_hub: PlanEatToolHub) -> ToolNode:
    """AIMessage의 ToolCall을 처리하는 표준 LangGraph ``ToolNode``를 만듭니다.

    이 노드는 ``messages``에 LangChain ``AIMessage``와 ``ToolMessage``를 저장하는
    그래프에서만 사용한다. 현재 BE1의 dict 메시지 상태에는 아래 직접 호출 노드를 쓴다.
    """

    return ToolNode(
        create_be2_tools(tool_hub),
        name="be2_tool_calls",
        handle_tool_errors=True,
    )


def build_recipe_recommendation_node(
    tool_hub: PlanEatToolHub,
) -> Callable[[BE2RecommendationNodeState], Awaitable[dict[str, object]]]:
    """BE1 세션 값을 받아 BE2 ToolCall을 실행하는 비동기 노드를 만듭니다.

    성공 시 ``be2_tool_result``와 출처 메타데이터를, 실패 시 ``be2_tool_error``만
    반환한다. BE1은 이 반환값을 자신의 최종 응답 변환 노드에서 소비하면 된다.
    """

    recipe_tool = create_recipe_recommendation_tool(tool_hub)

    async def recipe_recommendation_node(
        state: BE2RecommendationNodeState,
    ) -> dict[str, object]:
        """세션 상태를 ToolCall 입력으로 검증하고 안전한 결과만 상태에 기록합니다."""

        try:
            raw_result = await recipe_tool.ainvoke(
                {
                    "session_id": state.get("session_id"),
                    "confirmed_ingredients": state.get("confirmed_ingredients"),
                    "user_conditions": state.get("user_conditions"),
                }
            )
            payload = _tool_payload(raw_result)
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError):
            return _node_error("추천 Tool 입력 또는 결과 형식이 올바르지 않습니다.")

        if payload["ok"] is not True:
            return _node_error("추천 도구 실행 중 오류가 발생했습니다.")

        result = payload.get("result")
        source_metadata = payload.get("source_metadata")
        if not isinstance(result, dict) or not isinstance(source_metadata, dict):
            return _node_error("추천 Tool 결과 형식이 올바르지 않습니다.")
        return {
            "be2_tool_result": result,
            "be2_tool_source_metadata": source_metadata,
            "be2_tool_error": None,
        }

    return recipe_recommendation_node


def route_after_recipe_recommendation(
    state: BE2RecommendationNodeState,
) -> Literal["tool_succeeded", "tool_failed"]:
    """BE1이 conditional edge에 그대로 연결할 수 있는 결과 분기입니다."""

    if state.get("be2_tool_error"):
        return "tool_failed"
    return "tool_succeeded"


def build_meal_planning_node(
    tool_hub: PlanEatToolHub,
) -> Callable[[BE2MealPlanningNodeState], Awaitable[dict[str, object]]]:
    """확정 재료와 OpenAI 추출 제외 재료로 5개 식단 세트를 만드는 노드를 반환합니다.

    BE1이 새 결과 형식을 채택할 때 연결할 확장 지점이다. 현재 BE1의 상태나 외부
    Chat API는 이 함수를 호출하기 전까지 바뀌지 않는다.
    """

    meal_planning_tool = create_meal_planning_tool(tool_hub)

    async def meal_planning_node(
        state: BE2MealPlanningNodeState,
    ) -> dict[str, object]:
        try:
            raw_result = await meal_planning_tool.ainvoke(
                {
                    "session_id": state.get("session_id"),
                    "confirmed_ingredients": state.get("confirmed_ingredients"),
                    "user_conditions": state.get("user_conditions"),
                    "excluded_ingredients": state.get("excluded_ingredients", []),
                }
            )
            payload = _tool_payload(raw_result)
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError):
            return _meal_plan_error("식단 계획 Tool 입력 또는 결과 형식이 올바르지 않습니다.")

        if payload.get("ok") is not True:
            return _meal_plan_error("식단 계획 도구 실행 중 오류가 발생했습니다.")
        result = payload.get("result")
        source_metadata = payload.get("source_metadata")
        if not isinstance(result, dict) or not isinstance(source_metadata, dict):
            return _meal_plan_error("식단 계획 Tool 결과 형식이 올바르지 않습니다.")
        return {
            "meal_plan_result": result,
            "meal_plan_source_metadata": source_metadata,
            "meal_plan_error": None,
        }

    return meal_planning_node


def route_after_meal_planning(
    state: BE2MealPlanningNodeState,
) -> Literal["tool_succeeded", "tool_failed"]:
    """새 Meal Planning 직접 노드의 성공·실패 edge를 결정합니다."""

    return "tool_failed" if state.get("meal_plan_error") else "tool_succeeded"


def _tool_payload(raw_result: object) -> dict[str, object]:
    if not isinstance(raw_result, str):
        raise ValueError("ToolCall 결과가 문자열이 아닙니다.")
    payload = json.loads(raw_result)
    if not isinstance(payload, dict):
        raise ValueError("ToolCall 결과가 객체가 아닙니다.")
    return payload


def _node_error(message: str) -> dict[str, object]:
    return {
        "be2_tool_result": None,
        "be2_tool_source_metadata": None,
        "be2_tool_error": message,
    }


def _meal_plan_error(message: str) -> dict[str, object]:
    return {
        "meal_plan_result": None,
        "meal_plan_source_metadata": None,
        "meal_plan_error": message,
    }
