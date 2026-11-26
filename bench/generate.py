"""Synthetic benchmark generator: random defeasible/deontic theories rendered as contract prose.

Each *theory* is a random structure over a domain vocabulary:
  general rule g  (+ optional second supporting rule)
  exception tree  depth 0–3; each exception has the opposite head and prevails over its parent
  defeater        "Clause k does not apply where …" (undercuts rule k)
  tie             an opposed rule linked to g with "neither prevails"            (-> abstention)
  cycle           two clauses that each claim to prevail ("notwithstanding")     (-> κL repair:
                  resolvable by specificity, or unresolved -> abstention + review)
  definition      intermediate concept defined by a strict ("in all cases") or defeasible rule
  distractor      a rule on a different issue
Facts are sampled to balance labels; gold labels come from pcnr on the gold theory.

Each theory is rendered in `variants` paraphrases: clause order, sentence templates and the
way each priority is expressed differ ("notwithstanding Clause p", "subject to Clause e",
"except as provided in Clause e", an inline "unless …", or a precedence schedule entry).
Priorities are never stated as data; they must be read from drafting language.

    python bench/generate.py --theories 120 --variants 2 --seed 42 --out bench/data/scenarios.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "solver"))
sys.path.insert(0, str(HERE))
from domains import DOMAINS  # noqa: E402
from pcnr import Config, run  # noqa: E402
from pcnr import literals as L  # noqa: E402


class Exhausted(Exception):
    pass


@dataclass
class GRule:
    key: str
    ants: list[str]
    head: str | None          # None for defeaters
    type: str                 # strict | defeasible | defeater
    role: str                 # general | support | exception | tie | cycle | defeater | definition | distractor
    parent: str | None = None
    children: list[str] = field(default_factory=list)


@dataclass
class Structure:
    domain: dict
    rules: dict[str, GRule]
    links: list[tuple[str, str, str]]          # (a, b, kind) kind: exception | tie | cycle
    priorities: list[tuple[str, str]]
    conditions: list[str]                      # base condition literals that may become facts
    tags: set[str]


# ---------------------------------------------------------------- structure

def make_structure(rng: random.Random, dom: dict, want_undetermined: bool = False) -> Structure:
    pool = list(dom["conditions"])
    rng.shuffle(pool)
    const = dom["const"]
    lean = dom["lean"]

    def head_lean(head: str) -> int:
        if L.canonical(head) == L.canonical(dom["issue"]):
            return 1
        if L.canonical(head) == L.canonical(dom["opposite"]):
            return -1
        return lean.get(head.split("(")[0], 0)

    def take(k: int, want: int) -> list[str]:
        """k conditions, preferring ones whose lean supports a head of lean `want`."""
        if len(pool) < k:
            raise Exhausted
        out = []
        for _ in range(k):
            pick = next((c for c in pool if lean.get(c) == want), pool[0])
            pool.remove(pick)
            out.append(f"{pick}({const})")
        return out

    rules: dict[str, GRule] = {}
    links, prios, tags = [], [], set()

    def add(role, ants, head, typ="defeasible", parent=None) -> GRule:
        r = GRule(f"{role[:3]}{len(rules)}", ants, head, typ, role, parent)
        rules[r.key] = r
        if parent:
            rules[parent].children.append(r.key)
        return r

    def opp(head):
        return dom["opposite"] if L.canonical(head) == L.canonical(dom["issue"]) else dom["issue"]

    ghead = dom["issue"] if rng.random() < 0.75 else dom["opposite"]
    g = add("general", take(rng.choice([1, 2]), head_lean(ghead)), ghead)
    tags.add("general")
    depth = rng.choices([0, 1, 2, 3], weights=[1, 3, 3, 2])[0]

    def grow(parent: GRule, level: int):
        if level >= depth:
            return
        for _ in range(rng.choice([1, 2]) if level == 0 else 1):
            h = opp(parent.head)
            e = add("exception", take(1, head_lean(h)), h, parent=parent.key)
            links.append((e.key, parent.key, "exception"))
            prios.append((e.key, parent.key))
            tags.add("exception" if level == 0 else "nested_exception")
            if rng.random() < 0.85:
                grow(e, level + 1)

    try:
        grow(g, 0)
        if rng.random() < 0.2:
            s = add("support", take(1, head_lean(g.head)), g.head)
            tags.add("second_support")
            if rng.random() < 0.5:
                h = opp(s.head)
                e = add("exception", take(1, head_lean(h)), h, parent=s.key)
                links.append((e.key, s.key, "exception"))
                prios.append((e.key, s.key))
        x = rng.random()
        if want_undetermined:
            x = 0.0 if rng.random() < 0.5 else 0.2
            resolvable_p = 0.0
        else:
            resolvable_p = 0.6
        h = opp(g.head)
        if x < 0.15:
            t = add("tie", take(1, head_lean(h)), h, parent=g.key)
            links.append((t.key, g.key, "tie"))
            tags.add("tie")
        elif x < 0.30:
            resolvable = rng.random() < resolvable_p
            ants = list(g.ants) + take(1, head_lean(h)) if resolvable else take(1, head_lean(h))
            c = add("cycle", ants, h, parent=g.key)
            links.append((c.key, g.key, "cycle"))
            prios += [(c.key, g.key), (g.key, c.key)]
            tags.add("cycle_resolvable" if resolvable else "cycle_unresolvable")
        if rng.random() < 0.35:
            cands = [r for r in rules.values() if r.role in ("general", "support", "exception", "tie", "cycle")]
            target = rng.choice(cands)
            add("defeater", take(1, -head_lean(target.head)), None, "defeater", parent=target.key)
            tags.add("defeater")
        if rng.random() < 0.4:
            host = rng.choice([r for r in rules.values() if r.role in ("general", "exception", "support")])
            hl = head_lean(host.head)
            inter = next((i for i in dom["intermediates"] if lean.get(i) == hl), rng.choice(list(dom["intermediates"])))
            ilit = f"{inter}({const})"
            typ = "strict" if rng.random() < 0.5 else "defeasible"
            add("definition", take(rng.choice([1, 2]), lean.get(inter, 0)), ilit, typ)
            old = host.ants[0]
            for r in rules.values():        # substitute consistently (keeps cycle scopes intact)
                r.ants = [ilit if a == old else a for a in r.ants]
            tags.add(f"definition_{typ}")
    except Exhausted:
        pass
    if rng.random() < 0.5:
        d = dom["distractor"]
        add("distractor", [f"{d['cond'][0]}({const})"], d["head"])
        tags.add("distractor")
    conditions = sorted({a for r in rules.values() for a in r.ants
                         if a.split("(")[0] in dom["conditions"] or a.split("(")[0] == dom["distractor"]["cond"][0]})
    if pool and rng.random() < 0.3:
        conditions.append(f"{pool.pop()}({const})")   # stated but irrelevant fact
        tags.add("irrelevant_fact")
    return Structure(dom, rules, links, prios, conditions, tags)


# ---------------------------------------------------------------- rendering

def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _phrase(dom: dict, lit: str, as_head: bool) -> str:
    pred = lit.split("(")[0]
    if L.canonical(lit) == L.canonical(dom["issue"]):
        return dom["pos"]
    if L.canonical(lit) == L.canonical(dom["opposite"]):
        return dom["neg"]
    if lit == dom["distractor"]["head"]:
        return dom["distractor"]["phrase"]
    if pred in dom["intermediates"]:
        return dom["intermediates"][pred][0]
    if pred == dom["distractor"]["cond"][0]:
        return dom["distractor"]["cond"][2]
    return dom["conditions"][pred][1]


def render(st: Structure, facts: list[str], rng: random.Random, variant: int) -> dict:
    dom, rules = st.domain, st.rules
    targeted = {r.parent for r in rules.values() if r.role == "defeater"}
    # 1. how each exception link is expressed
    style: dict[tuple[str, str], str] = {}
    inline_host: dict[str, str] = {}
    for a, b, kind in st.links:
        if kind != "exception":
            style[(a, b)] = kind
            continue
        opts = ["notwithstanding", "subject_to", "except_as", "schedule"]
        e, p = rules[a], rules[b]
        if (not e.children and a not in targeted and b not in targeted and b not in inline_host
                and rules[b].role != "cycle"):
            opts.append("inline")
        s = rng.choice(opts)
        style[(a, b)] = s
        if s == "inline":
            inline_host[b] = a
    inlined = set(inline_host.values())
    # 2. clause numbering
    own = [k for k in rules if k not in inlined]
    if variant > 0:
        rng.shuffle(own)
    num = {k: i + 1 for i, k in enumerate(own)}
    rid = {k: f"r{num[k]}" for k in own}
    for host, e in inline_host.items():
        num[e] = num[host]
        rid[e] = f"r{num[host]}b"
    cu = lambda k: f"C{num[k]}"  # noqa: E731

    prefixes: dict[str, list[str]] = {k: [] for k in own}
    suffixes: dict[str, list[str]] = {k: [] for k in own}
    schedule: list[str] = []
    conflicts, priorities = [], []
    for a, b, kind in st.links:
        s = style[(a, b)]
        if s == "notwithstanding":
            prefixes[a].append(f"notwithstanding Clause {num[b]}")
            src = [cu(a)]
        elif s == "subject_to":
            prefixes[b].append(f"subject to Clause {num[a]}")
            src = [cu(b)]
        elif s == "except_as":
            suffixes[b].append(f"except as provided in Clause {num[a]}")
            src = [cu(b)]
        elif s == "inline":
            src = [cu(b)]
        elif s == "schedule":
            schedule.append(rng.choice([
                f"In case of conflict, Clause {num[a]} prevails over Clause {num[b]}.",
                f"Where Clauses {num[a]} and {num[b]} both apply, Clause {num[a]} takes precedence.",
            ]))
            src = [f"S{len(schedule)}"]
        elif s == "tie":
            schedule.append(f"Where Clauses {num[b]} and {num[a]} both apply, neither clause prevails over the other.")
            src = [f"S{len(schedule)}"]
        else:  # cycle: each clause claims to prevail over the other
            prefixes[a].append(f"notwithstanding Clause {num[b]}")
            prefixes[b].append(f"notwithstanding Clause {num[a]}")
            src = [cu(a), cu(b)]
        conflicts.append({"left": rid[a], "right": rid[b], "source": src[0], "sources": src})
        if kind == "exception" or kind == "cycle":
            priorities.append({"stronger": rid[a], "weaker": rid[b], "source": src[0], "sources": src})
        if kind == "cycle":
            priorities.append({"stronger": rid[b], "weaker": rid[a], "source": src[-1], "sources": src})

    # 3. clause texts
    templates = [("If {C}, {H}", "if {C}, {H}"), ("Where {C}, {H}", "where {C}, {H}"), ("{H_cap} if {C}", "{H} if {C}")]
    clause_text: dict[int, str] = {}
    for k in own:
        r = rules[k]
        cond = " and ".join(_phrase(dom, a, False) for a in r.ants)
        if r.role == "defeater":
            body = rng.choice([f"Clause {num[r.parent]} does not apply where {cond}",
                               f"Clause {num[r.parent]} cannot be relied upon if {cond}"])
        elif r.role == "definition":
            lead = "In all cases" if r.type == "strict" else "As a general rule"
            body = f"{lead}, {_phrase(dom, r.head, True)} if {cond}"
        else:
            head = _phrase(dom, r.head, True)
            plain, lower = rng.choice(templates)
            if prefixes[k]:
                body = _cap(" and ".join(prefixes[k])) + ", " + lower.format(C=cond, H=head)
            else:
                body = plain.format(C=cond, H=head, H_cap=_cap(head))
        if k in inline_host:
            e = rules[inline_host[k]]
            body += ", unless " + " and ".join(_phrase(dom, a, False) for a in e.ants)
        for sfx in suffixes[k]:
            body += f", {sfx}"
        clause_text[num[k]] = f"Clause {num[k]}. {_cap(body)}."

    # 4. glossary and case file
    lits = sorted({a for r in rules.values() for a in r.ants} | {r.head for r in rules.values() if r.head}
                  | set(facts))
    gloss = []
    for lit in lits:
        node = L.parse(lit)
        if isinstance(node, L.Deon):
            continue
        pred = lit.lstrip("¬").split("(")[0]
        if lit.startswith("¬"):
            continue
        if L.canonical(lit) == L.canonical(dom["issue"]):
            meaning = dom["issue_gloss"]
        elif lit == dom["distractor"]["head"]:
            meaning = dom["distractor"]["gloss"]
        else:
            meaning = _phrase(dom, lit, False)
        gloss.append(f"{lit} means that {meaning}")
    if L.parse(dom["issue"]).__class__ is L.Deon:
        gloss.append(dom["issue_gloss"].replace(":", " means that", 1))
        gloss.append("Obl(φ) means φ is obligatory, Perm(φ) that φ is permitted, Forb(φ) that φ is forbidden")
    rng.shuffle(gloss)
    fact_order = list(facts)
    rng.shuffle(fact_order)

    units, gold_facts = [], []

    def doc(doc_id, items):
        offset = 0
        for uid, path, text in items:
            units.append({"source_unit_id": uid, "doc_id": doc_id, "path": path,
                          "span": {"start": offset, "end": offset + len(text)}, "text": text})
            offset += len(text) + 1
    doc("GLOSSARY", [("D1", "defined terms", "Defined terms. " + "; ".join(gloss) + ".")])
    doc("CONTRACT", [(f"C{n}", f"clause {n}", clause_text[n]) for n in sorted(clause_text)])
    if schedule:
        doc("SCHEDULE", [(f"S{i + 1}", f"schedule 1 para {i + 1}", f"Schedule 1, paragraph {i + 1}. {t}")
                         for i, t in enumerate(schedule)])
    fitems = []
    for i, f in enumerate(fact_order):
        pred = f.split("(")[0]
        sent = dom["distractor"]["cond"][1] if pred == dom["distractor"]["cond"][0] else dom["conditions"][pred][0]
        fitems.append((f"F{i + 1}", f"case file item {i + 1}", sent))
        gold_facts.append({"atom": f, "source": f"F{i + 1}"})
    doc("CASEFILE", fitems)

    gold_rules = []
    for k, r in rules.items():
        obj = {"id": rid[k], "antecedents": list(r.ants), "type": r.type, "source": cu(k)}
        if r.role == "defeater":
            obj.update(consequent=f"block({rid[r.parent]})", target=rid[r.parent])
        else:
            obj["consequent"] = L.canonical(r.head)
        gold_rules.append(obj)
    return {"source_units": units,
            "gold": {"facts": gold_facts, "rules": gold_rules, "conflicts": conflicts, "priorities": priorities},
            "styles": sorted({s for s in style.values()})}


# ---------------------------------------------------------------- labels

def gold_labels(dom: dict, gold: dict, units) -> tuple[str, str, object]:
    obj = {**gold, "query": dom["issue"]}
    res_cw = run(obj, source_units=units, config=Config(absence_policy="negative", require_fact_sources=True,
                                                         deontic_duality=dom["deontic_duality"]))
    res_eq1 = run(obj, source_units=units, config=Config(absence_policy="undetermined",
                                                          deontic_duality=dom["deontic_duality"]))
    return res_cw.label, res_eq1.label, res_cw


def generate(n_theories: int, variants: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    targets = ["Positive", "Negative", "Positive", "Negative", "Undetermined"]
    out, t = [], 0
    attempts = 0
    while t < n_theories:
        attempts += 1
        dom = DOMAINS[list(DOMAINS)[t % len(DOMAINS)]]
        want = targets[t % len(targets)]
        st = make_structure(rng, dom, want_undetermined=(want == "Undetermined"))
        best = None
        for _ in range(60):
            facts = sorted(c for c in st.conditions if rng.random() < 0.5)
            probe = render(st, facts, random.Random(0), 0)
            cw, eq1, res = gold_labels(dom, probe["gold"], probe["source_units"])
            if any("INCONSISTENT" in x for x in res.review_reasons):
                continue
            if any(d["severity"] == "error" for d in res.diagnostics):
                raise RuntimeError(f"generator produced invalid gold theory: {res.diagnostics}")
            if best is None or cw == want:
                best = (facts, cw, eq1, res)
            if cw == want:
                break
        if best is None:
            continue
        facts, cw, eq1, res = best
        tid = f"T{t + 1:04d}"
        for v in range(variants):
            rend = render(st, facts, random.Random(f"{seed}-{tid}-{v}"), v)
            cw_v, eq1_v, res_v = gold_labels(dom, rend["gold"], rend["source_units"])
            assert (cw_v, eq1_v) == (cw, eq1), (tid, v, cw_v, cw)
            assert res_v.check is None or res_v.check.get("ok") in (True, None), res_v.check
            out.append({
                "scenario_id": f"{tid}-v{v + 1}", "theory_id": tid, "variant": v + 1, "domain": dom["name"],
                "phenomena": sorted(st.tags | {f"style_{s}" for s in rend["styles"]}),
                "query_nl": dom["query_nl"], "target_literal": L.canonical(dom["issue"]),
                "decision_config": {"deontic_duality": dom["deontic_duality"]},
                "source_units": rend["source_units"], "gold": rend["gold"],
                "gold_label": cw, "gold_label_eq1": eq1, "gold_reason": res_v.reason,
                "gold_review_flag": res_v.review_flag,
            })
        t += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theories", type=int, default=120)
    ap.add_argument("--variants", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=HERE / "data/scenarios.jsonl")
    a = ap.parse_args()
    rows = generate(a.theories, a.variants, a.seed)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    from collections import Counter
    print(f"wrote {len(rows)} scenarios ({a.theories} theories x {a.variants}) to {a.out}")
    print("labels (closed world):", dict(Counter(r["gold_label"] for r in rows)))
    print("labels (Eq. 1):       ", dict(Counter(r["gold_label_eq1"] for r in rows)))
    print("domains:", dict(Counter(r["domain"] for r in rows)))
    print("phenomena:", dict(Counter(p for r in rows if r["variant"] == 1 for p in r["phenomena"])))
    print("review flags:", sum(r["gold_review_flag"] for r in rows))
    v1 = [r for r in rows if r["variant"] == 1]
    sig = lambda r: json.dumps([sorted((sorted(x["antecedents"]), x["type"], x.get("consequent") if x["type"] != "defeater" else "block") for x in r["gold"]["rules"]), sorted(f["atom"] for f in r["gold"]["facts"])])  # noqa: E731
    print("distinct theories (rules+facts, ids ignored):", len({sig(r) for r in v1}), "of", len(v1))


if __name__ == "__main__":
    main()
