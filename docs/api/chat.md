# Chat API

LangGraph 기반 AI Agent의 단일 진입점입니다. 사용자의 자연어와 최대 5장의 첨부 이미지를 받아 세션 상태에 따라 재료 확인, 추가 조건 질문, 레시피·영양·장보기 결과를 반환합니다.

## Endpoint

```text
POST /chat
Content-Type: application/json
```

## Request

| Field | Type | Required | Description |
|---|---|:---:|---|
| `session_id` | string | O | LangGraph 상태를 연결할 대화 세션 ID |
| `message` | string | O | 자연어 요청 또는 추가 질문에 대한 답변 |
| `attachments` | array | X | 이미지 첨부 목록, 최대 5개 |

```json
{
  "session_id": "session-001",
  "message": "냉장고 재료로 만들 수 있는 메뉴 추천해줘.",
  "attachments": [
    {
      "type": "image",
      "data": "<base64-image-or-url>"
    }
  ]
}
```

`attachments[].type`은 현재 `image`만 지원합니다. `data`는 Base64 이미지 또는 URL입니다.

## Response

### 추천 완료: `200 SUCCESS`

```json
{
  "status": "SUCCESS",
  "step": "COMPLETED",
  "response": "조건에 맞는 레시피 5개를 추천했습니다.",
  "data": {
    "recipes": [
      {
        "recipe_id": "R001",
        "title": "두부 양배추 볶음",
        "image": "https://example.com/image.jpg",
        "cook_time": 20,
        "owned_ingredients": ["두부", "양배추"],
        "missing_ingredients": [
          {
            "name": "간장",
            "importance": "필수",
            "alternative": null
          }
        ],
        "shopping_list": [
          {
            "ingredient": "간장",
            "amount": "1병",
            "estimated_price": 2500
          }
        ],
        "nutrition": {
          "calories": 430,
          "protein": 28,
          "carbohydrate": 18,
          "fat": 15
        }
      }
    ]
  }
}
```

### 추가 조건 필요: `200 NEED_MORE_INFO`

```json
{
  "status": "NEED_MORE_INFO",
  "step": "CONDITION_INPUT",
  "response": "추천을 위해 몇 가지 정보를 더 알려주세요.",
  "questions": [
    "식단 목표가 무엇인가요?",
    "조리 가능한 시간은 얼마나 되나요?",
    "예산은 얼마인가요?"
  ]
}
```

### 재료 확인 필요: `200 NEED_MORE_INFO`

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

### 오류: `500 ERROR`

```json
{
  "status": "ERROR",
  "response": "요청 처리 중 오류가 발생했습니다."
}
```

## 주요 필드

| Field | Description |
|---|---|
| `status` | `SUCCESS`, `NEED_MORE_INFO`, `ERROR` |
| `step` | `CONDITION_INPUT`, `INGREDIENT_CONFIRM`, `COMPLETED` 등 현재 Workflow 단계 |
| `response` | 사용자에게 표시할 자연어 응답 |
| `data.recipes` | 추천 레시피 목록 |
| `questions` | 추가로 필요한 조건 질문 |
| `ingredients` | 사용자 확인이 필요한 재료 목록 |

레시피의 `importance`는 `필수`, `구매 권장`, `대체 가능`, `생략 가능` 중 하나입니다. 영양 정보의 단위는 열량 `kcal`, 나머지 영양소 `g`입니다.

## 처리 원칙

- `session_id`로 LangGraph State를 유지합니다.
- Vision Tool이 이미지에서 재료 후보를 추출하고, 사용자가 확인한 재료만 추천에 사용합니다.
- Recipe Tool은 내부 레시피 DB를 조회합니다.
- Nutrition Tool은 레시피 영양 정보를 분석합니다.
- Shopping Tool은 부족 재료와 권장 수량을 생성합니다.
- ChromaDB는 재료 활용법, 대체재, 보관법 검색에 사용합니다.
- 새로운 Tool은 `/chat` 계약을 바꾸지 않고 LangGraph Workflow에 추가합니다.
