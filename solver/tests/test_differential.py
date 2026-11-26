"""Randomised differential test against the authors' reference solver.

Generates random acyclic ground theories (defeasible rules, intermediate literals, targeted
defeaters, declared conflicts between opposed rules, acyclic priorities) and checks that pcnr
(with Eq. 1 taken literally) returns the same label as baselines/symbolic_reasoner_reference.py,
and that every acceptance proof passes the independent checker.

Theories where some argument defeats itself are skipped: the reference code never lets an
argument attack itself, whereas Eq. 4 (and pcnr) does.
"""

import importlib.util
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent / "neuro_symbolic"
sys.path.insert(0, str(ROOT))

from pcnr import run  # noqa: E402
from pcnr import literals as L  # noqa: E402

spec = importlib.util.spec_from_file_location("reference_solver", REPO / "baselines/symbolic_reasoner_reference.py")
REF = importlib.util.module_from_spec(spec)
sys.modules["reference_solver"] = REF
spec.loader.exec_module(REF)

N_THEORIES = 3000
LEVEL = {"M0": 1, "M1": 2, "Q(": 3}


def random_theory(rng: random.Random) -> dict:
    facts = [f"F{i}(c)" for i in range(5)]
    true_facts = {f: rng.random() < 0.6 for f in facts}
    heads = ["Q(c)", "¬Q(c)", "M0(c)", "¬M0(c)", "M1(c)", "¬M1(c)"]
    rules, produced = [], []
    for i in range(rng.randint(2, 8)):
        rid = f"r{i}"
        if rules and rng.random() < 0.25:
            target = rng.choice(rules)["id"]
            ants = rng.sample(facts + produced, k=rng.randint(1, 2))
            rules.append({"id": rid, "antecedents": ants, "consequent": f"block({target})", "type": "defeater",
                          "target": target, "source": "S"})
            continue
        head = rng.choice(heads)
        level = LEVEL[head.lstrip("¬")[:2]]          # atoms are layered: F < M0 < M1 < Q
        pool = facts + [x for x in produced if LEVEL[x.lstrip("¬")[:2]] < level]
        ants = rng.sample(pool, k=min(len(pool), rng.randint(1, 2)))
        rules.append({"id": rid, "antecedents": ants, "consequent": head, "type": "defeasible", "source": "S"})
        if head not in produced:
            produced.append(head)
    derivational = [r for r in rules if r["type"] != "defeater"]
    conflicts = []
    for i, a in enumerate(derivational):
        for b in derivational[i + 1:]:
            if a["consequent"] == L.complement(b["consequent"]) and rng.random() < 0.7:
                conflicts.append((a["id"], b["id"]))
    order = {r["id"]: rng.random() for r in rules}   # acyclic priorities from a random ranking
    candidate = conflicts + [(r["id"], r["target"]) for r in rules if r["type"] == "defeater"]
    priorities = []
    for a, b in candidate:
        if rng.random() < 0.6:
            priorities.append((a, b) if order[a] > order[b] else (b, a))
    return {"query": "Q(c)", "facts": true_facts, "rules": rules,
            "conflicts": [{"left": a, "right": b, "source": "S"} for a, b in conflicts],
            "priorities": [{"stronger": a, "weaker": b, "source": "S"} for a, b in priorities]}


class Differential(unittest.TestCase):
    def test_agrees_with_reference(self):
        rng = random.Random(42)
        compared = skipped = proofs = 0
        mismatches = []
        for n in range(N_THEORIES):
            t = random_theory(rng)
            ours = run(t)
            if any(d["code"] != "SOURCES_UNAVAILABLE" and d["severity"] == "error" for d in ours.diagnostics):
                self.fail(f"generator produced an invalid theory: {ours.diagnostics}")
            if any(e["type"] == "defeat" and e["source"] == e["target"] for e in ours.proof["edges"]) or \
                    _self_defeat(t):
                skipped += 1
                continue
            ref = REF.reason(t)["label"]
            compared += 1
            if ref != ours.label:
                mismatches.append((n, ref, ours.label, t))
            if ours.proof["kind"] == "acceptance":
                proofs += 1
                self.assertTrue(ours.check["ok"], (n, ours.check))
        print(f"\n  differential: {compared} compared, {skipped} skipped (self-defeat), "
              f"{len(mismatches)} mismatches, {proofs} acceptance proofs checked")
        self.assertFalse(mismatches, mismatches[:3])
        self.assertGreater(compared, N_THEORIES * 0.8)


def _self_defeat(t) -> bool:
    from pcnr.reasoner import compile_defeats, construct_arguments
    from pcnr.priority import repair_cycles
    from pcnr.theory import coerce_raw, ground, normalize
    from pcnr.validate import validate
    raw, _ = normalize(coerce_raw(t))
    th = ground(validate(raw).clean_raw)
    args = construct_arguments(th)
    return any(d.attacker == d.target for d in compile_defeats(th, args, repair_cycles(th).effective))


if __name__ == "__main__":
    unittest.main()
