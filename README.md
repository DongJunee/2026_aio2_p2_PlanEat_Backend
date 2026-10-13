<div align="center">

<img src="docs/images/planeat-brand-mark.png" alt="PlanEat 잎과 접시 로고" width="112">

<h1>🥗 PlanEat</h1>

<h3>냉장고 속 재료를, 오늘의 한 끼로.</h3>

<p><code>FastAPI</code> &nbsp;·&nbsp; <code>LangGraph</code> &nbsp;·&nbsp; <code>Tool Hub</code> &nbsp;·&nbsp; <code>AI Meal Planning</code></p>

</div>

> 냉장고·영수증 사진 또는 자연어 재료를 바탕으로, 혼밥 식단을 제안하는 대화형 AI 서비스입니다.

## 프로젝트 개요

혼자 사는 사람은 냉장고에 재료가 있어도 메뉴를 정하기 어렵고, 이미 가진 재료를 다시 사거나 남기기 쉽습니다. PlanEat은 보유 재료와 식단 조건을 대화로 모아 지금 만들 수 있는 식단을 제안합니다.

<p align="center">
  <img src="docs/images/planeat-overview-flow-v2.gif" alt="냉장고 재료가 AI 식단 계획을 거쳐 한 끼 식단으로 이어지는 PlanEat 서비스 흐름 애니메이션" width="840">
</p>

> [!TIP]
> **PlanEat의 핵심 원칙** — 이미지에서 찾은 재료는 바로 추천에 쓰지 않습니다. 사용자가 확인한 재료와 식단 조건이 갖춰진 뒤에만 추천을 시작합니다.

<br>

## 목차

### 🎤 발표

1. [팀원 소개](#팀원-소개)
2. [사용자 페르소나와 공감지도](#사용자-페르소나와-공감지도)
3. [주요 기능](#주요-기능)
4. [서비스 동작 방식](#서비스-동작-방식)
5. [핵심 기술 1: LangGraph Orchestrator](#핵심-기술-1-langgraph-orchestrator)
6. [핵심 기술 2: Tool Hub](#핵심-기술-2-tool-hub)
7. [확장 서비스와 안전성](#확장-서비스와-안전성)
8. [트러블슈팅](#트러블슈팅)

### 🛠️ 프로젝트 문서

1. [빠른 시작](#빠른-시작)
2. [Chat API](#chat-api)
3. [구성 요소와 설정](#구성-요소와-설정)
4. [LangGraph와 Tool Hub 상세](#langgraph와-tool-hub-상세)
5. [외부·보조 서비스](#외부보조-서비스)
6. [프로젝트 구조와 테스트](#프로젝트-구조와-테스트)
7. [관련 문서](#관련-문서)

<br>

## 팀원 소개

<table>
  <thead>
    <tr>
      <th width="18%" align="center">이름</th>
      <th width="22%" align="left">역할</th>
      <th align="left">담당 영역</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td align="center">조권식</td>
      <td>PM</td>
      <td>일정·요구사항 관리, 팀 업무 조율, 데이터 전처리와 초기 데이터 관리</td>
    </tr>
    <tr>
      <td align="center">이홍진</td>
      <td>FE</td>
      <td>Streamlit 화면, 사용자 입력·이미지 수집, API 연동, 상태별 화면 전환, UI/UX</td>
    </tr>
    <tr>
      <td align="center">박동준</td>
      <td>BE · Tool Hub</td>
      <td>이미지 기반 재료 인식, Recipe·Nutrition·Shopping·Guide Tool, ChromaDB 검색, Tool 호출 결과 관리</td>
    </tr>
    <tr>
      <td align="center">최경락</td>
      <td>BE · Agent Orchestrator</td>
      <td>FastAPI, LangGraph 상태 전이, 세션 관리, Jev 판정, Guardrails, 결과 통합</td>
    </tr>
  </tbody>
</table>

<br>

## 사용자 페르소나와 공감지도

<p align="center">
  <img src="docs/images/planeat-persona-empathy-map.png" alt="PlanEat 사용자 페르소나와 공감지도 인포그래픽" width="100%">
</p>

<br>

## 주요 기능

| 기능 | 내용 | 사용자 가치 |
| :--- | :--- | :--- |
| 이미지·자연어 재료 입력 | 냉장고·영수증 사진과 직접 입력한 재료를 모두 처리 | 입력 방식에 맞춰 보유 재료를 쉽게 등록 |
| 재료 후보 확인 | 이미지 인식 결과를 후보로 보여주고 추가·삭제·수량 수정을 받음 | 잘못 인식된 재료가 추천에 반영되는 문제 방지 |
| 대화형 식단 조건 수집 | 다이어트, 고단백, 채식, 조리 시간 같은 조건을 여러 대화에 걸쳐 누적 | 한 번에 모든 정보를 입력하지 않아도 맞춤 추천 가능 |
| 식단·영양·장보기 추천 | 레시피 2세트(세트당 5개), 영양 정보, 부족 재료와 장보기 목록 제공 | 지금 가진 재료로 만들 수 있는 메뉴와 추가 구매 항목을 함께 확인 |
| 추천 피드백과 PDF | 추천 결과에 대한 조건 변경·재추천 요청을 반영하거나 원하는 세트를 선택해 상세 PDF 생성 | 비교·수정 후 선택한 식단을 조리할 때 활용 |

> [!NOTE]
> 추천 결과는 **2개 세트**, 세트별 **5개 레시피**로 제공됩니다. 각 레시피에는 영양 정보, 부족 재료, 장보기 목록이 함께 포함됩니다.

<br>

## 서비스 동작 방식

```mermaid
flowchart LR
    A[1. 재료 입력<br/>사진 또는 자연어] --> B[2. 재료 확인<br/>후보 수정·확정]
    B --> C[3. 식단 조건 입력<br/>다이어트·시간 등]
    C --> D[4. 식단 추천<br/>레시피·영양·장보기]
    D --> E{5. 다음 행동}
    E -->|조건 피드백| C
    E -->|세트 선택| F[상세 PDF 생성]
```

| 단계 | 사용자가 하는 일 | 서비스가 하는 일 |
| --- | --- | --- |
| 1. 재료 입력 | 냉장고·영수증 사진을 올리거나 보유 재료를 직접 입력 | 사진에서는 재료 후보를 찾고, 자연어는 재료·수량으로 정리 |
| 2. 재료 확인 | 후보를 확인하고 추가·삭제·수량 수정 | 확인된 재료만 확정 목록으로 저장 |
| 3. 식단 조건 입력 | 다이어트, 고단백, 채식, 조리 시간 등을 입력 | 여러 대화의 조건을 누적해 추천 기준 구성 |
| 4. 식단 추천 | 두 추천 세트를 비교하고 원하는 세트를 선택 | 레시피·영양·부족 재료·장보기 목록을 만들고 선택 시 PDF 생성 |
| 5. 피드백·선택 | 조건을 바꿔 재추천을 요청하거나 세트를 선택 | 피드백을 기존 조건에 누적해 재추천·재검증하거나, 선택한 세트의 PDF를 생성 |

핵심은 **사진 인식 결과를 바로 추천에 쓰지 않는 것**입니다. 사용자가 재료를 확인한 뒤에만 추천을 시작하므로, 인식 오류 때문에 만들 수 없는 메뉴가 추천되는 문제를 줄입니다.

### 사용자 흐름 예시

아래 화면은 서비스 동작 방식의 주요 단계를 보여줍니다. 이미지에서 찾은 재료는 먼저 확인·수정할 수 있고, 확정된 재료와 식단 조건을 바탕으로 추천 결과를 제공합니다.

<p align="center">
  <strong>① 재료 후보 확인</strong><br>
  <img src="docs/images/fridge-ingredient-confirmation.png" alt="냉장고 재료 후보 확인 화면" width="720">
</p>

<p align="center">
  <strong>② 영수증 기반 재료 입력</strong><br>
  <img src="docs/images/receipt-ingredient-confirmation.png" alt="영수증 재료 후보 확인 화면" width="720">
</p>

<p align="center">
  <strong>③ 식단 추천 결과</strong><br>
  <img src="docs/images/recipe-recommendations.png" alt="추천 결과 화면" width="720">
</p>

<br>

### 사용자 피드백 루프와 검증 범위

추천 완료 뒤 사용자가 "단백질을 더 높여줘" 또는 "다른 메뉴로 다시 추천해줘"처럼 피드백을 보내면, 서비스는 피드백 의도를 분류하고 기존 식단 조건에 누적합니다. 누적된 조건으로 같은 Tool Hub 추천·DTO·Guardrails 검증 경로를 다시 실행하므로, 이전에 확정한 재료와 조건은 유지하면서 결과를 조정할 수 있습니다. 최근 피드백은 최대 10개까지 세션에 보관합니다.

Tool Hub 결과와 최종 응답은 Guardrails와 응답 DTO 검증을 통과한 경우에만 사용자에게 반환됩니다. 검증에 실패하면 결과를 노출하지 않고 오류로 처리합니다.

<br>

### LangGraph와 Tool Hub의 역할

- **LangGraph**는 대화의 현재 단계와 다음 질문을 결정하는 Orchestrator입니다. 재료 후보 확인, 조건 수집, 완료 상태를 관리하고, 완료일 때만 Tool Hub를 호출합니다.
- **Tool Hub**는 확정 재료와 조건으로 실제 추천 데이터를 만드는 실행 계층입니다. 레시피 후보 검색, 식단 조합, 영양·장보기 계산, 부족 재료의 대체 가이드 보강을 담당합니다.
- 두 계층을 분리해 이미지 인식 후보나 미확정 조건이 추천 데이터에 들어가지 않도록 했습니다.

<br>

## 핵심 기술 1: LangGraph Orchestrator

일반적인 챗봇은 한 번의 요청마다 독립적으로 응답하기 쉽습니다. PlanEat은 재료 확인, 조건 수집, 추천 완료처럼 여러 차례 이어지는 대화를 다뤄야 하므로 LangGraph로 **현재 상태와 다음 행동을 명시적으로 관리**합니다. 한마디로 요약하면 **대화가 올바른 순서로 진행되도록 제어하는 워크플로우 엔진** 입니다.

```mermaid
stateDiagram-v2
    [*] --> 재료입력
    재료입력 --> 재료확인: 이미지 재료 후보 추출
    재료입력 --> 조건입력: 자연어 재료 입력
    재료확인 --> 재료확인: 수정 또는 재확인
    재료확인 --> 재료입력: 후보 거절
    재료확인 --> 조건입력: 재료 확정
    조건입력 --> 조건입력: 목적·조건 부족
    조건입력 --> 추천완료: 조건 충족
    추천완료 --> [*]: Tool Hub 실행
```

| LangGraph가 관리하는 것 | 사용자에게 주는 효과 |
| --- | --- |
| `session_id`별 재료 후보·확정 재료·조건·대화 단계 | 여러 번 나눠 말해도 맥락을 유지합니다. |
| `IMAGE_INPUT` → `INGREDIENT_CONFIRM` → `CONDITION_INPUT` → `COMPLETED` 전이 | 지금 무엇을 입력하거나 확인해야 하는지 화면이 명확합니다. |
| 이미지 후보와 확정 재료의 분리 | 잘못 인식된 재료가 추천에 바로 반영되지 않습니다. |
| 완료 단계에서만 Tool Hub 호출 | 조건이 부족한 상태의 불필요한 추천·검색을 막습니다. |
| 메시지 10개 초과 시 문맥 요약 | 오래된 대화는 내부 요약으로 저장하고 최근 2개 메시지는 그대로 유지해, 긴 대화도 맥락과 상태 크기를 함께 관리합니다. |


<br>

## 핵심 기술 2: Tool Hub

Tool Hub는 확정 재료와 사용자 조건을 받아 실제 추천 데이터를 만드는 로컬 실행 계층입니다. 현재 외부 Tool Hub API를 호출하는 구조가 아니라, 프로젝트 내부 도구를 조합해 실행합니다. 이 구조 덕분에 추천 로직을 대화 제어 로직과 분리하고, 향후 외부 서비스로 교체할 수 있습니다.

```mermaid
flowchart TB
    INPUT[확정 재료 + 식단 조건]
    RESULT[추천 응답<br/>2세트 × 5개 레시피]

    subgraph SEARCH[1. 후보 찾기]
        RECIPE[Recipe Tool<br/>검증 CSV + Chroma RAG<br/>후보 검색]
    end

    subgraph PLAN[2. 식단 구성]
        PAIR[Planning Tool<br/>레시피<br/>조합 생성]
        SELECT[Jev 또는 규칙<br/>기반 선택<br/>최종 5개 세트]
    end

    subgraph ENRICH[3. 추천 정보 완성]
        SHOPPING[Shopping Tool<br/>부족 재료<br/>장보기]
        NUTRITION[Nutrition Tool<br/>영양 정보]
        GUIDE[Guide Tool<br/>대체재 검색]
    end

    INPUT --> RECIPE --> PAIR --> SELECT
    SELECT --> SHOPPING --> RESULT
    SELECT --> NUTRITION --> RESULT
    SELECT -. 부족 재료 .-> GUIDE
    GUIDE -. 부족 재료 대체재 .-> SHOPPING
```

| Tool / 조합 계층 | 처리 내용 | 사용자 가치 |
| --- | --- | --- |
| Recipe Tool | 검증된 내부 CSV를 원본으로 유지하고, 사전 구축한 Chroma 벡터 DB의 RAG 의미 검색으로 후보를 보강한 뒤 보유 재료·조리 시간·다이어트·고단백·채식 조건으로 정렬 | 단순 키워드가 달라도 사용자의 재료·조건과 의미상 가까운 레시피까지 찾아, 보유 재료 활용도가 높은 메뉴를 우선 제안 |
| Planning Tool | 최소 10개 레시피 후보를 메인·반찬 조합으로 만들고 5개 식단을 선택 | 한 가지 메뉴가 아닌 균형 잡힌 선택지 제공 |
| Shopping Tool | 부족 재료를 중요도순으로 최대 5개까지 정리 | 바로 구매해야 할 재료를 빠르게 파악 |
| Nutrition Tool | 원본 영양 정보를 우선 사용하고 없을 때만 제한적으로 추정 | 조건에 맞는 식단을 비교 가능 |
| Guide Tool | 부족 재료의 대체재·활용법을 보강 | 재료가 조금 부족해도 조리 대안을 확인 |

RAG 경로는 `CSV 원본 검증 → Chroma 벡터 인덱스 의미 검색 → 관련 레시피 ID 후보 확보 → 검증된 CSV 상세 데이터로 추천 구성` 순서로 동작합니다. 즉, 벡터 DB는 자연어 조건과 유사한 후보를 찾는 검색 계층이며, 최종 레시피·영양·장보기 데이터의 기준은 검증된 내부 CSV입니다. Chroma 인덱스가 설정되지 않았거나 검색에 실패하면 CSV 기반 검색으로 계속 추천합니다.

Tool Hub의 중요한 안전 장치는 **이미지에서 추정한 후보가 아니라 사용자가 확인한 재료만 입력으로 받는다**는 것입니다. 추천 결과는 다시 DTO와 Guardrails 검증을 거쳐서만 화면으로 전달됩니다.

<br>

## 확장 서비스와 안전성

PlanEat은 모든 기능을 한 모델에 맡기지 않고, 역할별 서비스와 fallback을 조합합니다. 이 서비스들은 독립적으로 켜고 끌 수 있어 장애가 전체 추천 흐름을 멈추지 않도록 설계했습니다.

| 서비스 | 발표에서 설명할 역할 | 왜 사용했나 | 장애·미설정 시 동작 |
| --- | --- | --- | --- |
| OpenAI | 이미지·자연어에서 재료와 수량을 구조화하고, 대화 안내를 생성 | 사진과 자유 형식 문장을 일관된 재료·조건 데이터로 바꾸기 위해 | 필요한 AI 처리 실패 시 안전한 오류·고정 안내를 반환 |
| TypeSafe Jev | 조건이 충분한지, 후보를 확정·수정·거절했는지 같은 **결정**을 분류 | 생성 모델의 자유로운 문장 대신, 상태 전이에 필요한 정해진 판단값을 얻기 위해 | confidence 미달·timeout이면 결정적 규칙으로 fallback |
| NeMo Guardrails | 프롬프트 인젝션, 내부 프롬프트·API 키 노출을 입력·출력·Tool 결과에서 차단 | 사용자 입력·검색 결과·Tool 결과가 안전 경계를 우회하지 못하게 하기 위해 | 자체 정규화 검사로 안전성 경계 유지 |
| ChromaDB | 레시피 후보 의미 검색과 재료 대체 가이드 보강 | 단순 키워드 일치만으로 놓칠 수 있는 유사 레시피와 대체 정보를 찾기 위해 | 내부 CSV 기반 추천을 계속 사용 |
| LangSmith | LangGraph의 단계 전이와 Tool 호출 trace 관찰 | 다단계 대화에서 어느 단계·도구에서 문제가 생겼는지 추적하기 위해 | trace만 생략하고 사용자 응답은 계속 처리 |

<p align="center">
  <img src="docs/images/planeat-jev-nemo-roles.png" alt="Jev와 NeMo Guardrails의 역할 비교 인포그래픽" width="100%">
</p>

<br>

### 참조 개념: Jev와 NeMo Guardrails

| 용어 | 쉬운 설명 |
| --- | --- |
| **Jev** | 문장을 만드는 AI가 아니라, “재료를 확정했는가?”, “조건이 충분한가?”처럼 다음 행동을 고르는 판단 서비스입니다. |
| **NeMo Guardrails** | AI 입력·출력에 안전 규칙을 적용하는 보호 장치입니다. PlanEat에서는 내부 프롬프트 공개나 API 키 노출 같은 위험한 내용을 차단합니다. |

특히 Jev와 NeMo Guardrails의 역할은 다릅니다. **Jev는 “다음에 무엇을 할지” 판단**하고, **NeMo Guardrails는 “이 내용을 보여줘도 안전한지” 검사**합니다. 따라서 Jev가 실패하면 규칙 기반 전이로, NeMo가 실패하면 기존 자체 안전성 검사로 각각 fallback합니다.

<br>

### 프롬프트 인젝션·비신뢰 입력 방어 시연

사용자 입력과 첨부 이미지는 모두 신뢰하지 않는 데이터로 취급합니다. 시스템 지시 공개를 요청하는 텍스트는 추천 흐름으로 전달하지 않고, 식단 추천에 필요한 재료·목적을 다시 묻는 안전한 안내로 바꿉니다. 재료를 판별할 수 없는 이미지도 후보 재료나 추천 조건으로 사용하지 않고, 사용자가 확인 가능한 냉장고·영수증 사진을 다시 요청합니다.

<p align="center">
  <strong>① 텍스트 프롬프트 인젝션 차단</strong><br>
  <img src="docs/images/prompt-injection-text-blocked.png" alt="시스템 지시사항 공개 요청을 거절하고 재료와 식단 목표를 묻는 화면" width="720"><br>
  시스템 지시사항 공개 요청을 거절하고, 필요한 입력만 안내
</p>

<p align="center">
  <strong>② 비신뢰 이미지 안전 재시도</strong><br>
  <img src="docs/images/untrusted-image-retry.png" alt="재료를 확인할 수 없는 이미지에 대해 냉장고 또는 영수증 사진을 다시 요청하는 화면" width="720"><br>
  이미지 속 텍스트·불명확한 내용을 재료로 채택하지 않고 재촬영 요청
</p>

> [!IMPORTANT]
> 두 번째 화면은 **이미지 입력을 신뢰하지 않는 처리**를 보여 주는 사례입니다. 재료를 식별하지 못한 이미지는 추천 입력으로 사용하지 않으며, 명시적인 텍스트 프롬프트 인젝션 차단은 첫 번째 화면과 같은 입력·출력 Guardrails가 담당합니다.

<br>


## 트러블슈팅

배포 이후 메모리 512MB 서버에서 레시피 추천 결과를 PDF로 생성하는 과정 중 서버가 종료되는 문제가 발생했습니다.

<p align="center">
  <img src="docs/images/planeat-troubleshooting-before-after.png" alt="PDF 생성 트러블슈팅 변경 전후 비교 인포그래픽" width="100%">
</p>

<br>

| 문제 | 원인 | 대응 |
| --- | --- | --- |
| PDF 생성 중 서버 종료 | 레시피 추출 단계에서 내부 벡터 DB와 CSV를 함께 참조하는 2중 구조로 동작한 뒤 PDF를 생성하면서 메모리 사용량이 급증 | 서버 메모리 증설은 비용 부담으로 보류하고, 배포 환경에서는 벡터 DB를 거치지 않고 **검증된 내부 CSV만 참조**하도록 전환 |

> **운영 판단:** 내부 회의 결과 서버 메모리를 올리는 것보다 사용자가 최종적으로 받는 **PDF 결과물의 안정적인 제공을 우선**하기로 했습니다. 따라서 배포 서버는 CSV 단일 참조로 추천한 뒤 PDF를 생성합니다. 벡터 DB는 메모리 여유가 있는 개발·확장 환경에서 선택적으로 활용할 수 있도록 분리합니다.

<br>

---

# 프로젝트 문서

> 아래 내용은 저장소를 실행하거나 API를 연동하는 개발자를 위한 문서입니다.

<br>

## 빠른 시작

### 준비물

- Python 3.11 이상
- [uv](https://docs.astral.sh/uv/)
- OpenAI API key — 이미지 인식·자연어 구조화·임시 추천 fallback에 필요

### 설치 및 실행

macOS/Linux:

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

`.env`의 `OPENAI_API_KEY`를 실제 키로 교체합니다. `.env`와 실제 키는 커밋하지 않습니다.

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Health check: <http://127.0.0.1:8000/health>

<br>

## Chat API

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/chat` | 세션별 대화와 식단 추천 처리 |
| `GET` | `/health` | 서버 상태 확인 |
| `GET` | `/pdfs/{filename}` | 선택한 식단 세트의 PDF 다운로드 |

### 요청 예시

```json
{
  "session_id": "session-001",
  "message": "두부 1모와 계란 4개로 20분 안에 만들 수 있는 다이어트 식단을 추천해줘.",
  "attachments": []
}
```

`attachments`는 생략할 수 있으며, 넣는 경우 `type: "image"`과 Base64 이미지 또는 이미지 URL을 사용합니다. 한 요청에는 최대 5개를 허용합니다.

### 응답 흐름

프런트엔드는 `status`와 `step`만으로 화면 전환을 판단합니다. 표시 문구와 데이터는 `response`, `questions`, `ingredients`, `data`에서 읽습니다.

| `status` | `step` | 의미 |
| --- | --- | --- |
| `NEED_MORE_INFO` | `IMAGE_INPUT` | 사진 또는 자연어 재료가 필요합니다. |
| `NEED_MORE_INFO` | `INGREDIENT_CONFIRM` | 이미지에서 찾은 재료 후보의 확인·수정이 필요합니다. |
| `NEED_MORE_INFO` | `CONDITION_INPUT` | 필수 식단 목적이 필요합니다. 조리 시간은 선택입니다. |
| `NEED_MORE_INFO` | `INPUT_REQUIREMENTS` | 이미지와 조건을 함께 받는 호환 응답입니다. |
| `SUCCESS` | `COMPLETED` | 추천이 준비됐습니다. `data.recipe_sets`에 2세트가 들어 있습니다. |
| `ERROR` | 없음 | 오류 메시지를 표시합니다. |

성공 응답의 `recipe_sets`는 정확히 2개이고, 각 세트는 정확히 5개 레시피를 가집니다. `shopping_list` 항목은 `ingredient`, `amount`만 포함합니다. 전체 스키마와 예시는 [Chat API 명세](docs/api/chat.md)를 참고하세요.

<br>

## 구성 요소와 설정

| 영역 | 역할 |
| --- | --- |
| FastAPI | `/chat`, `/health`, PDF 다운로드와 OpenAPI 제공 |
| ChatService | 요청 검증 뒤 세션·LLM·LangGraph·응답 DTO를 연결 |
| LangGraph | 대화 상태 전이와 완료 단계 Tool 실행 |
| Tool Hub | Recipe·Nutrition·Shopping·Guide Tool 결과 조합 |
| 내부 CSV / ChromaDB | 레시피 원본과 선택적 의미 검색 |
| NeMo Guardrails | 입력·LLM 출력·Tool 결과의 안전성 검사 |
| LangSmith | 선택적 비식별 실행 trace |

기본 Tool Hub는 내부 CSV를 사용합니다. `CHROMA_PERSIST_DIRECTORY`를 지정한 경우에만 사전 생성 Chroma 인덱스로 검색을 보강합니다. 외부 Tool Hub endpoint와 별도 Vision 서비스 endpoint는 아직 연동하지 않았으며, adapter와 Tool node가 교체 지점입니다. `TOOL_HUB_ENABLED=false`일 때만 OpenAI 기반 임시 추천 경로로 fallback합니다.

<br>

## LangGraph와 Tool Hub 상세

### 1. LangGraph Orchestrator

`ChatService`는 세션 데이터와 요청을 `ChatState`로 만들고, LangGraph가 다음 화면 단계와 Tool Hub 호출 여부를 결정합니다. 대화가 10개 메시지를 넘으면 오래된 메시지를 로컬 요약으로 압축하고 최근 2개를 유지합니다.

```mermaid
stateDiagram-v2
    [*] --> WAITING_IMAGE
    WAITING_IMAGE --> INGREDIENT_CONFIRM: 이미지에서 재료 후보 추출
    WAITING_IMAGE --> CONDITION_INPUT: 자연어 재료가 확정됨
    INGREDIENT_CONFIRM --> INGREDIENT_CONFIRM: 수정 또는 재확인
    INGREDIENT_CONFIRM --> WAITING_IMAGE: 후보 거절
    INGREDIENT_CONFIRM --> CONDITION_INPUT: 후보 확정, 조건 부족
    INGREDIENT_CONFIRM --> COMPLETED: 후보 확정, 조건 충족
    CONDITION_INPUT --> CONDITION_INPUT: 조건 부족
    CONDITION_INPUT --> COMPLETED: 조건 충족
    COMPLETED --> TOOL_HUB: 확정 재료와 조건만 전달
    TOOL_HUB --> [*]
```

| 내부 상태 | API `step` | 처리 내용 |
| --- | --- | --- |
| `WAITING_IMAGE` | `IMAGE_INPUT` | 이미지 또는 자연어 재료 입력을 기다립니다. |
| `WAITING_INGREDIENT_CONFIRM` | `INGREDIENT_CONFIRM` | 이미지 인식 후보를 확인·수정·거절합니다. |
| `WAITING_CONDITIONS` | `CONDITION_INPUT` | 필수 식단 목적을 수집합니다. 조리 시간은 선택입니다. |
| `COMPLETED` | `COMPLETED` | Tool Hub를 실행하고 추천 결과를 응답 DTO로 변환합니다. |

Tool Hub 호출 전 `ToolRequest`는 `session_id`, `tool_name`, `confirmed_ingredients`, `user_conditions`로 정규화됩니다. 확정 재료 또는 조건이 하나라도 없으면 요청을 만들지 않습니다. Tool 결과는 다시 DTO 검증과 NeMo Guardrails 검사를 통과해야 `/chat` 응답에 반영됩니다.

### 2. 로컬 Tool Hub 처리 흐름

현재 Tool Hub는 외부 HTTP 서비스가 아닙니다. `PlanEatToolHub`가 프로젝트 안에서 `Recipe Tool`, `Nutrition Tool`, `Shopping Tool`, `Guide Tool`을 조합하는 로컬 구현이며, 외부 endpoint로 바꿀 수 있도록 `ToolRequestExecutor` 인터페이스 뒤에 분리돼 있습니다.

```mermaid
flowchart TD
    INPUT[확정 재료<br/>사용자 조건] --> SEARCH[Recipe Tool<br/>후보 검색·필터·정렬]
    SEARCH --> PAIR[Planning Tool<br/>메인·반찬 조합]
    PAIR --> SELECT[Jev 선택<br/>규칙 기반 fallback]
    SELECT --> EXCLUDE[제외 재료 판단<br/>필요 시 재계획]
    EXCLUDE --> SHOPPING[Shopping Tool<br/>부족 재료·장보기]
    SHOPPING --> NUTRITION[Nutrition Tool]
    NUTRITION --> ADAPT[2개 세트 × 5개 레시피 DTO 변환]
    GUIDE[Guide Tool<br/>대체재 검색] --> SHOPPING
```

1. **Recipe Tool** — 기본 카탈로그인 `data/chroma/COOKRCP01_INTEGRITY_CHECKED_V13.csv`에서 후보를 읽습니다. 확정 재료 일치 여부, 보유 주재료 수, 부족 재료 수, 조리 시간, 고단백·다이어트·채식 조건을 반영해 정렬합니다. Chroma 경로가 설정되면 의미 검색으로 후보를 보강하고, 연결에 실패하면 CSV 검색을 계속 사용합니다.
2. **Planning Tool** — 서로 다른 레시피가 최소 10개 필요합니다. 상위 후보에서 메인과 반찬 조합을 만들고, 정확히 5개 식단 세트를 고릅니다. 현재 `/chat` 호환 계층에서는 이 5개 메인·반찬 세트의 레시피를 평면화·재정렬해 `BEST_MATCH`, `MORE_OPTIONS` 두 세트에 각각 5개씩 반환합니다.
3. **Jev 선택과 fallback** — TypeSafe Jev가 활성화되어 있고 신뢰도 기준을 통과하면 사용자 조건에 맞는 조합 5개를 선택합니다. API 키가 없거나 timeout·형식 오류·낮은 신뢰도가 발생하면 결정적 규칙이 앞선 후보 5개를 선택해 흐름을 유지합니다.
4. **제외 재료 처리** — 사용자가 제외한 재료는 생략 가능·대체 가능·필수로 판정합니다. 생략 가능은 장보기에서 빼고, 대체 가능은 Guide Tool의 첫 대체재를 사용합니다. 필수 재료이거나 대체재가 없으면 해당 재료가 포함되지 않은 후보로 다시 계획합니다.
5. **Shopping Tool** — 각 레시피의 보유 재료와 부족 재료를 비교해 `missing_ingredients`와 `shopping_list`를 만듭니다. 중요도는 필수 → 권장 → 대체 가능 → 생략 가능 순이며, 화면에는 최대 5개 항목만 노출합니다. 생략 가능 재료는 장보기 목록에서 제외합니다.
6. **Nutrition Tool** — 카탈로그의 영양 정보를 우선 사용합니다. 값이 없는 레시피만 알려진 기본 재료의 1인분 근사치로 계산하고, 내부적으로 출처를 구분합니다.
7. **Guide Tool** — 부족 재료에 대해 대체재·활용법을 검색해 Shopping Tool을 보조합니다. Chroma가 설정되지 않았거나 검색에 실패해도 추천·영양·장보기는 계속 동작합니다.

### 3. 실패와 fallback 정책

| 상황 | 동작 |
| --- | --- |
| `TOOL_HUB_ENABLED=false` | Tool Hub 대신 OpenAI 임시 추천 경로를 사용합니다. |
| Chroma 초기화·검색 실패 | 빈 가이드 검색기 또는 CSV Recipe Tool로 fallback합니다. |
| Jev 비활성·timeout·저신뢰 | 보수적인 결정적 규칙으로 세트 선택과 제외 재료 판단을 수행합니다. |
| 최소 10개 레시피 또는 5개 세트를 만들 수 없음 | 안전한 Tool 오류로 변환하며 `/chat`은 `500 ERROR`을 반환합니다. |
| Tool 결과 DTO·Guardrails 검증 실패 | 결과를 사용자에게 노출하지 않고 `500 ERROR`을 반환합니다. |

<br>

## 외부·보조 서비스

서비스마다 담당 범위와 장애 시 영향이 다릅니다. OpenAI와 TypeSafe Jev는 선택적으로 외부 API를 호출하며, NeMo Guardrails·CSV·기본 가이드 검색 fallback은 애플리케이션 내부에서 동작합니다.

| 서비스 | 분류 | PlanEat에서의 역할 | 비활성화·실패 시 |
| --- | --- | --- | --- |
| OpenAI | 외부 AI API | 이미지·자연어 재료 구조화, 안내 문구 생성, Tool Hub 비활성 시 임시 추천 | 해당 AI 처리가 필요한 요청은 안전한 오류 또는 고정 안내로 처리합니다. |
| TypeSafe Jev | 외부 판단 API | 자연어를 정해진 선택값으로 분류해 상태 전이·식단 선택을 보조 | 결정적 규칙으로 fallback하고 API 계약은 유지합니다. |
| NeMo Guardrails | 로컬 안전성 라이브러리 | 입력·LLM 출력·Tool 결과의 위험 패턴 검사 | 기존 `app/core/safety.py` 검사만 유지합니다. |
| ChromaDB | 선택적 로컬 벡터 저장소 | 레시피 후보 의미 검색과 부족 재료 가이드 검색 | 내부 CSV와 빈 가이드 검색기로 계속 동작합니다. |
| LangSmith | 선택적 외부 관측성 서비스 | LangGraph 실행 trace 확인 | trace만 생략하며 `/chat` 흐름에는 영향이 없습니다. |

### OpenAI: 구조화 추출과 임시 추천

OpenAI는 최종 추천 데이터의 기본 source가 아닙니다. 기본 설정에서 추천 카드는 내부 CSV 기반 Tool Hub가 만듭니다. OpenAI는 다음처럼 **구조화가 필요한 경계**에 사용합니다.

- 냉장고·영수증 이미지와 자연어에서 재료명·수량을 추출합니다.
- 자연어 재료 입력을 확정 재료 형태로 정리하고, 부족한 정보를 묻는 안내 문구를 만듭니다.
- `TOOL_HUB_ENABLED=false`일 때에만 2세트 × 5개 형식의 임시 추천 fallback을 생성합니다.

이미지와 사용자 메시지는 외부 AI 처리 대상이 될 수 있으므로 운영 전 제3자 전송·보관 정책을 검토해야 합니다. API 키는 `OPENAI_API_KEY`로만 주입하며, 코드·fixture·로그에 기록하지 않습니다.

### TypeSafe Jev: 문장 생성이 아닌 결정 보조

Jev는 사용자에게 보여 줄 답변을 만드는 모델이 아니라, 미리 정의한 선택지와 confidence를 반환해 LangGraph가 안전하게 분기하도록 돕는 판단 서비스입니다. `TYPESAFE_JEV_ENABLED=true`와 API 키가 모두 있을 때만 호출합니다.

| 적용 위치 | Jev가 판단하는 것 | 후속 처리 |
| --- | --- | --- |
| 조건 수집 | 식단 목적이 충분한지 (`ready` / `needs_more_info`) | `CONDITION_INPUT` 유지 또는 완료 단계 진행 |
| 재료 후보 확인 | `confirmed` / `edited` / `rejected` / `unclear` | 후보 확정·수정·폐기·재확인 |
| 완료 후 피드백 | 재추천, 조건 갱신, 세트 선택의 의도 | 기존 조건을 갱신하거나 검증된 세트 선택 처리 |
| Tool Hub 식단 계획 | 메인·반찬 후보 중 최종 5개 선택 | 정확히 5개가 선택될 때만 반영 |
| 제외 재료 | 생략 가능·대체 가능·필수 | 장보기 수정 또는 레시피 재계획 |

`TYPESAFE_JEV_MIN_CONFIDENCE`의 기본값은 `0.8`입니다. API 오류, timeout, 응답 형식 오류, confidence 미달 또는 정확히 5개를 고르지 못한 경우에는 Jev 결과를 사용하지 않고 결정적 fallback을 적용합니다. 따라서 Jev가 꺼져 있어도 `/chat`의 `status`, `step`, 응답 스키마는 바뀌지 않습니다.

활성화 시 현재 사용자 메시지와 식단 계획에 필요한 요약 데이터가 TypeSafe API로 전송될 수 있습니다. 사용자 메시지·이미지 원문과 API 키를 로그·fixture·문서에 남기지 않으며, 운영 전 개인정보 및 제3자 처리 정책을 검토해야 합니다.

### NeMo Guardrails: 대화 제어가 아닌 안전성 경계

NeMo Guardrails는 LangGraph의 상태 전이를 결정하지 않고, `IORails.check_async()` 기반의 결정적 정규식 검사만 수행합니다. 별도의 NVIDIA API 키나 추가 모델 호출은 필요하지 않습니다.

```text
사용자 메시지
  → app/core/safety.py 정규화 검사
  → NeMo input rail
  → LangGraph · Tool Hub
  → NeMo output rail (ToolResult)
  → NeMo output rail (최종 안내 문구)
  → ChatResponse
```

- **입력 검사**: 이전 지시 무시, 시스템·개발자 프롬프트 공개, prompt injection, jailbreak 같은 패턴을 차단합니다. NFKC 정규화와 구분 문자 제거를 하는 자체 검사도 함께 적용해 표기 우회를 줄입니다.
- **출력·Tool 결과 검사**: 시스템·개발자 지시, 내부 프롬프트, API 키 등 내부 정보가 응답으로 새지 않도록 막습니다. 현재 Tool Hub는 별도 async 계약을 쓰므로 구조적 tool-call rail 대신 output rail로 검사합니다.
- **차단과 장애 처리**: 프롬프트 공개 요청은 FE 호환을 위해 안전한 `NEED_MORE_INFO / CONDITION_INPUT` 안내로 바꾸고, 그 외 위험 입력은 `400 ERROR`으로 처리합니다. 출력·Tool 결과 차단은 원문을 노출하지 않고 `500 ERROR`으로 처리합니다. NeMo 설정·엔진 장애 때는 오류 유형만 로그에 남기고 기존 자체 검사로 fallback합니다.

기본값은 `NEMO_GUARDRAILS_ENABLED=true`이며, 설정 경로는 `NEMO_GUARDRAILS_CONFIG_PATH=guardrails`입니다. 긴급 호환성 확인 외에는 비활성화하지 않습니다.

### ChromaDB와 LangSmith

- **ChromaDB**는 외부 Recipe API가 아니라 선택적 로컬 인덱스입니다. `CHROMA_PERSIST_DIRECTORY`가 있을 때만 Recipe Tool의 후보 검색과 Guide Tool의 대체재 검색에 사용합니다. 기동 중 전체 재색인은 무거울 수 있으므로 `CHROMA_AUTO_INDEX_ON_STARTUP=false`가 기본값입니다.
- **LangSmith**는 `LANGSMITH_TRACING=true`와 API 키가 있을 때만 LangGraph 실행을 관찰합니다. 사용자 메시지·이미지·대화 요약의 입력/출력은 숨기고, 해시된 세션 ID, 상태, 첨부 수, Tool 활성화 여부 같은 비식별 metadata만 전송합니다. tracer 초기화나 전송이 실패해도 추천 요청은 계속 처리됩니다.

전체 환경 변수 예시는 [`.env.example`](.env.example)에 있습니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 없음 | Vision·LLM 연동 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 기본 OpenAI 모델 |
| `TOOL_HUB_ENABLED` | `true` | 로컬 Tool Hub 사용 여부 |
| `CHROMA_PERSIST_DIRECTORY` | 없음 | 선택적 사전 생성 Chroma 인덱스 경로 |
| `CHROMA_AUTO_INDEX_ON_STARTUP` | `false` | 기동 시 CSV 전체 재색인 여부 |
| `NEMO_GUARDRAILS_ENABLED` | `true` | NeMo Guardrails 사용 여부 |
| `TYPESAFE_JEV_ENABLED` | `false` | TypeSafe Jev 선택 연동 여부 |
| `LANGSMITH_TRACING` | `false` | LangGraph tracing 여부 |
| `PDF_STORAGE_DIRECTORY` | `output/pdf` | 생성 PDF 저장 경로 |

CSV를 변경해 Chroma 인덱스를 갱신할 때만 아래 명령을 실행합니다. 일반 개발·데모 환경에서는 `CHROMA_AUTO_INDEX_ON_STARTUP=false`를 유지하세요.

```bash
PYTHONPATH=. uv run python scripts/index_recipe_catalog.py --persist-directory data/chroma
```

<br>

## 프로젝트 구조와 테스트

```text
app/
├── api/v1/endpoints/chat/  HTTP 입력·응답과 PDF endpoint
├── schemas/                Pydantic 요청·응답 DTO
├── agent/                  LangGraph 상태·전이·Tool node
├── services/               세션·대화·PDF 처리
├── integrations/           LLM·Vision·CSV·Chroma 등 외부 연결
└── repositories/           세션·데이터 접근
docs/                       API·아키텍처·연동·운영 문서
mocks/chat/                 FE 개발용 요청·응답 fixture
prompts/                    버전 관리되는 LLM 운영 프롬프트
guardrails/                 NeMo Guardrails 설정
tests/                      단위·API·통합 테스트
```

macOS/Linux:

```bash
PYTHONPATH=. uv run pytest
git diff --check
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "."
uv run pytest
git diff --check
```

<br>

## 관련 문서

- [Chat API 명세](docs/api/chat.md)
- [데이터 흐름](docs/architecture/data-flow.md)
- [구현 개요](docs/architecture/implementation-overview.md)
- [Tool Hub 연동 상태](docs/integrations/tool-hub-readiness.md)
- [Tool Hub 상세 연동](docs/integrations/tool_hub_integration.md)
- [NeMo Guardrails](docs/integrations/guardrails.md)
- [TypeSafe Jev](docs/integrations/jev.md)
- [LangSmith 연동](docs/integrations/langsmith.md)
- [온보딩](docs/operations/onboarding.md)
- [전체 문서 안내](docs/README.md)
- [FE fixture 안내](mocks/chat/README.md)
