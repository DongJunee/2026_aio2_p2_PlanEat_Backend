"""레시피 PDF의 이미지 URL 보안 경계와 렌더링 입력을 검증합니다."""

import asyncio
import base64
import re
from datetime import datetime, timezone

import httpx

from app.schemas.chat import MissingIngredient, Nutrition, Recipe
from app.services.pdf_service import (
    _build_pdf_filename,
    _embed_recipe_image,
    _is_allowed_recipe_image_url,
    _tip_text,
)


def _recipe(image: str | None) -> Recipe:
    """이미지 변환 테스트에 필요한 최소 레시피를 만듭니다."""

    return Recipe(
        recipe_id="506",
        title="새우 두부 계란찜",
        image=image,
        cook_time=0,
        cooking_steps=["재료를 손질합니다.", "팬에 넣고 익힙니다."],
        nutrition=Nutrition(calories=0, protein=0, carbohydrate=0, fat=0),
    )


def test_recipe_pdf_allows_only_official_catalog_image_urls() -> None:
    """임의 호스트가 PDF 생성 중 요청되는 SSRF 경로가 되지 않도록 막습니다."""

    assert _is_allowed_recipe_image_url(
        "https://www.foodsafetykorea.go.kr/uploadimg/cook/10_00028_2.png"
    )
    assert not _is_allowed_recipe_image_url("https://example.com/recipe.png")
    assert not _is_allowed_recipe_image_url("file:///etc/passwd")


def test_recipe_pdf_embeds_allowed_image_url_as_data_uri() -> None:
    """허용된 카탈로그 이미지는 PDF 스레드에서 바로 그릴 수 있는 data URI가 됩니다."""

    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
        "p9Lh7wAAAABJRU5ErkJggg=="
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.foodsafetykorea.go.kr"
        return httpx.Response(200, headers={"content-type": "image/png"}, content=image_bytes)

    async def embed() -> Recipe:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _embed_recipe_image(
                client,
                _recipe("https://www.foodsafetykorea.go.kr/uploadimg/cook/10_00028_2.png"),
            )

    embedded = asyncio.run(embed())

    assert embedded.image is not None
    assert embedded.image.startswith("data:image/png;base64,")


def test_recipe_pdf_filename_has_timestamp_unique_suffix_and_set_id(monkeypatch) -> None:
    """발급 시간·충돌 방지 값·세트 ID가 PDF 파일명에 모두 포함됩니다."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return cls(2026, 10, 9, 7, 30, 45, tzinfo=timezone.utc)

    monkeypatch.setattr("app.services.pdf_service.datetime", FixedDatetime)
    filename = _build_pdf_filename("BEST_MATCH")

    assert re.fullmatch(r"20261009T073045Z-[0-9a-f]{8}-BEST_MATCH\.pdf", filename)


def test_recipe_pdf_tip_prefers_optional_or_alternative_ingredients() -> None:
    recipe = _recipe(None).model_copy(
        update={
            "missing_ingredients": [
                MissingIngredient(name="간장", importance="필수"),
                MissingIngredient(name="레몬즙", importance="생략 가능", alternative="식초"),
                MissingIngredient(name="후추", importance="대체 가능"),
            ]
        }
    )

    assert _tip_text(recipe) == (
        "선택 재료: 레몬즙 · 대체 가능: 후추 · 대체안: 레몬즙 → 식초"
    )


def test_recipe_pdf_tip_does_not_repeat_required_ingredient() -> None:
    recipe = _recipe(None).model_copy(
        update={
            "missing_ingredients": [
                MissingIngredient(name="간장", importance="필수"),
            ]
        }
    )

    assert _tip_text(recipe) == (
        "선택 재료나 대체 가능한 재료 정보가 없습니다. 장보기 목록을 확인해 주세요."
    )


def test_recipe_pdf_tip_uses_source_optional_and_substitutable_fields() -> None:
    """보유 여부와 무관하게 원본 CSV의 선택·대체 재료를 팁에 표시합니다."""

    recipe = _recipe(None).model_copy(
        update={
            "optional_ingredients": ["양파"],
            "substitutable_ingredients": ["부추"],
            "missing_ingredients": [
                MissingIngredient(name="간장", importance="필수"),
            ],
        }
    )

    assert _tip_text(recipe) == "선택 재료: 양파 · 대체 가능: 부추"
