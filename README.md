# PlanEat Backend

FastAPI와 LangGraph 기반의 대화형 식단 추천 백엔드입니다. 사용자의 자연어 요청과 최대 5장의 이미지를 `/chat`으로 받아 재료 확인, 조건 질문, 레시피·영양·장보기 결과를 반환합니다.

## 핵심 기능

- 첨부 이미지 최대 5장 처리
- 이미지와 직접 입력한 재료가 모두 없으면 이미지 또는 자연어 재료 입력 단계로 전환
- 첫 요청이라도 자연어 재료가 있으면 이미지 단계를 건너뛰고, 목적이 있으면 바로 추천
- 이미지별 식재료 추출 및 중복 병합
- 사용자 재료 확인·수정
- `session_id` 기반 LangGraph 상태 관리
- 프로젝트 내부 CSV 레시피 카탈로그 기반 추천

- 성공 응답은 레시피 2세트, 세트당 5개 레시피
- 최종 추천 후 피드백 재추천 또는 선택 세트 상세 PDF URL 반환
- Nutrition·Shopping Tool 호출
- ChromaDB 기반 재료 활용법·대체재·보관법 검색
- NeMo Guardrails 기반 입력·출력·Tool 결과 안전성 검사

## API

단일 진입점은 `POST /chat`이며, 생성된 선택 세트 PDF는 `GET /pdfs/{filename}`으로 다운로드합니다.

상세 요청·응답 형식은 [Chat API 명세](docs/api/chat.md)를 참고합니다.
역할별 데이터 교환은 [데이터 흐름도](docs/architecture/data-flow.md)를 참고합니다.
프롬프트 관리·보안 원칙은 [Prompt 관리](docs/operations/prompt-management.md)를 참고합니다.
NeMo Guardrails 설정·적용 범위는 [Guardrails 연동 가이드](docs/integrations/guardrails.md)를 참고합니다.
TypeSafe Jev 자연어 판정 설정은 [Jev 연동 가이드](docs/integrations/jev.md)를 참고합니다.
소스 구조와 실행 흐름은 [구현 개요](docs/architecture/implementation-overview.md)를 참고합니다.
Tool Hub 연동 상태와 외부 endpoint 확장 지점은 [Tool Hub 연동 상태](docs/integrations/tool-hub-readiness.md)를 참고합니다.
전체 문서 카테고리와 권장 읽기 순서는 [문서 안내](docs/README.md)를 참고합니다.

주요 상태:

- `SUCCESS`: 추천 완료
- `NEED_MORE_INFO`: 조건 또는 재료 확인 필요
- `ERROR`: 처리 실패

## 처리 원칙

- 이미지 인식 결과는 후보이며, 사용자가 확인한 재료만 추천에 사용합니다.
- 이미지를 사용할 수 없는 경우 `message`에 직접 입력한 지원 재료로도 추천을 진행합니다.
- `session_id`로 대화와 LangGraph State를 이어갑니다.
- Recipe Tool은 프로젝트 내부 CSV 레시피 카탈로그를 조회합니다.
- 레시피 추천 과정에서 외부 Recipe API를 호출하지 않습니다.
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
│   └── integrations/
│   │   ├── llm/                        # LLM Provider
│   │   ├── vision/                     # 이미지 인식 Provider
│   │   ├── recipe_source/              # 로컬 CSV 레시피 카탈로그
│   │   └── vector_store/               # ChromaDB
├── prompts/                            # LLM 운영 프롬프트
├── guardrails/                         # NeMo Guardrails 설정
├── tests/                              # 단위·통합 테스트
├── docs/                               # 프로젝트 관련 문서
│   ├── README.md                       # 문서 카테고리와 읽기 순서
│   ├── api/                            # API 계약
│   ├── architecture/                   # 구조·데이터 흐름
│   ├── integrations/                   # 외부 연동·안전성
│   └── operations/                     # 개발·운영
├── data/                               # 레시피 CSV·사전 생성 ChromaDB(커밋)
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

현재 `/chat`은 LangGraph 기반의 세션 단계 전이를 제공합니다. 이미지 재료와 자연어 재료는
OpenAI 구조화 추출로 처리하고, 재료 확인이 끝난 완료 단계에서는 Tool Hub Recipe·Nutrition·
Shopping·RAG Tool 결과를 반환합니다. `TOOL_HUB_ENABLED=false`이면 OpenAI가 생성한
임시 추천 경로로 fallback합니다.

완료 응답의 `response`는 두 추천 세트 중 하나를 선택해 상세 PDF를 발급받도록 안내하는
서버 템플릿이며, 실제 추천 차이는 `data.recipe_sets`에서 확인합니다. 현재 추천이 마음에
들지 않아 재생성을 요청하면 직전 레시피 ID를 후보에서 제외해 새 레시피를 찾습니다. 기본 Tool Hub는
프로젝트 내부 CSV를 결정적으로 검색합니다.
`mocks/chat/response-success.json`은 FE fixture와 테스트 전용이며 런타임 응답 source가 아닙니다.

### 로컬 레시피 CSV

기본 카탈로그는 `data/COOKRCP01_FINAL_WITH_INGREDIENT_GROUPS_REVISED_V2.csv`입니다.
외부 API를 호출하지 않고 이 내부 CSV만 읽습니다. 파일은 UTF-8(BOM 허용)으로 저장하며,
보강 내부 형식의 주요 열은 아래와 같습니다.

```text
RCP_SEQ,RCP_NM,RCP_PAT2,RCP_PARTS_DTLS,REQUIRED_INGREDIENTS,
OPTIONAL_INGREDIENTS,SUBSTITUTABLE_INGREDIENTS,REQUIRED_SEASONINGS,SUBSTITUTABLE_SEASONINGS
```

`REQUIRED_*`, `SUBSTITUTABLE_*`, `OPTIONAL_INGREDIENTS`는 각각 필수, 대체 가능,
생략 가능 재료로 정규화됩니다. `RCP_PAT2`는 메인·반찬 조합을 위한 역할로 사용합니다.
필요하면 정규화된 자체 CSV 경로를 `CsvRecipeRepository` 생성자에 전달할 수도 있습니다.

기본 `PlanEatToolHub.from_local_catalog(...)`는 `data/`의 보강 내부 CSV를 읽습니다.
이 카탈로그의 재료 중요도는 제외 재료 재계획에 사용됩니다.

### 사전 생성 Chroma 인덱스

`data/chroma/`에는 레시피 카탈로그의 사전 생성 임베딩 인덱스가 포함되어 있습니다. 서버는
기본적으로 이 인덱스를 열어 의미 검색에 사용하며, 기동할 때 다시 임베딩하지 않습니다.
`CHROMA_AUTO_INDEX_ON_STARTUP`은 `false`로 유지하세요. CSV를 의도적으로 교체한 경우에만
수동 색인 명령을 실행하고, 바뀐 `data/chroma/` 파일도 함께 검토해 커밋합니다.

LLM 추천·완료 응답을 사용하려면 `.env`에 `OPENAI_API_KEY`를 설정합니다. 기본 모델은
`gpt-4o-mini`이며, 필요하면 `OPENAI_MODEL`로 변경할 수 있습니다.

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

`TYPESAFE_JEV_ENABLED=true`와 `TYPESAFE_API_KEY`를 설정하면, Jev가 누적된 자연어 `message`에서 필수 식단 목적 충족 여부와 재료 확인 의도(`confirmed`, `rejected`, `edited`, `unclear`)를 판정합니다. 조리 시간은 선택 입력으로 저장되어 입력된 경우에만 추천 필터에 반영됩니다. 신뢰도가 `TYPESAFE_JEV_MIN_CONFIDENCE` 이상일 때만 결과를 반영합니다. API 키가 없거나 Jev 호출이 실패·저신뢰이면 조건 키워드와 확인 표현을 확인하는 보수적 fallback이 적용됩니다.

Jev에는 이 판정에 필요한 현재 사용자 메시지만 전송됩니다. 실서비스 활성화 전에는 개인정보 처리·보관 정책과 TypeSafe 계약을 확인하세요.
