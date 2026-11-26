"""Four-stage validator (paper §3.4) emitting typed diagnostics
<code, object-id, source-span, severity, repairable>.

  Stage 1  schema        required fields, enumerated rule types, identifiers, value ranges
  Stage 2  symbol table  literal syntax, predicate arity, variable grounding (rule safety),
                         defeater targets, duplicate ids, grounded dependency cycles
  Stage 3  provenance    every cited source unit exists; spans lie within the unit text
  Stage 4  graph         conflict/priority endpoints exist; conflicts are well-founded
                         (opposed effects, or defeater->target); priorities lie inside a
                         declared conflict and carry a source; priority cycles detected

`blocking` diagnostics quarantine the object (it never enters the reasoner).
Provenance failures are non-blocking: the object is reasoned with, the case is flagged for
review, and the proof checker's SourceOK will fail (as in the paper's citation stress test).
Priority cycles are non-blocking here: they are routed to `priority.repair_cycles`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Callable

from . import literals as L
from .theory import RULE_TYPES, ground, resolve_pointer


@dataclass(frozen=True)
class Diagnostic:
    code: str
    object_kind: str          # fact | rule | conflict | priority | query | theory
    object_id: str
    source_span: str | None
    severity: str             # error | warning | info
    repairable: bool
    blocking: bool
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _oid(kind: str, obj: dict) -> str:
    if kind == "fact":
        return str(obj.get("atom"))
    if kind == "rule":
        return str(obj.get("id"))
    if kind == "conflict":
        return f"{obj.get('left')}⋈{obj.get('right')}"
    if kind == "priority":
        return f"{obj.get('stronger')}≻{obj.get('weaker')}"
    return kind


@dataclass
class ValidationReport:
    diagnostics: list[Diagnostic]
    clean_raw: dict           # raw with blocking objects removed and implied conflicts added

    @property
    def blocking(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.blocking]

    @property
    def needs_review(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)


def validate(raw: dict, *, require_sources: bool = True, require_fact_sources: bool = False,
             vocabulary: set[str] | None = None) -> ValidationReport:
    diags: list[Diagnostic] = []
    bad: set[tuple[str, str]] = set()    # (kind, object_id) to quarantine

    def emit(code, kind, obj, severity="error", repairable=True, blocking=True, message=""):
        oid = _oid(kind, obj) if isinstance(obj, dict) else str(obj)
        src = obj.get("source") if isinstance(obj, dict) else None
        diags.append(Diagnostic(code, kind, oid, src, severity, repairable, blocking, message))
        if blocking:
            bad.add((kind, oid))

    # ---------------- stage 1: schema ----------------
    q = raw.get("query")
    try:
        if not isinstance(q, str) or L.is_blocker(q):
            raise L.LiteralError("query must be a legal literal")
        L.parse(q)
    except L.LiteralError as e:
        emit("QUERY_INVALID", "query", {"source": None}, repairable=False, message=str(e))
        return ValidationReport(diags, {**raw, "facts": [], "rules": [], "conflicts": [], "priorities": []})

    for f in raw["facts"]:
        if not isinstance(f.get("atom"), str):
            emit("SCHEMA_FIELD", "fact", f, message="fact.atom must be a string")
        c = f.get("confidence")
        if c is not None and not (isinstance(c, (int, float)) and 0 <= c <= 1):
            emit("SCHEMA_RANGE", "fact", f, blocking=False, severity="warning", message=f"confidence {c!r} not in [0,1]")
    ids = Counter(r.get("id") for r in raw["rules"])
    for r in raw["rules"]:
        if not isinstance(r.get("id"), str) or not r["id"]:
            emit("SCHEMA_FIELD", "rule", r, message="rule.id must be a non-empty string")
        elif ids[r["id"]] > 1:
            emit("DUPLICATE_ID", "rule", r, message=f"rule id {r['id']!r} used {ids[r['id']]} times")
        if r.get("type") not in RULE_TYPES:
            emit("SCHEMA_ENUM", "rule", r, message=f"type {r.get('type')!r} not in {RULE_TYPES}")
        if not isinstance(r.get("antecedents", []), list):
            emit("SCHEMA_FIELD", "rule", r, message="antecedents must be a list")
        if not isinstance(r.get("consequent"), str):
            emit("SCHEMA_FIELD", "rule", r, message="consequent must be a string")
        c = r.get("confidence")
        if c is not None and not (isinstance(c, (int, float)) and 0 <= c <= 1):
            emit("SCHEMA_RANGE", "rule", r, blocking=False, severity="warning", message=f"confidence {c!r} not in [0,1]")
        if r.get("type") == "defeater" and not r.get("target"):
            emit("DEFEATER_NO_TARGET", "rule", r, message="defeater requires a target rule")
    for kind, coll_name, keys in (("conflict", "conflicts", ("left", "right")),
                                  ("priority", "priorities", ("stronger", "weaker"))):
        for o in raw[coll_name]:
            if not all(isinstance(o.get(k), str) for k in keys):
                emit("SCHEMA_FIELD", kind, o, message=f"{kind} needs string fields {keys}")

    # ---------------- stage 2: symbol table ----------------
    arities: dict[str, Counter] = defaultdict(Counter)
    parsed_ok: dict[tuple[str, str], list[str]] = {}

    def check_literal(text, kind, obj, role, allow_block=False):
        try:
            node = L.parse(text)
        except L.LiteralError as e:
            emit("LITERAL_PARSE", kind, obj, message=f"{role}: {e}")
            return None
        if isinstance(node, L.Block) and not allow_block:
            emit("BLOCKER_MISUSE", kind, obj, message=f"{role}: block() tokens may only be defeater outputs")
            return None
        for pred, n in L.signatures(text):
            arities[pred][n] += 1
        return text

    for f in raw["facts"]:
        if isinstance(f.get("atom"), str) and check_literal(f["atom"], "fact", f, "atom"):
            parsed_ok[("fact", f["atom"])] = [f["atom"]]
            if L.variables(f["atom"]):
                emit("UNGROUNDED_FACT", "fact", f, message="facts must be ground")
    fact_set = {f.get("atom") for f in raw["facts"]}
    for f in raw["facts"]:
        a = f.get("atom")
        if isinstance(a, str) and ("fact", a) in parsed_ok and L.complement(a) in fact_set:
            emit("FACT_INCONSISTENT", "fact", f, repairable=True,
                 message=f"both {a} and its complement are asserted as facts")

    rule_ids = {r.get("id") for r in raw["rules"]}
    by_id = {r.get("id"): r for r in raw["rules"]}
    for r in raw["rules"]:
        if r.get("type") not in RULE_TYPES:
            continue
        ok = all(check_literal(a, "rule", r, "antecedent") for a in r.get("antecedents", []) if isinstance(a, str))
        is_def = r["type"] == "defeater"
        if isinstance(r.get("consequent"), str):
            ok = check_literal(r["consequent"], "rule", r, "consequent", allow_block=is_def) and ok
            if is_def and ok and not L.is_blocker(r["consequent"]):
                emit("DEFEATER_CONSEQUENT", "rule", r, message="defeater consequent must be block(target)")
            if is_def and ok and L.is_blocker(r["consequent"]) and r["consequent"] != f"block({r.get('target')})":
                emit("DEFEATER_CONSEQUENT", "rule", r, message="block() argument differs from target")
        if is_def:
            if r.get("target") not in rule_ids:
                emit("DEFEATER_TARGET_MISSING", "rule", r, message=f"target {r.get('target')!r} is not a rule")
            elif r.get("target") == r.get("id"):
                emit("DEFEATER_SELF_TARGET", "rule", r)
        if ok and not is_def:
            declared = frozenset(r.get("variables", []))
            body_vars = set().union(*(L.variables(a, declared) for a in r.get("antecedents", []))) if r.get("antecedents") else set()
            unsafe = L.variables(r["consequent"], declared) - body_vars
            if unsafe:
                emit("UNGROUNDED_VARIABLE", "rule", r, message=f"head variables {sorted(unsafe)} not bound by the body")
        if vocabulary:
            for lit in list(r.get("antecedents", [])) + ([r.get("consequent")] if not is_def else []):
                try:
                    unknown = {p for p, _ in L.signatures(lit)} - vocabulary
                except (L.LiteralError, TypeError):
                    continue
                if unknown:
                    emit("UNKNOWN_PREDICATE", "rule", r, severity="warning", blocking=False,
                         message=f"predicates outside vocabulary: {sorted(unknown)}")
    for pred, counts in arities.items():
        if len(counts) > 1:
            majority = counts.most_common(1)[0][0]
            for kind, coll, fields in (("fact", raw["facts"], ("atom",)), ("rule", raw["rules"], ("antecedents", "consequent"))):
                for o in coll:
                    lits = [o.get(fl) for fl in fields]
                    lits = [x for v in lits for x in (v if isinstance(v, list) else [v]) if isinstance(v, (str, list))]
                    try:
                        if any(p == pred and n != majority for lit in lits if isinstance(lit, str) for p, n in L.signatures(lit)):
                            emit("ARITY_MISMATCH", kind, o, message=f"{pred} used with arities {dict(counts)}")
                    except L.LiteralError:
                        pass

    # ---------------- stage 4a: conflicts & priorities (endpoints / well-foundedness) -----
    conflicts_out, implied = [], []
    declared_pairs = set()
    for c in raw["conflicts"]:
        a, b = by_id.get(c.get("left")), by_id.get(c.get("right"))
        if a is None or b is None:
            emit("CONFLICT_ENDPOINT_MISSING", "conflict", c, message="conflict endpoint is not a rule")
            continue
        if a is b:
            emit("CONFLICT_SELF", "conflict", c)
            continue
        if ("rule", a["id"]) in bad or ("rule", b["id"]) in bad:
            emit("DEPENDS_ON_QUARANTINED", "conflict", c, severity="info", repairable=False, blocking=False,
                 message="an endpoint rule was quarantined; re-checked after repair")
            continue
        ta, tb = a["type"], b["type"]
        if "defeater" in (ta, tb):
            if not ((ta == "defeater" and a.get("target") == b["id"]) or (tb == "defeater" and b.get("target") == a["id"])):
                emit("CONFLICT_DEFEATER_UNTARGETED", "conflict", c,
                     message="a defeater may only be in conflict with the rule it targets")
                continue
        else:
            try:
                if not L.opposed(a["consequent"], b["consequent"]):
                    emit("CONFLICT_NOT_OPPOSED", "conflict", c,
                         message=f"{a['consequent']} and {b['consequent']} are not normatively opposed")
                    continue
            except L.LiteralError:
                continue
            if ta == "strict" and tb == "strict":
                emit("STRICT_CONFLICT", "conflict", c, repairable=False,
                     message="two strict rules with opposed heads: theory is inconsistent")
                continue
        declared_pairs.add(frozenset((a["id"], b["id"])))
        conflicts_out.append(c)
    for r in raw["rules"]:
        if r.get("type") == "defeater" and r.get("target") in rule_ids and ("rule", r["id"]) not in bad:
            key = frozenset((r["id"], r["target"]))
            if key not in declared_pairs:
                declared_pairs.add(key)
                implied.append({"left": r["id"], "right": r["target"], "source": r.get("source"),
                                "basis": "defeater target", "implied": True})

    quarantined_rules = {oid for kind, oid in bad if kind == "rule"}
    priorities_out = []
    for p in raw["priorities"]:
        s, w = p.get("stronger"), p.get("weaker")
        if s not in rule_ids or w not in rule_ids:
            emit("PRIORITY_ENDPOINT_MISSING", "priority", p)
            continue
        if s in quarantined_rules or w in quarantined_rules:
            emit("DEPENDS_ON_QUARANTINED", "priority", p, severity="info", repairable=False, blocking=False,
                 message="an endpoint rule was quarantined; re-checked after repair")
            continue
        if s == w:
            emit("PRIORITY_SELF", "priority", p)
            continue
        if frozenset((s, w)) not in declared_pairs:
            emit("PRIORITY_OUTSIDE_CONFLICT", "priority", p,
                 message="priorities apply only within a declared conflict pair")
            continue
        if not p.get("source"):
            emit("PRIORITY_UNSUPPORTED", "priority", p, message="priority has no source-linked basis")
            continue
        priorities_out.append(p)
    if _has_cycle({(p["stronger"], p["weaker"]) for p in priorities_out}):
        diags.append(Diagnostic("PRIORITY_CYCLE", "theory", "priorities", None, "warning", True, False,
                                "priority graph is cyclic; routed to meta-priority repair"))

    # ---------------- stage 3: provenance (non-blocking) ----------------
    units = raw.get("source_units")
    if units is None:
        diags.append(Diagnostic("SOURCES_UNAVAILABLE", "theory", "source_units", None, "info", False, False,
                                "no source units supplied; provenance not checked"))
    else:
        for kind, coll in (("fact", raw["facts"]), ("rule", raw["rules"]),
                           ("conflict", conflicts_out), ("priority", priorities_out)):
            for o in coll:
                if kind == "fact" and not o.get("source") and not require_fact_sources:
                    continue
                if not o.get("source") and not require_sources:
                    continue
                ok, why = resolve_pointer(o.get("source"), units)
                if not ok:
                    code = "SOURCE_MISSING" if not o.get("source") else (
                        "SPAN_OUT_OF_RANGE" if "span" in why else "SOURCE_UNRESOLVED")
                    emit(code, kind, o, blocking=False, message=why)

    # ---------------- assemble clean raw, then grounded checks ----------------
    clean = {**raw,
             "facts": [f for f in raw["facts"] if ("fact", _oid("fact", f)) not in bad],
             "rules": [r for r in raw["rules"] if ("rule", _oid("rule", r)) not in bad]}
    keep = {r["id"] for r in clean["rules"]}
    # a defeater whose target was quarantined cannot enter either
    for r in list(clean["rules"]):
        if r["type"] == "defeater" and r["target"] not in keep:
            emit("DEFEATER_TARGET_QUARANTINED", "rule", r, message="target rule was quarantined")
    clean["rules"] = [r for r in clean["rules"] if ("rule", r["id"]) not in bad]
    keep = {r["id"] for r in clean["rules"]}
    clean["conflicts"] = [c for c in conflicts_out + implied if c["left"] in keep and c["right"] in keep]
    clean["priorities"] = [p for p in priorities_out if p["stronger"] in keep and p["weaker"] in keep]

    # grounded dependency cycles (paper Def. 4: "validation rejects grounded dependency cycles")
    try:
        theory = ground(clean)
    except ValueError as e:
        emit("GROUNDING_LIMIT", "theory", {"source": None}, repairable=False, message=str(e))
        return ValidationReport(diags, {**clean, "rules": [], "conflicts": [], "priorities": []})
    cyc = _dependency_cycle_rules(theory)
    if cyc:
        for r in clean["rules"]:
            if r["id"] in cyc:
                emit("DEPENDENCY_CYCLE", "rule", r, message="rule participates in a grounded dependency cycle")
        clean["rules"] = [r for r in clean["rules"] if r["id"] not in cyc]
        keep = {r["id"] for r in clean["rules"]}
        clean["conflicts"] = [c for c in clean["conflicts"] if c["left"] in keep and c["right"] in keep]
        clean["priorities"] = [p for p in clean["priorities"] if p["stronger"] in keep and p["weaker"] in keep]
    return ValidationReport(diags, clean)


def _has_cycle(edges: set[tuple[str, str]]) -> bool:
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
    state: dict[str, int] = {}

    def visit(n):
        state[n] = 1
        for m in graph[n]:
            if state.get(m) == 1 or (m not in state and visit(m)):
                return True
        state[n] = 2
        return False
    return any(n not in state and visit(n) for n in list(graph))


def _dependency_cycle_rules(theory) -> set[str]:
    """Schema ids of rules lying on a cycle of the ground dependency graph r' -> r
    (r' concludes an antecedent of r)."""
    producers = defaultdict(set)
    for r in theory.rules.values():
        if r.type != "defeater":
            producers[r.consequent].add(r.id)
    graph = {r.id: {p for a in r.antecedents for p in producers.get(a, ())} for r in theory.rules.values()}
    index, low, stack, on, out, counter = {}, {}, [], set(), set(), [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph[v]:
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in graph[v]:
                out.update(theory.rules[x].schema_id for x in comp)
    for v in graph:
        if v not in index:
            strong(v)
    return out


# ---- validation-triggered repair (paper §3.4: one bounded pass) -------------------------

@dataclass
class RepairRequest:
    kind: str                  # fact | rule | conflict | priority
    object: dict
    diagnostics: list[Diagnostic]
    query: str
    neighborhood: dict[str, str]  # cited source-unit texts


RepairHook = Callable[[RepairRequest], "dict | None | str"]
"""Return a replacement object, None to delete the object, or "keep" to leave it unchanged."""


def repair_once(raw: dict, report: ValidationReport, hook: RepairHook, **validate_kwargs):
    """Send each failed repairable object (with its diagnostics and source neighbourhood) to
    `hook` once, splice replacements into raw, and revalidate. Returns (raw', report', log)."""
    by_obj: dict[tuple[str, str], list[Diagnostic]] = defaultdict(list)
    for d in report.diagnostics:
        if d.severity == "error" and d.repairable and d.object_kind in ("fact", "rule", "conflict", "priority"):
            by_obj[(d.object_kind, d.object_id)].append(d)
    if not by_obj:
        return raw, report, []
    units = raw.get("source_units") or {}
    log, new = [], {k: list(raw[k]) for k in ("facts", "rules", "conflicts", "priorities")}
    coll_name = {"fact": "facts", "rule": "rules", "conflict": "conflicts", "priority": "priorities"}
    for (kind, oid), ds in by_obj.items():
        coll = new[coll_name[kind]]
        idx = next((i for i, o in enumerate(coll) if _oid(kind, o) == oid), None)
        if idx is None:
            continue
        obj = coll[idx]
        cited = {str(obj.get("source", "")).partition("#")[0]} | {d.source_span.partition("#")[0] for d in ds if d.source_span}
        hood = {u: (units.get(u) or {}).get("text", "") for u in cited if u in units}
        out = hook(RepairRequest(kind, obj, ds, raw["query"], hood))
        if out == "keep":
            log.append({"object": oid, "kind": kind, "action": "kept"})
        elif out is None:
            coll.pop(idx)
            log.append({"object": oid, "kind": kind, "action": "deleted"})
        else:
            coll[idx] = out
            log.append({"object": oid, "kind": kind, "action": "replaced", "replacement": out})
    raw2 = {**raw, **new}
    return raw2, validate(raw2, **validate_kwargs), log
