"""OpenAI Responses API를 사용한 최종 Chat 응답 생성기입니다."""

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from app.core.config import Settings
from app.core.safety import SafetyViolationError, validate_completion_output
from app.schemas.chat import IngredientConfirmation, RecommendationData

_PROMPT_DIRECTORY = Path(__file__).resolve().parents[3] / "prompts"
_COMPLETION_PROMPT_PARTS = (
    "shared/security.md",
    "shared/output-korean.md",
    "chat/completion.md",
)
_RECOMMENDATION_PROMPT_PARTS = (
    "shared/security.md",
    "shared/output-korean.md",
    "chat/recommendation.md",
)
_INGREDIENT_PROMPT_PARTS = (
    "shared/security.md",
    "chat/ingredient-extraction.md",
)
_CLARIFICATION_PROMPT_PARTS = (
    "shared/security.md",
    "shared/output-korean.md",
    "chat/clarification.md",
)

_INGREDIENT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["name", "amount"],
            },
        }
    },
    "required": ["ingredients"],
}

_CLARIFICATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "response": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["response", "questions"],
}

_RECOMMENDATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "response": {"type": "string"},
        "data": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "recipe_sets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "set_id": {"type": "string"},
                            "recipes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "recipe_id": {"type": "string"},
                                        "title": {"type": "string"},
                                        "image": {"type": ["string", "null"]},
                                        "cook_time": {"type": "integer"},
                                        "owned_ingredients": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "missing_ingredients": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "properties": {
                                                    "name": {"type": "string"},
                                                    "importance": {
                                                        "type": "string",
                                                        "enum": [
                                                            "필수",
                                                            "권장",
                                                            "대체 가능",
                                                            "생략 가능",
                                                        ],
                                                    },
                                                    "alternative": {
                                                        "type": ["string", "null"]
                                                    },
                                                },
                                                "required": [
                                                    "name",
                                                    "importance",
                                                    "alternative",
                                                ],
                                            },
                                        },
                                        "shopping_list": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "properties": {
                                                    "ingredient": {"type": "string"},
                                                    "amount": {"type": "string"},
                                                },
                                                "required": ["ingredient", "amount"],
                                            },
                                        },
                                        "nutrition": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "properties": {
                                                "calories": {"type": "number"},
                                                "protein": {"type": "number"},
                                                "carbohydrate": {"type": "number"},
                                                "fat": {"type": "number"},
                                            },
                                            "required": [
                                                "calories",
                                                "protein",
                                                "carbohydrate",
                                                "fat",
                                            ],
                                        },
                                    },
                                    "required": [
                                        "recipe_id",
                                        "title",
                                        "image",
                                        "cook_time",
                                        "owned_ingredients",
                                        "missing_ingredients",
                                        "shopping_list",
                                        "nutrition",
                                    ],
                                },
                            },
                        },
                        "required": ["set_id", "recipes"],
                    },
                }
            },
            "required": ["recipe_sets"],
        },
    },
    "required": ["response", "data"],
}


class GeneratedRecommendation(TypedDict):
    """OpenAI가 생성하고 Pydantic 검증을 통과한 완료 응답입니다."""

    response: str
    data: dict[str, object]


class GeneratedClarification(TypedDict):
    """OpenAI가 생성하고 서버 검증을 통과한 추가 입력 안내입니다."""

    response: str
    questions: list[str]


class LLMResponseError(Exception):
    """LLM 설정 또는 호출 결과가 Chat 응답으로 사용할 수 없을 때 발생합니다."""


@lru_cache
def load_prompt_part(relative_path: str) -> str:
    """지정된 프롬프트 조각을 한 번 읽어 재사용합니다."""

    try:
        return (_PROMPT_DIRECTORY / relative_path).read_text(encoding="utf-8")
    except OSError as error:
        raise LLMResponseError("프롬프트 조각을 읽을 수 없습니다.") from error


@lru_cache
def load_completion_instructions() -> str:
    """추천 완료 단계에 필요한 공통·단계별 프롬프트만 순서대로 조합합니다."""

    return "\n\n".join(load_prompt_part(path) for path in _COMPLETION_PROMPT_PARTS)


@lru_cache
def load_recommendation_instructions() -> str:
    """BE2 연결 전 임시 추천 생성에 필요한 프롬프트를 조합합니다."""

    return "\n\n".join(load_prompt_part(path) for path in _RECOMMENDATION_PROMPT_PARTS)


@lru_cache
def load_ingredient_instructions() -> str:
    """이미지·자연어 재료 추출에 필요한 프롬프트를 조합합니다."""

    return "\n\n".join(load_prompt_part(path) for path in _INGREDIENT_PROMPT_PARTS)


@lru_cache
def load_clarification_instructions() -> str:
    """현재 워크플로우에서 부족한 입력만 자연스럽게 질문하는 프롬프트를 조합합니다."""

    return "\n\n".join(load_prompt_part(path) for path in _CLARIFICATION_PROMPT_PARTS)


class OpenAIResponder:
    """`gpt-4o-mini`로 재료·추가 질문·임시 추천을 구조화해 생성합니다."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.openai_api_key
        self._model = settings.openai_model

    async def generate_clarification_response(
        self,
        response_kind: str,
        user_message: str,
        context: Mapping[str, object],
    ) -> GeneratedClarification:
        """현재 누락된 입력에 맞는 안내 문구와 질문을 구조화해 생성합니다.

        상태 코드와 LangGraph 전이는 서버가 결정하고, 사용자에게 표시되는 문구만
        모델에 맡긴다. 따라서 모델이 질문을 잘못 만들거나 호출에 실패해도 FE가
        분기하는 `status`·`step`은 영향을 받지 않는다.
        """

        if not self._api_key:
            raise LLMResponseError("OPENAI_API_KEY가 설정되지 않았습니다.")

        input_text = (
            "<untrusted_user_message>\n"
            f"{user_message}\n"
            "</untrusted_user_message>\n\n"
            "<workflow_context>\n"
            f"{json.dumps({'response_kind': response_kind, **dict(context)}, ensure_ascii=False)}\n"
            "</workflow_context>"
        )

        try:
            client = AsyncOpenAI(api_key=self._api_key)
            response = await client.responses.create(
                model=self._model,
                instructions=load_clarification_instructions(),
                input=input_text,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "planeat_clarification",
                        "schema": _CLARIFICATION_RESPONSE_SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=600,
                store=False,
            )
        except OpenAIError as error:
            raise LLMResponseError("OpenAI 추가 입력 안내 호출에 실패했습니다.") from error

        try:
            raw_output = getattr(response, "output_text", "")
            if not isinstance(raw_output, str) or not raw_output.strip():
                raise ValueError("빈 추가 입력 안내 응답")
            payload = json.loads(raw_output)
            if not isinstance(payload, dict):
                raise ValueError("추가 입력 안내 객체가 아닙니다.")
            message = payload.get("response")
            raw_questions = payload.get("questions")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("빈 추가 입력 안내 문구")
            if (
                not isinstance(raw_questions, list)
                or not raw_questions
                or any(not isinstance(question, str) or not question.strip() for question in raw_questions)
            ):
                raise ValueError("추가 질문 목록이 올바르지 않습니다.")
            questions = [question.strip() for question in raw_questions]
            message = message.strip()
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise LLMResponseError("OpenAI 추가 입력 안내 응답을 검증할 수 없습니다.") from error

        try:
            validate_completion_output(message)
            for question in questions:
                validate_completion_output(question)
        except SafetyViolationError as error:
            raise LLMResponseError("안전하지 않은 추가 입력 안내입니다.") from error

        return {"response": message, "questions": questions}

    async def extract_ingredients(
        self,
        user_message: str,
        attachments: Sequence[Mapping[str, str]],
        current_ingredients: Sequence[Mapping[str, str]] = (),
    ) -> list[dict[str, str]]:
        """이미지 또는 자연어에서 사용자가 제공한 재료만 구조화합니다.

        고정 재료 사전에 의존하지 않고 OpenAI 구조화 출력을 사용한다. 이미지가 없고
        API 키도 없는 초기 요청은 재료를 추정하지 않고 빈 목록을 반환해 기존 이미지
        안내 단계로 남긴다. 이미지가 첨부됐는데 추출기가 없으면 안전하게 오류 처리한다.
        """

        if not self._api_key:
            if attachments:
                raise LLMResponseError("이미지 재료 추출을 위한 OPENAI_API_KEY가 없습니다.")
            return []

        current_payload = json.dumps(list(current_ingredients), ensure_ascii=False)
        text_input = (
            "<untrusted_user_message>\n"
            f"{user_message}\n"
            "</untrusted_user_message>\n\n"
            "<current_ingredients>\n"
            f"{current_payload}\n"
            "</current_ingredients>"
        )
        if attachments:
            content: list[dict[str, object]] = [
                {"type": "input_text", "text": text_input}
            ]
            for attachment in attachments:
                image_data = attachment.get("data")
                if not isinstance(image_data, str) or not image_data:
                    raise LLMResponseError("이미지 입력 형식이 올바르지 않습니다.")
                content.append({"type": "input_image", "image_url": image_data})
            input_payload: str | list[dict[str, object]] = [
                {"role": "user", "content": content}
            ]
        else:
            input_payload = text_input

        try:
            client = AsyncOpenAI(api_key=self._api_key)
            response = await client.responses.create(
                model=self._model,
                instructions=load_ingredient_instructions(),
                input=input_payload,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "planeat_ingredients",
                        "schema": _INGREDIENT_RESPONSE_SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=1_000,
                store=False,
            )
        except OpenAIError as error:
            raise LLMResponseError("OpenAI 재료 추출 호출에 실패했습니다.") from error

        try:
            raw_output = getattr(response, "output_text", "")
            if not isinstance(raw_output, str) or not raw_output.strip():
                raise ValueError("빈 재료 추출 응답")
            payload = json.loads(raw_output)
            raw_ingredients = payload.get("ingredients") if isinstance(payload, dict) else None
            if not isinstance(raw_ingredients, list):
                raise ValueError("재료 목록이 없습니다.")
            validated: list[dict[str, str]] = []
            for raw_ingredient in raw_ingredients:
                ingredient = IngredientConfirmation.model_validate(raw_ingredient)
                validated.append(ingredient.model_dump())
            return validated
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as error:
            raise LLMResponseError("OpenAI 재료 추출 응답을 검증할 수 없습니다.") from error

    async def generate_recommendation(
        self,
        user_message: str,
        confirmed_ingredients: Sequence[Mapping[str, str]],
        user_conditions: Mapping[str, object] | None,
    ) -> GeneratedRecommendation:
        """확정 재료·조건으로 API 계약에 맞는 임시 추천을 구조화해 생성합니다.

        BE2 Recipe·Nutrition·Shopping·RAG 결과가 연결되기 전의 교체 지점이다. OpenAI의
        Structured Outputs로 JSON 형태를 제한한 뒤, 서버에서도 `RecommendationData`를
        다시 검증해 잘못된 모델 응답이 FE 계약으로 유입되지 않게 한다.
        """

        if not self._api_key:
            raise LLMResponseError("OPENAI_API_KEY가 설정되지 않았습니다.")

        input_text = (
            "<untrusted_user_message>\n"
            f"{user_message}\n"
            "</untrusted_user_message>\n\n"
            "<confirmed_ingredients>\n"
            f"{json.dumps(list(confirmed_ingredients), ensure_ascii=False)}\n"
            "</confirmed_ingredients>\n\n"
            "<untrusted_user_conditions>\n"
            f"{json.dumps(user_conditions or {}, ensure_ascii=False)}\n"
            "</untrusted_user_conditions>"
        )

        try:
            client = AsyncOpenAI(api_key=self._api_key)
            response = await client.responses.create(
                model=self._model,
                instructions=load_recommendation_instructions(),
                input=input_text,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "planeat_recommendation",
                        "schema": _RECOMMENDATION_RESPONSE_SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=6_000,
                store=False,
            )
        except OpenAIError as error:
            raise LLMResponseError("OpenAI API 호출에 실패했습니다.") from error

        try:
            raw_output = getattr(response, "output_text", "")
            if not isinstance(raw_output, str):
                raise TypeError("구조화 응답이 문자열이 아닙니다.")
            raw_payload = raw_output.strip()
            if not raw_payload:
                raise ValueError("빈 구조화 응답")
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict) or not isinstance(payload.get("response"), str):
                raise ValueError("완료 안내 문구가 없습니다.")
            recommendation = RecommendationData.model_validate(payload.get("data"))
            message = payload["response"].strip()
            if not message:
                raise ValueError("빈 완료 안내 문구")
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as error:
            raise LLMResponseError("OpenAI 구조화 응답을 검증할 수 없습니다.") from error

        try:
            validate_completion_output(message)
        except SafetyViolationError as error:
            raise LLMResponseError("안전하지 않은 모델 응답입니다.") from error

        return {
            "response": message,
            "data": recommendation.model_dump(),
        }

    async def generate_completion_message(
        self,
        user_message: str,
        recipe_sets: Sequence[dict[str, object]],
    ) -> str:
        """사용자 입력과 신뢰된 추천 데이터를 분리해 최종 안내 문구를 생성합니다."""

        if not self._api_key:
            raise LLMResponseError("OPENAI_API_KEY가 설정되지 않았습니다.")

        # 사용자 메시지는 지시문이 아닌 참고 정보로만 전달해 프롬프트 우선순위를 고정한다.
        input_text = (
            "<untrusted_user_message>\n"
            f"{user_message}\n"
            "</untrusted_user_message>\n\n"
            "<trusted_recipe_sets>\n"
            f"{json.dumps(recipe_sets, ensure_ascii=False)}\n"
            "</trusted_recipe_sets>"
        )

        try:
            client = AsyncOpenAI(api_key=self._api_key)
            response = await client.responses.create(
                model=self._model,
                instructions=load_completion_instructions(),
                input=input_text,
                max_output_tokens=120,
                store=False,
            )
        except OpenAIError as error:
            raise LLMResponseError("OpenAI API 호출에 실패했습니다.") from error

        message = response.output_text.strip()
        if not message:
            raise LLMResponseError("OpenAI API가 빈 응답을 반환했습니다.")
        try:
            validate_completion_output(message)
        except SafetyViolationError as error:
            raise LLMResponseError("안전하지 않은 모델 응답입니다.") from error
        return message
