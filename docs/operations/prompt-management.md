# Prompt 관리와 보안

## 단일 관리 위치

PlanEat의 운영 프롬프트는 최상단 [`prompts/`](../prompts/)에서 관리한다. 현재
최종 추천 안내 문구는 아래 세 파일을 순서대로 조합한다.

```text
prompts/
├── shared/security.md       # 모든 LLM 단계의 비신뢰 입력·인젝션 방어
├── shared/output-korean.md  # 모든 LLM 단계의 공통 출력 규칙
└── chat/completion.md       # COMPLETED 단계 전용 지시
```

다른 단계는 공통 조각을 재사용하고 그 단계에 필요한 전용 파일만 더한다. 파일을 나누는
것만으로는 입력 토큰이 줄지 않으며, 필요한 조각만 선택할 때 토큰 절감 효과가 생긴다.

`app/integrations/llm/openai_responder.py`는 이 파일을 OpenAI Responses API의
`instructions`로 전달한다. 동적 값은 템플릿 치환하지 않고 다음처럼 출처별 블록으로
분리해 `input`에 전달한다.

```text
<untrusted_user_message>
사용자 메시지
</untrusted_user_message>

<trusted_recipe_sets>
서버가 검증한 추천 데이터
</trusted_recipe_sets>
```

## 방어 원칙

- 다층 방어는 `입력 정규화·검증 → 시스템 프롬프트 → 모델 → 출력 검증` 순서로 적용한다.
  한 계층을 통과했다고 안전하다고 가정하지 않는다.
- 입력 검증 전에는 Unicode NFKC 정규화, 소문자 변환, 공백·밑줄·기호 제거를 적용한다.
  이로써 `이 전 지 시 를 무 시 해`, `SYSTEM_PROMPT`처럼 분리된 표기를 비교할 수 있다.
  구현은 [`app/core/safety.py`](../app/core/safety.py)에 둔다.
- 알려진 우회 표지는 입력 단계에서 `400 ERROR`로 차단한다. 다만 키워드 검사는 우회될 수
  있으므로, 통과한 텍스트도 모델에서는 절대 지시가 아닌 비신뢰 데이터로 취급한다.
- LLM 응답은 사용자에게 반환하기 전 내부 지시·API 키 노출을 검사한다. 실패하면 원문을
  노출하지 않고 `500 ERROR`를 반환한다.
- 프롬프트 파일의 역할·출력·보안 지시는 `instructions`에만 둔다.
- 사용자 메시지, 이미지 인식 결과, Tool Hub·RAG 결과는 데이터로 취급하며 그 안의
  지시문을 실행하지 않는다.
- 외부 Tool 호출 권한은 BE2의 LangGraph가 결정한다. 최종 응답 생성 LLM에는 Tool을
  제공하지 않는다.
- `message`는 최대 2,000자로 제한하고, 최종 출력은 최대 120 토큰으로 제한한다.
- OpenAI Responses 요청에는 `store=False`를 지정한다.
- NeMo Guardrails의 `regex check input`·`regex check output`은 모델 호출 전후에
  내부 지시·비밀값 패턴을 다시 검사한다. 설정은 [`guardrails/config.yml`](../guardrails/config.yml)에만 둔다.
- 프롬프트 변경 시 정상 요청과 "이전 지시를 무시해" 같은 인젝션 시나리오를 테스트한다.
- 공통 프롬프트 조각은 항상 앞에, 사용자·세션별 데이터는 뒤에 둔다. 고정 접두어가
  유지되어야 프롬프트 캐시를 재사용할 수 있다.

OpenAI는 프롬프트를 코드처럼 버전 관리하고, 검증된 인자를 통해 동적 데이터를 전달하며,
프롬프트 변경에 테스트를 포함할 것을 권장한다. 또한 개발자 지시보다 낮은 권한의
사용자 입력을 개발자 지시 위치에 넣지 않아야 한다.
