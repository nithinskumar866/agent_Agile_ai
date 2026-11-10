"""Evaluate retrieval over the golden questions in evals/rag_golden.yaml.

    python -m evals.rag_eval              # prints the numbers and any misses
    python -m evals.rag_eval --k 3

Three numbers, because RAG fails in three different ways:
- recall@k: of the questions the documents answer, how often the right passage is in the top k;
- MRR: how near the top it is (1.0 = always first). A right passage buried at rank 4 is a coin flip
  for the model;
- refusal: of the questions the documents do NOT answer, how many correctly return nothing.
  This is the one that prevents confident made-up answers.

No model is called, so it is fast, free and repeatable. Whether the *model* then cites and refuses
correctly is the job of the adk eval cases (python -m evals.run).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import knowledge  # noqa: E402

GOLDEN = Path(__file__).with_name("rag_golden.yaml")


@dataclass
class RagScores:
    recall: float
    mrr: float
    refusal: float
    answerable: int
    unanswerable: int
    misses: list[str] = field(default_factory=list)
    false_answers: list[str] = field(default_factory=list)


def evaluate(k: int = 3, golden_path: Path = GOLDEN) -> RagScores:
    golden = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    collection, found, reciprocal, misses = golden["collection"], 0, 0.0, []
    for case in golden["answerable"]:
        results = knowledge.search(case["q"], collection, max_results=k)["results"]
        rank = next((r["rank"] for r in results
                     if r["citation"].split("/", 1)[1].split(" §")[0] == case["doc"] and case["must_contain"] in r["text"]), None)
        if rank:
            found += 1
            reciprocal += 1 / rank
        else:
            misses.append(f"{case['q']!r}: wanted {case['doc']} containing {case['must_contain']!r}, got "
                          f"{[r['citation'] for r in results] or 'nothing'}")
    refused, false_answers = 0, []
    for question in golden["unanswerable"]:
        results = knowledge.search(question, collection, max_results=k)["results"]
        if not results:
            refused += 1
        else:
            false_answers.append(f"{question!r} returned {[r['citation'] for r in results]}")
    n, m = len(golden["answerable"]), len(golden["unanswerable"])
    return RagScores(found / n, reciprocal / n, refused / m, n, m, misses, false_answers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--k", type=int, default=3, help="passages returned per question (default 3)")
    args = parser.parse_args(argv)
    s = evaluate(args.k)
    print(f"recall@{args.k}: {s.recall:.2f}   ({s.answerable} answerable questions)")
    print(f"MRR:       {s.mrr:.2f}")
    print(f"refusal:   {s.refusal:.2f}   ({s.unanswerable} unanswerable questions)")
    for line in s.misses:
        print("MISS:", line)
    for line in s.false_answers:
        print("SHOULD HAVE REFUSED:", line)
    return 0 if not s.misses and not s.false_answers else 1


if __name__ == "__main__":
    sys.exit(main())
