"""Chat API의 세션 관리와 LangGraph 실행을 연결합니다."""

from dataclasses import replace
from collections.abc import Mapping, Sequence
import json
import re
from typing import Literal, Protocol

from app.agent.graph import ChatState, WorkflowStep, chat_graph
from app.agent.tools.contracts import (
    ToolRequest,
    ToolRequestPreparationError,
    ToolResult,
    build_tool_request,
)
from app.core.config import get_settings
from app.core.observability import build_langsmith_run_config
from app.core.safety import (
    SafetyViolationError,
    validate_completion_output,
    validate_user_message,
)
from app.integrations.decision_engine.typesafe_jev import (
    ConditionReadinessDecision,
    IngredientConfirmationDecision,
    JevConditionReadinessEvaluator,
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
)
from app.schemas.chat import ChatRequest, RecommendationData


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

    async def evaluate(self, user_message: str) -> IngredientConfirmationDecision | None:
        """확정·거절·수정·모호함 중 하나를 반환합니다."""


class ToolHubProvider(Protocol):
    """BE2 Tool Hub가 제공해야 하는 비동기 실행 인터페이스입니다."""

    async def execute(self, request: ToolRequest) -> ToolResult:
        """정규화된 ToolRequest를 실행합니다."""


class ChatService:
    """세션 상태, LangGraph 전이, LLM 임시 추천 응답 생성을 연결합니다."""

    def __init__(
        self,
        llm_responder: LLMResponder | None = None,
        condition_evaluator: ConditionReadinessEvaluator | None = None,
        ingredient_confirmation_evaluator: IngredientConfirmationEvaluator | None = None,
        tool_provider: ToolHubProvider | None = None,
        session_repository: ChatSessionRepository | None = None,
        guardrail_validator: GuardrailValidator | None = None,
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
        # BE2 endpoint가 합의되기 전에는 provider를 호출하지 않는다. 테스트나 실제
        # 어댑터를 주입한 경우에만 ToolRequest를 전달해 임시 결과를 강제로 만들지 않는다.
        self._tool_provider = tool_provider
        self._session_repository = session_repository or InMemoryChatSessionRepository()
        self._guardrail_validator = guardrail_validator or NemoGuardrailService(settings)

    async def handle(self, request: ChatRequest) -> tuple[dict[str, object], int]:
        """요청을 한 단계 진행시키고, LLM 실패를 안전한 API 오류로 변환합니다."""

        try:
            validate_user_message(request.message)
        except SafetyViolationError:
            return {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}, 400
        if not await self._guardrail_validator.validate_input(request.message):
            return {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}, 400

        session = await self._session_repository.get(request.session_id)
        previous_step = session.step
        request_conditions = await self._read_conditions_from_message(request.message)
        effective_conditions = request_conditions or session.user_conditions
        confirmation_decision: IngredientConfirmationDecision | None = None
        candidate_ingredients = session.ingredient_candidates
        confirmed_ingredients = session.confirmed_ingredients
        extracted_ingredients: tuple[IngredientCandidate, ...] | None = None
        if previous_step == "WAITING_INGREDIENT_CONFIRM":
            confirmation_decision = await self._read_ingredient_confirmation(request.message)
            candidate_ingredients, confirmed_ingredients = _apply_confirmation_decision(
                session=session,
                decision=confirmation_decision,
                user_message=request.message,
            )
            if confirmation_decision.intent == "edited" and not candidate_ingredients:
                # 후보를 모두 제거한 수정은 확인 목록을 만들 수 없으므로 재촬영으로 전환한다.
                confirmation_decision = IngredientConfirmationDecision(
                    intent="rejected", confidence=confirmation_decision.confidence
                )
            if confirmation_decision.intent == "edited":
                try:
                    extracted_ingredients = await self._extract_ingredients(
                        user_message=request.message,
                        attachments=(),
                        current_ingredients=session.ingredient_candidates,
                    )
                except LLMResponseError:
                    return {
                        "status": "ERROR",
                        "response": "재료 입력을 해석하는 중 오류가 발생했습니다.",
                    }, 500
                # 빈 결과는 사용자가 전체 후보를 삭제한 경우가 아니라 모델이 수정 내용을
                # 해석하지 못한 경우일 수 있으므로, 기존 후보를 보존해 빈 확인 응답을 막는다.
                if extracted_ingredients:
                    candidate_ingredients = extracted_ingredients
        elif previous_step == "WAITING_IMAGE" and not request.attachments:
            # 새 세션의 첫 요청에서는 이미지 우선 UX를 지킨다. 첫 응답으로 이미지
            # 요청을 보낸 뒤 같은 세션에서 사진이 없다고 답한 경우에만 자연어 재료를
            # LLM으로 추출해 확정 재료로 저장한다.
            if session.messages:
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
                    # 이미지 인식 후보가 아니라 사용자 명시 입력이므로 별도 확인 없이
                    # 확정 재료로 보관한다.
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
                    "status": "ERROR",
                    "response": "이미지에서 재료를 확인할 수 없습니다.",
                }, 500
            candidate_ingredients = extracted_ingredients
        messages = [*session.messages, {"role": "user", "content": request.message}]
        state: ChatState = {
            "step": previous_step,
            "messages": messages,
            "summary": session.summary,
            "has_image": bool(request.attachments),
            "has_conditions": effective_conditions is not None,
            "has_confirmed_ingredients": bool(confirmed_ingredients),
            "ingredient_confirmation": (
                confirmation_decision.intent if confirmation_decision is not None else None
            ),
        }
        if previous_step == "WAITING_CONDITIONS":
            state["condition_ready"] = effective_conditions is not None

        trace_config = build_langsmith_run_config(
            self._settings,
            session_id=request.session_id,
            workflow_step=previous_step,
            has_image=bool(request.attachments),
            has_conditions=effective_conditions is not None,
            has_confirmed_ingredients=bool(confirmed_ingredients),
            attachment_count=len(request.attachments or []),
        )
        if trace_config is None:
            result = await chat_graph.ainvoke(state)
        else:
            result = await chat_graph.ainvoke(state, config=trace_config)
        next_session = _next_session_state(
            session=session,
            next_step=result["step"],
            user_conditions=request_conditions,
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
            messages=tuple(result.get("messages", messages)),
            summary=str(result.get("summary", session.summary)),
        )
        await self._session_repository.save(request.session_id, next_session)

        try:
            if result["step"] == "COMPLETED":
                tool_error = await self._execute_tool_request(
                    session_id=request.session_id,
                    session=next_session,
                )
                if tool_error is not None:
                    return tool_error, 500
            response = await self._to_response(result, request.message, next_session)
        except LLMResponseError:
            return {"status": "ERROR", "response": "추천 응답 생성 중 오류가 발생했습니다."}, 500

        next_session = replace(
            next_session,
            messages=(
                *next_session.messages,
                _conversation_message("assistant", response["response"]),
            ),
        )
        await self._session_repository.save(request.session_id, next_session)
        return response, 200

    async def _to_response(
        self,
        state: Mapping[str, object],
        user_message: str,
        session: ChatSessionState,
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
                    "사용 가능한 재료와 수량을 알려주세요.",
                    "식단 목표는 무엇인가요? 예: 다이어트, 고단백, 채식",
                    "조리 가능한 시간은 얼마나 되나요? 예: 20분 이내",
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
                    "메시지에 조리 가능한 시간을 알려주세요. 예: 20분 이내",
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
            if confirmation == "edited":
                message = "수정된 재료 목록을 확인해주세요."
            elif confirmation == "unclear":
                message = "재료가 모두 맞는지, 수정할 재료가 있는지 알려주세요."
            else:
                message = "AI가 인식한 재료를 확인해주세요."
            return {
                "status": "NEED_MORE_INFO",
                "step": "INGREDIENT_CONFIRM",
                "response": message,
                "ingredients": _ingredient_payload(session.ingredient_candidates),
            }
        if response_kind == "CONDITION_INPUT":
            message, questions = await self._clarification_response(
                response_kind=response_kind,
                user_message=user_message,
                state=state,
                session=session,
                fallback_response="추천을 위해 몇 가지 정보를 더 알려주세요.",
                fallback_questions=["식단 목표가 무엇인가요?", "조리 가능한 시간은 얼마나 되나요?"],
            )
            return {
                "status": "NEED_MORE_INFO",
                "step": "CONDITION_INPUT",
                "response": message,
                "questions": questions,
            }
        generated_recommendation = await self._generate_recommendation(
            user_message=user_message,
            session=session,
        )
        completion_message, recipe_sets = generated_recommendation
        if not await self._guardrail_validator.validate_output(completion_message):
            raise LLMResponseError("NeMo Guardrails가 최종 응답을 차단했습니다.")
        return {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": completion_message,
            "data": {"recipe_sets": recipe_sets},
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

        BE2가 준비되면 이 메서드의 호출 경계를 Recipe·Nutrition·Shopping·RAG 결과
        어댑터로 교체한다. 추천 데이터는 코드에 내장하지 않고 LLM 또는 BE2 결과에서
        받아 서버 DTO로 검증한다.
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
            if not isinstance(amount, str) or not amount.strip():
                raise LLMResponseError("재료 추출 응답 형식이 올바르지 않습니다.")
            normalized.append(IngredientCandidate(name=name, amount=amount))
        return tuple(normalized)

    async def _read_ingredient_confirmation(
        self, user_message: str
    ) -> IngredientConfirmationDecision:
        """Jev 판정을 우선하고, 장애·비활성 시 제한적인 로컬 판정으로 fallback합니다."""

        decision = await self._ingredient_confirmation_evaluator.evaluate(user_message)
        if decision is not None:
            return decision
        return _fallback_ingredient_confirmation(user_message)

    async def _execute_tool_request(
        self, *, session_id: str, session: ChatSessionState
    ) -> dict[str, str] | None:
        """확정 재료와 조건을 BE2 계약으로 만들어 provider에 전달합니다."""

        if self._tool_provider is None:
            # BE2 endpoint가 아직 없을 때는 임의의 fake 결과를 만들지 않고, 추천 생성기가
            # 확정 재료·조건을 직접 처리하도록 둔다.
            return None

        try:
            request = build_tool_request(
                session_id=session_id,
                tool_name="recipe_recommendation",
                session=session,
            )
            result = await self._tool_provider.execute(request)
        except ToolRequestPreparationError:
            return {
                "status": "ERROR",
                "response": "재료 확인이 완료되지 않아 추천을 진행할 수 없습니다.",
            }
        if result.error:
            return {"status": "ERROR", "response": "추천 도구 실행 중 오류가 발생했습니다."}
        tool_result_payload = json.dumps(
            {
                "result": result.result,
                "source_metadata": result.source_metadata,
            },
            ensure_ascii=False,
            default=str,
        )
        if not await self._guardrail_validator.validate_tool_result(tool_result_payload):
            return {"status": "ERROR", "response": "추천 도구 결과를 검증할 수 없습니다."}
        return None

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
    ingredient_candidates: tuple[IngredientCandidate, ...] | None = None,
    confirmed_ingredients: tuple[IngredientCandidate, ...] | None = None,
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
        next_session = replace(next_session, ingredient_candidates=ingredient_candidates)
    if confirmed_ingredients is not None:
        next_session = replace(next_session, confirmed_ingredients=confirmed_ingredients)
    if next_step == "WAITING_IMAGE":
        next_session = replace(
            next_session,
            ingredient_candidates=tuple(),
            confirmed_ingredients=tuple(),
        )
    if user_conditions is not None:
        return replace(next_session, user_conditions=user_conditions)
    return next_session


def _conversation_message(
    role: Literal["user", "assistant"], content: object
) -> ConversationMessage:
    """응답을 세션 이력에 저장할 메시지 DTO로 정규화합니다."""

    return {"role": role, "content": str(content)}


def _apply_confirmation_decision(
    *,
    session: ChatSessionState,
    decision: IngredientConfirmationDecision,
    user_message: str,
) -> tuple[tuple[IngredientCandidate, ...], tuple[IngredientCandidate, ...]]:
    """분류된 의도에 따라 후보·확정 재료를 갱신합니다."""

    if decision.intent == "confirmed":
        # 후보를 확정 목록으로 이동해 사용자 확인 전 데이터가 Tool 입력에 남지 않게 한다.
        return tuple(), session.ingredient_candidates
    if decision.intent == "rejected":
        return tuple(), tuple()
    if decision.intent == "edited":
        return _apply_ingredient_edit(session.ingredient_candidates, user_message), tuple()
    return session.ingredient_candidates, tuple()


def _fallback_ingredient_confirmation(user_message: str) -> IngredientConfirmationDecision:
    """Jev를 사용할 수 없을 때 확인 의도를 결정하는 최소 fallback입니다."""

    if re.search(r"추가|더 넣|빼고|삭제|제외|교체|바꿔|수정|없고|없는|대신", user_message):
        return IngredientConfirmationDecision(intent="edited", confidence=1.0)
    if re.search(r"아니|틀렸|잘못|다시|전혀", user_message):
        return IngredientConfirmationDecision(intent="rejected", confidence=1.0)
    if re.search(r"맞아요|맞습니다|맞아|전부 맞|다 맞|네[, ]*(맞|그렇)|확인했", user_message):
        return IngredientConfirmationDecision(intent="confirmed", confidence=1.0)
    return IngredientConfirmationDecision(intent="unclear", confidence=1.0)


def _apply_ingredient_edit(
    candidates: tuple[IngredientCandidate, ...], user_message: str
) -> tuple[IngredientCandidate, ...]:
    """LLM 추출 실패 시에도 기존 후보의 삭제·수량 변경만 제한적으로 반영합니다."""

    updated = list(candidates)
    for candidate in tuple(updated):
        name = re.escape(candidate.name)
        if re.search(
            rf"(?:{name}).{{0,12}}(?:없|빼|삭제|제외)|(?:없|빼|삭제|제외).{{0,12}}(?:{name})",
            user_message,
        ):
            updated = [item for item in updated if item.name != candidate.name]
    return tuple(updated)


def _ingredient_payload(
    candidates: tuple[IngredientCandidate, ...],
) -> list[dict[str, str]]:
    """세션의 immutable 후보를 FE 응답용 JSON 목록으로 변환합니다."""

    return [{"name": candidate.name, "amount": candidate.amount} for candidate in candidates]


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


chat_service = ChatService()
