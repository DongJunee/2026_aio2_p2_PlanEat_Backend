"""TypeSafe Jev를 조건 입력 판정에 연결하는 어댑터입니다.

Jev는 사용자에게 보여 줄 문구를 생성하지 않습니다. 고정된 선택지와 confidence만
LangGraph에 전달해, 외부 모델의 불확실한 결과가 API 계약을 바꾸지 않게 합니다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import Settings

_SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"
_CONDITION_READY_KEY = "condition_readiness"
_INGREDIENT_CONFIRMATION_KEY = "ingredient_confirmation"
IngredientConfirmationIntent = Literal["confirmed", "rejected", "edited", "unclear"]


@dataclass(frozen=True)
class ConditionReadinessDecision:
    """식단·시간 조건이 추천에 충분한지에 대한 신뢰도 있는 결정입니다."""

    is_ready: bool
    confidence: float


@dataclass(frozen=True)
class IngredientConfirmationDecision:
    """재료 후보에 대한 사용자의 자연어 확인 의도와 신뢰도입니다."""

    intent: IngredientConfirmationIntent
    confidence: float


class JevConditionReadinessEvaluator:
    """Jev Choice 질문으로 추천 조건의 충족 여부를 판단합니다.

    키가 없거나 응답 형식·confidence가 기대와 다르면 ``None``을 반환합니다.
    호출자는 이를 기존 결정적 전이로 fallback해야 합니다.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.typesafe_jev_enabled
        self._api_key = settings.typesafe_api_key
        self._model = settings.typesafe_model
        self._timeout_seconds = settings.typesafe_timeout_seconds
        self._min_confidence = settings.typesafe_jev_min_confidence

    async def evaluate(self, user_message: str) -> ConditionReadinessDecision | None:
        """현재 사용자 메시지에서 식단 목표와 조리 시간의 충족 여부를 반환합니다."""

        if not self._enabled or not self._api_key:
            return None

        payload = {
            "state": user_message,
            "model": self._model,
            "questions": {
                _CONDITION_READY_KEY: {
                    "type": "choice",
                    "instructions": (
                        "사용자가 식단 목표와 조리 가능한 시간을 모두 제공했는지 판단한다. "
                        "둘 중 하나라도 구체적으로 알 수 없으면 needs_more_info를 선택한다."
                    ),
                    "criteria": {
                        "ready": "식단 목표와 조리 가능 시간이 모두 명시되어 있다.",
                        "needs_more_info": "식단 목표 또는 조리 가능 시간이 빠졌거나 모호하다.",
                    },
                }
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    _SYSTEM_ONE_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                response_payload = response.json()
        except (httpx.HTTPError, ValueError):
            # 외부 모델의 장애·JSON 형식 오류가 대화 흐름 전체를 실패시키면 안 된다.
            return None

        return self.parse_condition_readiness(
            response_payload, min_confidence=self._min_confidence
        )

    @staticmethod
    def parse_condition_readiness(
        payload: Mapping[str, Any], *, min_confidence: float
    ) -> ConditionReadinessDecision | None:
        """신뢰도 기준을 통과한 Jev Choice 응답만 내부 결정으로 변환합니다."""

        try:
            answers = payload["answers"]
            answer = answers[_CONDITION_READY_KEY]
            choice = answer["choice"]
            confidence = float(answer["confidence"])
        except (KeyError, TypeError, ValueError):
            return None

        if not 0.0 <= confidence <= 1.0 or confidence < min_confidence:
            return None
        if choice == "ready":
            return ConditionReadinessDecision(is_ready=True, confidence=confidence)
        if choice == "needs_more_info":
            return ConditionReadinessDecision(is_ready=False, confidence=confidence)
        return None


class JevIngredientConfirmationEvaluator:
    """Jev Choice 질문으로 재료 후보 확인 의도를 분류합니다.

    Jev는 후보 재료를 수정하지 않고 의도만 분류한다. ``edited``인 경우 후보의 실제
    변경은 서비스 계층의 제한적인 자연어 parser가 담당하며, 합의된 Tool Hub 추출 DTO가
    생기면 해당 parser를 교체한다.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.typesafe_jev_enabled
        self._api_key = settings.typesafe_api_key
        self._model = settings.typesafe_model
        self._timeout_seconds = settings.typesafe_timeout_seconds
        self._min_confidence = settings.typesafe_jev_min_confidence

    async def evaluate(self, user_message: str) -> IngredientConfirmationDecision | None:
        """재료 확인·거절·수정·모호함 중 하나를 신뢰도와 함께 반환합니다."""

        if not self._enabled or not self._api_key:
            return None

        payload = {
            "state": user_message,
            "model": self._model,
            "questions": {
                _INGREDIENT_CONFIRMATION_KEY: {
                    "type": "choice",
                    "instructions": (
                        "사용자가 앞서 제시된 재료 후보를 어떻게 처리할지 분류한다. "
                        "모두 맞다는 긍정, 틀렸다는 거절, 특정 재료의 추가·삭제·수정은 "
                        "각각 confirmed, rejected, edited로 분류하고 판단이 어려우면 unclear를 선택한다."
                    ),
                    "criteria": {
                        "confirmed": "제시된 재료가 모두 맞다고 확인한다.",
                        "rejected": "제시된 재료가 틀렸거나 다시 인식해야 한다고 말한다.",
                        "edited": "재료를 추가·삭제·교체하거나 수량을 수정한다.",
                        "unclear": "확인 의도를 명확히 판단할 수 없다.",
                    },
                }
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    _SYSTEM_ONE_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                response_payload = response.json()
        except (httpx.HTTPError, ValueError):
            # Jev 장애나 JSON 형식 오류가 재료 확인 전체를 중단시키지 않게 한다.
            return None

        return self.parse_ingredient_confirmation(
            response_payload, min_confidence=self._min_confidence
        )

    @staticmethod
    def parse_ingredient_confirmation(
        payload: Mapping[str, Any], *, min_confidence: float
    ) -> IngredientConfirmationDecision | None:
        """허용된 choice와 confidence를 검증해 내부 의도로 변환합니다."""

        try:
            answers = payload["answers"]
            answer = answers[_INGREDIENT_CONFIRMATION_KEY]
            intent = answer["choice"]
            confidence = float(answer["confidence"])
        except (KeyError, TypeError, ValueError):
            return None

        allowed_intents = {"confirmed", "rejected", "edited", "unclear"}
        if (
            intent not in allowed_intents
            or not 0.0 <= confidence <= 1.0
            or confidence < min_confidence
        ):
            return None
        return IngredientConfirmationDecision(
            intent=intent,  # type: ignore[arg-type]
            confidence=confidence,
        )
