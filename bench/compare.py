"""Full comparison on the 240-scenario benchmark (analogue of the paper's Tables 8, 9, 13, 14).

    python bench/compare.py --run bench/runs/luna_full

Requires `extract.py` and `score.py` to have been run on --run (score.py writes the
proposed / proposed+repair labels). Direct-LLM calls are made here (cached under <run>/direct).
Paired statistics use the authors' own functions from neuro_symbolic/evaluation/reproduce_statistics.py.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from baselines import (_sides, direct_llm, lexical_top_rule, monotonic, pdl_features, pdl_oof,  # noqa: E402
                       priority_no_nested, to_theory)
from llm import content_json, usage  # noqa: E402
from score import macro_f1, schema_valid, wilson  # noqa: E402

spec = importlib.util.spec_from_file_location("authors_stats", HERE.parent / "neuro_symbolic/evaluation/reproduce_statistics.py")
AUTH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUTH)


def tier(sc) -> int:
    p = set(sc["phenomena"])
    if "nested_exception" in p:
        return 3
    if p & {"tie", "cycle_resolvable", "cycle_unresolvable", "defeater"}:
        return 2
    if "exception" in p:
        return 1
    return 0


def metrics(preds: dict[str, str], scs: list[dict]) -> dict:
    gold = {s["scenario_id"]: s["gold_label"] for s in scs}
    pairs = [(gold[k], preds[k]) for k in gold]
    k = sum(g == p for g, p in pairs)
    def acc(f):
        sel = [s for s in scs if f(s)]
        return 100 * sum(preds[s["scenario_id"]] == s["gold_label"] for s in sel) / len(sel) if sel else None
    und = [s for s in scs if s["gold_label"] == "Undetermined"]
    return {
        "accuracy": 100 * k / len(pairs), "ci95": [round(x, 1) for x in wilson(k, len(pairs))],
        "macro_f1": macro_f1(pairs),
        "exception_acc": acc(lambda s: "exception" in s["phenomena"]),
        "nested_exception_acc": acc(lambda s: "nested_exception" in s["phenomena"]),
        "undetermined_recall": acc(lambda s: s["gold_label"] == "Undetermined"),
        "false_determinate_pct": 100 * sum(preds[s["scenario_id"]] != "Undetermined" for s in und) / len(und),
        "tier_acc": {t: acc(lambda s, t=t: tier(s) == t) for t in range(4)},
    }


def paired(name, first, second, scs, primary):
    truth = np.array([s["gold_label"] for s in scs])
    a = np.array([first[s["scenario_id"]] for s in scs]) == truth
    b = np.array([second[s["scenario_id"]] for s in scs]) == truth
    fo, so, p = AUTH.mcnemar(a, b)
    delta = a.astype(int) - b.astype(int)
    lo, hi = AUTH.paired_bootstrap(delta)
    return {"comparison": name, "diff_pp": 100 * float(delta.mean()), "ci95_pp": [lo, hi],
            "first_only": fo, "second_only": so, "mcnemar_raw_p": p, "primary": primary}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--scenarios", type=Path, default=HERE / "data/scenarios.jsonl")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    scs = [json.loads(x) for x in a.scenarios.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = {r["scenario_id"]: r for r in csv.DictReader((a.run / "scenario_scores.csv").open())}
    scs = [s for s in scs if s["scenario_id"] in rows]
    ids = [s["scenario_id"] for s in scs]

    # ---- gold formalization (symbolic layer only; Table 8/9 analogue)
    gold_preds = defaultdict(dict)
    for s in scs:
        th = to_theory(s["gold"], s)
        gold_preds["Lexical top-rule"][s["scenario_id"]] = lexical_top_rule(th, s)
        gold_preds["Monotonic forward chaining"][s["scenario_id"]] = monotonic(th, s)
        gold_preds["Priority without nested defeats"][s["scenario_id"]] = priority_no_nested(th, s)
        gold_preds["pcnr (grounded)"][s["scenario_id"]] = s["gold_label"]   # gold labels are pcnr on gold
    gold_table = {m: metrics(p, scs) for m, p in gold_preds.items()}

    # ---- extracted theories (unrepaired) for symbolic baselines + PDL features
    exts, valids = {}, {}
    for s in scs:
        files = sorted((a.run / "extractions").glob(f"{s['scenario_id']}-*.json"))
        try:
            exts[s["scenario_id"]] = content_json(json.loads(files[-1].read_text(encoding="utf-8")))
            valids[s["scenario_id"]] = schema_valid(exts[s["scenario_id"]])[0]
        except (ValueError, KeyError, json.JSONDecodeError, IndexError):
            exts[s["scenario_id"]], valids[s["scenario_id"]] = None, False
    preds = defaultdict(dict)
    for s in scs:
        sid = s["scenario_id"]
        th = to_theory(exts[sid], s) if exts[sid] else None
        preds["Lexical top-rule (extracted)"][sid] = lexical_top_rule(th, s)
        preds["Monotonic (extracted)"][sid] = monotonic(th, s)
        preds["Priority w/o nested defeats (extracted)"][sid] = priority_no_nested(th, s)
        preds["Proposed LLM-to-NMR"][sid] = rows[sid]["label"]
        preds["Proposed + repair"][sid] = rows[sid].get("label_repair") or rows[sid]["label"]
    vocab = sorted({f.split("(")[0] for s in scs for f in [x["atom"] for x in s["gold"]["facts"]]
                    + [y for r in s["gold"]["rules"] for y in r["antecedents"]]})
    X = [pdl_features(exts[s["scenario_id"]], s, vocab, valids[s["scenario_id"]]) for s in scs]
    y = [s["gold_label"] for s in scs]
    for sid, p in zip(ids, pdl_oof(X, y)):
        preds["PDL logistic regression"][sid] = str(p)

    # ---- direct LLM
    with ThreadPoolExecutor(a.workers) as pool:
        direct = dict(zip(ids, pool.map(lambda s: direct_llm(s, a.run), scs)))
    for sid in ids:
        preds["Direct LLM (gpt-6-luna)"][sid] = direct[sid]["label"]
    # unsupported-answer proxy: determinate answer citing no clause whose rule concludes that side
    unsupported = 0
    for s in scs:
        d = direct[s["scenario_id"]]
        if d["label"] == "Undetermined":
            continue
        pos, neg = _sides(s)
        side = pos if d["label"] == "Positive" else neg
        th = to_theory(s["gold"], s)
        side_units = {str(r.source) for r in th.rules.values() if r.type != "defeater" and r.consequent in side}
        if not (set(d["source_ids"]) & side_units):
            unsupported += 1

    order = ["Direct LLM (gpt-6-luna)", "PDL logistic regression", "Lexical top-rule (extracted)",
             "Monotonic (extracted)", "Priority w/o nested defeats (extracted)", "Proposed LLM-to-NMR", "Proposed + repair"]
    table = {m: metrics(preds[m], scs) for m in order}
    table["Direct LLM (gpt-6-luna)"]["determinate_without_supporting_citation_pct"] = 100 * unsupported / len(scs)
    for m in order:
        th = defaultdict(set)
        for s in scs:
            th[s["theory_id"]].add(preds[m][s["scenario_id"]])
        table[m]["paraphrase_agreement"] = f"{sum(len(v) == 1 for v in th.values())}/{len(th)}"

    prim = [paired("Proposed vs direct LLM", preds["Proposed LLM-to-NMR"], preds["Direct LLM (gpt-6-luna)"], scs, True),
            paired("Proposed vs PDL", preds["Proposed LLM-to-NMR"], preds["PDL logistic regression"], scs, True),
            paired("Proposed vs priority/no nested", preds["Proposed LLM-to-NMR"], preds["Priority w/o nested defeats (extracted)"], scs, True)]
    for r, adj in zip(prim, AUTH.holm_adjust([r["mcnemar_raw_p"] for r in prim])):
        r["mcnemar_holm_p"] = adj
    expl = paired("Proposed + repair vs proposed", preds["Proposed + repair"], preds["Proposed LLM-to-NMR"], scs, False)
    expl2 = paired("Proposed + repair vs direct LLM", preds["Proposed + repair"], preds["Direct LLM (gpt-6-luna)"], scs, False)

    out = {"scenarios": len(scs), "gold_formalization": gold_table, "downstream": table,
           "paired": prim + [expl, expl2],
           "direct_llm_usage": usage([d["response"] for d in direct.values()])}
    (a.run / "comparison.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    with (a.run / "predictions_all_methods.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "theory_id", "domain", "tier", "gold"] + order + [f"gold-theory: {m}" for m in gold_preds])
        for s in scs:
            w.writerow([s["scenario_id"], s["theory_id"], s["domain"], tier(s), s["gold_label"]]
                       + [preds[m][s["scenario_id"]] for m in order] + [gold_preds[m][s["scenario_id"]] for m in gold_preds])

    def fmt(v):
        return "—" if v is None else f"{v:.1f}"
    print(f"\n=== Gold formalization ({len(scs)} scenarios): accuracy by tier")
    print(f"{'method':36s} {'acc':>6s} {'F1':>6s} {'T0':>6s} {'T1':>6s} {'T2':>6s} {'T3':>6s}")
    for m, r in gold_table.items():
        print(f"{m:36s} {fmt(r['accuracy']):>6s} {fmt(r['macro_f1']):>6s} " + " ".join(f"{fmt(r['tier_acc'][t]):>6s}" for t in range(4)))
    print(f"\n=== Downstream on extracted theories / direct answers")
    print(f"{'method':42s} {'acc':>6s} {'95% CI':>14s} {'F1':>6s} {'exc':>6s} {'nest':>6s} {'U-rec':>6s} {'falseDet':>8s} {'para':>7s}")
    for m in order:
        r = table[m]
        print(f"{m:42s} {fmt(r['accuracy']):>6s} {str(r['ci95']):>14s} {fmt(r['macro_f1']):>6s} {fmt(r['exception_acc']):>6s} "
              f"{fmt(r['nested_exception_acc']):>6s} {fmt(r['undetermined_recall']):>6s} {fmt(r['false_determinate_pct']):>8s} {r['paraphrase_agreement']:>7s}")
    print("\n=== Paired comparisons (exact McNemar; Holm over the 3 primary; 20k paired bootstrap)")
    for r in out["paired"]:
        print(f"{r['comparison']:34s} {r['diff_pp']:+6.1f} pp [{r['ci95_pp'][0]:+.1f}, {r['ci95_pp'][1]:+.1f}]  "
              f"{r['first_only']}/{r['second_only']} discordant  p={r['mcnemar_raw_p']:.4f}"
              + (f"  Holm p={r['mcnemar_holm_p']:.4f}" if r["primary"] else "  (exploratory)"))
    print("\ndirect LLM usage:", out["direct_llm_usage"],
          "| determinate answers citing no supporting clause:", f"{table['Direct LLM (gpt-6-luna)']['determinate_without_supporting_citation_pct']:.1f}%")


if __name__ == "__main__":
    main()
