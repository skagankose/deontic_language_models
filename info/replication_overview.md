# Replication overview: Ulizko et al. (2026), Proof-Carrying Neuro-Symbolic Reasoning (BDCC 10:295)

## What the paper proposes

The LLM never gives the legal answer. It only turns text into logic, and a deterministic solver decides:

1. **Evidence package:** fixed, source-linked clauses plus a query. Retrieval is not part of the paper's evaluation.
2. **LLM extraction:** facts, rules (strict, defeasible or defeater), conflicts between rules, priorities (which rule wins), and a citation for each item.
3. **Validation and repair:** schema, symbol, citation and graph checks. A failing item gets one targeted LLM re-extraction; anything still unresolved becomes Undetermined and is flagged for human review.
4. **Priority-cycle repair:** if priorities form a loop, it is broken by authority, then specificity, then date, then directness. If none of these settles it, the answer is Undetermined with a review flag.
5. **Reasoning:** build arguments from the rules, work out which arguments defeat which given the priorities, and keep the skeptical set of accepted arguments (the grounded extension). The answer is Positive, Negative or Undetermined.
6. **Proof and Check:** every conclusion ships with a proof graph, which an independent checker verifies.
7. **Evaluation:** a controlled benchmark, an LLM experiment against baselines, a public-contract and Russian-law subset, stress tests, scalability runs, and a lawyer study.

## What we used, what we built, what remains

| Part | Released by the authors (we just used it) | Missing, so we built it |
|---|---|---|
| **Core solver** (arguments, defeats, grounded extension) | ✅ ~160-line reference solver | Re-implemented in `pcnr`; matches theirs on all 1,040 released records and on 2,862 random theories |
| **Deontic logic** (Obl/Perm/Forb) | ❌ treated as plain strings | ✅ normalisation, opposition checks, optional duality |
| **Validator** | ❌ | ✅ 4 stages, typed diagnostics, quarantine of bad items |
| **Repair loop** | ❌ | ✅ one bounded repair pass, wired to gpt-6-luna |
| **Priority-cycle repair** | ❌ | ✅ also exposed that the paper's rule can't break exception cycles |
| **Proof graphs and checker** | ❌ only coarse sketches | ✅ argument-level proofs plus an independent checker; forged proofs are rejected |
| **Extraction prompt and schema** | ✅ | Added representation conventions for the model |
| **Contract text and benchmark** | ❌ placeholder text; 1,040 records are really 8 logic problems | ✅ 240 scenarios: 120 distinct theories × 2 paraphrases, 4 domains (2 deontic), exact answer keys |
| **LLM outputs** | ❌ | ✅ gpt-6-luna extraction on all 240 scenarios |
| **Extraction error metrics** | aggregate numbers only | ✅ per-object matching giving the fact, rule, priority and conflict error rates, plus citation accuracy |
| **Baselines** | labels only | ✅ direct LLM, probabilistic classifier (PDL), lexical, monotonic, and priority without nested exceptions |
| **Statistics scripts** | ✅ McNemar, Holm, bootstrap | Reused on our data; all published numbers reproduce from their saved predictions |
| **Stress tests** | result tables only | ✅ injected extraction errors and priority cycles |
| **Scalability** | result table only; engine not released | ✅ timing runs from 10 to 5,000 rules |
| **Lawyer study** | 216 anonymized rows (statistics reproduce) | ❌ needs human participants |

## Key findings along the way

- **Unstated labeling rule:** the released labels treat "no rule fires" as Negative, which the paper never states.
- **Malformed records:** 163 released "Undetermined" records only come out that way because they accept both the conclusion and its negation.
- **Specificity can't break exception cycles:** under the paper's own encoding (an exception lists only its own conditions), ranking by specificity can never resolve a cycle between an exception and its parent. The numeric specificity values in the released records suggest the authors did it differently from what the paper says.
- **Main result (gpt-6-luna):** the proposed pipeline reaches 97.9% accuracy, versus 78.8% when the model answers directly. It also significantly beats every baseline, and every proof passes the checker.

## Still open

- The lawyer study, which needs human participants.
- Statistical tests that treat the two paraphrases of a theory as one case.
- Optionally, a run with a second model.

## Where things are

| Folder | Contents |
|---|---|
| `neuro_symbolic/` | the authors' repository (commit `3951065`) |
| `replication/` | first-pass checks of the authors' code and data |
| `solver/` | `pcnr`, our full symbolic layer, with its tests |
| `bench/` | benchmark generator, extraction, scoring, baselines, stress tests, scalability, run outputs |
| `data/option_a/` | the earlier 8-case extraction package from another agent |
| `info/` | these summary notes |
