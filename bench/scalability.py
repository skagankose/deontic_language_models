"""Scalability experiment (paper §3.10.8, Table 17) for pcnr.

Synthetic policy-like theories with N rules and 2N facts (seed 42): acyclic dependency layers,
1–3 antecedents per rule, complementary-head conflict pairs and targeted defeaters, priorities
only inside declared pairs. The same theory is reused for all repetitions of a size.
5 warm-up runs, 31 measured runs; medians of argument construction + defeat compilation
("build AF"), grounded extension ("GE"), total, and tracemalloc peak.

    python bench/scalability.py [--sizes 10 50 200 1000 5000] [--reps 31]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "solver"))
from pcnr.priority import repair_cycles  # noqa: E402
from pcnr.reasoner import compile_defeats, construct_arguments, grounded  # noqa: E402
from pcnr.theory import coerce_raw, ground, normalize  # noqa: E402
from pcnr.validate import validate  # noqa: E402


def synth(n_rules: int, seed: int = 42) -> dict:
    rng = random.Random(seed)
    facts = [f"F{i}(c)" for i in range(2 * n_rules)]
    rules, conflicts, priorities, heads = [], [], [], []
    for i in range(n_rules):
        pool = facts if not heads or rng.random() < 0.6 else heads
        ants = rng.sample(pool, k=min(len(pool), rng.randint(1, 3)))
        rid = f"r{i}"
        x = rng.random()
        if heads and x < 0.3:                          # rebuttal of an earlier positive-head rule
            # rebut only original heads, so every literal keeps a single derivation (as the paper's
            # near-linear argument counts imply); rebutting a rebuttal would give D_j a second
            # producer and argument counts grow multiplicatively through the layers
            other = rng.choice([r for r in rules if r["type"] != "defeater" and r["consequent"] in heads])
            ants = rng.sample(facts, k=rng.randint(1, 3))
            head = other["consequent"][1:] if other["consequent"].startswith("¬") else "¬" + other["consequent"]
            rules.append({"id": rid, "antecedents": ants, "consequent": head, "type": "defeasible", "source": "S"})
            conflicts.append({"left": rid, "right": other["id"], "source": "S"})
            if rng.random() < 0.7:
                s, w = (rid, other["id"]) if rng.random() < 0.5 else (other["id"], rid)
                priorities.append({"stronger": s, "weaker": w, "source": "S"})
        elif heads and x < 0.4:                        # targeted defeater
            t = rng.choice([r for r in rules if r["type"] != "defeater"])
            rules.append({"id": rid, "antecedents": rng.sample(facts, k=rng.randint(1, 2)),
                          "consequent": f"block({t['id']})", "type": "defeater", "target": t["id"], "source": "S"})
        else:
            head = f"D{i}(c)"
            rules.append({"id": rid, "antecedents": ants, "consequent": head, "type": "defeasible", "source": "S"})
            heads.append(head)
    return {"query": rules[0]["consequent"] if rules else "D0(c)", "facts": {f: True for f in facts},
            "rules": rules, "conflicts": conflicts, "priorities": priorities}


def measure(theory, reps: int, warmup: int = 5) -> dict:
    def once():
        t0 = time.perf_counter()
        args = construct_arguments(theory, max_arguments=5_000_000)
        defeats = compile_defeats(theory, args, prio)
        t1 = time.perf_counter()
        grounded(args, defeats)
        t2 = time.perf_counter()
        return t1 - t0, t2 - t1, len(args), len(defeats)
    prio = repair_cycles(theory).effective
    for _ in range(warmup):
        once()
    build, ge = [], []
    for _ in range(reps):
        b, g, n_args, n_def = once()
        build.append(b)
        ge.append(g)
    tracemalloc.start()
    once()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return {"build_af_ms": 1e3 * statistics.median(build), "ge_ms": 1e3 * statistics.median(ge),
            "total_ms": 1e3 * statistics.median([b + g for b, g in zip(build, ge)]),
            "peak_mb": peak / 2**20, "arguments": n_args, "defeat_edges": n_def}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200, 1000, 5000])
    ap.add_argument("--reps", type=int, default=31)
    ap.add_argument("--out", type=Path, default=HERE / "runs/scalability.json")
    a = ap.parse_args()
    rows = []
    print(f"{'rules':>6s} {'facts':>6s} {'rule-args':>9s} {'defeats':>8s} {'build AF ms':>11s} {'GE ms':>8s} {'total ms':>9s} {'peak MB':>8s}")
    for n in a.sizes:
        raw, _ = normalize(coerce_raw(synth(n)))
        rep = validate(raw, require_sources=False)
        theory = ground(rep.clean_raw)
        m = measure(theory, a.reps)
        row = {"rules": len(theory.rules), "facts": len(theory.facts),
               "rule_derived_arguments": m["arguments"] - len(theory.facts), **m}
        rows.append(row)
        print(f"{row['rules']:6d} {row['facts']:6d} {row['rule_derived_arguments']:9d} {m['defeat_edges']:8d} "
              f"{m['build_af_ms']:11.1f} {m['ge_ms']:8.1f} {m['total_ms']:9.1f} {m['peak_mb']:8.1f}", flush=True)
    import platform
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"machine": platform.platform(), "python": platform.python_version(),
                                 "processor": platform.processor(), "reps": a.reps, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
