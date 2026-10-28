"""Recuperador híbrido: BM25 (léxico, en memoria) + Pinecone (semántico), fusionados con RRF.

    python rag.py "¿cómo evito el problema N+1?"
    python rag.py --mode bm25 "setMaxResults paginación"
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import Literal

from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from config import EMBEDDING_MODEL, TEXT_KEY, Settings, get_settings
from ingest import DEFAULT_DOCS_DIR, load_documents

Mode = Literal["hybrid", "bm25", "vector"]
MODES: tuple[Mode, ...] = ("hybrid", "bm25", "vector")

STOPWORDS = {
    # español
    "a", "al", "algo", "como", "con", "cual", "cuando", "de", "del", "el", "en", "es", "esta",
    "este", "hay", "la", "las", "lo", "los", "mas", "me", "mi", "no", "o", "para", "pero", "por",
    "puedo", "que", "se", "si", "sin", "sobre", "su", "sus", "un", "una", "uno", "y", "ya",
    # inglés
    "an", "and", "are", "as", "be", "by", "can", "do", "for", "from", "how", "i", "if", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "what", "when", "with", "you",
}


def tokenize(text: str) -> list[str]:
    """Minúsculas, sin acentos y sin stopwords; conserva identificadores como setMaxResults o N+1."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return [token for token in re.findall(r"[a-z0-9_]+(?:\+\d+)?", text) if token not in STOPWORDS]


class RAGSystem:
    """Encapsula un EnsembleRetriever (BM25 + PineconeVectorStore) y devuelve los top-k chunks.

    BM25 se construye en memoria con los mismos chunks que se indexaron en Pinecone
    (``load_documents`` es determinista), así ambos retrievers comparten ``chunk_id`` y
    el ensemble puede deduplicar y fusionar los rankings por ese id.
    """

    def __init__(
        self,
        *,
        k: int = 5,
        fetch_k: int = 20,
        weights: tuple[float, float] = (0.5, 0.5),
        mode: Mode = "hybrid",
        docs_dir: Path = DEFAULT_DOCS_DIR,
        corpus: list[Document] | None = None,
        settings: Settings | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode debe ser uno de {MODES}")
        self.k = k
        self.mode = mode
        self.settings = settings or get_settings()
        self.corpus = corpus if corpus is not None else load_documents(docs_dir)

        self.bm25 = BM25Retriever.from_documents(self.corpus, preprocess_func=tokenize, k=fetch_k)
        self.vector = self._vector_retriever(fetch_k) if mode != "bm25" else None
        self.ensemble = (
            EnsembleRetriever(retrievers=[self.bm25, self.vector], weights=list(weights), id_key="chunk_id")
            if mode == "hybrid"
            else None
        )

    def _vector_retriever(self, fetch_k: int) -> BaseRetriever:
        from langchain_openai import OpenAIEmbeddings
        from langchain_pinecone import PineconeVectorStore
        from pinecone import Pinecone

        self.settings.require_keys()
        pc = Pinecone(api_key=self.settings.pinecone_api_key)
        store = PineconeVectorStore(
            index=pc.Index(self.settings.index_name),
            embedding=OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=self.settings.openai_api_key),
            text_key=TEXT_KEY,
            namespace=self.settings.namespace,
        )
        return store.as_retriever(search_kwargs={"k": fetch_k})

    @property
    def retriever(self) -> BaseRetriever:
        return {"hybrid": self.ensemble, "bm25": self.bm25, "vector": self.vector}[self.mode]

    def retrieve(self, query: str, k: int | None = None) -> list[Document]:
        return self.retriever.invoke(query)[: k or self.k]


def main() -> None:
    parser = argparse.ArgumentParser(description="Consulta el recuperador híbrido.")
    parser.add_argument("query")
    parser.add_argument("--mode", choices=MODES, default="hybrid")
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()

    rag = RAGSystem(k=args.k, mode=args.mode)
    for rank, doc in enumerate(rag.retrieve(args.query), start=1):
        meta = doc.metadata
        page = f" p.{int(meta['page'])}" if "page" in meta else ""
        print(f"{rank}. [{meta['doc_id']}]{page} {meta.get('section', '')}")
        print(f"   {doc.page_content[:200].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
