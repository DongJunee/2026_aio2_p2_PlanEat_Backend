# Tool Hub 연동 준비 상태

이 문서는 BE1이 단독으로 완료한 Tool Hub 준비 작업과, BE2·FE 합의가 필요한 항목을 구분한다.
실제 BE2 네트워크 호출은 아직 수행하지 않는다.

## 완료된 BE1 준비 작업

| 항목 | 구현 위치 | 내용 |
| --- | --- | --- |
| 세션 저장소 경계 | `app/repositories/chat_session.py` | `ChatSessionRepository`와 메모리 구현체를 분리했다. 이후 Redis·DB 구현체로 교체할 수 있다. |
| Tool 호출 전 상태 | `ChatSessionState` | 재료 후보, 확정 재료, 조건 메시지를 보관할 수 있다. |
| Tool 계약 경계 | `app/agent/tools/contracts.py` | `ToolRequest`, `ToolResult`, 요청 준비 오류를 정의했다. |
| Vision 호출 경계 | `docs/data-flow.md` | BE1이 첨부 이미지를 BE2 Vision Function Call로 전달하고 후보를 받는 목표 흐름을 정의했다. 상세 DTO는 팀 합의 전이다. |
| 사용자 확인 보호 | `build_tool_request()` | `confirmed_ingredients`가 비어 있으면 Tool 요청 생성을 거부한다. 이미지 후보 재료는 전달하지 않는다. |
| fake provider | `app/agent/tools/fake_provider.py` | 실 네트워크 없이 BE1 호출 경계를 테스트하는 결정적 결과를 반환한다. |
| 테스트 | `tests/unit/test_chat_service.py` | 미확정 재료 차단과 유효한 요청·fake 결과를 검증한다. |

## 현재 상태 흐름

```text
이미지 첨부
  → 재료 후보(ingredient_candidates) 저장
  → FE가 사용자 확인 UI를 표시
  → 확정 재료(confirmed_ingredients)는 아직 저장하지 않음
  → Tool 요청 생성 시도
  → ToolRequestPreparationError로 차단
```

이 제약은 사용자 확인 전 이미지 인식 후보를 추천·RAG 입력으로 사용하지 않는다는 프로젝트
계약을 코드로 강제한다.

## 실제 연동 전 합의가 필요한 사항

다음은 BE1만으로 안전하게 결정할 수 없으므로, FE·BE2와 합의한 뒤 구현한다.

1. **재료 확인 입력 형식**: 확인, 삭제, 수량 수정, 재료 추가를 어떤 JSON 또는 메시지 형식으로
   FE가 보낼지 결정한다. 합의 후에만 `confirmed_ingredients`를 채운다.
2. **사용자 조건 DTO**: 식단 목표, 조리 시간, 알레르기 등의 필드·필수 여부·정규화 규칙을
   결정한다. 현재는 원문 메시지만 보관하며, Tool 요청에서도 임시 `message` 필드로만 표현한다.
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
