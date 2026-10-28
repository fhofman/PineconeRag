"""Configuración compartida: variables de entorno y constantes del índice."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
METRIC = "cosine"
TEXT_KEY = "text"


@dataclass(frozen=True)
class Settings:
    pinecone_api_key: str | None
    openai_api_key: str | None
    index_name: str
    namespace: str
    cloud: str
    region: str

    def require_keys(self) -> None:
        missing = [
            name
            for name, value in (
                ("PINECONE_API_KEY", self.pinecone_api_key),
                ("OPENAI_API_KEY", self.openai_api_key),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"faltan variables en el entorno o en .env: {', '.join(missing)}")


def get_settings() -> Settings:
    return Settings(
        pinecone_api_key=os.getenv("PINECONE_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        index_name=os.getenv("INDEX_NAME", "pinecone-rag"),
        namespace=os.getenv("PINECONE_NAMESPACE", "doctrine-docs"),
        cloud=os.getenv("PINECONE_CLOUD", "aws"),
        region=os.getenv("PINECONE_REGION", "us-east-1"),
    )
