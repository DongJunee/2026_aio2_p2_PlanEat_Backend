# Tool Hub 연동 준비 상태

이 문서는 BE1이 단독으로 완료한 Tool Hub 준비 작업과, BE2·FE 합의가 필요한 항목을 구분한다.
실제 BE2 네트워크 호출은 아직 수행하지 않는다.

## 완료된 BE1 준비 작업

| 항목 | 구현 위치 | 내용 |
| --- | --- | --- |
| 세션 저장소 경계 | `app/repositories/chat_session.py` | `ChatSessionRepository`와 메모리 구현체를 분리했다. 이후 Redis·DB 구현체로 교체할 수 있다. |
| Tool 호출 전 상태 | `ChatSessionState` | 재료 후보, 확정 재료, 충분성이 판별된 자연어 사용자 조건 원문을 보관할 수 있다. |
| Tool 계약 경계 | `app/agent/tools/contracts.py` | `ToolRequest`, `ToolResult`, 요청 준비 오류를 정의했다. |
| Vision 호출 경계 | `docs/architecture/data-flow.md` | BE1이 첨부 이미지를 BE2 Vision Function Call로 전달하고 후보를 받는 목표 흐름을 정의했다. 상세 DTO는 팀 합의 전이다. |
| 사용자 확인 보호 | `build_tool_request()` | `confirmed_ingredients`가 비어 있으면 Tool 요청 생성을 거부한다. 이미지 후보 재료는 전달하지 않는다. |
| 확인 의도 분류 | `JevIngredientConfirmationEvaluator` | `confirmed`·`rejected`·`edited`·`unclear`에 따라 후보 상태와 다음 단계를 결정한다. |
| 완료 단계 호출 경계 | `ChatService._execute_tool_request()` | 확정 재료와 조건으로 `ToolRequest`를 만들고 현재는 fake provider에 전달한다. |
| Tool 결과 안전성 경계 | `NemoGuardrailService.validate_tool_result()` | 현재 `ToolResult`를 NeMo output rail로 검사하며, 구조적 tool-call DTO 합의 후 `tool result validation` rail로 교체한다. |
| fake provider | `app/agent/tools/fake_provider.py` | 실 네트워크 없이 BE1 호출 경계를 테스트하는 결정적 결과를 반환한다. |
| 테스트 | `tests/unit/test_chat_service.py` | 미확정 재료 차단과 유효한 요청·fake 결과를 검증한다. |

## 현재 상태 흐름

```text
이미지 첨부
  → 재료 후보(ingredient_candidates) 저장
  → 사용자 자연어 답변을 Jev로 분류
  → confirmed: confirmed_ingredients로 이동
  → edited: 후보 수정 후 재확인
  → rejected: 후보 폐기 후 이미지 재요청
  → unclear: 후보 유지 후 재확인
  → confirmed_ingredients와 조건이 모두 있으면 ToolRequest 생성
  → fake provider 실행 (BE2 endpoint 합의 전)
```

이 제약은 사용자 확인 전 이미지 인식 후보를 추천·RAG 입력으로 사용하지 않는다는 프로젝트
계약을 코드로 강제한다.

## 실제 연동 전 합의가 필요한 사항

다음은 BE1만으로 안전하게 결정할 수 없으므로, FE·BE2와 합의한 뒤 구현한다.

1. **재료 수정 추출 계약**: 현재는 BE1 fallback parser가 제한적인 추가·삭제·수량 변경만 처리한다.
   복잡한 식재료명·수량 추출 DTO는 BE2와 합의해 교체한다.
2. **사용자 조건 DTO**: 현재 식단 목표·조리 시간을 자연어 원문으로 수집한다. 알레르기 등 추가
   필드의 필수 여부·정규화 규칙은 FE·BE2와 합의해 확장한다.
3. **BE2 endpoint와 인증**: URL, 인증 방식, tool 이름의 허용 목록, 요청·응답의 상세 DTO를
   정한다. Vision 요청에 이미지를 직접 담을지 안전한 `image_ref`를 쓸지도 함께 결정한다.
4. **오류 정책**: timeout, 재시도 횟수, 부분 결과, BE2 오류를 FE에 어떻게 표시할지 결정한다.
5. **결과 변환 규칙**: BE2 `ToolResult`를 외부 `ChatResponse.data.recipe_sets`로 바꾸는 검증·
   fallback 규칙을 정한다.

## 실제 어댑터 구현 순서

1. 합의된 요청·응답 DTO로 `ToolRequest`와 `ToolResult`의 하위 필드를 구체화한다.
2. `app/agent/tools/`에 BE2용 async HTTP adapter를 추가한다.
3. timeout·재시도·오류 변환을 adapter 안에 제한한다.
4. fake provider와 실제 adapter를 설정으로 선택할 수 있게 한다.
5. 성공·timeout·BE2 오류·불완전 결과의 API 통합 테스트를 추가한다.

이 과정에서도 `/chat`의 `status`, `step`, 레시피 2세트와 세트당 5개라는 FE 계약은 유지한다.
