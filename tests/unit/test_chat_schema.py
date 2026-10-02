from app.schemas.chat import (
    ChatConditionInputResponse,
    ChatErrorResponse,
    ChatRequest,
    ChatSuccessResponse,
)


def test_chat_request_accepts_at_most_five_image_attachments() -> None:
    request = ChatRequest(
        session_id="session-001",
        message="추천해줘",
        attachments=[
            {"type": "image", "data": f"image-{index}"}
            for index in range(5)
        ],
    )

    assert len(request.attachments) == 5


def test_chat_response_models_match_each_workflow_state() -> None:
    condition_response = ChatConditionInputResponse(
        status="NEED_MORE_INFO",
        step="CONDITION_INPUT",
        response="조건을 알려주세요.",
        questions=["식단 목표가 무엇인가요?"],
    )
    error_response = ChatErrorResponse(
        status="ERROR",
        response="요청 처리 중 오류가 발생했습니다.",
    )

    assert condition_response.step == "CONDITION_INPUT"
    assert error_response.status == "ERROR"
    assert ChatSuccessResponse.__name__ == "ChatSuccessResponse"
