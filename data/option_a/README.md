# Synthetic clause-to-theory experiment

Eight evidence packages cover all 1,040 public structural records from Ulizko et al., *Proof-Carrying Neuro-Symbolic Reasoning for Non-Monotonic Legal Decision Support with LLMs* (doi:10.3390/bdcc10090295). Upstream repository: https://github.com/thecontrey/neuro_symbolic at 3951065e05836c01ab44b86fe5b23aa9d26a883d. The upstream folder contains the authors' unchanged reference solver, extraction prompt, schema, citation and license (CC BY 4.0). The English prose, adaptation instructions and evaluation harness were created for this experiment.

## Run

Use Python 3.10+ and a chat-completions-compatible endpoint. From the unpacked option_a directory:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install 'jsonschema>=4.20'
    python experiment.py extract --model gpt-4o --out runs/gpt4o
    python experiment.py score --out runs/gpt4o

The default endpoint uses OPENAI_API_KEY from your environment. To use a locally served model, set --url http://localhost:8000/v1 and --model to its served name. Clear OPENAI_API_KEY before using a local or other third-party endpoint. Pick a dated model identifier for a version-specific experiment. Each run makes eight calls and stores raw responses. Running the same command resumes after a connection failure. Malformed model outputs count as failures. Use a fresh output directory for another attempt. No provider responses are bundled.

## What the extractor sees

inputs.jsonl contains the synthetic English case file, numbered clauses, a register of conflicts and priorities, and a query. The runner sends only these texts, the authors' prompt and schema, and instructions fixing notation and scope. No case IDs, answers, formal theories, example outputs or original legal text are sent. A fixed vocabulary and numbered clauses let us score structures without confusing renaming with logical errors. The texts explicitly state both conflict and priority when the released structure specifies them. Inactive facts are omitted and inactive clauses remain visible. Rule language is intentionally controlled. This is an easier task than extraction from authentic contracts.

## Read the scores

gold.jsonl holds the answer key. case_map.csv maps every released case ID to its evidence package, authors' saved prediction, released label and both computed labels. Score counts use eight unique inputs; per-record summaries would overweight repeated structures. No train/test split is needed for this fixed zero-shot pilot. Keep all renderings of the same theory together if you subsequently introduce prompt tuning or held-out evaluation.

- exact requires all four components to match: established facts, clause-ID-aligned rule bodies/heads/types/targets, undirected conflicts and directed priorities. Antecedent order has no significance. Confidence and source metadata are excluded from structural exactness.
- citations_resolve checks that the cited synthetic IDs exist. It does not verify that a cited passage supports the individual claim.
- reference_agrees compares reference-solver outputs on extracted and gold theories. This isolates extraction-induced changes to the decision.
- reference_vs_released compares the extracted theory's reference-solver result to the released expert label.
- default_vs_released applies the explicit experimental convention that an absence of any constructed argument for either query polarity gives Negative. The original solver is unchanged.
- Missing responses or invalid outputs receive zero credit in the denominator. Score only untrusted model outputs from your own controlled calls; this runner assumes small theories.

Perfect structural recovery yields 8/8 reference agreement and 7/8 expert-label accuracy under unchanged solver semantics. Under the optional no-argument convention it yields 8/8 on the released labels. Label accuracy alone cannot establish that the model recovered every rule.

## Rebuild from upstream

    git clone https://github.com/thecontrey/neuro_symbolic
    git -C neuro_symbolic checkout 3951065e05836c01ab44b86fe5b23aa9d26a883d
    python experiment.py build neuro_symbolic

The eight deduplicated inputs share the same target and five defeasible rules, with two conflict/priority configurations. All 99 no-argument records share one structure. On all 163 tied-label records the reference solver actually accepts both polarities with zero defeat edges. Its decision function returns Undetermined when both polarities or neither polarity are accepted. The symmetric-conflict unit fixture has mutual defeat instead. These data do not independently test distinct deontic operators, strict rules or targeted defeaters.
