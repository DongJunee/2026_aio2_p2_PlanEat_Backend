# PlanEat 데이터 흐름

## 역할과 경계

| 역할 | 담당자 | 책임 |
|---|---|---|
| FE | 이홍진 | Streamlit 화면, 사용자 입력 수집, `/chat` 응답 상태별 화면 전환 |
| BE1 | 최경락 | API Server, LangGraph Orchestrator, 세션 상태 관리, 워크플로우 분기 |
| BE2 | 박동준 | Tool Hub, RAG 검색, 도구 실행 결과 반환 |

`POST /chat`의 외부 계약은 [Chat API 명세](api/chat.md)를 따릅니다. BE2와 BE1 사이의 호출 경로와 세부 payload 형식은 아직 구현 전이므로, 아래의 BE1 인터페이스는 역할 기반의 논리적 데이터 흐름입니다.

## 1. FE → BE2: 사용자 대화 요청과 화면 응답

```mermaid
sequenceDiagram
    participant FE as FE Streamlit App
    participant BE2 as BE2 API Server and LangGraph

    FE->>BE2: POST /chat
    Note right of FE: session_id, message, attachments optional
    BE2->>BE2: session_id로 대화 상태 조회 및 워크플로우 분기

    alt 이미지가 필요함
        BE2-->>FE: 200 NEED_MORE_INFO and IMAGE_INPUT
        Note left of FE: response, questions
    else 인식 재료 확인이 필요함
        BE2-->>FE: 200 NEED_MORE_INFO and INGREDIENT_CONFIRM
        Note left of FE: response, ingredients
    else 추가 조건이 필요함
        BE2-->>FE: 200 NEED_MORE_INFO and CONDITION_INPUT
        Note left of FE: response, questions
    else 추천 완료
        BE2-->>FE: 200 SUCCESS and COMPLETED
        Note left of FE: response, data.recipe_sets
    else 처리 오류
        BE2-->>FE: 500 ERROR
        Note left of FE: response
    end
```

FE는 `status`와 `step`으로 화면 흐름을 결정하고, `response`, `questions`, `ingredients`, `data`는 표시 데이터로 사용합니다. `ERROR`에는 `step`이 없습니다.

## 2. BE2 → BE1: 도구 실행과 RAG 결과

```mermaid
sequenceDiagram
    participant BE2 as BE2 LangGraph Orchestrator
    participant BE1 as BE1 Tool Hub and RAG
    participant Store as Recipe DB and Vector Store

    BE2->>BE1: ToolRequest
    Note right of BE2: session_id, tool_name, confirmed_ingredients, user_conditions
    BE1->>BE1: tool_name에 따라 도구 실행 요청 검증 및 라우팅

    alt 레시피 또는 영양 조회
        BE1->>Store: 레시피와 영양 정보 조회
        Store-->>BE1: RecipeResult or NutritionResult
    else 재료 활용 RAG 검색
        BE1->>Store: 재료 활용법, 대체재, 보관법 검색
        Store-->>BE1: RAGContext
    else 장보기 목록 생성
        BE1-->>BE1: 부족 재료와 수량 정리
    end

    BE1-->>BE2: ToolResult
    Note left of BE1: result data, source metadata optional, error optional
    BE2->>BE2: 결과를 LangGraph State에 반영하고 다음 단계 결정
```

### BE2 → BE1 전달 원칙

| 항목 | 용도 |
|---|---|
| `session_id` | 도구 호출을 현재 대화와 연결 |
| `tool_name` | BE1이 실행할 도구 선택 |
| `confirmed_ingredients` | 사용자 확인을 마친 재료만 전달 |
| `user_conditions` | 식단 목표·조리 시간 등 추천 조건 전달 |

이미지 인식으로 얻은 재료 후보는 FE의 사용자 확인 전에는 BE1의 추천·검색 입력으로 사용하지 않습니다. BE1의 `ToolResult`는 BE2 내부 워크플로우용 결과이며, BE2가 이를 `/chat` 외부 응답 형태로 변환합니다.

## 흐름 요약

```mermaid
flowchart LR
    FE[FE Streamlit App] -->|ChatRequest| BE2[BE2 API Server and LangGraph]
    BE2 -->|ToolRequest| BE1[BE1 Tool Hub and RAG]
    BE1 -->|ToolResult| BE2
    BE2 -->|ChatResponse| FE
```
