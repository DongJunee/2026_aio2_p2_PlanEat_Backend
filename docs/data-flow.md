# PlanEat 데이터 흐름

## 역할과 경계

| 역할 | 담당자 | 책임 |
|---|---|---|
| FE | 이홍진 | Streamlit 화면, 사용자 입력 수집, `/chat` 응답 상태별 화면 전환 |
| BE1 | 최경락 | API Server, LangGraph Orchestrator, 세션 상태 관리, 워크플로우 분기 |
| BE2 | 박동준 | Tool Hub, RAG 검색, 도구 실행 결과 반환 |

`POST /chat`의 외부 계약은 [Chat API 명세](api/chat.md)를 따릅니다. BE1과 BE2 사이의 호출 경로와 세부 payload 형식은 아직 구현 전이므로, 아래의 BE2 인터페이스는 역할 기반의 논리적 데이터 흐름입니다.

## 1. 외부 대화 흐름: FE ↔ BE1

FE는 `POST /chat`만 호출하고, `status`와 `step`으로 다음 화면을 결정한다. BE1은 HTTP API,
세션 상태, LangGraph 전이와 응답 DTO 변환을 담당한다.

```mermaid
sequenceDiagram
    participant FE as FE Streamlit App
    participant BE1 as BE1 API Server and LangGraph

    FE->>BE1: POST /chat (이미지 없음)
    BE1-->>FE: 200 NEED_MORE_INFO / IMAGE_INPUT

    FE->>BE1: POST /chat (이미지 첨부)
    BE1->>BE1: 재료 후보를 세션 상태에 반영
    BE1-->>FE: 200 NEED_MORE_INFO / INGREDIENT_CONFIRM

    FE->>BE1: POST /chat (재료 확인 결과)
    BE1-->>FE: 200 NEED_MORE_INFO / CONDITION_INPUT

    FE->>BE1: POST /chat (식단·시간 조건)
    BE1->>BE1: 도구 결과를 세션 상태에 반영
    BE1-->>FE: 200 SUCCESS / COMPLETED
```

입력 형식 오류·안전성 검사 실패는 `400 ERROR`, 처리 실패는 `500 ERROR`로 반환한다. `ERROR`에는
`step`이 없다. BE1은 이미지를 직접 인식하지 않으며, 2절의 BE2 Vision 결과를
`INGREDIENT_CONFIRM` 응답으로 변환한다.

## 2. 내부 도구 흐름: BE1 ↔ BE2

BE1은 사용자에게 확인받기 전의 재료 후보를 추천·RAG 입력으로 보내지 않는다. BE2는 Vision과
Tool Hub 실행 결과만 반환하며, FE용 JSON으로 바꾸는 책임은 BE1에 있다.

```mermaid
sequenceDiagram
    participant BE1 as BE1 LangGraph Orchestrator
    participant BE2 as BE2 Tool Hub, Vision, RAG

    Note over BE1,BE2: 이미지 첨부 후
    BE1->>BE2: VisionRequest
    Note right of BE1: session_id, image input or image_ref
    BE2-->>BE1: ToolResult
    Note left of BE2: ingredient candidates, source metadata optional

    Note over BE1,BE2: FE가 재료와 조건을 확정한 후
    BE1->>BE2: ToolRequest
    Note right of BE1: session_id, tool_name, confirmed_ingredients, user_conditions
    BE2-->>BE1: ToolResult
    Note left of BE2: recipe, nutrition, shopping, RAG result or error
```

### BE1 → BE2 전달 원칙

| 항목 | 용도 |
|---|---|
| `session_id` | 도구 호출을 현재 대화와 연결 |
| `tool_name` | BE1이 실행할 도구 선택 |
| `confirmed_ingredients` | 사용자 확인을 마친 재료만 전달 |
| `user_conditions` | 식단 목표·조리 시간 등 추천 조건 전달 |

### Vision Function Call 입력 계약

이미지 인식은 재료가 확정되기 전 단계이므로, 일반 추천 ToolRequest와 구분한다. BE1은 이미지가
첨부됐을 때 BE2 Vision Function Call에 `session_id`와 이미지 입력을 전달하고, BE2는 재료 후보만
반환한다. 후보는 FE의 확인 전 추천·RAG 입력으로 사용할 수 없다.

현재 BE2 endpoint와 상세 DTO는 미합의 상태다. 실제 연동 전 아래 둘 중 하나를 팀에서 확정해야 한다.

| 선택지 | 설명 |
| --- | --- |
| Vision 전용 DTO | 예: `VisionRequest = { session_id, attachments }`처럼 이미지 인식 전용 요청을 별도로 둔다. |
| ToolRequest 확장 | 기존 ToolRequest에 `attachments` 또는 안전한 `image_ref` 필드를 추가한다. |

이미지 원문을 BE1으로 전달할지, 업로드 후 생성한 안전한 참조값만 전달할지도 함께 결정한다. URL을
그대로 외부 서비스에 전달할 경우 SSRF와 접근 제어 위험이 있으므로, BE2에서 허용 형식·크기·출처를
검증하는 정책이 필요하다.

이미지 인식으로 얻은 재료 후보는 FE의 사용자 확인 전에는 BE2의 추천·검색 입력으로 사용하지 않습니다. BE2의 `ToolResult`는 BE1 내부 워크플로우용 결과이며, BE1이 이를 `/chat` 외부 응답 형태로 변환합니다.

## 책임 경계 요약

```mermaid
flowchart LR
    FE[FE]
    BE1[BE1<br/>API · Session · LangGraph]
    BE2[BE2<br/>Vision · Tool Hub · RAG]

    FE <-->|ChatRequest · ChatResponse| BE1
    BE1 <-->|VisionRequest · ToolRequest · ToolResult| BE2
```
