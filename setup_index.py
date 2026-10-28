"""Crea el índice Serverless de Pinecone si no existe y valida su dimensión."""

from __future__ import annotations

import logging
import time

from pinecone import Pinecone, ServerlessSpec

from config import EMBEDDING_DIMENSION, EMBEDDING_MODEL, METRIC, Settings, get_settings

log = logging.getLogger("setup_index")


def ensure_index(pc: Pinecone, settings: Settings) -> None:
    name = settings.index_name
    if name in pc.list_indexes().names():
        description = pc.describe_index(name)
        if description.dimension != EMBEDDING_DIMENSION:
            raise SystemExit(
                f"el índice {name} tiene dimensión {description.dimension}, pero {EMBEDDING_MODEL} "
                f"genera vectores de {EMBEDDING_DIMENSION}. Usá otro INDEX_NAME o borrá el índice."
            )
        if description.metric != METRIC:
            log.warning("el índice %s usa la métrica %s (se esperaba %s)", name, description.metric, METRIC)
        log.info("el índice %s ya existe (dim=%s, metric=%s)", name, description.dimension, description.metric)
        return

    log.info("creando índice serverless %s en %s/%s (dim=%s)", name, settings.cloud, settings.region, EMBEDDING_DIMENSION)
    pc.create_index(
        name=name,
        dimension=EMBEDDING_DIMENSION,
        metric=METRIC,
        spec=ServerlessSpec(cloud=settings.cloud, region=settings.region),
    )
    while not pc.describe_index(name).status["ready"]:
        time.sleep(1)
    log.info("índice %s listo", name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    if not settings.pinecone_api_key:
        raise SystemExit("falta PINECONE_API_KEY en el entorno o en .env")
    pc = Pinecone(api_key=settings.pinecone_api_key)
    ensure_index(pc, settings)
    stats = pc.Index(settings.index_name).describe_index_stats()
    namespaces = {ns: info.vector_count for ns, info in (stats.namespaces or {}).items()}
    print(f"índice {settings.index_name}: {stats.total_vector_count} vectores, namespaces={namespaces}")


if __name__ == "__main__":
    main()
