# LangSmith 연동 가이드

PlanEat Backend는 선택적으로 LangGraph 실행을 LangSmith에 전송한다. LangSmith는
관측성 도구이며, tracing 장애가 `/chat` 응답을 실패시키지 않도록 구성되어 있다.

## 1. 설정

`.env`에 다음 값을 설정한다.

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-langsmith-api-key>
LANGSMITH_PROJECT=planeat-backend
```

LangSmith self-hosted endpoint를 사용하는 경우에만 다음 값을 추가한다.

```env
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

API 키는 `.env`와 배포 환경의 secret store에만 보관하며, 코드·fixture·문서에 실제 값을
기록하지 않는다.

## 2. 현재 trace 범위

`ChatService`가 요청별 LangSmith callback을 `build_chat_graph(...).ainvoke()`에 연결한다.
따라서 `planeat.chat` root run 아래에 요약·단계 전이와 완료 단계
`tool_hub_recipe_recommendation` child run이 함께 표시된다. 다음 metadata를 기록한다.

- 해시된 `session_id`
- 요청 시작 시점의 내부 workflow step
- 이미지 첨부 여부와 첨부 개수
- 조건 입력 여부
- 확정 재료 보유 여부
- Tool Hub node 활성화 여부와 논리적 tool 이름

raw OpenAI·TypeSafe HTTP 호출 자체는 별도 run으로 분리하지 않지만, Tool Hub node의 실행·
성공/실패 전이는 LangGraph child run으로 확인할 수 있다. 입력·출력은 숨김 설정을 유지하고
비식별 metadata만 전송한다.

## 3. 개인정보 보호

LangSmith client에 `LANGSMITH_HIDE_INPUTS=true`, `LANGSMITH_HIDE_OUTPUTS=true`를
적용하므로 LangGraph state의 사용자 메시지·대화 요약·이미지 원문이 trace payload에
기록되지 않는다. 사용자 식별을 위해 원문 대신 짧은 SHA-256 해시만 metadata에 넣는다.

tracer 생성에 필요한 환경변수는 생성 직후 복원하며, LangSmith 초기화 또는 전송 오류는
로그만 남기고 API 응답 흐름을 중단하지 않는다.

## 4. 로컬 확인

```bash
uv run uvicorn app.main:app --reload --port 8000
```

같은 `session_id`로 `/chat`을 호출한 뒤 LangSmith의 `planeat-backend` 프로젝트에서
`planeat.chat` run과 `tool_hub_recipe_recommendation` child run을 확인한다. `LANGSMITH_TRACING=false`이거나
API 키가 없으면 LangSmith 전송 없이도 Tool Hub 동작은 유지된다.
