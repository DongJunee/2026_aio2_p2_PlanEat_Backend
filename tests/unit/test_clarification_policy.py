import asyncio

from app.repositories.chat_session import ChatSessionState
from app.services.chat_service import ChatService


class OverDetailedClarificationGenerator:
    """선택 조건을 필수처럼 묻는 모델 응답을 재현하는 fake입니다."""

    async def generate_clarification_response(
        self,
        response_kind: str,
        user_message: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        return {
            "response": (
                "현재 재료의 정확한 수량과 조리 시간에 대한 선호를 알려주실 수 있나요?"
            ),
            "questions": [
                "현미밥의 수량은 얼마인가요?",
                "조리 시간에 대한 선호가 있으신가요?",
            ],
        }


def test_clarification_does_not_expose_optional_conditions() -> None:
    """모델이 수량·조리 시간을 요구해도 필수 질문으로 노출하지 않습니다."""

    service = ChatService(llm_responder=OverDetailedClarificationGenerator())
    message, questions = asyncio.run(
        service._clarification_response(
            response_kind="IMAGE_INPUT",
            user_message="현미밥과 다이어트 식단을 원해요.",
            state={
                "has_image": False,
                "has_conditions": True,
                "has_confirmed_ingredients": False,
            },
            session=ChatSessionState(messages=({"role": "user", "content": "추천"},)),
            fallback_response="재료를 텍스트로 알려주세요.",
            fallback_questions=["사용 가능한 재료를 알려주세요."],
        )
    )

    assert message == "재료를 텍스트로 알려주세요."
    assert questions == ["사용 가능한 재료를 알려주세요."]


class IngredientWithoutAmountResponder:
    """수량 없이 입력된 재료를 재현하는 fake입니다."""

    async def extract_ingredients(
        self,
        user_message: str,
        attachments: tuple[dict[str, str], ...],
        current_ingredients: tuple[dict[str, str], ...] = (),
    ) -> list[dict[str, str]]:
        return [{"name": "현미밥", "amount": ""}]


def test_ingredient_without_amount_is_kept_as_confirmed_input() -> None:
    """수량 미입력은 재료 누락으로 처리하지 않고 수량 미정으로 보정합니다."""

    service = ChatService(llm_responder=IngredientWithoutAmountResponder())
    ingredients = asyncio.run(
        service._extract_ingredients(
            user_message="현미밥이 있어요.",
            attachments=(),
        )
    )

    assert [(item.name, item.amount) for item in ingredients] == [("현미밥", "수량 미정")]
