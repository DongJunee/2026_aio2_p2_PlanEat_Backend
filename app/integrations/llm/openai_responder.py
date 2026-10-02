"""OpenAI Responses API를 사용한 최종 Chat 응답 생성기입니다."""

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI, OpenAIError

from app.core.config import Settings
from app.core.safety import SafetyViolationError, validate_completion_output

_PROMPT_DIRECTORY = Path(__file__).resolve().parents[3] / "prompts"
_COMPLETION_PROMPT_PARTS = (
    "shared/security.md",
    "shared/output-korean.md",
    "chat/completion.md",
)


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


class OpenAIResponder:
    """`gpt-4o-mini`로 추천 결과에 대한 사용자용 안내 문구를 생성합니다."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.openai_api_key
        self._model = settings.openai_model

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
