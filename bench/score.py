"""Score an extraction run against the benchmark gold (paper §3.10.4 metrics).

    python bench/score.py --run bench/runs/gpt4o [--no-repair] [--model gpt-4o-2024-08-06]

Object alignment (one-to-one, content-based, ids ignored):
  facts       canonical literal equality
  rules       derivational: same antecedent set and consequent; matched with a different type
              counts once as a type error (TE). defeaters: same antecedents and the target maps to
              the gold target through the rule alignment.
  conflicts   unordered pairs mapped through the rule alignment; defeater-target pairs excluded
  priorities  directed pairs mapped through the rule alignment; reversed = direction error (DE);
              pairs involving defeaters excluded
  FEER = (FP+FN)/(TP+FP+FN); REER = (FP+FN+TE)/(TP+FP+FN+TE); PEER = (FP+FN+DE)/(TP+FP+FN+DE)
  CEER (conflicts) = (FP+FN)/(TP+FP+FN)
  citation accuracy: matched objects whose cited unit is a gold source unit of that object
Downstream: pcnr on the extracted theory (closed-world policy, deontic duality for deontic
domains, fact provenance required), without and with one bounded LLM repair pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "solver"))
sys.path.insert(0, str(HERE))
from extract import SCHEMA, make_repair_hook, reextract  # noqa: E402
from llm import content_json, usage  # noqa: E402
from pcnr import Config, run  # noqa: E402
from pcnr import literals as L  # noqa: E402

LABELS = ("Positive", "Negative", "Undetermined")


def canon(x):
    try:
        return L.canonical(x)
    except (L.LiteralError, TypeError, AttributeError):
        return str(x)


def unit(ptr):
    return str(ptr or "").partition("#")[0]


# ---------------------------------------------------------------- alignment

def align(gold: dict, ext: dict) -> dict:
    out = {}
    # facts
    gf = {canon(f["atom"]): f["source"] for f in gold["facts"]}
    xf = {}
    for f in ext.get("facts", []):
        if isinstance(f, dict) and "atom" in f:
            xf.setdefault(canon(f["atom"]), f.get("source"))
    tp = gf.keys() & xf.keys()
    out["facts"] = dict(TP=len(tp), FP=len(xf.keys() - gf.keys()), FN=len(gf.keys() - xf.keys()))
    cites = [(xf[k] is not None and unit(xf[k]) == gf[k]) for k in tp]

    # rules
    g_rules = {r["id"]: r for r in gold["rules"]}
    x_rules = [r for r in ext.get("rules", []) if isinstance(r, dict)]
    gkey = lambda r: (frozenset(canon(a) for a in r.get("antecedents", [])), canon(r.get("consequent")))  # noqa: E731
    unmatched_g = {rid for rid, r in g_rules.items() if r["type"] != "defeater"}
    idmap: dict[str, str] = {}
    TP = TE = 0
    x_left = []
    for r in x_rules:
        if r.get("type") == "defeater" or L.is_blocker(str(r.get("consequent", ""))):
            x_left.append(r)
            continue
        k = gkey(r)
        hit = next((g for g in sorted(unmatched_g) if gkey(g_rules[g]) == k and g_rules[g]["type"] == r.get("type")), None) \
            or next((g for g in sorted(unmatched_g) if gkey(g_rules[g]) == k), None)
        if hit is None:
            x_left.append(r)
            continue
        unmatched_g.discard(hit)
        idmap[str(r.get("id"))] = hit
        if g_rules[hit]["type"] == r.get("type"):
            TP += 1
        else:
            TE += 1
        cites.append(unit(r.get("source")) == g_rules[hit]["source"])
    g_def = {rid for rid, r in g_rules.items() if r["type"] == "defeater"}
    FP = 0
    for r in x_left:
        if r.get("type") == "defeater":
            ants = frozenset(canon(a) for a in r.get("antecedents", []))
            tgt = idmap.get(str(r.get("target")))
            hit = next((g for g in sorted(g_def) if frozenset(canon(a) for a in g_rules[g]["antecedents"]) == ants
                        and g_rules[g]["target"] == tgt), None)
            if hit:
                g_def.discard(hit)
                idmap[str(r.get("id"))] = hit
                if canon(r.get("consequent")) == f"block({r.get('target')})":
                    TP += 1
                else:          # right type and target, malformed head (e.g. ¬q instead of block(t))
                    TE += 1
                cites.append(unit(r.get("source")) == g_rules[hit]["source"])
                continue
        FP += 1
    FN = len(unmatched_g) + len(g_def)
    out["rules"] = dict(TP=TP, FP=FP, FN=FN, TE=TE)

    # conflicts
    def_target_pairs = {frozenset((rid, r["target"])) for rid, r in g_rules.items() if r["type"] == "defeater"}
    g_conf = {frozenset((c["left"], c["right"])): c for c in gold["conflicts"]}
    x_conf = {}
    for c in ext.get("conflicts", []):
        if not isinstance(c, dict):
            continue
        a, b = idmap.get(str(c.get("left"))), idmap.get(str(c.get("right")))
        key = frozenset((a, b)) if a and b else ("unmapped", id(c))
        if key in def_target_pairs:
            continue
        x_conf.setdefault(key, c)
    tp = g_conf.keys() & x_conf.keys()
    out["conflicts"] = dict(TP=len(tp), FP=len(x_conf.keys() - g_conf.keys()), FN=len(g_conf.keys() - x_conf.keys()))
    cites += [unit(x_conf[k].get("source")) in g_conf[k]["sources"] for k in tp]

    # priorities
    g_pri = {(p["stronger"], p["weaker"]): p for p in gold["priorities"]}
    defeaters = {rid for rid, r in g_rules.items() if r["type"] == "defeater"}
    TP = FP = DE = 0
    hit_g = set()
    for p in ext.get("priorities", []):
        if not isinstance(p, dict):
            continue
        s, w = idmap.get(str(p.get("stronger"))), idmap.get(str(p.get("weaker")))
        if s in defeaters or w in defeaters:
            continue
        if s and w and (s, w) in g_pri and (s, w) not in hit_g:
            TP += 1
            hit_g.add((s, w))
            cites.append(unit(p.get("source")) in g_pri[(s, w)]["sources"])
        elif s and w and (w, s) in g_pri and (w, s) not in hit_g and (s, w) not in g_pri:
            DE += 1
            hit_g.add((w, s))
        else:
            FP += 1
    out["priorities"] = dict(TP=TP, FP=FP, FN=len(g_pri.keys() - hit_g), DE=DE)
    out["citations"] = dict(correct=sum(cites), total=len(cites))
    out["exact"] = (out["facts"]["FP"] + out["facts"]["FN"] + out["rules"]["FP"] + out["rules"]["FN"]
                    + out["rules"]["TE"] + out["conflicts"]["FP"] + out["conflicts"]["FN"]
                    + out["priorities"]["FP"] + out["priorities"]["FN"] + out["priorities"]["DE"]) == 0
    return out


def rate(c: dict, extra: str | None = None) -> float:
    err = c["FP"] + c["FN"] + (c.get(extra, 0) if extra else 0)
    den = c["TP"] + err
    return 100 * err / den if den else 0.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (100 * (c - h) / d, 100 * (c + h) / d)


def macro_f1(pairs) -> float:
    f = []
    for lab in LABELS:
        tp = sum(g == lab and p == lab for g, p in pairs)
        fp = sum(g != lab and p == lab for g, p in pairs)
        fn = sum(g == lab and p != lab for g, p in pairs)
        f.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return 100 * sum(f) / len(f)


# ---------------------------------------------------------------- scoring

def schema_valid(obj) -> tuple[bool, str]:
    try:
        import jsonschema
    except ImportError:
        return True, "jsonschema unavailable"
    try:
        jsonschema.validate(obj, SCHEMA)
        return True, ""
    except jsonschema.ValidationError as e:
        return False, e.message


def reason(sc: dict, theory: dict | None, hook=None):
    if theory is None:
        return None
    cfg = Config(absence_policy="negative", require_fact_sources=True,
                 deontic_duality=sc["decision_config"]["deontic_duality"])
    return run(theory, query=sc["target_literal"], source_units=sc["source_units"], config=cfg, repair_hook=hook)


def score_run(run_dir: Path, scenarios: list[dict], model: str, repair: bool) -> dict:
    rows, repair_calls = [], []
    agg = defaultdict(Counter)
    extraction_responses = []
    for sc in scenarios:
        files = sorted((run_dir / "extractions").glob(f"{sc['scenario_id']}-*.json"))
        if not files:
            continue
        resp = json.loads(files[-1].read_text(encoding="utf-8"))
        extraction_responses.append(resp)
        row = {"scenario_id": sc["scenario_id"], "theory_id": sc["theory_id"], "domain": sc["domain"],
               "gold": sc["gold_label"], "phenomena": " ".join(sc["phenomena"])}
        try:
            ext = content_json(resp)
            valid, why = schema_valid(ext)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            ext, valid, why = None, False, f"unparseable: {e}"
        row["schema_valid"] = valid
        if ext is not None:
            al = align(sc["gold"], ext)
            for k in ("facts", "rules", "conflicts", "priorities", "citations"):
                agg[k].update(al[k])
            row["exact_theory"] = al["exact"]
            row.update({f"{k}_{m}": v for k in ("facts", "rules", "conflicts", "priorities") for m, v in al[k].items()})
        else:
            row["exact_theory"] = False
        res = reason(sc, ext if valid or ext is not None else None)
        row["label"] = res.label if res else "Undetermined"
        row["review"] = res.review_flag if res else True
        row["proof_kind"] = res.proof["kind"] if res and res.proof else None
        row["check_ok"] = res.check.get("ok") if res and res.check else None
        if repair:
            ext2, valid2 = ext, valid
            if not valid:
                r2 = reextract(model, sc, run_dir, why)
                repair_calls.append(r2)
                try:
                    ext2 = content_json(r2)
                    valid2, _ = schema_valid(ext2)
                except (ValueError, KeyError, json.JSONDecodeError):
                    ext2, valid2 = None, False
            calls: list = []
            hook = make_repair_hook(model, sc, ext2 or {}, run_dir, calls) if ext2 is not None else None
            res2 = reason(sc, ext2, hook)
            repair_calls += calls
            row["schema_valid_repair"] = valid2
            row["label_repair"] = res2.label if res2 else "Undetermined"
            row["review_repair"] = res2.review_flag if res2 else True
            row["proof_kind_repair"] = res2.proof["kind"] if res2 and res2.proof else None
            row["check_ok_repair"] = res2.check.get("ok") if res2 and res2.check else None
            row["repair_calls"] = len(calls) + (0 if valid else 1)
            if ext2 is not None and (not valid or calls):
                al2 = align(sc["gold"], ext2)
                for k in ("facts", "rules", "conflicts", "priorities", "citations"):
                    agg[f"repair_{k}"].update(al2[k])
            elif ext2 is not None:
                for k in ("facts", "rules", "conflicts", "priorities", "citations"):
                    agg[f"repair_{k}"].update(align(sc["gold"], ext2)[k])
        rows.append(row)

    n = len(rows)

    def downstream(lab_key, kind_key, check_key):
        pairs = [(r["gold"], r[lab_key]) for r in rows]
        k = sum(g == p for g, p in pairs)
        proofs = [r for r in rows if r[kind_key] in ("acceptance", "absence")]
        return {"accuracy": 100 * k / n, "accuracy_ci95": wilson(k, n), "macro_f1": macro_f1(pairs),
                "proof_coverage": 100 * len(proofs) / n,
                "proof_validity": 100 * sum(bool(r[check_key]) for r in proofs) / len(proofs) if proofs else None,
                "confusion": dict(Counter(f"{g}->{p}" for g, p in pairs if g != p))}

    def extraction(prefix=""):
        a = {k: agg[f"{prefix}{k}"] for k in ("facts", "rules", "conflicts", "priorities", "citations")}
        return {"FEER": rate(a["facts"]), "REER": rate(a["rules"], "TE"), "PEER": rate(a["priorities"], "DE"),
                "CEER": rate(a["conflicts"]),
                "citation_accuracy": 100 * a["citations"]["correct"] / max(1, a["citations"]["total"]),
                "counts": {k: dict(v) for k, v in a.items()}}

    summary = {
        "run": str(run_dir), "model": model, "scenarios": n,
        "schema_valid_pct": 100 * sum(r["schema_valid"] for r in rows) / n,
        "exact_theory_pct": 100 * sum(bool(r["exact_theory"]) for r in rows) / n,
        "extraction": extraction(),
        "downstream": downstream("label", "proof_kind", "check_ok"),
        "usage_extraction": usage(extraction_responses),
    }
    if repair:
        summary["repair"] = {
            "schema_valid_pct": 100 * sum(r["schema_valid_repair"] for r in rows) / n,
            "scenarios_repaired": sum(r["repair_calls"] > 0 for r in rows),
            "extraction": extraction("repair_"),
            "downstream": downstream("label_repair", "proof_kind_repair", "check_ok_repair"),
            "usage": usage(repair_calls),
        }
    by = defaultdict(list)
    for r in rows:
        by[f"domain={r['domain']}"].append(r["gold"] == r["label"])
        by[f"gold={r['gold']}"].append(r["gold"] == r["label"])
        for p in r["phenomena"].split():
            if not p.startswith("style_"):
                by[f"phen={p}"].append(r["gold"] == r["label"])
    summary["accuracy_by"] = {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by.items())}
    # theory-level consistency across paraphrases
    th = defaultdict(list)
    for r in rows:
        th[r["theory_id"]].append(r["label"])
    multi = [v for v in th.values() if len(v) > 1]
    summary["paraphrase_label_agreement"] = f"{sum(len(set(v)) == 1 for v in multi)}/{len(multi)}"
    return summary, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--scenarios", type=Path, default=HERE / "data/scenarios.jsonl")
    ap.add_argument("--no-repair", action="store_true")
    a = ap.parse_args()
    cfg = json.loads((a.run / "config.json").read_text())
    scs = [json.loads(x) for x in a.scenarios.read_text(encoding="utf-8").splitlines() if x.strip()]
    summary, rows = score_run(a.run, scs, cfg["model"], repair=not a.no_repair)
    (a.run / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    with (a.run / "scenario_scores.csv").open("w", newline="") as fh:
        keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in rows[0], list(rows[0]).index(k) if k in rows[0] else 0))
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({k: v for k, v in summary.items() if k != "accuracy_by"}, indent=2, ensure_ascii=False))
    print("accuracy by:", json.dumps(summary["accuracy_by"], indent=1))


if __name__ == "__main__":
    main()
