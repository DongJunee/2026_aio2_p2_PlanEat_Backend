# Chat API Mock

FE 개발용 `POST /chat` 요청·응답 fixture입니다.

| 파일 | 용도 |
|---|---|
| `request-no-image.json` | `message`만 보내는 자연어 요청 |
| `request-image.json` | 자연어 `message`와 이미지를 함께 보내는 요청 |
| `response-input-requirements.json` | 이미지와 조건을 한 번에 수집하는 응답 |
| `response-image-input.json` | 이미지 첨부 요청 응답 |
| `response-ingredient-confirm.json` | 인식 재료 확인 단계 |
| `response-condition-input.json` | 추가 조건 질문 단계 |
| `response-success.json` | 추천 완료 응답 |
| `response-error.json` | 오류 응답 |

`response-success.json`은 `recipe_sets` 2개와 세트별 레시피 5개로 구성되어 있습니다.

`INGREDIENT_CONFIRM` 단계에서 FE는 확인 전용 필드 없이 자연어 `message`를 다시 보냅니다.
예를 들어 `네, 모두 맞아요`는 확정, `계란은 빼고 양파 1개 추가해줘`는 수정,
`아니요, 틀렸어요`는 이미지 재요청, 모호한 답변은 재확인 흐름으로 처리됩니다.

모든 응답은 [Chat API 명세](../../docs/api/chat.md)의 필드명을 따릅니다.
