# Replicating "Proof-Carrying Neuro-Symbolic Reasoning for Non-Monotonic Legal Decision Support with LLMs"

A replication and extension of Ulizko et al. (2026), *Big Data and Cognitive Computing* 10:295
([doi:10.3390/bdcc10090295](https://doi.org/10.3390/bdcc10090295)).

The method: an LLM turns legal evidence (contract clauses + case facts) into a defeasible/deontic theory, and a
deterministic argumentation solver decides the query (Positive / Negative / Undetermined), returning a proof graph
that an independent checker verifies.

## 📄 Reports: see [`info/`](info/)

All write-ups and results are in the [`info/`](info/) directory:

| Report | What it covers |
|---|---|
| [`info/replication_overview.md`](info/replication_overview.md) | **Start here.** What the paper proposes, what the authors released, what we built, key findings |
| [`info/results.md`](info/results.md) | Full results: direct LLM vs logistic regression vs our method, confusion matrices, per-domain and per-tier accuracy, paired statistics, stress tests, scalability, cost |
| [`info/benchmark_statistics.md`](info/benchmark_statistics.md) | The synthetic benchmark: queries, evidence packages, label distribution, phenomena |
| [`info/deontic_extension.md`](info/deontic_extension.md) | How obligations, permissions and prohibitions were added to the authors' solver |
| [`info/priority_cycle_problem.md`](info/priority_cycle_problem.md) | Why the paper's priority-cycle repair cannot break exception cycles |

## Main result (240 synthetic scenarios, gpt-6-luna)

| Method | Accuracy (95% CI) |
|---|---|
| Direct LLM answer | 78.8% [73.1, 83.5] |
| Logistic regression (PDL) | 87.1% [82.2, 90.7] |
| **LLM extraction + `pcnr` solver** | **97.9% [95.2, 99.1]** |

Details, caveats and statistics: [`info/results.md`](info/results.md).

## Repository layout

| Folder | Contents |
|---|---|
| [`info/`](info/) | **reports** (see above) |
| `solver/` | **`pcnr`**, our full symbolic layer: deontic literals, 4-stage validator, κL priority-cycle repair, grounded semantics, proof graphs, independent checker, 39 tests |
| `bench/` | synthetic benchmark generator, LLM extraction, scoring, baselines, stress tests, scalability |
| `bench/data/` | the 240-scenario benchmark (`scenarios.jsonl`, and `scenarios.csv` for spreadsheets) |
| `bench/runs/` | cached LLM responses and results (gpt-6-luna full run; GPT-4o and luna pilots) |
| `neuro_symbolic/` | the authors' repository as a git submodule, pinned to commit `3951065` (CC BY 4.0) |
| `replication/` | first-pass checks of the authors' code and released data |
| `data/option_a/` | an earlier 8-case closed-loop extraction package |

## How to run

All commands are run from the repository root.

### 1. Install (once)

```bash
git clone --recursive https://github.com/skagankose/deontic_language_models.git
cd deontic_language_models
python3 -m venv neuro_symbolic/.venv
neuro_symbolic/.venv/bin/pip install -r neuro_symbolic/requirements.txt
PY=neuro_symbolic/.venv/bin/python
```

`--recursive` fetches the authors' repository into `neuro_symbolic/`. If you cloned without it, run
`git submodule update --init`. Python 3.10+ is required. The solver itself uses only the standard library; the other
packages are needed for scoring, baselines and statistics.

### 2. Check that everything works

```bash
(cd solver && ../$PY -m unittest discover -s tests)
```

This runs 39 tests, including a 3,000-theory differential test against the authors' reference solver. It takes about 5 seconds.

### 3. Run the solver on a single theory

```bash
export PYTHONPATH=solver
$PY -m pcnr run neuro_symbolic/fixtures/formal_reasoning/nested_exception.json \
    --proof-out proof.json --dot proof.dot
$PY -m pcnr check proof.json neuro_symbolic/fixtures/formal_reasoning/nested_exception.json
```

`run` prints the label, the accepted conclusions and the Check result. `--proof-out` / `--dot` save the proof graph
(render the DOT file with Graphviz: `dot -Tpng proof.dot -o proof.png`). Useful options: `--absence-policy negative`
(closed world, "no rule applies" = Negative) and `--deontic-duality`. To write your own theory, copy one of the JSON files
in `neuro_symbolic/fixtures/formal_reasoning/`.

### 4. Reproduce the results without an API key

The LLM responses are cached in `bench/runs/`, so these commands make no API calls:

```bash
$PY solver/scripts/run_released.py               # solver on the authors' 1,040 released records
$PY bench/score.py   --run bench/runs/luna_full  # extraction metrics + our method's accuracy
$PY bench/compare.py --run bench/runs/luna_full  # all baselines + paired statistics
$PY bench/stress.py  --run bench/runs/luna_full  # extraction-error and priority-cycle stress tests
$PY bench/scalability.py                         # solver runtime, 10 to 5,000 rules
```

Outputs go to `bench/runs/luna_full/` (`summary.json`, `comparison.json`, `stress.json`, `scenario_scores.csv`,
`predictions_all_methods.csv`) and `bench/runs/scalability.json`.

### 5. Regenerate the benchmark

```bash
$PY bench/generate.py --theories 120 --variants 2 --seed 42    # writes bench/data/scenarios.jsonl
```

The same seed reproduces the same 240 scenarios.

### 6. New LLM runs (needs an OpenAI key)

All LLM calls use OpenAI `gpt-6-luna`. Provide a key either as an environment variable or in a `.env` file at the
repository root (`.env` files are git-ignored):

```bash
export OPENAI_API_KEY=sk-...          # or: echo "OPENAI_API_KEY=sk-..." > .env
$PY bench/extract.py --run bench/runs/my_run [--limit 24]   # extraction (24 scenarios ≈ a quick pilot)
$PY bench/score.py   --run bench/runs/my_run                # scores it; makes repair calls only if validation fails
$PY bench/compare.py --run bench/runs/my_run                # adds the direct-LLM baseline (240 more calls)
```

Responses are cached per run directory, so re-running a command never repeats a call. A full 240-scenario run uses
about 430k prompt and 465k completion tokens for extraction, and 140k + 70k for the direct baseline.
