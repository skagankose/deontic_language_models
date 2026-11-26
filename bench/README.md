# bench: synthetic contract benchmark + LLM-to-logic evaluation

This is step 3 of the replication of Ulizko et al. (2026). The authors' released data are 8 distinct
logic problems with placeholder text, so their LLM experiment cannot be replayed. This folder
generates a varied benchmark with exact answer keys. It also runs schema-constrained LLM extraction
and scores it with the paper's metrics, using the `pcnr` solver (../solver) for gold labels,
downstream decisions, repair and proof checking.

## Files

| File | Purpose |
|---|---|
| `domains.py` | 4 domains: supply (penalty), lease (rent reduction), data (Perm/Forb disclosure, deontic), employment (Obl/Perm¬ overtime, deontic). Each has 11 conditions with a lean (supports or defeats the issue), 2 defined concepts and a distractor issue |
| `generate.py` | random theory structures, rendered as contract prose with gold theory and labels |
| `prompts/extraction_guidance.md` | representation conventions appended to the authors' extraction prompt |
| `llm.py` | stdlib OpenAI client with on-disk cache and retries (key from `$OPENAI_API_KEY` or `data/option_a/.env`). **All LLM calls use `gpt-6-luna`** |
| `extract.py` | extraction runner, whole-output re-extraction, and the LLM `repair_hook` for pcnr |
| `score.py` | object alignment, FEER/REER/PEER/CEER, citations, downstream accuracy with and without repair |
| `baselines.py` | Table 7 baselines: direct LLM, lexical top-rule, monotonic, priority without nested defeats, PDL |
| `compare.py` | all-method comparison + paired statistics (authors' McNemar/Holm/bootstrap code) |
| `stress.py` | extraction-error stress test (Table 16) and cyclic-priority test (Table 18) |
| `scalability.py` | runtime scaling (Table 17) |
| `data/scenarios.jsonl` | 240 scenarios = 120 theories × 2 paraphrases (seed 42) |
| `runs/pilot_gpt4o/` | cached responses, `summary.json`, `scenario_scores.csv` |

## What a theory contains

- **Core rules:** a general rule, an optional second supporting rule, and an exception tree of depth 0–3. Each exception has the opposite head and prevails over its parent.
- **Defeaters:** "Clause k does not apply where …".
- **Ties:** "neither clause prevails", which yields an abstention.
- **Mutual-override cycles:** "notwithstanding" in both directions. Cycles are either resolvable by specificity (κL) or unresolvable (abstention plus review).
- **Defined concepts:** strict ("in all cases") or defeasible ("as a general rule") rules, which create inference chains.
- **Noise:** distractor rules and irrelevant facts.

How a priority is expressed varies by paraphrase, and the text never states it as data: "notwithstanding Clause n", "subject to Clause n", "except as provided in Clause n", an inline "unless …", or a precedence schedule. Paraphrases also change clause order and sentence templates.

Gold labels are computed by pcnr on the gold theory. The closed-world policy is the primary label; the Eq. 1 label is also stored. Deontic domains use D-axiom duality, so an accepted Forb(a) answers "may a?" with Negative. Facts are resampled to balance labels, and samples whose extension is inconsistent are rejected. Every gold theory passes validation, and every gold proof passes Check.

```text
240 scenarios | closed world: 100 Positive / 100 Negative / 40 Undetermined | 120/120 distinct theories
phenomena (per theory): exception 111, nested 64, defeater 36, tie 25, cycle 28 (5 resolvable),
definitions 47 (21 strict), distractor 70, second support 23, irrelevant fact 38
```

## Run

```bash
PY=neuro_symbolic/.venv/bin/python
$PY bench/generate.py --theories 120 --variants 2 --seed 42
$PY bench/extract.py --model gpt-4o-2024-08-06 --run bench/runs/<name> [--limit N]
$PY bench/score.py --run bench/runs/<name> [--no-repair]
```

## Scorer validation (simulated extractors, all 240 scenarios)

| Injected error (one per scenario) | Metric that moves | Downstream accuracy |
|---|---|---|
| none (gold as extraction) | all error rates 0, exact 100%, proof validity 100% | 100% |
| one priority reversed | PEER 33.7 | 82.5% |
| one fact dropped | FEER 31.8 | 66.2% |
| one rule typed strict | REER 18.2 | 92.1% |
| one wrong citation | citation acc. 93.2, **proof validity 68%** | 100% |
| one conflict + its priority dropped | CEER 31.3, PEER 32.7 | 83.8% |

## Pilot: GPT-4o (gpt-4o-2024-08-06), first 24 scenarios (12 theories × 2), 2026-11-25

| | No repair | Validation-triggered repair |
|---|---|---|
| Schema-valid outputs | 75.0% | 87.5% (object-level repair fixes the rest inside pcnr) |
| FEER / REER / PEER / CEER | 0.0 / 28.9 / 42.6 / 36.1 | 0.0 / 26.8 / 39.3 / 32.8 |
| Citation accuracy (matched objects) | 99.6% | 99.6% |
| Exact theory | 16.7% | — |
| Downstream accuracy (95% Wilson) | 62.5% [42.7, 78.8] | **87.5% [69.0, 95.7]** |
| Macro-F1 | 56.5 | 75.4 |
| Proof coverage / validity | 70.8% / 100% | 95.8% / 100% |

- **Repair:** all 6 cases that repair fixed were defeaters written with the issue's negation instead of `block(target)`. The validator caught them, abstained, and one LLM repair pass fixed them (9 repair calls).
- **Errors after repair:** all three remaining mistakes are gold-Undetermined cases answered determinately. A missed half of a mutual-override cycle or a missed tie link removes the conflict that should force abstention. Extraction errors therefore *defeat the safety mechanism* the method relies on.
- **Dominant error types:** missed links (CEER and PEER from "subject to" / "notwithstanding" / schedule references), ordinary clauses typed strict, and an "unless" exception given its parent's conditions.
- **Paraphrase stability:** the same label on 9/12 theory pairs.
- **Cost:** about 54k input and 18k output tokens in total.

These are pilot numbers. With n = 24 the intervals are wide, and nothing here is a statistical claim.

## Limitations

- **Templated prose:** rendering uses fixed templates with plausibility leans, not authentic drafting. Some clauses are still legally odd.
- **Encoding conventions** follow the paper (exception antecedents = own conditions only; conflicts only where the text links rules). The guidance tells the model these conventions. A different but defensible formalization is scored as an error.
- **One constant per domain** (no multi-party grounding), unit-level citations (no character spans), and no retrieval, which matches the paper's fixed evidence packages.


## Model change (2026-11-25): gpt-6-luna only

All LLM calls now use `gpt-6-luna`. It is a reasoning model: it rejects `max_tokens` and `top_p` and accepts only the
default temperature (1), so the paper's near-zero decoding (Table 5) cannot be reproduced. Calls use
`seed=42, max_completion_tokens=32000`, JSON mode. The model has no dated snapshot. The GPT-4o pilot above is
kept for reference only.

After the luna pilot, two benchmark wordings were de-modalised ("the customer **may** claim the penalty" invited `Perm(...)` on
a plain literal; the data distractor said "must"), and the guidance gained one line restricting deontic operators to action
literals. The regenerated benchmark has identical structures and labels (verified 240/240); only the text of 94 scenarios changed.

## Step 4 results: gpt-6-luna, all 240 scenarios (2026-11-25)

### Extraction (proposed pipeline, `runs/luna_full`)

| Metric | Value |
|---|---|
| Schema-valid outputs | 100% |
| Exact theory recovery | 75.0% |
| FEER / REER / PEER / CEER | 2.8 / 4.2 / 6.9 / 6.3 % |
| Citation accuracy | 99.2% |
| Validation-triggered repair | never triggered (no blocking diagnostics) |
| Tokens | 428k prompt, 465k completion (268k reasoning) |

### Gold formalization (symbolic layer only; Table 8/9 analogue)

| Method | Acc | F1 | Tier 0 | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|---|---|---|
| Lexical top-rule | 77.1 | 56.0 | 100 | 97.8 | 60.0 | 76.6 |
| Monotonic forward chaining | 66.7 | 63.8 | 100 | 82.6 | 83.3 | 51.6 |
| Priority without nested defeats | 85.0 | 73.0 | 100 | 100 | 70.0 | 85.9 |
| pcnr grounded (= gold by construction) | 100 | 100 | 100 | 100 | 100 | 100 |

Tiers: 0 = general rules only (n=6), 1 = exceptions (46), 2 = ties/cycles/defeaters (60), 3 = nested exceptions (128).

### Downstream (Table 13 analogue)

| Method | Acc [95% Wilson] | Macro-F1 | Exception | Nested | Undetermined recall | False-determinate | Paraphrase agreement |
|---|---|---|---|---|---|---|---|
| Direct LLM (gpt-6-luna) | 78.8 [73.1, 83.5] | 67.8 | 78.8 | 81.2 | 25.0 | 75.0 | 107/120 |
| PDL logistic regression | 87.1 [82.2, 90.7] | 81.0 | 87.8 | 86.7 | 47.5 | 52.5 | 113/120 |
| Lexical top-rule (extracted) | 77.5 [71.8, 82.3] | 56.3 | 77.5 | 77.3 | 0.0 | 100 | 110/120 |
| Monotonic (extracted) | 68.3 [62.2, 73.9] | 65.7 | 67.6 | 53.9 | 65.0 | 35.0 | 118/120 |
| Priority w/o nested defeats (extracted) | 84.2 [79.0, 88.2] | 72.9 | 84.7 | 85.2 | 27.5 | 72.5 | 116/120 |
| **Proposed LLM-to-NMR** | **97.9 [95.2, 99.1]** | **97.1** | 97.7 | 96.9 | 95.0 | 5.0 | 115/120 |
| Proposed + repair | 97.9 (identical; repair never triggered) | | | | | | |

Proof coverage for the proposed pipeline is 82.9% (the rest are Undetermined answers, which carry abstention summaries); proof validity is 100%.
The direct LLM gives a determinate answer citing no clause that supports that side in 7.9% of scenarios. Its errors
are dominated by abstention cases: 30 of 40 gold-Undetermined scenarios were answered Positive or Negative.

### Paired comparisons (Table 14 analogue; exact McNemar, Holm over the 3 primary, 20k paired bootstrap)

| Comparison | Δ accuracy [95% CI] | Discordant (proposed only / other only) | Holm p |
|---|---|---|---|
| Proposed vs direct LLM | +19.2 pp [+13.8, +24.6] | 49 / 3 | 3.1e-11 |
| Proposed vs PDL | +10.8 pp [+7.1, +15.0] | 27 / 1 | 2.2e-07 |
| Proposed vs priority w/o nested defeats | +13.8 pp [+9.6, +18.3] | 33 / 0 | 4.7e-10 |

Unlike the paper, where only the direct-LLM difference was significant, all three primary differences are significant here.
Paraphrase pairs are not independent (120 theories × 2), so the p-values overstate precision somewhat; theory-clustered tests are a sensible follow-up.

### Stress tests

Extraction errors injected into the luna theories (seed 42, one run per condition):

| Condition | Rate | Objects perturbed | Accuracy | Proof validity |
|---|---|---|---|---|
| none | — | 0 | 97.9 | 100 |
| fact omission | 5% | 43 | 92.5 | 100 |
| fact hallucination | 5% | 32 | 96.7 | 100 |
| rule-type error | 5% | 60 | 94.2 | 100 |
| priority-direction error | 5% | 41 | 93.8 | 100 |
| citation mismatch | 5% | 162 | 97.9 | **84.9** |
| mixed | 10% | 136 | 94.2 | 91.4 |

Cyclic priorities: a reversed priority edge was injected into 25/50/100 gold theories.

| Specificity used by κL | Auto-repaired | Review flags | False determinate | Accuracy after |
|---|---|---|---|---|
| antecedent inclusion (paper §3.7 text) | 0% | 60–67% | 0% | 34–40% |
| numeric depth (released-data `meta.specificity`) | 100% | 0% | 0% | 100% |

Under the paper's own encoding, an exception's antecedents are only its own conditions, so inclusion never orders an
exception against its parent and κL cannot repair those cycles. The numeric variant is circular here, because the depth is derived
from the gold priorities. The paper's 79–88% lies between these regimes and depends on how specificity metadata is assigned.
False-determinate conclusions are 0% in every condition. The remaining unflagged cases are cycles that do not touch the query.

### Scalability (Table 17 analogue; Apple Silicon, Python 3.14, 5 warm-up + 31 measured, medians)

| Rules | Facts | Rule-derived args | Defeat edges | Build AF ms | GE ms | Total ms | Peak MB |
|---|---|---|---|---|---|---|---|
| 10 | 20 | 10 | 6 | 0.1 | 0.0 | 0.1 | 0.0 |
| 50 | 100 | 50 | 36 | 0.4 | 0.1 | 0.5 | 0.2 |
| 200 | 400 | 200 | 215 | 1.8 | 0.2 | 2.0 | 0.9 |
| 1000 | 2000 | 1000 | 2025 | 10.2 | 1.3 | 11.6 | 5.2 |
| 5000 | 10000 | 5000 | 14201 | 62.1 | 7.5 | 70.1 | 26.2 |

These numbers are not comparable to the paper's (1.18M edges and 6.2 s at 5,000 rules), because conflict density differs and their engine is unreleased.
Argument construction dominates, as the paper reports. A first generator version that let rebuttals chain gave literals
several derivations and exceeded 5M arguments at 5,000 rules: full argument enumeration is exponential in alternative derivations.
