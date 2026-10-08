# BE2 Tool Hub 결과 계약

BE2의 `PlanEatToolHub.execute(ToolRequest)`는 기존 `ToolResult`를 다음처럼 반환한다.

```json
{
  "result": {
    "response": "사용자에게 보일 완료 안내 문구",
    "data": { "recipe_sets": ["정확히 두 세트, 세트당 다섯 레시피"] }
  },
  "source_metadata": {
    "recipe_sources": ["..."],
    "recipe_guide_sources": ["..."],
    "recipe_count": 10
  },
  "error": null
}
```

`result.data`는 기존 `app.schemas.chat.RecommendationData`로 바로 검증 가능하다.
통합 시 BE1은 `ToolResult.error`가 없을 때 `result.response`와 `result.data`를 사용하고,
실패 시 기존 오류 정책을 적용한다. 이 문서는 BE1 소스·외부 Chat API를 바꾸지 않는다.

Vision은 추천 ToolRequest와 분리한다. `OpenAIVisionIngredientExtractor.extract()`의 결과는
사용자 확인 전 후보이므로 Recipe·Nutrition·Shopping·RAG 실행에 전달하면 안 된다.
