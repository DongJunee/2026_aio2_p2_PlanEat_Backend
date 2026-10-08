"""레시피의 열량과 탄수화물·단백질·지방을 정규화하는 Tool입니다."""

from dataclasses import dataclass

from app.agent.tools.be2_models import CatalogRecipe, NutritionValues


@dataclass(frozen=True)
class NutritionResult:
    """계산된 영양 정보와 데이터 출처입니다."""

    values: NutritionValues
    source: str


class NutritionTool:
    """공급원 영양 정보를 우선하고, 없을 때만 제한적인 로컬 추정치를 사용합니다."""

    # 각 항목은 레시피 1인분에 흔히 쓰는 기본 분량의 근사치다. 원재료의 정확한 g 수량이
    # 없는 공개 API 결과를 0으로 반환하는 것보다 낫지만, source를 구분해 표시한다.
    _DEFAULT_INGREDIENT_NUTRITION: dict[str, NutritionValues] = {
        "두부": NutritionValues(calories=180, protein=18, carbohydrate=5, fat=10),
        "계란": NutritionValues(calories=70, protein=6, carbohydrate=0, fat=5),
        "닭가슴살": NutritionValues(calories=165, protein=31, carbohydrate=0, fat=4),
        "양배추": NutritionValues(calories=25, protein=1, carbohydrate=6, fat=0),
        "당근": NutritionValues(calories=30, protein=1, carbohydrate=7, fat=0),
        "양파": NutritionValues(calories=40, protein=1, carbohydrate=9, fat=0),
        "밥": NutritionValues(calories=210, protein=4, carbohydrate=46, fat=0),
    }

    def calculate(self, recipe: CatalogRecipe) -> NutritionResult:
        """Recipe Source 영양 정보 또는 알려진 재료의 기본 분량 합계를 반환합니다."""

        if recipe.nutrition is not None:
            return NutritionResult(values=recipe.nutrition, source=recipe.source)

        totals = {"calories": 0.0, "protein": 0.0, "carbohydrate": 0.0, "fat": 0.0}
        known_count = 0
        for ingredient in recipe.ingredients:
            facts = self._DEFAULT_INGREDIENT_NUTRITION.get(ingredient.name)
            if facts is None:
                continue
            known_count += 1
            totals["calories"] += float(facts.calories)
            totals["protein"] += float(facts.protein)
            totals["carbohydrate"] += float(facts.carbohydrate)
            totals["fat"] += float(facts.fat)

        return NutritionResult(
            values=NutritionValues(**totals),
            source="estimated:default-serving" if known_count else "estimated:no-data",
        )
