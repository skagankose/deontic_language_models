"""Argument construction, defeat compilation and grounded semantics (paper §3.5–3.6).

Semantics implemented (Eq. 3–7), with the points where the paper is silent made explicit:

  Arguments  A0 = fact arguments; B_T(S) adds <P_r, U_r, out(r), r> for every rule r whose
             antecedents are concluded (in order) by arguments in S ∪ A0. Arg(T) = lfp(B_T).
             Signatures are canonical trees "r(sig1,sig2,...)", so construction is deterministic.
  Attack     (A,B) iff ∃B' ⊑ B with
               rebut:    top(A), top(B') derivational and top(A) ⋈ top(B')
               undercut: top(A) is a defeater whose target is top(B')
             [ours] a defeasible/defeater attacker cannot attack B' whose top rule is strict
             (strict conclusions are only attackable through their defeasible subarguments).
             [ours, = authors' reference code] undercut is one-directional: the targeted rule
             does not attack the defeater (a blocker token does not contradict a legal literal).
  Defeat     attack on B' succeeds unless top(B') ≻ top(A)   (Eq. 4)
  Grounded   S0 = ∅, S_{i+1} = Γ(S_i); rank(A) = least i with A ∈ S_i (membership certificate)
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass

from .theory import Theory


@dataclass(frozen=True)
class Argument:
    id: str
    conclusion: str
    top: str | None                 # rule id; None for fact arguments
    children: tuple[str, ...]       # direct subarguments, aligned with antecedents of top
    premises: frozenset[str]
    rules: frozenset[str]
    subs: frozenset[str]            # all subarguments including self
    signature: str

    @property
    def is_fact(self) -> bool:
        return self.top is None


@dataclass(frozen=True)
class Defeat:
    attacker: str
    target: str
    via: str                        # attacked subargument B' ⊑ target
    kind: str                       # rebut | undercut
    priority: tuple[str, str] | None  # (stronger, weaker) if a priority made it asymmetric


class ArgumentLimit(RuntimeError):
    pass


def construct_arguments(theory: Theory, max_arguments: int = 200_000) -> list[Argument]:
    args: list[Argument] = []
    by_concl: dict[str, list[Argument]] = defaultdict(list)
    seen: set[str] = set()

    def add(conclusion, top, children, premises, rules, signature):
        aid = f"A{len(args) + 1}"
        subs = frozenset({aid}).union(*(a.subs for a in children))
        arg = Argument(aid, conclusion, top, tuple(c.id for c in children), premises, rules, subs, signature)
        args.append(arg)
        by_concl[conclusion].append(arg)
        seen.add(signature)
        if len(args) > max_arguments:
            raise ArgumentLimit(f"more than {max_arguments} arguments")

    for lit in sorted(theory.facts):
        add(lit, None, (), frozenset((lit,)), frozenset(), f"fact[{lit}]")

    rules = sorted(theory.rules.values(), key=lambda r: r.id)
    changed = True
    while changed:
        changed = False
        for r in rules:
            choices = [list(by_concl.get(a, ())) for a in r.antecedents]
            if any(not c for c in choices):
                continue
            for combo in itertools.product(*choices):
                if any(r.id in c.rules for c in combo):   # no repeated rule instance on a branch
                    continue
                sig = f"{r.id}({','.join(c.signature for c in combo)})"
                if sig in seen:
                    continue
                add(r.out, r.id, combo,
                    frozenset().union(*(c.premises for c in combo)) if combo else frozenset(),
                    frozenset({r.id}).union(*(c.rules for c in combo)), sig)
                changed = True
    return args


def compile_defeats(theory: Theory, args: list[Argument],
                    priorities: set[tuple[str, str]]) -> list[Defeat]:
    """Defeat_T (Eq. 4): attacks not blocked by a priority of the attacked rule."""
    return [d for d, ok in _attacks(theory, args, priorities) if ok]


def blocked_attacks(theory: Theory, args: list[Argument],
                    priorities: set[tuple[str, str]]) -> list[Defeat]:
    """Attacks that fail because top(B') ≻ top(A); `priority` holds that (stronger, weaker) pair.
    Shown in proofs so a reviewer sees counterarguments that were considered and overridden."""
    return [d for d, ok in _attacks(theory, args, priorities) if not ok]


def _attacks(theory: Theory, args: list[Argument], priorities: set[tuple[str, str]]):
    rules = theory.rules
    # index: rule id -> arguments whose top is that rule (the possible B')
    by_top: dict[str, list[Argument]] = defaultdict(list)
    for a in args:
        if a.top:
            by_top[a.top].append(a)
    # index: subargument id -> arguments containing it
    containing: dict[str, list[Argument]] = defaultdict(list)
    for a in args:
        for s in a.subs:
            containing[s].append(a)
    # attackable rules for each attacker rule
    targets_of: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in rules.values():
        if r.type == "defeater":
            if r.target in rules:
                targets_of[r.id].append((r.target, "undercut"))
    for pair in theory.conflicts:
        a, b = tuple(pair)
        ra, rb = rules.get(a), rules.get(b)
        if not ra or not rb or "defeater" in (ra.type, rb.type):
            continue
        targets_of[a].append((b, "rebut"))
        targets_of[b].append((a, "rebut"))

    ok_edges: dict[tuple[str, str], Defeat] = {}
    blocked: dict[tuple[str, str], Defeat] = {}
    for attacker in args:
        if attacker.is_fact:
            continue
        ra = rules[attacker.top]
        for target_rule, kind in targets_of.get(attacker.top, ()):
            rb = rules[target_rule]
            if rb.type == "strict" and ra.type != "strict":
                continue
            fails = (rb.id, ra.id) in priorities      # top(B') ≻ top(A): attack fails
            prio = (rb.id, ra.id) if fails else ((ra.id, rb.id) if (ra.id, rb.id) in priorities else None)
            for b_prime in by_top.get(target_rule, ()):
                for target in containing[b_prime.id]:
                    key = (attacker.id, target.id)
                    if fails:
                        blocked.setdefault(key, Defeat(attacker.id, target.id, b_prime.id, kind, prio))
                    else:
                        ok_edges.setdefault(key, Defeat(attacker.id, target.id, b_prime.id, kind, prio))
    order = lambda d: (int(d.attacker[1:]), int(d.target[1:]))  # noqa: E731
    for d in sorted(ok_edges.values(), key=order):
        yield d, True
    for key, d in sorted(blocked.items(), key=lambda kv: order(kv[1])):
        if key not in ok_edges:          # another subargument is successfully attacked
            yield d, False


def grounded(args: list[Argument], defeats: list[Defeat]) -> dict[str, int]:
    """Grounded extension by iterating Γ from ∅ (Eq. 5–6). Returns {argument id: rank}."""
    attackers: dict[str, set[str]] = defaultdict(set)
    for d in defeats:
        attackers[d.target].add(d.attacker)
    victims: dict[str, set[str]] = defaultdict(set)
    for d in defeats:
        victims[d.attacker].add(d.target)
    ids = [a.id for a in args]
    rank: dict[str, int] = {}
    current: set[str] = set()
    i = 0
    while True:
        i += 1
        defeated = set().union(*(victims[c] for c in current)) if current else set()
        nxt = {a for a in ids if attackers[a] <= defeated}
        if nxt == current:
            return rank
        for a in nxt - current:
            rank[a] = i
        current = nxt


def statuses(args: list[Argument], defeats: list[Defeat], rank: dict[str, int]) -> dict[str, str]:
    """IN (grounded), OUT (defeated by an IN argument), UNDEC (neither)."""
    out = {d.target for d in defeats if d.attacker in rank}
    return {a.id: "IN" if a.id in rank else "OUT" if a.id in out else "UNDEC" for a in args}
