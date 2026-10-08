# Tool Hub 연동 상태

이 문서는 LangGraph에 연결된 로컬 Tool Hub의 현재 동작과, 외부 Tool Hub endpoint 연동 전에
합의해야 할 항목을 구분한다. 기본 앱은 프로젝트 내부 CSV를 사용하며, `TOOL_HUB_ENABLED`
설정으로 전체 경로를 끌 수 있다.

## 현재 구현

| 항목 | 구현 위치 | 내용 |
| --- | --- | --- |
| 세션 저장소 경계 | `app/repositories/chat_session.py` | `ChatSessionRepository`와 메모리 구현체를 분리했다. 이후 Redis·DB 구현체로 교체할 수 있다. |
| Tool 호출 전 상태 | `ChatSessionState` | 재료 후보, 확정 재료, 충분성이 판별된 자연어 사용자 조건 원문을 보관할 수 있다. |
| Tool 계약 경계 | `app/agent/tools/contracts.py` | `ToolRequest`, `ToolResult`, 요청 준비 오류를 정의했다. |
| Vision 호출 경계 | `app/integrations/vision/openai_vision.py` | 사용자 확인 전 후보 추출 adapter를 제공한다. `/chat`의 현재 추출 경계는 OpenAI responder다. |
| 사용자 확인 보호 | `build_tool_request()` | `confirmed_ingredients`가 비어 있으면 Tool 요청 생성을 거부한다. 이미지 후보 재료는 전달하지 않는다. |
| 확인 의도 분류 | `JevIngredientConfirmationEvaluator` | `confirmed`·`rejected`·`edited`·`unclear`에 따라 후보 상태와 다음 단계를 결정한다. |
| 완료 단계 호출 경계 | `app/agent/graph.py`, `app/agent/tools/nodes.py` | `COMPLETED`에서 `tool_hub_recipe_recommendation` node가 확정 재료·조건으로 Tool을 실행한다. |
| Tool 결과 안전성 경계 | `NemoGuardrailService.validate_tool_result()` | 현재 `ToolResult`를 NeMo output rail로 검사하며, 구조적 tool-call DTO 합의 후 `tool result validation` rail로 교체한다. |
| 테스트 provider | `tests/unit/test_chat_service.py`, `tests/unit/test_chat_graph.py` | Tool node 실행, 결과 변환, 사용자 확인 전 재료 차단을 검증한다. |

## 현재 상태 흐름

```text
이미지 첨부
  → 재료 후보(ingredient_candidates) 저장
  → 사용자 자연어 답변을 Jev로 분류
  → confirmed: confirmed_ingredients로 이동
  → edited: 후보 수정 후 재확인
  → rejected: 후보 폐기 후 이미지 재요청
  → unclear: 후보 유지 후 재확인
  → confirmed_ingredients와 조건이 모두 있으면 LangGraph Tool Hub node 실행
  → ToolResult를 RecommendationData로 검증해 ChatResponse로 변환
```

이 제약은 사용자 확인 전 이미지 인식 후보를 추천·RAG 입력으로 사용하지 않는다는 프로젝트
계약을 코드로 강제한다.

## 외부 Tool Hub endpoint 연동 전 합의가 필요한 사항

다음은 Orchestrator만으로 안전하게 결정할 수 없으므로, FE·Tool Hub와 합의한 뒤 구현한다.

1. **재료 추출 계약**: 현재는 OpenAI 구조화 추출기가 이미지·자연어 재료와 수량을 처리한다.
   Tool Hub Vision·재료 정규화 DTO는 합의 후 해당 어댑터로 교체한다.
2. **사용자 조건 DTO**: 현재 식단 목표·조리 시간을 자연어 원문으로 수집한다. 알레르기 등 추가
   필드의 필수 여부·정규화 규칙은 FE·Tool Hub와 합의해 확장한다.
3. **Tool Hub endpoint와 인증**: URL, 인증 방식, tool 이름의 허용 목록, 요청·응답의 상세 DTO를
   정한다. Vision 요청에 이미지를 직접 담을지 안전한 `image_ref`를 쓸지도 함께 결정한다.
4. **오류 정책**: timeout, 재시도 횟수, 부분 결과, Tool Hub 오류를 FE에 어떻게 표시할지 결정한다.
5. **결과 변환 규칙**: 현재 로컬 adapter는 `RecommendationData`로 검증한다. 외부 Tool Hub도 같은
   검증·fallback 규칙을 따를지 정한다.

## 외부 adapter로 교체할 때의 순서

1. 합의된 요청·응답 DTO로 `ToolRequest`와 `ToolResult`의 하위 필드를 구체화한다.
2. `app/agent/tools/`에 Tool Hub용 async HTTP adapter를 추가하고 현재 `ToolRequestExecutor` 계약을 구현한다.
3. timeout·재시도·오류 변환을 adapter 안에 제한한다.
4. 실제 adapter를 설정으로 주입하고, 실패 시 기존 `500 ERROR` 계약을 유지한다.
5. 성공·timeout·Tool Hub 오류·불완전 결과의 API 통합 테스트를 추가한다.

이 과정에서도 `/chat`의 `status`, `step`, 레시피 2세트와 세트당 5개라는 FE 계약은 유지한다.
