# PineconeRag

Módulo de recuperación escalable sobre Pinecone Serverless: ingesta de documentos técnicos (documentación de Doctrine ORM para PHP, en `docs/`), recuperador híbrido BM25 + vectorial y evaluación con Precision@k / Recall@k sobre un golden set.

| Archivo | Qué hace |
|---|---|
| `config.py` | Lee `.env` y define el modelo de embeddings (`text-embedding-3-small`, dimensión 1536, métrica coseno). |
| `setup_index.py` | Crea el índice Serverless si no existe; si existe, valida que la dimensión sea 1536. |
| `ingest.py` | Carga PDF, Markdown y JSON, los fragmenta y los sube a Pinecone con `PineconeVectorStore`. |
| `rag.py` | Clase `RAGSystem`: `EnsembleRetriever` que combina `BM25Retriever` y el retriever de Pinecone. |
| `evaluate.py` | Corre el golden set y mide Precision@5, Recall@5, Hit@5 y MRR. |
| `golden_set.json` | 10 preguntas con el documento que las responde. |

## Cómo funciona

**Ingesta.** `RecursiveCharacterTextSplitter.from_tiktoken_encoder` con chunks de 600 tokens y 80 de solapamiento (promedio real: ~450 tokens, porque cada chunk queda dentro de una sección). El Markdown se corta por encabezados (ignorando los `#` dentro de bloques de código). El PDF se agrupa por sus secciones numeradas (`9.2.12 Transactions and Concurrency`), así un chunk puede cruzar páginas sin mezclar temas. También se descartan los encabezados y pies de página que se repiten.

Cada vector guarda en la metadata:

| Campo | Ejemplo |
|---|---|
| `text` | el texto original del chunk, para no tener que consultar otra base |
| `source` | `doctrine2-readthedocs-io-en-stable.pdf` |
| `page` | `147` (página donde empieza el chunk; sólo PDF) |
| `section` | `9.2.12 Transactions and Concurrency` |
| `categories` | `["doctrine", "reference guide", "transactions and concurrency"]` |
| `doc_id` | `doctrine2-readthedocs-io-en-stable.pdf#9.2.12`: la sección lógica, usada en la evaluación |
| `chunk_id` | id del vector, determinista (re-ingestar sobrescribe, no duplica) |
| `doc_type` | `pdf`, `markdown` o `json` |

Todo se escribe en el namespace `doctrine-docs` (configurable), para no mezclar este corpus con otros datos del mismo índice.

**Recuperación híbrida.** BM25 se construye en memoria con los mismos chunks que hay en Pinecone (la carga es determinista, así que los `chunk_id` coinciden). Cada retriever trae 20 candidatos, el `EnsembleRetriever` los fusiona con Reciprocal Rank Fusion (pesos 0.5 / 0.5, deduplicando por `chunk_id`) y `RAGSystem.retrieve()` devuelve los 5 primeros. El tokenizador de BM25 pasa a minúsculas, quita acentos y stopwords, y conserva identificadores como `setMaxResults`, `DiscriminatorMap` o `N+1`. Esto es justamente lo que la búsqueda semántica tiende a diluir.

## Replicar el índice

Necesitás Python 3.10+ y claves de [Pinecone](https://app.pinecone.io) y de OpenAI.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar PINECONE_API_KEY, OPENAI_API_KEY e INDEX_NAME
```

1. Crear (o validar) el índice Serverless: `aws/us-east-1`, dimensión 1536, coseno.

   ```bash
   python setup_index.py
   ```

2. Ver qué se va a subir sin gastar llamadas a las APIs:

   ```bash
   python ingest.py --dry-run
   ```

3. Generar los embeddings y subirlos (357 chunks; cuesta menos de USD 0,01 en embeddings):

   ```bash
   python ingest.py
   ```

4. Consultar:

   ```bash
   python rag.py "¿Cómo evito el problema N+1?"
   python rag.py --mode bm25 "setMaxResults con fetch join"
   ```

5. Evaluar:

   ```bash
   python evaluate.py --compare
   ```

Si `INDEX_NAME` apunta a un índice que ya existe con otra dimensión (por ejemplo 1024 o 768), `setup_index.py` e `ingest.py` se detienen con un error en lugar de intentar subir los vectores.

Para agregar documentos, dejalos en `docs/` (PDF, `.md`, `.json` o `.jsonl`) y volvé a correr `ingest.py`. Un JSON puede ser una lista, JSONL o un objeto con `documents` / `pages`. Cada ítem usa `text` (o `content` / `body`) y puede traer `id`, `page`, `categories`, `tags`, `section` y `source`.

## Evaluación

`golden_set.json` tiene 10 preguntas: algunas en español sobre la guía en Markdown, otras en inglés sobre el PDF, algunas en español sobre temas que sólo cubre el PDF (cruce de idiomas) y otras con nombres técnicos exactos. Cada una tiene un `documento_id_esperado` y, si el tema también aparece en otra sección, una lista de `documentos_relevantes`.

- **Recall@5:** fracción de los documentos relevantes que aparecen entre los 5 chunks recuperados.
- **Precision@5:** fracción de los 5 chunks recuperados que pertenecen a un documento relevante.
- **Hit@5:** el documento esperado está en el top 5.
- **MRR:** 1 / posición del primer chunk relevante.

Resultados (k = 5):

| Modo | Precision@5 | Recall@5 | Hit@5 | MRR |
|---|---|---|---|---|
| BM25 | 0.520 | 0.875 | 1.000 | 1.000 |
| Vectorial (Pinecone) | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ |
| Híbrido (Ensemble) | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ |

> Las filas vectorial e híbrida se completan con la salida de `python evaluate.py --compare` una vez que el índice está cargado.

Precision@5 tiene un techo bajo por construcción: la mayoría de las preguntas tienen 1 o 2 secciones relevantes, y los 5 chunks suelen venir de secciones vecinas que tocan el tema de costado.
