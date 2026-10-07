# PlanEat Backend 협업 지침

> 최종 수정: 2026-10-07 (v1.3)
>
> 이 문서를 수정하면 날짜와 버전을 함께 갱신한다.

이 문서는 PlanEat Backend를 작업하는 사람과 에이전트의 공통 작업 계약이다. 담당자가
달라도 같은 Chat API 계약, LangGraph 단계, 검증 기준을 유지하는 것이 목표다. 구현 전에는
관련 문서를 읽고, 구현 후에는 이 문서의 완료 조건을 확인한다.

## 1. 프로젝트 기준

- 런타임: Python 3.11, FastAPI, LangGraph, `uv`
- 의존성 기준 파일: `pyproject.toml`, `uv.lock`
- API 진입점: `POST /chat`, `GET /health`
- API 계약: `docs/api/chat.md`
- FE fixture: `mocks/chat/`
- 데이터 흐름: `docs/architecture/data-flow.md`
- 현재 Tool Hub·RAG는 미연동 상태다. BE1은 API·세션·LangGraph 단계 전이를 담당하며,
  BE2가 준비되기 전에는 OpenAI 구조화 재료 추출과 임시 추천 데이터로 FE 통합을 검증한다.

다음 문서는 구현의 기준이다.

| 변경 영역 | 먼저 확인할 문서·코드 |
| --- | --- |
| Chat API 계약 | `docs/api/chat.md`, `app/schemas/chat.py`, `mocks/chat/`, `tests/` |
| LangGraph 워크플로우 | `app/agent/graph.py`, `app/services/chat_service.py`, `docs/architecture/data-flow.md` |
| API 라우팅 | `app/api/v1/endpoints/chat/router.py`, `app/main.py` |
| 로컬 실행·온보딩 | `README.md`, `docs/operations/onboarding.md` |

문서의 구현 상태와 실제 라우터·테스트를 일치시킨다. 구현되지 않은 Tool Hub·RAG 기능을
구현 완료로 표현하지 않는다.

## 2. 폴더 책임 경계

```text
app/
├── api/v1/endpoints/chat/  HTTP 입력 수신, 응답 모델 노출
├── schemas/                Pydantic 요청·응답 DTO와 검증 규칙
├── agent/                  LangGraph State, 노드, 전이 규칙
│   └── tools/              BE2 Tool Hub 연결 어댑터의 확장 지점
├── services/               세션 상태와 워크플로우 실행 연결
├── integrations/           LLM·Vision·Recipe Source·Vector Store 연결
├── repositories/           세션·레시피 DB 접근
├── models/                 DB 모델
├── core/                   설정·공통 인프라
└── main.py                 앱 생성과 router 등록만 담당

docs/                       API 계약·구조·연동·운영 문서
mocks/chat/                 FE 개발용 요청·응답 fixture
prompts/                    버전 관리되는 LLM 운영 프롬프트
tests/                      pytest 단위·API 통합 테스트
```

- 라우터에 LangGraph 노드나 Tool Hub 호출 세부 구현을 넣지 않는다.
- 외부 입력과 외부 응답은 `app/schemas/`의 Pydantic 모델로 검증한다.
- `app/main.py`, `app/schemas/`, `docs/`, `pyproject.toml`은 공유 영역이다. 호환성을
  깨는 변경은 관련 담당자와 먼저 공유한다.

## 3. 역할과 공통 계약

| 역할 | 담당자 | 주 담당 영역 | 책임 산출물 |
| --- | --- | --- | --- |
| BE1 | 최경락 | LangGraph Orchestrator·API Server | `/chat` 계약, 세션 상태, LangGraph 분기, FE 응답 변환 |
| BE2 | 박동준 | Tool Hub·RAG | Tool 실행·RAG 검색, 정규화된 `ToolResult` 반환 |

### FE ↔ BE1 계약

```text
ChatRequest  = { session_id, message, attachments? }
attachment   = { type: "image", data }

ChatResponse.status = SUCCESS | NEED_MORE_INFO | ERROR
ChatResponse.step   = COMPLETED | IMAGE_INPUT | CONDITION_INPUT | INGREDIENT_CONFIRM
```

- FE는 `status`와 `step`으로만 화면 흐름을 분기한다.
- 표시 문구와 데이터는 `response`, `questions`, `ingredients`, `data`에서 읽는다.
- `ERROR` 응답에는 `step`을 포함하지 않는다.
- 성공 응답은 `recipe_sets` 2개, 세트별 레시피 5개를 유지한다.
- `shopping_list` 항목은 `ingredient`, `amount`만 가진다. 가격·비용·예산 필드를
  새로 추가하지 않는다.

### BE1 ↔ BE2 계약

BE2 인터페이스의 URL과 상세 DTO는 BE2 구현 시점에 합의한다. 그 전까지 BE1은 기본 설정에서
BE2를 호출하지 않으며, 테스트는 fixture와 주입된 provider로 전이·API 계약만 검증한다.

```text
ToolRequest = { session_id, tool_name, confirmed_ingredients, user_conditions }
ToolResult  = { result, source_metadata?, error? }
```

- 사용자 확인 전 이미지 인식 재료 후보를 추천·RAG 입력으로 사용하지 않는다.
- BE2 결과는 BE1 내부 상태에 반영한 뒤 `/chat` 응답 DTO로 변환한다.
- Tool Hub 연동으로 외부 API 계약을 바꿔서는 안 된다. 계약 변경이 필요하면 5절을
  따른다.

## 4. 공통 구현 규칙

### Python·FastAPI·LangGraph

- 타입 힌트를 작성하고, 공개 클래스·함수·라우터에는 역할을 설명하는 docstring을 둔다.
- 복잡한 상태 전이·fallback·데이터 변환에는 왜 필요한지 설명하는 한국어 주석을 작성한다.
- API 키·토큰·이미지 원문·사용자 메시지 전체를 코드, fixture, 문서, 로그에 넣지 않는다.
- 외부 I/O는 async API를 사용한다. 동기식·오래 걸리는 작업을 async 라우터에서 직접
  실행하지 않는다.
- 설정값과 비밀값은 환경변수에서만 읽고, 새 환경변수는 `.env.example`과 문서에 함께
  반영한다. 실제 비밀값은 커밋하지 않는다.
- LangGraph 노드는 한 가지 책임을 맡고, 상태 전이는 테스트로 고정한다. `session_id`별
  상태가 다른 세션으로 섞이지 않도록 한다.
- `main.py` 변경 시 router 등록과 `/openapi.json` 노출을 확인한다.

### Tool Hub·RAG 연동

- BE1의 외부 응답과 RAG 문서는 신뢰할 수 없는 입력으로 취급한다.
- 실 Tool Hub·LLM·Vector Store 호출을 기본 테스트에 연결하지 않는다. 주입된 provider 또는
  fixture로 성공·실패 경로를 재현한다.
- 운영 프롬프트는 최상단 `prompts/`에서만 관리한다. 사용자 메시지나 Tool Hub·RAG
  결과를 `instructions`에 붙여 넣지 않고, 출처를 표시한 비신뢰 입력 블록으로 전달한다.
- Tool Hub의 timeout, 재시도, fallback, 결과 DTO를 바꾸면 BE2·BE1 담당자와 API 계약,
  fixture, 테스트를 함께 갱신한다.
- BE2가 아직 준비되지 않은 영역은 임시 데이터를 사용할 수 있으나, 코드 docstring과
  문서에 임시 동작임을 표시하고 교체 지점을 분리한다.

## 5. API·계약 변경 규칙

`/chat`의 경로, 메서드, 요청·응답 필드, 상태 코드, 상태 전이, fixture를 변경하면 같은
변경에 아래 항목을 포함한다.

1. `app/schemas/`의 DTO와 검증 규칙을 수정한다.
2. 라우터·서비스·LangGraph 전이를 계약과 일치시킨다.
3. `docs/api/chat.md`와 관련 `mocks/chat/*.json`을 갱신한다.
4. 정상 흐름과 해당 실패·입력 검증 흐름의 테스트를 추가·수정한다.
5. `/openapi.json`에서 경로와 요청·응답 스키마가 의도대로 노출되는지 확인한다.
6. FE 또는 BE2에 영향을 주면 변경 전 담당자에게 공유한다.

기존 FE 계약을 깨는 필드 삭제·이름 변경·타입 변경은 임의로 진행하지 않는다. 호환 계층,
버전 경로, 또는 팀 합의된 동시 배포 방식을 먼저 결정한다.

## 6. 의존성·데이터·Git 규칙

- 새 패키지 전에 Python 표준 라이브러리와 기존 의존성으로 해결 가능한지 확인한다.
- 패키지 변경은 `uv add` 또는 `uv add --dev`로 수행하고 `uv.lock`을 함께 갱신한다.
- 로컬 DB·ChromaDB·업로드 파일·`.env`는 커밋하지 않는다.
- 운영 데이터 삭제, 대량 갱신, `git push --force`, 이미 적용된 migration 수정은 실행 전
  사람의 확인을 받는다.
- 한 작업은 한 목적에 집중한다. 다른 담당자의 파일을 무관하게 포맷·되돌리지 않는다.
- 브랜치는 `main`에 직접 커밋하지 않고 `feat/`, `fix/`, `docs/`, `chore/` 접두어를
  사용한다. Codex 작업 브랜치에는 `codex/` 접두어를 추가할 수 있다.
- 커밋 전 `git status`와 `git diff`로 의도하지 않은 파일과 비밀값이 없는지 확인한다.

## 7. 에이전트 작업 원칙

- 담당 경계가 모호하거나 공유 계약 변경이 필요한 경우, 임의로 다른 담당자의 도메인
  로직을 설계하지 않는다. 계약에 맞는 fixture·fake를 사용하거나 필요한 결정을 문서화해
  사람에게 확인을 요청한다.
- 문서와 작업 지시가 충돌하면, 충돌 지점을 구체적으로 설명하고 확인받는다. 단, 명백한
  문서 오탈자나 구현 상태 표기 오류는 근거를 남기고 수정할 수 있다.
- 요청 범위를 벗어난 리팩터링, 패키지 교체, 무관한 포맷팅은 하지 않는다.
- 테스트를 실행하지 못했거나 체크리스트 일부를 건너뛴 경우, 완료로 보고하지 않고 이유를
  함께 남긴다.

## 8. 작업 완료 체크리스트

- [ ] 코드가 역할·폴더 책임 경계에 맞게 배치되어 있다.
- [ ] API 변경이면 DTO, 라우터, LangGraph/서비스, 명세, mock, 테스트가 함께 갱신되었다.
- [ ] 상태 코드·단계 코드와 실제 응답이 `docs/api/chat.md`와 일치한다.
- [ ] Tool Hub 미연동 영역은 문서화된 LLM fallback 또는 fixture를 사용하며 임시 동작과 교체 지점이 표시되었다.
- [ ] 새 환경변수·의존성·외부 서비스 변경이 관련 파일에 반영되었고 비밀값은 없다.
- [ ] `PYTHONPATH=. uv run pytest`와 `git diff --check`를 실행했거나 실행할 수 없는 이유를
  기록했다.
- [ ] 공유 파일 또는 FE·BE2 계약을 바꿨다면 관련 담당자에게 리뷰를 요청했다.
