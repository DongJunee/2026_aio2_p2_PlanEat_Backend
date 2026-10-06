import asyncio

from app.agent.graph import (
    SUMMARY_KEEP_MESSAGE_COUNT,
    SUMMARY_TRIGGER_MESSAGE_COUNT,
    ChatState,
    ConversationMessage,
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
    assert result["response_kind"] == "INPUT_REQUIREMENTS"
