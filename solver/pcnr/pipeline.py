"""Algorithm 1, steps 3–9: validate -> (bounded repair) -> priority repair -> arguments ->
defeats -> grounded extension -> decision -> proof -> Check.

Decision (Eq. 1) with the choices the paper leaves implicit exposed as Config switches:
  q accepted and comp(q) not           -> Positive
  comp(q) accepted and q not           -> Negative
  both accepted                        -> Undetermined + review flag (INCONSISTENT_EXTENSION;
                                          only possible when opposed rules lack a declared conflict)
  neither accepted:
      no argument for q or comp(q) exists and absence_policy == "negative" -> Negative
          (closed-world default reproducing the released gold labels)
      otherwise                        -> Undetermined
  forced Undetermined + review flag when
      * blocking validation errors remain and on_invalid == "abstain"
      * an unresolved priority cycle touches a rule relevant to the query and
        cycle_policy == "abstain_query"
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from . import literals as L
from .check import check
from .priority import repair_cycles
from .proof import build_proof
from .reasoner import blocked_attacks, compile_defeats, construct_arguments, grounded, statuses
from .theory import coerce_raw, ground, normalize
from .validate import RepairHook, repair_once, validate


@dataclass
class Config:
    absence_policy: str = "undetermined"     # "undetermined" (Eq. 1 literally) | "negative"
    cycle_policy: str = "abstain_query"      # "abstain_query" | "drop_edges"
    on_invalid: str = "abstain"              # "abstain" | "drop" (reason on the valid remainder)
    require_sources: bool = True
    require_fact_sources: bool = False
    deontic_duality: bool = False            # D axiom / O-P duality in the decision function
    vocabulary: frozenset[str] | None = None
    max_arguments: int = 200_000
    run_check: bool = True


@dataclass
class Result:
    case_id: str | None
    query: str
    label: str
    reason: str
    review_flag: bool
    review_reasons: list[str]
    accepted: list[str]
    diagnostics: list[dict]
    normalization_log: list[dict]
    repair_attempts: list[dict]
    priority_repair_log: list[dict]
    n_arguments: int
    n_defeats: int
    proof: dict | None
    check: dict | None
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def run(obj: dict, *, query: str | None = None, source_units=None, config: Config | None = None,
        repair_hook: RepairHook | None = None, settings: dict | None = None) -> Result:
    cfg = config or Config()
    t0 = time.perf_counter()
    raw = coerce_raw(obj, query=query, source_units=source_units, settings=settings)
    raw, norm_log = normalize(raw)
    vkw = dict(require_sources=cfg.require_sources, require_fact_sources=cfg.require_fact_sources,
               vocabulary=set(cfg.vocabulary) if cfg.vocabulary else None)
    report = validate(raw, **vkw)
    attempts: list[dict] = []
    if repair_hook and any(d.severity == "error" and d.repairable for d in report.diagnostics):
        raw, report, attempts = repair_once(raw, report, repair_hook, **vkw)
    t1 = time.perf_counter()

    review: list[str] = []
    if report.blocking:
        review.append(f"{len(report.blocking)} blocking validation error(s) after repair")
    if any(d.severity == "error" and not d.blocking for d in report.diagnostics):
        review.append("provenance error(s): proof will not pass SourceOK")
    if any(d.code == "QUERY_INVALID" for d in report.diagnostics):
        return Result(raw.get("case_id"), str(raw.get("query")), "Undetermined", "invalid query", True,
                      review, [], [d.to_dict() for d in report.diagnostics], norm_log, attempts, [],
                      0, 0, None, None)

    theory = ground(report.clean_raw)
    prep = repair_cycles(theory)
    t2 = time.perf_counter()
    args = construct_arguments(theory, cfg.max_arguments)
    defeats = compile_defeats(theory, args, prep.effective)
    rank = grounded(args, defeats)
    status = statuses(args, defeats, rank)
    t3 = time.perf_counter()

    q = theory.query
    accepted = sorted({a.conclusion for a in args if a.id in rank and not L.is_blocker(a.conclusion)})
    acc = set(accepted)
    pos_lits = [q] + sorted(L.duality_positives(q)) if cfg.deontic_duality else [q]
    neg_lits = [L.complement(q)] + sorted(L.duality_negatives(q)) if cfg.deontic_duality else [L.complement(q)]
    pos = next((x for x in pos_lits if x in acc), None)
    neg = next((x for x in neg_lits if x in acc), None)
    kind, conclusion = "abstention", None
    if pos and neg:
        label, reason = "Undetermined", "both the query and its complement are accepted"
        review.append("INCONSISTENT_EXTENSION: opposed conclusions accepted together; "
                      "a conflict between opposed rules is probably undeclared")
    elif pos:
        label, reason, kind, conclusion = "Positive", f"{pos} is accepted", "acceptance", pos
    elif neg:
        label, reason, kind, conclusion = "Negative", f"{neg} is accepted", "acceptance", neg
    else:
        no_arg = not any(a.conclusion in (pos_lits + neg_lits) for a in args)
        if no_arg and cfg.absence_policy == "negative":
            label, reason, kind = "Negative", "closed-world: no argument for the query or its complement", "absence"
        else:
            label = "Undetermined"
            reason = "no argument for either side" if no_arg else "unresolved conflict: neither side is defended"

    # forced abstention
    if prep.unresolved:
        relevant = _relevant_rules(q, args, defeats, pos_lits + neg_lits)
        touched = [sorted(c) for c in prep.unresolved if c & relevant]
        if touched:
            review.append(f"unresolved priority cycle(s) {touched} affect the query")
            if cfg.cycle_policy == "abstain_query" and label != "Undetermined":
                label, reason, kind, conclusion = "Undetermined", "unresolved priority cycle", "abstention", None
    if report.blocking and cfg.on_invalid == "abstain" and label != "Undetermined":
        label, reason, kind, conclusion = "Undetermined", "theory failed validation", "abstention", None

    proof = build_proof(theory, args, defeats, rank, status, label=label, conclusion=conclusion,
                        kind=kind, priorities=prep.effective, repair_log=prep.log,
                        blocked=blocked_attacks(theory, args, prep.effective) if kind == "acceptance" else ())
    t4 = time.perf_counter()
    chk = check(proof, theory, require_fact_sources=cfg.require_fact_sources,
                deontic_duality=cfg.deontic_duality) if cfg.run_check else None
    t5 = time.perf_counter()
    return Result(
        theory.case_id, q, label, reason, bool(review), review, accepted,
        [d.to_dict() for d in report.diagnostics], norm_log, attempts, prep.log,
        len(args), len(defeats), proof, chk,
        {"validate": 1e3 * (t1 - t0), "ground+priority": 1e3 * (t2 - t1), "reason": 1e3 * (t3 - t2),
         "proof": 1e3 * (t4 - t3), "check": 1e3 * (t5 - t4)},
    )


def _relevant_rules(q, args, defeats, lits) -> set[str]:
    """Rules of arguments for q/¬q and, transitively, of their attackers."""
    by_id = {a.id: a for a in args}
    attackers = {}
    for d in defeats:
        attackers.setdefault(d.target, set()).add(d.attacker)
    frontier = [a.id for a in args if a.conclusion in lits]
    seen = set()
    while frontier:
        x = frontier.pop()
        if x in seen:
            continue
        seen.add(x)
        frontier.extend(attackers.get(x, ()))
    return set().union(*(by_id[x].rules for x in seen)) if seen else set()
