# PlanEat Backend 문서 안내

## 1. API 계약

FE와 BE 사이의 요청·응답 형식과 검증 규칙이다.

- [Chat API 명세](api/chat.md)

## 2. 구조·데이터 흐름

BE1의 구성, LangGraph 단계, FE·BE1·BE2 사이의 흐름을 설명한다.

- [구현 개요](architecture/implementation-overview.md)
- [데이터 흐름](architecture/data-flow.md)

## 3. 외부 연동·안전성

외부 서비스 연동 설정, Tool Hub 준비 상태, 입력·출력 보호 기준이다.

- [Tool Hub 연동 준비](integrations/tool-hub-readiness.md)
- [TypeSafe Jev 연동](integrations/jev.md)
- [NeMo Guardrails 연동](integrations/guardrails.md)
- [LangSmith 연동](integrations/langsmith.md)
- [프롬프트 관리](operations/prompt-management.md)

## 4. 개발·운영

로컬 실행, 환경변수, 테스트와 온보딩 절차다.

- [온보딩](operations/onboarding.md)

## 권장 읽기 순서

1. [온보딩](operations/onboarding.md)
2. [구현 개요](architecture/implementation-overview.md)
3. [Chat API 명세](api/chat.md)
4. [데이터 흐름](architecture/data-flow.md)
