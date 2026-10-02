# TypeSafe Jev 연동 가이드

## 1. Jev란?

[Jev](https://docs.typesafe.ai/)는 TypeSafe AI의 System One 모델이다. 사람에게 보여 줄
자연어 문장을 생성하는 LLM과 달리, 프로그램이 바로 사용할 수 있는 **정해진 형태의 판단**을
반환하는 데 초점을 둔다.

호출할 때 상태(`state`)와 질문(`questions`)을 전달하면, 선택 결과(`choice`)와 confidence를
반환한다. 이 프로젝트에서는 Jev를 레시피 생성기나 최종 안내 문구 생성기로 사용하지 않고,
LangGraph가 다음 단계를 정하는 보조 판단기로만 사용한다.

## 2. PlanEat에서의 사용 범위

현재 Jev는 `CONDITION_INPUT` 단계에서 아래 질문 하나를 판단한다.

> 사용자가 식단 목표와 조리 가능한 시간을 모두 제공했는가?

| Jev 결과 | confidence | LangGraph 처리 |
| --- | --- | --- |
| `needs_more_info` | 설정한 최저 confidence 이상 | `CONDITION_INPUT`을 유지하고 추가 조건을 요청한다. |
| `ready` | 설정한 최저 confidence 이상 | 추천 완료 단계로 진행한다. |
| API 오류, 응답 형식 오류, 최저 confidence 미만 | 관계없음 | Jev 결과를 사용하지 않고 기존 결정적 전이로 fallback한다. |

예를 들어 `다이어트 메뉴 추천해줘`는 조리 시간이 없으므로 추가 입력을 요청할 수 있다.
`다이어트 식단으로 20분 안에 만들고 싶어요`는 두 조건이 있어 완료 단계로 진행할 수 있다.

Jev가 활성화되어도 `/chat` 요청·응답의 `status`, `step`, DTO는 변경되지 않는다.

## 3. API 키 발급

1. [TypeSafe Console](https://console.typesafe.ai/keys)에 로그인한다.
2. API 키를 발급한다.
3. 키는 로컬 `.env`에만 저장한다. `.env`는 커밋하면 안 된다.

## 4. 환경변수 설정

프로젝트 루트에서 `.env.example`을 복사해 `.env`를 만든 뒤 아래 값을 설정한다.

```bash
cp .env.example .env
```

```env
# Jev 조건 판정을 활성화한다.
TYPESAFE_JEV_ENABLED=true

# TypeSafe Console에서 발급한 실제 키를 입력한다.
TYPESAFE_API_KEY=<your-typesafe-api-key>

# 현재 기본 모델이다.
TYPESAFE_MODEL=jev-latest

# 외부 API 호출 최대 대기 시간(초)이다.
TYPESAFE_TIMEOUT_SECONDS=2.0

# 이 confidence 이상일 때만 Jev 결정을 반영한다.
TYPESAFE_JEV_MIN_CONFIDENCE=0.8
```

`TYPESAFE_JEV_ENABLED=false`이거나 `TYPESAFE_API_KEY`가 없으면 Jev를 호출하지 않는다.
이 상태에서도 기존 Chat 흐름은 정상 동작한다.

## 5. Confidence 기준 선택

기본값 `0.8`은 보수적인 기준이다. 실제 테스트에서 충분한 조건 문장이 `ready`이지만
confidence `0.68`로 나올 수 있다. 이 경우 Jev 판단은 무시되고 fallback된다.

초기 실험에서 Jev의 `ready` 결과도 반영하려면 다음처럼 낮출 수 있다.

```env
TYPESAFE_JEV_MIN_CONFIDENCE=0.65
```

값을 낮출수록 조건이 충분하지 않은데 추천 단계로 진행할 위험이 커진다. 운영 반영 전에는
대표적인 한국어 입력을 수집해 confidence 분포와 오판 사례를 확인한 뒤 기준을 결정한다.

## 6. 로컬 검증

서버를 실행한다.

```bash
uv run uvicorn app.main:app --reload
```

Swagger UI(`http://127.0.0.1:8000/docs`)에서 같은 `session_id`로 아래 순서대로 요청한다.

1. 이미지 없이 요청해 `IMAGE_INPUT`을 확인한다.
2. 이미지를 첨부해 `INGREDIENT_CONFIRM`을 확인한다.
3. 재료를 확인해 `CONDITION_INPUT`을 확인한다.
4. 조리 시간이 빠진 조건을 보내 `CONDITION_INPUT` 유지 여부를 확인한다.
5. 식단 목표와 조리 시간을 모두 보낸 뒤 `COMPLETED` 진행 여부를 확인한다.

완료 응답 문구까지 확인하려면 기존 `OPENAI_API_KEY`도 `.env`에 설정해야 한다.

## 7. 개인정보 및 장애 처리

Jev가 활성화되면 `CONDITION_INPUT` 단계의 현재 사용자 메시지가 TypeSafe API에 전달된다.
운영 전에는 개인정보 처리, 데이터 보관, 제3자 전송 정책을 검토해야 한다.

네트워크 오류, timeout, TypeSafe 오류 응답, 예상하지 못한 응답 구조는 Chat API 오류로
전파하지 않는다. Jev 어댑터는 `None`을 반환하고 기존 LangGraph 전이가 계속 처리한다.
