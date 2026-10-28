"""Ingesta de docs/ a un índice Pinecone Serverless.

Lee PDF, Markdown y JSON, los parte con RecursiveCharacterTextSplitter (medido en
tokens) y los sube con embeddings de OpenAI text-embedding-3-small. Cada vector
guarda en su metadata el texto original, la fuente, la página, la sección, un
doc_id estable y etiquetas de categoría, así la búsqueda no necesita otra base.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from bisect import bisect_right
from itertools import accumulate
from pathlib import Path
from typing import Any, Iterable, Iterator

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from config import EMBEDDING_MODEL, TEXT_KEY, get_settings

log = logging.getLogger("ingest")

DEFAULT_DOCS_DIR = Path(__file__).resolve().parent / "docs"
DEFAULT_CHUNK_TOKENS = 600
DEFAULT_OVERLAP_TOKENS = 80
MIN_CHUNK_CHARS = 40
MAX_UPSERT_BATCH = 200

TEXT_FIELDS = ("text", "content", "body", "chunk_text", "markdown")
CATEGORY_FIELDS = ("categories", "tags", "category", "etiquetas")
PAGE_FIELDS = ("page", "page_number", "pagina")
LIST_KEYS = ("documents", "docs", "chunks", "pages", "items", "data")
SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".json", ".jsonl"}
FILENAME_STOPWORDS = {
    "and", "de", "docs", "el", "en", "estable", "for", "guia", "guide", "html", "io",
    "la", "of", "para", "pdf", "readthedocs", "stable", "the", "www",
}

HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Encabezados numerados de documentación Sphinx exportada a PDF: "9.2.12 Transactions and Concurrency"
PDF_HEADING_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+([A-Z][^.]{2,80})$")


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_tag(value: str) -> str:
    tag = value.strip().lower()
    tag = re.sub(r"^\d+(\.\d+)*\.?\s*", "", tag)
    tag = re.sub(r"\s+", " ", tag).strip(" .:-#*`")
    return tag[:80]


def slugify(value: str) -> str:
    slug = re.sub(r"[^\w]+", "-", value.lower(), flags=re.UNICODE).strip("-")
    return slug[:80]


def coerce_categories(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;|]", value)
        return [tag for part in parts if (tag := normalize_tag(part))]
    if isinstance(value, (list, tuple)):
        tags: list[str] = []
        for item in value:
            tags.extend(coerce_categories(item))
        return tags
    return []


def unique_tags(*groups: Iterable[str], limit: int = 10) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for group in groups:
        for raw in group:
            tag = normalize_tag(str(raw))
            if not tag or len(tag) < 2 or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
            if len(tags) >= limit:
                return tags
    return tags


def tags_from_filename(path: Path) -> list[str]:
    stem = re.sub(r"\d+", " ", path.stem.lower())
    tokens = re.split(r"[^a-záéíóúñü]+", stem)
    return [token for token in tokens if token not in FILENAME_STOPWORDS and len(token) >= 2]


def make_splitter(chunk_tokens: int, overlap_tokens: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=chunk_tokens,
        chunk_overlap=overlap_tokens,
    )


def split_text(splitter: RecursiveCharacterTextSplitter, text: str) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    return [piece.strip() for piece in splitter.split_text(text) if piece and piece.strip()]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip().lower()
        raw = raw.strip().strip("\"'")
        if raw.startswith("[") and raw.endswith("]"):
            meta[key] = [part.strip().strip("\"'") for part in raw[1:-1].split(",") if part.strip()]
        else:
            meta[key] = raw
    return meta, text[match.end():]


def clean_heading(title: str) -> str:
    title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)
    return re.sub(r"[*_`]", "", title).strip()


def split_markdown(text: str) -> list[tuple[str, str, str]]:
    """Devuelve (sección de nivel 2, subtítulo, contenido) ignorando '#' dentro de bloques de código."""
    sections: list[tuple[str, str, str]] = []
    parent, title, buffer = "", "", []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            content = f"{title}\n\n{body}" if title else body
            sections.append((parent, title, content))

    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if not match:
            buffer.append(line)
            continue
        flush()
        buffer = []
        level, title = len(match.group(1)), clean_heading(match.group(2))
        if level <= 2:
            parent = title
    flush()
    return sections


def chunk_id(source: str, page: int | None, index: int) -> str:
    page_part = "na" if page is None else str(page)
    raw = f"{source}::p{page_part}::c{index}"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    if len(slug) <= 200:
        return slug
    return f"{slug[:180]}-{hashlib.sha1(raw.encode()).hexdigest()[:12]}"


def build_chunks(
    *,
    text: str,
    source: str,
    doc_type: str,
    doc_id: str,
    categories: list[str],
    page: int | None,
    section: str,
    splitter: RecursiveCharacterTextSplitter,
    id_offset: int = 0,
) -> tuple[list[Document], int]:
    pieces = split_text(splitter, text)
    docs: list[Document] = []
    for offset, piece in enumerate(pieces):
        if len(piece) < MIN_CHUNK_CHARS:
            continue
        cid = chunk_id(source, page, id_offset + offset)
        metadata: dict[str, Any] = {
            "chunk_id": cid,
            "doc_id": doc_id,
            "source": source,
            "doc_type": doc_type,
            "categories": categories,
        }
        if page is not None:
            metadata["page"] = page
        if section:
            metadata["section"] = section[:200]
        docs.append(Document(id=cid, page_content=piece, metadata=metadata))
    return docs, len(pieces)


def load_pdf(path: Path, source: str, splitter: RecursiveCharacterTextSplitter) -> list[Document]:
    """Agrupa el texto del PDF por los encabezados numerados y fragmenta cada sección.

    Los chunks pueden cruzar páginas (así no quedan cortados al tamaño de una página);
    ``page`` es la página donde empieza cada chunk. El doc_id es la sección
    (p. ej. ``archivo.pdf#9.2.12``), así un tema de varias páginas tiene un único id evaluable.
    """
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    file_tags = tags_from_filename(path)
    group, number, title = "", "", "front matter"
    lines: list[tuple[int, str]] = []
    docs: list[Document] = []

    def emit() -> None:
        if not lines:
            return
        doc_id = f"{source}#{number}" if number else f"{source}#front-matter"
        categories = unique_tags(file_tags, [group] if group else [], [title])
        text = "\n".join(line for _, line in lines)
        line_starts = list(accumulate((len(line) + 1 for _, line in lines[:-1]), initial=0))
        cursor = 0
        for index, piece in enumerate(splitter.split_text(text)):
            piece = piece.strip()
            if len(piece) < MIN_CHUNK_CHARS:
                continue
            position = text.find(piece[:200], cursor)
            cursor = position if position >= 0 else cursor
            page = lines[bisect_right(line_starts, cursor) - 1][0]
            cid = f"{chunk_id(source, page, index)}-s{slugify(number or 'fm')}"
            docs.append(
                Document(
                    id=cid,
                    page_content=piece,
                    metadata={
                        "chunk_id": cid,
                        "doc_id": doc_id,
                        "source": source,
                        "doc_type": "pdf",
                        "categories": categories,
                        "page": page,
                        "section": f"{number} {title}".strip(),
                    },
                )
            )

    pages = [normalize_text(page.extract_text() or "").splitlines() for page in reader.pages]
    boilerplate = repeated_lines(pages)
    for page_number, page_lines in enumerate(pages, start=1):
        for line in page_lines:
            if line_signature(line) in boilerplate:
                continue
            match = PDF_HEADING_RE.match(line.strip())
            if match and is_next_section(match.group(1), number):
                emit()
                lines = []
                number, title = match.group(1), match.group(2).strip()
                if number.count(".") == 1:
                    group = title
            lines.append((page_number, line))
    emit()
    return docs


def line_signature(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def repeated_lines(pages: list[list[str]], ratio: float = 0.2) -> set[str]:
    """Encabezados y pies de página: líneas cortas (ignorando números) presentes en muchas páginas."""
    counts: dict[str, int] = {}
    for lines in pages:
        for signature in {line_signature(line) for line in lines[:2] + lines[-2:] if len(line) < 100}:
            counts[signature] = counts.get(signature, 0) + 1
    threshold = max(3, int(len(pages) * ratio))
    return {signature for signature, count in counts.items() if count >= threshold}


def is_next_section(candidate: str, current: str) -> bool:
    """Acepta un encabezado sólo si avanza la numeración; descarta referencias cruzadas en el texto."""
    if not current:
        return True
    as_tuple = lambda value: tuple(int(part) for part in value.split("."))  # noqa: E731
    return as_tuple(candidate) > as_tuple(current)


def load_markdown(path: Path, source: str, splitter: RecursiveCharacterTextSplitter) -> list[Document]:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    file_tags = tags_from_filename(path)
    explicit: list[str] = []
    for key in CATEGORY_FIELDS:
        explicit.extend(coerce_categories(meta.get(key)))
    docs: list[Document] = []
    cursor = 0
    for parent, title, content in split_markdown(body):
        parent_number = re.match(r"^(\d+)\.", parent)
        anchor = parent_number.group(1) if parent_number else slugify(parent) or "intro"
        section = title if title == parent or not parent else f"{parent} › {title}"
        section_docs, produced = build_chunks(
            text=content,
            source=source,
            doc_type="markdown",
            doc_id=f"{source}#{anchor}",
            categories=unique_tags(file_tags, explicit, [parent, title]),
            page=None,
            section=section,
            splitter=splitter,
            id_offset=cursor,
        )
        cursor += produced
        docs.extend(section_docs)
    return docs


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def json_items(data: Any) -> tuple[list[Any], list[str]]:
    if isinstance(data, list):
        return data, []
    if isinstance(data, dict):
        shared: list[str] = []
        for key in CATEGORY_FIELDS:
            shared.extend(coerce_categories(data.get(key)))
        for key in LIST_KEYS:
            if isinstance(data.get(key), list):
                return data[key], shared
        return [data], shared
    raise ValueError("el JSON debe ser un objeto, una lista o JSONL")


def text_of(item: dict[str, Any]) -> str:
    for key in TEXT_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def load_json(path: Path, source: str, splitter: RecursiveCharacterTextSplitter) -> list[Document]:
    file_tags = tags_from_filename(path)
    if path.suffix == ".jsonl":
        items = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number} no es JSON válido") from exc
        shared_tags: list[str] = []
    else:
        items, shared_tags = json_items(json.loads(path.read_text(encoding="utf-8")))

    docs: list[Document] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            log.warning("se omite %s item %s: no es un objeto", source, index)
            continue
        text = text_of(item)
        if not text:
            log.warning("se omite %s item %s: no tiene texto", source, index)
            continue
        explicit: list[str] = []
        for key in CATEGORY_FIELDS:
            explicit.extend(coerce_categories(item.get(key)))
        page = next((p for key in PAGE_FIELDS if (p := _as_int(item.get(key))) is not None), None)
        item_source = str(item.get("source") or source)
        item_docs, _ = build_chunks(
            text=text,
            source=item_source,
            doc_type="json",
            doc_id=str(item.get("id") or f"{item_source}#{index}"),
            categories=unique_tags(file_tags, shared_tags, explicit),
            page=page,
            section=str(item.get("section") or item.get("title") or ""),
            splitter=splitter,
            id_offset=index * 1000,
        )
        docs.extend(item_docs)
    return docs


LOADERS = {
    ".pdf": load_pdf,
    ".md": load_markdown,
    ".markdown": load_markdown,
    ".json": load_json,
    ".jsonl": load_json,
}


def load_documents(
    root: Path = DEFAULT_DOCS_DIR,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Document]:
    """Carga y fragmenta todo el corpus. Es determinista: los mismos archivos dan los mismos chunk_id."""
    if not root.is_dir():
        raise FileNotFoundError(f"no existe el directorio de documentos: {root}")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not files:
        raise FileNotFoundError(f"no hay PDF, Markdown ni JSON en {root}")

    splitter = make_splitter(chunk_tokens, overlap_tokens)
    docs: list[Document] = []
    for path in files:
        source = path.relative_to(root).as_posix()
        try:
            loaded = LOADERS[path.suffix.lower()](path, source, splitter)
        except Exception as exc:
            log.error("no se pudo leer %s: %s", source, exc)
            continue
        log.info("%s → %s fragmentos", source, len(loaded))
        docs.extend(loaded)
    if not docs:
        raise RuntimeError(f"no quedó texto útil para indexar en {root}")
    return docs


def batched(items: list[Document], size: int) -> Iterator[list[Document]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def upsert_documents(docs: list[Document], batch_size: int) -> int:
    from langchain_openai import OpenAIEmbeddings
    from langchain_pinecone import PineconeVectorStore
    from pinecone import Pinecone

    from setup_index import ensure_index

    settings = get_settings()
    settings.require_keys()
    pc = Pinecone(api_key=settings.pinecone_api_key)
    ensure_index(pc, settings)

    store = PineconeVectorStore(
        index=pc.Index(settings.index_name),
        embedding=OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=settings.openai_api_key),
        text_key=TEXT_KEY,
        namespace=settings.namespace,
    )
    total = 0
    for batch in batched(docs, batch_size):
        store.add_documents(batch, ids=[doc.id for doc in batch])
        total += len(batch)
        log.info("lote de %s fragmentos enviado (acumulado %s/%s)", len(batch), total, len(docs))
    print(f"fragmentos enviados a {settings.index_name}/{settings.namespace}: {total}")
    return total


def print_preview(docs: list[Document]) -> None:
    by_source: dict[str, int] = {}
    doc_ids: set[str] = set()
    for doc in docs:
        by_source[doc.metadata["source"]] = by_source.get(doc.metadata["source"], 0) + 1
        doc_ids.add(doc.metadata["doc_id"])
    for source, count in by_source.items():
        print(f"{source}: {count} fragmentos")
    print(f"total: {len(docs)} fragmentos en {len(doc_ids)} documentos lógicos (doc_id)")
    sample = docs[min(len(docs) - 1, len(docs) // 2)]
    print("ejemplo:")
    for key in ("chunk_id", "doc_id", "source", "page", "section", "categories"):
        if key in sample.metadata:
            value = sample.metadata[key]
            print(f"  {key}: {', '.join(value) if isinstance(value, list) else value}")
    print(f"  texto: {sample.page_content[:180].replace(chr(10), ' ')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Procesa PDF, Markdown y JSON y los sube a Pinecone Serverless.")
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS_DIR, help="directorio de documentos")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--chunk-tokens", type=int, default=DEFAULT_CHUNK_TOKENS)
    parser.add_argument("--overlap-tokens", type=int, default=DEFAULT_OVERLAP_TOKENS)
    parser.add_argument("--dry-run", action="store_true", help="procesa los archivos sin subirlos")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if not 1 <= args.batch_size <= MAX_UPSERT_BATCH:
        raise SystemExit(f"--batch-size debe estar entre 1 y {MAX_UPSERT_BATCH}")
    if args.overlap_tokens >= args.chunk_tokens:
        raise SystemExit("--overlap-tokens tiene que ser menor que --chunk-tokens")

    docs = load_documents(args.docs.resolve(), args.chunk_tokens, args.overlap_tokens)
    print_preview(docs)
    if args.dry_run:
        print("dry-run: no se escribió nada en Pinecone")
        return
    upsert_documents(docs, args.batch_size)


if __name__ == "__main__":
    main()
