# Results on our synthetic benchmark (gpt-6-luna, 2026-11-25)

Benchmark: `bench/data/scenarios.jsonl`. It has 240 scenarios = 120 distinct cases × 2 paraphrases, 4 queries/domains, and gold labels
100 Positive / 100 Negative / 40 Undetermined (see `info/benchmark_statistics.md`). Raw outputs: `bench/runs/luna_full/`.

## Setup: the three main methods

| Method | What it does | Input |
|---|---|---|
| **Direct LLM** | gpt-6-luna reads the evidence and answers Positive / Negative / Undetermined directly, with a rationale and cited unit ids (authors' baseline prompt plus label definitions) | query + evidence text |
| **Logistic regression (PDL)** | multinomial logistic regression on the paper's Table 6 features of the *extracted* theory: which facts hold, number of active rules, conflict and priority indicators, confidence sums supporting each side, number of sources, schema validity. 10-fold stratified out-of-fold predictions, C = 1, lbfgs, max_iter 1000, random_state 42. No attack graph, priorities not applied as hard rules | gpt-6-luna extraction |
| **Our method (LLM-to-NMR)** | gpt-6-luna extracts a formal theory; the `pcnr` solver validates it, repairs priority cycles, computes the grounded extension and decides; every answer comes with a proof graph checked by an independent checker | gpt-6-luna extraction |

All three use the same model, the same 240 evidence packages and the same gold labels. LLM settings: `gpt-6-luna`, JSON mode,
seed 42, default temperature (1; the paper's 0.01 is not supported by this model). Decisions use the closed-world
convention ("no rule applies" = Negative) and the deontic duality in the two deontic domains.

## 1. Main result

| Method | Accuracy (95% Wilson CI) | Macro-F1 | Exception acc. | Nested-exception acc. | Undetermined recall | False-determinate rate | Same answer on both paraphrases |
|---|---|---|---|---|---|---|---|
| Direct LLM (gpt-6-luna) | 78.8% [73.1, 83.5] | 67.8 | 78.8% | 81.2% | 25.0% | 75.0% | 107/120 |
| Logistic regression (PDL) | 87.1% [82.2, 90.7] | 81.0 | 87.8% | 86.7% | 47.5% | 52.5% | 113/120 |
| **Our method** | **97.9% [95.2, 99.1]** | **97.1** | **97.7%** | **96.9%** | **95.0%** | **5.0%** | 115/120 |

Definitions:
- **Exception / nested-exception accuracy:** accuracy on scenarios that contain an exception (222) or an exception to an exception (128).
- **Undetermined recall:** share of the 40 gold-Undetermined scenarios answered Undetermined.
- **False-determinate rate:** share of those 40 answered Positive or Negative instead, i.e. the system should have abstained but didn't.

## 2. Accuracy by gold label

| Method | Positive (n=100) | Negative (n=100) | Undetermined (n=40) |
|---|---|---|---|
| Direct LLM | 85/100 | 94/100 | 10/40 |
| Logistic regression | 96/100 | 94/100 | 19/40 |
| **Our method** | **99/100** | **98/100** | **38/40** |

## 3. Confusion matrices (rows = gold, columns = predicted)

**Direct LLM**

| gold \ predicted | Positive | Negative | Undetermined |
|---|---|---|---|
| Positive | 85 | 5 | 10 |
| Negative | 1 | 94 | 5 |
| Undetermined | 11 | 19 | 10 |

**Logistic regression (PDL)**

| gold \ predicted | Positive | Negative | Undetermined |
|---|---|---|---|
| Positive | 96 | 3 | 1 |
| Negative | 4 | 94 | 2 |
| Undetermined | 10 | 11 | 19 |

**Our method**

| gold \ predicted | Positive | Negative | Undetermined |
|---|---|---|---|
| Positive | 99 | 0 | 1 |
| Negative | 0 | 98 | 2 |
| Undetermined | 0 | 2 | 38 |

Our method never answers Positive when the gold answer is Negative, or vice versa. Its 5 errors are all
"Undetermined vs a determinate answer". The direct LLM's largest error group is abstention cases answered
Positive or Negative (30 of 40).

## 4. Accuracy by domain (query)

| Domain / query | Direct LLM | Logistic regression | Our method |
|---|---|---|---|
| Supply: "Can the customer claim the contractual delay penalty?" | 47/60 | 55/60 | **60/60** |
| Lease: "Is the tenant entitled to a rent reduction?" | 54/60 | 58/60 | **59/60** |
| Data (deontic): "May the provider disclose the customer data …?" | 44/60 | 51/60 | **59/60** |
| Employment (deontic): "Must the employer pay overtime compensation?" | 44/60 | 45/60 | **57/60** |

## 5. Accuracy by reasoning complexity (tier)

Tier 0 = general rules only (n=6); 1 = exceptions (46); 2 = ties, priority cycles or defeaters (60); 3 = nested exceptions (128).

| Method | Tier 0 | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|---|
| Direct LLM | 100.0 | 91.3 | 61.7 | 81.2 |
| Logistic regression | 100.0 | 100.0 | 76.7 | 86.7 |
| **Our method** | 100.0 | 100.0 | **98.3** | **96.9** |

## 6. Paired statistical comparisons

All methods answer the same 240 scenarios, so comparisons are paired:
- exact McNemar test on discordant scenarios;
- Holm correction over the 3 primary comparisons;
- 95% CI from 20,000 paired bootstrap resamples (`random.Random(42)`).

The test code is the authors' own (`neuro_symbolic/evaluation/reproduce_statistics.py`).

| Comparison | Accuracy difference (95% CI) | Ours right & other wrong / other right & ours wrong | McNemar p (raw) | Holm-adjusted p |
|---|---|---|---|---|
| Ours vs direct LLM | **+19.2 pp** [+13.8, +24.6] | 49 / 3 | 1.0e-11 | **3.1e-11** |
| Ours vs logistic regression | **+10.8 pp** [+7.1, +15.0] | 27 / 1 | 2.2e-07 | **2.2e-07** |
| Ours vs priority w/o nested defeats | **+13.8 pp** [+9.6, +18.3] | 33 / 0 | 2.3e-10 | **4.7e-10** |

All three differences are significant. (In the paper, only the difference from the direct LLM was significant.)
Caveat: the two paraphrases of a case are not independent, so these p-values somewhat overstate precision.
A test that resamples whole cases would be more conservative.

## 7. Our method in detail

### Extraction quality (gpt-6-luna, object-level match to gold)

| Object | Correct | Spurious (FP) | Missed (FN) | Other error | Error rate |
|---|---|---|---|---|---|
| Facts | 720 | 5 | 16 | — | FEER **2.8%** |
| Rules | 1,307 | 45 | 8 | 5 wrong type | REER **4.2%** |
| Conflicts | 713 | 33 | 15 | — | CEER **6.3%** |
| Priorities | 714 | 33 | 20 | 0 reversed | PEER **6.9%** |
| Citations | 3,432 of 3,459 correct | | | | **99.2%** |

| Further metric | Value |
|---|---|
| Schema-valid outputs | 100% (240/240) |
| Theories extracted exactly right | 75.0% (180/240) |
| Proof coverage (answers with a checkable proof) | 82.9%; the remaining 17.1% are Undetermined answers, which carry an abstention summary instead |
| Proof validity (proofs passing the independent checker) | **100%** |
| Validation-triggered repair | never triggered (no output failed validation) |

### Errors of our method (5 of 240)

| Scenario | Gold | Predicted |
|---|---|---|
| T0010-v1 | Undetermined | Negative |
| T0023-v2 | Positive | Undetermined |
| T0072-v1 | Negative | Undetermined |
| T0080-v1 | Undetermined | Negative |
| T0092-v2 | Negative | Undetermined |

## 8. Other baselines (for completeness)

| Method | Accuracy (95% CI) | Macro-F1 | Undetermined recall | Same answer on both paraphrases |
|---|---|---|---|---|
| Lexical top-rule (on extracted theory) | 77.5% [71.8, 82.3] | 56.3 | 0.0% | 110/120 |
| Monotonic forward chaining (extracted) | 68.3% [62.2, 73.9] | 65.7 | 65.0% | 118/120 |
| Priority without nested defeats (extracted) | 84.2% [79.0, 88.2] | 72.9 | 27.5% | 116/120 |

On the **gold** theories (perfect extraction, symbolic layer only): lexical 77.1%, monotonic 66.7%, priority without
nested defeats 85.0%, and our solver 100% (gold labels are defined by it).

## 9. Robustness

### Extraction errors injected into the gpt-6-luna theories (seed 42, one run per condition)

| Injected error | Rate per object | Objects perturbed | Accuracy | Proof validity |
|---|---|---|---|---|
| none | — | 0 | 97.9% | 100% |
| fact omission | 5% | 43 | 92.5% | 100% |
| fact hallucination | 5% | 32 | 96.7% | 100% |
| rule-type error | 5% | 60 | 94.2% | 100% |
| priority-direction error | 5% | 41 | 93.8% | 100% |
| citation mismatch | 5% | 162 | 97.9% | **84.9%** |
| mixed | 10% | 136 | 94.2% | 91.4% |

Wrong citations leave answers intact but break auditability. Hallucinated facts lower accuracy while proofs still pass
the checker: no step checks that a cited clause actually supports the extracted object.

### Injected priority cycles (25 / 50 / 100 gold theories)

| Specificity used by the repair | Auto-repaired | Review flags | False determinate | Accuracy after |
|---|---|---|---|---|
| condition inclusion (paper's text) | 0% | 60–67% | 0% | 34–40% |
| numeric depth (as in the released data; circular here) | 100% | 0% | 0% | 100% |

See `info/priority_cycle_problem.md`.

### Scalability of the solver (Apple Silicon, Python 3.14; medians of 31 runs)

| Rules | Facts | Arguments | Defeat edges | Build ms | Grounded extension ms | Total ms | Peak MB |
|---|---|---|---|---|---|---|---|
| 10 | 20 | 10 | 6 | 0.1 | 0.0 | 0.1 | 0.0 |
| 50 | 100 | 50 | 36 | 0.4 | 0.1 | 0.5 | 0.2 |
| 200 | 400 | 200 | 215 | 1.8 | 0.2 | 2.0 | 0.9 |
| 1,000 | 2,000 | 1,000 | 2,025 | 10.2 | 1.3 | 11.6 | 5.2 |
| 5,000 | 10,000 | 5,000 | 14,201 | 62.1 | 7.5 | 70.1 | 26.2 |

## 10. Cost (gpt-6-luna tokens)

| Step | Calls | Prompt tokens | Completion tokens (of which reasoning) |
|---|---|---|---|
| Extraction (our method and the PDL features) | 240 | 427,833 | 464,525 (268,246) |
| Direct LLM baseline | 240 | 141,753 | 67,878 (48,207) |
| Repair | 0 | 0 | 0 |

## 11. Caveats

- **Synthetic benchmark.** Templated prose and no expert review, so results on real contracts are likely lower.
- **Model settings.** One model, one call per scenario, at temperature 1 (the paper's low-temperature setting is unsupported). gpt-6-luna has no dated snapshot.
- **Paired tests.** The paraphrase pairs are not independent, so the p-values overstate precision somewhat.
- **Direct-LLM labels.** The direct LLM was told the label definitions, including closed-world Negative, so its errors are not caused by not knowing the conventions.
- **Earlier GPT-4o pilot (24 scenarios, earlier benchmark wording).** 62.5% without repair and 87.5% with repair, for reference only.
