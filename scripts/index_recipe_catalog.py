"""내부 CSV 레시피 카탈로그를 Chroma 검색 인덱스로 적재합니다."""

from __future__ import annotations

import argparse
import asyncio

from app.integrations.vector_store.chroma_recipe_catalog import index_csv_recipe_catalog


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CSV 레시피를 Chroma에 적재합니다.")
    parser.add_argument("--persist-directory", required=True)
    parser.add_argument("--collection-name", default="recipe_catalog")
    parser.add_argument("--catalog-path")
    return parser.parse_args()


async def _main() -> None:
    args = _arguments()
    count = await index_csv_recipe_catalog(
        persist_directory=args.persist_directory,
        collection_name=args.collection_name,
        catalog_path=args.catalog_path,
    )
    print(f"{count} recipes indexed in {args.collection_name}.")


if __name__ == "__main__":
    asyncio.run(_main())
