import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    ChatConditionInputResponse,
    ChatErrorResponse,
    ChatImageInputResponse,
    ChatRequest,
    ChatSuccessResponse,
    RecommendationData,
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


def test_chat_request_allows_missing_attachments() -> None:
    request = ChatRequest(
        session_id="session-002",
        message="다이어트 저녁 메뉴 추천해줘.",
    )

    assert request.attachments == []


def test_chat_request_rejects_an_overly_long_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(session_id="session-003", message="a" * 2_001)


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

    image_response = ChatImageInputResponse(
        status="NEED_MORE_INFO",
        step="IMAGE_INPUT",
        response="이미지를 첨부해주세요.",
        questions=["냉장고 또는 영수증 이미지를 첨부해주세요."],
    )
    assert image_response.step == "IMAGE_INPUT"


def test_recommendation_data_requires_two_sets_of_five_recipes() -> None:
    recipe = {
        "recipe_id": "R001",
        "title": "두부 양배추 볶음",
        "image": None,
        "cook_time": 20,
        "owned_ingredients": ["두부"],
        "missing_ingredients": [],
        "shopping_list": [],
        "nutrition": {
            "calories": 430,
            "protein": 28,
            "carbohydrate": 18,
            "fat": 15,
        },
    }
    data = RecommendationData(
        recipe_sets=[
            {"set_id": "SET001", "recipes": [recipe.copy() for _ in range(5)]},
            {"set_id": "SET002", "recipes": [recipe.copy() for _ in range(5)]},
        ]
    )

    assert len(data.recipe_sets) == 2
    assert all(len(recipe_set.recipes) == 5 for recipe_set in data.recipe_sets)
