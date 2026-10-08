"""환경변수 기반 애플리케이션 설정입니다."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env`와 시스템 환경변수에서 외부 AI 연동 설정을 읽습니다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "planeat-backend"
    langsmith_endpoint: str | None = None
    typesafe_jev_enabled: bool = False
    typesafe_api_key: str | None = None
    typesafe_model: str = "jev-latest"
    typesafe_timeout_seconds: float = 2.0
    typesafe_jev_min_confidence: float = 0.8
    nemo_guardrails_enabled: bool = True
    nemo_guardrails_config_path: str = "guardrails"
    tool_hub_enabled: bool = True
    tool_hub_catalog_path: str | None = None
    chroma_persist_directory: str | None = None
    chroma_collection_name: str = "recipe_guides"


@lru_cache
def get_settings() -> Settings:
    """프로세스 동안 동일한 설정 인스턴스를 재사용합니다."""

    return Settings()
