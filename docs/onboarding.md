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

## 3. API 작업 기준

- 진입점: `POST /chat`
- 요청: `session_id`, `message`, 선택적 `attachments`
- 첨부파일: 현재 `image`, 최대 5개
- 상태: `SUCCESS`, `NEED_MORE_INFO`, `ERROR`
- 상세 계약: [docs/api/chat.md](docs/api/chat.md)

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
