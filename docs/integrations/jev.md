# TypeSafe Jev 연동 가이드

## 1. Jev란?

[Jev](https://docs.typesafe.ai/)는 TypeSafe AI의 System One 모델이다. 사람에게 보여 줄
자연어 문장을 생성하는 LLM과 달리, 프로그램이 바로 사용할 수 있는 **정해진 형태의 판단**을
반환하는 데 초점을 둔다.

호출할 때 상태(`state`)와 질문(`questions`)을 전달하면, 선택 결과(`choice`)와 confidence를
반환한다. 이 프로젝트에서는 Jev를 레시피 생성기나 최종 안내 문구 생성기로 사용하지 않고,
LangGraph가 다음 단계를 정하는 보조 판단기로만 사용한다.

## 2. PlanEat에서의 사용 범위

현재 Jev는 각 사용자 `message`에서 조건 충분성과 재료 확인 의도를 판단한다. 외부 요청에는
별도 조건·확인 DTO를 두지 않으며, 자연어 메시지를 Jev에 전달해 내부 상태 전이를 결정한다.

> 사용자가 식단 목표와 조리 가능한 시간을 모두 제공했는가?

| Jev 결과 | confidence | LangGraph 처리 |
| --- | --- | --- |
| `needs_more_info` | 설정한 최저 confidence 이상 | `CONDITION_INPUT`을 유지하고 추가 조건을 요청한다. |
| `ready` | 설정한 최저 confidence 이상 | 조건이 준비된 것으로 저장하고 이미지·재료 확인 단계의 다음 전이를 진행한다. |
| API 오류, 응답 형식 오류, 최저 confidence 미만 | 관계없음 | Jev 결과를 사용하지 않고 기존 결정적 전이로 fallback한다. |

재료 확인 단계에서는 별도의 `ingredient_confirmation` 질문을 사용한다. Jev는 수정 의도만
반환하고, 수정된 재료·수량은 OpenAI 구조화 추출기가 반영한다.

| Jev 결과 | LangGraph 처리 |
|---|---|
| `confirmed` | 후보를 `confirmed_ingredients`로 이동하고 조건 입력 여부를 확인한다. |
| `edited` | 추가·삭제·수량 변경을 후보에 반영한 뒤 재확인한다. |
| `rejected` | 후보를 폐기하고 이미지를 다시 요청한다. |
| `unclear` | 후보를 유지하고 확인 답변을 다시 요청한다. |

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
# Jev 자연어 조건·재료 확인 판정을 활성화한다.
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
3. 재료 확인에 `네, 모두 맞아요`를 보내 `confirmed` 처리를 확인한다.
4. 재료 확인에 `계란은 빼고 양파 1개 추가해줘`를 보내 수정 목록 재확인을 확인한다.
5. 재료 확인에 `아니요, 틀렸어요`를 보내 이미지 재요청을 확인한다.
6. 식단 목표와 조리 시간을 모두 보낸 뒤 `COMPLETED`와 주입된 ToolRequest 전달을 확인한다.

완료 응답 문구까지 확인하려면 기존 `OPENAI_API_KEY`도 `.env`에 설정해야 한다.

## 7. 개인정보 및 장애 처리

Jev가 활성화되면 각 요청의 현재 사용자 메시지가 TypeSafe API에 전달된다.
운영 전에는 개인정보 처리, 데이터 보관, 제3자 전송 정책을 검토해야 한다.

네트워크 오류, timeout, TypeSafe 오류 응답, 예상하지 못한 응답 구조는 Chat API 오류로
전파하지 않는다. Jev 어댑터는 `None`을 반환하고 기존 LangGraph 전이가 계속 처리한다.
