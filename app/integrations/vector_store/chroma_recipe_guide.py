"""ChromaDB에 Recipe Guide 문서를 적재·검색하는 Tool Hub 어댑터입니다."""

import asyncio
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.tools.tool_models import IngredientGuide


@dataclass(frozen=True)
class GuideDocument:
    """ChromaDB에 적재할, 검토된 재료 가이드 원문입니다."""

    document_id: str
    ingredient: str
    content: str
    source: str
    substitutes: tuple[str, ...] = ()
    storage_tip: str | None = None
    flavor_note: str | None = None


class ChromaRecipeGuideRetriever:
    """주입받은 Chroma Collection으로 재료별 가이드 문서를 검색합니다."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    @classmethod
    def open(cls, *, persist_directory: str | Path, collection_name: str = "recipe_guides") -> "ChromaRecipeGuideRetriever":
        """지정한 로컬 경로의 Chroma Collection을 엽니다.

        Chroma는 실제 실행 시점에만 import해, 일반 단위 테스트가 로컬 DB를 만들지 않게 한다.
        """

        try:
            import chromadb
        except ImportError as error:  # pragma: no cover - 의존성 설치 환경에서만 발생
            raise RuntimeError("ChromaDB 의존성이 설치되어 있지 않습니다.") from error
        client = chromadb.PersistentClient(path=str(persist_directory))
        return cls(client.get_or_create_collection(name=collection_name))

    async def upsert(self, documents: Iterable[GuideDocument]) -> None:
        """검토된 문서만 명시적 호출로 적재합니다."""

        items = list(documents)
        if not items:
            return
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[item.document_id for item in items],
            documents=[item.content for item in items],
            metadatas=[
                {
                    "ingredient": item.ingredient,
                    "source": item.source,
                    "substitutes": json.dumps(item.substitutes, ensure_ascii=False),
                    "storage_tip": item.storage_tip or "",
                    "flavor_note": item.flavor_note or "",
                }
                for item in items
            ],
        )

    async def retrieve(self, ingredient: str) -> IngredientGuide | None:
        """가장 가까운 한 문서를 사용자 노출 가능한 가이드 DTO로 정규화합니다."""

        result = await asyncio.to_thread(
            self._collection.query,
            query_texts=[ingredient],
            n_results=1,
            include=["documents", "metadatas"],
        )
        return _guide_from_chroma_result(ingredient, result)


def load_markdown_documents(directory: str | Path) -> list[GuideDocument]:
    """간단한 Markdown front matter를 검토된 Chroma 적재 문서로 읽습니다.

    지원하는 front matter 키는 ``ingredient``, ``source``, ``substitutes``,
    ``storage_tip``, ``flavor_note``다. PDF는 추출·검토 후 같은 문서 형식으로 변환해
    적재하며, 원본 PDF를 무검증으로 벡터 DB에 넣지 않는다.
    """

    root = Path(directory)
    documents: list[GuideDocument] = []
    for path in sorted(root.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        metadata, content = _split_front_matter(raw)
        ingredient = metadata.get("ingredient", "").strip()
        if not ingredient or not content.strip():
            continue
        substitutes = tuple(
            item.strip() for item in metadata.get("substitutes", "").split(",") if item.strip()
        )
        documents.append(
            GuideDocument(
                document_id=path.stem,
                ingredient=ingredient,
                content=content.strip(),
                source=metadata.get("source", path.name),
                substitutes=substitutes,
                storage_tip=metadata.get("storage_tip") or None,
                flavor_note=metadata.get("flavor_note") or None,
            )
        )
    return documents


def _guide_from_chroma_result(ingredient: str, result: object) -> IngredientGuide | None:
    if not isinstance(result, Mapping):
        return None
    documents = result.get("documents")
    metadatas = result.get("metadatas")
    if not isinstance(documents, list) or not documents or not isinstance(documents[0], list) or not documents[0]:
        return None
    content = documents[0][0]
    metadata: Mapping[str, object] = {}
    if isinstance(metadatas, list) and metadatas and isinstance(metadatas[0], list) and metadatas[0]:
        candidate = metadatas[0][0]
        if isinstance(candidate, Mapping):
            metadata = candidate
    if not isinstance(content, str):
        return None
    return IngredientGuide(
        ingredient=_string(metadata.get("ingredient")) or ingredient,
        usage_tip=content,
        substitutes=_string_list(metadata.get("substitutes")),
        storage_tip=_string(metadata.get("storage_tip")) or None,
        flavor_note=_string(metadata.get("flavor_note")) or None,
        sources=[_string(metadata.get("source"))] if _string(metadata.get("source")) else [],
    )


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    header, separator, content = raw[len("---\n") :].partition("\n---\n")
    if not separator:
        return {}, raw
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        key, delimiter, value = line.partition(":")
        if delimiter and key.strip():
            metadata[key.strip()] = value.strip()
    return metadata, content


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.split(",")]
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
