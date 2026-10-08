# Tool Hub Meal Planning 결과 계약

Tool Hub의 실제 추천 흐름은 `MealPlanningSubgraph`다. Vision(OpenAI) 결과가 사용자에게
확인된 뒤, 다음 순서로 실행한다.

```text
Recipe 후보 검색 → 메인·반찬 조합 → Jev 선택 → 세트별 Shopping(최대 5개) → Nutrition
```

정식 진입점은 `PlanEatToolHub.execute_meal_plan(MealPlanningRequest)`이며, 정확히 5개
세트를 반환한다. 각 세트는 `main_recipe` 하나와 `side_recipe` 하나를 가진다.
`excluded_ingredients`는 OpenAI가 사용자 자연어에서 구조화해 전달하며, Jev가
`생략 가능`·`대체 가능`·`필수`로 판단한다. 전자의 두 경우 Shopping만 바꾸고,
필수 재료이거나 대체재가 없으면 Recipe와 Shopping을 재계획한다.

`PlanEatToolHub.execute(ToolRequest)`는 Orchestrator의 현재 계약을 위해서만 남긴 호환
adapter다. 이 adapter는 위의 정식 5세트 결과를 기존 2세트×5레시피 카드 DTO로 변환하며,
별도 추천 흐름을 실행하지 않는다.

기본 앱의 호환 adapter는 `data/COOKRCP01_FINAL_WITH_INGREDIENT_GROUPS_REVISED_V2.csv`를 읽는
로컬 Recipe Source를 사용한다. 결과의 `source_metadata.recipe_sources`에는
`internal:recipe-catalog`이 기록되며, 외부 Recipe API나 `mocks/chat/` fixture는 런타임 추천에
사용하지 않는다. 동일 입력에 대한 검색·정렬은 결정적이다.

```json
{
  "result": {
    "response": "사용자에게 보일 완료 안내 문구",
    "data": { "recipe_sets": ["정확히 두 세트, 세트당 다섯 레시피"] }
  },
  "source_metadata": {
    "recipe_sources": ["..."],
    "recipe_guide_sources": ["..."],
    "recipe_count": 10
  },
  "error": null
}
```

`result.data`는 기존 `app.schemas.chat.RecommendationData`로 바로 검증 가능하다.
통합 시 Orchestrator는 `ToolResult.error`가 없을 때 `result.response`와 `result.data`를 사용하고,
실패 시 기존 오류 정책을 적용한다. Orchestrator는 이 결과를 기존 `ChatResponse`로 변환하며,
외부 Chat API의 경로와 DTO는 바꾸지 않는다.

Vision은 추천 ToolRequest와 분리한다. `OpenAIVisionIngredientExtractor.extract()`의 결과는
사용자 확인 전 후보이므로 Recipe·Nutrition·Shopping·RAG 실행에 전달하면 안 된다.

## LangChain Tool Calling·LangGraph 노드 준비

Tool Hub는 Orchestrator의 `app/agent/graph.py` 완료 단계에 연결되며, 기존 `ChatService`와
`/chat` DTO를 유지한 채 아래의 확장 지점을 제공한다.

| 목적 | Tool Hub 진입점 | 입출력 |
| --- | --- | --- |
| LLM Function Calling | `create_tool_hub_tools(tool_hub)` | 호환용 `recipe_recommendation`과 정식 `meal_planning` Tool 반환 |
| 표준 LangChain ToolNode | `build_tool_hub_call_node(tool_hub)` | `messages: list[BaseMessage]` 상태에서 두 ToolCall 처리 |
| 기존 Orchestrator 상태용 직접 노드 | `build_recipe_recommendation_node(tool_hub)` | 기존 2×5 호환 결과를 `tool_*` 키에 반환 |
| 새 식단 상태용 직접 노드 | `build_meal_planning_node(tool_hub)` | 5개 메인·반찬 세트를 `meal_plan_*` 키에 반환 |

모든 ToolCall은 다음 값만 받는다. 이미지 Vision 후보가 아니라 사용자 확인이 끝난 재료만
`confirmed_ingredients`에 넣어야 한다.

```json
{
  "session_id": "session-123",
  "confirmed_ingredients": [{"name": "두부", "amount": "1모"}],
  "user_conditions": {"message": "다이어트 식단으로 30분 안에 만들고 싶어요."}
}
```

ToolCall의 JSON 결과는 `{ ok, result, source_metadata, error }`다. `ok: true`일 때
`result.data`는 기존 `RecommendationData`와 호환된다.

정식 `meal_planning` ToolCall은 아래처럼 `excluded_ingredients`를 추가로 받으며,
`result.recipe_sets`는 정확히 5개다.

```json
{
  "session_id": "session-123",
  "confirmed_ingredients": [{"name": "두부", "amount": "1모"}],
  "user_conditions": {"message": "고단백 식단을 20분 안에 만들고 싶어요."},
  "excluded_ingredients": ["간장"]
}
```

### 현재 Orchestrator StateGraph 연결

현재 Orchestrator의 `ChatState.messages`는 LangChain `BaseMessage`가 아닌 dict 메시지이므로,
기본 완료 흐름은 직접 호출 노드를 사용한다. 표준 `ToolNode`가 필요한 별도 메시지 상태에서는
다음처럼 연결할 수 있다.

```python
from app.agent.tools.nodes import (
    build_recipe_recommendation_node,
    route_after_recipe_recommendation,
)

graph.add_node("tool_hub_recipe_recommendation", build_recipe_recommendation_node(tool_hub))
graph.add_conditional_edges(
    "tool_hub_recipe_recommendation",
    route_after_recipe_recommendation,
    {
        "tool_succeeded": "response_node",
        "tool_failed": "tool_error_node",
    },
)
```

이 노드는 `session_id`, `confirmed_ingredients`, `user_conditions`를 읽고,
`tool_result`, `tool_source_metadata`, `tool_error`만 반환한다. 따라서 Orchestrator는
성공 경로에서 `tool_result["data"]`를 검증해 기존 `/chat` 응답으로 변환하면 된다.

표준 LLM Tool Calling 메시지 상태로 전환하는 경우에만
`build_tool_hub_call_node(tool_hub)`를 사용한다. 두 노드 모두 외부 Recipe API를 호출하지
않고 `PlanEatToolHub.from_local_catalog(...)`의 로컬 CSV 카탈로그를 사용할 수 있다.

### 새 Meal Planning 상태에 연결할 때

Orchestrator가 5개 메인·반찬 세트 DTO를 채택한 뒤에는 기존 노드 대신 아래 Tool Hub 노드를 연결한다.
이 노드는 OpenAI가 구조화 추출한 `excluded_ingredients`까지 받아 결과를 분리된 상태 키에
기록한다.

```python
from app.agent.tools.nodes import build_meal_planning_node, route_after_meal_planning

graph.add_node("tool_hub_meal_planning", build_meal_planning_node(tool_hub))
graph.add_conditional_edges(
    "tool_hub_meal_planning",
    route_after_meal_planning,
    {"tool_succeeded": "response_node", "tool_failed": "tool_error_node"},
)
```

기본 앱은 `TOOL_HUB_ENABLED=true`일 때 위의 호환용 `recipe_recommendation` node를
완료 단계에 연결한다. 설정을 `false`로 두거나 provider를 주입하지 않은 테스트에서는 기존
OpenAI 임시 추천 경로가 유지된다.

## Recipe Source 조립

- `PlanEatToolHub.from_local_catalog(...)`: 경로가 없으면 `data/`의 보강 내부 CSV를,
  경로가 있으면 지정된 개발·검증 CSV를 읽는다. 내부 CSV의 필수·대체·생략 재료 열은
  `RecipeIngredient.importance`로 보존되므로 Jev의 제외 재료 판정과 재계획이 데이터
  근거를 갖는다.
