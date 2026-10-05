"""NeMo Guardrails를 기존 Chat 안전성 경계에 연결합니다.

NeMo는 대화 단계 전이를 대체하지 않고, 사용자 입력·LLM 출력·Tool 결과에 대한
결정적 정규식 rail을 실행한다. 기존 ``app.core.safety`` 검사는 호환성과 다층 방어를
위해 유지하며, NeMo 설정 오류나 일시적인 검사 장애가 대화 API 전체를 중단시키지
않도록 기존 검사를 fallback으로 사용한다.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "guardrails"


class GuardrailValidator(Protocol):
    """ChatService가 사용하는 입력·출력·Tool 결과 검증 인터페이스입니다."""

    async def validate_input(self, text: str) -> bool:
        """사용자 입력이 정책을 통과하면 ``True``를 반환합니다."""

    async def validate_output(self, text: str) -> bool:
        """LLM 출력이 정책을 통과하면 ``True``를 반환합니다."""

    async def validate_tool_result(self, text: str) -> bool:
        """Tool 결과가 출력 안전성 정책을 통과하면 ``True``를 반환합니다."""


class NemoGuardrailService:
    """NeMo IORails의 입력·출력 검사를 비동기로 실행하는 어댑터입니다.

    NeMo 0.24의 ``IORails.check_async``는 응답을 생성하지 않고 rail만 검사하므로,
    현재 OpenAI Responses API와 LangGraph의 외부 계약을 건드리지 않는다. 같은
    ``ChatService``가 여러 이벤트 루프에서 호출되는 테스트·개발 환경에서는 엔진을
    루프별로 다시 만들어, 닫힌 이벤트 루프에 묶인 비동기 자원을 재사용하지 않는다.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.nemo_guardrails_enabled
        configured_path = Path(settings.nemo_guardrails_config_path)
        self._config_path = (
            configured_path
            if configured_path.is_absolute()
            else _PROJECT_ROOT / configured_path
        )
        self._rails = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._failure_logged = False

    async def validate_input(self, text: str) -> bool:
        """사용자 메시지에 NeMo 입력 rail을 적용합니다."""

        return await self._check(text, role="user")

    async def validate_output(self, text: str) -> bool:
        """최종 LLM 응답에 NeMo 출력 rail을 적용합니다."""

        return await self._check(text, role="assistant")

    async def validate_tool_result(self, text: str) -> bool:
        """BE2 결과를 assistant 출력과 같은 비신뢰 텍스트 경계에서 검사합니다.

        현재 BE1의 ToolProvider는 OpenAI tool-call 대화 이력이 아니라 별도 async
        계약을 사용하므로 NeMo의 구조적 ``tool result validation`` rail을 직접
        호출할 수 없다. 결과를 최종 출력 rail에 통과시켜 내부 지시·비밀값 패턴이
        FE 응답 경계로 넘어가지 않게 하고, BE2가 Chat Completions tool loop를
        제공하면 그때 구조적 rail로 교체한다.
        """

        return await self.validate_output(text)

    async def _check(self, text: str, *, role: str) -> bool:
        """지정한 role에 맞는 NeMo rail을 실행하고 장애 시 fallback합니다."""

        if not self._enabled or not text:
            return True

        try:
            rails = await self._get_rails()
            from nemoguardrails.rails.llm.options import RailStatus, RailType

            rail_type = RailType.INPUT if role == "user" else RailType.OUTPUT
            result = await rails.check_async(
                [{"role": role, "content": text}],
                rail_types=[rail_type],
            )
            return result.status is not RailStatus.BLOCKED
        except Exception as error:  # pragma: no cover - provider/config dependent
            # 기존 정규화 검사도 함께 실행되므로 NeMo 장애를 500으로 확대하지 않는다.
            if not self._failure_logged:
                logger.warning(
                    "NeMo Guardrails check failed; deterministic safety checks remain active: %s",
                    type(error).__name__,
                )
                self._failure_logged = True
            return True

    async def _get_rails(self):
        """현재 이벤트 루프에 사용할 NeMo Guardrails 엔진을 지연 초기화합니다."""

        from nemoguardrails import Guardrails, RailsConfig

        current_loop = asyncio.get_running_loop()
        if self._rails is not None and self._event_loop is not current_loop:
            # 이전 루프의 자원을 정리하되, 정리 실패가 새 검사까지 막지 않게 한다.
            try:
                await self._rails.shutdown()
            except Exception:  # pragma: no cover - loop lifecycle dependent
                pass
            self._rails = None

        if self._rails is None:
            config = RailsConfig.from_path(str(self._config_path))
            self._rails = Guardrails(
                config,
                use_iorails=True,
                require_iorails=True,
            )
            self._event_loop = current_loop
        return self._rails
