# Replicating "Proof-Carrying Neuro-Symbolic Reasoning for Non-Monotonic Legal Decision Support with LLMs"

A replication and extension of Ulizko et al. (2026), *Big Data and Cognitive Computing* 10:295
([doi:10.3390/bdcc10090295](https://doi.org/10.3390/bdcc10090295)).

The method: an LLM turns legal evidence (contract clauses + case facts) into a defeasible/deontic theory, and a
deterministic argumentation solver decides the query (Positive / Negative / Undetermined), returning a proof graph
that an independent checker verifies.

**Start here:** [`info/replication_overview.md`](info/replication_overview.md). It covers what the paper proposes, what the
authors released, what we built, and the key findings.

## Repository layout

| Folder | Contents |
|---|---|
| `neuro_symbolic/` | the authors' repository as a git submodule, pinned to commit `3951065` (CC BY 4.0) |
| `solver/` | **`pcnr`**, our full symbolic layer: deontic literals, 4-stage validator, κL priority-cycle repair, grounded semantics, proof graphs, independent checker, 39 tests |
| `bench/` | synthetic benchmark generator, LLM extraction, scoring, baselines, stress tests, scalability, and run outputs |
| `bench/data/` | the 240-scenario benchmark (`scenarios.jsonl`, `scenarios.csv`) |
| `bench/runs/` | cached LLM responses and results (gpt-6-luna full run, GPT-4o and luna pilots) |
| `replication/` | first-pass checks of the authors' code and released data |
| `data/option_a/` | an earlier 8-case closed-loop extraction package |
| `info/` | summary notes: overview, results, benchmark statistics, deontic extension, priority-cycle problem |

## Setup

```bash
git clone --recursive <this-repo-url>
cd <repo>
python3 -m venv neuro_symbolic/.venv
neuro_symbolic/.venv/bin/pip install -r neuro_symbolic/requirements.txt
```

The solver itself needs only the Python standard library (3.10+). `jsonschema`, `numpy`, `scipy`, `scikit-learn` and
`pandas` are needed for scoring, baselines and statistics.

## Common commands (from the repository root)

```bash
PY=neuro_symbolic/.venv/bin/python

# solver tests (incl. 3,000-theory differential test against the authors' reference solver)
(cd solver && ../$PY -m unittest discover -s tests)

# run the solver on the authors' 1,040 released records
$PY solver/scripts/run_released.py

# regenerate the benchmark
$PY bench/generate.py --theories 120 --variants 2 --seed 42

# re-score the cached gpt-6-luna run and recompute all comparisons (no API calls: responses are cached)
$PY bench/score.py --run bench/runs/luna_full
$PY bench/compare.py --run bench/runs/luna_full
$PY bench/stress.py --run bench/runs/luna_full
$PY bench/scalability.py
```

## LLM calls

All LLM calls use OpenAI `gpt-6-luna`. To make new calls, put your key in the environment (`OPENAI_API_KEY=...`) or in a
`.env` file at the repository root. `.env` files are git-ignored. Cached responses in `bench/runs/` let you re-score
and re-run comparisons without a key.

```bash
$PY bench/extract.py --run bench/runs/<new_run>     # extraction (gpt-6-luna)
```

## Main result (240 synthetic scenarios, gpt-6-luna)

| Method | Accuracy (95% CI) |
|---|---|
| Direct LLM answer | 78.8% [73.1, 83.5] |
| Logistic regression (PDL) | 87.1% [82.2, 90.7] |
| **LLM extraction + `pcnr` solver** | **97.9% [95.2, 99.1]** |

Details, caveats and statistics: [`info/results.md`](info/results.md).
