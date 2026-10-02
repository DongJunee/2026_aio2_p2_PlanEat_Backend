"""Tool Hub 연동 전 Chat 단계 전이를 담당하는 LangGraph입니다."""

from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

WorkflowStep = Literal[
    "WAITING_IMAGE",
    "WAITING_INGREDIENT_CONFIRM",
    "WAITING_CONDITIONS",
    "COMPLETED",
]


class ChatState(TypedDict, total=False):
    step: WorkflowStep
    has_image: bool
    has_conditions: bool
    condition_ready: bool | None
    response_kind: Literal[
        "IMAGE_INPUT",
        "INPUT_REQUIREMENTS",
        "INGREDIENT_CONFIRM",
        "CONDITION_INPUT",
        "COMPLETED",
    ]


def route_chat(state: ChatState) -> ChatState:
    """현재 세션 단계와 자연어 조건 판정 결과로 다음 FE 응답을 결정합니다."""

    step = state.get("step", "WAITING_IMAGE")

    if step == "WAITING_IMAGE":
        if state.get("has_image"):
            return {
                "step": "WAITING_INGREDIENT_CONFIRM",
                "response_kind": "INGREDIENT_CONFIRM",
            }
        if not state.get("has_conditions"):
            return {"step": "WAITING_IMAGE", "response_kind": "INPUT_REQUIREMENTS"}
        return {"step": "WAITING_IMAGE", "response_kind": "IMAGE_INPUT"}

    if step == "WAITING_INGREDIENT_CONFIRM":
        if state.get("has_conditions"):
            return {"step": "COMPLETED", "response_kind": "COMPLETED"}
        return {"step": "WAITING_CONDITIONS", "response_kind": "CONDITION_INPUT"}

    if step == "WAITING_CONDITIONS" and state.get("has_conditions"):
        return {"step": "COMPLETED", "response_kind": "COMPLETED"}

    if step == "WAITING_CONDITIONS" and state.get("condition_ready") is False:
        # Jev가 높은 신뢰도로 조건 부족을 판단했을 때만 한 번 더 입력을 받는다.
        return {"step": "WAITING_CONDITIONS", "response_kind": "CONDITION_INPUT"}

    return {"step": "COMPLETED", "response_kind": "COMPLETED"}


def build_chat_graph():
    graph = StateGraph(ChatState)
    graph.add_node("route_chat", route_chat)
    graph.set_entry_point("route_chat")
    graph.add_edge("route_chat", END)
    return graph.compile()


chat_graph = build_chat_graph()
