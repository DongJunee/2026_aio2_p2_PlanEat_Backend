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

`status`와 `step`은 FE 분기 처리에 사용하는 고정 코드입니다. 사용자에게 표시하는 문구는 `response`, 추가 입력 항목은 `questions`·`ingredients`·`data`를 사용합니다.

### 상태 코드

| HTTP 상태 | `status` | 설명 |
|---:|---|---|
| 200 | `SUCCESS` | 레시피 추천이 완료되었습니다. |
| 200 | `NEED_MORE_INFO` | 다음 진행을 위해 사용자 입력 또는 확인이 필요합니다. |
| 400 | `ERROR` | 정규화된 입력 검증에서 안전하지 않은 요청으로 판단되었습니다. |
| 500 | `ERROR` | 요청 처리 중 서버 오류가 발생했습니다. |

### 진행 단계 코드

`step`은 `status`가 `SUCCESS` 또는 `NEED_MORE_INFO`일 때 포함됩니다.

| `status` | `step` | FE 처리 |
|---|---|---|
| `SUCCESS` | `COMPLETED` | `data.recipe_sets`를 표시합니다. |
| `NEED_MORE_INFO` | `IMAGE_INPUT` | `questions`를 표시하고 이미지 첨부를 요청합니다. |
| `NEED_MORE_INFO` | `CONDITION_INPUT` | `questions`를 표시하고 추가 조건을 입력받습니다. |
| `NEED_MORE_INFO` | `INGREDIENT_CONFIRM` | `ingredients`를 표시하고 인식 재료를 확인받습니다. |

`ERROR` 응답에는 `step`을 포함하지 않습니다.

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
- Tool Hub 연동 전에는 이미지 인식 재료와 추천 결과를 API·FE 통합 검증용 임시 데이터로 반환합니다.
- `COMPLETED` 단계의 `response` 문구는 `OPENAI_MODEL`(기본값 `gpt-4o-mini`)로 생성합니다.
- OpenAI API 키가 없거나 LLM 호출에 실패하면 `500 ERROR`를 반환합니다.
- 최종 추천 응답 프롬프트는 [`prompts/`](../../prompts/)의 공통·단계별 조각을 조합해 관리합니다.
- 프롬프트의 역할·출력·보안 지시는 `instructions`에, 사용자 메시지와 추천 데이터는 출처별 `input` 블록에 분리해 전달합니다.
- 최종 응답 LLM에는 Tool을 제공하지 않으며, 출력은 최대 120 토큰으로 제한합니다.
- Chat API는 NFKC·소문자·구분 문자 제거로 정규화한 입력을 검사하고, 통과한 사용자
  메시지도 비신뢰 데이터 블록으로만 LLM에 전달합니다. LLM 출력은 내부 지시·API 키 노출
  여부를 다시 검사합니다.
- Tool Hub 연동 후에는 이 임시 데이터를 Vision·Recipe·Nutrition·Shopping Tool 실행 결과로 대체합니다.
- 사용자 확인 전의 이미지 인식 결과는 추천에 사용하지 않습니다.
- Recipe Tool은 내부 레시피 DB를 조회합니다.
- Nutrition Tool은 영양 정보를 제공합니다.
- Shopping Tool은 부족 재료와 장보기 목록을 생성합니다.
- ChromaDB는 재료 활용법·대체재·보관법 검색에 사용합니다.
- 새로운 Tool은 `/chat` 계약을 바꾸지 않고 Workflow에 추가합니다.

## FE Mock

상태별 fixture는 [`mocks/chat/`](../../mocks/chat/)에 있습니다.
