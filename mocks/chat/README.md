# Chat API Mock

FE 개발용 `POST /chat` 요청·응답 fixture입니다.

| 파일 | 용도 |
|---|---|
| `request-no-image.json` | 이미지 없이 보내는 텍스트 요청 |
| `request-image.json` | 이미지 첨부 요청 |
| `response-image-input.json` | 이미지 첨부 요청 응답 |
| `response-ingredient-confirm.json` | 인식 재료 확인 단계 |
| `response-condition-input.json` | 추가 조건 질문 단계 |
| `response-success.json` | 추천 완료 응답 |
| `response-error.json` | 오류 응답 |

`response-success.json`은 `recipe_sets` 2개와 세트별 레시피 5개로 구성되어 있습니다.

모든 응답은 [Chat API 명세](../../docs/api/chat.md)의 필드명을 따릅니다.
