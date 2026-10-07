"""Tool Hub 연동 전 Chat 단계 전이와 대화 요약을 담당하는 LangGraph입니다."""

from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

SUMMARY_TRIGGER_MESSAGE_COUNT = 10
SUMMARY_KEEP_MESSAGE_COUNT = 2
SUMMARY_MAX_LENGTH = 4_000

MessageRole = Literal["user", "assistant"]


class ConversationMessage(TypedDict):
    """LangGraph가 전달하는 최소 대화 메시지입니다."""

    role: MessageRole
    content: str

WorkflowStep = Literal[
    "WAITING_IMAGE",
    "WAITING_INGREDIENT_CONFIRM",
    "WAITING_CONDITIONS",
    "COMPLETED",
]


class ChatState(TypedDict, total=False):
    step: WorkflowStep
    messages: list[ConversationMessage]
    summary: str
    has_image: bool
    has_conditions: bool
    has_confirmed_ingredients: bool
    condition_ready: bool | None
    ingredient_confirmation: Literal["confirmed", "rejected", "edited", "unclear"] | None
    response_kind: Literal[
        "IMAGE_INPUT",
        "INPUT_REQUIREMENTS",
        "INGREDIENT_RETRY",
        "INGREDIENT_CONFIRM",
        "CONDITION_INPUT",
        "COMPLETED",
    ]


def should_summarize(
    state: ChatState,
) -> Literal["summarize_conversation", "route_chat"]:
    """메시지가 10개를 초과했을 때만 요약 노드로 보냅니다."""

    if len(state.get("messages", [])) > SUMMARY_TRIGGER_MESSAGE_COUNT:
        return "summarize_conversation"
    return "route_chat"


def summarize_conversation(state: ChatState) -> ChatState:
    """오래된 대화를 결정적으로 압축하고 최근 메시지는 보존합니다.

    BE1의 기본 테스트는 외부 LLM에 의존하지 않아야 하므로 현재는 로컬 요약을 사용한다.
    ``_build_conversation_summary``가 실제 요약 모델을 연결할 때의 교체 지점이다.
    요약 후 최근 2개 메시지만 남겨 세션 상태가 요청마다 무한히 커지지 않게 한다.
    """

    messages = state.get("messages", [])
    if len(messages) <= SUMMARY_TRIGGER_MESSAGE_COUNT:
        return {}

    older_messages = messages[:-SUMMARY_KEEP_MESSAGE_COUNT]
    recent_messages = messages[-SUMMARY_KEEP_MESSAGE_COUNT:]
    summary = _build_conversation_summary(
        previous_summary=state.get("summary", ""),
        messages=older_messages,
    )
    return {"summary": summary, "messages": list(recent_messages)}


def _build_conversation_summary(
    *, previous_summary: str, messages: list[ConversationMessage]
) -> str:
    """이전 요약과 오래된 메시지를 안전한 길이의 결정적 요약으로 합칩니다."""

    summary_parts: list[str] = []
    if previous_summary.strip():
        summary_parts.append(previous_summary.strip())
    summary_parts.extend(
        f"{message['role']}: {_compact_message(message['content'])}"
        for message in messages
    )
    summary = "\n".join(summary_parts)
    if len(summary) <= SUMMARY_MAX_LENGTH:
        return summary
    # 요약도 세션에 계속 누적되므로 오래된 부분을 잘라 상태 크기를 bounded하게 유지한다.
    return summary[-SUMMARY_MAX_LENGTH:]


def _compact_message(content: str, max_length: int = 240) -> str:
    """요약 상태에 저장할 메시지 길이를 제한합니다."""

    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


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
        confirmation = state.get("ingredient_confirmation")
        if confirmation == "rejected":
            return {"step": "WAITING_IMAGE", "response_kind": "INGREDIENT_RETRY"}
        if confirmation in {"edited", "unclear"}:
            return {
                "step": "WAITING_INGREDIENT_CONFIRM",
                "response_kind": "INGREDIENT_CONFIRM",
            }
        if confirmation == "confirmed" and state.get("has_conditions"):
            return {"step": "COMPLETED", "response_kind": "COMPLETED"}
        if confirmation == "confirmed":
            return {"step": "WAITING_CONDITIONS", "response_kind": "CONDITION_INPUT"}
        return {
            "step": "WAITING_INGREDIENT_CONFIRM",
            "response_kind": "INGREDIENT_CONFIRM",
        }

    if step == "WAITING_CONDITIONS" and not state.get("has_confirmed_ingredients"):
        return {
            "step": "WAITING_INGREDIENT_CONFIRM",
            "response_kind": "INGREDIENT_CONFIRM",
        }

    if step == "WAITING_CONDITIONS" and state.get("has_conditions"):
        return {"step": "COMPLETED", "response_kind": "COMPLETED"}

    if step == "WAITING_CONDITIONS" and state.get("condition_ready") is False:
        # Jev가 높은 신뢰도로 조건 부족을 판단했을 때만 한 번 더 입력을 받는다.
        return {"step": "WAITING_CONDITIONS", "response_kind": "CONDITION_INPUT"}

    return {"step": "COMPLETED", "response_kind": "COMPLETED"}


def build_chat_graph():
    graph = StateGraph(ChatState)
    graph.add_node("summarize_conversation", summarize_conversation)
    graph.add_node("route_chat", route_chat)
    graph.set_conditional_entry_point(
        should_summarize,
        {
            "summarize_conversation": "summarize_conversation",
            "route_chat": "route_chat",
        },
    )
    graph.add_edge("summarize_conversation", "route_chat")
    graph.add_edge("route_chat", END)
    return graph.compile()


chat_graph = build_chat_graph()
