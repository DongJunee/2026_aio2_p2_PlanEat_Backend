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
설정 상세와 BE2 연동 시 확장 지점은 [Guardrails 연동 가이드](guardrails.md)를 참고합니다.

## 3. API 작업 기준

- 진입점: `POST /chat`
- 요청: `session_id`, `message`, 선택적 `attachments` (`message`에서 식단 목적·조리 시간을 판별)
- 첨부파일: 현재 `image`, 최대 5개
- 상태: `SUCCESS`, `NEED_MORE_INFO`, `ERROR`
- 이미지와 자연어 조건이 모두 없으면 `INPUT_REQUIREMENTS` 단계로 한 번에 요청
- 성공 응답: `recipe_sets` 2개, 각 세트의 레시피 5개
- NeMo Guardrails: 입력·LLM 출력·Tool 결과의 결정적 안전성 검사
- 상세 계약: [Chat API](api/chat.md)

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

Jev는 사용자 메시지로부터 식단 목표·조리 시간이 충분히 입력됐는지와 재료 확인 의도
(`confirmed`, `rejected`, `edited`, `unclear`)를 `choice`와 confidence로 판단합니다. 외부
`/chat` 요청·응답 계약은 바꾸지 않습니다.

1. TypeSafe Console에서 API 키를 발급한다.
2. `.env`에 `TYPESAFE_JEV_ENABLED=true`, `TYPESAFE_API_KEY`, 필요하면 timeout·confidence 설정을 추가한다.
3. 키를 넣지 않은 상태에서는 기존 결정적 LangGraph 전이가 계속 동작하는지 확인한다.
4. 키를 넣은 상태에서는 불충분한 조건 입력이 `CONDITION_INPUT`을 유지하는지, 재료 확인 답변에 따라
   후보 확정·수정·재촬영·재확인이 분기되는지 검증한다.

Jev 호출 실패, 응답 형식 오류, 또는 최저 confidence 미만 결과는 모두 기존 전이로 fallback된다. 운영 활성화 전에는 사용자 메시지가 TypeSafe에 전송되는 것에 대한 개인정보·데이터 보관 검토가 필요하다.
