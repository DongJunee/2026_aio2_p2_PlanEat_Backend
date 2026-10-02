# PlanEat Backend 구현 개요

이 문서는 현재 저장소에 구현된 Chat API의 구성, 요청 흐름, 상태 전이, 응답 계약과
외부 AI 연동 지점을 한눈에 설명한다. Tool Hub·RAG·실제 Vision 연동은 아직 구현 범위가
아니며, 해당 부분은 FE 통합 검증을 위한 결정적 임시 데이터로 동작한다.

## 1. 전체 구조

```text
FE
  │ POST /chat (session_id, message, attachments?)
  ▼
FastAPI Router
  ▼
ChatService ── 세션별 현재 단계 저장
  │
  ├── Jev 조건 판정 (선택 사항, CONDITION_INPUT에서만)
  ▼
LangGraph route_chat
  ▼
응답 변환
  ├── IMAGE_INPUT / INGREDIENT_CONFIRM / CONDITION_INPUT
  └── COMPLETED → 임시 레시피 데이터 + OpenAI 완료 안내 문구
  ▼
ChatResponse JSON
```

| 영역 | 주요 파일 | 역할 |
| --- | --- | --- |
| 앱 생성·공통 오류 형식 | `app/main.py` | FastAPI 앱, router 등록, 요청 DTO 검증 오류를 `ERROR` JSON으로 변환, OpenAPI 정리 |
| HTTP 진입점 | `app/api/v1/endpoints/chat/router.py` | `POST /chat` 요청 수신, 서비스 호출, HTTP 상태 설정 |
| DTO·API 계약 | `app/schemas/chat.py` | 요청·응답 Pydantic 모델 및 필드 제약 |
| 상태 전이 | `app/agent/graph.py` | LangGraph `ChatState`와 단계별 분기 규칙 |
| 세션·응답 조립 | `app/services/chat_service.py` | 세션 상태 저장, Graph 실행, fixture 레시피·응답 변환 |
| 최종 안내 문구 | `app/integrations/llm/openai_responder.py` | OpenAI Responses API 호출 및 출력 안전성 검사 |
| 선택적 조건 판정 | `app/integrations/decision_engine/typesafe_jev.py` | TypeSafe Jev 호출, confidence 검증, fallback |
| 안전성 검사 | `app/core/safety.py` | 사용자 입력·LLM 출력의 위험한 패턴 검사 |

## 2. Chat API 계약

진입점은 `POST /chat`이며, 상세 명세는 [Chat API](api/chat.md)를 기준으로 한다.

### 요청

```json
{
  "session_id": "session-001",
  "message": "냉장고 재료로 저녁 메뉴를 추천해줘.",
  "attachments": [
    { "type": "image", "data": "<base64-image-or-url>" }
  ]
}
```

| 필드 | 구현 제약 |
| --- | --- |
| `session_id` | 빈 문자열 불가. 동일 세션의 워크플로우 단계를 이어 간다. |
| `message` | 1~2,000자. 안전성 검사를 통과해야 한다. |
| `attachments` | 선택 사항이며 최대 5개. 현재 `image` 타입만 허용한다. |

### 응답 공통 규칙

FE는 `status`, `step`만으로 화면 흐름을 분기한다.

| HTTP | `status` | `step` | 의미 |
| --- | --- | --- | --- |
| 200 | `NEED_MORE_INFO` | `IMAGE_INPUT` | 이미지 첨부가 필요하다. |
| 200 | `NEED_MORE_INFO` | `INGREDIENT_CONFIRM` | 인식된 재료 후보의 확인이 필요하다. |
| 200 | `NEED_MORE_INFO` | `CONDITION_INPUT` | 식단 목표·조리 시간 등의 조건이 필요하다. |
| 200 | `SUCCESS` | `COMPLETED` | 레시피 2세트(각 5개)가 준비됐다. |
| 400, 500 | `ERROR` | 없음 | 오류. 항상 `status`, `response`만 반환한다. |

모든 오류는 다음 JSON 형태를 보장한다.

```json
{
  "status": "ERROR",
  "response": "사용자에게 표시할 오류 문구"
}
```

FastAPI 기본 검증 오류인 `422 {"detail": ...}`는 `app/main.py`에서 `400 ERROR` 형식으로
변환한다. 따라서 FE가 오류 형식을 별도로 처리할 필요가 없다.

## 3. LangGraph 상태 전이

`app/agent/graph.py`의 내부 단계와 외부 `step`은 의도적으로 분리돼 있다.

```text
WAITING_IMAGE
  ├── 이미지 없음 ───────────────→ IMAGE_INPUT (WAITING_IMAGE 유지)
  └── 이미지 있음 ───────────────→ INGREDIENT_CONFIRM
                                      │
WAITING_INGREDIENT_CONFIRM ────────────┘
  └──────────────────────────────────→ CONDITION_INPUT
                                       (WAITING_CONDITIONS)

WAITING_CONDITIONS
  ├── Jev: needs_more_info, 신뢰도 충족 → CONDITION_INPUT 유지
  └── 그 외(ready·비활성·실패·저신뢰) → COMPLETED
```

| 내부 상태 | 외부 응답 | 다음 내부 상태 |
| --- | --- | --- |
| `WAITING_IMAGE`, 이미지 없음 | `IMAGE_INPUT` | `WAITING_IMAGE` |
| `WAITING_IMAGE`, 이미지 있음 | `INGREDIENT_CONFIRM` | `WAITING_INGREDIENT_CONFIRM` |
| `WAITING_INGREDIENT_CONFIRM` | `CONDITION_INPUT` | `WAITING_CONDITIONS` |
| `WAITING_CONDITIONS`, 조건 부족 | `CONDITION_INPUT` | `WAITING_CONDITIONS` |
| `WAITING_CONDITIONS`, 조건 충족 또는 fallback | `COMPLETED` | `COMPLETED` |
| `COMPLETED` | `COMPLETED` | `COMPLETED` |

세션 상태는 `ChatSessionRepository` 뒤의 프로세스 메모리에 저장된다. 현재는 단계뿐 아니라
재료 후보, 확정 재료, 마지막 조건 메시지를 보관할 수 있다. 서버를 재시작하면 상태가 초기화되고,
멀티 인스턴스 배포 시에는 같은 인터페이스의 Redis·DB 구현체로 교체해야 한다.

## 4. 완료 응답의 데이터 구성

`COMPLETED`에서는 `_demo_recipe_sets()`가 임시 레시피 데이터를 만든다. 이는 BE2 Tool Hub가
준비되기 전 계약·FE 통합을 검증하기 위한 것이며, 실제 검색·추천 결과가 아니다. 실제 연동에서는
BE1이 이미지를 직접 인식하지 않고, BE2 Vision Function Call이 반환한 후보를 FE가 확정한 뒤에만
Recipe·Nutrition·Shopping·RAG Function Call을 요청한다.

```json
{
  "status": "SUCCESS",
  "step": "COMPLETED",
  "response": "OpenAI가 생성한 짧은 한국어 안내 문구",
  "data": {
    "recipe_sets": [
      { "set_id": "...", "recipes": ["정확히 5개"] },
      { "set_id": "...", "recipes": ["정확히 5개"] }
    ]
  }
}
```

`app/schemas/chat.py`가 다음을 강제한다.

- `recipe_sets`: 정확히 2개
- 세트별 `recipes`: 정확히 5개
- `shopping_list` 항목: `ingredient`, `amount`만 허용
- nutrition: `calories`, `protein`, `carbohydrate`, `fat`이며 음수를 허용하지 않음

최종 `response` 문구는 `OpenAIResponder`가 생성한다. OpenAI API 키가 없거나 호출·출력
검증에 실패하면 `500 ERROR`를 반환한다. 레시피 데이터 자체는 LLM이 만들거나 변경하지 않는다.

## 5. TypeSafe Jev 조건 판정

Jev는 `CONDITION_INPUT`에서 현재 사용자 메시지의 식단 목표·조리 시간 충족 여부를 판단한다.
자세한 설치와 환경변수는 [Jev 연동 가이드](jev.md)를 참고한다.

1. `ChatService`가 현재 상태가 `WAITING_CONDITIONS`인지 확인한다.
2. 활성화된 경우 Jev API에 고정된 `choice` 질문을 보낸다.
3. `choice`가 `needs_more_info`이고 confidence가 기준 이상이면 조건 입력을 반복한다.
4. `ready`, API 실패, timeout, 응답 구조 오류, confidence 미달은 기존 Graph 흐름으로 fallback한다.

외부 API 호출은 세션 잠금 밖에서 수행해, 한 세션의 네트워크 지연이 다른 세션의 처리를
막지 않게 한다. API 키·사용자 메시지 원문은 애플리케이션 로그에 기록하지 않는다.

## 6. 안전 처리

### 사용자 입력

`ChatService.handle()`은 Graph 실행 전에 `validate_user_message()`를 호출한다. 위험한 패턴이
정규화 후 탐지되면 `400 ERROR`를 반환하며 상태 전이를 진행하지 않는다.

### OpenAI 프롬프트

운영 프롬프트는 `prompts/`에서만 조합한다.

- `instructions`: 보안·언어·완료 단계 지시만 포함
- `input`: `<untrusted_user_message>`, `<trusted_recipe_sets>` 블록으로 데이터 출처를 분리
- `store=False`: OpenAI 요청 저장을 비활성화
- 출력: 최종 응답으로 보내기 전에 `validate_completion_output()`으로 재검사

가격·비용·예산처럼 API 계약에 없는 정보는 최종 안내 문구에 추측해 넣지 않도록 프롬프트에서
제한한다.

## 7. 설정과 실행

최소 실행 설정은 `.env.example`을 복사한 `.env` 파일이다.

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload
```

| 설정 | 용도 | 없을 때 동작 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 완료 안내 문구 생성 | 완료 단계에서 `500 ERROR` |
| `OPENAI_MODEL` | OpenAI 모델 선택 | `gpt-4o-mini` 사용 |
| `TYPESAFE_JEV_ENABLED` | Jev 조건 판정 사용 여부 | `false`가 기본값 |
| `TYPESAFE_API_KEY` | Jev 인증 | Jev를 호출하지 않고 fallback |
| `TYPESAFE_MODEL` | Jev 모델 선택 | `jev-latest` 사용 |
| `TYPESAFE_TIMEOUT_SECONDS` | Jev 호출 제한 시간 | `2.0`초 |
| `TYPESAFE_JEV_MIN_CONFIDENCE` | Jev 판단 반영 기준 | `0.8` |

실제 키는 `.env`에만 보관하고 Git에 추가하지 않는다.

## 8. 테스트와 fixture

| 위치 | 검증 대상 |
| --- | --- |
| `tests/unit/test_chat_schema.py` | 요청 DTO 제약, 응답 DTO, 모든 응답 fixture의 계약 적합성 |
| `tests/unit/test_chat_service.py` | 상태 전이, 안전 오류, 요청 형식 오류, Jev low-confidence fallback, OpenAPI 응답 코드 |
| `tests/unit/test_prompt_management.py` | 프롬프트 조합과 비신뢰 입력 분리 |
| `tests/unit/test_safety.py` | 입력·출력 안전성 검사 |
| `mocks/chat/` | FE가 사용할 상태별 요청·응답 예시 |

검증 명령은 다음과 같다.

```bash
PYTHONPATH=. uv run pytest
git diff --check
```

## 9. 현재 한계와 교체 지점

| 현재 임시 구현 | 향후 교체 지점 |
| --- | --- |
| `_INGREDIENTS`의 고정 재료 후보 | Vision 또는 BE2 Tool Hub의 이미지 인식 결과 |
| `_demo_recipe_sets()`의 고정 레시피 10개 | BE2 Recipe·Nutrition·Shopping 결과 |
| 프로세스 메모리 세션 | DB 또는 Redis 세션 저장소 |
| Jev의 단일 조건 충분성 판단 | 합의된 정책에 따른 추가 라우팅·점수화 판단 |
| Tool Hub fake provider·호출 전 검증 | BE2 URL·timeout·재시도 정책을 반영한 실제 비동기 어댑터 |

Tool Hub·RAG가 연결되더라도 외부 `/chat` DTO, `status`, `step`, 레시피 2세트·세트당 5개라는
FE 계약은 유지해야 한다.
