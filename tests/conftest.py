"""기본 테스트가 외부 observability 서비스로 trace를 전송하지 않게 합니다."""

import os


# 개발자의 로컬 `.env`에 LangSmith가 활성화되어 있어도 pytest는 외부 네트워크를
# 사용하지 않아야 한다. 구조화된 tracing 자체는 test_observability.py에서 fake로 검증한다.
os.environ["LANGSMITH_TRACING"] = "false"
