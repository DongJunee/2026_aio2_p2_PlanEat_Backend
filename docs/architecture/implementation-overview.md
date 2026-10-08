# PlanEat Backend 구현 개요

이 문서는 현재 저장소에 구현된 Chat API의 구성, 요청 흐름, 상태 전이, 응답 계약과
외부 AI 연동 지점을 한눈에 설명한다. 완료 단계에서는 LangGraph가 Tool Hub Recipe·Nutrition·
Shopping·RAG Tool을 실행하고, 설정으로 비활성화한 경우에만 OpenAI 임시 추천으로 fallback한다.

## 1. 전체 구조

```text
FE
  │ POST /chat (session_id, message, attachments?)
  ▼
FastAPI Router
  ▼
ChatService ── 세션별 현재 단계 저장
  │
  ├── Jev 자연어 조건·재료 확인 판정 (선택 사항)
  ├── app.core.safety + NeMo Guardrails 입력 검사
  ├── LangSmith callback (선택 사항, 비식별 metadata만 전송)
  ▼
LangGraph summarize_conversation (> 10 messages) -> route_chat
  ▼
route_chat → (COMPLETED인 경우) Tool Hub recipe_recommendation Tool node
  ▼
응답 변환
  ├── INPUT_REQUIREMENTS / IMAGE_INPUT / INGREDIENT_CONFIRM / CONDITION_INPUT
  │      └── message 직접 입력 재료가 있으면 이미지 없이 조건 확인·추천으로 우회
  └── COMPLETED → Tool Hub 결과 또는 비활성화 시 OpenAI 임시 추천
                         │
                         └── RecommendationData·NeMo Guardrails 검증
  ▼
ChatResponse JSON → 피드백 재추천 또는 세트 선택
                         ├── Jev Plan → 조건 갱신 → 재추천·재검증
                         └── 선택 세트 → PDF 생성·저장 → pdf_url
```

| 영역 | 주요 파일 | 역할 |
| --- | --- | --- |
| 앱 생성·공통 오류 형식 | `app/main.py` | FastAPI 앱, router 등록, 요청 DTO 검증 오류를 `ERROR` JSON으로 변환, OpenAPI 정리 |
| HTTP 진입점 | `app/api/v1/endpoints/chat/router.py` | `POST /chat` 요청 수신, 서비스 호출, HTTP 상태 설정 |
| DTO·API 계약 | `app/schemas/chat.py` | 요청·응답 Pydantic 모델 및 필드 제약 |
| 상태 전이 | `app/agent/graph.py` | LangGraph `ChatState`와 단계별 분기 규칙 |
| 세션·응답 조립 | `app/services/chat_service.py` | 세션 상태 저장, Graph 실행, Tool Hub/LLM 추천·응답 변환 |
| 재료 추출 | `app/integrations/llm/openai_responder.py` | 이미지·자연어 입력에서 재료와 수량을 Structured Outputs로 추출·검증 |
| LLM 추가 안내·추천 | `app/integrations/llm/openai_responder.py` | OpenAI Structured Outputs 기반 부족 정보 질문·임시 추천 생성과 응답 검증 |
| 자연어 조건·확인 판정 | `app/integrations/decision_engine/typesafe_jev.py` | 식단 조건 충분성 및 재료 확인 의도에 대한 Jev choice, confidence 검증, fallback |
| 재료 확인·Tool 준비 | `app/services/chat_service.py`, `app/agent/graph.py` | 후보 수정·확정, 세션 반영, 완료 단계 Tool Hub node 실행과 결과 변환 |
| 안전성 검사 | `app/core/safety.py` | 사용자 입력·LLM 출력의 위험한 패턴 검사 |
| NeMo Guardrails | `app/integrations/guardrails/nemo.py`, `guardrails/config.yml` | 입력·출력·Tool 결과의 NeMo IORails 정규식 검사 및 장애 시 기존 안전성 검사 fallback |
| LangSmith observability | `app/core/observability.py` | LangGraph 실행 trace와 비식별 상태 metadata 전송. 사용자 입력·이미지 원문은 숨김 |

## 2. Chat API 계약

진입점은 `POST /chat`이며, 상세 명세는 [Chat API](../api/chat.md)를 기준으로 한다.

### 요청

```json
{
  "session_id": "session-001",
  "message": "다이어트 식단으로 20분 안에 만들고 싶어요.",
  "attachments": [
    { "type": "image", "data": "<base64-image-or-url>" }
  ]
}
```

| 필드 | 구현 제약 |
| --- | --- |
| `session_id` | 빈 문자열 불가. 동일 세션의 워크플로우 단계를 이어 간다. |
| `message` | 1~2,000자. 안전성 검사를 통과해야 한다. |
| `attachments` | 선택 사항이며 최대 5개. 생략하면 빈 목록으로 처리하고, 현재 `image` 타입만 허용한다. |
| 식단 목적·선택 시간 | 별도 요청 필드 없이 `message`에서 자연어로 누적한다. 목적만 필수이며 Jev 또는 fallback이 충분성을 판정한다. |

### 응답 공통 규칙

FE는 `status`, `step`만으로 화면 흐름을 분기한다.

| HTTP | `status` | `step` | 의미 |
| --- | --- | --- | --- |
| 200 | `NEED_MORE_INFO` | `INPUT_REQUIREMENTS` | 이미지와 조건을 동시에 수집하는 호환 응답이다. |
| 200 | `NEED_MORE_INFO` | `IMAGE_INPUT` | 재료가 없을 때 이미지 또는 자연어 재료 입력을 안내한다. 첫 요청의 자연어 재료는 바로 확정한다. |
| 200 | `NEED_MORE_INFO` | `INGREDIENT_CONFIRM` | 인식된 재료 후보의 확인이 필요하다. |
| 200 | `NEED_MORE_INFO` | `CONDITION_INPUT` | 필수 식단 목적이 필요하다. 조리 시간은 선택값이다. |
| 200 | `SUCCESS` | `COMPLETED` | 레시피 2세트(각 5개)가 준비됐다. 이후 피드백 또는 세트 선택을 받는다. |
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
  ├── message에 직접 재료 있음 ───→ 조건 있음: COMPLETED / 없음: CONDITION_INPUT
  └── 이미지 있음 ───────────────→ INGREDIENT_CONFIRM
                                      │
WAITING_INGREDIENT_CONFIRM ────────────┘
  ├── confirmed + 조건 있음 ─────────→ COMPLETED + ToolRequest
  ├── confirmed + 조건 없음 ─────────→ CONDITION_INPUT
  ├── edited ───────────────────────→ INGREDIENT_CONFIRM
  ├── rejected ─────────────────────→ IMAGE_INPUT
  └── unclear ──────────────────────→ INGREDIENT_CONFIRM
                                       (WAITING_CONDITIONS)

WAITING_CONDITIONS
  ├── 자연어 조건 부족 ────────────→ CONDITION_INPUT 유지
  └── 자연어 조건 충족 ────────────→ COMPLETED
```

| 내부 상태 | 외부 응답 | 다음 내부 상태 |
| --- | --- | --- |
| `WAITING_IMAGE`, 이미지 없음 | `IMAGE_INPUT` | `WAITING_IMAGE` |
| `WAITING_IMAGE`, 이미지 요청 후 message 직접 재료·조건 있음 | `COMPLETED` | `COMPLETED` |
| `WAITING_IMAGE`, 이미지 요청 후 message 직접 재료·조건 없음 | `CONDITION_INPUT` | `WAITING_CONDITIONS` |
| `WAITING_IMAGE`, 이미지 있음 | `INGREDIENT_CONFIRM` | `WAITING_INGREDIENT_CONFIRM` |
| `WAITING_INGREDIENT_CONFIRM`, `confirmed`·조건 있음 | `COMPLETED` | `COMPLETED` |
| `WAITING_INGREDIENT_CONFIRM`, `confirmed`·조건 없음 | `CONDITION_INPUT` | `WAITING_CONDITIONS` |
| `WAITING_INGREDIENT_CONFIRM`, `edited` | `INGREDIENT_CONFIRM` | `WAITING_INGREDIENT_CONFIRM` |
| `WAITING_INGREDIENT_CONFIRM`, `rejected` | `IMAGE_INPUT` | `WAITING_IMAGE` |
| `WAITING_INGREDIENT_CONFIRM`, `unclear` | `INGREDIENT_CONFIRM` | `WAITING_INGREDIENT_CONFIRM` |
| `WAITING_CONDITIONS`, 조건 부족 | `CONDITION_INPUT` | `WAITING_CONDITIONS` |
| `WAITING_CONDITIONS`, 조건 충족·확정 재료 존재 | `COMPLETED` | `COMPLETED` |
| `COMPLETED` | `COMPLETED` | `COMPLETED` |

세션 상태는 `ChatSessionRepository` 뒤의 프로세스 메모리에 저장된다. 현재는 단계뿐 아니라
재료 후보, 확정 재료, 충분성이 판별된 자연어 사용자 조건 원문을 보관할 수 있다. 서버를 재시작하면 상태가 초기화되고,
멀티 인스턴스 배포 시에는 같은 인터페이스의 Redis·DB 구현체로 교체해야 한다.

## 4. 완료 응답의 데이터 구성

`COMPLETED`에서는 `app/agent/graph.py`의 Tool Hub node가 확정 재료와 사용자 조건으로
`ToolRequest`를 만들고 `PlanEatToolHub`를 실행한다. Tool 결과는 `ChatService`가
`RecommendationData`로 다시 검증한 뒤 FE 계약에 맞는 2세트(세트당 5개)로 변환한다.
`TOOL_HUB_ENABLED=false`이거나 테스트에서 provider를 주입하지 않은 경우에만
`OpenAIResponder.generate_recommendation()`의 Structured Outputs 임시 경로를 사용한다.

```json
{
  "status": "SUCCESS",
  "step": "COMPLETED",
  "response": "확정한 재료와 조건에 맞는 메인·반찬 식단을 추천했습니다. 두 세트 중 하나를 선택해 주세요. 선택한 레시피의 상세 PDF를 생성해드릴게요.",
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

`data.recipe_sets`는 기본 Tool Hub 또는 `TOOL_HUB_ENABLED=false`일 때의 fallback LLM이 생성한다.
`response`는 두 세트 중 하나를 선택하고 상세 PDF를 발급받도록 안내하는 서버 고정 문구다.
Tool Hub 또는 fallback LLM의 결과는 `RecommendationData` 검증과 안전성 검사를 모두 통과한 경우에만
반환하며, Tool Hub 실행·검증에 실패하면 `500 ERROR`를 반환한다.

초기 완료 응답은 `next_action=FEEDBACK_OR_SET_SELECTION`과 `available_set_ids`를 포함하고,
두 세트 중 하나를 선택하면 상세 PDF를 생성한다는 안내 문구를 반환한다.
피드백은 Jev Plan과 조건 병합을 거쳐 같은 Tool·DTO·Output Guardrail 경로로 재추천한다.
세트 선택이 감지되면 검증된 한 세트만 PDF 생성기에 전달하고, 저장 성공 후
`next_action=PDF_READY`, `selected_set_id`, `pdf_url`을 반환한다.

기본 실행 환경의 추천 source metadata는 `internal:recipe-catalog`이며, 프로젝트 내부 CSV에서
최대 10개 후보를 조회한 뒤 결정적인 점수·조합 규칙으로 결과를 만든다. 외부 Recipe API나
`mocks/chat/response-success.json`을 런타임에서 사용하지 않는다. fixture는 FE 예시와 테스트에서만
사용한다.

## 5. TypeSafe Jev 자연어 판정

Jev는 사용자 메시지의 식단 조건 충분성과 재료 확인 의도를 판단한다. 외부 `/chat` 요청에는
구조화된 조건·확인 필드가 없으며, 충분하다고 판정된 메시지 원문과 사용자 확인 결과만
세션의 내부 Tool 입력으로 보관한다.
자세한 설치와 환경변수는 [Jev 연동 가이드](../integrations/jev.md)를 참고한다.

이미지 요청 이후 사용자가 재료를 자연어로 직접 입력하는 경우에는 Jev의 이미지 후보 확인
판정을 거치지 않는다. `OpenAIResponder.extract_ingredients()`로 지원 재료를 사전 없이
추출하고, 사용자가 명시한 값이므로 `confirmed_ingredients`로 저장한다. 이 메서드는 Tool Hub의
Vision·재료 정규화 결과가 준비되면 해당 결과 어댑터로 교체한다.

### 5.1 조건 충분성 판정

1. `ChatService`가 자연어 `message`를 Jev의 고정된 `choice` 질문으로 평가한다.
2. `ready`가 confidence 기준 이상이면 메시지 원문을 조건으로 보관한다.
3. `needs_more_info`가 confidence 기준 이상이면 조건 입력을 반복한다.
4. API 실패, timeout, 응답 구조 오류, confidence 미달은 목표 키워드와 시간 표현을 함께 확인하는 보수적 fallback으로 처리한다.

### 5.2 재료 확인 의도 판정

재료 후보를 반환한 다음 요청에서는 `ingredient_confirmation` choice를 사용한다.

| 결과 | 세션·Graph 처리 |
|---|---|
| `confirmed` | `ingredient_candidates`를 비우고 `confirmed_ingredients`로 이동한다. 조건이 있으면 ToolRequest를 생성한다. |
| `edited` | 제한적인 추가·삭제·수량 변경 parser를 적용하고 `INGREDIENT_CONFIRM`을 다시 반환한다. |
| `rejected` | 후보·확정 재료를 비우고 `IMAGE_INPUT`으로 재촬영을 요청한다. |
| `unclear` | 후보를 유지하고 `INGREDIENT_CONFIRM` 재확인을 요청한다. |

Jev가 비활성화·실패·저신뢰이면 `ChatService`의 확인 표현 fallback을 사용한다. `edited` 요청의
복잡한 식재료명·수량 변경은 OpenAI 구조화 추출기가 반영하며, 수량이 없는 재료는 `수량 미정`으로
보정한다. 이미지 후보는 사용자 확인 전까지 Tool 입력에 포함하지 않는다.

외부 API 호출은 세션 잠금 밖에서 수행해, 한 세션의 네트워크 지연이 다른 세션의 처리를
막지 않게 한다. API 키·사용자 메시지 원문은 애플리케이션 로그에 기록하지 않는다.

## 6. 안전 처리

### 사용자 입력

`ChatService.handle()`은 Graph 실행 전에 `validate_user_message()`를 호출한다. 위험한 패턴이
정규화 후 탐지되면 `400 ERROR`를 반환하며 상태 전이를 진행하지 않는다.

### OpenAI 프롬프트

운영 프롬프트는 `prompts/`에서만 조합한다.

- `instructions`: 보안·언어·완료 단계 지시만 포함
- `input`: 사용자 메시지·확정 재료·조건·기존 추천 데이터를 출처별 비신뢰/신뢰 블록으로 분리
- `store=False`: OpenAI 요청 저장을 비활성화
- 출력: 최종 응답으로 보내기 전에 `validate_completion_output()`으로 재검사
- NeMo `regex check output`: 최종 응답을 FE에 반환하기 전 내부 지시·API 키 패턴을 추가 검사

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
| `TOOL_HUB_ENABLED` | 완료 단계 Tool Hub node 사용 여부 | `false`이면 임시 LLM 추천 |
| `TOOL_HUB_CATALOG_PATH` | Tool Hub Recipe Tool 카탈로그 경로 | 프로젝트 내부 CSV 사용 |
| `CHROMA_PERSIST_DIRECTORY` | 선택적 Recipe Guide RAG 저장 경로 | 빈 RAG retriever 사용 |
| `CHROMA_COLLECTION_NAME` | Chroma Recipe Guide collection | `recipe_guides` |
| `OPENAI_API_KEY` | 재료 추출·추가 안내 및 Tool Hub 비활성화 시 임시 추천 | 완료 단계에서 `500 ERROR` |
| `OPENAI_MODEL` | OpenAI 모델 선택 | `gpt-4o-mini` 사용 |
| `TYPESAFE_JEV_ENABLED` | Jev 자연어 조건·재료 확인 판정 사용 여부 | `false`가 기본값 |
| `TYPESAFE_API_KEY` | Jev 인증 | Jev를 호출하지 않고 fallback |
| `TYPESAFE_MODEL` | Jev 모델 선택 | `jev-latest` 사용 |
| `TYPESAFE_TIMEOUT_SECONDS` | Jev 호출 제한 시간 | `2.0`초 |
| `TYPESAFE_JEV_MIN_CONFIDENCE` | Jev 판단 반영 기준 | `0.8` |
| `NEMO_GUARDRAILS_ENABLED` | NeMo 입력·출력·Tool 결과 rail 사용 여부 | `true` |
| `NEMO_GUARDRAILS_CONFIG_PATH` | NeMo `config.yml` 디렉터리 | `guardrails` |
| `LANGSMITH_TRACING` | LangGraph 실행 trace 사용 여부 | `false` |
| `LANGSMITH_API_KEY` | LangSmith 인증 | tracing을 전송하지 않음 |
| `LANGSMITH_PROJECT` | trace 프로젝트 이름 | `planeat-backend` |
| `LANGSMITH_ENDPOINT` | LangSmith endpoint 또는 self-hosted 주소 | SDK 기본 endpoint |

실제 키는 `.env`에만 보관하고 Git에 추가하지 않는다.
LangSmith의 trace 범위·metadata·개인정보 보호 기준은 [LangSmith 연동 가이드](../integrations/langsmith.md)를 따른다.

## 8. 테스트와 fixture

| 위치 | 검증 대상 |
| --- | --- |
| `tests/unit/test_chat_schema.py` | 요청 DTO 제약, 응답 DTO, 모든 응답 fixture의 계약 적합성 |
| `tests/unit/test_chat_service.py` | 상태 전이, Jev 확인 의도, 후보 수정·확정, ToolRequest 전달, 안전 오류, OpenAPI 응답 코드 |
| `tests/unit/test_prompt_management.py` | 프롬프트 조합과 비신뢰 입력 분리 |
| `tests/unit/test_safety.py` | 입력·출력 안전성 검사 |
| `tests/unit/test_guardrails.py` | NeMo 입력·출력 rail과 ChatService 차단·fallback 경계 |
| `mocks/chat/` | FE가 사용할 상태별 요청·응답 예시 |

검증 명령은 다음과 같다.

```bash
PYTHONPATH=. uv run pytest
git diff --check
```

## 9. 현재 한계와 교체 지점

| 현재 임시 구현 | 향후 교체 지점 |
| --- | --- |
| `OpenAIResponder.extract_ingredients()`의 LLM 재료 추출 | Tool Hub Vision·재료 정규화 결과 |
| `OpenAIResponder.generate_clarification_response()`의 추가 입력 안내 | 상태별 FE 안내 정책 또는 향후 대화형 오케스트레이터 |
| `TOOL_HUB_ENABLED=false`의 LLM 임시 추천 | Tool Hub Recipe·Nutrition·Shopping·RAG 결과 |
| 프로세스 메모리 세션 | DB 또는 Redis 세션 저장소 |
| Jev 조건 판정과 제한적인 확인 의도 parser | 합의된 정책에 따른 조건 추출·재료 수정 DTO |
| 로컬 CSV·선택적 Chroma 기반 Tool Hub | 외부 Tool Hub URL·timeout·재시도 정책을 반영한 비동기 어댑터 |

Tool Hub·RAG가 연결되더라도 외부 `/chat` DTO, `status`, `step`, 레시피 2세트·세트당 5개라는
FE 계약은 유지해야 한다.
