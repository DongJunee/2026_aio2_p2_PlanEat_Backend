"""OpenAI Vision 추출기의 식품 필터 단위 테스트입니다."""

import asyncio
import json
from types import SimpleNamespace

from app.integrations.vision import openai_vision
from app.integrations.vision.openai_vision import OpenAIVisionIngredientExtractor


def test_vision_extractor_drops_non_food_receipt_items(monkeypatch) -> None:
    payload = {
        "ingredients": [
            {"name": "커다란 응원", "amount": "1개", "is_food": False},
            {"name": "우유", "amount": "900ml", "is_food": True},
        ]
    }

    class FakeResponses:
        async def create(self, **kwargs: object) -> SimpleNamespace:
            required = kwargs["text"]["format"]["schema"]["properties"]["ingredients"]["items"]["required"]  # type: ignore[index]
            assert "is_food" in required
            return SimpleNamespace(output_text=json.dumps(payload, ensure_ascii=False))

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    monkeypatch.setattr(openai_vision, "AsyncOpenAI", lambda api_key, **_: FakeClient(api_key))

    result = asyncio.run(
        OpenAIVisionIngredientExtractor(api_key="test-key").extract(
            ({"type": "image", "data": "data:image/png;base64,AAAA"},)
        )
    )

    assert [item.model_dump() for item in result] == [{"name": "우유", "amount": "900ml"}]
