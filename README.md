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

## API

단일 진입점은 `POST /chat`입니다.

상세 요청·응답 형식은 [Chat API 명세](docs/api/chat.md)를 참고합니다.
역할별 데이터 교환은 [데이터 흐름도](docs/data-flow.md)를 참고합니다.

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
├── scripts/                            # 레시피 데이터 적재·갱신
├── tests/                              # 단위·통합 테스트
├── docs/api/chat.md                    # Chat API 명세
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

