# PlanEat Backend 온보딩

## 1. 준비

- Python 3.11 이상
- Git
- uv

```bash
uv --version
```

## 2. 설치 및 실행

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

확인:

- <http://127.0.0.1:8000/health>
- <http://127.0.0.1:8000/docs>

`.env.example`을 복사해 `.env`를 만들고 키를 입력합니다.

```bash
cp .env.example .env
```

NeMo Guardrails는 기본 활성화되며 `guardrails/config.yml`을 읽습니다. 로컬 사고 대응이나
호환성 확인 때문에 일시적으로 끄려면 `NEMO_GUARDRAILS_ENABLED=false`를 설정합니다.
설정 상세와 Tool Hub 연동 시 확장 지점은 [Guardrails 연동 가이드](../integrations/guardrails.md)를 참고합니다.

LangGraph 실행을 LangSmith에서 확인하려면 `.env`에 `LANGSMITH_TRACING=true`,
`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`를 설정합니다. tracing은 선택 사항이며,
사용자 메시지와 이미지 원문은 trace payload에 기록하지 않습니다. 자세한 설정과 확인 방법은
[LangSmith 연동 가이드](../integrations/langsmith.md)를 참고합니다.

기본적으로 `TOOL_HUB_ENABLED=true`이면 완료 단계에서 로컬 CSV 기반 Recipe·Nutrition·
Shopping Tool을 실행합니다. 저장소에 포함된 `data/chroma/` 사전 생성 인덱스가 Recipe Guide와
레시피 의미 검색 후보를 보강하며, 레시피 상세의 원본은 계속 CSV입니다. 서버는 이 인덱스를
열기만 하고 기동 시 임베딩하지 않습니다. `.env`에서 `CHROMA_AUTO_INDEX_ON_STARTUP=false`를
유지합니다.

CSV를 의도적으로 변경할 때에만 아래 명령으로 인덱스를 생성·갱신합니다. 생성된
`data/chroma/` 변경 파일은 CSV 변경과 함께 검토 후 커밋합니다.

```bash
PYTHONPATH=. uv run python scripts/index_recipe_catalog.py --persist-directory data/chroma
```

자동 색인은 메모리를 크게 사용할 수 있으므로 일반 개발·데모 환경에서는 사용하지 않습니다.

기본 카탈로그를 바꾸려면
`TOOL_HUB_CATALOG_PATH`를 지정합니다. Tool을 끄고 기존 OpenAI 임시 추천만 확인하려면
`TOOL_HUB_ENABLED=false`로 설정합니다.

기본 source는 `data/COOKRCP01_FINAL_WITH_INGREDIENT_GROUPS_REVISED_V2.csv`이며, 동일한 확정
재료·조건에는 결정적인 추천 결과가 반환될 수 있습니다. `mocks/chat/response-success.json`은
런타임 source가 아니라 FE fixture와 테스트용 예시입니다.

## 3. API 작업 기준

- 진입점: `POST /chat`
- 요청: `session_id`, `message`, 선택적 `attachments` (`message`에서 식단 목적과 선택 조리 시간을 판별하고 세션에 누적)
- 첨부파일: 현재 `image`, 최대 5개
- 상태: `SUCCESS`, `NEED_MORE_INFO`, `ERROR`
- 재료가 없는 요청은 `IMAGE_INPUT` 단계로 사진 또는 자연어 재료 입력을 안내하며, 첫 요청이라도
  `message`에 자연어 재료가 있으면 이미지 단계를 건너뜀
- 성공 응답: `recipe_sets` 2개, 각 세트의 레시피 5개
- 성공 후 `next_action=FEEDBACK_OR_SET_SELECTION`이면 피드백 재추천 또는 세트 선택을 받고,
  선택 시 `pdf_url`을 반환한다.
- 완료 단계: LangGraph `tool_hub_recipe_recommendation` node가 Tool Hub를 실행하고 결과를 검증
- NeMo Guardrails: 입력·LLM 출력·Tool 결과의 결정적 안전성 검사
- 상세 계약: [Chat API](../api/chat.md)

## 4. 코드 위치

- `app/api/v1/endpoints/chat/`: Chat API
- `app/schemas/`: 요청·응답 모델
- `app/agent/`: LangGraph State·Workflow
- `app/agent/tools/`: Vision·Recipe·Nutrition·Shopping Tool
- `app/services/`: 세션·대화 처리
- `app/repositories/`: DB 접근
- `app/integrations/`: LLM·Vision·Recipe Source·ChromaDB 연결
- `scripts/`: 레시피 데이터 적재·갱신
- `tests/`: 테스트

이미지 인식 결과를 바로 추천에 사용하지 말고, 사용자 확인 후 확정된 재료만 Agent State에 반영합니다. Recipe Tool은 내부 DB를 조회하고, 식품안전나라 API는 적재·갱신에만 사용합니다.

## 5. 확인

```bash
uv run pytest
git diff --check
git status
```

## 6. 선택 사항: TypeSafe Jev 자연어 판정

Jev는 사용자 메시지로부터 식단 목적이 충분히 입력됐는지와 재료 확인 의도
(`confirmed`, `rejected`, `edited`, `unclear`)를 `choice`와 confidence로 판단합니다. 조리 시간은
선택 입력으로 별도 필수 판정에 사용하지 않습니다. 외부
`/chat` 요청·응답 계약은 바꾸지 않습니다.

1. TypeSafe Console에서 API 키를 발급한다.
2. `.env`에 `TYPESAFE_JEV_ENABLED=true`, `TYPESAFE_API_KEY`, 필요하면 timeout·confidence 설정을 추가한다.
3. 키를 넣지 않은 상태에서는 기존 결정적 LangGraph 전이가 계속 동작하는지 확인한다.
4. 키를 넣은 상태에서는 불충분한 조건 입력이 `CONDITION_INPUT`을 유지하는지, 재료 확인 답변에 따라
   후보 확정·수정·재촬영·재확인이 분기되는지 검증한다.

Jev 호출 실패, 응답 형식 오류, 또는 최저 confidence 미만 결과는 모두 기존 전이로 fallback된다. 운영 활성화 전에는 사용자 메시지가 TypeSafe에 전송되는 것에 대한 개인정보·데이터 보관 검토가 필요하다.
