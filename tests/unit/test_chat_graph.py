import asyncio

from app.agent.graph import (
    SUMMARY_KEEP_MESSAGE_COUNT,
    SUMMARY_TRIGGER_MESSAGE_COUNT,
    ChatState,
    ConversationMessage,
    build_chat_graph,
    chat_graph,
)


def _messages(count: int) -> list[ConversationMessage]:
    return [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index}"}
        for index in range(count)
    ]


def test_summary_node_keeps_ten_messages_without_summarizing() -> None:
    state: ChatState = {
        "step": "WAITING_IMAGE",
        "messages": _messages(SUMMARY_TRIGGER_MESSAGE_COUNT),
        "has_image": False,
        "has_conditions": False,
    }

    result = asyncio.run(chat_graph.ainvoke(state))

    assert result.get("summary", "") == ""
    assert len(result["messages"]) == SUMMARY_TRIGGER_MESSAGE_COUNT


def test_summary_node_compacts_messages_after_threshold() -> None:
    state: ChatState = {
        "step": "WAITING_IMAGE",
        "messages": _messages(SUMMARY_TRIGGER_MESSAGE_COUNT + 1),
        "summary": "기존 요약",
        "has_image": False,
        "has_conditions": False,
    }

    result = asyncio.run(chat_graph.ainvoke(state))

    assert result["summary"].startswith("기존 요약")
    assert "message-0" in result["summary"]
    assert len(result["messages"]) == SUMMARY_KEEP_MESSAGE_COUNT
    assert result["messages"][-1]["content"] == "message-10"
    assert result["response_kind"] == "IMAGE_INPUT"


def test_waiting_image_can_complete_with_user_confirmed_ingredients() -> None:
    state: ChatState = {
        "step": "WAITING_IMAGE",
        "messages": _messages(1),
        "has_image": False,
        "has_conditions": True,
        "has_confirmed_ingredients": True,
    }

    result = asyncio.run(chat_graph.ainvoke(state))

    assert result["step"] == "COMPLETED"
    assert result["response_kind"] == "COMPLETED"


def test_waiting_image_with_text_ingredients_requests_only_missing_conditions() -> None:
    state: ChatState = {
        "step": "WAITING_IMAGE",
        "messages": _messages(1),
        "has_image": False,
        "has_conditions": False,
        "has_confirmed_ingredients": True,
    }

    result = asyncio.run(chat_graph.ainvoke(state))

    assert result["step"] == "WAITING_CONDITIONS"
    assert result["response_kind"] == "CONDITION_INPUT"


def test_completed_state_runs_injected_tool_node() -> None:
    async def fake_tool_node(state: ChatState) -> dict[str, object]:
        assert state["step"] == "COMPLETED"
        return {
            "tool_result": {
                "response": "Tool Hub 결과",
                "data": {"recipe_sets": []},
            },
            "tool_source_metadata": {"provider": "test"},
            "tool_error": None,
        }

    graph = build_chat_graph(tool_node=fake_tool_node)
    result = asyncio.run(
        graph.ainvoke(
            {
                "step": "WAITING_IMAGE",
                "messages": _messages(1),
                "has_image": False,
                "has_conditions": True,
                "has_confirmed_ingredients": True,
            }
        )
    )

    assert result["step"] == "COMPLETED"
    assert result["tool_result"]["response"] == "Tool Hub 결과"
