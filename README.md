# PlanEat Backend

FastAPI와 LangGraph 기반의 대화형 식단 추천 백엔드입니다. 사용자의 자연어 요청과 최대 5장의 이미지를 `/chat`으로 받아 재료 확인, 조건 질문, 레시피·영양·장보기 결과를 반환합니다.

## 핵심 기능

- 첨부 이미지 최대 5장 처리
- 이미지가 없으면 이미지 첨부 요청 단계로 전환
- 이미지별 식재료 추출 및 중복 병합
- 사용자 재료 확인·수정
- `session_id` 기반 LangGraph 상태 관리
- 내부 레시피 DB 기반 추천

- 성공 응답은 레시피 2세트, 세트당 5개 레시피
- Nutrition·Shopping Tool 호출
- ChromaDB 기반 재료 활용법·대체재·보관법 검색
- NeMo Guardrails 기반 입력·출력·Tool 결과 안전성 검사

## API

단일 진입점은 `POST /chat`입니다.

상세 요청·응답 형식은 [Chat API 명세](docs/api/chat.md)를 참고합니다.
역할별 데이터 교환은 [데이터 흐름도](docs/architecture/data-flow.md)를 참고합니다.
프롬프트 관리·보안 원칙은 [Prompt 관리](docs/operations/prompt-management.md)를 참고합니다.
NeMo Guardrails 설정·적용 범위는 [Guardrails 연동 가이드](docs/integrations/guardrails.md)를 참고합니다.
TypeSafe Jev 자연어 판정 설정은 [Jev 연동 가이드](docs/integrations/jev.md)를 참고합니다.
소스 구조와 실행 흐름은 [구현 개요](docs/architecture/implementation-overview.md)를 참고합니다.
Tool Hub 연결 전 준비 상태는 [Tool Hub 연동 준비](docs/integrations/tool-hub-readiness.md)를 참고합니다.
전체 문서 카테고리와 권장 읽기 순서는 [문서 안내](docs/README.md)를 참고합니다.

주요 상태:

- `SUCCESS`: 추천 완료
- `NEED_MORE_INFO`: 조건 또는 재료 확인 필요
- `ERROR`: 처리 실패

## 처리 원칙

- 이미지 인식 결과는 후보이며, 사용자가 확인한 재료만 추천에 사용합니다.
- `session_id`로 대화와 LangGraph State를 이어갑니다.
- Recipe Tool은 내부 레시피 DB를 조회합니다.
- 식품안전나라 API는 레시피 초기 적재·갱신에만 사용합니다.
- 원본 레시피 정보와 LLM 생성 설명을 구분합니다.
- 새로운 Tool은 `/chat` API를 바꾸지 않고 LangGraph Workflow에 추가합니다.

## 폴더 구조

```text
.
├── app/
│   ├── main.py                         # FastAPI 진입점
│   ├── api/v1/endpoints/chat/          # POST /chat
│   ├── agent/                          # LangGraph graph·state
│   │   └── tools/                      # Vision·Recipe·Nutrition·Shopping Tool
│   ├── schemas/                        # Chat Request·Response 모델
│   ├── services/                       # 세션·대화 처리
│   ├── repositories/                   # 세션·레시피 DB 접근
│   ├── models/                         # DB 모델
│   ├── integrations/
│   │   ├── llm/                        # LLM Provider
│   │   ├── vision/                     # 이미지 인식 Provider
│   │   ├── recipe_source/              # 식품안전나라 적재·갱신
│   │   └── vector_store/               # ChromaDB
│   └── db/                             # DB 설정
├── prompts/                            # LLM 운영 프롬프트
├── guardrails/                          # NeMo Guardrails 설정
├── tests/                              # 단위·통합 테스트
├── docs/                               # 프로젝트 관련 문서
│   ├── README.md                       # 문서 카테고리와 읽기 순서
│   ├── api/                            # API 계약
│   ├── architecture/                   # 구조·데이터 흐름
│   ├── integrations/                   # 외부 연동·안전성
│   └── operations/                     # 개발·운영
├── data/                               # 로컬 DB·ChromaDB(커밋하지 않음)
├── pyproject.toml                      # uv 의존성 설정
├── .env.example                        # 환경 변수 예시
└── .gitignore
```

## 실행

```bash
uv sync
uv run uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

환경 변수:

```bash
cp .env.example .env
```

## 테스트

```bash
uv run pytest
git diff --check
```

현재 `/chat`은 LangGraph 기반의 세션 단계 전이를 제공합니다. Tool Hub·RAG 연동 전에는 API·FE 통합 검증을 위한 임시 재료·추천 데이터를 반환합니다.

LLM 최종 응답을 사용하려면 `.env`에 `OPENAI_API_KEY`를 설정합니다. 기본 모델은 `gpt-4o-mini`이며, 필요하면 `OPENAI_MODEL`로 변경할 수 있습니다.

### 선택 사항: LangSmith tracing

LangGraph 실행을 LangSmith에서 확인하려면 `.env`에 `LANGSMITH_TRACING=true`,
`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`를 설정합니다. `/chat` 요청의 상태 전이와
첨부 개수 등 비식별 metadata만 전송하며, 사용자 메시지와 이미지 원문은 trace payload에서
숨깁니다. API 키가 없거나 tracing을 `false`로 두면 LangSmith 호출 없이 동작합니다.
상세 설정과 trace 범위는 [LangSmith 연동 가이드](docs/integrations/langsmith.md)를 참고하세요.

NeMo Guardrails는 기본 활성화되며 `guardrails/config.yml`의 정규식 입력·출력 rail을
사용합니다. 장애가 발생하면 기존 `app/core/safety.py` 검사로 안전하게 fallback합니다.
긴급하게 비활성화해야 할 때만 `.env`에서 `NEMO_GUARDRAILS_ENABLED=false`로 설정합니다.

### 선택 사항: TypeSafe Jev 자연어 판정

`TYPESAFE_JEV_ENABLED=true`와 `TYPESAFE_API_KEY`를 설정하면, Jev가 자연어 `message`에서 식단 목표·조리 시간 충족 여부와 재료 확인 의도(`confirmed`, `rejected`, `edited`, `unclear`)를 판정합니다. 신뢰도가 `TYPESAFE_JEV_MIN_CONFIDENCE` 이상일 때만 결과를 반영합니다. API 키가 없거나 Jev 호출이 실패·저신뢰이면 조건 키워드와 확인 표현을 확인하는 보수적 fallback이 적용됩니다.

Jev에는 이 판정에 필요한 현재 사용자 메시지만 전송됩니다. 실서비스 활성화 전에는 개인정보 처리·보관 정책과 TypeSafe 계약을 확인하세요.
