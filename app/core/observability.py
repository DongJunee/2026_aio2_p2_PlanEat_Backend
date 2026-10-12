"""LangGraph 실행을 LangSmith에 안전하게 전송하기 위한 공통 설정입니다."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tracers import LangChainTracer

from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_langsmith_run_config(
    settings: Settings,
    *,
    session_id: str,
    workflow_step: str,
    has_image: bool,
    has_conditions: bool,
    has_confirmed_ingredients: bool,
    attachment_count: int,
    tool_enabled: bool = False,
) -> RunnableConfig | None:
    """LangGraph 요청에 사용할 LangSmith callback 설정을 만듭니다.

    LangSmith는 관측성 도구이므로 trace 전송 실패가 `/chat` 결과를 실패시키면 안 된다.
    사용자 메시지·이미지 원문은 민감정보가 될 수 있어 payload 입력/출력을 숨기고,
    상태 전이 분석에 필요한 비식별 metadata만 전송한다.
    """

    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return None

    # LangChain의 기본 Client가 읽는 환경변수는 Settings의 `.env` 값만으로는
    # 자동 주입되지 않으므로, tracer를 만드는 짧은 구간에만 프로세스 환경에 반영한다.
    # 복원하지 않으면 이후 요청에서 tracing이 꺼져도 전역 callback이 남을 수 있다.
    environment = {
        "LANGSMITH_API_KEY": settings.langsmith_api_key,
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_HIDE_INPUTS": "true",
        "LANGSMITH_HIDE_OUTPUTS": "true",
        "LANGSMITH_PROJECT": settings.langsmith_project,
    }
    if settings.langsmith_endpoint:
        environment["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    previous_environment = {
        name: os.environ.get(name) for name in environment
    }
    try:
        os.environ.update(environment)
        tracer = LangChainTracer(
            project_name=settings.langsmith_project,
            tags=["planeat", "chat", "langgraph"],
        )
    except Exception:  # pragma: no cover - SDK 초기화 실패는 실행을 막지 않아야 한다.
        logger.warning("LangSmith tracer 초기화에 실패해 이번 요청의 tracing을 건너뜁니다.")
        return None
    finally:
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    metadata: dict[str, Any] = {
        "session_id_hash": _hash_identifier(session_id),
        "workflow_step": workflow_step,
        "has_image": has_image,
        "has_conditions": has_conditions,
        "has_confirmed_ingredients": has_confirmed_ingredients,
        "attachment_count": attachment_count,
    }
    if tool_enabled:
        # 사용자 입력·재료 원문 대신 Tool Hub 경로가 실행됐다는 사실만 남긴다.
        metadata["tool_enabled"] = True
        metadata["tool_name"] = "recipe_recommendation"
    return {
        "callbacks": [tracer],
        "run_name": "planeat.chat",
        "metadata": metadata,
    }


def _hash_identifier(value: str) -> str:
    """외부 관측성 시스템에 세션 원문을 보내지 않고 안정적인 식별자를 만듭니다."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
