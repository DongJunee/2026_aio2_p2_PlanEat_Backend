"""환경변수 기반 애플리케이션 설정입니다."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env`와 시스템 환경변수에서 OpenAI 설정을 읽습니다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    """프로세스 동안 동일한 설정 인스턴스를 재사용합니다."""

    return Settings()
