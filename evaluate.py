"""Evalúa el recuperador contra el golden set e imprime Precision@k, Recall@k, Hit@k y MRR.

Cada pregunta trae un ``documento_id_esperado`` (el doc_id de la sección que la responde)
y, opcionalmente, ``documentos_relevantes``: otras secciones que también la responden.

- Recall@k: fracción de los documentos relevantes que aparecen entre los k chunks recuperados.
- Precision@k: fracción de los k chunks recuperados que pertenecen a un documento relevante.
- Hit@k: 1 si el documento esperado aparece entre los k recuperados.
- MRR: 1 / posición del primer chunk relevante (0 si no hay ninguno).

    python evaluate.py                    # híbrido
    python evaluate.py --compare          # BM25 vs vectorial vs híbrido
    python evaluate.py --mode bm25        # sin claves de API
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ingest import load_documents
from rag import MODES, RAGSystem

DEFAULT_GOLDEN_SET = Path(__file__).resolve().parent / "golden_set.json"


@dataclass
class QueryResult:
    question: str
    expected: str
    retrieved: list[str]
    precision: float
    recall: float
    hit: bool
    reciprocal_rank: float


def load_golden_set(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    for item in items:
        if not item.get("pregunta") or not item.get("documento_id_esperado"):
            raise SystemExit(f"cada ítem de {path} necesita 'pregunta' y 'documento_id_esperado'")
    return items


def score(question: str, expected: str, relevant: set[str], retrieved: list[str], k: int) -> QueryResult:
    top = retrieved[:k]
    relevant_hits = [doc_id for doc_id in top if doc_id in relevant]
    first = next((rank for rank, doc_id in enumerate(top, start=1) if doc_id in relevant), None)
    return QueryResult(
        question=question,
        expected=expected,
        retrieved=top,
        precision=len(relevant_hits) / k,
        recall=len(relevant & set(top)) / len(relevant),
        hit=expected in top,
        reciprocal_rank=1 / first if first else 0.0,
    )


def evaluate(rag: RAGSystem, golden: list[dict], k: int) -> list[QueryResult]:
    results = []
    for item in golden:
        expected = item["documento_id_esperado"]
        relevant = {expected, *item.get("documentos_relevantes", [])}
        retrieved = [doc.metadata["doc_id"] for doc in rag.retrieve(item["pregunta"], k)]
        results.append(score(item["pregunta"], expected, relevant, retrieved, k))
    return results


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summary(results: list[QueryResult]) -> dict[str, float]:
    return {
        "precision": mean([r.precision for r in results]),
        "recall": mean([r.recall for r in results]),
        "hit": mean([float(r.hit) for r in results]),
        "mrr": mean([r.reciprocal_rank for r in results]),
    }


def print_detail(mode: str, results: list[QueryResult], k: int) -> None:
    print(f"\n=== {mode} — detalle por pregunta (k={k}) ===")
    for index, r in enumerate(results, start=1):
        mark = "✓" if r.hit else "✗"
        print(f"{index:>2}. {mark} P@{k}={r.precision:.2f} R@{k}={r.recall:.2f} RR={r.reciprocal_rank:.2f}  {r.question}")
        print(f"      esperado: {r.expected}")
        print(f"      top-{k}:   {', '.join(short(doc_id) for doc_id in r.retrieved)}")


def short(doc_id: str) -> str:
    source, _, anchor = doc_id.partition("#")
    return f"{'md' if source.endswith('.md') else 'pdf' if source.endswith('.pdf') else source}#{anchor}"


def print_summary(rows: dict[str, dict[str, float]], k: int, total: int) -> None:
    print(f"\n=== Resumen ({total} preguntas, k={k}) ===")
    print(f"{'modo':<8} {'Precision@' + str(k):>12} {'Recall@' + str(k):>10} {'Hit@' + str(k):>8} {'MRR':>6}")
    for mode, m in rows.items():
        print(f"{mode:<8} {m['precision']:>12.3f} {m['recall']:>10.3f} {m['hit']:>8.3f} {m['mrr']:>6.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calcula Precision@k y Recall@k sobre el golden set.")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--mode", choices=MODES, default="hybrid")
    parser.add_argument("--compare", action="store_true", help="evalúa bm25, vector e hybrid")
    parser.add_argument("--quiet", action="store_true", help="sólo el resumen")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    golden = load_golden_set(args.golden)
    corpus = load_documents()
    modes = ["bm25", "vector", "hybrid"] if args.compare else [args.mode]

    rows: dict[str, dict[str, float]] = {}
    for mode in modes:
        rag = RAGSystem(k=args.k, mode=mode, corpus=corpus)
        results = evaluate(rag, golden, args.k)
        if not args.quiet:
            print_detail(mode, results, args.k)
        rows[mode] = summary(results)
    print_summary(rows, args.k, len(golden))


if __name__ == "__main__":
    main()
