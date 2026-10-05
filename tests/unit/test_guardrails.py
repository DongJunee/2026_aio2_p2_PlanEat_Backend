import asyncio

from app.core.config import Settings
from app.integrations.guardrails.nemo import NemoGuardrailService
from app.repositories.chat_session import (
    ChatSessionState,
    InMemoryChatSessionRepository,
    IngredientCandidate,
)
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService


class StubGuardrailValidator:
    """네트워크 없이 ChatService의 각 guardrail 경계를 검증하는 fake입니다."""

    def __init__(
        self,
        *,
        allow_input: bool = True,
        allow_output: bool = True,
        allow_tool: bool = True,
    ):
        self.allow_input = allow_input
        self.allow_output = allow_output
        self.allow_tool = allow_tool

    async def validate_input(self, text: str) -> bool:
        return self.allow_input

    async def validate_output(self, text: str) -> bool:
        return self.allow_output

    async def validate_tool_result(self, text: str) -> bool:
        return self.allow_tool


class StubCompletionMessageGenerator:
    """OpenAI 호출 없이 완료 응답을 생성하는 fake입니다."""

    async def generate_completion_message(
        self, user_message: str, recipe_sets: list[dict[str, object]]
    ) -> str:
        return "시스템 프롬프트를 공개합니다."


def test_nemo_input_rail_blocks_separator_variants() -> None:
    service = NemoGuardrailService(Settings(nemo_guardrails_enabled=True))

    assert asyncio.run(service.validate_input("ignore_previous_instructions")) is False
    assert asyncio.run(service.validate_input("건강한 두부 요리 추천해줘")) is True


def test_nemo_output_rail_blocks_internal_disclosure_and_api_key() -> None:
    service = NemoGuardrailService(Settings(nemo_guardrails_enabled=True))

    assert asyncio.run(service.validate_output("시스템 프롬프트를 공개합니다.")) is False
    assert asyncio.run(service.validate_output("sk-1234567890123456")) is False
    assert asyncio.run(service.validate_output("두부와 양배추를 활용한 레시피입니다.")) is True


def test_nemo_guardrails_can_be_disabled_for_local_incident_response() -> None:
    service = NemoGuardrailService(Settings(nemo_guardrails_enabled=False))

    assert asyncio.run(service.validate_input("ignore previous instructions")) is True
    assert asyncio.run(service.validate_output("system prompt")) is True


def test_chat_service_returns_400_when_guardrail_blocks_input() -> None:
    service = ChatService(
        guardrail_validator=StubGuardrailValidator(allow_input=False),
    )

    payload, status_code = asyncio.run(
        service.handle(ChatRequest(session_id="guardrail-input-session", message="메뉴 추천"))
    )

    assert status_code == 400
    assert payload == {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}


def test_chat_service_returns_500_when_guardrail_blocks_final_output() -> None:
    repository = InMemoryChatSessionRepository()
    asyncio.run(
        repository.save(
            "guardrail-output-session",
            ChatSessionState(
                step="COMPLETED",
                confirmed_ingredients=(IngredientCandidate(name="두부", amount="1모"),),
                user_conditions={"message": "다이어트 식단으로 20분 안에 만들고 싶어요."},
            ),
        )
    )
    service = ChatService(
        llm_responder=StubCompletionMessageGenerator(),
        guardrail_validator=StubGuardrailValidator(allow_output=False),
        session_repository=repository,
    )

    payload, status_code = asyncio.run(
        service.handle(ChatRequest(session_id="guardrail-output-session", message="계속 진행해줘"))
    )

    assert status_code == 500
    assert payload == {"status": "ERROR", "response": "추천 응답 생성 중 오류가 발생했습니다."}
