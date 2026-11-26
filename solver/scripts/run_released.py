"""Run pcnr on every released record (1,040) and the authors' fixtures.

For each record: label under both absence policies, agreement with the authors' reference
solver, agreement with the released gold label, proof Check result, diagnostics.

    python solver/scripts/run_released.py [--repo neuro_symbolic] [--out solver/results]
"""

from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from pcnr import Config, run  # noqa: E402

DATASETS = {
    "controlled_600": "data_anonymized/controlled_benchmark/controlled_benchmark_600.jsonl",
    "llm_to_logic_240": "data_anonymized/llm_to_logic/llm_to_logic_240.jsonl",
    "adaptation_200": "data_anonymized/adaptation_subset/public_contract_russian_law_200.jsonl",
}


def load_reference(repo: Path):
    spec = importlib.util.spec_from_file_location("ref", repo / "baselines/symbolic_reasoner_reference.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ref"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=HERE.parents[1] / "neuro_symbolic")
    ap.add_argument("--out", type=Path, default=HERE.parent / "results")
    a = ap.parse_args()
    ref = load_reference(a.repo)
    a.out.mkdir(parents=True, exist_ok=True)

    cfg_strict = Config(absence_policy="undetermined")
    cfg_cw = Config(absence_policy="negative")
    print("== authors' fixtures")
    for f in sorted((a.repo / "fixtures/formal_reasoning").glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        r = run(t, config=cfg_strict)
        print(f"  {f.name:28s} expected={t['expected_label']:12s} pcnr={r.label:12s} "
              f"check={r.check.get('ok') if r.check else None}")

    rows, summary = [], []
    for name, rel in DATASETS.items():
        recs = [json.loads(x) for x in (a.repo / rel).read_text(encoding="utf-8").splitlines() if x.strip()]
        c = collections.Counter()
        diag = collections.Counter()
        review = collections.Counter()
        t0 = time.perf_counter()
        for rec in recs:
            strict = run(rec, config=cfg_strict)
            cw = run(rec, config=cfg_cw)
            ref_label = ref.reason({"query": rec["target_literal"], "facts": rec["facts"], "rules": rec["rules"],
                                    "conflicts": rec["conflicts"], "priorities": rec["priorities"]})["label"]
            gold = rec["gold_label"]
            c["n"] += 1
            c["strict_eq_reference"] += strict.label == ref_label
            c["strict_eq_gold"] += strict.label == gold
            c["cw_eq_gold"] += cw.label == gold
            chk = cw.check or {}
            if chk.get("applicable"):
                c["proofs_checkable"] += 1
                c["proofs_ok"] += bool(chk.get("ok"))
            c[f"proof_kind:{cw.proof['kind']}"] += 1
            c["review_flag"] += cw.review_flag
            for d in cw.diagnostics:
                if d["severity"] != "info":
                    diag[d["code"]] += 1
            for rr in cw.review_reasons:
                review[rr.split(":")[0]] += 1
            rows.append({"dataset": name, "case_id": rec["case_id"], "tier": rec.get("tier"), "gold": gold,
                         "reference": ref_label, "pcnr_strict": strict.label, "pcnr_closed_world": cw.label,
                         "proof_kind": cw.proof["kind"], "check_ok": chk.get("ok"),
                         "review_flag": cw.review_flag, "review": " | ".join(cw.review_reasons),
                         "n_arguments": cw.n_arguments, "n_defeats": cw.n_defeats})
        dt = time.perf_counter() - t0
        n = c["n"]
        print(f"\n== {name} ({n} records, {1e3 * dt / n:.2f} ms/record for both policies + reference)")
        print(f"  pcnr (Eq.1 literal)   == reference solver : {c['strict_eq_reference']}/{n}")
        print(f"  pcnr (Eq.1 literal)   == gold             : {c['strict_eq_gold']}/{n}")
        print(f"  pcnr (closed world)   == gold             : {c['cw_eq_gold']}/{n}")
        print(f"  proofs passing Check  : {c['proofs_ok']}/{c['proofs_checkable']} checkable "
              f"(kinds: { {k.split(':')[1]: v for k, v in c.items() if k.startswith('proof_kind')} })")
        print(f"  review flags          : {c['review_flag']}  {dict(review)}")
        print(f"  non-info diagnostics  : {dict(diag)}")
        summary.append({"dataset": name, "n": n, **{k: v for k, v in c.items() if k != "n"}})

    with (a.out / "released_records.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (a.out / "released_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {a.out / 'released_records.csv'} and released_summary.json")


if __name__ == "__main__":
    main()
