"""Baselines from the paper's Table 7, on our benchmark.

  direct_llm               gpt-6-luna answers from the same evidence package (authors' baseline prompt
                           + label definitions matching the gold semantics)
  lexical_top_rule         among rules whose antecedents hold (monotonic closure), take the one whose
                           clause text best overlaps the query (token Jaccard); its head decides
  monotonic                forward chaining over all strict/defeasible rules, ignoring conflicts
  priority_no_nested       one-level priority overrides: a fired rule for q/¬q is dropped if a fired
                           conflicting rule is stronger, or a fired defeater targets it; overridden
                           rules still override (no reinstatement, no nested defeat)
  pdl                      multinomial logistic regression on the Table 6 features, 10-fold stratified
                           out-of-fold predictions (C=1, lbfgs, max_iter=1000, shuffle, random_state=42)
Symbolic baselines use the same decision conventions as pcnr: no rule for either side -> Negative
(closed world); both sides -> Undetermined; D-axiom duality in deontic domains.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "solver"))
sys.path.insert(0, str(HERE))
from extract import user_message  # noqa: E402
from llm import MODEL, chat, content_json  # noqa: E402
from pcnr import literals as L  # noqa: E402
from pcnr.theory import coerce_raw, ground, normalize  # noqa: E402
from pcnr.validate import validate  # noqa: E402

AUTHORS = HERE.parent / "neuro_symbolic"
DIRECT_SYSTEM = (AUTHORS / "prompts/end_to_end_llm_rag_baseline_prompt.md").read_text(encoding="utf-8") + """
Label definitions:
- Positive: the query holds under the evidence.
- Negative: the query does not hold: its opposite is established, or no provision establishes it.
- Undetermined: provisions conflict and the evidence does not resolve which prevails.
Return one JSON object: {"label": "Positive" | "Negative" | "Undetermined", "rationale": "<short>",
"source_ids": ["<unit id>", ...]}."""


def direct_llm(sc: dict, run_dir: Path, model: str = MODEL) -> dict:
    resp = chat(model, DIRECT_SYSTEM, user_message(sc), cache_dir=run_dir / "direct", tag=sc["scenario_id"])
    try:
        out = content_json(resp)
        label = out.get("label") if out.get("label") in ("Positive", "Negative", "Undetermined") else "Undetermined"
        return {"label": label, "source_ids": [str(s) for s in out.get("source_ids", [])],
                "rationale": out.get("rationale", ""), "response": resp}
    except (ValueError, KeyError, json.JSONDecodeError):
        return {"label": "Undetermined", "source_ids": [], "rationale": "", "response": resp}


# ---------------------------------------------------------------- symbolic baselines

def to_theory(obj: dict, sc: dict):
    """Validated, grounded theory (invalid objects dropped) or None."""
    raw, _ = normalize(coerce_raw(obj, query=sc["target_literal"], source_units=sc["source_units"]))
    rep = validate(raw, require_sources=False)
    if any(d.code == "QUERY_INVALID" for d in rep.diagnostics):
        return None
    return ground(rep.clean_raw)


def _sides(sc):
    q = L.canonical(sc["target_literal"])
    pos, neg = {q}, {L.complement(q)}
    if sc["decision_config"]["deontic_duality"]:
        pos |= L.duality_positives(q)
        neg |= L.duality_negatives(q)
    return pos, neg


def _closure(theory):
    known = set(theory.facts)
    changed = True
    while changed:
        changed = False
        for r in theory.rules.values():
            if r.type != "defeater" and r.consequent not in known and all(a in known for a in r.antecedents):
                known.add(r.consequent)
                changed = True
    fired = [r for r in theory.rules.values() if all(a in known for a in r.antecedents)]
    return known, fired


def _decide(pos_hit: bool, neg_hit: bool, any_rule: bool) -> str:
    if pos_hit and neg_hit:
        return "Undetermined"
    if pos_hit:
        return "Positive"
    if neg_hit:
        return "Negative"
    return "Undetermined" if any_rule else "Negative"


def monotonic(theory, sc) -> str:
    if theory is None:
        return "Undetermined"
    known, _ = _closure(theory)
    pos, neg = _sides(sc)
    return _decide(bool(known & pos), bool(known & neg), False)


def priority_no_nested(theory, sc) -> str:
    if theory is None:
        return "Undetermined"
    _, fired = _closure(theory)
    pos, neg = _sides(sc)
    issue = [r for r in fired if r.type != "defeater" and r.consequent in pos | neg]
    fired_ids = {r.id for r in fired}
    surviving = []
    for r in issue:
        overridden = any(
            (s, r.id) in theory.priorities and frozenset((s, r.id)) in theory.conflicts
            for s in fired_ids if s != r.id
        ) or any(d.type == "defeater" and d.target == r.id and (r.id, d.id) not in theory.priorities for d in fired)
        if not overridden:
            surviving.append(r)
    return _decide(any(r.consequent in pos for r in surviving), any(r.consequent in neg for r in surviving),
                   bool(issue))


_TOK = re.compile(r"[a-z]+")


def lexical_top_rule(theory, sc) -> str:
    if theory is None:
        return "Undetermined"
    _, fired = _closure(theory)
    pos, neg = _sides(sc)
    texts = {u["source_unit_id"]: u["text"] for u in sc["source_units"]}
    qtok = set(_TOK.findall(sc["query_nl"].lower()))
    best, best_score = None, -1.0
    for r in sorted(fired, key=lambda r: r.id):
        if r.type == "defeater" or r.consequent not in pos | neg:
            continue
        ctok = set(_TOK.findall(texts.get(str(r.source).partition("#")[0], "").lower()))
        score = len(qtok & ctok) / max(1, len(qtok | ctok))
        if score > best_score:
            best, best_score = r, score
    if best is None:
        return "Negative"
    return "Positive" if best.consequent in pos else "Negative"


# ---------------------------------------------------------------- PDL (Table 6 features)

def pdl_features(ext: dict | None, sc: dict, vocabulary: list[str], schema_valid: bool) -> list[float]:
    theory = to_theory(ext, sc) if ext else None
    if theory is None:
        return [0.0] * (len(vocabulary) + 9)
    preds = {f.split("(")[0].lstrip("¬") for f in theory.facts}
    known, fired = _closure(theory)
    pos, neg = _sides(sc)
    issue = [r for r in fired if r.type != "defeater" and r.consequent in pos | neg]
    conflicting = any(r.consequent in pos for r in issue) and any(r.consequent in neg for r in issue)
    fired_ids = {r.id for r in fired}
    has_prio = any(s in fired_ids and w in fired_ids for s, w in theory.priorities)
    sp = sum((r.confidence or 0.0) for r in issue if r.consequent in pos)
    sn = sum((r.confidence or 0.0) for r in issue if r.consequent in neg)
    sources = {str(o.source).partition("#")[0] for o in list(theory.rules.values()) + list(theory.facts.values()) if o.source}
    return [1.0 if v in preds else 0.0 for v in vocabulary] + [
        float(len(fired)), float(conflicting), float(len(theory.conflicts)), float(has_prio),
        sp, sn, sp - sn, float(len(sources)), float(schema_valid)]


def pdl_oof(X, y, seed: int = 42):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    clf = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
    folds = StratifiedKFold(n_splits=10, shuffle=True, random_state=seed)
    return list(cross_val_predict(clf, np.asarray(X), np.asarray(y), cv=folds))
