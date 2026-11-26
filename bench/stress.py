"""Stress tests (paper §3.10.7 / Table 16 and §3.10.9 / Table 18).

Extraction-error stress test: start from the run's (unrepaired) extracted theories; with seed 42,
perturb each eligible object independently at the family rate:
  fact_omission        remove the fact
  fact_hallucination   insert a schema-valid unsupported fact (an unused vocabulary literal citing a case-file unit)
  rule_type            change only the type field (defeasible<->strict, defeater->defeasible)
  priority_direction   swap stronger/weaker
  citation_mismatch    move the cited span outside the unit
  mixed                10%, families chosen uniformly per object
Then rerun pcnr + Check without changing gold labels. One seeded run per condition (descriptive).

Cyclic-priority test: sample 25/50/100 gold theories that have a priority (seed 42) and add the
reverse of one existing priority edge, citing a different clause (a 2-cycle inside a declared
conflict pair; the paper's 3-rule cycles are impossible when priorities must lie inside pairs of
opposed rules). κL repair then resolves it by specificity/authority or escalates.

    python bench/stress.py --run bench/runs/luna_full
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from llm import content_json  # noqa: E402
from score import reason  # noqa: E402

FAMILIES = ["fact_omission", "fact_hallucination", "rule_type", "priority_direction", "citation_mismatch"]


def perturb(ext: dict, sc: dict, family: str, rate: float, rng: random.Random) -> tuple[dict, int]:
    ext = copy.deepcopy(ext)
    units = {u["source_unit_id"]: u["text"] for u in sc["source_units"]}
    vocab = sorted({a for r in sc["gold"]["rules"] for a in r["antecedents"]} - {f["atom"] for f in sc["gold"]["facts"]})
    fam_choice = (lambda: rng.choice(FAMILIES)) if family == "mixed" else (lambda: family)
    n = 0
    facts = []
    for f in ext.get("facts", []):
        fam = fam_choice()
        if fam in ("fact_omission", "fact_hallucination", "citation_mismatch") and rng.random() < rate:
            n += 1
            if fam == "fact_omission":
                continue
            if fam == "fact_hallucination":
                facts.append(f)
                cands = [v for v in vocab if v not in {x.get("atom") for x in ext["facts"]}]
                if cands:
                    facts.append({"atom": rng.choice(cands), "source": f.get("source"), "confidence": 0.9})
                continue
            f = {**f, "source": _shift(f.get("source"), units)}
        facts.append(f)
    ext["facts"] = facts
    for r in ext.get("rules", []):
        fam = fam_choice()
        if fam in ("rule_type", "citation_mismatch") and rng.random() < rate:
            n += 1
            if fam == "rule_type":
                r["type"] = {"defeasible": "strict", "strict": "defeasible", "defeater": "defeasible"}.get(r.get("type"), "defeasible")
            else:
                r["source"] = _shift(r.get("source"), units)
    for p in ext.get("priorities", []):
        fam = fam_choice()
        if fam in ("priority_direction", "citation_mismatch") and rng.random() < rate:
            n += 1
            if fam == "priority_direction":
                p["stronger"], p["weaker"] = p.get("weaker"), p.get("stronger")
            else:
                p["source"] = _shift(p.get("source"), units)
    return ext, n


def _shift(src, units):
    uid = str(src or "").partition("#")[0]
    length = len(units.get(uid, ""))
    return f"{uid}#{length + 5}-{length + 60}"


def evaluate(scs, theories) -> dict:
    correct = proofs = valid = 0
    for sc in scs:
        res = reason(sc, theories[sc["scenario_id"]])
        lab = res.label if res else "Undetermined"
        correct += lab == sc["gold_label"]
        if res and res.proof and res.proof["kind"] in ("acceptance", "absence"):
            proofs += 1
            valid += bool(res.check and res.check.get("ok"))
    return {"accuracy": 100 * correct / len(scs), "proof_validity": 100 * valid / proofs if proofs else None,
            "proofs": proofs}


def extraction_stress(run: Path, scs: list[dict]) -> list[dict]:
    exts = {}
    for sc in scs:
        f = sorted(glob.glob(str(run / "extractions" / f"{sc['scenario_id']}-*.json")))
        try:
            exts[sc["scenario_id"]] = content_json(json.loads(Path(f[-1]).read_text(encoding="utf-8")))
        except (ValueError, KeyError, IndexError, json.JSONDecodeError):
            exts[sc["scenario_id"]] = None
    base = evaluate(scs, exts)
    rows = [{"condition": "none (unperturbed extraction)", "rate": 0, "perturbed_objects": 0, **base}]
    for family, rate in [(f, 0.05) for f in FAMILIES] + [("mixed", 0.10)]:
        pert, total = {}, 0
        for sc in scs:
            e = exts[sc["scenario_id"]]
            if e is None:
                pert[sc["scenario_id"]] = None
                continue
            pert[sc["scenario_id"]], k = perturb(e, sc, family, rate, random.Random(f"42-{family}-{sc['scenario_id']}"))
            total += k
        rows.append({"condition": family, "rate": rate, "perturbed_objects": total, **evaluate(scs, pert)})
    return rows


def _depths(gold: dict) -> dict[str, int]:
    """Exception depth from the gold priorities: general rule 1, exception 2, nested exception 3 ..."""
    stronger = {}
    for p in gold["priorities"]:
        stronger.setdefault(p["stronger"], []).append(p["weaker"])
    memo: dict[str, int] = {}

    def d(r, seen=()):
        if r not in memo:
            memo[r] = 1 + max((d(w, seen + (r,)) for w in stronger.get(r, []) if w not in seen), default=0)
        return memo[r]
    return {r["id"]: d(r["id"]) for r in gold["rules"]}


def cycle_stress(scs: list[dict]) -> list[dict]:
    eligible = [s for s in scs if s["gold"]["priorities"] and not any(
        p in s["phenomena"] for p in ("cycle_resolvable", "cycle_unresolvable"))]
    rows = []
    for mode in ("inclusion (paper text)", "meta depth (released data)"):
        for n in (25, 50, 100):
            rng = random.Random(42)
            sample = rng.sample(eligible, n)
            repaired = flagged = false_det = correct = 0
            for sc in sample:
                g = copy.deepcopy(sc["gold"])
                if mode.startswith("meta"):
                    depth = _depths(g)
                    for r in g["rules"]:
                        r["meta"] = {"specificity": depth[r["id"]]}
                    g["settings"] = {"specificity_from_meta": True}
                p = rng.choice(g["priorities"])
                other = next((r["source"] for r in g["rules"] if r["id"] == p["weaker"]), p["source"])
                g["priorities"].append({"stronger": p["weaker"], "weaker": p["stronger"], "source": other, "sources": [other]})
                res = reason(sc, g)
                log = res.priority_repair_log
                repaired += bool(log) and all(e["action"] != "removed" for e in log)
                flagged += any("unresolved priority cycle" in x for x in res.review_reasons)
                false_det += res.label != "Undetermined" and res.label != sc["gold_label"]
                correct += res.label == sc["gold_label"]
            rows.append({"specificity": mode, "injected_cases": n, "repaired_automatically_pct": 100 * repaired / n,
                         "human_review_flags_pct": 100 * flagged / n, "false_determinate_pct": 100 * false_det / n,
                         "accuracy_after_repair_pct": 100 * correct / n})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--scenarios", type=Path, default=HERE / "data/scenarios.jsonl")
    a = ap.parse_args()
    scs = [json.loads(x) for x in a.scenarios.read_text(encoding="utf-8").splitlines() if x.strip()]
    ex = extraction_stress(a.run, scs)
    cy = cycle_stress(scs)
    (a.run / "stress.json").write_text(json.dumps({"extraction_stress": ex, "cycle_stress": cy}, indent=2))
    print(f"{'condition':32s} {'rate':>5s} {'objs':>5s} {'acc %':>6s} {'proof valid %':>13s}")
    for r in ex:
        pv = "—" if r["proof_validity"] is None else f"{r['proof_validity']:.1f}"
        print(f"{r['condition']:32s} {r['rate']:5.2f} {r['perturbed_objects']:5d} {r['accuracy']:6.1f} {pv:>13s}")
    print(f"\n{'specificity':28s} {'cases':>5s} {'auto-repaired %':>15s} {'review %':>9s} {'false det. %':>12s} {'acc after %':>11s}")
    for r in cy:
        print(f"{r['specificity']:28s} {r['injected_cases']:5d} {r['repaired_automatically_pct']:15.1f} {r['human_review_flags_pct']:9.1f} "
              f"{r['false_determinate_pct']:12.1f} {r['accuracy_after_repair_pct']:11.1f}")


if __name__ == "__main__":
    main()
