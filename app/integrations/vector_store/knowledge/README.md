# Recipe Guide Knowledge

이 디렉터리에는 검토가 끝난 재료 활용·대체·보관·맛 변화 정보를 Markdown으로 둡니다.
`ChromaRecipeGuideRetriever.upsert(load_markdown_documents(...))`를 명시적으로 실행해
ChromaDB에 적재합니다. 원본 PDF는 텍스트 추출과 출처 검토를 거쳐 Markdown으로 정규화한 뒤
넣습니다.

각 문서는 아래 형식을 사용합니다.

```markdown
---
ingredient: 재료명
source: 검토한 출처 또는 문서명
substitutes: 대체재1, 대체재2
storage_tip: 짧은 보관 방법
flavor_note: 대체 시 맛 변화
---

재료 활용법과 조리 시 주의점을 작성합니다.
```
