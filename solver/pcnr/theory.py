"""Defeasible normative theory T = <F, R, ≻, ⋈, σ> (paper §3.3, Definition 2).

Three input shapes are accepted and coerced to one *raw* dict:
  * released anonymized records   (facts: {atom: bool}, target_literal, source_units: [..])
  * authors' formal fixtures      (facts: {atom: bool}, query = literal)
  * extractor output (schema)     (facts: [{atom, source, confidence}], source_pointers)

Raw dict shape (all later stages work on this):
  {case_id, query, facts: [{atom, source, confidence}], rules: [...], conflicts: [...],
   priorities: [...], source_units: {id: {text, doc_id, path, span}} | None, settings: {...}}

Source pointers are source-unit ids, optionally with a character span relative to the unit
text: "SU1" or "SU1#12-80".
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from typing import Any

from . import literals as L

RULE_TYPES = ("strict", "defeasible", "defeater")


# ---- raw coercion ------------------------------------------------------------------------

def coerce_raw(obj: dict, *, query: str | None = None, source_units: Any = None,
               case_id: str | None = None, settings: dict | None = None) -> dict:
    obj = copy.deepcopy(obj)
    q = query or obj.get("target_literal") or obj.get("query")
    facts_in = obj.get("facts", [])
    facts: list[dict] = []
    if isinstance(facts_in, dict):
        for atom, value in facts_in.items():
            if value is True:
                facts.append({"atom": atom, "source": None, "confidence": None})
    elif isinstance(facts_in, list):
        for f in facts_in:
            facts.append({"atom": f, "source": None, "confidence": None} if isinstance(f, str) else dict(f))
    units = source_units if source_units is not None else obj.get("source_units")
    return {
        "case_id": case_id or obj.get("case_id") or obj.get("id") or obj.get("name"),
        "query": q,
        "facts": facts,
        "rules": [dict(r) for r in obj.get("rules", [])],
        "conflicts": [dict(c) for c in obj.get("conflicts", [])],
        "priorities": [dict(p) for p in obj.get("priorities", [])],
        "source_units": _coerce_units(units),
        "settings": {**obj.get("settings", {}), **(settings or {})},
    }


def _coerce_units(units: Any) -> dict | None:
    if units is None:
        return None
    out: dict[str, dict] = {}
    if isinstance(units, dict):
        for uid, value in units.items():
            out[uid] = {"text": value} if isinstance(value, str) else dict(value)
    else:
        for u in units:
            uid = u.get("source_unit_id") or u.get("id")
            out[uid] = dict(u)
    return out


# ---- deterministic normalisation (paper: "Pure serialization defects are normalized deterministically")

def normalize(raw: dict) -> tuple[dict, list[dict]]:
    """Fix serialisation-level defects only. Returns (raw', log)."""
    raw = copy.deepcopy(raw)
    log: list[dict] = []

    def note(kind, obj_id, before, after):
        log.append({"op": kind, "object": obj_id, "before": before, "after": after})

    def canon(text, obj_id, field_name):
        if not isinstance(text, str):
            return text
        try:
            c = L.canonical(text)
        except L.LiteralError:
            return text.strip()
        if c != text:
            note("canonical_literal", obj_id, {field_name: text}, {field_name: c})
        return c

    if isinstance(raw.get("query"), str):
        try:
            raw["query"] = L.canonical(raw["query"])
        except L.LiteralError:
            pass
    for f in raw["facts"]:
        f["atom"] = canon(f.get("atom"), f.get("atom"), "atom")
        f["confidence"] = _to_float(f.get("confidence"))
    rule_ids = [r.get("id") for r in raw["rules"] if isinstance(r.get("id"), str)]
    for r in raw["rules"]:
        rid = r.get("id")
        if isinstance(r.get("type"), str) and r["type"].strip().lower() != r["type"]:
            note("lowercase_type", rid, r["type"], r["type"].strip().lower())
            r["type"] = r["type"].strip().lower()
        if isinstance(r.get("antecedents"), str):
            r["antecedents"] = [r["antecedents"]]
        r["antecedents"] = [canon(a, rid, "antecedent") for a in r.get("antecedents", []) or []]
        if r.get("type") == "defeater":
            if r.get("target") is not None:
                r["target"] = _fix_ref(r["target"], rule_ids, rid, log)
            if not r.get("consequent") and r.get("target"):
                note("defeater_consequent", rid, None, f"block({r['target']})")
                r["consequent"] = f"block({r['target']})"
        r["consequent"] = canon(r.get("consequent"), rid, "consequent")
        r["confidence"] = _to_float(r.get("confidence"))
    for c in raw["conflicts"]:
        for k in ("left", "right"):
            c[k] = _fix_ref(c.get(k), rule_ids, f"conflict:{c.get('left')}-{c.get('right')}", log)
    for p in raw["priorities"]:
        for k in ("stronger", "weaker"):
            p[k] = _fix_ref(p.get(k), rule_ids, f"priority:{p.get('stronger')}>{p.get('weaker')}", log)
    return raw, log


def _to_float(x):
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return x
    return x


def _fix_ref(ref, rule_ids, obj_id, log):
    """Repair rule references that differ only by case or a missing 'r' prefix."""
    if not isinstance(ref, (str, int)) or ref in rule_ids:
        return ref
    s = str(ref).strip()
    candidates = [rid for rid in rule_ids if rid.lower() == s.lower() or rid.lower() == f"r{s}".lower()]
    if len(candidates) == 1:
        log.append({"op": "fix_rule_reference", "object": obj_id, "before": ref, "after": candidates[0]})
        return candidates[0]
    return ref


# ---- typed theory ------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    id: str                     # instance id (== schema id for ground rules)
    schema_id: str
    antecedents: tuple[str, ...]
    consequent: str
    type: str
    target: str | None          # instance id of targeted rule (defeaters)
    source: str | None
    confidence: float | None
    meta: tuple                 # frozen items of the meta dict
    binding: tuple[tuple[str, str], ...] = ()

    @property
    def meta_dict(self) -> dict:
        return dict(self.meta)

    @property
    def out(self) -> str:
        return f"block({self.target})" if self.type == "defeater" else self.consequent


@dataclass(frozen=True)
class Fact:
    literal: str
    source: str | None
    confidence: float | None


@dataclass(frozen=True)
class Conflict:
    left: str
    right: str
    source: str | None
    basis: str | None = None
    implied: bool = False


@dataclass(frozen=True)
class Priority:
    stronger: str
    weaker: str
    source: str | None
    basis: str | None = None


@dataclass
class Theory:
    case_id: str | None
    query: str
    facts: dict[str, Fact]
    rules: dict[str, Rule]                 # ground instances
    conflicts: set[frozenset[str]]         # instance-level, symmetric
    conflict_info: dict[frozenset[str], Conflict]
    priorities: set[tuple[str, str]]       # instance-level explicit (validated) priorities
    priority_info: dict[tuple[str, str], Priority]
    source_units: dict | None
    settings: dict = field(default_factory=dict)


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def ground(raw: dict, max_instances: int = 100_000) -> Theory:
    """Build a ground Theory from a validated raw dict.

    Rule schemas with variables (x, y, z, x1.., ?name, or listed in rule["variables"]) are
    instantiated over the constants occurring in facts and the query. Conflicts, priorities and
    defeater targets declared between schemas apply to instance pairs whose shared variables
    are bound identically ("same issue and scope").
    """
    constants = sorted({t for f in raw["facts"] for t in L.terms(f["atom"])}
                       | ({t for t in L.terms(raw["query"])} if raw.get("query") else set()))
    instances: dict[str, list[Rule]] = {}
    total = 0
    for r in raw["rules"]:
        declared = frozenset(r.get("variables", []))
        lits = list(r.get("antecedents", [])) + ([r["consequent"]] if r.get("type") != "defeater" else [])
        vs = sorted(set().union(*(L.variables(x, declared) for x in lits)) if lits else set())
        vs = [v for v in vs if v not in constants]
        out = []
        for values in itertools.product(constants, repeat=len(vs)) if vs else [()]:
            binding = dict(zip(vs, values))
            iid = r["id"] if not vs else f"{r['id']}[{','.join(f'{k}={v}' for k, v in binding.items())}]"
            out.append(Rule(
                id=iid, schema_id=r["id"],
                antecedents=tuple(L.substitute(a, binding) for a in r.get("antecedents", [])),
                consequent=r["consequent"] if r["type"] == "defeater" else L.substitute(r["consequent"], binding),
                type=r["type"], target=r.get("target"), source=r.get("source"),
                confidence=r.get("confidence"), meta=_freeze(r.get("meta") or {}),
                binding=tuple(sorted(binding.items())),
            ))
            total += 1
            if total > max_instances:
                raise ValueError(f"grounding exceeds {max_instances} rule instances")
        instances[r["id"]] = out

    def compatible(a: Rule, b: Rule) -> bool:
        ba, bb = dict(a.binding), dict(b.binding)
        return all(ba[k] == bb[k] for k in ba.keys() & bb.keys())

    rules: dict[str, Rule] = {}
    for schema_id, insts in instances.items():
        for inst in insts:
            if inst.type == "defeater":
                targets = [t for t in instances.get(inst.target, []) if compatible(inst, t)]
                for t in targets:  # one defeater instance per compatible target instance
                    iid = inst.id if len(targets) == 1 else f"{inst.id}->{t.id}"
                    rules[iid] = Rule(**{**inst.__dict__, "id": iid, "target": t.id,
                                         "consequent": f"block({t.id})"})
            else:
                rules[inst.id] = inst

    by_schema: dict[str, list[Rule]] = {}
    for rule in rules.values():
        by_schema.setdefault(rule.schema_id, []).append(rule)

    conflicts: set[frozenset[str]] = set()
    conflict_info: dict[frozenset[str], Conflict] = {}
    for c in raw["conflicts"]:
        for a in by_schema.get(c["left"], []):
            for b in by_schema.get(c["right"], []):
                if compatible(a, b):
                    key = frozenset((a.id, b.id))
                    conflicts.add(key)
                    conflict_info[key] = Conflict(a.id, b.id, c.get("source"), c.get("basis"), c.get("implied", False))
    priorities: set[tuple[str, str]] = set()
    priority_info: dict[tuple[str, str], Priority] = {}
    for p in raw["priorities"]:
        for a in by_schema.get(p["stronger"], []):
            for b in by_schema.get(p["weaker"], []):
                if compatible(a, b) and frozenset((a.id, b.id)) in conflicts:
                    priorities.add((a.id, b.id))
                    priority_info[(a.id, b.id)] = Priority(a.id, b.id, p.get("source"), p.get("basis"))

    facts = {f["atom"]: Fact(f["atom"], f.get("source"), f.get("confidence")) for f in raw["facts"]}
    return Theory(raw.get("case_id"), raw["query"], facts, rules, conflicts, conflict_info,
                  priorities, priority_info, raw.get("source_units"), dict(raw.get("settings", {})))


# ---- provenance helpers ------------------------------------------------------------------

def resolve_pointer(pointer: str | None, units: dict | None) -> tuple[bool, str]:
    """(ok, reason). Pointer = unit id, optionally '#start-end' relative to the unit text."""
    if units is None:
        return True, "no source units supplied"
    if not pointer:
        return False, "missing source pointer"
    uid, _, span = str(pointer).partition("#")
    if uid not in units:
        return False, f"unknown source unit {uid!r}"
    if span:
        try:
            start, end = (int(x) for x in span.split("-", 1))
        except ValueError:
            return False, f"malformed span {span!r}"
        text = units[uid].get("text") or ""
        if not (0 <= start < end <= len(text)):
            return False, f"span {start}-{end} outside unit text (length {len(text)})"
    return True, "ok"
