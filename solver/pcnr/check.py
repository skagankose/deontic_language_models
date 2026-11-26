"""Independent proof checker: Check(G_c, T, c) = RootOK ∧ SourceOK ∧ EdgeOK ∧ CompleteOK (Eq. 9).

Deliberately does NOT import the reasoner. It re-derives what it needs from the theory:
  * its own argument enumeration (by canonical signature),
  * its own attack/defeat computation,
  * the effective priority relation (explicit priorities + its own run of the deterministic
    κL repair),
and verifies grounded membership via the proof's rank certificate instead of recomputing the
grounded extension: every defeater of a displayed accepted argument of rank k must be defeated
by a displayed accepted argument of rank < k (rank-1 arguments must be unattacked).

  RootOK      root(s) conclude c, c matches the label, roots are certified accepted
  SourceOK    fact/rule nodes match T; every displayed fact, rule, conflict and priority has a
              resolvable source pointer (facts only if require_fact_sources)
  EdgeOK      support edges instantiate rule antecedents in order; defeat edges ∈ Defeat_T;
              priority edges are explicit or recorded in the repair log
  CompleteOK  for every displayed accepted argument the listed attackers = all its defeaters
"""

from __future__ import annotations

import itertools
from collections import defaultdict

from . import literals as L
from .priority import repair_cycles
from .theory import Theory, resolve_pointer


class _World:
    def __init__(self, theory: Theory):
        self.t = theory
        self.info: dict[str, tuple[str, str | None, tuple[str, ...]]] = {}
        self.rules_of: dict[str, frozenset[str]] = {}
        self.subs: dict[str, frozenset[str]] = {}
        by_concl: dict[str, list[str]] = defaultdict(list)
        for f in theory.facts:
            s = f"fact[{f}]"
            self.info[s] = (f, None, ())
            self.rules_of[s] = frozenset()
            self.subs[s] = frozenset({s})
            by_concl[f].append(s)
        changed = True
        while changed:
            changed = False
            for rid, r in theory.rules.items():
                pools = [list(by_concl.get(a, ())) for a in r.antecedents]
                if any(not p for p in pools):
                    continue
                for combo in itertools.product(*pools):
                    if any(rid in self.rules_of[c] for c in combo):
                        continue
                    s = f"{rid}({','.join(combo)})"
                    if s in self.info:
                        continue
                    concl = f"block({r.target})" if r.type == "defeater" else r.consequent
                    self.info[s] = (concl, rid, tuple(combo))
                    self.rules_of[s] = frozenset({rid}).union(*(self.rules_of[c] for c in combo))
                    self.subs[s] = frozenset({s}).union(*(self.subs[c] for c in combo))
                    by_concl[concl].append(s)
                    changed = True
        self.repair = repair_cycles(theory)
        self.priorities = self.repair.effective
        self._defeaters: dict[str, set[str]] | None = None

    def _attack_at(self, attacker: str, b_prime: str) -> bool:
        """Does `attacker` attack the subargument b_prime at its top rule (before priorities)?"""
        ra_id, rb_id = self.info[attacker][1], self.info[b_prime][1]
        if ra_id is None or rb_id is None:
            return False
        ra, rb = self.t.rules[ra_id], self.t.rules[rb_id]
        if ra.type == "defeater":
            hit = ra.target == rb_id
        else:
            hit = rb.type != "defeater" and frozenset((ra_id, rb_id)) in self.t.conflicts
        return hit and not (rb.type == "strict" and ra.type != "strict")

    def attacks_on(self, attacker: str, target: str) -> bool:
        """Does `attacker` defeat `target` (some B' ⊑ target, Eq. 4)?"""
        ra_id = self.info[attacker][1]
        return any(self._attack_at(attacker, b) and (self.info[b][1], ra_id) not in self.priorities
                   for b in self.subs[target])

    def blocked_at(self, attacker: str, b_prime: str) -> bool:
        """Attack on b_prime exists but fails because top(b_prime) ≻ top(attacker)."""
        return self._attack_at(attacker, b_prime) and \
            (self.info[b_prime][1], self.info[attacker][1]) in self.priorities

    def defeaters(self, target: str) -> set[str]:
        if self._defeaters is None:
            self._defeaters = defaultdict(set)
            sigs = list(self.info)
            for a in sigs:
                if self.info[a][1] is None:
                    continue
                for b in sigs:
                    if self.attacks_on(a, b):
                        self._defeaters[b].add(a)
        return self._defeaters[target]


def check(proof: dict, theory: Theory, *, require_fact_sources: bool = False,
          deontic_duality: bool = False) -> dict:
    w = _World(theory)
    problems: dict[str, list[str]] = {"RootOK": [], "SourceOK": [], "EdgeOK": [], "CompleteOK": []}
    q = theory.query
    label = proof.get("accepted_conclusion")
    kind = proof.get("kind")

    if kind == "absence":
        if label != "Negative":
            problems["RootOK"].append("absence proof must carry label Negative")
        if any(info[0] in (q, L.complement(q)) for info in w.info.values()):
            problems["RootOK"].append("an argument for the query or its complement exists")
        return _result(problems)
    if kind != "acceptance":
        return {"applicable": False, "ok": None, "reason": f"no Check for proof kind {kind!r}"}

    c = proof.get("conclusion")
    pos = {q} | (L.duality_positives(q) if deontic_duality else set())
    neg = {L.complement(q)} | (L.duality_negatives(q) if deontic_duality else set())
    if not ((label == "Positive" and c in pos) or (label == "Negative" and c in neg)):
        problems["RootOK"].append(f"conclusion {c!r} does not justify label {label!r}")

    nodes = {n["id"]: n for n in proof["nodes"]}
    args = {i: n for i, n in nodes.items() if n["type"] == "argument"}
    sig = {i: n.get("signature") for i, n in args.items()}
    for i, s in sig.items():
        if s not in w.info:
            problems["EdgeOK"].append(f"{i}: signature {s!r} is not constructible from T")
        elif w.info[s][0] != args[i]["conclusion"]:
            problems["EdgeOK"].append(f"{i}: conclusion does not match its signature")
    if problems["EdgeOK"]:
        return _result(problems)
    sig_to_id = {s: i for i, s in sig.items()}

    roots = [i for i, n in args.items() if n.get("role") == "root"]
    if not roots:
        problems["RootOK"].append("no root argument")
    for r in roots:
        if args[r]["conclusion"] != c:
            problems["RootOK"].append(f"root {r} concludes {args[r]['conclusion']!r}, not {c!r}")
        if args[r].get("status") != "IN":
            problems["RootOK"].append(f"root {r} not marked accepted")
    if not any(e["type"] == "concludes" and e["target"] == "C" and e["source"] in roots for e in proof["edges"]):
        problems["RootOK"].append("no concludes edge from a root to the conclusion node")

    # certified set = arguments whose complete attacker list the proof declares
    listed = proof.get("attackers_listed", {})
    accepted = set(listed)
    for i in accepted:
        if i not in args or args[i].get("status") != "IN":
            problems["RootOK"].append(f"certified argument {i} is not a displayed accepted argument")
    accepted &= set(args)
    for r in roots:
        if r not in accepted:
            problems["RootOK"].append(f"root {r} is not certified")
    for i in accepted:
        k = args[i].get("rank")
        if not isinstance(k, int) or k < 1:
            problems["RootOK"].append(f"{i}: missing rank")
            continue
        for y in w.defeaters(sig[i]):
            ok = any(isinstance(args[z].get("rank"), int) and args[z]["rank"] < k
                     and w.attacks_on(sig[z], y) for z in accepted)
            if not ok:
                problems["RootOK"].append(f"{i} (rank {k}): defeater {y} is not defeated by a lower-ranked displayed argument")
    # the certified set is closed under subarguments
    for i in accepted:
        for s in w.subs[sig[i]]:
            if s not in sig_to_id or sig_to_id[s] not in accepted:
                problems["EdgeOK"].append(f"{i}: subargument {s} is not displayed and certified")

    # SourceOK
    units = theory.source_units
    for n in nodes.values():
        if n["type"] == "fact":
            f = theory.facts.get(n["label"])
            if f is None:
                problems["SourceOK"].append(f"fact {n['label']} is not in F")
            elif require_fact_sources:
                ok, why = resolve_pointer(f.source, units)
                if not ok:
                    problems["SourceOK"].append(f"fact {n['label']}: {why}")
        elif n["type"] == "rule":
            r = theory.rules.get(n["label"])
            if r is None:
                problems["SourceOK"].append(f"rule {n['label']} is not in R")
                continue
            if (list(r.antecedents), r.out, r.type, r.source) != (n["antecedents"], n["consequent"], n["rule_type"], n["source"]):
                problems["SourceOK"].append(f"rule {n['label']} differs from T")
            ok, why = resolve_pointer(r.source, units)
            if not ok:
                problems["SourceOK"].append(f"rule {n['label']}: {why}")

    # EdgeOK
    for e in proof["edges"]:
        t = e["type"]
        if t == "support":
            a = args.get(e["target"])
            child = args.get(e["source"])
            if a is None or child is None or a["top"] is None:
                problems["EdgeOK"].append(f"support edge {e} has bad endpoints")
                continue
            r = theory.rules[a["top"]]
            i = e.get("antecedent_index")
            if not isinstance(i, int) or i >= len(r.antecedents) or r.antecedents[i] != child["conclusion"] \
                    or w.info[sig[a["id"]]][2][i] != sig[child["id"]]:
                problems["EdgeOK"].append(f"support edge {e['source']}->{e['target']} does not instantiate antecedent {i}")
        elif t == "premise":
            a = args.get(e["target"])
            if a is None or a["top"] is not None or nodes.get(e["source"], {}).get("label") != a["conclusion"]:
                problems["EdgeOK"].append(f"premise edge {e} invalid")
        elif t == "applies":
            a = args.get(e["target"])
            if a is None or f"R:{a['top']}" != e["source"]:
                problems["EdgeOK"].append(f"applies edge {e} invalid")
        elif t == "defeat":
            s, tg = sig.get(e["source"]), sig.get(e["target"])
            if s is None or tg is None or not w.attacks_on(s, tg):
                problems["EdgeOK"].append(f"defeat edge {e['source']}->{e['target']} is not in Defeat_T")
                continue
            ra = w.info[s][1]
            rb = w.info[sig[e["via"]]][1] if e.get("via") in sig else None
            pair = frozenset((ra, rb)) if rb else None
            if e.get("kind") == "rebut" and pair in theory.conflict_info:
                ok, why = resolve_pointer(theory.conflict_info[pair].source, units)
                if not ok:
                    problems["SourceOK"].append(f"conflict {ra}⋈{rb}: {why}")
        elif t == "attack_blocked":
            a_s, tg, via = sig.get(e["source"]), sig.get(e["target"]), sig.get(e.get("via"))
            if None in (a_s, tg, via) or via not in w.subs[tg] or not w.blocked_at(a_s, via):
                problems["EdgeOK"].append(f"blocked attack {e['source']}->{e['target']} is not an attack "
                                          f"overridden by a priority")
            elif e.get("blocked_by") != [f"R:{w.info[via][1]}", f"R:{w.info[a_s][1]}"]:
                problems["EdgeOK"].append(f"blocked attack {e['source']}->{e['target']} cites the wrong priority")
        elif t == "priority":
            pair = (e["source"][2:], e["target"][2:])
            if pair not in w.priorities:
                problems["EdgeOK"].append(f"priority {pair} is not in the effective priority relation")
            elif "repair_log" in e:
                if not any(x.get("oriented") == list(pair) for x in w.repair.log):
                    problems["EdgeOK"].append(f"priority {pair} cites a repair that did not occur")
            else:
                if pair not in theory.priorities:
                    problems["EdgeOK"].append(f"priority {pair} is not explicit in T")
                ok, why = resolve_pointer(theory.priority_info[pair].source if pair in theory.priority_info else None, units)
                if not ok:
                    problems["SourceOK"].append(f"priority {pair}: {why}")

    # CompleteOK
    for i in accepted:
        claimed = {sig.get(x) for x in listed.get(i, [])}
        actual = w.defeaters(sig[i])
        if claimed != actual:
            missing = sorted(actual - claimed)
            extra = sorted(x for x in claimed - actual if x)
            problems["CompleteOK"].append(f"{i}: missing attackers {missing}, extra {extra}")
        for x in listed.get(i, []):
            if x not in args:
                problems["CompleteOK"].append(f"{i}: listed attacker {x} is not displayed")
            elif not any(e["type"] == "defeat" and e["source"] == x and e["target"] == i for e in proof["edges"]):
                problems["CompleteOK"].append(f"{i}: no defeat edge from listed attacker {x}")
    return _result(problems)


def _result(problems: dict[str, list[str]]) -> dict:
    out = {k: not v for k, v in problems.items()}
    out["ok"] = all(out.values())
    out["applicable"] = True
    out["problems"] = {k: v for k, v in problems.items() if v}
    return out
