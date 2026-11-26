# Replication notes: Ulizko et al. 2026 (BDCC 10:295)

Upstream code: `../neuro_symbolic` (github.com/thecontrey/neuro_symbolic, pinned to commit `3951065`).
Venv: `../neuro_symbolic/.venv` (Python 3.14, packages from `requirements.txt`).

## What was run (2026-11-25)

| Step | Command (run from `project/`) | Result |
|---|---|---|
| Fixtures | `neuro_symbolic/.venv/bin/python neuro_symbolic/baselines/symbolic_reasoner_reference.py neuro_symbolic/fixtures/formal_reasoning/<f>.json` | Positive / Positive / Undetermined, all as expected |
| Unit tests | `cd neuro_symbolic && .venv/bin/python -m unittest discover -s tests -v` | 2/2 pass |
| Record validation | `cd neuro_symbolic && .venv/bin/python evaluation/validate_jsonl.py` | 1040 records, 0 errors |
| Paper metrics (from saved predictions) | `evaluation/recompute_metrics.py`, `evaluation/reproduce_statistics.py` | All Tables 8, 11, 13, 14, 15, 19 numbers match |
| **Solver re-run on gold theories** | `neuro_symbolic/.venv/bin/python replication/run_solver_on_records.py [--closed-world]` | see below |

## Finding: unstated closed-world default

The reference solver follows Eq. (1) literally: Negative only if ¬q is accepted, otherwise Undetermined.
On the released gold theories:

| Dataset | Literal Eq. (1) | With `--closed-world` |
|---|---|---|
| controlled_600 | 547/600 (91.2%) | **600/600** |
| llm_to_logic_240 (gold theories) | 218/240 | **240/240** |
| adaptation_200 (gold theories) | 176/200 | **200/200** |

All misses are Tier-0 "absence of triggering fact" cases: only `ValidContract` holds, no rule fires, and gold = Negative.
The authors' evaluated engine therefore treats "no argument for q or ¬q" as Negative and reserves
Undetermined for unresolved conflicts. The paper does not state this convention. `closed_world_label()` in the harness implements it.

With that default, the solver disagrees with the authors' stored controlled-benchmark predictions on exactly 4 cases:
CTRL_T1_088, CTRL_T2_070, CTRL_T2_142, CTRL_T3_076. These are the four implementation defects audited in the paper's Table 10.
So the reference solver reproduces the corrected engine behaviour, and the reported 99.3% is the buggy pre-correction snapshot.

On llm_to_logic and adaptation, the stored predictions come from GPT-4o-extracted theories, which are not released.
Agreement with them is 208/240 = 86.7% and 170/200 = 85.0%, which equal the paper's downstream accuracies.

## What the reference solver does NOT include (relevant for the full pipeline)

- No validator (schema, symbol table, provenance, graph stages) and no repair loop.
- No κL meta-priority cycle repair (Sec. 3.7).
- No proof-graph generator and no `Check` (RootOK/SourceOK/EdgeOK/CompleteOK, Eq. 9).
- No deontic semantics: `Obl`/`Perm`/`Forb` are opaque strings, and all conflicts must be declared explicitly.
- Literal complement uses the `¬` prefix only.

## Constraint for the LLM stage

The released `source_units[].text` fields are placeholders ("ANONYMIZED SOURCE UNIT…").
No natural-language evidence exists to extract from, so the paper's LLM experiment cannot be replayed on their data.
Options: (a) generate natural-language clause text from the gold theories, then extract back and compare against gold;
(b) build new scenarios from raw ContractNLI/CUAD/MAUD text.

## Step 2 (2026-11-25): full symbolic layer

See `../solver/README.md`. `pcnr` adds the validator, κL priority repair, proof graphs and the
independent Check to the reference semantics. It matches the reference solver on all 1,040 released
records and on 2,862 random theories, and all 877 released-record proofs pass Check.

## Step 3 (2026-11-25): benchmark + LLM extraction

See `../bench/README.md`. The benchmark has 240 scenarios (120 distinct theories × 2 paraphrases, 4 domains, 2 of them deontic),
with exact gold computed by pcnr. The scorer covers FEER/REER/PEER/CEER, citations, downstream accuracy and proof validity, and
pcnr's repair hook is wired to OpenAI. GPT-4o pilot on 24 scenarios: 62.5% → 87.5% downstream accuracy with validation-triggered repair.

## Step 4 (2026-11-25): full evaluation with gpt-6-luna

See `../bench/README.md`. All LLM calls use gpt-6-luna; the paper's Table 5 decoding is unsupported by this model.
On 240 scenarios the proposed pipeline reaches 97.9% accuracy, against 78.8% for direct gpt-6-luna, 87.1% for PDL and 84.2% for
priority without nested defeats. All three paired differences are significant after Holm correction. The stress tests reproduce the paper's pattern:
citation errors hurt auditability, not answers. With the paper's text-defined κL specificity, the cyclic-priority repair
cannot resolve exception cycles.
