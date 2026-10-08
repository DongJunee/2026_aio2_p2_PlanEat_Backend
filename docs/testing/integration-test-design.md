# PlanEat Backend 통합테스트 설계서

> 최종 수정: 2026-10-08 (v1.0)

## 요약

- `/chat`의 다중 턴 상태와 세션 격리를 검증한다.
- 재료·목적 필수값, 이미지 확인, Tool Hub 추천을 검증한다.
- 추천 결과는 2세트 × 5레시피이며, 입력별 recipe ID와 CSV 출처를 비교한다.
- 피드백 재추천 후 세트 선택, PDF 생성·다운로드까지 검증한다.
- 가드레일·LangSmith 민감정보 비노출을 확인한다.

실행 명령: `$env:PYTHONPATH='.'; uv run pytest -q`

## 1. 목적

현재 구현된 FastAPI·LangGraph·세션·Tool Hub·PDF 흐름이 하나의 사용자 대화 시나리오에서
계약대로 연결되는지 검증한다. 단위 함수의 내부 동작보다 HTTP 요청부터 상태 전이, 추천 결과,
PDF 다운로드까지의 경계를 검증하는 것을 우선한다.

이 문서는 다음을 기준으로 작성한다.

- API: `POST /chat`, `GET /health`, `GET /pdfs/{filename}`
- 그래프 단계: `IMAGE_INPUT`, `INGREDIENT_CONFIRM`, `CONDITION_INPUT`, `COMPLETED`
- 필수 확인값: 확정 재료와 식단 목적
- 선택값: 조리 시간, 인분, 선호·기피 조건 등
- 기본 Tool Hub: 내부 CSV 카탈로그 기반 `PlanEatToolHub`
- 세션 저장소: 현재 프로세스 메모리 기반 저장소
- 추천 결과: `recipe_sets` 2개, 세트별 레시피 5개
- 완료 후 흐름: 최종 추천 → 피드백 또는 세트 선택 → 선택 세트 PDF 생성 → URL 반환

단위 테스트의 세부 함수 검증은 기존 `tests/unit/`에서 유지하고, 이 문서의 시나리오는
`tests/integration/`에 추가한다. 현재 `tests/unit/test_chat_service.py`에 있는 TestClient 기반
흐름 검증은 통합 테스트로 이동하거나 중복을 줄이는 방식으로 정리할 수 있다.

## 2. 테스트 범위

### 포함 범위

1. HTTP 요청·응답 스키마와 상태 코드
2. 동일 `session_id`의 다중 턴 상태 누적과 세션 격리
3. 자연어 재료 추출, 이미지 후보 확인·수정·거절
4. 재료·목적 필수값 정책과 선택 조건의 누적
5. LangGraph 단계 전이와 Tool Hub 실행 경계
6. 내부 CSV 카탈로그 기반 추천 결과의 비목업성·구조 유효성
7. 추천 피드백 재추천과 세트 선택 후 PDF 생성
8. 입력·Tool 결과·완료 응답 가드레일
9. LangSmith 추적 설정과 민감정보 비노출 계약

### 제외 범위

- 실제 OpenAI, Vision, NeMo, LangSmith SaaS 네트워크 호출을 기본 CI에 연결하는 테스트
- 운영 DB·Redis·S3 등 아직 연결되지 않은 영속 인프라의 성능·장애 시험
- 외부 Recipe API 또는 외부 RAG 품질 자체의 정확도 평가
- PDF의 요리 내용 자체에 대한 도메인 전문가 평가

외부 서비스가 정상적으로 연결된 환경에서는 별도 수동 스모크 테스트로 수행하며, 기본 통합
테스트는 주입된 fake provider와 로컬 내부 카탈로그로 재현 가능해야 한다.

## 3. 현재 구현 기준 흐름

```text
HTTP POST /chat
  → 입력·안전성 검증
  → session_id 상태 복원
  → LangGraph 단계 판단
  → 재료 후보 추출 또는 사용자 확인
  → 재료·목적 확인
  → Tool Hub 추천·영양·장보기 결과 조합
  → 결과 가드레일·응답 DTO 검증
  → COMPLETED 응답
       ├─ 피드백 → 조건 갱신 → 재추천·재검증
       └─ 세트 선택 → PDF 생성·저장 → pdf_url 반환
```

현재 기본 설정은 로컬 내부 CSV 카탈로그를 사용하는 Tool Hub 경로다. 외부 Tool Hub/RAG
서버를 호출하는 구현으로 간주하지 않으며, `source_metadata.recipe_sources`가
`internal:recipe-catalog`인지 확인해 목업 fixture와 실제 로컬 카탈로그를 구분한다.

추천 결과의 표시 문구는 고정된 계약 문구일 수 있지만, 실제 변동 여부는 다음 값을 비교해
검증한다.

```text
data.recipe_sets[*].recipes[*].recipe_id
data.recipe_sets[*].recipes[*].recipe_name
```

`mocks/chat/response-success.json`은 FE fixture와 계약 문서용이며 런타임 추천 데이터의
출처로 사용하지 않는다.

## 4. 테스트 환경과 fixture 원칙

### 4.1 공통 환경

| 항목 | 기준 |
| --- | --- |
| Python | 3.11 |
| 실행 | `uv run` |
| API client | FastAPI `TestClient` 또는 `httpx` |
| 앱 | 실제 `app.main:app` |
| 세션 | 테스트마다 새로운 `session_id` 또는 repository 초기화 |
| PDF 저장소 | 테스트별 `tmp_path` |
| 외부 네트워크 | 기본 차단 |
| 비밀값 | `.env`·실제 API 키 사용 금지 |

### 4.2 주입할 provider

실제 애플리케이션 조립과 라우터는 유지하고, 외부 I/O만 테스트용 provider로 교체한다.

- 구조화 LLM/응답 provider: 고정된 정상·실패 응답을 반환하는 fake
- 이미지 재료 추출 provider: 후보 재료와 신뢰도 반환
- 입력·출력 가드레일 provider: 허용·차단·실패 케이스를 명시적으로 반환
- Jev 피드백 planner: 재료·목적을 삭제하지 않고 피드백 slot만 반환하는 fake
- PDF 저장 디렉터리: `tmp_path`
- LangSmith: 기본 테스트에서는 trace collector 또는 비활성 설정

Tool Hub 자체는 가능한 경우 로컬 CSV 카탈로그를 사용한다. 결과가 테스트용 fake라면
`source_metadata`에 명시하고, 응답 구조만 검증하는 테스트와 실제 내부 카탈로그 결과를
검증하는 테스트를 분리한다.

### 4.3 테스트 데이터

공통 재료·목적 데이터는 다음처럼 최소화한다.

```json
{
  "ingredients": [
    {"name": "두부", "amount": "1모"},
    {"name": "계란", "amount": "2개"}
  ],
  "purpose": "다이어트"
}
```

- 수량이 없는 입력도 허용하며 내부적으로 `수량 미정`으로 처리되는지 확인한다.
- 목적만 보내는 경우 이전 턴의 확정 재료가 유지되는지 확인한다.
- 조리 시간은 저장할 수 있지만 필수 질문으로 재요청하지 않는지 확인한다.
- 이미지 data는 작은 테스트용 base64 값만 사용하고 실제 사용자 이미지는 커밋하지 않는다.

## 5. 통합 테스트 시나리오

### 5.1 API·기본 계약

| ID | 시나리오 | 검증 내용 |
| --- | --- | --- |
| IT-API-001 | health 확인 | `GET /health`가 200과 현재 헬스 응답을 반환한다. |
| IT-API-002 | 정상 요청 스키마 | `session_id`, `message`가 있으면 요청이 처리되고 응답이 `ChatResponse` 구조를 따른다. |
| IT-API-003 | 잘못된 요청 | 빈 `session_id`, 빈 메시지, 잘못된 attachment가 4xx로 반환되며 내부 예외가 노출되지 않는다. |
| IT-API-004 | OpenAPI 계약 | `/chat`의 request·response schema, `/health`, `/pdfs/{filename}`가 `/openapi.json`에 노출된다. |
| IT-API-005 | 오류 응답 규칙 | `ERROR` 응답에는 `step`을 넣지 않고, 오류 메시지에 stack trace나 비밀값이 없다. |

### 5.2 재료·목적 수집과 상태 전이

| ID | 시나리오 | 요청 순서 | 기대 결과 |
| --- | --- | --- | --- |
| IT-FLOW-001 | 정보 부족으로 시작 | `레시피를 만들어줘` | `NEED_MORE_INFO`, `IMAGE_INPUT`; 재료 입력을 요청한다. |
| IT-FLOW-002 | 재료만 자연어 입력 | `두부와 계란이 있어` | `NEED_MORE_INFO`, `CONDITION_INPUT`; 확정 재료는 유지하고 목적만 묻는다. |
| IT-FLOW-003 | 재료·목적 동시 입력 | `두부와 계란으로 다이어트 식단을 만들어줘` | Tool 경로를 거쳐 `SUCCESS`, `COMPLETED`; 시간 질문은 하지 않는다. |
| IT-FLOW-004 | 다중 턴 누적 | 재료 입력 → 목적 입력 | 두 번째 응답에서 첫 턴 재료가 유지되고 추천 입력에 포함된다. |
| IT-FLOW-005 | 선택 조건 입력 | 목적 입력 → `15분 내로 해줘` | 목적을 잃지 않고 시간은 `user_conditions`에 누적한다. 목적 재질문은 하지 않는다. |
| IT-FLOW-006 | 수량 없는 재료 | `현미밥, 설렁탕, 냉동만두, 스팸이 있어` | 수량을 필수 질문으로 만들지 않으며 필요 시 `수량 미정`으로 처리한다. |
| IT-FLOW-007 | 세션 격리 | `session_a`와 `session_b`에 서로 다른 재료 입력 | 한 세션의 재료·목적·추천·피드백이 다른 세션에 섞이지 않는다. |
| IT-FLOW-008 | 10턴 이상 대화 | 동일 세션에서 10회 이상 입력 | 최근 메시지 요약 정책이 작동하더라도 확정 재료·목적·추천 상태가 보존된다. |
| IT-FLOW-009 | 완료 후 재료 교체 | 같은 세션에서 새 재료로 변경 요청 | 기존 재료 대신 새 재료를 Tool Hub에 전달하고 재추천한다. |

### 5.3 이미지 확인 흐름

| ID | 시나리오 | 요청 순서 | 기대 결과 |
| --- | --- | --- | --- |
| IT-IMAGE-001 | 이미지 후보 추출 | image attachment 전송 | `INGREDIENT_CONFIRM`, `ingredient_candidates`가 반환된다. |
| IT-IMAGE-002 | 후보 확인 | 후보를 확인하는 메시지 전송 | 후보가 `confirmed_ingredients`로 이동하고, 목적이 있으면 Tool 단계로 진행한다. |
| IT-IMAGE-003 | 후보 수정 | 후보 중 하나를 수정하는 메시지 전송 | 수정된 후보만 반영하고 다시 확인 단계 또는 다음 필수 단계로 이동한다. |
| IT-IMAGE-004 | 후보 거절 | 이미지 재료를 사용할 수 없다고 응답 | 후보를 폐기하고 `IMAGE_INPUT`에서 새 재료 입력을 요청한다. |
| IT-IMAGE-005 | 확인 전 Tool 차단 | 후보 추출 직후 Tool 호출 여부 검사 | 사용자 확인 전 후보가 `confirmed_ingredients` 또는 Tool 요청에 들어가지 않는다. |

### 5.4 Tool Hub·추천 결과

| ID | 시나리오 | 검증 내용 |
| --- | --- | --- |
| IT-TOOL-001 | 정상 로컬 카탈로그 | Tool Hub 결과가 `recipe_sets` 2개, 각 5개 레시피를 만들며 DTO 검증을 통과한다. |
| IT-TOOL-002 | 출처 확인 | 결과의 `source_metadata.recipe_sources`에 내부 CSV 출처가 표시된다. |
| IT-TOOL-003 | 입력별 결과 차이 | 서로 다른 재료 입력에서 recipe id/name 집합이 동일하게 고정되지 않는다. |
| IT-TOOL-004 | 영양·장보기 조합 | 추천 결과에 필요한 nutrition·shopping 데이터가 포함되고, shopping item은 `ingredient`, `amount`만 가진다. |
| IT-TOOL-005 | Tool 실패 fallback | Tool 오류 시 설정된 fallback provider가 응답을 생성하고, 노출 가능한 오류는 안전한 계약 메시지로 제한된다. |
| IT-TOOL-006 | Tool 비활성 | `TOOL_HUB_ENABLED=false`에서 Tool 호출 없이 fallback 경로가 실행된다. |
| IT-TOOL-007 | 결과 가드레일 실패 | 잘못된 Tool 결과가 응답으로 노출되지 않고 `ERROR` 또는 정의된 재생성 경로로 처리된다. 현재 구현 기준 기대값은 오류 계약이며, 재생성 정책 도입 시 이 케이스의 기대값을 갱신한다. |

### 5.5 완료·피드백·PDF

| ID | 시나리오 | 요청 순서 | 기대 결과 |
| --- | --- | --- | --- |
| IT-RESULT-001 | 완료 응답 | 재료·목적이 모두 확인된 상태 | `SUCCESS`, `COMPLETED`, canonical response 문구, `next_action=FEEDBACK_OR_SET_SELECTION`을 반환한다. |
| IT-RESULT-002 | 세트 구조 | 완료 응답 검사 | `recipe_sets`가 2개이고 각 세트의 레시피가 5개다. `available_set_ids`가 세트와 일치한다. |
| IT-RESULT-003 | 피드백 재추천 | 완료 후 `조금 더 저칼로리로 바꿔줘` | 피드백이 조건에 병합되고 재추천된다. 기존 재료·목적은 보존된다. |
| IT-RESULT-004 | 피드백 반복 | 여러 차례 피드백 전송 | `feedback_history`는 최근 10개 정책을 따르고 추천 결과가 다시 가드레일을 통과한다. |
| IT-RESULT-005 | 세트 선택 | `1번 세트 선택` 또는 `SET001 선택` | 선택 세트가 재검증된 뒤 PDF를 생성하고 `next_action=PDF_READY`, `selected_set_id`, `pdf_url`을 반환한다. |
| IT-RESULT-006 | PDF 다운로드 | 반환된 `pdf_url` GET | 200, `application/pdf`, 저장된 파일과 일치하는 본문을 반환한다. |
| IT-RESULT-007 | PDF 파일 구조 | 생성 PDF 검사 | 안전한 UUID 파일명이고, 5개 레시피 페이지가 포함된다. |
| IT-RESULT-008 | 잘못된 세트 선택 | 존재하지 않는 세트 id 전송 | PDF를 만들지 않고 유효한 세트 선택을 요청하는 오류/추가 정보 응답을 반환한다. |
| IT-RESULT-009 | PDF 경로 안전성 | `../`, 절대 경로, 잘못된 확장자 요청 | 파일 시스템 외부 접근 없이 404 또는 안전한 4xx를 반환한다. |

### 5.6 안전성·관측성

| ID | 시나리오 | 검증 내용 |
| --- | --- | --- |
| IT-SAFE-001 | 입력 유해성 | 정책 위반 입력 | 400 `ERROR`; 사용자 메시지 원문과 내부 정책 정보가 불필요하게 노출되지 않는다. |
| IT-SAFE-002 | 출력 유해성 | 유해한 추천 응답을 fake provider가 반환 | 허용되지 않은 결과가 사용자에게 전달되지 않는다. |
| IT-SAFE-003 | 민감정보 로그 | 메시지·이미지·API key가 포함된 요청 | 로그와 trace metadata에 원문·base64·키가 남지 않는다. |
| IT-OBS-001 | LangSmith 비활성 | tracing 미설정 | API 기능은 정상 동작하고 외부 tracing 호출을 요구하지 않는다. |
| IT-OBS-002 | LangSmith 활성 설정 | tracing/API key/project 설정을 주입 | root `planeat.chat`과 Tool child run 이름·비민감 metadata가 예상대로 생성된다. |
| IT-OBS-003 | trace 메타데이터 | trace 수집 fake 검사 | session id는 hash 형태이고 `workflow_step`, `has_image`, `tool_enabled` 등 허용 필드만 포함된다. |

## 6. 대표 시나리오 상세

### 6.1 다중 턴 재료·목적 보존

```text
Given  새로운 session_id와 빈 메모리 세션 저장소
When   POST /chat {"message":"현미밥, 설렁탕, 냉동만두, 스팸이 있어"}
Then   NEED_MORE_INFO / CONDITION_INPUT
       확정 재료 4개, 목적 미확인

When   같은 session_id로 {"message":"다이어트 목표야"}
Then   SUCCESS / COMPLETED 또는 추천 실행에 필요한 정상 단계
       Tool 요청의 confirmed_ingredients에 앞선 4개 재료가 존재
       user_conditions.purpose가 "다이어트"
       조리 시간 질문 없음
```

이 케이스는 Swagger 화면의 대화 보존 여부가 아니라 서버의 `session_id` 기반 상태 저장과
다음 요청 복원이 정상인지 검증한다.

### 6.2 피드백 후 세트 선택과 PDF

```text
Given  재료와 목적이 확정되어 COMPLETED 응답을 받은 세션
When   같은 session_id로 식단 수정 피드백 전송
Then   기존 재료·목적을 유지한 새 recipe_sets 2개 반환

When   같은 session_id로 "SET001 선택" 전송
Then   PDF_READY, selected_set_id=SET001, pdf_url 반환

When   pdf_url GET
Then   PDF content-type과 파일 내용이 정상이고, 서버 저장 파일이 존재
```

### 6.3 목업 데이터 오인 방지

동일한 고정 response 문자열만 비교하지 않는다. 최소 두 종류의 재료 입력을 사용해
`recipe_id` 집합을 비교하고, Tool provider 결과의 출처 metadata를 함께 검사한다.
테스트 카탈로그를 사용하는 경우에는 fixture 출처를 명시하고, 내부 CSV 검증 테스트와
분리한다.

## 7. 구현 권장 구조

```text
tests/
├── integration/
│   ├── conftest.py                 # app, client, session, temp pdf, fake provider
│   ├── test_chat_api_flow.py       # HTTP와 상태 전이
│   ├── test_tool_hub_flow.py       # 로컬 카탈로그·fallback·결과 DTO
│   ├── test_feedback_pdf_flow.py   # 피드백·선택·PDF
│   ├── test_safety_flow.py         # 입력·출력 가드레일
│   └── test_observability_flow.py  # LangSmith 설정·metadata
└── unit/                           # 기존 단위 테스트 유지
```

fixture는 다음 계층으로 나눈다.

1. `app` fixture: 실제 FastAPI app과 라우터
2. `client` fixture: TestClient
3. `isolated_session` fixture: 테스트별 session id와 메모리 저장소
4. `fake_external_providers`: LLM·Vision·Jev·가드레일 주입
5. `local_catalog`: 실제 내부 CSV 또는 작은 명시적 테스트 CSV
6. `pdf_storage`: `tmp_path` 기반 임시 경로

테스트 간 전역 `chat_service`의 provider와 저장소가 남지 않도록 fixture teardown에서 원래
구성을 복구한다.

## 8. 실행 계획

개별 통합 테스트:

```powershell
$env:PYTHONPATH='.'
uv run pytest tests/integration -q
```

전체 테스트 및 계약 검사:

```powershell
$env:PYTHONPATH='.'
uv run pytest -q
git diff --check
```

선택적 LangSmith 스모크 테스트는 API key가 있는 로컬 환경에서만 별도 marker로 실행한다.
기본 CI 명령에는 포함하지 않으며, 실제 사용자 메시지·이미지·API key를 trace에 보내지 않는지
먼저 확인한다.

## 9. 합격 기준

- IT-API, IT-FLOW, IT-RESULT의 필수 정상 흐름이 모두 통과한다.
- 같은 `session_id`의 재료·목적·피드백이 다음 턴과 Tool 요청에 보존된다.
- 다른 `session_id`의 상태가 섞이지 않는다.
- 사용자 확인 전 이미지 후보가 Tool 입력에 사용되지 않는다.
- 완료 응답은 2세트 × 5레시피 계약을 만족한다.
- 서로 다른 재료 입력에서 결과가 항상 동일한 고정 mock으로 나오지 않는다.
- 세트 선택 후 실제 PDF 파일이 생성되고 URL GET이 성공한다.
- PDF 경로 탈출과 유효하지 않은 세트 선택이 차단된다.
- 가드레일 오류가 안전한 API 오류 또는 합의된 재생성 응답으로 처리된다.
- 기본 통합 테스트는 외부 네트워크와 실 API key 없이 재현된다.
- `PYTHONPATH=. uv run pytest`와 `git diff --check`가 통과한다.

## 10. 현재 한계와 후속 보완

1. 세션 저장소가 프로세스 메모리이므로 다중 worker·재시작·다중 인스턴스 환경의 상태
   보존은 별도 Redis/DB 통합 테스트가 필요하다.
2. 외부 Tool Hub·Vector Store·실제 LangSmith 전송은 기본 통합 테스트 범위가 아니다.
3. 현재 출력 가드레일 실패의 기본 기대값은 안전한 오류 계약이다. 출력 재생성 정책을
   런타임에 도입하면 재생성 횟수, 실패 시 최종 fallback, trace metadata를 이 설계서와
   테스트에 함께 반영해야 한다.
4. PDF 저장소가 로컬 파일 시스템인 동안에는 파일 존재·다운로드·경로 안전성까지 검증하고,
   object storage 전환 시 signed URL 만료·권한·삭제 정책을 추가한다.

