"""Chat API의 프롬프트 인젝션 다층 방어 유틸리티입니다."""

import re
import unicodedata


class SafetyViolationError(Exception):
    """비신뢰 입력 또는 모델 출력이 보안 규칙을 위반했을 때 발생합니다."""


_SEPARATORS = re.compile(r"[\s_\-:：.`'\"/\\|]+")
_INPUT_INJECTION_MARKERS = frozenset(
    {
        "ignorepreviousinstructions",
        "ignoreallpreviousinstructions",
        "disregardpreviousinstructions",
        "forgetpreviousinstructions",
        "systemprompt",
        "developerprompt",
        "revealprompt",
        "promptinjection",
        "jailbreak",
        "danmode",
        "이전지시무시",
        "이전지시를무시",
        "이전지시사항을무시",
        "앞선지시를무시",
        "모든지시무시",
        "모든지시를무시",
        "시스템프롬프트",
        "개발자프롬프트",
        "프롬프트공개",
        "프롬프트인젝션",
        "탈옥",
        "역할변경",
    }
)
_OUTPUT_DISCLOSURE_MARKERS = frozenset(
    {
        "systemprompt",
        "developerprompt",
        "apikey",
        "openaiapikey",
        "시스템프롬프트",
        "개발자프롬프트",
        "api키",
        "오픈에이아이api키",
    }
)
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")


def normalize_text(text: str) -> str:
    """NFKC·소문자·구분 문자 제거로 우회 표현을 비교 가능한 형태로 바꿉니다."""

    normalized = unicodedata.normalize("NFKC", text).lower()
    return _SEPARATORS.sub("", normalized)


def validate_user_message(message: str) -> None:
    """인젝션으로 자주 쓰이는 지시를 정규화한 뒤 사전에 차단합니다."""

    normalized = normalize_text(message)
    if any(marker in normalized for marker in _INPUT_INJECTION_MARKERS):
        raise SafetyViolationError("안전하지 않은 사용자 입력입니다.")


def validate_completion_output(message: str) -> None:
    """최종 응답의 내부 지시·비밀정보 노출을 마지막으로 검사합니다."""

    normalized = normalize_text(message)
    if _API_KEY_PATTERN.search(message) or any(
        marker in normalized for marker in _OUTPUT_DISCLOSURE_MARKERS
    ):
        raise SafetyViolationError("안전하지 않은 모델 출력입니다.")
