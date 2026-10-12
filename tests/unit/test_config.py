"""기본 설정이 가벼운 CSV 추천 경로를 선택하는지 검증합니다."""

from app.core.config import Settings


def test_settings_disable_chroma_by_default() -> None:
    """명시적 환경변수가 없으면 ONNX 기반 Chroma 검색을 시작하지 않습니다."""

    assert Settings(_env_file=None).chroma_persist_directory is None
