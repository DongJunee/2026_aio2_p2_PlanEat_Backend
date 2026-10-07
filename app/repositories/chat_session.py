"""Chat 워크플로우 상태의 저장소 추상화입니다.

현재 구현은 프로세스 메모리를 사용한다. 서비스는 이 인터페이스만 의존하므로,
다중 인스턴스 운영 시 Redis나 DB 구현체로 교체할 수 있다.
"""

import asyncio
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict


class ConversationMessage(TypedDict):
    """세션에 저장하는 사용자·어시스턴트 메시지입니다."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class IngredientCandidate:
    """이미지 인식 후 아직 사용자 확인을 거치지 않은 재료 후보입니다."""

    name: str
    amount: str


@dataclass(frozen=True)
class ChatSessionState:
    """Tool 요청 준비에 필요한 세션별 대화 상태입니다."""

    step: str = "WAITING_IMAGE"
    messages: tuple[ConversationMessage, ...] = field(default_factory=tuple)
    summary: str = ""
    ingredient_candidates: tuple[IngredientCandidate, ...] = field(default_factory=tuple)
    confirmed_ingredients: tuple[IngredientCandidate, ...] = field(default_factory=tuple)
    user_conditions: Mapping[str, object] | None = None


class ChatSessionRepository(Protocol):
    """세션 저장소 구현체가 제공해야 하는 비동기 접근 인터페이스입니다."""

    def get(self, session_id: str) -> Awaitable[ChatSessionState]:
        """현재 세션 상태를 조회합니다."""

    def save(self, session_id: str, state: ChatSessionState) -> Awaitable[None]:
        """세션 상태를 저장합니다."""


class InMemoryChatSessionRepository:
    """개발·테스트용 프로세스 메모리 세션 저장소입니다."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSessionState] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> ChatSessionState:
        """세션 상태가 없으면 이미지 입력 대기 상태를 반환합니다."""

        async with self._lock:
            return self._sessions.get(session_id, ChatSessionState())

    async def save(self, session_id: str, state: ChatSessionState) -> None:
        """세션의 최신 워크플로우 상태를 저장합니다."""

        async with self._lock:
            self._sessions[session_id] = state
