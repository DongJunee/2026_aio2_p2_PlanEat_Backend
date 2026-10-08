# Chat API Mock

FE 개발용 `POST /chat` 요청·응답 fixture입니다.

이 디렉터리의 JSON은 FE 화면 개발과 테스트 계약 검증을 위한 예시 데이터이며, 실행 중인
`/chat`의 추천 source가 아닙니다. 기본 서버는 프로젝트 내부 CSV 기반 Tool Hub를 사용합니다.

| 파일 | 용도 |
|---|---|
| `request-no-image.json` | `message`만 보내는 자연어 요청 |
| `request-image.json` | 자연어 `message`와 이미지를 함께 보내는 요청 |
| `request-image-base64.json` | 테스트 냉장고 이미지 3장을 Base64 Data URL로 포함한 Swagger 요청 |
| `response-input-requirements.json` | 이미지와 조건을 한 번에 수집하는 응답 |
| `response-image-input.json` | 이미지 첨부 요청 응답 |
| `response-ingredient-confirm.json` | 인식 재료 확인 단계 |
| `response-condition-input.json` | 추가 조건 질문 단계 |
| `response-success.json` | 추천 완료 응답 |
| `response-error.json` | 오류 응답 |

`response-success.json`은 `recipe_sets` 2개와 세트별 레시피 5개로 구성되어 있으며, 완료 후
두 세트 중 하나를 선택하면 상세 PDF를 생성한다는 안내와 `available_set_ids`를 포함합니다.

## Base64 이미지 테스트

`images/`에는 같은 냉장고를 서로 다른 구도로 촬영한 테스트 이미지 3장이 있습니다.
`request-image-base64.json`은 이 이미지들을 `data:image/png;base64,...` 형식으로 인코딩한
완성 요청입니다. 파일 내용을 그대로 Swagger의 `/chat` Request body에 붙여넣어 테스트할 수
있습니다. 요청 후 응답의 `ingredients`를 확인하고, 같은 `session_id`로 재료 확인 메시지를
다시 보내면 다음 단계로 진행됩니다.

`INGREDIENT_CONFIRM` 단계에서 FE는 확인 전용 필드 없이 자연어 `message`를 다시 보냅니다.
예를 들어 `네, 모두 맞아요`는 확정, `계란은 빼고 양파 1개 추가해줘`는 수정,
`아니요, 틀렸어요`는 이미지 재요청, 모호한 답변은 재확인 흐름으로 처리됩니다.

모든 응답은 [Chat API 명세](../../docs/api/chat.md)의 필드명을 따릅니다.
