import asyncio
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any

# Mock del índice de Pinecone para testing
class MockPineconeIndex:
    async def upsert(self, vectors: List[Dict]):
        # Simula la inserción en la base de datos
        return {"upserted_count": len(vectors)}

    async def query(self, vector: List[float], top_k: int, filter: Dict = None):
        # Simula una consulta con filtro
        return {"matches": []}

class IngestionPipeline:
    def __init__(self, index: MockPineconeIndex, dimension: int = 8):
        self.index = index
        self.dimension = dimension

    def _embed(self, text: str) -> List[float]:
        # Embedding simulado; reemplazar por un modelo real
        return [0.1] * self.dimension

    def create_metadata(self, doc_text: str, category: str, author: str) -> Dict[str, Any]:
        return {
            "category": category,
            "author": author,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "char_count": len(doc_text),
        }

    async def process_and_upsert_batches(self, documents: List[Dict], batch_size: int = 100) -> int:
        total = 0
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]

            vectors = [
                {
                    "id": str(uuid.uuid4()),
                    "values": self._embed(doc["text"]),
                    "metadata": self.create_metadata(doc["text"], doc["category"], doc["author"]),
                }
                for doc in batch
            ]

            response = await self.index.upsert(vectors=vectors)
            total += response["upserted_count"]
        return total

    async def search_by_category(self, query_vector: List[float], category: str):
        return await self.index.query(
            vector=query_vector,
            top_k=5,
            filter={"category": {"$eq": category}},
        )

async def main():
    index = MockPineconeIndex()
    pipeline = IngestionPipeline(index)

    docs = [
        {"text": "Contenido A", "category": "legal", "author": "User1"},
        {"text": "Contenido B", "category": "hr", "author": "User2"},
        {"text": "Contenido Legal 2", "category": "legal", "author": "User3"},
        {"text": "Contenido finanzas 1", "category": "finanzas", "author": "User4"},
        {"text": "Contenido finanzas 2", "category": "finanzas", "author": "User5"},
    ]

    # 1. Ejecutar ingesta
    total = await pipeline.process_and_upsert_batches(docs, batch_size=2)
    print(f"Vectores insertados: {total}")

    # 2. Ejecutar búsqueda filtrada
    query_vector = pipeline._embed("consulta sobre temas legales")
    results = await pipeline.search_by_category(query_vector, category="legal")
    print(f"Resultados: {results}")

if __name__ == "__main__":
    asyncio.run(main())
