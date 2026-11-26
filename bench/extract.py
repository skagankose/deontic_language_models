"""LLM-to-logic extraction over the benchmark, plus the LLM side of validation-triggered repair.

    python bench/extract.py --run bench/runs/luna [--limit 24] [--workers 4]   (model: gpt-6-luna)

Prompt = the authors' schema-constrained extraction prompt + our representation conventions
(bench/prompts/extraction_guidance.md) + the authors' extractor JSON schema. The user message
carries only the query, the query literal and the source units. No gold, no labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
from llm import MODEL, chat, content_json, decoding_for, usage  # noqa: E402

AUTHORS = PROJECT / "neuro_symbolic"
PROMPT = (AUTHORS / "prompts/schema_constrained_extraction_prompt.md").read_text(encoding="utf-8")
SCHEMA = json.loads((AUTHORS / "schemas/extractor_output.schema.json").read_text(encoding="utf-8"))
GUIDANCE = (HERE / "prompts/extraction_guidance.md").read_text(encoding="utf-8")
SYSTEM = f"{PROMPT}\n\n{GUIDANCE}\nJSON schema for your output:\n{json.dumps(SCHEMA)}"
PROMPT_HASH = hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]


def user_message(sc: dict) -> str:
    return json.dumps({"query": sc["query_nl"], "query_literal": sc["target_literal"],
                       "source_units": {u["source_unit_id"]: u["text"] for u in sc["source_units"]}},
                      ensure_ascii=False)


def extract_one(model: str, sc: dict, run_dir: Path) -> dict:
    return chat(model, SYSTEM, user_message(sc), cache_dir=run_dir / "extractions", tag=sc["scenario_id"])


# ---- repair (paper §3.4: one bounded pass) ------------------------------------------------

def reextract(model: str, sc: dict, run_dir: Path, error: str) -> dict:
    """Whole-output repair when the first output is unparseable or schema-invalid."""
    user = user_message(sc) + "\n\nYour previous output was rejected by the validator: " + error[:800] + \
        "\nReturn a corrected JSON object that conforms to the schema."
    return chat(model, SYSTEM, user, cache_dir=run_dir / "repairs", tag=f"{sc['scenario_id']}-whole")


REPAIR_SYSTEM = f"""You repair one object of a formal legal theory that failed validation.
{GUIDANCE}
You receive the failed object, the validator diagnostics, the query, the defined terms, the rule ids
present in the theory and the text of the cited source units. Return a JSON object
{{"action": "replace", "object": <corrected object with the same fields>}} or
{{"action": "delete"}} if the object has no basis in the text."""


def make_repair_hook(model: str, sc: dict, extracted: dict, run_dir: Path, calls: list):
    units = {u["source_unit_id"]: u["text"] for u in sc["source_units"]}
    rule_ids = [r.get("id") for r in extracted.get("rules", []) if isinstance(r, dict)]

    def hook(req):
        hood = dict(req.neighborhood)
        uid = str(req.object.get("source", "")).partition("#")[0]   # the unit the object cites
        if uid in units:
            hood[uid] = units[uid]
        payload = {"object_kind": req.kind, "object": req.object,
                   "diagnostics": [{"code": d.code, "message": d.message} for d in req.diagnostics],
                   "query_literal": req.query, "defined_terms": units.get("D1", ""),
                   "rule_ids": rule_ids, "source_neighborhood": hood}
        resp = chat(model, REPAIR_SYSTEM, json.dumps(payload, ensure_ascii=False),
                    cache_dir=run_dir / "repairs", tag=f"{sc['scenario_id']}-obj")
        calls.append(resp)
        try:
            out = content_json(resp)
        except (ValueError, KeyError, json.JSONDecodeError):
            return "keep"
        if out.get("action") == "delete":
            return None
        obj = out.get("object")
        return obj if isinstance(obj, dict) else "keep"
    return hook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--scenarios", type=Path, default=HERE / "data/scenarios.jsonl")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    scs = [json.loads(x) for x in a.scenarios.read_text(encoding="utf-8").splitlines() if x.strip()]
    if a.limit:
        scs = scs[:a.limit]
    a.run.mkdir(parents=True, exist_ok=True)
    cfg = {"model": a.model, "prompt_hash": PROMPT_HASH, "scenarios": str(a.scenarios),
           "decoding": decoding_for(a.model)}
    cfg_path = a.run / "config.json"
    if cfg_path.exists() and json.loads(cfg_path.read_text())["prompt_hash"] != PROMPT_HASH:
        raise SystemExit("prompt changed since this run was started; use a new --run directory")
    cfg_path.write_text(json.dumps(cfg, indent=2))
    with ThreadPoolExecutor(a.workers) as pool:
        responses = list(pool.map(lambda sc: extract_one(a.model, sc, a.run), scs))
    print(json.dumps({"scenarios": len(scs), **usage(responses),
                      "models": sorted({r.get("model", "?") for r in responses})}, indent=2))


if __name__ == "__main__":
    main()
