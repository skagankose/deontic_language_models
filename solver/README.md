# pcnr: proof-carrying non-monotonic reasoner

A standard-library-only re-implementation of the symbolic layer of Ulizko et al. (2026),
*Proof-Carrying Neuro-Symbolic Reasoning for Non-Monotonic Legal Decision Support with LLMs*
(BDCC 10:295). It covers Algorithm 1 steps 3–9. The authors' public `symbolic_reasoner_reference.py`
covers only argument construction, defeats and the grounded extension.

| Paper | Module | Authors' reference code |
|---|---|---|
| §3.2 literal language, Obl/Perm/Forb, Forb(a) → Obl(¬a) | `pcnr/literals.py` | opaque strings |
| §3.3 theory, finite grounding of rule schemas | `pcnr/theory.py` | ground only |
| §3.4 4-stage validator, typed diagnostics, bounded repair | `pcnr/validate.py` | — |
| §3.7 κL meta-priority cycle repair | `pcnr/priority.py` | — |
| §3.5–3.6 arguments, Eq. 4 defeats, grounded extension | `pcnr/reasoner.py` | ✓ |
| §3.9 proof graphs | `pcnr/proof.py` | — |
| Eq. 9 Check = RootOK ∧ SourceOK ∧ EdgeOK ∧ CompleteOK | `pcnr/check.py` | — |
| Algorithm 1 orchestration, decision function (Eq. 1) | `pcnr/pipeline.py` | ✓ (label only) |

## Usage

```bash
PY=../neuro_symbolic/.venv/bin/python      # any Python ≥ 3.10 works; jsonschema is only used by one test

$PY -m pcnr run theory.json [--absence-policy negative] [--deontic-duality] \
                            [--proof-out proof.json] [--dot proof.dot] [--full]
$PY -m pcnr check proof.json theory.json
$PY -m unittest discover -s tests          # 39 tests, incl. 3,000-theory differential test
$PY scripts/run_released.py                # all 1,040 released records
```

```python
from pcnr import run, Config
res = run(extractor_json, query="EnforceablePenalty(case)", source_units=units,
          config=Config(absence_policy="negative"), repair_hook=my_llm_repair)
res.label, res.review_flag, res.proof, res.check
```

Accepted inputs: released records, the authors' fixtures, and extractor output conforming to
`schemas/extractor_output.schema.json`. Source pointers are source-unit ids, optionally with a span:
`"SU1"` or `"SU1#12-80"`.

## Results (2026-11-25)

| Check | Result |
|---|---|
| Authors' 3 fixtures | 3/3 labels; both acceptance proofs pass Check |
| 1,040 released records, Eq. 1 literal | **1,040/1,040 identical to the reference solver** |
| 1,040 released records, `absence_policy="negative"` | **1,040/1,040 = released gold labels** |
| Proofs on released records | 877/877 checkable proofs pass Check (acceptance + absence) |
| Random differential test (seed 42) | 2,862 theories, 0 label mismatches vs reference; 1,068 proofs pass Check |
| Checker tamper tests | hidden attacker, forged rank, wrong label, altered rule, fake defeat, reversed priority, unconstructible argument: all rejected |
| GPT-4o extractions (`data/option_a/runs/gpt4o`) | 8/8 released labels; all determinate proofs pass Check with fact provenance required |

The 163 released "Undetermined" records each get the review flag `INCONSISTENT_EXTENSION`.
Their theories omit the r1⋈r2 / r1⋈r4 conflicts, so both `EnforceablePenalty` and its negation
are accepted.

## Semantic decisions

The paper is silent or ambiguous on each point below. Each choice is a `Config` switch or is
documented in code.

1. **Eq. 1 when no argument exists.** Default `absence_policy="undetermined"` follows Eq. 1
   literally. `"negative"` (closed world) reproduces all released gold labels. The resulting proof
   is an *absence certificate*: Check re-enumerates the arguments and confirms none exists for q or ¬q.
2. **Both q and ¬q accepted.** This is possible only when opposed rules lack a declared conflict.
   The result is Undetermined plus the review flag `INCONSISTENT_EXTENSION`. The reference code
   returns Undetermined silently.
3. **Undercut is one-directional.** A defeater attacks every argument that uses its target rule.
   The target does not attack the defeater back (this matches the reference code and ASPIC+). Eq. 4's
   priority filter still applies, so if target ≻ defeater, the undercut fails.
4. **Strict rules.** A defeasible rule or defeater cannot attack an argument at a strict top rule;
   it must attack a defeasible subargument (ASPIC+-style; the paper does not say). Two strict rules
   in conflict raise a `STRICT_CONFLICT` error.
5. **Self-defeat.** Eq. 4 allows (A, A). The reference code skips self-attacks, and the
   differential test skips these theories (138/3,000).
6. **Deontic.** Opposition is never inferred by the reasoner: conflicts must be declared, as in
   the paper. The validator rejects declared conflicts between non-opposed heads. Opposed pairs
   are p/¬p, Obl(a)/Forb(a), and Perm(a)/Forb(a). `deontic_duality=True` lets Obl(¬a) or Perm(¬a)
   refute Obl(a), and lets Obl(a) establish Perm(a). It is off by default ("no unrestricted modal closure").
7. **κL.** Authority comes from `meta.authority` (larger is stronger). Specificity is strict
   inclusion of ground antecedent sets, computed rather than read from meta. effectiveDate applies
   only if `settings.temporal_precedence_authorized`. Directness is primary > cross_reference > inferred.
   Comparison stops at the first component that is not a tie. Information missing on one side, or
   incomparable scopes, leave the cycle unresolved, which removes its edges and escalates the case.
   `cycle_policy="abstain_query"` (the default) forces Undetermined if the cycle touches the query.
8. **Validation failures.** Schema, symbol and graph errors quarantine the object; one bounded
   `repair_hook` pass follows. With `on_invalid="abstain"` (the default), any remaining blocking
   error gives Undetermined plus a review flag. Provenance errors are non-blocking: the object is
   used, the case is flagged, and SourceOK fails, matching the paper's citation stress test.
9. **Proof completeness.** For every certified argument (roots, subarguments, defenders), the proof
   lists *all* defeaters (CompleteOK). It also shows counterarguments overridden by a priority as
   `attack_blocked` edges. Grounded membership is certified by ranks (the Γ-iteration index).
   The checker never trusts the reasoner's extension: it re-enumerates arguments and recomputes
   defeats itself.

## Not yet covered

- The LLM side of repair: `repair_hook` is the interface, and step 3 will plug the OpenAI call in.
- Preferred or other semantics (the paper uses grounded only).
- Scalability tuning beyond indexing. The checker's defeat computation is O(n²) and fine for proof-sized theories.
