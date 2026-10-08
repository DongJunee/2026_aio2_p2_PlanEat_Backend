"""냉장고 이미지에서 사용자 확인 전 재료 후보만 추출하는 BE2 Vision provider입니다."""

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.tools.be2_models import ToolIngredient
from app.agent.tools.ingredient_validation_tool import IngredientValidationTool

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "vision" / "ingredient-extraction.md"

_VISION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "amount": {"type": "string"}},
                "required": ["name", "amount"],
            },
        }
    },
    "required": ["ingredients"],
}


class VisionToolError(RuntimeError):
    """이미지 검증·Vision provider 호출·구조화 출력 오류입니다."""


class DetectedIngredient(BaseModel):
    """사용자 확인이 필요한 이미지 인식 재료 후보입니다."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    amount: str = Field(min_length=1)


class VisionIngredientResponse(BaseModel):
    """Vision 모델의 엄격한 구조화 결과입니다."""

    model_config = ConfigDict(extra="forbid")

    ingredients: list[DetectedIngredient]


class OpenAIVisionIngredientExtractor:
    """OpenAI Responses API로 이미지의 재료 후보를 추출합니다.

    이 클래스는 후보만 반환한다. 후보를 확정 재료나 Recipe/RAG 입력으로 사용하는
    정책은 BE1의 재료 확인 단계가 책임진다.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        allowed_image_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if not api_key.strip():
            raise ValueError("Vision provider용 OpenAI API 키가 필요합니다.")
        self._api_key = api_key
        self._model = model
        self._allowed_image_hosts = frozenset(host.lower() for host in allowed_image_hosts)
        self._ingredient_validator = IngredientValidationTool()

    async def extract(
        self,
        attachments: Sequence[Mapping[str, str]],
        *,
        user_message: str = "",
    ) -> list[DetectedIngredient]:
        """이미지 Data URL 또는 명시적 allowlist의 HTTPS URL만 Vision API에 전달합니다."""

        if not attachments or len(attachments) > 5:
            raise VisionToolError("이미지는 1장 이상 5장 이하로 전달해야 합니다.")

        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": "<untrusted_user_message>\n"
                f"{user_message}\n"
                "</untrusted_user_message>",
            }
        ]
        for attachment in attachments:
            image_data = _validated_image_data(attachment, self._allowed_image_hosts)
            content.append({"type": "input_image", "image_url": image_data})

        try:
            client = AsyncOpenAI(api_key=self._api_key)
            response = await client.responses.create(
                model=self._model,
                instructions=load_vision_instructions(),
                input=[{"role": "user", "content": content}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "planeat_vision_ingredients",
                        "schema": _VISION_RESPONSE_SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=700,
                store=False,
            )
        except OpenAIError as error:
            raise VisionToolError("Vision provider 호출에 실패했습니다.") from error

        raw_output = getattr(response, "output_text", "")
        try:
            parsed = json.loads(raw_output)
            candidates = VisionIngredientResponse.model_validate(parsed).ingredients
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            raise VisionToolError("Vision provider 응답 형식이 올바르지 않습니다.") from error
        normalized = self._ingredient_validator.normalize(
            ToolIngredient(name=candidate.name, amount=candidate.amount)
            for candidate in candidates
        )
        return [DetectedIngredient.model_validate(item.model_dump()) for item in normalized]


@lru_cache
def load_vision_instructions() -> str:
    """Vision 프롬프트를 최상위 prompts 디렉터리에서만 읽습니다."""

    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise VisionToolError("Vision 프롬프트를 읽을 수 없습니다.") from error


def _validated_image_data(attachment: Mapping[str, str], allowed_hosts: frozenset[str]) -> str:
    if attachment.get("type") != "image":
        raise VisionToolError("image 타입 첨부만 지원합니다.")
    image_data = attachment.get("data")
    if not isinstance(image_data, str) or not image_data.strip():
        raise VisionToolError("이미지 데이터가 필요합니다.")
    image_data = image_data.strip()
    if image_data.startswith("data:image/"):
        return image_data

    parsed = urlparse(image_data)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host and host in allowed_hosts:
        return image_data
    # 외부 URL은 Vision provider가 요청하는 대상이 된다. allowlist 없이 그대로 전달하면
    # SSRF·접근 제어 위험이 있으므로 Data URL만 기본 허용한다.
    raise VisionToolError("허용되지 않은 이미지 URL입니다.")
