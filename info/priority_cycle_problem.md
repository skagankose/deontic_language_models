# Why priority-cycle repair can't break exception cycles

A short note on a problem found while replicating Ulizko et al. (2026), §3.7 "Priority Repair and Cyclic Priorities".

## The mechanism in the paper

Extracted priorities can form a **cycle**: clause A prevails over B *and* B prevails over A (for example, both
clauses say "notwithstanding the other clause"). The paper repairs a cycle by comparing the two rules with a
lexicographic key:

    κL(r) = <authority, specificity, effectiveDate, directness>

The first component that gives a strict, source-verifiable answer decides the direction. If none does, the cycle stays
unresolved, and the query becomes **Undetermined** with a human-review flag.

The paper defines **specificity** as *"strict inclusion of the rules' validated factual and legal scope"*: a rule is more
specific if its conditions strictly include the other rule's conditions.

## The problem

The paper's own encoding writes an exception with **only its own conditions** (Table 2, and every released record):

```
r1: ValidContract(x), Delay(x) ⇒ EnforceablePenalty(x)     general rule
r2: ForceMajeure(x)            ⇒ ¬EnforceablePenalty(x)    exception to r1
```

`{ForceMajeure}` is not a subset of `{ValidContract, Delay}`, and vice versa. The condition sets are **incomparable**,
so specificity cannot order an exception against the rule it excepts. This holds for every exception under this encoding.

The other κL components rarely help:
- **Authority:** only applies when the rules come from different levels of the source hierarchy (statute vs contract). Within one contract it ties.
- **Effective date:** only applies when the governing instrument authorises temporal precedence.
- **Directness:** only applies when one rule comes from a cross-reference or is inferred.

So for a cycle between an exception and its parent inside one contract, **κL as described never resolves it**.

## Evidence

Cyclic-priority stress test (`bench/stress.py`): we added the reverse of one existing priority edge to 25/50/100 valid
gold theories of our benchmark.

| Specificity used by κL | Auto-repaired | Review flags | False determinate | Accuracy after |
|---|---|---|---|---|
| Strict inclusion of conditions (paper text) | **0%** | 60–67% | 0% | 34–40% |
| Numeric depth (as in the released records) | 100% | 0% | 0% | 100% |
| *Paper, Table 18* | *79–88%* | *12–21%* | *0%* | *87–92%* |

- **Review flags below 100%:** the cycles that weren't flagged did not touch the query, so the answer was unaffected.
- **Released metadata:** the authors' 1,040 released records carry a numeric `meta.specificity` per rule, by exception depth: r1 = 1, r2 = r4 = 2, r3 = r5 = 3 (all with `authority: 2`). That suggests the paper's repair used these numbers rather than condition inclusion as described.
- **The numeric row is circular here:** we derived the depth from the gold priorities themselves, so it restores the answer key by construction.

The paper's 79–88% sits between the two regimes. It depends on *where the specificity numbers come from*, which the paper does not explain.

## Why it matters

- **The failure is safe.** An unresolved cycle leads to abstention plus a review flag, never a wrong answer (0% false determinate in every setting).
- **It is costly.** Under the paper's stated definition, every exception cycle goes to human review, and the automatic repair the paper reports cannot happen.
- **It is unreproducible as written.** Matching the paper requires numeric specificity metadata. But the paper doesn't say who assigns it (annotators? the LLM?), and the extraction schema has no field for it.
- **A related danger is not caught by repair at all.** If the LLM extracts only *one* direction of a real cycle, no cycle is visible and the system gives a confident answer where it should abstain. Both GPT-4o and gpt-6-luna did this on the same benchmark case (T0005).

## How our solver handles it

`solver/pcnr/priority.py` implements κL as the paper describes it (inclusion-based specificity) by default. One setting
switches to the numeric variant:

```python
theory.settings["specificity_from_meta"] = True   # use rule.meta["specificity"] numbers when both rules have them
```

With the default, exception cycles stay unresolved: the cycle's priority edges are removed, and the query is Undetermined
and flagged (`cycle_policy="abstain_query"`). The repair log records every component comparison, so a reviewer can see why.

## Possible fixes (not implemented)

1. **Measure specificity on the exception's scope.** Treat an exception as applying within its parent's conditions, so
   that `r2`'s effective scope becomes `{ValidContract, Delay, ForceMajeure}` ⊋ `{ValidContract, Delay}`. Inclusion then works.
   This requires knowing which rule an exception belongs to, which is the very link the cycle makes ambiguous.
2. **Make specificity explicit extracted metadata.** Add `meta.specificity` (or "this clause is an exception to clause n")
   to the extraction schema and evaluate it like any other extracted object.
3. **Use exception language as evidence.** "Unless …" or "except where …" already states the direction. A cycle arising
   from such language is an extraction error that should be re-extracted, not resolved by κL.
4. **Detect half-cycles.** When one clause says "notwithstanding Clause n", check that the extraction didn't drop a
   matching statement in Clause n.
