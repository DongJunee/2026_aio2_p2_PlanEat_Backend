# NeMo Guardrails 연동

PlanEat은 LangGraph의 단계 전이를 NeMo Guardrails로 대체하지 않는다. NeMo 0.24의
`IORails.check_async()`를 안전성 경계에 연결해 사용자 입력, 최종 LLM 출력, BE2 Tool
결과가 FE로 넘어가기 전 결정적 정규식 rail을 실행한다.

## 적용 범위

```text
POST /chat
  → app/core/safety.py 정규화 검사
  → NeMo input rail (regex check input)
  → 기존 LangGraph·세션·Tool 흐름
  → BE2 ToolResult를 NeMo output rail로 검사
  → OpenAI 완료 문구 + NeMo output rail (regex check output)
  → ChatResponse
```

현재 활성화한 패턴은 다음과 같다.

- 입력: 이전 지시 무시, 시스템/개발자 프롬프트 공개, prompt injection, jailbreak 등
- 출력·Tool 결과: 시스템/개발자 프롬프트, API 키·OpenAI 키 등 내부 정보 노출

기존 [`app/core/safety.py`](../app/core/safety.py)의 NFKC·구분자 제거 검사는 그대로
유지한다. 두 계층 중 하나라도 입력을 차단하면 외부 계약에 맞춰 `400 ERROR`를 반환하고,
최종 출력·Tool 결과가 차단되면 원문을 노출하지 않고 `500 ERROR`를 반환한다.

## 설정

```bash
NEMO_GUARDRAILS_ENABLED=true
NEMO_GUARDRAILS_CONFIG_PATH=guardrails
```

기본값은 활성화다. `NEMO_GUARDRAILS_CONFIG_PATH`는 `guardrails/config.yml` 디렉터리
경로이며, 운영 환경에서는 읽기 전용으로 배포한다. 패턴 변경은 이 파일에서만 하고,
사용자 메시지·이미지 원문·Tool 결과를 설정이나 로그에 저장하지 않는다.

NeMo 검사에 장애가 생기면 이 어댑터는 검사 실패를 로그에 남기되(오류 유형만 기록),
기존 결정적 안전성 검사로 fallback한다. 따라서 외부 `/chat` 상태 코드 계약은 바뀌지
않는다. 장애를 재현하거나 긴급 우회할 때만 `NEMO_GUARDRAILS_ENABLED=false`를 사용한다.

## BE2 Tool Hub 확장 지점

현재 BE1의 `ToolProvider`는 별도 `ToolRequest`/`ToolResult` 프로토콜이므로, NeMo의
구조적 `tool result validation` rail에 필요한 OpenAI Chat Completions tool-call 이력은
없다. 따라서 현재는 ToolResult를 출력 rail에 통과시킨다. BE2가 tool-call 대화 루프를
제공하면 `NemoGuardrailService.validate_tool_result()`를 NeMo의 `tool_input` rail로
교체하고, 도구 이름·인자 스키마·`tool_call_id` 연결을 함께 검증한다.

RAG가 연결되면 검색 문서는 LLM 지시가 아닌 비신뢰 데이터로 유지하고, 문서 필터링은
별도 retrieval rail PoC에서 오탐률·지연시간을 측정한 뒤 추가한다. 현재 설정은
`IORails`에서 지원되는 입력·출력 정규식 rail만 사용하므로 추가 모델 호출이나 NVIDIA
API 키가 필요하지 않다.

## 테스트·검증

```bash
PYTHONPATH=. uv run pytest
git diff --check
```

`tests/unit/test_guardrails.py`는 separator 우회 입력, 내부 정보·API 키 출력 차단,
비활성화 fallback을 검증한다. `ChatService`는 별도 `GuardrailValidator`를 주입할 수
있어 정책 차단·장애 경로를 네트워크 없이 테스트한다.
