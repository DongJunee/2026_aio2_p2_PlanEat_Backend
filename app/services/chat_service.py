"""Chat API의 세션 관리와 LangGraph 실행을 연결합니다."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
import re
from typing import Literal, Protocol

from app.agent.graph import ChatState, WorkflowStep, build_chat_graph
from app.agent.tools.factory import build_default_tool_hub
from app.agent.tools.nodes import build_recipe_recommendation_node
from app.agent.tools.tool_calls import ToolRequestExecutor
from app.core.config import get_settings
from app.core.observability import build_langsmith_run_config
from app.core.safety import (
    SafetyViolationError,
    is_prompt_disclosure_request,
    validate_completion_output,
    validate_user_message,
)
from app.integrations.decision_engine.typesafe_jev import (
    ConditionReadinessDecision,
    FeedbackPlan,
    IngredientConfirmationDecision,
    JevConditionReadinessEvaluator,
    JevFeedbackPlanner,
    JevIngredientConfirmationEvaluator,
)
from app.integrations.guardrails.nemo import GuardrailValidator, NemoGuardrailService
from app.integrations.llm.openai_responder import LLMResponseError, OpenAIResponder
from app.repositories.chat_session import (
    ChatSessionRepository,
    ChatSessionState,
    ConversationMessage,
    InMemoryChatSessionRepository,
    IngredientCandidate,
    PendingIngredientAction,
)
from app.schemas.chat import (
    ChatRequest,
    RecommendationData,
    RECIPE_SET_DISPLAY_LABELS,
    RecipeSet,
    get_recipe_set_key,
)
from app.services.pdf_service import RecipePdfService, recipe_pdf_service


# 추천 결과의 대표 세트 문구는 FE에 표시되고, 선택 요청은 서버 내부 set_key로
# 다시 변환해야 PDF 생성 경로가 아닌 피드백 경로로 빠지지 않는다.
_KOREAN_SET_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "BESTMATCH": (
        "베스트 매치",
        "베스트매치",
        "베스트",
        "가장 잘 맞는",
        "가장잘맞는",
        RECIPE_SET_DISPLAY_LABELS["BEST_MATCH"],
    ),
    "MOREOPTIONS": (RECIPE_SET_DISPLAY_LABELS["MORE_OPTIONS"],),
}
_SELECTION_INTENT_PATTERN = re.compile(
    r"선택|고르|골라|정해|결정|진행|발급|보내|pdf|피디에프|파일|"
    r"할게|할께|해줘|해주세요|주세요|받고\s*싶",
    flags=re.IGNORECASE,
)
_INGREDIENT_REMOVAL_PATTERN = (
    r"(?:빼\s*(?:줘|주세요|고|서|자|버려)?|삭제|제거|제외|없애|덜어|"
    r"넣지\s*(?:말|마)|안\s*넣)"
)
_QUANTITY_EXPRESSION_PATTERN = re.compile(
    r"(?:수량|개수|분량|양)|"
    r"(?:\d+(?:[.,]\d+)?|반|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*"
    r"(?:개|알|모|팩|봉|캔|병|장|줄|근|g|kg|ml|l|인분|마리|포기|통|단|대|쪽|"
    r"스푼|큰술|작은술)",
    flags=re.IGNORECASE,
)
_DISH_NAME_SUFFIXES = (
    "찌개",
    "전골",
    "볶음",
    "조림",
    "구이",
    "찜",
    "국",
    "탕",
    "밥",
    "죽",
    "면",
    "라면",
    "파스타",
    "샐러드",
    "덮밥",
    "김밥",
    "비빔밥",
    "카레",
    "스튜",
    "수프",
    "피자",
    "샌드위치",
)
_PROMPT_DISCLOSURE_RESPONSE = (
    "요청하신 내용은 안내할 수 없습니다. 레시피 추천을 원하시면 "
    "사용 가능한 재료와 식단 목표를 알려주세요."
)
_PROMPT_DISCLOSURE_QUESTIONS = [
    "사용 가능한 재료는 무엇인가요?",
    "식단 목표는 무엇인가요?",
]
_UNRECOGNIZED_IMAGE_RESPONSE = (
    "올려주신 사진은 냉장고 재료나 영수증 사진으로 확인하기 어려워요. "
    "냉장고 속 재료가 보이는 사진 또는 영수증 사진을 올려주세요."
)
_UNRECOGNIZED_IMAGE_QUESTIONS = [
    "냉장고 안 재료가 잘 보이는 사진이나 영수증 사진을 첨부해주세요.",
]
_ADDITIONAL_IMAGE_RESPONSE = (
    "추가 이미지에서 확인한 재료를 기존 재료 목록에 합쳤습니다. "
    "현재 목록으로 확정할까요? 더 추가하거나 수정할 재료가 있다면 말씀해주세요."
)


def _guardrail_rejection(message: str) -> tuple[dict[str, object], int]:
    """공개 요청은 FE 오류 화면 대신 기존 추가 입력 계약으로 안전하게 안내합니다."""

    if is_prompt_disclosure_request(message):
        return {
            "status": "NEED_MORE_INFO",
            "step": "CONDITION_INPUT",
            "response": _PROMPT_DISCLOSURE_RESPONSE,
            "questions": list(_PROMPT_DISCLOSURE_QUESTIONS),
        }, 200
    return {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}, 400


class LLMResponder(Protocol):
    """재료 추출·추가 질문·최종 구조화 추천을 제공하는 LLM 어댑터입니다."""

    async def generate_clarification_response(
        self,
        response_kind: str,
        user_message: str,
        context: Mapping[str, object],
    ) -> Mapping[str, object]:
        """현재 누락된 입력에 맞는 사용자 안내를 생성합니다."""

    async def extract_ingredients(
        self,
        user_message: str,
        attachments: Sequence[Mapping[str, str]],
        current_ingredients: Sequence[Mapping[str, str]] = (),
    ) -> list[dict[str, str]]:
        """이미지 또는 자연어에서 사용자가 제공한 재료만 추출합니다."""

    async def generate_recommendation(
        self,
        user_message: str,
        confirmed_ingredients: Sequence[Mapping[str, str]],
        user_conditions: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        """확정 재료와 조건을 구조화된 추천 응답으로 변환합니다."""


class ConditionReadinessEvaluator(Protocol):
    """조건 입력의 충분성을 판단하는 외부 결정 모델 인터페이스입니다."""

    async def evaluate(self, user_message: str) -> ConditionReadinessDecision | None:
        """신뢰도 기준을 통과한 조건 충족 결과만 반환합니다."""


class IngredientConfirmationEvaluator(Protocol):
    """재료 후보에 대한 사용자 자연어 확인 의도를 판단합니다."""

    async def evaluate(
        self,
        user_message: str,
        proposed_ingredients: Sequence[Mapping[str, str]],
    ) -> IngredientConfirmationDecision | None:
        """확정·거절·수정·모호함 중 하나를 반환합니다."""


@dataclass(frozen=True)
class IngredientConfirmationRead:
    """재료 확인 판정과 다음 턴에 유지할 확인 문맥입니다."""

    decision: IngredientConfirmationDecision
    guidance: str | None
    acknowledged_ingredient_names: tuple[str, ...]
    pending_ingredient_correction: str | None
    pending_ingredient_action: PendingIngredientAction | None
    extraction_message: str | None = None


class FeedbackPlanner(Protocol):
    """완료된 추천에 대한 사용자 피드백의 다음 노드 계획 경계입니다."""

    async def plan(
        self,
        user_message: str,
        current_conditions: Mapping[str, object] | None,
    ) -> FeedbackPlan:
        """피드백을 조건 갱신·재추천 계획으로 분류합니다."""


class ChatService:
    """세션 상태, LangGraph 전이, Tool Hub·임시 LLM 응답을 연결합니다."""

    def __init__(
        self,
        llm_responder: LLMResponder | None = None,
        condition_evaluator: ConditionReadinessEvaluator | None = None,
        ingredient_confirmation_evaluator: IngredientConfirmationEvaluator | None = None,
        tool_provider: ToolRequestExecutor | None = None,
        session_repository: ChatSessionRepository | None = None,
        guardrail_validator: GuardrailValidator | None = None,
        feedback_planner: FeedbackPlanner | None = None,
        pdf_service: RecipePdfService | None = None,
    ) -> None:
        settings = get_settings()
        self._settings = settings
        self._llm_responder = llm_responder or OpenAIResponder(settings)
        self._condition_evaluator = condition_evaluator or JevConditionReadinessEvaluator(
            settings
        )
        self._ingredient_confirmation_evaluator = (
            ingredient_confirmation_evaluator
            or JevIngredientConfirmationEvaluator(settings)
        )
        self._feedback_planner = feedback_planner or JevFeedbackPlanner(settings)
        self._pdf_service = pdf_service or recipe_pdf_service
        self._tool_provider = tool_provider
        self._chat_graph = build_chat_graph(
            tool_node=(
                build_recipe_recommendation_node(tool_provider)
                if tool_provider is not None
                else None
            )
        )
        self._session_repository = session_repository or InMemoryChatSessionRepository()
        self._guardrail_validator = guardrail_validator or NemoGuardrailService(settings)

    async def handle(self, request: ChatRequest) -> tuple[dict[str, object], int]:
        """요청을 한 단계 진행시키고, LLM 실패를 안전한 API 오류로 변환합니다."""

        try:
            validate_user_message(request.message)
        except SafetyViolationError:
            return _guardrail_rejection(request.message)
        if not await self._guardrail_validator.validate_input(request.message):
            return _guardrail_rejection(request.message)

        session = await self._session_repository.get(request.session_id)
        previous_step = session.step
        stored_recommendation = _stored_recommendation(session)
        regenerate_with_new_recipes = False
        replace_confirmed_ingredients = False
        completed_additional_image = (
            previous_step == "COMPLETED" and bool(request.attachments)
        )
        if (
            previous_step == "COMPLETED"
            and stored_recommendation is not None
            and not completed_additional_image
        ):
            feedback_context = {
                **dict(session.user_conditions or {}),
                "available_recipe_sets": [
                    recipe_set.set_id for recipe_set in stored_recommendation.recipe_sets
                ],
            }
            feedback_plan = await self._feedback_planner.plan(
                request.message,
                feedback_context,
            )

            # Jev가 완료 단계의 다음 노드를 먼저 고른다. 아래의 문자열 parser는
            # Jev 비활성화·timeout 같은 fallback에만 쓰며, 정상 Jev 판단을 덮어쓰지
            # 않는다. 세트 ID는 이미 생성된 Tool 결과 안에서만 해석한다.
            is_jev_fallback = feedback_plan.source == "fallback"
            regenerate_with_new_recipes = feedback_plan.intent == "regenerate" or (
                is_jev_fallback and _is_recipe_regeneration_request(request.message)
            )
            replace_confirmed_ingredients = (
                feedback_plan.intent == "replace_ingredients"
                or (
                    is_jev_fallback
                    and _looks_like_ingredient_replacement(request.message)
                )
            )
            wants_set_selection = feedback_plan.intent == "select_set" or is_jev_fallback
            if wants_set_selection:
                selected_set_id = _parse_selected_set_id(
                    request.message, stored_recommendation
                )
                if selected_set_id is not None:
                    return await self._handle_set_selection(
                        request=request,
                        session=session,
                        recommendation=stored_recommendation,
                        selected_set_id=selected_set_id,
                    )
                if feedback_plan.intent == "select_set":
                    return await self._respond_to_incomplete_set_selection(
                        request=request,
                        session=session,
                        recommendation=stored_recommendation,
                    )

            if feedback_plan.intent in {"reconsider", "out_of_scope", "unclear"}:
                return await self._respond_to_completed_feedback(
                    request=request,
                    session=session,
                    recommendation=stored_recommendation,
                    intent=feedback_plan.intent,
                )

            if replace_confirmed_ingredients:
                # 보유 재료 수정은 기존 식단 목적을 유지한다. 재료를 조건 원문에
                # 섞으면 Tool Hub가 "돼지고기"를 식단 목적처럼 읽을 수 있다.
                request_conditions = {
                    **dict(session.user_conditions or {}),
                    "feedback_plan": feedback_plan.intent,
                    "regenerated_recipe_request": False,
                }
            else:
                request_conditions = _merge_feedback_conditions(
                    session.user_conditions,
                    request.message,
                    feedback_plan,
                )
            if regenerate_with_new_recipes:
                request_conditions = _exclude_previously_recommended_recipes(
                    request_conditions,
                    stored_recommendation,
                )
            else:
                # 이전 재생성 플래그가 다음 일반 피드백 응답의 문구까지 바꾸지 않게 한다.
                request_conditions = {
                    **request_conditions,
                    "regenerated_recipe_request": False,
                }
            condition_ready = _has_required_condition(request_conditions)
        elif completed_additional_image:
            # 완료 후 이미지를 올리는 경우에는 피드백·재추천 문장으로 해석하지 않고,
            # 기존 조건을 유지한 채 재료 추가 확인 단계로 되돌린다.
            request_conditions = session.user_conditions
            condition_ready = _has_required_condition(request_conditions)
        else:
            request_conditions, condition_ready = await self._read_condition_state(
                request.message,
                session.user_conditions,
            )
        effective_conditions = request_conditions or session.user_conditions
        if request_conditions is None:
            condition_ready = _has_required_condition(effective_conditions)
        confirmation_decision: IngredientConfirmationDecision | None = None
        candidate_ingredients = session.ingredient_candidates
        confirmed_ingredients = session.confirmed_ingredients
        extracted_ingredients: tuple[IngredientCandidate, ...] | None = None
        ingredient_edit_summary: list[str] = []
        ingredient_confirmation_message: str | None = None
        acknowledged_ingredient_names = session.acknowledged_ingredient_names
        pending_ingredient_correction = session.pending_ingredient_correction
        pending_ingredient_action = session.pending_ingredient_action
        ingredient_extraction_message = request.message
        if replace_confirmed_ingredients:
            # 완료 이후 보유 재료가 바뀌었다면 곧바로 Tool Hub로 보내지 않는다.
            # 추출한 새 목록을 후보로 되돌려 사용자 확인을 다시 받은 뒤에만
            # Recipe/Shopping node가 확정 재료를 사용하도록 한다.
            try:
                extracted_ingredients = await self._extract_ingredients(
                    user_message=request.message,
                    attachments=(),
                    current_ingredients=session.confirmed_ingredients,
                )
            except LLMResponseError:
                return {
                    "status": "ERROR",
                    "response": "새 재료 입력을 해석하는 중 오류가 발생했습니다.",
                }, 500
            if extracted_ingredients:
                candidate_ingredients = extracted_ingredients
                confirmed_ingredients = tuple()
                confirmation_decision = IngredientConfirmationDecision(
                    intent="edited", confidence=1.0
                )
                ingredient_edit_summary = _summarize_ingredient_changes(
                    before=session.confirmed_ingredients,
                    after=candidate_ingredients,
                    user_message=request.message,
                )
            else:
                return {
                    "status": "NEED_MORE_INFO",
                    "step": "INGREDIENT_CONFIRM",
                    "response": (
                        "바뀐 보유 재료를 확인하지 못했어요. 추가하거나 제외할 재료를 "
                        "한 번 더 알려주세요."
                    ),
                    "ingredients": _ingredient_payload(session.confirmed_ingredients),
                    "confirmed_ingredients": _ingredient_payload(
                        session.confirmed_ingredients
                    ),
                }, 200
        elif request.attachments and previous_step in {
            "WAITING_INGREDIENT_CONFIRM",
            "WAITING_CONDITIONS",
            "COMPLETED",
        }:
            # 첫 이미지의 후보를 확인 중이거나 일부 재료를 이미 확정한 뒤에는,
            # 후속 이미지를 새 목록으로 덮어쓰지 않고 기존 재료와 합친다. 완료 후
            # 재료를 추가하는 경우에도 기존 확정 목록은 유지하고, Vision 결과는
            # 사용자 재확인 전까지 후보로만 보관해 Tool 입력에 섞이지 않게 한다.
            try:
                extracted_ingredients = await self._extract_ingredients(
                    user_message=request.message,
                    attachments=tuple(
                        {"type": attachment.type, "data": attachment.data}
                        for attachment in request.attachments
                    ),
                )
            except LLMResponseError:
                return {
                    "status": "ERROR",
                    "response": "추가 이미지에서 재료를 인식하는 중 오류가 발생했습니다.",
                }, 500
            if not extracted_ingredients:
                # 기존 재료 상태는 유지하고, 사용자가 새 이미지를 다시 올릴 수 있게
                # 기존 이미지 입력 계약으로 안내한다.
                return {
                    "status": "NEED_MORE_INFO",
                    "step": "IMAGE_INPUT",
                    "response": _UNRECOGNIZED_IMAGE_RESPONSE,
                    "questions": list(_UNRECOGNIZED_IMAGE_QUESTIONS),
                }, 200
            candidate_ingredients = _merge_ingredient_candidates(
                session.ingredient_candidates,
                extracted_ingredients,
            )
            # 이미 확인한 재료는 확정 상태로 유지하고, 새 이미지에서만 나온 재료를
            # 후보로 둔다. 후보를 확정할 때 서비스 계층에서 두 목록을 합친다.
            confirmed_ingredients = session.confirmed_ingredients
            candidate_ingredients = _exclude_confirmed_ingredients(
                candidate_ingredients,
                confirmed_ingredients,
            )
            confirmation_decision = IngredientConfirmationDecision(
                intent="edited", confidence=1.0
            )
            ingredient_edit_summary = _summarize_ingredient_changes(
                before=session.ingredient_candidates,
                after=candidate_ingredients,
                user_message="추가 이미지",
            )
            if candidate_ingredients:
                ingredient_confirmation_message = _ADDITIONAL_IMAGE_RESPONSE
            elif confirmed_ingredients:
                # 새 이미지가 기존 확정 재료만 다시 보여준 경우에는 빈 후보 확인
                # 응답을 만들지 않고 기존 확정 상태를 그대로 이어간다.
                confirmation_decision = IngredientConfirmationDecision(
                    intent="confirmed", confidence=1.0
                )
                ingredient_confirmation_message = (
                    "추가 이미지에서 새로운 재료를 찾지 못했습니다. 기존 확정 재료를 유지합니다."
                )
        elif previous_step == "WAITING_INGREDIENT_CONFIRM":
            confirmation_read = await self._read_ingredient_confirmation(
                request.message,
                session.ingredient_candidates,
                acknowledged_ingredient_names=session.acknowledged_ingredient_names,
                pending_ingredient_correction=session.pending_ingredient_correction,
                pending_ingredient_action=session.pending_ingredient_action,
            )
            confirmation_decision = confirmation_read.decision
            ingredient_confirmation_message = confirmation_read.guidance
            acknowledged_ingredient_names = confirmation_read.acknowledged_ingredient_names
            pending_ingredient_correction = confirmation_read.pending_ingredient_correction
            pending_ingredient_action = confirmation_read.pending_ingredient_action
            ingredient_extraction_message = _normalize_ingredient_edit_message(
                confirmation_read.extraction_message or request.message
            )
            candidate_ingredients, confirmed_ingredients = _apply_confirmation_decision(
                session=session,
                decision=confirmation_decision,
                user_message=request.message,
            )
            if confirmation_decision.intent == "rejected" and confirmed_ingredients:
                # 추가 이미지 후보를 거절한 경우에는 기존에 확정한 재료까지 폐기하지
                # 않고, 새 후보만 취소한 뒤 기존 확정 흐름으로 돌아간다.
                confirmation_decision = IngredientConfirmationDecision(
                    intent="confirmed", confidence=confirmation_decision.confidence
                )
                ingredient_confirmation_message = (
                    "추가로 인식한 재료는 취소하고 기존 확정 재료를 유지합니다."
                )
            if confirmation_decision.intent == "edited":
                try:
                    extracted_ingredients = await self._extract_ingredients(
                        user_message=ingredient_extraction_message,
                        attachments=(),
                        current_ingredients=session.ingredient_candidates,
                    )
                except LLMResponseError:
                    return {
                        "status": "ERROR",
                        "response": "재료 입력을 해석하는 중 오류가 발생했습니다.",
                    }, 500
                # OpenAI는 수정된 전체 목록을 제안할 뿐 직접 상태를 바꾸지 않는다.
                # 서버는 사용자 문장에 없는 재료의 추가·제거·수량 변경을 거부한다.
                # 명시적으로 말한 짧은 동작어는 모델이 누락해도 안전하게 보완한다.
                proposed_candidates = _reconcile_explicit_ingredient_edits(
                    before=session.ingredient_candidates,
                    extracted=extracted_ingredients,
                    user_message=ingredient_extraction_message,
                )
                candidate_ingredients = _validate_ingredient_edit_result(
                    before=session.ingredient_candidates,
                    proposed=proposed_candidates,
                    user_message=ingredient_extraction_message,
                )
                if candidate_ingredients is None:
                    candidate_ingredients = session.ingredient_candidates
                    confirmation_decision = IngredientConfirmationDecision(
                        intent="unclear", confidence=confirmation_decision.confidence
                    )
                    ingredient_confirmation_message = (
                        "말씀하신 변경 내용을 현재 목록에서 안전하게 확인하지 못했어요. "
                        "추가하거나 제외할 재료명을 다시 알려주세요."
                    )
                else:
                    ingredient_edit_summary = _summarize_ingredient_changes(
                        before=session.ingredient_candidates,
                        after=candidate_ingredients,
                        user_message=ingredient_extraction_message,
                    )
                if not candidate_ingredients and confirmed_ingredients:
                    confirmation_decision = IngredientConfirmationDecision(
                        intent="confirmed", confidence=confirmation_decision.confidence
                    )
                    ingredient_confirmation_message = (
                        "추가 재료를 반영하지 않고 기존 확정 재료를 유지합니다."
                    )
                elif not candidate_ingredients:
                    # 삭제와 추가가 같은 답변에 있는 경우 OpenAI 반영 뒤에도 후보가 비어
                    # 있을 때만 재촬영으로 전환한다. 반영 전 목록만 보고 전환하면 새로
                    # 추가한 재료까지 잃을 수 있다.
                    confirmation_decision = IngredientConfirmationDecision(
                        intent="rejected", confidence=confirmation_decision.confidence
                    )
                elif (
                    ingredient_edit_summary
                    and _requests_finalization_after_edit(request.message)
                ):
                    # 수정과 확정을 함께 말한 경우에는 먼저 수정 결과를 만든 뒤,
                    # 갱신된 목록만 확정 재료로 이동한다.
                    confirmed_ingredients = _merge_ingredient_candidates(
                        confirmed_ingredients,
                        candidate_ingredients,
                    )
                    candidate_ingredients = tuple()
                    confirmation_decision = IngredientConfirmationDecision(
                        intent="confirmed", confidence=confirmation_decision.confidence
                    )
            if confirmation_decision.intent in {"confirmed", "rejected", "edited"}:
                acknowledged_ingredient_names = tuple()
                pending_ingredient_correction = None
                pending_ingredient_action = None
        elif previous_step == "WAITING_IMAGE" and not request.attachments:
            # 첫 요청인지와 관계없이 사용자가 텍스트로 재료를 직접 입력하면 이미지
            # 요청보다 자연어 입력을 우선한다. 이미지 인식 후보가 아니라 사용자 명시
            # 입력이므로 별도 확인 없이 확정 재료로 보관한다.
            try:
                extracted_ingredients = await self._extract_ingredients(
                    user_message=request.message,
                    attachments=(),
                )
            except LLMResponseError:
                return {
                    "status": "ERROR",
                    "response": "재료 입력을 해석하는 중 오류가 발생했습니다.",
                }, 500
            if extracted_ingredients:
                candidate_ingredients = tuple()
                confirmed_ingredients = extracted_ingredients
        elif previous_step == "WAITING_IMAGE" and request.attachments:
            try:
                extracted_ingredients = await self._extract_ingredients(
                    user_message=request.message,
                    attachments=tuple(
                        {"type": attachment.type, "data": attachment.data}
                        for attachment in request.attachments
                    ),
                )
            except LLMResponseError:
                return {
                    "status": "ERROR",
                    "response": "이미지에서 재료를 인식하는 중 오류가 발생했습니다.",
                }, 500
            if not extracted_ingredients:
                return {
                    "status": "NEED_MORE_INFO",
                    "step": "IMAGE_INPUT",
                    "response": _UNRECOGNIZED_IMAGE_RESPONSE,
                    "questions": list(_UNRECOGNIZED_IMAGE_QUESTIONS),
                }, 200
            candidate_ingredients = extracted_ingredients
        messages = [*session.messages, {"role": "user", "content": request.message}]
        workflow_step = previous_step
        if replace_confirmed_ingredients and confirmation_decision is not None:
            # 완료 상태에서 재료 변경을 감지한 경우에는, 갱신된 후보를 먼저
            # 복명복창하고 사용자의 확정을 기다리는 노드로 명시적으로 되돌린다.
            workflow_step = "WAITING_INGREDIENT_CONFIRM"
        if (
            request.attachments
            and previous_step in {"WAITING_CONDITIONS", "COMPLETED"}
            and confirmation_decision is not None
        ):
            # 조건 입력 또는 완료 이후 새 이미지가 들어오면, 기존 확정 재료는 유지한
            # 채 새 후보만 먼저 확인해야 하므로 그래프를 재료 확인 단계로 되돌린다.
            workflow_step = "WAITING_INGREDIENT_CONFIRM"
        state: ChatState = {
            "step": workflow_step,
            "messages": messages,
            "summary": session.summary,
            "session_id": request.session_id,
            "confirmed_ingredients": [
                {"name": ingredient.name, "amount": ingredient.amount}
                for ingredient in confirmed_ingredients
            ],
            "user_conditions": dict(effective_conditions or {}),
            "has_image": bool(request.attachments),
            "has_conditions": condition_ready,
            "has_confirmed_ingredients": bool(confirmed_ingredients),
            "ingredient_confirmation": (
                confirmation_decision.intent if confirmation_decision is not None else None
            ),
        }
        if ingredient_edit_summary:
            state["ingredient_edit_summary"] = ingredient_edit_summary
        if ingredient_confirmation_message:
            state["ingredient_confirmation_message"] = ingredient_confirmation_message
        if previous_step == "WAITING_CONDITIONS":
            state["condition_ready"] = condition_ready

        trace_config = build_langsmith_run_config(
            self._settings,
            session_id=request.session_id,
            workflow_step=previous_step,
            has_image=bool(request.attachments),
            has_conditions=condition_ready,
            has_confirmed_ingredients=bool(confirmed_ingredients),
            attachment_count=len(request.attachments or []),
            tool_enabled=self._tool_provider is not None,
        )
        if trace_config is None:
            result = await self._chat_graph.ainvoke(state)
        else:
            result = await self._chat_graph.ainvoke(state, config=trace_config)
        next_session = _next_session_state(
            session=session,
            next_step=result["step"],
            user_conditions=effective_conditions if request_conditions is not None else None,
            ingredient_candidates=(
                candidate_ingredients
                if confirmation_decision is not None or extracted_ingredients is not None
                else None
            ),
            confirmed_ingredients=(
                confirmed_ingredients
                if confirmation_decision is not None
                or (extracted_ingredients is not None and not request.attachments)
                else None
            ),
            acknowledged_ingredient_names=(
                acknowledged_ingredient_names
                if confirmation_decision is not None or extracted_ingredients is not None
                else None
            ),
            pending_ingredient_correction=(
                pending_ingredient_correction
                if confirmation_decision is not None or extracted_ingredients is not None
                else None
            ),
            pending_ingredient_action=(
                pending_ingredient_action
                if confirmation_decision is not None or extracted_ingredients is not None
                else None
            ),
            messages=tuple(result.get("messages", messages)),
            summary=str(result.get("summary", session.summary)),
        )
        await self._session_repository.save(request.session_id, next_session)

        try:
            if result.get("tool_error"):
                return {
                    "status": "ERROR",
                    "response": "추천 도구 실행 중 오류가 발생했습니다.",
                }, 500
            response = await self._to_response(
                result,
                request.message,
                next_session,
                tool_result=result.get("tool_result"),
            )
        except LLMResponseError:
            return {"status": "ERROR", "response": "추천 응답 생성 중 오류가 발생했습니다."}, 500

        next_session = _store_recommendation_if_present(next_session, response)
        next_session = replace(
            next_session,
            messages=(
                *next_session.messages,
                _conversation_message("assistant", response["response"]),
            ),
        )
        await self._session_repository.save(request.session_id, next_session)
        return response, 200

    async def _respond_to_completed_feedback(
        self,
        *,
        request: ChatRequest,
        session: ChatSessionState,
        recommendation: RecommendationData,
        intent: Literal["reconsider", "out_of_scope", "unclear"],
    ) -> tuple[dict[str, object], int]:
        """Tool 실행 없이 완료된 추천을 유지하고 다음 입력을 안내합니다."""

        if intent == "out_of_scope":
            response_message = (
                "PlanEat에서는 현재 식단을 바꾸거나 다른 식단을 다시 추천하고, "
                "추천 세트의 상세 PDF를 만들 수 있어요."
            )
        elif intent == "reconsider":
            response_message = (
                "현재 추천은 그대로 두었어요. 다른 메뉴가 필요하면 '다시 추천해줘', "
                "조건을 바꾸려면 원하시는 조건을 말씀해주세요."
            )
        else:
            response_message = (
                "원하시는 다음 작업을 조금만 더 알려주세요. 예를 들어 '다시 추천해줘', "
                "'20분 이내로 바꿔줘', '1번 세트 PDF 만들어줘'라고 말할 수 있어요."
            )
        response = _completed_feedback_response(
            recommendation=recommendation,
            response_message=response_message,
        )
        next_session = replace(
            session,
            messages=(
                *session.messages,
                _conversation_message("user", request.message),
                _conversation_message("assistant", response_message),
            ),
        )
        await self._session_repository.save(request.session_id, next_session)
        return response, 200

    async def _respond_to_incomplete_set_selection(
        self,
        *,
        request: ChatRequest,
        session: ChatSessionState,
        recommendation: RecommendationData,
    ) -> tuple[dict[str, object], int]:
        """Jev가 세트 선택으로 판단했지만 대상이 없을 때만 한 번 더 묻습니다."""

        labels = ", ".join(
            recipe_set.set_id for recipe_set in recommendation.recipe_sets
        )
        response_message = f"선택할 식단 세트를 알려주세요. 현재 선택 가능한 세트는 {labels}예요."
        response = _completed_feedback_response(
            recommendation=recommendation,
            response_message=response_message,
        )
        next_session = replace(
            session,
            messages=(
                *session.messages,
                _conversation_message("user", request.message),
                _conversation_message("assistant", response_message),
            ),
        )
        await self._session_repository.save(request.session_id, next_session)
        return response, 200

    async def _handle_set_selection(
        self,
        *,
        request: ChatRequest,
        session: ChatSessionState,
        recommendation: RecommendationData,
        selected_set_id: str,
    ) -> tuple[dict[str, object], int]:
        """선택 세트의 상세 PDF를 생성하고 다운로드 URL을 반환합니다."""

        selected_set = _find_recipe_set(recommendation, selected_set_id)
        if selected_set is None:
            return {"status": "ERROR", "response": "선택한 식단 세트를 찾을 수 없습니다."}, 400

        selected_payload = {"selected_set": selected_set.model_dump()}
        if not await self._guardrail_validator.validate_tool_result(
            json.dumps(selected_payload, ensure_ascii=False)
        ):
            return {"status": "ERROR", "response": "선택한 식단을 안전하게 처리할 수 없습니다."}, 500
        try:
            pdf_url = await self._pdf_service.generate(
                session_id=request.session_id,
                recommendation=recommendation,
                selected_set_id=selected_set_id,
            )
        except (OSError, ValueError, RuntimeError):
            return {"status": "ERROR", "response": "선택한 식단 PDF를 생성하지 못했습니다."}, 500

        response_message = f"{selected_set.set_id} 식단의 상세 PDF를 생성했습니다."
        if not await self._guardrail_validator.validate_output(response_message):
            return {"status": "ERROR", "response": "PDF 응답을 안전하게 표시할 수 없습니다."}, 500
        response = {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": response_message,
            "data": recommendation.model_dump(),
            "next_action": "PDF_READY",
            # selected_set_id·available_set_ids는 기존 세션/PDF 계약을 유지하는
            # 내부 키이고, 화면에 표시할 문구는 data.recipe_sets[*].set_id를 사용한다.
            "available_set_ids": [
                get_recipe_set_key(recipe_set, index)
                for index, recipe_set in enumerate(recommendation.recipe_sets)
            ],
            "selected_set_id": selected_set_id,
            "pdf_url": pdf_url,
        }
        next_session = replace(
            session,
            selected_set_id=selected_set_id,
            pdf_url=pdf_url,
            messages=(
                *session.messages,
                _conversation_message("user", request.message),
                _conversation_message("assistant", response_message),
            ),
        )
        await self._session_repository.save(request.session_id, next_session)
        return response, 200

    async def _to_response(
        self,
        state: Mapping[str, object],
        user_message: str,
        session: ChatSessionState,
        tool_result: object = None,
    ) -> dict[str, object]:
        """LangGraph의 내부 단계를 외부 Chat API 응답 형식으로 변환합니다."""

        response_kind = state["response_kind"]

        if response_kind == "IMAGE_INPUT":
            image_request_already_sent = bool(session.messages)
            if image_request_already_sent:
                fallback_response = (
                    "사진이 없어도 괜찮아요. 냉장고에 있는 재료를 텍스트로 알려주시면 "
                    "그 재료를 기준으로 추천해드릴게요."
                )
                fallback_questions = [
                    "사용 가능한 재료를 알려주세요.",
                    "식단 목표는 무엇인가요? 예: 다이어트, 고단백, 채식",
                ]
            else:
                fallback_response = "정확한 재료 확인을 위해 냉장고 또는 영수증 이미지를 첨부해주세요."
                fallback_questions = ["냉장고, 냉동실 또는 영수증 이미지를 첨부해주세요."]
            message, questions = await self._clarification_response(
                response_kind=response_kind,
                user_message=user_message,
                state=state,
                session=session,
                fallback_response=fallback_response,
                fallback_questions=fallback_questions,
            )
            return {
                "status": "NEED_MORE_INFO",
                "step": "IMAGE_INPUT",
                "response": message,
                "questions": questions,
            }
        if response_kind == "INGREDIENT_RETRY":
            message, questions = await self._clarification_response(
                response_kind=response_kind,
                user_message=user_message,
                state=state,
                session=session,
                fallback_response="인식된 재료가 맞지 않습니다. 재료가 보이는 이미지를 다시 첨부해주세요.",
                fallback_questions=["재료가 잘 보이는 냉장고 또는 영수증 이미지를 다시 첨부해주세요."],
            )
            return {
                "status": "NEED_MORE_INFO",
                "step": "IMAGE_INPUT",
                "response": message,
                "questions": questions,
            }
        if response_kind == "INPUT_REQUIREMENTS":
            message, questions = await self._clarification_response(
                response_kind=response_kind,
                user_message=user_message,
                state=state,
                session=session,
                fallback_response="추천에 필요한 이미지와 조건을 자연어로 함께 알려주세요.",
                fallback_questions=[
                    "냉장고, 냉동실 또는 영수증 이미지를 첨부해주세요.",
                    "메시지에 식단 목표를 알려주세요. 예: 다이어트, 고단백, 채식",
                ],
            )
            return {
                "status": "NEED_MORE_INFO",
                "step": "INPUT_REQUIREMENTS",
                "response": message,
                "questions": questions,
            }
        if response_kind == "INGREDIENT_CONFIRM":
            confirmation = state.get("ingredient_confirmation")
            guided_message = state.get("ingredient_confirmation_message")
            if isinstance(guided_message, str) and guided_message.strip():
                message = guided_message.strip()
            elif confirmation == "edited":
                edit_summary = _ingredient_edit_summary_from_state(state)
                summary_message = (
                    "\n\n".join(edit_summary)
                    if edit_summary
                    else "말씀하신 재료 수정 내용을 반영했습니다."
                )
                message = (
                    f"{summary_message}\n\n이 목록으로 재료를 확정할까요? "
                    "더 추가하거나 수정할 내용이 있다면 말씀해주세요."
                )
            elif confirmation == "unclear":
                message = (
                    "현재 재료 목록으로 확정할까요? 수정하거나 추가할 재료가 있다면 말씀해주세요."
                )
            else:
                message = (
                    "AI가 인식한 재료 목록입니다. 이 재료로 확정할까요? "
                    "추가하거나 수정할 재료가 있다면 말씀해주세요."
                )
            return {
                "status": "NEED_MORE_INFO",
                "step": "INGREDIENT_CONFIRM",
                "response": message,
                "ingredients": _ingredient_payload(session.ingredient_candidates),
                "confirmed_ingredients": _ingredient_payload(session.confirmed_ingredients),
            }
        if response_kind == "CONDITION_INPUT":
            message, questions = await self._clarification_response(
                response_kind=response_kind,
                user_message=user_message,
                state=state,
                session=session,
                fallback_response="추천을 위해 몇 가지 정보를 더 알려주세요.",
                fallback_questions=["식단 목표가 무엇인가요? 예: 다이어트, 고단백, 채식"],
            )
            return {
                "status": "NEED_MORE_INFO",
                "step": "CONDITION_INPUT",
                "response": message,
                "questions": questions,
            }
        if tool_result is not None:
            return await self._response_from_tool_hub(
                tool_result,
                regenerated=_is_regenerated_recipe_request(session.user_conditions),
                ingredient_edit_summary=_ingredient_edit_summary_from_state(state),
            )

        generated_recommendation = await self._generate_recommendation(
            user_message=user_message,
            session=session,
        )
        _generated_message, recipe_sets = generated_recommendation
        completion_message = _with_ingredient_edit_confirmation(
            _completion_selection_message(
                regenerated=_is_regenerated_recipe_request(session.user_conditions)
            ),
            _ingredient_edit_summary_from_state(state),
        )
        if not await self._guardrail_validator.validate_output(completion_message):
            raise LLMResponseError("NeMo Guardrails가 최종 응답을 차단했습니다.")
        return {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": completion_message,
            "data": {"recipe_sets": recipe_sets},
            "next_action": "FEEDBACK_OR_SET_SELECTION",
            "available_set_ids": [
                recipe_set["set_key"]
                for recipe_set in recipe_sets
                if isinstance(recipe_set.get("set_key"), str)
            ],
        }

    async def _response_from_tool_hub(
        self,
        raw_result: object,
        *,
        regenerated: bool = False,
        ingredient_edit_summary: Sequence[str] = (),
    ) -> dict[str, object]:
        """Tool Hub 결과를 기존 ChatResponse 계약으로 검증·변환합니다.

        Tool 결과는 외부 입력과 같은 비신뢰 경계로 취급한다. 따라서 FE에 반환하기
        전에 NeMo rail과 기존 Pydantic DTO를 모두 통과시킨다.
        """

        if not isinstance(raw_result, Mapping):
            raise LLMResponseError("Tool Hub 결과 형식이 올바르지 않습니다.")
        tool_result_payload = json.dumps(raw_result, ensure_ascii=False, default=str)
        if not await self._guardrail_validator.validate_tool_result(tool_result_payload):
            raise LLMResponseError("Tool Hub 결과가 안전성 검사를 통과하지 못했습니다.")

        completion_message = raw_result.get("response")
        raw_data = raw_result.get("data")
        if not isinstance(completion_message, str) or not isinstance(raw_data, Mapping):
            raise LLMResponseError("Tool Hub 결과 형식이 올바르지 않습니다.")
        try:
            validate_completion_output(completion_message)
            recommendation = RecommendationData.model_validate(raw_data)
        except (SafetyViolationError, ValueError) as error:
            raise LLMResponseError("Tool Hub 결과 검증에 실패했습니다.") from error
        completion_message = _with_ingredient_edit_confirmation(
            _completion_selection_message(regenerated=regenerated),
            ingredient_edit_summary,
        )
        validate_completion_output(completion_message)
        if not await self._guardrail_validator.validate_output(completion_message):
            raise LLMResponseError("NeMo Guardrails가 Tool Hub 완료 응답을 차단했습니다.")

        return {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": completion_message,
            "data": recommendation.model_dump(),
            "next_action": "FEEDBACK_OR_SET_SELECTION",
            "available_set_ids": [
                get_recipe_set_key(recipe_set, index)
                for index, recipe_set in enumerate(recommendation.recipe_sets)
            ],
        }

    async def _clarification_response(
        self,
        *,
        response_kind: str,
        user_message: str,
        state: Mapping[str, object],
        session: ChatSessionState,
        fallback_response: str,
        fallback_questions: list[str],
    ) -> tuple[str, list[str]]:
        """LLM 안내를 검증하고, 실패하면 단계별 고정 fallback을 반환합니다.

        모델은 표시 문구만 생성한다. 호출 실패·잘못된 JSON·안전성 검증 실패가 발생해도
        FE가 사용하는 상태 전이와 응답 구조는 결정적인 fallback으로 유지한다.
        """

        generator = getattr(self._llm_responder, "generate_clarification_response", None)
        if not callable(generator):
            return fallback_response, fallback_questions

        context = {
            "has_image": bool(state.get("has_image")),
            "has_conditions": bool(state.get("has_conditions")),
            "has_confirmed_ingredients": bool(state.get("has_confirmed_ingredients")),
            "ingredient_confirmation": state.get("ingredient_confirmation"),
            "ingredient_candidates": _ingredient_payload(session.ingredient_candidates),
            "user_conditions": dict(session.user_conditions or {}),
            "image_request_already_sent": bool(session.messages),
        }
        try:
            generated = await generator(response_kind, user_message, context)
            if not isinstance(generated, Mapping):
                raise ValueError("추가 입력 안내 응답 형식이 올바르지 않습니다.")
            message = generated.get("response")
            questions = generated.get("questions")
            if (
                not isinstance(message, str)
                or not message.strip()
                or isinstance(questions, (str, bytes))
                or not isinstance(questions, Sequence)
                or not questions
                or any(
                    not isinstance(question, str) or not question.strip()
                    for question in questions
                )
            ):
                raise ValueError("추가 입력 안내 응답 형식이 올바르지 않습니다.")
            message = message.strip()
            normalized_questions = [question.strip() for question in questions]
            # 재료·목적만 필수값이다. 생성 모델이 수량·시간·끼니·선호 같은 선택값을
            # 필수 질문처럼 되살려도 FE에 노출하지 않도록 단계와 무관하게 제거한다.
            normalized_questions = [
                question
                for question in normalized_questions
                if not _is_optional_condition_question(question)
            ]
            if _contains_optional_condition_question(message):
                # 본문에 선택값을 요구하는 문장이 남으면 질문 목록만 고쳐도 UX가
                # 일관되지 않으므로, 서버가 보장하는 단계별 fallback 문구를 사용한다.
                return fallback_response, fallback_questions
            if not normalized_questions:
                return fallback_response, fallback_questions
            validate_completion_output(message)
            for question in normalized_questions:
                validate_completion_output(question)
            if not await self._guardrail_validator.validate_output(
                "\n".join((message, *normalized_questions))
            ):
                return fallback_response, fallback_questions
            return message, normalized_questions
        except (LLMResponseError, SafetyViolationError, TypeError, ValueError):
            return fallback_response, fallback_questions

    async def _generate_recommendation(
        self,
        *,
        user_message: str,
        session: ChatSessionState,
    ) -> tuple[str, list[dict[str, object]]]:
        """기본 LLM 응답기가 제공하는 구조화 추천을 API 응답 데이터로 검증합니다.

        ``TOOL_HUB_ENABLED=false``이거나 provider가 주입되지 않은 fallback 경로에서만
        호출한다. 기본 완료 흐름은 Tool Hub 결과를 사용하며, 두 경로 모두 서버 DTO로
        다시 검증한다.
        """

        generator = getattr(self._llm_responder, "generate_recommendation", None)
        if not callable(generator):
            raise LLMResponseError("추천 생성기가 설정되지 않았습니다.")

        confirmed_ingredients = [
            {"name": ingredient.name, "amount": ingredient.amount}
            for ingredient in session.confirmed_ingredients
        ]
        generated = await generator(
            user_message,
            confirmed_ingredients,
            session.user_conditions,
        )
        if not isinstance(generated, Mapping):
            raise LLMResponseError("LLM 추천 응답 형식이 올바르지 않습니다.")

        message = generated.get("response")
        raw_data = generated.get("data")
        if not isinstance(message, str) or not isinstance(raw_data, Mapping):
            raise LLMResponseError("LLM 추천 응답 형식이 올바르지 않습니다.")
        try:
            validate_completion_output(message)
            recommendation = RecommendationData.model_validate(raw_data)
        except (SafetyViolationError, ValueError) as error:
            raise LLMResponseError("LLM 추천 응답 검증에 실패했습니다.") from error

        recipe_sets = recommendation.model_dump().get("recipe_sets")
        if not isinstance(recipe_sets, list):
            raise LLMResponseError("LLM 추천 레시피 세트가 없습니다.")
        return message, recipe_sets

    async def _extract_ingredients(
        self,
        *,
        user_message: str,
        attachments: Sequence[Mapping[str, str]],
        current_ingredients: Sequence[IngredientCandidate] = (),
    ) -> tuple[IngredientCandidate, ...]:
        """LLM 재료 추출 결과를 내부 세션 DTO로 검증합니다."""

        extractor = getattr(self._llm_responder, "extract_ingredients", None)
        if not callable(extractor):
            raise LLMResponseError("재료 추출기가 설정되지 않았습니다.")
        current_payload = [
            {"name": ingredient.name, "amount": ingredient.amount}
            for ingredient in current_ingredients
        ]
        extracted = await extractor(user_message, attachments, current_payload)
        if isinstance(extracted, (str, bytes)) or not isinstance(extracted, Sequence):
            raise LLMResponseError("재료 추출 응답 형식이 올바르지 않습니다.")
        normalized: list[IngredientCandidate] = []
        for item in extracted:
            if not isinstance(item, Mapping):
                raise LLMResponseError("재료 추출 응답 형식이 올바르지 않습니다.")
            name = item.get("name")
            amount = item.get("amount")
            if not isinstance(name, str) or not name.strip():
                raise LLMResponseError("재료 추출 응답 형식이 올바르지 않습니다.")
            if amount is None or (isinstance(amount, str) and not amount.strip()):
                amount = "수량 미정"
            if not isinstance(amount, str):
                raise LLMResponseError("재료 추출 응답 형식이 올바르지 않습니다.")
            normalized.append(IngredientCandidate(name=name, amount=amount))
        return tuple(normalized)

    async def _read_ingredient_confirmation(
        self,
        user_message: str,
        proposed_ingredients: Sequence[IngredientCandidate],
        *,
        acknowledged_ingredient_names: Sequence[str] = (),
        pending_ingredient_correction: str | None = None,
        pending_ingredient_action: PendingIngredientAction | None = None,
    ) -> IngredientConfirmationRead:
        """Jev의 자연어 판단을 우선하고, 서버는 상태 변경만 검증합니다.

        Jev는 목록 전체 확정·수정·재인식 여부처럼 문맥 판단이 필요한 일을 맡는다.
        서버의 표현별 parser는 Jev가 불명확하거나 사용할 수 없을 때의 fallback 및
        이미 질문한 추가·교체의 후속 답변을 연결하는 용도로만 사용한다.
        """

        acknowledged_names = _known_acknowledged_ingredients(
            acknowledged_ingredient_names,
            proposed_ingredients,
        )

        def read(
            intent: Literal["confirmed", "rejected", "edited", "unclear"],
            *,
            confidence: float = 1.0,
            guidance: str | None = None,
            acknowledged: Sequence[str] = acknowledged_names,
            pending: str | None = pending_ingredient_correction,
            pending_action: PendingIngredientAction | None = pending_ingredient_action,
            extraction_message: str | None = None,
        ) -> IngredientConfirmationRead:
            return IngredientConfirmationRead(
                decision=IngredientConfirmationDecision(intent=intent, confidence=confidence),
                guidance=guidance,
                acknowledged_ingredient_names=tuple(acknowledged),
                pending_ingredient_correction=pending,
                pending_ingredient_action=pending_action,
                extraction_message=extraction_message,
            )

        if pending_ingredient_action:
            # 직전 질문에서 이미 재료와 동작을 정했으므로 "추가해줘"·"빼줘"처럼
            # 대상이 생략된 답변은 일반 수정 요청보다 먼저 그 대기 항목에 연결한다.
            # 그렇지 않으면 추출기에 재료명 없는 "추가해줘"만 전달돼 목록이 바뀌지 않는다.
            if _confirms_pending_ingredient_action(
                user_message, pending_ingredient_action.action
            ):
                name = pending_ingredient_action.ingredient_name
                action_message = (
                    f"{name} 추가해줘."
                    if pending_ingredient_action.action == "add"
                    else f"{name} 빼줘."
                )
                return read(
                    "edited",
                    acknowledged=(),
                    pending=None,
                    pending_action=None,
                    extraction_message=action_message,
                )
            if _has_explicit_ingredient_mutation_expression(user_message):
                return read("edited", acknowledged=(), pending=None, pending_action=None)
            if _is_action_rejection(user_message):
                return read(
                    "unclear",
                    guidance=(
                        "알겠어요. 현재 목록은 그대로 둘게요. "
                        "이 목록으로 재료를 확정할까요? 더 추가하거나 수정할 내용이 있다면 말씀해주세요."
                    ),
                    pending=None,
                    pending_action=None,
                )
            return read(
                "unclear",
                guidance=(
                    f"{_with_topic_particle(pending_ingredient_action.ingredient_name)} "
                    f"{_pending_action_question(pending_ingredient_action.action)}"
                ),
            )

        if pending_ingredient_correction:
            if _is_replacement_answer(user_message):
                # 후속 답변이 "소고기"처럼 짧아도 추출 모델에는 교체 문맥을 명시해
                # 기존 목록 전체를 유지하면서 해당 후보만 바꾸도록 한다.
                extraction_message = (
                    f"{pending_ingredient_correction} 대신 "
                    f"{user_message.strip()}로 바꿔줘."
                )
                return read(
                    "edited",
                    pending=None,
                    acknowledged=(),
                    extraction_message=extraction_message,
                )
            return read(
                "unclear",
                guidance=(
                    f"{pending_ingredient_correction} 대신 어떤 재료인지 알려주세요."
                ),
            )

        candidate_payload = [
            {"name": ingredient.name, "amount": ingredient.amount}
            for ingredient in proposed_ingredients
        ]
        jev_decision = await self._ingredient_confirmation_evaluator.evaluate(
            user_message,
            candidate_payload,
        )
        if jev_decision is not None and jev_decision.intent != "unclear":
            # Jev가 충분한 신뢰도로 의미를 판단한 경우, "빼줘" 같은 표면 표현을
            # 다시 정규식으로 분류하지 않는다. 수정 내용은 다음 단계의 구조화
            # 추출 결과를 서버가 검증한 뒤에만 상태에 반영한다.
            return read(
                jev_decision.intent,
                confidence=jev_decision.confidence,
                acknowledged=(),
                pending=None,
                pending_action=None,
            )

        # Jev가 불확실하거나 비활성일 때만 기존의 보수적 fallback을 사용한다.
        # 이 경로는 모델 판단을 덮어쓰는 주 경로가 아니라 서비스 연속성 장치다.
        candidate_replies = _candidate_subject_replies(user_message, proposed_ingredients)
        negative_names = [name for name, sentiment in candidate_replies if sentiment == "negative"]
        positive_names = [name for name, sentiment in candidate_replies if sentiment == "positive"]
        if negative_names and not _has_explicit_ingredient_mutation_expression(user_message):
            if _has_unlisted_replacement_answer(
                user_message,
                negative_names=negative_names,
                candidates=proposed_ingredients,
            ):
                # "연어가 아니고 참치야"처럼 기존 후보가 아닌 대체 재료까지 이미
                # 말한 경우에는 다시 묻지 않고 구조화 추출기로 최종 목록을 갱신한다.
                return read("edited", acknowledged=(), pending=None)
            acknowledged_names = _merge_acknowledged_ingredient_names(
                acknowledged_names,
                positive_names,
                proposed_ingredients,
            )
            incorrect_name = negative_names[0]
            prefix = _acknowledgement_prefix(positive_names)
            return read(
                "unclear",
                guidance=(
                    f"{prefix}{_with_subject_particle(incorrect_name)} 아니군요. "
                    f"{incorrect_name} 대신 어떤 재료인지 알려주세요."
                ),
                acknowledged=acknowledged_names,
                pending=incorrect_name,
            )

        if positive_names and not _has_explicit_ingredient_mutation_expression(user_message):
            acknowledged_names = _merge_acknowledged_ingredient_names(
                acknowledged_names,
                positive_names,
                proposed_ingredients,
            )
            if _all_candidates_acknowledged(acknowledged_names, proposed_ingredients):
                return read("confirmed", acknowledged=(), pending=None)
            return read(
                "unclear",
                guidance=(
                    f"{_acknowledgement_prefix(positive_names)}"
                    "나머지 재료도 모두 맞는지 알려주세요. 모두 맞다면 '맞아'라고 답하거나 "
                    "수정할 재료를 말씀해주세요."
                ),
                acknowledged=acknowledged_names,
                pending=None,
            )

        if _is_explicit_final_ingredient_confirmation(user_message):
            return read("confirmed", acknowledged=(), pending=None)
        if _is_generic_ingredient_rejection(user_message):
            return read(
                "unclear",
                guidance=(
                    "어떤 재료를 수정할지 알려주세요. "
                    "예: '돼지고기는 아니고 소고기예요.'"
                ),
            )
        if _has_candidate_mutation_expression(user_message, proposed_ingredients):
            # "양배추는 빼", "양배추는 없엉"처럼 후보가 분명한 삭제·교체는
            # Jev의 모호한 분류를 기다리지 않고 수정 추출기로 보낸다.
            return read("edited", acknowledged=(), pending=None)
        if _has_explicit_ingredient_mutation_expression(user_message):
            # "피망추가해줘", "방울토마토 제거해줘"처럼 새 재료 또는 삭제할
            # 재료를 사용자가 직접 지정한 요청도 Jev 분류 결과와 무관하게 수정으로
            # 처리한다. 그렇지 않으면 Jev의 rejected 결과가 후보를 초기화할 수 있다.
            return read("edited", acknowledged=(), pending=None)

        if _looks_like_recipe_name(user_message):
            return read(
                "unclear",
                guidance=(
                    "말씀하신 내용은 요리명으로 보여요. 지금은 재료를 확인하는 단계예요. "
                    "먼저 재료 목록을 확정한 뒤 원하시는 요리를 말씀해주세요."
                ),
            )

        mentioned_ingredient = _bare_ingredient_name(user_message)
        if mentioned_ingredient:
            existing = next(
                (
                    candidate
                    for candidate in proposed_ingredients
                    if _ingredient_candidate_key(candidate.name)
                    == _ingredient_candidate_key(mentioned_ingredient)
                ),
                None,
            )
            if existing:
                return read(
                    "unclear",
                    guidance=(
                        f"{_with_topic_particle(existing.name)} 현재 재료 목록에 있어요. "
                        f"{_with_object_particle(existing.name)} 뺄까요?"
                    ),
                    pending_action=PendingIngredientAction(
                        action="remove", ingredient_name=existing.name
                    ),
                )
            return read(
                "unclear",
                guidance=(
                    f"{_with_topic_particle(mentioned_ingredient)} 현재 재료 목록에 없어요. "
                    f"{_with_object_particle(mentioned_ingredient)} 추가할까요?"
                ),
                pending_action=PendingIngredientAction(
                    action="add", ingredient_name=mentioned_ingredient
                ),
            )

        decision = jev_decision or _fallback_ingredient_confirmation(user_message)
        if decision.intent in {"confirmed", "unclear"} and _has_ingredient_edit_expression(
            user_message
        ):
            # 수정 표현이 있는 복합 답변은 Jev가 실수로 confirmed라고 해도 먼저
            # 갱신해야 한다. 그 뒤에만 "확정지어줘" 의도를 처리할 수 있다.
            return read("edited", confidence=decision.confidence, acknowledged=(), pending=None)
        if (
            decision.intent == "unclear"
            and _is_explicit_final_ingredient_confirmation(user_message)
        ):
            # Jev가 짧은 확정 표현을 불명확하게 분류하더라도, 서버가 허용한
            # 목록 전체 확정 문구라면 후보를 확정한다. 이 검사는 수정 표현을
            # 우선 처리한 뒤에 수행하므로 "바꾸고 확정해줘"에는 적용되지 않는다.
            return read("confirmed", confidence=decision.confidence, acknowledged=(), pending=None)
        if (
            decision.intent == "confirmed"
            and not _is_explicit_final_ingredient_confirmation(user_message)
        ):
            # '재료', '없음'처럼 전체 목록을 확정했다고 보기 어려운 답변은 후보를
            # 유지하고 최종 확인 문구를 다시 안내한다.
            return read("unclear", confidence=decision.confidence)
        return read(
            decision.intent,
            confidence=decision.confidence,
            acknowledged=() if decision.intent in {"confirmed", "rejected", "edited"} else acknowledged_names,
            pending=None if decision.intent in {"confirmed", "rejected", "edited"} else pending_ingredient_correction,
        )


    async def _read_condition_state(
        self,
        user_message: str,
        previous_conditions: Mapping[str, object] | None,
    ) -> tuple[Mapping[str, object] | None, bool]:
        """현재 메시지와 기존 세션 조건을 병합하고 필수 조건 충족 여부를 반환합니다.

        조건은 여러 턴에 나뉘어 입력될 수 있다. 따라서 한 메시지에 목적과 시간이 모두
        있어야만 저장하던 방식 대신, 목적·조리 시간 슬롯을 세션에 누적한다. 목적만
        확보되면 추천 단계로 진행하고, 조리 시간은 있으면 필터에 사용하는 선택값이다.
        """

        extracted_slots = _extract_condition_slots(user_message)
        has_previous_conditions = bool(previous_conditions)
        if not extracted_slots and not has_previous_conditions:
            # Jev가 활성화된 경우 로컬 키워드에 없는 목적 표현도 판정할 수 있도록
            # 평가만 시도한다. 비활성화 상태에서는 아래에서 None으로 빠진다.
            decision = await self._condition_evaluator.evaluate(user_message)
            if decision is None or not decision.is_ready:
                return None, False
            merged_conditions: dict[str, object] = {
                "message": user_message,
                "purpose": user_message,
            }
            return merged_conditions, True

        merged_conditions = _merge_condition_state(
            previous_conditions,
            user_message,
            extracted_slots,
        )
        if merged_conditions is None:
            return None, False

        # 외부 I/O는 세션 저장소 잠금 밖에서 실행한다. 평가 입력은 누적된 원문이므로
        # 앞선 턴에서 받은 목적을 현재 턴의 시간 입력과 함께 판단할 수 있다.
        decision = await self._condition_evaluator.evaluate(
            str(merged_conditions.get("message", user_message))
        )
        if decision is not None:
            if decision.is_ready and not _has_required_condition(merged_conditions):
                # Jev가 로컬 키워드로 포착하지 못한 목적을 고신뢰로 판정한 경우에도
                # 다음 턴에서 잃지 않도록 원문을 purpose 슬롯에 보존한다.
                merged_conditions["purpose"] = user_message.strip()
            return merged_conditions, decision.is_ready
        return merged_conditions, _has_required_condition(merged_conditions)


def _next_session_state(
    *,
    session: ChatSessionState,
    next_step: WorkflowStep,
    user_conditions: Mapping[str, object] | None,
    ingredient_candidates: tuple[IngredientCandidate, ...] | None = None,
    confirmed_ingredients: tuple[IngredientCandidate, ...] | None = None,
    acknowledged_ingredient_names: tuple[str, ...] | None = None,
    pending_ingredient_correction: str | None = None,
    pending_ingredient_action: PendingIngredientAction | None = None,
    messages: tuple[ConversationMessage, ...] | None = None,
    summary: str | None = None,
) -> ChatSessionState:
    """단계 전이 중 얻은 데이터를 Tool 호출용 세션 상태에 안전하게 보존합니다."""

    next_session = replace(session, step=next_step)
    if messages is not None:
        next_session = replace(next_session, messages=messages)
    if summary is not None:
        next_session = replace(next_session, summary=summary)
    if ingredient_candidates is not None:
        next_session = replace(
            next_session,
            ingredient_candidates=ingredient_candidates,
            pending_ingredient_correction=pending_ingredient_correction,
            pending_ingredient_action=pending_ingredient_action,
        )
    if confirmed_ingredients is not None:
        next_session = replace(next_session, confirmed_ingredients=confirmed_ingredients)
    if acknowledged_ingredient_names is not None:
        next_session = replace(
            next_session,
            acknowledged_ingredient_names=acknowledged_ingredient_names,
        )
    if next_step == "WAITING_IMAGE":
        next_session = replace(
            next_session,
            ingredient_candidates=tuple(),
            confirmed_ingredients=tuple(),
            acknowledged_ingredient_names=tuple(),
            pending_ingredient_correction=None,
            pending_ingredient_action=None,
        )
    if user_conditions is not None:
        return replace(next_session, user_conditions=user_conditions)
    return next_session


def _conversation_message(
    role: Literal["user", "assistant"], content: object
) -> ConversationMessage:
    """응답을 세션 이력에 저장할 메시지 DTO로 정규화합니다."""

    return {"role": role, "content": str(content)}


def _stored_recommendation(session: ChatSessionState) -> RecommendationData | None:
    """세션에 저장된 최신 추천 결과를 서버 DTO로 다시 검증합니다."""

    if not isinstance(session.recommendation_data, Mapping):
        return None
    try:
        return RecommendationData.model_validate(session.recommendation_data)
    except ValueError:
        return None


def _store_recommendation_if_present(
    session: ChatSessionState, response: Mapping[str, object]
) -> ChatSessionState:
    """성공 추천을 저장해 다음 턴의 피드백·세트 선택 기준으로 사용합니다."""

    if response.get("status") != "SUCCESS":
        return session
    raw_data = response.get("data")
    if not isinstance(raw_data, Mapping):
        return session
    try:
        recommendation = RecommendationData.model_validate(raw_data)
    except ValueError:
        return session
    return replace(
        session,
        recommendation_data=recommendation.model_dump(),
        selected_set_id=None,
        pdf_url=None,
    )


def _find_recipe_set(
    recommendation: RecommendationData, set_id: str
) -> RecipeSet | None:
    """추천 DTO에서 내부 키 또는 표시 문구에 해당하는 세트를 찾습니다."""

    return next(
        (
            recipe_set
            for index, recipe_set in enumerate(recommendation.recipe_sets)
            if get_recipe_set_key(recipe_set, index) == set_id or recipe_set.set_id == set_id
        ),
        None,
    )


def _completed_feedback_response(
    *,
    recommendation: RecommendationData,
    response_message: str,
) -> dict[str, object]:
    """완료 상태를 유지하는 피드백 응답을 기존 FE 계약으로 조립합니다."""

    return {
        "status": "SUCCESS",
        "step": "COMPLETED",
        "response": response_message,
        "data": recommendation.model_dump(),
        "next_action": "FEEDBACK_OR_SET_SELECTION",
        "available_set_ids": [
            get_recipe_set_key(recipe_set, index)
            for index, recipe_set in enumerate(recommendation.recipe_sets)
        ],
    }


def _parse_selected_set_id(
    user_message: str, recommendation: RecommendationData
) -> str | None:
    """자연어 세트 선택을 검증된 추천 세트의 내부 키로 변환합니다."""

    available = [
        get_recipe_set_key(recipe_set, index)
        for index, recipe_set in enumerate(recommendation.recipe_sets)
    ]
    normalized_message = re.sub(r"[\s_-]+", "", user_message).upper()
    has_selection_intent = bool(_SELECTION_INTENT_PATTERN.search(user_message))
    for index, recipe_set in enumerate(recommendation.recipe_sets):
        set_key = get_recipe_set_key(recipe_set, index)
        normalized_key = re.sub(r"[\s_-]+", "", set_key).upper()
        normalized_display_id = re.sub(r"[\s_-]+", "", recipe_set.set_id).upper()
        if (
            normalized_key in normalized_message
            or normalized_display_id in normalized_message
        ) and (has_selection_intent or normalized_message in {normalized_key, normalized_display_id}):
            return set_key
        canonical_key_by_position = tuple(RECIPE_SET_DISPLAY_LABELS)[index]
        normalized_canonical_key = re.sub(r"[\s_-]+", "", canonical_key_by_position).upper()
        aliases = (
            *_KOREAN_SET_ID_ALIASES.get(normalized_key, ()),
            *_KOREAN_SET_ID_ALIASES.get(normalized_canonical_key, ()),
            recipe_set.set_id,
            recipe_set.label or "",
        )
        if has_selection_intent and any(
            re.sub(r"[\s_-]+", "", alias).upper() in normalized_message
            for alias in aliases
        ):
            return set_key

    number_match = re.search(
        r"(?<!\d)([1-9])\s*(?:번|번째)\s*(?:세트|걸|것)?\s*(?:으로|로)?",
        user_message,
    )
    if number_match and has_selection_intent:
        index = int(number_match.group(1)) - 1
        if 0 <= index < len(available):
            return available[index]
    if has_selection_intent and re.search(
        r"첫\s*(?:번째|번)(?:\s*(?:세트|걸|것))?\s*(?:으로|로)?", user_message
    ):
        return available[0] if available else None
    if has_selection_intent and re.search(
        r"둘째|두\s*(?:번째|번)(?:\s*(?:세트|걸|것))?\s*(?:으로|로)?",
        user_message,
    ):
        return available[1] if len(available) > 1 else None
    return None


def _merge_feedback_conditions(
    previous_conditions: Mapping[str, object] | None,
    user_message: str,
    feedback_plan: FeedbackPlan,
) -> dict[str, object]:
    """Jev Plan 이후 피드백 원문과 새 조건 슬롯을 기존 조건에 누적합니다."""

    merged = dict(previous_conditions or {})
    previous_message = merged.get("message", "")
    feedback_message = user_message.strip()
    if isinstance(previous_message, str) and previous_message.strip():
        merged["message"] = "\n".join(
            [previous_message.strip(), f"[사용자 피드백] {feedback_message}"]
        )
    else:
        merged["message"] = feedback_message
    merged.update(_extract_condition_slots(feedback_message))
    history = merged.get("feedback_history", [])
    history_values = list(history) if isinstance(history, list) else []
    history_values.append(feedback_message)
    merged["feedback_history"] = history_values[-10:]
    merged["feedback_plan"] = feedback_plan.intent
    return merged


def _is_recipe_regeneration_request(user_message: str) -> bool:
    """현재 추천이 마음에 들지 않아 새 레시피를 원하는 의도를 제한적으로 찾습니다."""

    return bool(
        re.search(
            r"마음에\s*(?:안|들지)|별로|다른\s*(?:레시피|식단|메뉴)|"
            # 추천 완료 단계에서는 "다시해줘"도 직전 식단을 새로 추천해 달라는
            # 명확한 제어 명령이다. 이전 레시피 ID를 제외해 추천 노드로 되돌린다.
            r"(?:다시|새로)\s*(?:추천|만들|짜|보여|해)(?:줘|주세요)?",
            user_message,
            flags=re.IGNORECASE,
        )
    )


def _exclude_previously_recommended_recipes(
    conditions: Mapping[str, object], recommendation: RecommendationData
) -> dict[str, object]:
    """재생성 시 이전 카드의 레시피 ID를 Tool Hub 후보에서 제외합니다."""

    existing = conditions.get("excluded_recipe_ids", [])
    excluded_ids = [item for item in existing if isinstance(item, str) and item]
    excluded_ids.extend(
        recipe.recipe_id
        for recipe_set in recommendation.recipe_sets
        for recipe in recipe_set.recipes
    )
    return {
        **conditions,
        "excluded_recipe_ids": list(dict.fromkeys(excluded_ids)),
        "regenerated_recipe_request": True,
    }


def _is_regenerated_recipe_request(conditions: Mapping[str, object] | None) -> bool:
    """현재 완료 응답이 기존 레시피를 제외해 만든 재추천인지 반환합니다."""

    return bool(conditions and conditions.get("regenerated_recipe_request") is True)


def _apply_confirmation_decision(
    *,
    session: ChatSessionState,
    decision: IngredientConfirmationDecision,
    user_message: str,
) -> tuple[tuple[IngredientCandidate, ...], tuple[IngredientCandidate, ...]]:
    """분류된 의도에 따라 후보·확정 재료를 갱신합니다."""

    if decision.intent == "confirmed":
        # 후보를 확정 목록으로 이동해 사용자 확인 전 데이터가 Tool 입력에 남지 않게 한다.
        return tuple(), _merge_ingredient_candidates(
            session.confirmed_ingredients,
            session.ingredient_candidates,
        )
    if decision.intent == "rejected":
        # 추가 이미지에서 들어온 후보만 취소하고, 이전에 확정한 재료는 유지한다.
        return tuple(), session.confirmed_ingredients
    if decision.intent == "edited":
        return (
            _apply_ingredient_edit(session.ingredient_candidates, user_message),
            session.confirmed_ingredients,
        )
    return session.ingredient_candidates, session.confirmed_ingredients


def _fallback_ingredient_confirmation(user_message: str) -> IngredientConfirmationDecision:
    """Jev를 사용할 수 없을 때 확인 의도를 결정하는 최소 fallback입니다."""

    if _has_ingredient_edit_expression(user_message):
        return IngredientConfirmationDecision(intent="edited", confidence=1.0)
    if _is_generic_ingredient_rejection(user_message):
        return IngredientConfirmationDecision(intent="unclear", confidence=1.0)
    if re.search(r"아니|틀렸|잘못|다시|전혀", user_message):
        return IngredientConfirmationDecision(intent="rejected", confidence=1.0)
    if _is_explicit_final_ingredient_confirmation(user_message):
        return IngredientConfirmationDecision(intent="confirmed", confidence=1.0)
    if re.search(r"맞아요|맞습니다|맞아|전부 맞|다 맞|네[, ]*(맞|그렇)|확인했", user_message):
        return IngredientConfirmationDecision(intent="confirmed", confidence=1.0)
    return IngredientConfirmationDecision(intent="unclear", confidence=1.0)


def _has_ingredient_edit_expression(user_message: str) -> bool:
    """후보를 수정해야 하는 추가·삭제·교체 표현이 있는지 보수적으로 확인합니다."""

    return _has_explicit_ingredient_mutation_expression(user_message)


def _has_explicit_ingredient_mutation_expression(user_message: str) -> bool:
    """교체 대상·삭제·추가가 실제로 주어진 표현인지 확인합니다.

    ``계란은 아니고 삼겹살은 맞아``처럼 어떤 후보가 틀렸다는 말만 있는 경우에는
    목록을 추측해 바꾸지 않고, 무엇으로 바꿀지 추가로 질문해야 한다.
    """

    return bool(
        re.search(
            rf"추가|더 넣|{_INGREDIENT_REMOVAL_PATTERN}|교체|바꾸|바꿔|수정|"
            r"없어|없엉|없고|없는|대신|"
            r"자리(?:에는|는|에)?",
            user_message,
        )
    )


def _candidate_subject_replies(
    user_message: str,
    candidates: Sequence[IngredientCandidate],
) -> list[tuple[str, Literal["positive", "negative"]]]:
    """후보명에 직접 붙은 긍정·부정 답변만 찾아 목록 전체 판정과 분리합니다."""

    replies: list[tuple[str, Literal["positive", "negative"]]] = []
    for candidate in candidates:
        name = candidate.name.strip()
        if not name:
            continue
        escaped_name = re.escape(name)
        # "계란은 아니고", "돼지고기가 틀려"처럼 후보 바로 뒤에 나타난 부정만
        # 잡는다. 임의의 문장 속 같은 글자가 후보 확인으로 오인되지 않게 한다.
        if re.search(
            rf"{escaped_name}\s*(?:은|는|이|가)?\s*"
            r"(?:아니(?:야|에요|예요|고|라|ㄴ데)|틀려|틀렸|잘못)",
            user_message,
        ):
            replies.append((name, "negative"))
        elif re.search(
            rf"{escaped_name}\s*(?:은|는|이|가)?\s*"
            r"(?:맞아|맞아요|맞습니다|맞지)",
            user_message,
        ):
            replies.append((name, "positive"))
    return replies


def _has_unlisted_replacement_answer(
    user_message: str,
    *,
    negative_names: Sequence[str],
    candidates: Sequence[IngredientCandidate],
) -> bool:
    """'A가 아니고 B야'에서 B가 새 재료로 제시됐는지 보수적으로 확인합니다."""

    candidate_names = {
        _ingredient_candidate_key(candidate.name) for candidate in candidates
    }
    for negative_name in negative_names:
        match = re.search(
            rf"{re.escape(negative_name)}\s*(?:은|는|이|가)?\s*"
            r"아니(?:야|에요|예요|고|라)\s*(?P<replacement>.+)",
            user_message,
        )
        if match is None:
            continue
        replacement = match.group("replacement").strip(" .,!?")
        normalized_replacement = _normalize_confirmation_message(replacement)
        if not normalized_replacement or re.search(r"모르|없어|없엉|다시", normalized_replacement):
            continue
        # "계란은 아니고 삼겹살은 맞아"처럼 다른 기존 후보만 긍정한 경우에는
        # 대체 재료가 아직 없으므로 추가 질문을 유지한다.
        if any(
            _ingredient_candidate_key(candidate.name) in normalized_replacement
            for candidate in candidates
            if _ingredient_candidate_key(candidate.name) != _ingredient_candidate_key(negative_name)
        ):
            continue
        if _ingredient_candidate_key(replacement) not in candidate_names:
            return True
    return False


def _has_candidate_mutation_expression(
    user_message: str, candidates: Sequence[IngredientCandidate]
) -> bool:
    """후보명을 주어로 삼은 짧은 추가·삭제·교체 요청을 감지합니다."""

    for candidate in candidates:
        name = candidate.name.strip()
        if not name:
            continue
        if re.search(
            rf"{re.escape(name)}\s*(?:을|를|은|는|이|가)?\s*"
            r"(?:빼|제외|삭제|제거|없(?:어|엉|어요|습니다|고|는)|바꾸|교체|수정)",
            user_message,
        ):
            return True
    return False


def _known_acknowledged_ingredients(
    names: Sequence[str], candidates: Sequence[IngredientCandidate]
) -> tuple[str, ...]:
    """수정 뒤 사라진 후보는 개인 확인 기록에서도 제거합니다."""

    known_by_key = {_ingredient_candidate_key(candidate.name): candidate.name for candidate in candidates}
    return tuple(
        known_by_key[key]
        for name in names
        if (key := _ingredient_candidate_key(name)) in known_by_key
    )


def _merge_acknowledged_ingredient_names(
    existing_names: Sequence[str],
    added_names: Sequence[str],
    candidates: Sequence[IngredientCandidate],
) -> tuple[str, ...]:
    """후보 목록 순서대로 개인 확인 완료 재료를 병합합니다."""

    acknowledged_keys = {
        _ingredient_candidate_key(name) for name in (*existing_names, *added_names)
    }
    return tuple(
        candidate.name
        for candidate in candidates
        if _ingredient_candidate_key(candidate.name) in acknowledged_keys
    )


def _all_candidates_acknowledged(
    acknowledged_names: Sequence[str], candidates: Sequence[IngredientCandidate]
) -> bool:
    """현재 후보 전부를 하나씩 확인했는지 반환합니다."""

    candidate_keys = {_ingredient_candidate_key(candidate.name) for candidate in candidates}
    acknowledged_keys = {_ingredient_candidate_key(name) for name in acknowledged_names}
    return bool(candidate_keys) and candidate_keys.issubset(acknowledged_keys)


def _is_replacement_answer(user_message: str) -> bool:
    """교체 대기 질문에 대한 실제 재료 답변인지 최소한으로 검증합니다."""

    normalized = _normalize_confirmation_message(user_message)
    if not normalized or _is_explicit_final_ingredient_confirmation(user_message):
        return False
    if _is_generic_ingredient_rejection(user_message):
        return False
    return not bool(re.fullmatch(r"(?:모르겠어|모르겠어요|없어|없어요|없습니다|다시)", normalized))


def _is_generic_ingredient_rejection(user_message: str) -> bool:
    """대상을 말하지 않은 부정 또는 '재료 전체'에 대한 부정을 판별합니다."""

    normalized = _normalize_confirmation_message(user_message)
    generic_rejections = {
        "아니",
        "틀려",
        "틀렸어",
        "틀렸어요",
        "아니야",
        "아니요",
        "아뇨",
        "아니에요",
        "아닙니다",
        "잘못됐어",
        "잘못됐어요",
    }
    if normalized in generic_rejections:
        return True
    return bool(
        re.fullmatch(
            r"재료(?:목록)?(?:가|은|는)?(?:아니야|아니에요|아닙니다|틀려|틀렸어|틀렸어요|잘못됐어|잘못됐어요)",
            normalized,
        )
    )


def _acknowledgement_prefix(ingredient_names: Sequence[str]) -> str:
    """같은 답변에서 맞다고 확인된 후보를 짧게 복명복창합니다."""

    if not ingredient_names:
        return ""
    return f"{_with_topic_particle(', '.join(ingredient_names))} 맞는 재료로 확인했습니다. "


def _with_topic_particle(name: str) -> str:
    """간단한 한국어 조사 처리를 적용한 주제어를 반환합니다."""

    last_character = name[-1] if name else ""
    if "가" <= last_character <= "힣" and (ord(last_character) - ord("가")) % 28:
        return f"{name}은"
    return f"{name}는"


def _with_subject_particle(name: str) -> str:
    """간단한 한국어 조사 처리를 적용한 주어를 반환합니다."""

    last_character = name[-1] if name else ""
    if "가" <= last_character <= "힣" and (ord(last_character) - ord("가")) % 28:
        return f"{name}이"
    return f"{name}가"


def _with_object_particle(name: str) -> str:
    """간단한 한국어 목적격 조사 처리를 적용한 재료명을 반환합니다."""

    last_character = name[-1] if name else ""
    if "가" <= last_character <= "힣" and (ord(last_character) - ord("가")) % 28:
        return f"{name}을"
    return f"{name}를"


def _is_action_confirmation(user_message: str) -> bool:
    """추가·제외 여부를 다시 물은 직후의 긍정 답변을 판별합니다."""

    return _normalize_confirmation_message(user_message) in {
        "네",
        "예",
        "어",
        "응",
        "그래",
        "좋아",
        "좋아요",
        "맞아",
        "맞아요",
        "맞습니다",
        "해",
        "해줘",
        "해주세요",
        "진행해",
        "진행해주세요",
    }


def _confirms_pending_ingredient_action(
    user_message: str, action: Literal["add", "remove"]
) -> bool:
    """대기 중인 추가·제외 질문에 대상 없이 답한 표현을 판별합니다."""

    normalized = _normalize_confirmation_message(user_message)
    if _is_action_confirmation(user_message):
        return True
    if action == "add":
        return normalized in {
            "추가",
            "추가해줘",
            "추가해주세요",
            "넣어줘",
            "넣어주세요",
        }
    return normalized in {
        "빼",
        "빼줘",
        "빼주세요",
        "제거",
        "제거해줘",
        "제거해주세요",
        "삭제",
        "삭제해줘",
        "삭제해주세요",
        "제외",
        "제외해줘",
        "제외해주세요",
    }


def _is_action_rejection(user_message: str) -> bool:
    """추가·제외 여부를 다시 물은 직후의 거절 답변을 판별합니다."""

    return _normalize_confirmation_message(user_message) in {
        "아니",
        "아니요",
        "아뇨",
        "아니야",
        "아니에요",
        "말고",
        "하지마",
        "하지마요",
        "안돼",
        "싫어",
        "됐어",
    }


def _pending_action_question(action: Literal["add", "remove"]) -> str:
    """확인 중인 재료 동작을 다시 묻는 짧은 문장을 반환합니다."""

    return "추가할까요?" if action == "add" else "뺄까요?"


def _looks_like_recipe_name(user_message: str) -> bool:
    """재료 확인 단계에서 들어온 요리명·요리 요청을 보수적으로 감지합니다."""

    normalized = _normalize_confirmation_message(user_message)
    if not normalized:
        return False
    if any(marker in normalized for marker in ("요리", "레시피", "메뉴")):
        return True
    return len(normalized) > 1 and any(
        normalized.endswith(suffix) for suffix in _DISH_NAME_SUFFIXES
    )


def _bare_ingredient_name(user_message: str) -> str | None:
    """추가·제외 동작 없이 말한 짧은 재료명을 반환합니다.

    이 함수는 식품 사전을 이용해 재료를 단정하지 않는다. 재료명처럼 보이는 짧은
    답변만 골라 사용자에게 추가·제외 의도를 다시 묻는 용도다.
    """

    name = user_message.strip(" \t\n,.!?~")
    if not name or len(name) > 30:
        return None
    if not re.fullmatch(r"[가-힣A-Za-z0-9]+(?:\s+[가-힣A-Za-z0-9]+){0,2}", name):
        return None
    normalized = _normalize_confirmation_message(name)
    if normalized in {
        "음",
        "음음",
        "글쎄",
        "모르겠어",
        "모르겠어요",
        "아니",
        "아니요",
        "아뇨",
        "네",
        "예",
        "어",
        "응",
        "그래",
    }:
        return None
    if any(
        marker in normalized
        for marker in (
            "재료",
            "목록",
            "확정",
            "추가",
            "삭제",
            "제거",
            "제외",
            "빼",
            "바꾸",
            "교체",
            "수정",
            "다이어트",
            "고단백",
            "저탄",
            "채식",
        )
    ):
        return None
    return name


def _normalize_confirmation_message(user_message: str) -> str:
    """확인 표현 비교용으로 공백·문장 부호를 제거합니다."""

    return re.sub(r"[\s,.!~?]+", "", user_message).lower()


def _is_explicit_final_ingredient_confirmation(user_message: str) -> bool:
    """목록 전체를 확정했다는 짧고 명시적인 답변을 판별합니다.

    이 단계의 입력은 바로 앞 응답에서 보여 준 후보 목록에 대한 답변이다. 그래도
    특정 재료 하나에 대한 긍정이나 단어 하나만으로 목록 전체를 확정하지 않도록,
    재료·목록을 가리키는 표현과 확정·긍정 표현이 함께 있는 경우만 허용한다.
    """

    normalized = _normalize_confirmation_message(user_message)
    if any(
        marker in normalized
        for marker in ("확정하지마", "확정하지말", "확정하지않", "확정말고")
    ):
        return False
    return normalized in {
        "네",
        "예",
        "어",
        "응",
        "그래",
        "맞아",
        "맞아요",
        "맞습니다",
        "네맞아",
        "네맞아요",
        "네맞습니다",
        "네모두맞아요",
        "네모두맞습니다",
        "모두맞아",
        "모두맞아요",
        "모두맞습니다",
        "전부맞아",
        "전부맞아요",
        "전부맞습니다",
        "다맞아",
        "다맞아요",
        "다맞습니다",
        "없어",
        "없어요",
        "없습니다",
        "재료맞아",
        "재료맞아요",
        "재료맞습니다",
        "재료가맞아",
        "재료가맞아요",
        "재료가맞습니다",
        "목록확정",
        "재료목록확정",
        "확정",
        "확정해",
        "확정해줘",
        "확정해주세요",
        "확정할게",
        "확정할게요",
        "확정합니다",
        "네확정",
        "네확정할게요",
        "이대로확정",
        "이대로확정할게요",
        "이대로진행",
        "이대로진행할게요",
        "그대로진행",
        "그대로진행해주세요",
        "확정지어줘",
        "확정지어주세요",
        "확정지을게",
        "확정지을게요",
    } or bool(
        re.fullmatch(
            r"(?:이|현재|그|해당|위|사진속)?"
            r"(?:재료(?:목록|들)?|목록)"
            r"(?:가|이|은|는|을|으로|로)?"
            r"(?:맞(?:아|아요|습니다|지|죠|나요|습니까)?|"
            r"확정(?:해(?:줘|주세요)?|할게(?:요)?|합니다)?|"
            r"진행(?:해(?:줘|주세요)?|할게(?:요)?|합니다)?)",
            normalized,
        )
    )


def _requests_finalization_after_edit(user_message: str) -> bool:
    """수정과 함께 갱신된 목록의 확정까지 요청했는지 판별합니다."""

    normalized = re.sub(r"[\s,.!~]+", "", user_message).lower()
    if any(
        marker in normalized
        for marker in ("확정하지마", "확정하지말", "확정하지않", "확정말고")
    ):
        return False
    if _has_standalone_trailing_confirmation_token(user_message):
        # "파프리카 대신 피망 고"처럼 수정문 뒤에 단독으로 붙인 구어체
        # 진행 표현만 인정한다. "피망고" 같은 재료명 내부 문자열과는 충돌하지 않는다.
        return True
    return any(
        marker in normalized
        for marker in (
            "목록확정",
            "재료확정",
            "확정지어",
            "확정해",
            "확정할게",
            "확정합니다",
            "이대로진행",
            "이재료로확정",
            "현재재료로확정",
            "재료가맞",
            "이재료가맞",
        )
    )


def _has_standalone_trailing_confirmation_token(user_message: str) -> bool:
    """수정문 끝의 단독 '가자'·'고'만 진행 의도로 해석합니다."""

    return bool(
        re.search(r"(?:^|\s)(?:가자|고)[.!~?]*\s*$", user_message, flags=re.IGNORECASE)
    )


def _normalize_ingredient_edit_message(user_message: str) -> str:
    """추출기에 전달할 수정 문장에서 구어체 확정 꼬리말을 분리합니다.

    ``피망추가해줘``처럼 띄어쓰지 않은 동작어도 ``피망 추가해줘``로 보정한다.
    단, 재료명 중간의 문자열은 바꾸지 않으며 ``피망고``와 ``피망 고``를 구분한다.
    """

    normalized = re.sub(r"\s+(?:가자|고)[.!~?]*\s*$", "", user_message).strip()
    return re.sub(
        r"(?<=[가-힣A-Za-z0-9])(?=(?:추가|제거|제외|삭제|교체|수정|바꾸|바꿔|빼))",
        " ",
        normalized,
    )


def _apply_ingredient_edit(
    candidates: tuple[IngredientCandidate, ...], user_message: str
) -> tuple[IngredientCandidate, ...]:
    """LLM 추출 실패 시에도 기존 후보의 삭제·수량 변경만 제한적으로 반영합니다."""

    updated = list(candidates)
    for candidate in tuple(updated):
        if _is_explicit_candidate_removal(candidate.name, user_message):
            updated = [item for item in updated if item.name != candidate.name]
    return tuple(updated)


def _merge_ingredient_candidates(
    existing: Sequence[IngredientCandidate],
    additions: Sequence[IngredientCandidate],
) -> tuple[IngredientCandidate, ...]:
    """기존 재료와 새 이미지 재료를 순서대로 합치고 이름 중복을 제거합니다."""

    merged = list(existing)
    index_by_key = {
        _ingredient_candidate_key(candidate.name): index
        for index, candidate in enumerate(merged)
        if _ingredient_candidate_key(candidate.name)
    }
    for candidate in additions:
        candidate_key = _ingredient_candidate_key(candidate.name)
        if not candidate_key:
            continue
        existing_index = index_by_key.get(candidate_key)
        if existing_index is None:
            index_by_key[candidate_key] = len(merged)
            merged.append(candidate)
        elif (
            merged[existing_index].amount == "수량 미정"
            and candidate.amount != "수량 미정"
        ):
            # 기존 수량을 사용자가 이미 확인했다면 유지하고, 미정일 때만 새 이미지의
            # 더 구체적인 수량으로 보완한다.
            merged[existing_index] = candidate
    return tuple(merged)


def _exclude_confirmed_ingredients(
    candidates: Sequence[IngredientCandidate],
    confirmed: Sequence[IngredientCandidate],
) -> tuple[IngredientCandidate, ...]:
    """이미 확정된 재료가 후속 이미지 후보에 다시 들어오지 않게 제외합니다."""

    confirmed_keys = {_ingredient_candidate_key(item.name) for item in confirmed}
    return tuple(
        candidate
        for candidate in candidates
        if _ingredient_candidate_key(candidate.name) not in confirmed_keys
    )


def _validate_ingredient_edit_result(
    *,
    before: Sequence[IngredientCandidate],
    proposed: Sequence[IngredientCandidate],
    user_message: str,
) -> tuple[IngredientCandidate, ...] | None:
    """LLM이 제안한 목록 변경이 사용자 발화 범위를 벗어나지 않는지 확인합니다.

    의미 해석은 Jev와 구조화 추출기에 맡긴다. 이 함수는 모델이 언급되지 않은
    재료를 추가·삭제하거나 수량을 바꾸는 것만 막는다. 검증할 수 없으면 기존
    목록을 유지하고 사용자에게 다시 물어본다.
    """

    before_by_key = {
        _ingredient_candidate_key(item.name): item
        for item in before
        if _ingredient_candidate_key(item.name)
    }
    proposed_by_key: dict[str, IngredientCandidate] = {}
    for item in proposed:
        key = _ingredient_candidate_key(item.name)
        if not key or key in proposed_by_key:
            return None
        proposed_by_key[key] = item

    added = [
        item for key, item in proposed_by_key.items() if key not in before_by_key
    ]
    removed = [
        item for key, item in before_by_key.items() if key not in proposed_by_key
    ]
    quantity_changed = [
        item
        for key, item in proposed_by_key.items()
        if key in before_by_key and item.amount != before_by_key[key].amount
    ]
    changed = [*added, *removed, *quantity_changed]
    if not changed:
        # 수정으로 분류됐지만 실제 변경이 없다면 모델이 사용자 의도를 놓친 것이다.
        return None
    if any(not _ingredient_name_is_mentioned(item.name, user_message) for item in changed):
        return None
    return tuple(proposed_by_key.values())


def _ingredient_name_is_mentioned(name: str, user_message: str) -> bool:
    """조사·공백 차이를 정규화한 뒤 재료명이 사용자 문장에 있는지 확인합니다."""

    normalized_name = _ingredient_candidate_key(name)
    return bool(normalized_name and normalized_name in _normalize_confirmation_message(user_message))


def _reconcile_explicit_ingredient_edits(
    *,
    before: Sequence[IngredientCandidate],
    extracted: Sequence[IngredientCandidate],
    user_message: str,
) -> tuple[IngredientCandidate, ...]:
    """추출 결과에 빠진, 사용자가 이름을 직접 말한 수정만 보완합니다.

    OpenAI 추출은 전체 후보 목록과 수량을 정리하는 주 경로다. 이 보정은 ``피망추가``
    같은 명시 요청을 모델이 빠뜨렸을 때만 기존 후보와 합쳐 사용자의 의도를 보존한다.
    """

    updated = list(extracted)
    if _is_partial_ingredient_quantity_update(before, extracted, user_message):
        # 추출 모델이 수량을 바꾼 항목만 반환해도, 나머지 기존 후보를 누락하지 않는다.
        # 삭제·교체·추가 요청은 아래의 명시 표현 보정으로 별도 처리한다.
        extracted_by_key = {
            _ingredient_candidate_key(item.name): item for item in extracted
        }
        updated = [
            extracted_by_key.get(_ingredient_candidate_key(item.name), item)
            for item in before
        ]
    for candidate in before:
        if _is_explicit_candidate_removal(candidate.name, user_message):
            updated = [
                item
                for item in updated
                if _ingredient_candidate_key(item.name)
                != _ingredient_candidate_key(candidate.name)
            ]

    for source, target in _explicit_replacements_from_message(before, user_message):
        source_key = _ingredient_candidate_key(source.name)
        target_key = _ingredient_candidate_key(target)
        if not target_key or target_key == source_key:
            continue
        source_index = next(
            (
                index
                for index, item in enumerate(updated)
                if _ingredient_candidate_key(item.name) == source_key
            ),
            None,
        )
        target_exists = any(
            _ingredient_candidate_key(item.name) == target_key for item in updated
        )
        if source_index is not None:
            if target_exists:
                updated.pop(source_index)
            else:
                updated[source_index] = IngredientCandidate(
                    name=target,
                    amount=source.amount,
                )
        elif not target_exists:
            # 모델이 교체 대상을 이미 빼 버린 경우에도, 사용자가 명시한 새 재료는
            # 누락하지 않고 기존 수량을 이어받아 후보에 남긴다.
            updated.append(IngredientCandidate(name=target, amount=source.amount))

    for name in _explicit_addition_names(user_message):
        if not any(_ingredient_candidate_key(item.name) == _ingredient_candidate_key(name) for item in updated):
            updated.append(IngredientCandidate(name=name, amount="수량 미정"))
    return tuple(updated)


def _is_partial_ingredient_quantity_update(
    before: Sequence[IngredientCandidate],
    extracted: Sequence[IngredientCandidate],
    user_message: str,
) -> bool:
    """수량만 바꾼 부분 추출 결과인지 반환합니다.

    수량 수정에서 모델이 변경한 재료 하나만 반환하는 경우가 있다. 이때 전체 목록을
    덮어쓰면 나머지 후보가 사라지므로, 기존 목록의 항목을 갱신하는 경로로 합친다.
    삭제·교체·추가 표현은 의도적으로 이 보정에서 제외해 각 명시 보정이 처리하게 한다.
    """

    if (
        not before
        or not extracted
        or len(extracted) >= len(before)
        or not _QUANTITY_EXPRESSION_PATTERN.search(user_message)
        or re.search(r"추가|더\s*넣", user_message)
    ):
        return False

    before_by_key = {
        _ingredient_candidate_key(item.name): item for item in before
    }
    extracted_keys = [_ingredient_candidate_key(item.name) for item in extracted]
    if any(key not in before_by_key for key in extracted_keys):
        return False
    if any(_is_explicit_candidate_removal(item.name, user_message) for item in before):
        return False
    if _explicit_replacements_from_message(before, user_message):
        return False
    return any(
        before_by_key[key].amount != item.amount
        for key, item in zip(extracted_keys, extracted)
    )


def _is_explicit_candidate_removal(candidate_name: str, user_message: str) -> bool:
    """후보명을 직접 언급한 제외·제거 요청인지 반환합니다."""

    escaped_name = re.escape(candidate_name)
    return bool(
        re.search(
            rf"(?:{escaped_name}).{{0,16}}(?:{_INGREDIENT_REMOVAL_PATTERN})|"
            rf"(?:{_INGREDIENT_REMOVAL_PATTERN}).{{0,16}}(?:{escaped_name})",
            user_message,
        )
    )


def _explicit_replacements_from_message(
    candidates: Sequence[IngredientCandidate], user_message: str
) -> list[tuple[IngredientCandidate, str]]:
    """'A 대신 B'와 'A를 B로 바꿔'의 직접 교체만 추출합니다."""

    replacements: list[tuple[IngredientCandidate, str]] = []
    for candidate in candidates:
        source = re.escape(candidate.name)
        match = re.search(
            rf"{source}\s*(?:을|를|은|는|이|가)?\s*대신\s*"
            r"(?P<target>[가-힣A-Za-z][가-힣A-Za-z0-9]*?)(?:으로|로)\s*"
            r"(?:바꾸|바꿔|교체)",
            user_message,
        )
        if match is None:
            match = re.search(
                rf"{source}\s*(?:을|를|은|는|이|가)?\s*대신\s*"
                r"(?P<target>[가-힣A-Za-z][가-힣A-Za-z0-9]*)",
                user_message,
            )
        if match is None:
            match = re.search(
                rf"{source}\s*(?:을|를|은|는|이|가)?\s*"
                r"(?P<target>[가-힣A-Za-z][가-힣A-Za-z0-9]*?)(?:으로|로)\s*"
                r"(?:바꾸|바꿔|교체)",
                user_message,
            )
        if match is not None:
            target = _strip_replacement_target_ending(match.group("target"))
            if target:
                replacements.append((candidate, target))
    return replacements


def _strip_replacement_target_ending(target: str) -> str:
    """교체 대상 뒤에 붙은 짧은 서술형 종결어미를 재료명에서 분리합니다.

    ``연어 대신 참치야``에서 ``참치야`` 전체를 재료명으로 보정하면, 추출 모델의
    ``참치``와 서로 다른 재료로 취급되어 후보가 중복될 수 있다. 이 함수는 ``대신``
    교체 구문에서만 사용하므로 일반 재료 추출 결과는 바꾸지 않는다.
    """

    return re.sub(r"(?:이에요|예요|이야|야)$", "", target.strip())


def _explicit_addition_names(user_message: str) -> list[str]:
    """수량 없이 직접 말한 'OO 추가' 재료명을 보수적으로 읽습니다."""

    names: list[str] = []
    for match in re.finditer(
        r"(?P<name>[가-힣A-Za-z][가-힣A-Za-z0-9]*?)(?:도|을|를|은|는|이|가)?\s+추가",
        user_message,
    ):
        # '양파 1개 추가'는 LLM이 수량까지 읽어야 하므로 '개'를 재료명으로
        # 오인하는 이 보정 경로에서는 건너뛴다.
        if match.start() > 0 and user_message[match.start() - 1].isdigit():
            continue
        name = match.group("name")
        if name not in names:
            names.append(name)
    return names


def _summarize_ingredient_changes(
    *,
    before: Sequence[IngredientCandidate],
    after: Sequence[IngredientCandidate],
    user_message: str,
) -> list[str]:
    """수정 전·후 후보를 비교해 사용자가 검증할 수 있는 변경 요약을 만듭니다."""

    before_by_key = {_ingredient_candidate_key(item.name): item for item in before}
    after_by_key = {_ingredient_candidate_key(item.name): item for item in after}
    added = [item.name for item in after if _ingredient_candidate_key(item.name) not in before_by_key]
    removed = [item.name for item in before if _ingredient_candidate_key(item.name) not in after_by_key]
    amount_changes = [
        f"{item.name}({before_by_key[_ingredient_candidate_key(item.name)].amount} → {item.amount})"
        for item in after
        if (
            _ingredient_candidate_key(item.name) in before_by_key
            and before_by_key[_ingredient_candidate_key(item.name)].amount != item.amount
        )
    ]

    summary: list[str] = []
    replacement_source = _replacement_source_from_message(user_message, removed)
    remaining_added = list(added)
    remaining_removed = list(removed)
    if replacement_source is not None and len(added) == 1:
        replacement_target = added[0]
        summary.append(f"변경한 재료: {replacement_source} → {replacement_target}")
        remaining_added.remove(replacement_target)
        remaining_removed.remove(replacement_source)
    if remaining_added:
        summary.append(f"추가한 재료: {', '.join(remaining_added)}")
    if remaining_removed:
        summary.append(f"제외한 재료: {', '.join(remaining_removed)}")
    if amount_changes:
        summary.append(f"수량 변경: {', '.join(amount_changes)}")
    return summary


def _is_replacement_request(user_message: str) -> bool:
    """추가·제외 조합을 단순 삭제가 아닌 교체로 표현할 수 있는지 확인합니다."""

    return bool(re.search(r"교체|바꾸|바꿔|대신|자리", user_message))


def _replacement_source_from_message(
    user_message: str, removed_names: Sequence[str]
) -> str | None:
    """사용자 문장에서 실제로 교체 대상으로 언급한 제외 재료를 찾습니다."""

    for name in removed_names:
        if re.search(
            rf"{re.escape(name)}\s*(?:을|를|은|는|이|가)?\s*"
            r"(?:아니|교체|바꾸|바꿔|대신|자리)",
            user_message,
        ):
            return name
    if len(removed_names) == 1 and _is_replacement_request(user_message):
        return removed_names[0]
    return None


def _ingredient_edit_summary_from_state(state: Mapping[str, object]) -> list[str]:
    """Graph 상태에 든 이번 턴의 안전한 변경 요약만 사용자 문구에 사용합니다."""

    raw_summary = state.get("ingredient_edit_summary")
    if isinstance(raw_summary, (str, bytes)) or not isinstance(raw_summary, Sequence):
        return []
    return [item for item in raw_summary if isinstance(item, str) and item.strip()]


def _ingredient_candidate_key(name: str) -> str:
    """후보 전·후 비교에만 쓰는 재료명 정규화 키를 반환합니다."""

    return "".join(name.split()).lower()


def _ingredient_payload(
    candidates: tuple[IngredientCandidate, ...],
) -> list[dict[str, str]]:
    """세션의 immutable 후보를 FE 응답용 JSON 목록으로 변환합니다."""

    return [{"name": candidate.name, "amount": candidate.amount} for candidate in candidates]


_PURPOSE_PATTERN = re.compile(
    r"다이어트|체중.?감량|저칼로리|저탄고지|저탄수|고단백|벌크업|건강식|건강한|"
    r"채식|비건|당.?조절|일반식",
    flags=re.IGNORECASE,
)
_COOKING_TIME_PATTERN = re.compile(r"(?P<value>\d+)\s*(?P<unit>분|시간)")
_COOKING_TIME_QUESTION_PATTERN = re.compile(
    r"조리.{0,8}시간|요리.{0,8}시간|몇\s*분|몇\s*시간",
    flags=re.IGNORECASE,
)
_OPTIONAL_CONDITION_QUESTION_PATTERN = re.compile(
    r"수량|몇\s*(?:개|명|인분|끼|팩|봉|캔|모)|\b양\b|"
    r"조리.{0,8}시간|요리.{0,8}시간|몇\s*분|몇\s*시간|"
    r"하루.{0,8}(?:몇|끼)|끼니|선호|좋아하는|피하고\s*싶|제외하고\s*싶",
    flags=re.IGNORECASE,
)


def _extract_condition_slots(user_message: str) -> dict[str, object]:
    """현재 메시지에서 목적과 선택 조리 시간 슬롯을 추출합니다."""

    slots: dict[str, object] = {}
    if _PURPOSE_PATTERN.search(user_message):
        slots["purpose"] = user_message.strip()

    time_match = _COOKING_TIME_PATTERN.search(user_message)
    if time_match:
        value = int(time_match.group("value"))
        if time_match.group("unit") == "시간":
            value *= 60
        slots["cooking_time_minutes"] = value
    return slots


def _merge_condition_state(
    previous_conditions: Mapping[str, object] | None,
    user_message: str,
    extracted_slots: Mapping[str, object],
) -> dict[str, object] | None:
    """조건 슬롯과 자연어 원문을 세션 단위로 누적합니다."""

    if not previous_conditions and not extracted_slots:
        return None

    merged = dict(previous_conditions or {})
    if extracted_slots:
        previous_message = merged.get("message", "")
        if isinstance(previous_message, str) and previous_message.strip():
            messages = [previous_message.strip(), user_message.strip()]
            merged["message"] = "\n".join(dict.fromkeys(messages))
        else:
            merged["message"] = user_message.strip()
        merged.update(extracted_slots)
    return merged


def _has_required_condition(conditions: Mapping[str, object] | None) -> bool:
    """추천 실행에 필요한 식단 목적이 누적됐는지 확인합니다.

    이전 세션이나 테스트 fixture가 ``message``만 저장한 경우도 호환하기 위해 목적
    슬롯이 없으면 원문에서 목적 표현을 한 번 더 확인한다. 조리 시간은 필수가 아니다.
    """

    if not conditions:
        return False
    purpose = conditions.get("purpose")
    if isinstance(purpose, str) and purpose.strip():
        return True
    message = conditions.get("message", "")
    return isinstance(message, str) and bool(_PURPOSE_PATTERN.search(message))


def _completion_selection_message(*, regenerated: bool = False) -> str:
    """완료 응답에서 두 추천 세트 중 하나를 선택하도록 안내합니다."""

    prefix = (
        "기존 추천과 다른 레시피로 식단을 다시 추천했습니다. "
        if regenerated
        else "확정한 재료와 조건에 맞는 메인·반찬 식단을 추천했습니다. "
    )
    return f"{prefix}두 세트 중 하나를 선택해 주세요. 선택한 레시피의 상세 PDF를 생성해드릴게요."


def _with_ingredient_edit_confirmation(
    completion_message: str, ingredient_edit_summary: Sequence[str]
) -> str:
    """수정과 확정을 같은 답변에서 요청한 경우 완료 문구에 변경 내역을 보존합니다."""

    summary = [item.strip() for item in ingredient_edit_summary if item.strip()]
    if not summary:
        return completion_message
    summary_message = "\n\n".join(summary)
    return (
        f"{summary_message}\n\n변경 내용을 반영해 재료를 확정했습니다. "
        f"{completion_message}"
    )


def _is_cooking_time_question(question: str) -> bool:
    """선택값인 조리 시간을 필수 질문으로 요구하는 문구인지 확인합니다."""

    return bool(_COOKING_TIME_QUESTION_PATTERN.search(question))


def _is_optional_condition_question(question: str) -> bool:
    """필수값이 아닌 식단 조건을 요구하는 질문인지 확인합니다."""

    return bool(_OPTIONAL_CONDITION_QUESTION_PATTERN.search(question))


def _contains_optional_condition_question(text: str) -> bool:
    """안내 본문에 선택 조건을 필수처럼 요구하는 표현이 포함됐는지 확인합니다."""

    return _is_optional_condition_question(text) and bool(
        re.search(r"(?:알려|원하|있으신|얼마|몇|어떤|무엇|선호|피하|제외)", text)
    )


def _looks_like_ingredient_replacement(user_message: str) -> bool:
    """완료 후 메시지가 기존 재료 전체 교체를 명시하는지 판정합니다.

    ``스팸을 빼줘`` 같은 제외 피드백은 기존 Jev Plan 경로로 보내야 하므로,
    재료를 새 목록으로 바꾸겠다는 표현만 교체 분기로 보낸다.
    """

    if re.search(r"(?:빼|삭제|제외|피하|싫)", user_message):
        return False
    return bool(
        re.search(
            r"(?:재료|식재료).{0,20}(?:바꾸|변경|교체|대체)|"
            r"(?:바꾸|변경|교체|대체).{0,20}(?:재료|식재료)|"
            r"(?:바꿔|변경|교체|대체).{0,30}(?:다시|새로)?\s*(?:추천|만들|짜|해줘)|"
            r"(?:새|다른)\s*(?:재료|식재료)",
            user_message,
        )
    )


def _build_default_tool_provider() -> ToolRequestExecutor | None:
    """기본 앱에 연결할 Tool Hub provider를 설정에 따라 조립합니다."""

    settings = get_settings()
    if not settings.tool_hub_enabled:
        return None
    return build_default_tool_hub(settings)


chat_service = ChatService(tool_provider=_build_default_tool_provider())
