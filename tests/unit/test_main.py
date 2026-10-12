"""애플리케이션 시작 시 선택적 Chroma 인덱스 준비를 검증합니다."""

import asyncio

from app import main
from app.core.config import Settings


def test_lifespan_indexes_recipe_catalog_when_chroma_is_configured(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_index(**kwargs: object) -> int:
        calls.append(kwargs)
        return 3

    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(
            chroma_persist_directory="/tmp/planeat-chroma",
            chroma_recipe_collection_name="recipes-test",
            tool_hub_catalog_path="/tmp/recipes.csv",
            chroma_auto_index_on_startup=True,
        ),
    )
    monkeypatch.setattr(main, "index_csv_recipe_catalog", fake_index)

    async def run_lifespan() -> None:
        async with main.lifespan(main.app):
            await asyncio.sleep(0)

    asyncio.run(run_lifespan())

    assert calls == [
        {
            "persist_directory": "/tmp/planeat-chroma",
            "collection_name": "recipes-test",
            "catalog_path": "/tmp/recipes.csv",
        }
    ]


def test_lifespan_skips_indexing_without_chroma_configuration(monkeypatch) -> None:
    called = False

    async def fake_index(**kwargs: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(main, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(main, "index_csv_recipe_catalog", fake_index)

    async def run_lifespan() -> None:
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_lifespan())

    assert called is False
