"""Chat API의 세션 관리와 LangGraph 실행을 연결합니다."""

from dataclasses import replace
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.agent.graph import ChatState, WorkflowStep, chat_graph
from app.core.config import get_settings
from app.core.safety import SafetyViolationError, validate_user_message
from app.integrations.decision_engine.typesafe_jev import (
    ConditionReadinessDecision,
    JevConditionReadinessEvaluator,
)
from app.integrations.llm.openai_responder import LLMResponseError, OpenAIResponder
from app.repositories.chat_session import (
    ChatSessionRepository,
    ChatSessionState,
    InMemoryChatSessionRepository,
    IngredientCandidate,
)
from app.schemas.chat import ChatRequest

_INGREDIENTS = [
    {"name": "두부", "amount": "1모"},
    {"name": "양배추", "amount": "반 통"},
    {"name": "계란", "amount": "4개"},
]
_INGREDIENT_CANDIDATES = tuple(
    IngredientCandidate(name=ingredient["name"], amount=ingredient["amount"])
    for ingredient in _INGREDIENTS
)


class CompletionMessageGenerator(Protocol):
    """최종 추천 안내 문구를 생성하는 LLM 어댑터의 인터페이스입니다."""

    async def generate_completion_message(
        self,
        user_message: str,
        recipe_sets: Sequence[dict[str, object]],
    ) -> str:
        """추천 데이터에 대한 사용자용 안내 문구를 반환합니다."""


class ConditionReadinessEvaluator(Protocol):
    """조건 입력의 충분성을 판단하는 외부 결정 모델 인터페이스입니다."""

    async def evaluate(self, user_message: str) -> ConditionReadinessDecision | None:
        """신뢰도 기준을 통과한 조건 충족 결과만 반환합니다."""


class ChatService:
    """세션 상태, LangGraph 전이, 최종 LLM 응답 생성을 연결합니다."""

    def __init__(
        self,
        llm_responder: CompletionMessageGenerator | None = None,
        condition_evaluator: ConditionReadinessEvaluator | None = None,
        session_repository: ChatSessionRepository | None = None,
    ) -> None:
        settings = get_settings()
        self._llm_responder = llm_responder or OpenAIResponder(settings)
        self._condition_evaluator = condition_evaluator or JevConditionReadinessEvaluator(
            settings
        )
        self._session_repository = session_repository or InMemoryChatSessionRepository()

    async def handle(self, request: ChatRequest) -> tuple[dict[str, object], int]:
        """요청을 한 단계 진행시키고, LLM 실패를 안전한 API 오류로 변환합니다."""

        try:
            validate_user_message(request.message)
        except SafetyViolationError:
            return {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}, 400

        session = await self._session_repository.get(request.session_id)
        previous_step = session.step
        state: ChatState = {
            "step": previous_step,
            "has_image": bool(request.attachments),
        }
        if previous_step == "WAITING_CONDITIONS":
            # 외부 I/O를 세션 잠금 밖에서 수행해 다른 세션을 지연시키지 않는다.
            # Jev가 비활성화·실패·저신뢰이면 None으로 남겨 기존 완료 전이를 유지한다.
            decision = await self._condition_evaluator.evaluate(request.message)
            state["condition_ready"] = decision.is_ready if decision is not None else None

        result = await chat_graph.ainvoke(state)
        next_session = _next_session_state(
            session=session,
            next_step=result["step"],
            previous_step=previous_step,
            user_message=request.message,
        )
        await self._session_repository.save(request.session_id, next_session)

        try:
            return await self._to_response(result, request.message), 200
        except LLMResponseError:
            return {"status": "ERROR", "response": "추천 응답 생성 중 오류가 발생했습니다."}, 500

    async def _to_response(
        self, state: Mapping[str, object], user_message: str
    ) -> dict[str, object]:
        """LangGraph의 내부 단계를 외부 Chat API 응답 형식으로 변환합니다."""

        response_kind = state["response_kind"]

        if response_kind == "IMAGE_INPUT":
            return {
                "status": "NEED_MORE_INFO",
                "step": "IMAGE_INPUT",
                "response": "정확한 재료 확인을 위해 냉장고 또는 영수증 이미지를 첨부해주세요.",
                "questions": ["냉장고, 냉동실 또는 영수증 이미지를 첨부해주세요."],
            }
        if response_kind == "INGREDIENT_CONFIRM":
            return {
                "status": "NEED_MORE_INFO",
                "step": "INGREDIENT_CONFIRM",
                "response": "AI가 인식한 재료를 확인해주세요.",
                "ingredients": _INGREDIENTS,
            }
        if response_kind == "CONDITION_INPUT":
            return {
                "status": "NEED_MORE_INFO",
                "step": "CONDITION_INPUT",
                "response": "추천을 위해 몇 가지 정보를 더 알려주세요.",
                "questions": ["식단 목표가 무엇인가요?", "조리 가능한 시간은 얼마나 되나요?"],
            }
        recipe_sets = _demo_recipe_sets()
        completion_message = await self._llm_responder.generate_completion_message(
            user_message, recipe_sets
        )
        return {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": completion_message,
            "data": {"recipe_sets": recipe_sets},
        }


def _next_session_state(
    *,
    session: ChatSessionState,
    next_step: WorkflowStep,
    previous_step: WorkflowStep,
    user_message: str,
) -> ChatSessionState:
    """단계 전이 중 얻은 데이터를 Tool 호출용 세션 상태에 안전하게 보존합니다."""

    next_session = replace(session, step=next_step)
    if next_step == "WAITING_INGREDIENT_CONFIRM" and not session.ingredient_candidates:
        return replace(next_session, ingredient_candidates=_INGREDIENT_CANDIDATES)
    if previous_step == "WAITING_CONDITIONS" and next_step == "COMPLETED":
        return replace(next_session, user_conditions_message=user_message)
    return next_session


def _demo_recipe_sets() -> list[dict[str, object]]:
    """BE2 연결 전 API·FE 통합 검증에 사용할 최소 유효 응답입니다."""

    recipes = [
        {
            "recipe_id": f"DEMO-{index:03d}",
            "title": f"추천 레시피 {index}",
            "image": None,
            "cook_time": 20,
            "owned_ingredients": ["두부"],
            "missing_ingredients": [],
            "shopping_list": [],
            "nutrition": {"calories": 400, "protein": 20, "carbohydrate": 30, "fat": 12},
        }
        for index in range(1, 11)
    ]
    return [
        {"set_id": "DEMO-SET-001", "recipes": recipes[:5]},
        {"set_id": "DEMO-SET-002", "recipes": recipes[5:]},
    ]


chat_service = ChatService()
