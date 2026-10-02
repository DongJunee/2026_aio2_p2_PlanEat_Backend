"""Chat API의 세션 관리와 LangGraph 실행을 연결합니다."""

from dataclasses import replace
from collections.abc import Mapping, Sequence
import re
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
        request_conditions = await self._read_conditions_from_message(request.message)
        effective_conditions = request_conditions or session.user_conditions
        state: ChatState = {
            "step": previous_step,
            "has_image": bool(request.attachments),
            "has_conditions": effective_conditions is not None,
        }
        if previous_step == "WAITING_CONDITIONS":
            state["condition_ready"] = effective_conditions is not None

        result = await chat_graph.ainvoke(state)
        next_session = _next_session_state(
            session=session,
            next_step=result["step"],
            user_conditions=request_conditions,
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
        if response_kind == "INPUT_REQUIREMENTS":
            return {
                "status": "NEED_MORE_INFO",
                "step": "INPUT_REQUIREMENTS",
                "response": "추천에 필요한 이미지와 조건을 자연어로 함께 알려주세요.",
                "questions": [
                    "냉장고, 냉동실 또는 영수증 이미지를 첨부해주세요.",
                    "메시지에 식단 목표를 알려주세요. 예: 다이어트, 고단백, 채식",
                    "메시지에 조리 가능한 시간을 알려주세요. 예: 20분 이내",
                ],
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

    async def _read_conditions_from_message(
        self, user_message: str
    ) -> Mapping[str, object] | None:
        """자연어 메시지에서 추천 조건의 충분성을 판단해 내부 전달값으로 보관합니다.

        외부 API에 구조화된 ``conditions`` 필드를 노출하지 않는다. Jev의 고신뢰 판단을
        우선하며, 비활성화·장애·저신뢰 상황에서는 개발 환경에서도 동작하도록 최소한의
        목표·시간 표현만 확인한다. Tool Hub에는 원문만 전달해 별도 계약 합의 전 임의의
        추출 필드를 강제하지 않는다.
        """

        # 외부 I/O는 세션 저장소 잠금 밖에서 실행한다.
        decision = await self._condition_evaluator.evaluate(user_message)
        if decision is not None:
            if not decision.is_ready:
                return None
            return {"message": user_message}

        if _has_fallback_conditions(user_message):
            return {"message": user_message}
        return None


def _next_session_state(
    *,
    session: ChatSessionState,
    next_step: WorkflowStep,
    user_conditions: Mapping[str, object] | None,
) -> ChatSessionState:
    """단계 전이 중 얻은 데이터를 Tool 호출용 세션 상태에 안전하게 보존합니다."""

    next_session = replace(session, step=next_step)
    if next_step == "WAITING_INGREDIENT_CONFIRM" and not session.ingredient_candidates:
        next_session = replace(next_session, ingredient_candidates=_INGREDIENT_CANDIDATES)
    if user_conditions is not None:
        return replace(next_session, user_conditions=user_conditions)
    return next_session


def _has_fallback_conditions(user_message: str) -> bool:
    """Jev를 사용할 수 없을 때만 적용할 보수적인 자연어 조건 확인 규칙입니다."""

    has_goal = bool(
        re.search(
            r"다이어트|체중.?감량|저칼로리|저탄고지|저탄수|고단백|벌크업|건강식|건강한|"
            r"채식|비건|당.?조절|일반식",
            user_message,
            flags=re.IGNORECASE,
        )
    )
    has_time = bool(
        re.search(r"\d+\s*분(?:\s*(?:안|이내|내))?|\d+\s*시간", user_message)
    )
    return has_goal and has_time


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
