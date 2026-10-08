"""BE2 도구 입력·Vision 후보의 재료명과 중복을 보수적으로 정규화합니다."""

from collections.abc import Iterable

from app.agent.tools.be2_models import ToolIngredient


class IngredientValidationTool:
    """확정 여부를 판단하지 않고 명확한 표기 차이와 중복만 처리합니다."""

    _ALIASES = {
        "달걀": "계란",
        "계란": "계란",
        "닭가슴": "닭가슴살",
        "파": "대파",
    }

    def normalize(self, ingredients: Iterable[ToolIngredient]) -> list[ToolIngredient]:
        """공백을 정리하고 동일 재료를 하나로 합칩니다.

        재료 후보가 사용자 확인 전이라는 사실은 변경하지 않는다. 모델이 불확실한
        ``수량 미상``과 명시된 수량이 함께 있으면 명시된 수량만 우선한다.
        """

        normalized: dict[str, ToolIngredient] = {}
        for ingredient in ingredients:
            name = " ".join(ingredient.name.split())
            if not name:
                continue
            canonical_name = self._ALIASES.get(name, name)
            candidate = ToolIngredient(name=canonical_name, amount=" ".join(ingredient.amount.split()))
            existing = normalized.get(canonical_name)
            if existing is None or (existing.amount == "수량 미상" and candidate.amount != "수량 미상"):
                normalized[canonical_name] = candidate
        return list(normalized.values())
