import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent / "neuro_symbolic"
sys.path.insert(0, str(ROOT))

from pcnr import Config, run  # noqa: E402
from pcnr import literals as L  # noqa: E402
from pcnr.check import check  # noqa: E402
from pcnr.theory import coerce_raw, ground, normalize  # noqa: E402
from pcnr.validate import validate  # noqa: E402

FIX = REPO / "fixtures/formal_reasoning"


def fixture(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def theory_of(obj, **kw):
    raw, _ = normalize(coerce_raw(obj, **kw))
    return ground(validate(raw).clean_raw)


def R(id, ants, cons, type="defeasible", **kw):
    return {"id": id, "antecedents": ants, "consequent": cons, "type": type, "source": "S", **kw}


def T(query, facts, rules, conflicts=(), priorities=(), **kw):
    return {"query": query, "facts": {f: True for f in facts}, "rules": rules,
            "conflicts": [{"left": a, "right": b, "source": "S"} for a, b in conflicts],
            "priorities": [{"stronger": a, "weaker": b, "source": "S"} for a, b in priorities], **kw}


def codes(result):
    return {d["code"] for d in result.diagnostics}


class Literals(unittest.TestCase):
    def test_canonical_forms(self):
        self.assertEqual(L.canonical("~Q( c )"), "¬Q(c)")
        self.assertEqual(L.canonical("not Q(c)"), "¬Q(c)")
        self.assertEqual(L.canonical("¬¬Q(c)"), "Q(c)")
        self.assertEqual(L.canonical("Forb(Pay(c, s))"), "Obl(¬Pay(c, s))")
        self.assertEqual(L.canonical("Prohibited(Pay(c))"), "Obl(¬Pay(c))")
        self.assertEqual(L.canonical("¬Forb(Pay(c))"), "¬Obl(¬Pay(c))")
        self.assertEqual(L.pretty("Obl(¬Pay(c))"), "Forb(Pay(c))")

    def test_complement_and_opposition(self):
        self.assertEqual(L.complement("Obl(a)"), "¬Obl(a)")
        self.assertTrue(L.opposed("Obl(a)", "Forb(a)".replace("Forb(a)", L.canonical("Forb(a)"))))
        self.assertTrue(L.opposed("Perm(a)", L.canonical("Forb(a)")))
        self.assertTrue(L.opposed("Perm(¬a)", "Obl(a)"))
        self.assertFalse(L.opposed("Obl(a)", "Perm(a)"))
        self.assertFalse(L.opposed("P(c)", "Q(c)"))

    def test_parse_errors(self):
        for bad in ("", "P(", "P(a,)", "Obl()", "P(a) junk"):
            with self.assertRaises(L.LiteralError):
                L.parse(bad)


class AuthorsFixtures(unittest.TestCase):
    def test_labels_and_proofs(self):
        for f in sorted(FIX.glob("*.json")):
            t = json.loads(f.read_text(encoding="utf-8"))
            r = run(t)
            self.assertEqual(r.label, t["expected_label"], f.name)
            if r.proof["kind"] == "acceptance":
                self.assertTrue(r.check["ok"], (f.name, r.check))

    def test_blocker_never_reported(self):
        r = run(fixture("targeted_defeater"))
        self.assertFalse(any(L.is_blocker(c) for c in r.accepted))

    def test_reproducible(self):  # Proposition 2
        a, b = run(fixture("nested_exception")), run(fixture("nested_exception"))
        self.assertEqual(json.dumps(a.proof, sort_keys=True), json.dumps(b.proof, sort_keys=True))

    def test_proof_matches_authors_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = json.loads((REPO / "schemas/proof_graph.schema.json").read_text())
        jsonschema.validate(run(fixture("nested_exception")).proof, schema)


class Semantics(unittest.TestCase):
    def test_paper_table2_case_study(self):
        """§4.9: all five exceptions active; grounded extension accepts the penalty."""
        rules = [R("r1", ["ValidContract(x)", "Delay(x)"], "EnforceablePenalty(x)"),
                 R("r2", ["ForceMajeure(x)"], "¬EnforceablePenalty(x)"),
                 R("r3", ["ForceMajeure(x)", "GrossNegligence(x)"], "EnforceablePenalty(x)"),
                 R("r4", ["NoticeMissing(x)"], "¬EnforceablePenalty(x)"),
                 R("r5", ["NoticeWaived(x)"], "EnforceablePenalty(x)")]
        facts = [f"{p}(case_17)" for p in ("ValidContract", "Delay", "ForceMajeure", "GrossNegligence",
                                            "NoticeMissing", "NoticeWaived")]
        pairs = [("r2", "r1"), ("r3", "r2"), ("r4", "r1"), ("r5", "r4")]
        r = run(T("EnforceablePenalty(case_17)", facts, rules, pairs, pairs))
        self.assertEqual(r.label, "Positive")
        self.assertTrue(r.check["ok"], r.check)
        roots = [n for n in r.proof["nodes"] if n.get("role") == "root"]
        self.assertEqual({n["top"].split("[")[0] for n in roots}, {"r1", "r3", "r5"})
        blocked = [e for e in r.proof["edges"] if e["type"] == "attack_blocked"]
        self.assertEqual(len(blocked), 2)   # r2 and r4 considered, overridden by r3 and r5

    def test_grounding_scopes_conflicts_per_case(self):
        rules = [R("r1", ["ValidContract(x)", "Delay(x)"], "EnforceablePenalty(x)"),
                 R("r2", ["ForceMajeure(x)"], "¬EnforceablePenalty(x)")]
        facts = ["ValidContract(c1)", "Delay(c1)", "ValidContract(c2)", "Delay(c2)", "ForceMajeure(c2)"]
        base = T("EnforceablePenalty(c1)", facts, rules, [("r2", "r1")], [("r2", "r1")])
        self.assertEqual(run(base).label, "Positive")
        self.assertEqual(run({**base, "query": "EnforceablePenalty(c2)"}).label, "Negative")
        self.assertTrue(run({**base, "query": "EnforceablePenalty(c2)"}).check["ok"])

    def test_strict_rule_not_rebutted_by_defeasible(self):
        t = T("Q(c)", ["A(c)", "B(c)"], [R("rs", ["A(c)"], "Q(c)", "strict"), R("rd", ["B(c)"], "¬Q(c)")],
              [("rs", "rd")])
        r = run(t)
        self.assertEqual(r.label, "Positive")
        self.assertTrue(r.check["ok"], r.check)

    def test_strict_on_defeasible_base_is_attackable(self):
        t = T("Q(c)", ["A(c)", "B(c)"],
              [R("r1", ["A(c)"], "M(c)"), R("r2", ["M(c)"], "Q(c)", "strict"), R("r3", ["B(c)"], "¬M(c)")],
              [("r1", "r3")])
        self.assertEqual(run(t).label, "Undetermined")

    def test_undercut_is_one_directional(self):
        t = T("Q(c)", ["A(c)", "B(c)"],
              [R("r1", ["A(c)"], "Q(c)"), R("d", ["B(c)"], "block(r1)", "defeater", target="r1")])
        r = run(t)
        self.assertEqual(r.label, "Undetermined")
        self.assertEqual(r.reason, "unresolved conflict: neither side is defended")

    def test_defeater_reinstates(self):
        t = T("Q(c)", ["A(c)", "B(c)", "C(c)"],
              [R("r1", ["A(c)"], "Q(c)"), R("r2", ["B(c)"], "¬Q(c)"),
               R("d", ["C(c)"], "block(r2)", "defeater", target="r2")],
              [("r1", "r2")])
        r = run(t)
        self.assertEqual(r.label, "Positive")
        self.assertTrue(r.check["ok"], r.check)

    def test_priority_on_defeater_target_blocks_undercut(self):
        t = T("Q(c)", ["A(c)", "B(c)"],
              [R("r1", ["A(c)"], "Q(c)"), R("d", ["B(c)"], "block(r1)", "defeater", target="r1")],
              priorities=[("r1", "d")])
        self.assertEqual(run(t).label, "Positive")

    def test_inconsistent_extension_is_flagged(self):
        t = T("Q(c)", ["A(c)", "B(c)"], [R("r1", ["A(c)"], "Q(c)"), R("r2", ["B(c)"], "¬Q(c)")])
        r = run(t)
        self.assertEqual(r.label, "Undetermined")
        self.assertTrue(r.review_flag)
        self.assertIn("INCONSISTENT_EXTENSION", r.review_reasons[0])

    def test_absence_policy(self):
        t = T("Q(c)", ["A(c)"], [R("r1", ["B(c)"], "Q(c)")])
        self.assertEqual(run(t).label, "Undetermined")
        r = run(t, config=Config(absence_policy="negative"))
        self.assertEqual((r.label, r.proof["kind"]), ("Negative", "absence"))
        self.assertTrue(r.check["ok"])
        self.assertEqual(r.proof["nodes"][0]["unmet_antecedents"], ["B(c)"])


class Deontic(unittest.TestCase):
    rules = [R("r1", ["Contract(c)"], "Obl(Deliver(c))"),
             R("r2", ["Strike(c)"], "Forb(Deliver(c))"),
             R("r3", ["Emergency(c)"], "Perm(Deliver(c))")]
    pairs = [("r2", "r1"), ("r3", "r2")]

    def test_forbidden_query_normalised(self):
        r = run(T("Forb(Deliver(c))", ["Contract(c)", "Strike(c)"], self.rules, self.pairs, self.pairs))
        self.assertEqual((r.query, r.label), ("Obl(¬Deliver(c))", "Positive"))
        self.assertTrue(r.check["ok"], r.check)

    def test_duality_switch(self):
        t = T("Obl(Deliver(c))", ["Contract(c)", "Strike(c)"], self.rules, self.pairs, self.pairs)
        self.assertEqual(run(t).label, "Undetermined")   # Eq. 1: ¬Obl(Deliver) never derived
        r = run(t, config=Config(deontic_duality=True))
        self.assertEqual(r.label, "Negative")
        self.assertTrue(r.check["ok"], r.check)

    def test_permission_as_exception_reinstates_obligation(self):
        r = run(T("Obl(Deliver(c))", ["Contract(c)", "Strike(c)", "Emergency(c)"], self.rules,
                  self.pairs, self.pairs))
        self.assertEqual(r.label, "Positive")
        self.assertTrue(r.check["ok"], r.check)

    def test_obligation_vs_permission_is_not_a_conflict(self):
        r = run(T("Obl(Deliver(c))", ["Contract(c)", "Emergency(c)"], self.rules, [("r3", "r1")]))
        self.assertIn("CONFLICT_NOT_OPPOSED", codes(r))


class Validation(unittest.TestCase):
    def good(self):
        return T("Q(c)", ["A(c)", "B(c)"], [R("r1", ["A(c)"], "Q(c)"), R("r2", ["B(c)"], "¬Q(c)")],
                 [("r2", "r1")], [("r2", "r1")])

    def test_normalisation_fixes_serialisation(self):
        t = self.good()
        t["rules"][1]["type"] = "Defeasible"
        t["rules"][1]["consequent"] = "~Q(c)"
        t["priorities"][0]["stronger"] = "R2"
        r = run(t)
        self.assertEqual(r.label, "Negative")
        self.assertEqual({e["op"] for e in r.normalization_log},
                         {"lowercase_type", "canonical_literal", "fix_rule_reference"})

    def assertCode(self, t, code, label=None, **kw):
        r = run(t, **kw)
        self.assertIn(code, codes(r), r.diagnostics)
        if label:
            self.assertEqual(r.label, label)
        return r

    def test_schema_and_symbol_errors(self):
        t = self.good(); t["rules"].append(R("r1", ["A(c)"], "Q(c)"))
        self.assertCode(t, "DUPLICATE_ID")
        t = self.good(); t["rules"][0]["type"] = "presumption"
        self.assertCode(t, "SCHEMA_ENUM")
        t = self.good(); t["rules"][0]["consequent"] = "Q(c"
        self.assertCode(t, "LITERAL_PARSE")
        t = self.good(); t["rules"][0]["consequent"] = "block(r2)"
        self.assertCode(t, "BLOCKER_MISUSE")
        t = self.good(); t["rules"].append(R("d", ["A(c)"], "block(r9)", "defeater", target="r9"))
        self.assertCode(t, "DEFEATER_TARGET_MISSING")
        t = self.good(); t["rules"][0] = R("r1", ["A(c)"], "Q(y)")
        self.assertCode(t, "UNGROUNDED_VARIABLE")
        t = self.good(); t["rules"][0]["antecedents"] = ["A(c, d)"]
        self.assertCode(t, "ARITY_MISMATCH")
        t = self.good(); t["facts"]["¬A(c)"] = True
        self.assertCode(t, "FACT_INCONSISTENT")

    def test_graph_errors(self):
        t = self.good(); t["conflicts"].append({"left": "r1", "right": "r9", "source": "S"})
        self.assertCode(t, "CONFLICT_ENDPOINT_MISSING")
        t = self.good(); t["rules"].append(R("r3", ["A(c)"], "P(c)")); t["conflicts"].append({"left": "r3", "right": "r1", "source": "S"})
        self.assertCode(t, "CONFLICT_NOT_OPPOSED")
        t = self.good(); t["rules"].append(R("r3", ["A(c)"], "Q(c)")); t["priorities"].append({"stronger": "r3", "weaker": "r2", "source": "S"})
        self.assertCode(t, "PRIORITY_OUTSIDE_CONFLICT")
        t = self.good(); t["priorities"][0]["source"] = None
        self.assertCode(t, "PRIORITY_UNSUPPORTED", "Undetermined")
        t = self.good(); t["rules"] = [R("r1", ["A(c)"], "Q(c)", "strict"), R("r2", ["B(c)"], "¬Q(c)", "strict")]
        self.assertCode(t, "STRICT_CONFLICT")
        t = T("Q(c)", ["A(c)"], [R("r1", ["A(c)", "M(c)"], "N(c)"), R("r2", ["N(c)"], "M(c)"), R("r3", ["A(c)"], "Q(c)")])
        self.assertCode(t, "DEPENDENCY_CYCLE", "Undetermined")

    def test_on_invalid_policies(self):
        t = self.good(); t["priorities"][0]["source"] = None
        r = run(t)
        self.assertEqual((r.label, r.review_flag), ("Undetermined", True))
        r = run(t, config=Config(on_invalid="drop"))
        self.assertEqual(r.label, "Undetermined")  # priority dropped -> mutual defeat
        t = self.good(); t["rules"].append(R("r9", ["A(c)"], "Bad("))
        self.assertEqual(run(t, config=Config(on_invalid="drop")).label, "Negative")

    def test_provenance(self):
        units = {"S": {"text": "Clause text of some length."}}
        t = self.good(); t["source_units"] = units
        r = run(t)
        self.assertTrue(r.check["ok"])
        t["rules"][1]["source"] = "S#0-999"
        r = self.assertCode(t, "SPAN_OUT_OF_RANGE", "Negative")   # non-blocking
        self.assertTrue(r.review_flag)
        self.assertFalse(r.check["SourceOK"])
        t["rules"][1]["source"] = "NOPE"
        self.assertCode(t, "SOURCE_UNRESOLVED")

    def test_repair_hook_bounded(self):
        t = self.good(); t["rules"][1]["consequent"] = "¬Q(c"
        calls = []

        def hook(req):
            calls.append(req)
            return {**req.object, "consequent": "¬Q(c)"}
        r = run(t, repair_hook=hook)
        self.assertEqual((r.label, len(calls)), ("Negative", 1))
        self.assertEqual(calls[0].diagnostics[0].code, "LITERAL_PARSE")

        def bad_hook(req):
            calls.append(req)
            return req.object
        calls.clear()
        r = run(t, repair_hook=bad_hook)
        self.assertEqual((r.label, len(calls), r.review_flag), ("Undetermined", 1, True))


class PriorityRepair(unittest.TestCase):
    def two_cycle(self, r1, r2):
        return T("Q(c)", ["A(c)", "B(c)", "C(c)"], [r1, r2], [("r1", "r2")], [("r1", "r2"), ("r2", "r1")])

    def test_specificity_resolves(self):
        r = run(self.two_cycle(R("r1", ["A(c)"], "Q(c)"), R("r2", ["A(c)", "B(c)"], "¬Q(c)")))
        self.assertEqual(r.label, "Negative")
        self.assertEqual({e["action"] for e in r.priority_repair_log}, {"kept", "reversed"})
        self.assertTrue(r.check["ok"], r.check)
        self.assertTrue(any(e["type"] == "priority" and "repair_log" in e for e in r.proof["edges"]))

    def test_authority_before_specificity(self):
        r = run(self.two_cycle(R("r1", ["A(c)"], "Q(c)", meta={"authority": 3}),
                               R("r2", ["A(c)", "B(c)"], "¬Q(c)", meta={"authority": 1})))
        self.assertEqual(r.label, "Positive")

    def test_incomparable_scope_unresolved(self):
        r = run(self.two_cycle(R("r1", ["A(c)"], "Q(c)"), R("r2", ["B(c)"], "¬Q(c)")))
        self.assertEqual(r.label, "Undetermined")
        self.assertTrue(any("unresolved priority cycle" in x for x in r.review_reasons))

    def test_temporal_precedence_requires_authorisation(self):
        t = self.two_cycle(R("r1", ["A(c)"], "Q(c)", meta={"effective_date": "2020-01-01"}),
                           R("r2", ["A(c)"], "¬Q(c)", meta={"effective_date": "2024-01-01"}))
        self.assertEqual(run(t).label, "Undetermined")
        r = run({**t, "settings": {"temporal_precedence_authorized": True}})
        self.assertEqual(r.label, "Negative")

    def test_four_cycle(self):
        rules = [R("r1", ["A(c)"], "Q(c)"), R("r2", ["A(c)", "B(c)"], "¬Q(c)"),
                 R("r3", ["A(c)", "B(c)", "C(c)"], "Q(c)"), R("r4", ["A(c)", "B(c)", "C(c)", "D(c)"], "¬Q(c)")]
        pairs = [("r1", "r2"), ("r2", "r3"), ("r3", "r4"), ("r4", "r1")]
        r = run(T("Q(c)", ["A(c)", "B(c)", "C(c)"], rules, pairs, pairs))
        self.assertEqual(r.label, "Positive")   # r3 most specific active rule
        self.assertTrue(r.check["ok"], r.check)


class CheckerTamper(unittest.TestCase):
    def setUp(self):
        self.t = fixture("nested_exception")
        self.res = run(self.t)
        self.theory = theory_of(self.t)
        self.assertTrue(check(self.res.proof, self.theory)["ok"])

    def tampered(self, fn):
        p = copy.deepcopy(self.res.proof)
        fn(p)
        return check(p, self.theory)

    def test_hidden_attacker(self):
        def f(p):
            root = next(n["id"] for n in p["nodes"] if n.get("role") == "root" and n["top"] == "r1")
            p["attackers_listed"][root] = []
        self.assertFalse(self.tampered(f)["CompleteOK"])

    def test_forged_rank(self):
        def f(p):
            for n in p["nodes"]:
                if n.get("role") == "root" and n["top"] == "r1":
                    n["rank"] = 1
        self.assertFalse(self.tampered(f)["RootOK"])

    def test_wrong_label(self):
        self.assertFalse(self.tampered(lambda p: p.update(accepted_conclusion="Negative"))["RootOK"])

    def test_altered_rule(self):
        def f(p):
            for n in p["nodes"]:
                if n["type"] == "rule" and n["label"] == "r2":
                    n["antecedents"] = []
        self.assertFalse(self.tampered(f)["SourceOK"])

    def test_fake_defeat_edge(self):
        def f(p):
            ids = [n["id"] for n in p["nodes"] if n.get("role") == "root"]
            p["edges"].append({"source": ids[0], "target": ids[-1], "type": "defeat", "via": ids[-1], "kind": "rebut"})
        self.assertFalse(self.tampered(f)["EdgeOK"])

    def test_reversed_priority(self):
        def f(p):
            for e in p["edges"]:
                if e["type"] == "priority" and e["source"] == "R:r3":
                    e["source"], e["target"] = "R:r2", "R:r3"
        self.assertFalse(self.tampered(f)["EdgeOK"])

    def test_unconstructible_argument(self):
        def f(p):
            for n in p["nodes"]:
                if n.get("role") == "root":
                    n["signature"] = "r1(fact[Nonsense(case)])"
                    break
        self.assertFalse(self.tampered(f)["ok"])


if __name__ == "__main__":
    unittest.main()
