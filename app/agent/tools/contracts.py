"""Orchestrator가 Tool Hub에 전달할 내부 계약과 호출 전 검증을 정의합니다.

Tool Hub URL과 하위 DTO는 아직 팀 합의 전이다. 따라서 이 모듈은 네트워크 호출을 하지 않고,
상위 필드와 사용자 확인 제약만 고정한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.repositories.chat_session import ChatSessionState


class ToolRequestPreparationError(ValueError):
    """사용자 확인이 끝나지 않아 Tool 요청을 만들 수 없을 때 발생합니다."""


@dataclass(frozen=True)
class ToolRequest:
    """Orchestrator에서 Tool Hub로 전달할 정규화 전 Tool 요청입니다.

    ``user_conditions``에는 Orchestrator가 여러 턴에 걸쳐 누적한 조건 원문과 슬롯을 담는다.
    식단 목적은 필수이며, ``cooking_time_minutes``는 존재할 때만 적용하는 선택값이다.
    상세 추출 DTO는 Tool Hub와 합의 후 구체화한다. 현재는 임의의 후보 재료가 전달되는 것을
    방지하는 역할을 한다.
    """

    session_id: str
    tool_name: str
    confirmed_ingredients: tuple[Mapping[str, Any], ...]
    user_conditions: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """Tool Hub 결과를 Orchestrator 워크플로우가 안전하게 처리할 수 있는 형태로 표현합니다."""

    result: Mapping[str, Any] | None = None
    source_metadata: Mapping[str, Any] | None = None
    error: str | None = None


def build_tool_request(
    *, session_id: str, tool_name: str, session: ChatSessionState
) -> ToolRequest:
    """확정 재료와 사용자 조건이 모두 있을 때만 Tool 요청을 구성합니다."""

    if not session.confirmed_ingredients:
        raise ToolRequestPreparationError("사용자가 확인한 재료가 없습니다.")
    if not session.user_conditions:
        raise ToolRequestPreparationError("사용자 조건이 없습니다.")

    return ToolRequest(
        session_id=session_id,
        tool_name=tool_name,
        confirmed_ingredients=tuple(
            {"name": ingredient.name, "amount": ingredient.amount}
            for ingredient in session.confirmed_ingredients
        ),
        user_conditions=session.user_conditions,
    )
