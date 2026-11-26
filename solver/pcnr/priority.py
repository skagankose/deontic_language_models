"""Meta-priority repair for cyclic priorities (paper §3.7, Table 3, Eq. 8).

    κL(r) = <authority, specificity, effectiveDate, directness>

For each strongly connected component of the priority graph, each edge is compared with κL.
Components are compared in order; for each one the outcome is
    ">" / "<"        strict, source-verifiable comparison  -> decides the edge
    "="              tie / no information on either side   -> go to next component
    "unestablished"  information missing on one side, scopes incomparable, or temporal
                     precedence not authorised              -> stop: edge unresolved
An edge is re-oriented only on a strict comparison. If any edge of a component is unresolved
(or the re-oriented component is still cyclic) the whole component is unresolved: its priority
edges are removed and the affected query is escalated (Undetermined + human-review flag).

Conventions (our choices where the paper is silent):
  authority     rule.meta["authority"], a number; larger = higher in the source hierarchy
  specificity   strict inclusion of ground antecedent sets (computed, not taken from meta), or,
                if theory.settings["specificity_from_meta"], the numeric rule.meta["specificity"]
                that the authors' released records carry. Under the paper's exception encoding
                (an exception's antecedents are only its own conditions) inclusion never holds
                between an exception and its parent, so only the meta variant can resolve such cycles.
  effectiveDate rule.meta["effective_date"] (ISO date); used only if
                theory.settings["temporal_precedence_authorized"] is true (lex posterior)
  directness    rule.meta["directness"] in {primary > cross_reference > inferred}
Extraction confidence and identifiers are never used.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .theory import Rule, Theory

DIRECTNESS = {"primary": 2, "cross_reference": 1, "inferred": 0}


@dataclass
class PriorityRepair:
    effective: set[tuple[str, str]]
    log: list[dict] = field(default_factory=list)
    unresolved: list[set[str]] = field(default_factory=list)


def _cmp_values(a, b):
    if a is None and b is None:
        return "="
    if a is None or b is None:
        return "unestablished"
    return ">" if a > b else "<" if a < b else "="


def compare(a: Rule, b: Rule, theory: Theory) -> tuple[str | None, list[dict]]:
    """Return (winner, trace) where winner is a.id, b.id or None (unresolved)."""
    ma, mb = a.meta_dict, b.meta_dict
    trace = []

    def step(name, result, va, vb):
        trace.append({"component": name, "result": result, "values": [va, vb]})
        return result

    comps = []
    comps.append(("authority", _cmp_values(ma.get("authority"), mb.get("authority")),
                  ma.get("authority"), mb.get("authority")))
    if theory.settings.get("specificity_from_meta") and "specificity" in ma and "specificity" in mb:
        # numeric specificity as carried by the authors' released records (meta.specificity 1/2/3)
        comps.append(("specificity", _cmp_values(ma["specificity"], mb["specificity"]),
                      ma["specificity"], mb["specificity"]))
    else:
        sa, sb = set(a.antecedents), set(b.antecedents)
        spec = ">" if sb < sa else "<" if sa < sb else "=" if sa == sb else "unestablished"
        comps.append(("specificity", spec, sorted(sa), sorted(sb)))
    da, db = ma.get("effective_date"), mb.get("effective_date")
    if theory.settings.get("temporal_precedence_authorized"):
        date = _cmp_values(da, db)
    else:
        date = "=" if (da is None and db is None) or da == db else "unestablished"
    comps.append(("effectiveDate", date, da, db))
    ra, rb = DIRECTNESS.get(ma.get("directness")), DIRECTNESS.get(mb.get("directness"))
    comps.append(("directness", _cmp_values(ra, rb), ma.get("directness"), mb.get("directness")))

    for name, result, va, vb in comps:
        step(name, result, va, vb)
        if result == ">":
            return a.id, trace
        if result == "<":
            return b.id, trace
        if result == "unestablished":
            return None, trace
    return None, trace  # all components tie


def _sccs(edges: set[tuple[str, str]]) -> list[set[str]]:
    graph = defaultdict(set)
    nodes = set()
    for a, b in edges:
        graph[a].add(b)
        nodes |= {a, b}
    index, low, stack, on, out, counter = {}, {}, [], set(), [], [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(graph[v]):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = set()
            while True:
                w = stack.pop()
                on.discard(w)
                comp.add(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(comp)
    for v in sorted(nodes):
        if v not in index:
            strong(v)
    return out


def repair_cycles(theory: Theory) -> PriorityRepair:
    edges = set(theory.priorities)
    result = PriorityRepair(effective=set(edges))
    for comp in _sccs(edges):
        inner = sorted(e for e in edges if e[0] in comp and e[1] in comp)
        decisions, resolved = [], True
        for s, w in inner:
            winner, trace = compare(theory.rules[s], theory.rules[w], theory)
            if winner is None:
                resolved = False
                decisions.append(((s, w), None, trace))
            else:
                decisions.append(((s, w), (winner, w if winner == s else s), trace))
        new_edges = {d[1] for d in decisions if d[1] is not None}
        if resolved and _sccs(new_edges):
            resolved = False
        result.effective -= set(inner)
        for (s, w), oriented, trace in decisions:
            if not resolved:
                action = "removed"
            elif oriented == (s, w):
                action = "kept"
            else:
                action = "reversed"
            result.log.append({"component": sorted(comp), "edge": [s, w],
                               "oriented": list(oriented) if oriented and resolved else None,
                               "action": action, "comparison": trace})
        if resolved:
            result.effective |= new_edges
        else:
            result.unresolved.append(set(comp))
    return result


def apply_log(explicit: set[tuple[str, str]], log: list[dict]) -> set[tuple[str, str]]:
    """Recompute the effective priority relation from explicit priorities and a repair log
    (used by the independent proof checker)."""
    eff = set(explicit)
    for entry in log:
        eff.discard(tuple(entry["edge"]))
    for entry in log:
        if entry["action"] in ("kept", "reversed"):
            eff.add(tuple(entry["oriented"]))
    return eff
