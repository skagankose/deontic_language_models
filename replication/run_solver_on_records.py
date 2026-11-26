"""Run the authors' reference solver on every released gold theory and compare to gold labels.

Usage:
    python replication/run_solver_on_records.py [--repo neuro_symbolic] [--show-mismatches]

The released JSONL records contain gold-formalized theories (facts, rules, conflicts,
priorities). The LLM-extracted theories are NOT released, so this checks the symbolic layer
only — the "under gold formalization" condition from Section 4.1 of the paper.
"""

from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASETS = {
    "controlled_600": "data_anonymized/controlled_benchmark/controlled_benchmark_600.jsonl",
    "llm_to_logic_240": "data_anonymized/llm_to_logic/llm_to_logic_240.jsonl",
    "adaptation_200": "data_anonymized/adaptation_subset/public_contract_russian_law_200.jsonl",
}
# Key in record["predictions"] holding the authors' stored output for the proposed engine.
PROPOSED_KEYS = ("proposed_grounded_nmr", "proposed_llm_to_nmr", "proposed")


def load_solver(repo: Path):
    path = repo / "baselines/symbolic_reasoner_reference.py"
    spec = importlib.util.spec_from_file_location("symbolic_reasoner_reference", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def closed_world_label(solver, theory: dict, result: dict) -> str:
    """Hypothesised convention of the authors' evaluated engine (not stated in the paper):
    if NO argument for q or its complement can be constructed at all, the claim fails -> Negative.
    'Undetermined' is reserved for cases where arguments exist but conflict without resolution."""
    if result["label"] != "Undetermined":
        return result["label"]
    arguments, _ = solver.construct_arguments(theory)
    q = theory["query"]
    if not any(a.conclusion in (q, solver.complement(q)) for a in arguments):
        return "Negative"
    return result["label"]


def to_theory(record: dict) -> dict:
    return {
        "query": record["target_literal"],
        "facts": record["facts"],
        "rules": record["rules"],
        "conflicts": record.get("conflicts", []),
        "priorities": record.get("priorities", []),
    }


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    labels = ("Positive", "Negative", "Undetermined")
    f1s = []
    for lab in labels:
        tp = sum(g == lab and p == lab for g, p in pairs)
        fp = sum(g != lab and p == lab for g, p in pairs)
        fn = sum(g == lab and p != lab for g, p in pairs)
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return 100 * sum(f1s) / len(f1s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=HERE.parent / "neuro_symbolic")
    ap.add_argument("--show-mismatches", action="store_true")
    ap.add_argument("--closed-world", action="store_true",
                    help="map 'no argument for q or ~q' to Negative (see closed_world_label)")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    args = ap.parse_args()

    solver = load_solver(args.repo)
    if args.closed_world:
        args.out = args.out / "closed_world"
    args.out.mkdir(parents=True, exist_ok=True)
    summary_rows = []

    for name, rel in DATASETS.items():
        records = [json.loads(line) for line in (args.repo / rel).read_text(encoding="utf-8").splitlines() if line.strip()]
        rows, pairs = [], []
        by_tier: dict = collections.defaultdict(list)
        for rec in records:
            theory = to_theory(rec)
            result = solver.reason(theory)
            if args.closed_world:
                result["label"] = closed_world_label(solver, theory, result)
            gold = rec["gold_label"]
            stored = next((rec["predictions"][k] for k in PROPOSED_KEYS if k in rec.get("predictions", {})), None)
            pairs.append((gold, result["label"]))
            by_tier[rec.get("tier")].append(gold == result["label"])
            rows.append({
                "case_id": rec["case_id"], "tier": rec.get("tier"), "gold": gold,
                "solver": result["label"], "authors_stored": stored,
                "solver_correct": gold == result["label"],
                "agrees_with_stored": stored == result["label"] if stored else None,
            })

        with (args.out / f"{name}_solver_predictions.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

        n = len(rows)
        correct = sum(r["solver_correct"] for r in rows)
        agree = [r["agrees_with_stored"] for r in rows if r["agrees_with_stored"] is not None]
        print(f"\n=== {name}: {n} records")
        print(f"  reference solver vs gold : {correct}/{n} = {100*correct/n:.2f}%  macro-F1 {macro_f1(pairs):.2f}%")
        if agree:
            print(f"  agreement with authors' stored proposed-engine predictions: {sum(agree)}/{len(agree)}")
        for tier in sorted(k for k in by_tier if k is not None):
            v = by_tier[tier]
            print(f"  tier {tier}: {sum(v)}/{len(v)} = {100*sum(v)/len(v):.1f}%")
        confusion = collections.Counter((g, p) for g, p in pairs if g != p)
        if confusion:
            print("  errors (gold -> solver):", dict(confusion))
        if args.show_mismatches:
            for r in rows:
                if not r["solver_correct"]:
                    print("   ", r)
        summary_rows.append({"dataset": name, "n": n, "correct": correct,
                             "accuracy_pct": round(100 * correct / n, 2),
                             "macro_f1_pct": round(macro_f1(pairs), 2)})

    with (args.out / "summary.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary_rows[0]))
        w.writeheader()
        w.writerows(summary_rows)
    print(f"\nWrote per-case CSVs and summary.csv to {args.out}")


if __name__ == "__main__":
    main()
