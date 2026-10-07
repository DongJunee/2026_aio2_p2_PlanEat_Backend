# Chat API

LangGraph Agent의 단일 API입니다.

```text
POST /chat
Content-Type: application/json
```

## Request

| Field | Type | Required | Description |
|---|---|:---:|---|
| `session_id` | string | O | 대화 세션 ID |
| `message` | string | O | 자연어 요청 또는 추가 답변, 최대 2,000자 |
| `attachments` | array | X | 이미지 목록, 최대 5개. 생략하면 빈 목록으로 처리 |

```json
{
  "session_id": "session-001",
  "message": "냉장고 재료로 저녁 메뉴 추천해줘.",
  "attachments": [
    {
      "type": "image",
      "data": "<base64-image-or-url>"
    }
  ]
}
```

`attachments[].type`은 현재 `image`만 지원합니다.

`attachments`는 생략해도 됩니다. 이미지 없이 시작하거나 이미지 요청 후에도 사용자가
이미지 요청 후 `"두부 1모와 계란이 있어요"`처럼 재료를 `message`에 직접 입력하면, BE1은 지원하는
모델이 실제로 확인한 재료명만 구조화해 사용자 입력 확정 재료로 사용합니다. 이미지 인식 후보가 아니므로
별도 `INGREDIENT_CONFIRM` 단계 없이 조건이 준비된 경우 추천 단계로 진행합니다.

식단 목표와 조리 가능 시간은 별도 JSON 필드가 아닌 `message`의 자연어로 전달합니다. 예를 들어
`"다이어트 식단으로 20분 안에 만들고 싶어요."`처럼 이미지와 함께 보낼 수 있습니다. 서버는 Jev
판정과 보수적 fallback 규칙으로 조건이 충분한지 판단합니다. 이미지가 없는 첫 요청에는
`IMAGE_INPUT`으로 사진을 먼저 요청하고, 사용자가 사진이 없다고 다시 답하면 자연어 재료 입력을
안내합니다.

## Response

`status`와 `step`은 FE 분기 처리에 사용하는 고정 코드입니다. 사용자에게 표시하는 `response`와
추가 입력 `questions`는 현재 사용자 메시지와 세션에 부족한 정보를 반영해 LLM이 생성하며,
호출 실패·응답 검증 실패 시 단계별 고정 fallback을 사용합니다. FE는 `questions`·`ingredients`·`data`를 사용합니다.

### 상태 코드

| HTTP 상태 | `status` | 설명 |
|---:|---|---|
| 200 | `SUCCESS` | 레시피 추천이 완료되었습니다. |
| 200 | `NEED_MORE_INFO` | 다음 진행을 위해 사용자 입력 또는 확인이 필요합니다. |
| 400 | `ERROR` | 요청 형식이 올바르지 않거나, 정규화된 입력 검증에서 안전하지 않은 요청으로 판단되었습니다. |
| 500 | `ERROR` | 요청 처리 중 서버 오류가 발생했습니다. |

### 진행 단계 코드

`step`은 `status`가 `SUCCESS` 또는 `NEED_MORE_INFO`일 때 포함됩니다.

| `status` | `step` | FE 처리 |
|---|---|---|
| `SUCCESS` | `COMPLETED` | `data.recipe_sets`를 표시합니다. |
| `NEED_MORE_INFO` | `INPUT_REQUIREMENTS` | 이미지와 조건을 동시에 수집해야 하는 호환 응답입니다. |
| `NEED_MORE_INFO` | `IMAGE_INPUT` | 첫 요청에서는 이미지를 요청하고, 재요청에서는 자연어 재료 입력도 안내합니다. |
| `NEED_MORE_INFO` | `CONDITION_INPUT` | `questions`를 표시하고 추가 조건을 입력받습니다. |
| `NEED_MORE_INFO` | `INGREDIENT_CONFIRM` | `ingredients`를 표시하고 인식 재료를 확인받습니다. |

`ERROR` 응답에는 `step`을 포함하지 않습니다.

### 첫 이미지 요청: `200 NEED_MORE_INFO`

이미지가 없는 첫 요청에서는 사진을 먼저 요청합니다. 아래 문구는 대표 예시이며 실제 `response`와
`questions`는 LLM이 생성하고, 실패하면 고정 fallback을 사용합니다.

```json
{
  "status": "NEED_MORE_INFO",
  "step": "IMAGE_INPUT",
  "response": "정확한 재료 확인을 위해 냉장고 또는 영수증 이미지를 첨부해주세요.",
  "questions": [
    "냉장고, 냉동실 또는 영수증 이미지를 첨부해주세요."
  ]
}
```

### 사진 없이 자연어 재료 입력: `200 NEED_MORE_INFO`

이미지 요청 후에도 사진이 없으면 `message`에 보유 재료와 수량을 자연어로 입력합니다. 서버는
LLM으로 재료를 추출해 확정 재료로 저장하고, 식단 조건이 함께 있으면 추천을 진행합니다. 조건이
없으면 `CONDITION_INPUT`으로 식단 목표와 조리 시간을 추가로 요청합니다.

```json
{
  "status": "NEED_MORE_INFO",
  "step": "IMAGE_INPUT",
  "response": "사진이 없어도 괜찮아요. 냉장고에 있는 재료를 텍스트로 알려주세요.",
  "questions": [
    "사용 가능한 재료와 수량을 알려주세요.",
    "식단 목표는 무엇인가요? 예: 다이어트, 고단백, 채식",
    "조리 가능한 시간은 얼마나 되나요? 예: 20분 이내"
  ]
}
```

### Success: `200`

성공 응답은 `recipe_sets` 2개를 반환하며, 각 세트는 레시피 5개를 포함합니다.

```json
{
  "status": "SUCCESS",
  "step": "COMPLETED",
  "response": "조건에 맞는 레시피 2세트(세트당 5개)를 추천했습니다.",
  "data": {
    "recipe_sets": [
      {
        "set_id": "SET001",
        "recipes": [
          "Recipe 객체 5개"
        ]
      },
      {
        "set_id": "SET002",
        "recipes": [
          "Recipe 객체 5개"
        ]
      }
    ]
  }
}
```

FE에서 사용할 수 있는 전체 성공 응답은 [`mocks/chat/response-success.json`](../../mocks/chat/response-success.json)을 참고합니다.

### 추가 조건 필요: `200`

```json
{
  "status": "NEED_MORE_INFO",
  "step": "CONDITION_INPUT",
  "response": "추천을 위해 몇 가지 정보를 더 알려주세요.",
  "questions": [
    "식단 목표가 무엇인가요?",
    "조리 가능한 시간은 얼마나 되나요?"
  ]
}
```

### 재료 확인 필요: `200`

```json
{
  "status": "NEED_MORE_INFO",
  "step": "INGREDIENT_CONFIRM",
  "response": "AI가 인식한 재료를 확인해주세요.",
  "ingredients": [
    { "name": "두부", "amount": "1모" },
    { "name": "양배추", "amount": "반 통" },
    { "name": "계란", "amount": "4개" }
  ]
}
```

FE는 별도 확인 DTO를 보내지 않고 사용자의 자연어 답변을 `message`로 다시 전송합니다.
BE1은 Jev의 `choice` 판정으로 답변 의도를 분류합니다.

| 내부 판정 | 처리 | 외부 응답 |
|---|---|---|
| `confirmed` | 후보를 확정 재료로 이동하고, 조건이 있으면 ToolRequest를 준비합니다. | `COMPLETED` 또는 `CONDITION_INPUT` |
| `edited` | 추가·삭제·수량 변경을 후보에 반영하고 다시 확인받습니다. | `INGREDIENT_CONFIRM` |
| `rejected` | 후보를 폐기하고 이미지를 다시 요청합니다. | `IMAGE_INPUT` |
| `unclear` | 후보를 유지하고 확인 답변을 다시 요청합니다. | `INGREDIENT_CONFIRM` |

예를 들어 `"계란은 빼고 양파 1개 추가해줘"`는 `edited`로 분류되어 수정된 후보 목록이
다시 반환됩니다. `"네, 모두 맞아요"`는 `confirmed`로 분류됩니다.

재료가 확정되고 식단 조건도 준비되면 내부적으로 아래 요청을 생성합니다. 이 DTO는 FE에
노출되지 않으며, BE2 provider가 주입된 경우에만 실행합니다. BE2 provider가 없으면
확정 재료·조건을 OpenAI 추천 생성기에 직접 전달합니다.

```text
ToolRequest = {
  session_id,
  tool_name: "recipe_recommendation",
  confirmed_ingredients,
  user_conditions
}
```

### Error: `400` 또는 `500`

```json
{
  "status": "ERROR",
  "response": "요청 처리 중 오류가 발생했습니다."
}
```

요청 본문이 DTO 규칙에 맞지 않으면 HTTP `400`과 아래 형식을 반환합니다. FastAPI 기본
`422` 검증 오류 객체를 반환하지 않으므로, FE는 모든 오류를 같은 `ERROR` 형태로 처리할 수
있습니다.

서버는 `app/core/safety.py`의 정규화 검사와 NeMo Guardrails 입력 rail을 순서대로 적용합니다.
둘 중 하나가 프롬프트 인젝션·내부 정보 요청을 차단해도 같은 `400 ERROR` 형식을 반환합니다.
최종 LLM 문구와 ToolResult의 NeMo 출력 rail 검사가 실패하면 원문을 반환하지 않고 `500 ERROR`로
처리합니다. 이 검사는 외부 요청·응답 필드나 `status`·`step` 계약을 변경하지 않습니다.

```json
{
  "status": "ERROR",
  "response": "요청 형식이 올바르지 않습니다."
}
```

## Recipe Set 객체

| Field | Type | Description |
|---|---|---|
| `set_id` | string | 추천 세트 ID |
| `recipes` | Recipe[5] | 세트에 포함된 레시피 5개 |

## Recipe 객체

| Field | Type | Description |
|---|---|---|
| `recipe_id` | string | 레시피 ID |
| `title` | string | 레시피명 |
| `image` | string/null | 대표 이미지 |
| `cook_time` | number | 조리 시간(분) |
| `owned_ingredients` | string[] | 보유 재료 |
| `missing_ingredients` | object[] | 부족 재료 |
| `shopping_list` | object[] | 장보기 목록 |
| `nutrition` | object | 영양 정보 |

### missing_ingredients

```json
{
  "name": "간장",
  "importance": "필수",
  "alternative": null
}
```

`importance`는 `필수`, `권장`, `대체 가능`, `생략 가능` 중 하나입니다.

### shopping_list

```json
{
  "ingredient": "간장",
  "amount": "1병"
}
```

### nutrition

```json
{
  "calories": 430,
  "protein": 28,
  "carbohydrate": 18,
  "fat": 15
}
```

영양 정보 단위는 열량 `kcal`, 나머지 영양소 `g`입니다.

## 처리 규칙

- `session_id`로 LangGraph State를 유지합니다.
- 이미지가 없는 첫 요청은 `IMAGE_INPUT`으로 사진을 먼저 요청합니다. 같은 세션에서 사진이 없다고
  다시 답하면 자연어 재료 입력을 안내합니다.
- 이미지와 식단 목적·조리 시간이 담긴 자연어 메시지를 함께 받은 뒤에는 재료 확인만 거치고 추천 단계로 진행합니다.
- 이미지가 없더라도 사용자가 `message`에 직접 입력한 지원 재료는 확정 재료로 처리하며, 식단 조건이
  있으면 바로 추천하고 조건이 없으면 `CONDITION_INPUT`만 반환합니다.
- 사용자 확인 전 재료 후보는 추천 Tool에 전달하지 않습니다.
- `confirmed` 판정 이후에만 확정 재료를 ToolRequest에 포함합니다.
- `edited`·`rejected`·`unclear` 판정은 각각 후보 수정·이미지 재요청·재확인 응답으로 처리합니다.
- 이미지와 자연어 재료는 `OPENAI_MODEL`(기본값 `gpt-4o-mini`)의 구조화 추출 결과를 서버에서
  검증해 후보 또는 확정 재료로 저장합니다.
- `INPUT_REQUIREMENTS`·`IMAGE_INPUT`·`CONDITION_INPUT`의 `response`와 `questions`는
  `OpenAIResponder.generate_clarification_response()`의 Structured Outputs로 생성하며,
  모델 장애나 검증 실패 시 단계별 고정 fallback으로 응답합니다. 이 fallback은 FE의
  `status`·`step` 계약을 변경하지 않습니다.
- Tool Hub 연동 전에는 확정 재료·조건을 바탕으로 OpenAI가 생성한 임시 추천 결과를 반환합니다.
- `COMPLETED` 단계의 `response`와 `data.recipe_sets`는 OpenAI Structured Outputs로 생성하고,
  서버에서 `RecommendationData`로 다시 검증합니다.
- 재료 추출·최종 추천 생성에서 API 키가 없거나 LLM 호출에 실패하면 `500 ERROR`를 반환합니다.
  추가 입력 안내 생성만 실패한 경우에는 단계별 고정 fallback을 사용해 `200 NEED_MORE_INFO`를 유지합니다.
- 최종 추천 응답 프롬프트는 [`prompts/`](../../prompts/)의 공통·단계별 조각을 조합해 관리합니다.
- 프롬프트의 역할·출력·보안 지시는 `instructions`에, 사용자 메시지와 추천 데이터는 출처별 `input` 블록에 분리해 전달합니다.
- 재료 추출 LLM과 추천 생성 LLM에는 Tool을 제공하지 않습니다. 두 출력 모두 구조화 JSON으로
  제한하고 서버 DTO로 다시 검증합니다.
- Chat API는 NFKC·소문자·구분 문자 제거로 정규화한 입력을 검사하고, 통과한 사용자
  메시지도 비신뢰 데이터 블록으로만 LLM에 전달합니다. LLM 출력은 내부 지시·API 키 노출
  여부를 다시 검사합니다.
- Tool Hub 연동 후에는 OpenAI 임시 재료·추천 데이터를 Vision·Recipe·Nutrition·Shopping·RAG
  Tool 실행 결과로 대체합니다.
- 사용자 확인 전의 이미지 인식 결과는 추천에 사용하지 않습니다.
- Recipe Tool은 내부 레시피 DB를 조회합니다.
- Nutrition Tool은 영양 정보를 제공합니다.
- Shopping Tool은 부족 재료와 장보기 목록을 생성합니다.
- ChromaDB는 재료 활용법·대체재·보관법 검색에 사용합니다.
- 새로운 Tool은 `/chat` 계약을 바꾸지 않고 Workflow에 추가합니다.

## FE Mock

상태별 fixture는 [`mocks/chat/`](../../mocks/chat/)에 있습니다.
