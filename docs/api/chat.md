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
| `message` | string | O | 자연어 요청 또는 추가 답변 |
| `attachments` | array | X | 이미지 목록, 최대 5개 |

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

## Response

### 이미지 첨부 필요: `200 NEED_MORE_INFO`

이미지는 Request에서 선택값이지만, 재료 기반 추천을 시작하려면 이미지가 필요합니다.

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

### Error: `500`

```json
{
  "status": "ERROR",
  "response": "요청 처리 중 오류가 발생했습니다."
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
- 사용자 확인 전의 이미지 인식 결과는 추천에 사용하지 않습니다.
- Recipe Tool은 내부 레시피 DB를 조회합니다.
- Nutrition Tool은 영양 정보를 제공합니다.
- Shopping Tool은 부족 재료와 장보기 목록을 생성합니다.
- ChromaDB는 재료 활용법·대체재·보관법 검색에 사용합니다.
- 새로운 Tool은 `/chat` 계약을 바꾸지 않고 Workflow에 추가합니다.

## FE Mock

상태별 fixture는 [`mocks/chat/`](../../mocks/chat/)에 있습니다.
