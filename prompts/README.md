# Prompt 관리

PlanEat의 운영 프롬프트는 이 폴더에서 Git으로 버전 관리한다. 코드 안에 장문의 프롬프트를
중복 작성하지 않는다.

| 파일 | 용도 | 호출 단계 |
|---|---|---|
| `shared/security.md` | 비신뢰 입력 경계와 프롬프트 인젝션 방어 | 모든 LLM 단계 |
| `shared/output-korean.md` | 한국어·간결한 출력 공통 규칙 | 모든 LLM 단계 |
| `chat/completion.md` | 추천 완료 단계의 역할·맥락·출력 규칙 | `COMPLETED` |
| `chat/ingredient-extraction.md` | 이미지·자연어 재료 구조화 추출 | 재료 입력 단계 |
| `chat/clarification.md` | 부족한 입력에 맞는 안내 문구·질문 생성 | `INPUT_REQUIREMENTS`, `IMAGE_INPUT`, `CONDITION_INPUT` |
| `chat/recommendation.md` | Tool Hub 비활성화 시 임시 레시피 생성과 JSON 출력 규칙 | `COMPLETED` |

`COMPLETED`의 임시 LLM 추천 단계는 `security.md`, `output-korean.md`,
`recommendation.md`를 표의 순서대로 조합합니다. 기존 안내 문구 fallback은
`security.md`, `output-korean.md`, `completion.md`를 조합합니다. 파일을 나누는 것 자체가
토큰을 줄이지는 않으며, 단계에 필요한 조각만 선택할 때 입력 토큰이 줄어듭니다.

재료 입력 단계는 `security.md`, `ingredient-extraction.md`를 조합해 이미지 또는 자연어에서
사용자가 명시한 재료만 구조화합니다. 재료명 목록은 코드에 내장하지 않습니다.

추가 입력 안내 단계는 `security.md`, `output-korean.md`, `clarification.md`를 조합해 현재
세션에 부족한 정보만 질문합니다. LLM 호출이 실패하면 서비스의 단계별 고정 fallback이
사용되므로 FE의 `status`·`step` 계약은 유지됩니다.

## 다층 방어

프롬프트는 단독 보안 장치가 아니다. Chat API는 아래 순서로 방어한다.

```text
입력 정규화·검증 → security.md 모델 지시 → LLM → 출력 검증
```

- `app/core/safety.py`는 입력을 NFKC 정규화하고 소문자로 바꾼 뒤 공백·밑줄·기호를
  제거해 비교한다. 따라서 `탈 옥`, `SYSTEM_PROMPT` 같은 분리 표기도 검사할 수 있다.
- 입력 검증은 알려진 인젝션 표지를 사전에 차단하지만, 통과한 입력도
  `untrusted_user_message` 데이터 블록으로만 모델에 전달한다.
- 출력 검증은 시스템·개발자 프롬프트와 API 키 같은 내부 정보 노출을 차단하는 마지막
  안전망이다. 검증 실패 시 원문을 반환하지 않는다.

## 변경 규칙

- 프롬프트는 애플리케이션 코드와 같은 PR에서 검토한다.
- 동적 값은 프롬프트 파일에 문자열 치환하지 않고, 검증된 Python 인자로 별도 전달한다.
- 사용자 메시지·Tool Hub 결과·RAG 문서는 비신뢰 입력으로 취급한다. 이 텍스트를
  개발자 지시 위치에 붙여 넣지 않는다.
- 프롬프트 변경 시 대표 정상 입력과 프롬프트 인젝션 입력을 fake LLM 테스트로 검증한다.
- 프롬프트 파일에는 API 키, 개인정보, 운영 데이터, 비공개 시스템 정보를 넣지 않는다.
- 공통 조각은 항상 먼저, 단계별 조각은 그 다음, 사용자별·세션별 데이터는 마지막에
  전달한다. 공통 접두어가 안정적으로 유지돼야 프롬프트 캐시 재사용 가능성이 높아진다.
