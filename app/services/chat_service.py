"""Chat API의 세션 관리와 LangGraph 실행을 연결합니다."""

from dataclasses import replace
from collections.abc import Mapping, Sequence
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
)
from app.schemas.chat import ChatRequest, RecommendationData, RecipeSet
from app.services.pdf_service import RecipePdfService, recipe_pdf_service


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


class FeedbackPlanner(Protocol):
    """완료된 추천에 대한 사용자 피드백의 재추천 계획 경계입니다."""

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
            return {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}, 400
        if not await self._guardrail_validator.validate_input(request.message):
            return {"status": "ERROR", "response": "요청을 처리할 수 없습니다."}, 400

        session = await self._session_repository.get(request.session_id)
        previous_step = session.step
        stored_recommendation = _stored_recommendation(session)
        if previous_step == "COMPLETED" and stored_recommendation is not None:
            selected_set_id = _parse_selected_set_id(request.message, stored_recommendation)
            if selected_set_id is not None:
                return await self._handle_set_selection(
                    request=request,
                    session=session,
                    recommendation=stored_recommendation,
                    selected_set_id=selected_set_id,
                )

            # 완료 결과 이후에는 새 메시지를 단순한 재추천 요청으로 취급하지 않고
            # Jev Plan을 거쳐 조건·피드백을 누적한 뒤 같은 Tool 검증 경로를 다시 탄다.
            feedback_plan = await self._feedback_planner.plan(
                request.message,
                session.user_conditions,
            )
            request_conditions = _merge_feedback_conditions(
                session.user_conditions,
                request.message,
                feedback_plan,
            )
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
                    "status": "ERROR",
                    "response": "이미지에서 재료를 확인할 수 없습니다.",
                }, 500
            candidate_ingredients = extracted_ingredients
        messages = [*session.messages, {"role": "user", "content": request.message}]
        state: ChatState = {
            "step": previous_step,
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

        response_message = f"{selected_set_id} 식단의 상세 PDF를 생성했습니다."
        if not await self._guardrail_validator.validate_output(response_message):
            return {"status": "ERROR", "response": "PDF 응답을 안전하게 표시할 수 없습니다."}, 500
        response = {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": response_message,
            "data": recommendation.model_dump(),
            "next_action": "PDF_READY",
            "available_set_ids": [recipe_set.set_id for recipe_set in recommendation.recipe_sets],
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
                fallback_questions=["식단 목표가 무엇인가요? 예: 다이어트, 고단백, 채식"],
            )
            return {
                "status": "NEED_MORE_INFO",
                "step": "CONDITION_INPUT",
                "response": message,
                "questions": questions,
            }
        if tool_result is not None:
            return await self._response_from_tool_hub(tool_result)

        generated_recommendation = await self._generate_recommendation(
            user_message=user_message,
            session=session,
        )
        _generated_message, recipe_sets = generated_recommendation
        completion_message = _completion_selection_message()
        if not await self._guardrail_validator.validate_output(completion_message):
            raise LLMResponseError("NeMo Guardrails가 최종 응답을 차단했습니다.")
        return {
            "status": "SUCCESS",
            "step": "COMPLETED",
            "response": completion_message,
            "data": {"recipe_sets": recipe_sets},
            "next_action": "FEEDBACK_OR_SET_SELECTION",
            "available_set_ids": [recipe_set["set_id"] for recipe_set in recipe_sets],
        }

    async def _response_from_tool_hub(self, raw_result: object) -> dict[str, object]:
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
        completion_message = _completion_selection_message()
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
                recipe_set.set_id for recipe_set in recommendation.recipe_sets
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
        self, user_message: str
    ) -> IngredientConfirmationDecision:
        """Jev 판정을 우선하고, 장애·비활성 시 제한적인 로컬 판정으로 fallback합니다."""

        decision = await self._ingredient_confirmation_evaluator.evaluate(user_message)
        if decision is not None:
            return decision
        return _fallback_ingredient_confirmation(user_message)

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
    """추천 DTO에서 세트 ID에 해당하는 세트를 찾습니다."""

    return next(
        (recipe_set for recipe_set in recommendation.recipe_sets if recipe_set.set_id == set_id),
        None,
    )


def _parse_selected_set_id(
    user_message: str, recommendation: RecommendationData
) -> str | None:
    """자연어 세트 선택을 검증된 추천 세트 ID로 변환합니다."""

    available = [recipe_set.set_id for recipe_set in recommendation.recipe_sets]
    normalized_message = re.sub(r"[\s_-]+", "", user_message).upper()
    has_selection_intent = bool(
        re.search(r"선택|고르|골라|정해|pdf|피디에프|파일", user_message, flags=re.IGNORECASE)
    )
    for set_id in available:
        normalized_id = re.sub(r"[\s_-]+", "", set_id).upper()
        if normalized_id in normalized_message and (has_selection_intent or normalized_message == normalized_id):
            return set_id

    number_match = re.search(r"(?<!\d)([1-9])\s*(?:번|번째)?\s*세트", user_message)
    if number_match and has_selection_intent:
        index = int(number_match.group(1)) - 1
        if 0 <= index < len(available):
            return available[index]
    if has_selection_intent and re.search(r"첫\s*(?:번째|번)?\s*세트", user_message):
        return available[0] if available else None
    if has_selection_intent and re.search(r"둘째|두\s*(?:번째|번)?\s*세트", user_message):
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


def _completion_selection_message() -> str:
    """완료 응답에서 두 추천 세트 중 하나를 선택하도록 안내합니다."""

    return (
        "확정한 재료와 조건에 맞는 메인·반찬 식단을 추천했습니다. "
        "두 세트 중 하나를 선택해 주세요. 선택한 레시피의 상세 PDF를 생성해드릴게요."
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


def _build_default_tool_provider() -> ToolRequestExecutor | None:
    """기본 앱에 연결할 Tool Hub provider를 설정에 따라 조립합니다."""

    settings = get_settings()
    if not settings.tool_hub_enabled:
        return None
    return build_default_tool_hub(settings)


chat_service = ChatService(tool_provider=_build_default_tool_provider())
