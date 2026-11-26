"""Closed-loop extraction on synthetic clauses. See README.md."""
import argparse
import csv
import hashlib
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "upstream"))
from symbolic_reasoner_reference import complement, construct_arguments, reason

SCHEMA = json.loads((ROOT / "upstream/extractor_output.schema.json").read_text())
PROMPT = (ROOT / "upstream/schema_constrained_extraction_prompt.md").read_text()
PREDICATES = ["ValidContract", "Delay", "ForceMajeure", "GrossNegligence", "NoticeMissing", "NoticeWaived"]
FACTS = [
    "The parties entered into a valid contract.",
    "The supplier delivered after the agreed deadline.",
    "A force majeure event occurred.",
    "The supplier acted with gross negligence.",
    "The required notice was missing.",
    "The customer waived the notice requirement.",
]
CONDITIONS = dict(zip(PREDICATES, ["the contract is valid", "delivery is late", "force majeure occurs",
                                    "the supplier acts with gross negligence", "the required notice is missing",
                                    "the customer waives the notice requirement"]))
QUERY = "EnforceablePenalty(case)"
GUIDANCE = """Use the vocabulary ValidContract, Delay, ForceMajeure, GrossNegligence, NoticeMissing, NoticeWaived, EnforceablePenalty, each with argument (case). Prefix an explicitly negative conclusion with ¬. List only established facts. Use the printed clause numbers as rule IDs (1 becomes r1). Cite the printed source IDs. The register enumerates the agreed conflicts and priorities. List only the registered pairs. Each clause states a defeasible rule. Return all clauses including inactive ones. source_pointers lists the cited IDs. Do not answer the query."""

def load_lines(name):
    return [json.loads(s) for s in (ROOT / name).read_text().splitlines()]

def dump_lines(name, rows):
    (ROOT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))

def core(t):
    facts = sorted(k for k, v in t["facts"].items() if v)
    rules = sorted((x["id"], x["type"], sorted(x["antecedents"]), x["consequent"], x.get("target")) for x in t["rules"])
    conflicts = sorted({tuple(sorted((x["left"], x["right"]))) for x in t["conflicts"]})
    priorities = sorted({(x["stronger"], x["weaker"]) for x in t["priorities"]})
    return dict(facts=facts, rules=rules, conflicts=conflicts, priorities=priorities)

def labels(t):
    result = reason(t)
    args, _ = construct_arguments(t)
    has_argument = any(a.conclusion in (QUERY, complement(QUERY)) for a in args)
    return result["label"], result["label"] if has_argument else "Negative"

def render(t):
    c = core(t)
    units = {"F": "Case file. " + " ".join(text for p, text in zip(PREDICATES, FACTS) if p+"(case)" in c["facts"])}
    for rid, kind, ants, head, target in c["rules"]:
        assert kind == "defeasible" and target is None and head in (QUERY, complement(QUERY))
        cond = " and ".join(CONDITIONS[a.split("(")[0]] for a in ants)
        outcome = "may collect" if head == QUERY else "may not collect"
        units[rid.upper()] = f"Clause {rid[1:]}. If {cond}, the customer {outcome} the contractual delay penalty, subject to the conflict register."
    regs = [f"Clauses {a[1:]} and {b[1:]} conflict." for a,b in c["conflicts"]]
    regs += [f"Clause {a[1:]} takes precedence over clause {b[1:]}." for a,b in c["priorities"]]
    units["P"] = "Conflict register. " + " ".join(regs)
    return {"query":"Can the customer enforce the delay penalty?", "source_units":units}

def build(repo):
    predictions = {}
    for path in (repo/"outputs/predictions").glob("*.csv"):
        for row in csv.DictReader(path.open()):
            for col in ("proposed_grounded_nmr","proposed_llm_to_nmr","proposed"):
                if col in row:
                    predictions[row["case_id"]] = row[col]
    cases, gold, mapping = {}, {}, []
    for path in sorted((repo/"data_anonymized").glob("*/*.jsonl")):
        for line in path.read_text().splitlines():
            r = json.loads(line)
            assert r["target_literal"] == QUERY
            t = {k:r[k] for k in ("facts","rules","conflicts","priorities")}
            t["query"] = QUERY
            key = hashlib.sha256(json.dumps(core(t),sort_keys=True).encode()).hexdigest()[:12]
            case = render(t)
            cases[key] = {"id":key, **case}
            strict, default = labels(t)
            target = {"id":key,"structure":core(t),"reference":strict,"default":default,"released":r["gold_label"]}
            assert gold.setdefault(key,target) == target
            mapping.append(dict(case_id=r["case_id"],dataset=path.parent.name,id=key,
                                released=r["gold_label"],reference=strict,default=default,saved=predictions[r["case_id"]]))
    dump_lines("inputs.jsonl",cases.values())
    dump_lines("gold.jsonl",gold.values())
    with (ROOT/"case_map.csv").open("w") as f:
        w=csv.DictWriter(f,fieldnames=mapping[0]); w.writeheader(); w.writerows(mapping)

def api_key(url):
    """OPENAI_API_KEY from the environment, else from .env next to this script
    (accepts OPENAI_API_KEY or OPEN_AI_KEY). Only ever sent to api.openai.com."""
    if "api.openai.com" not in url:
        return None
    key = os.getenv("OPENAI_API_KEY")
    env = ROOT/".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() in ("OPENAI_API_KEY", "OPEN_AI_KEY") and value.strip():
                key = value.strip().strip('"').strip("'")
    if not key:
        raise SystemExit("No OpenAI key found in environment or .env")
    return key

# Decoding settings from the paper's Table 5 (temperature/top_p 0.01, seed 42, max_tokens 4096),
# plus JSON mode so outputs are bare JSON objects.
DECODING = dict(temperature=0.01, top_p=0.01, seed=42, max_tokens=4096, response_format={"type": "json_object"})

def extract(model, url, out):
    out.mkdir(parents=True, exist_ok=True)
    config = dict(model=model, endpoint=url, decoding=DECODING, prompt_hash=hashlib.sha256((PROMPT+GUIDANCE+json.dumps(SCHEMA)).encode()).hexdigest())
    manifest = out/"config.json"
    if manifest.exists() and json.loads(manifest.read_text()) != config:
        raise SystemExit("Choose a new output directory for this configuration.")
    manifest.write_text(json.dumps(config,indent=2))
    key = api_key(url)
    for case in load_lines("inputs.jsonl"):
        path = out/(case["id"]+".json")
        if path.exists(): continue
        evidence = {k:case[k] for k in ("query","source_units")}
        messages = [{"role":"system","content":PROMPT+"\n"+GUIDANCE+"\n"+json.dumps(SCHEMA)},
                    {"role":"user","content":json.dumps(evidence,ensure_ascii=False)}]
        body = dict(model=model,messages=messages,**DECODING)
        headers = {"Content-Type":"application/json"}
        if key: headers["Authorization"]="Bearer "+key
        request = urllib.request.Request(url.rstrip("/")+"/chat/completions",json.dumps(body).encode(),headers)
        with urllib.request.urlopen(request, timeout=180) as resp:
            response = json.load(resp)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(response,ensure_ascii=False)); tmp.replace(path)

def score(out):
    inputs = {r["id"]:r for r in load_lines("inputs.jsonl")}
    results = {}
    for g in load_lines("gold.jsonl"):
        key = g["id"]
        try:
            response = json.loads((out/(key+".json")).read_text())
            content = response["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):  # tolerate ```json fences
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            x = json.loads(content)
            jsonschema.validate(x,SCHEMA)
            units = inputs[key]["source_units"]
            rules = [r["id"] for r in x["rules"]]
            if len(set(rules)) != len(rules): raise ValueError("duplicate rule IDs")
            for name, fields in [("conflicts",("left","right")),("priorities",("stronger","weaker"))]:
                if any(edge[f] not in rules for edge in x[name] for f in fields): raise ValueError("unknown rule reference")
            if any(r.get("target") not in rules for r in x["rules"] if r["type"]=="defeater"): raise ValueError("unknown defeater target")
            t = dict(x, facts={f["atom"]:True for f in x["facts"]},query=QUERY)
            cites = [v["source"] for name in ("facts","rules","conflicts","priorities") for v in x[name]]
            actual = json.loads(json.dumps(core(t)))
            strict, default = labels(t)
            parts = {name: actual[name]==expected for name,expected in g["structure"].items()}
            results[key] = dict(valid=True,**parts,exact=all(parts.values()),
                                citations_resolve=all(s in units for s in cites+x["source_pointers"]),
                                reference_agrees=strict==g["reference"], reference_vs_released=strict==g["released"],
                                default_vs_released=default==g["released"])
        except (ValueError,KeyError,TypeError,IndexError,OSError,jsonschema.ValidationError) as e:
            results[key] = dict(valid=False,error=str(e)[:200])
    metrics = ["valid","facts","rules","conflicts","priorities","exact","citations_resolve","reference_agrees","reference_vs_released","default_vs_released"]
    summary = {m:sum(bool(r.get(m)) for r in results.values()) for m in metrics}
    print(json.dumps(dict(denominator=len(results),counts=summary,cases=results),indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("build").add_argument("repo",type=Path)
    run=sub.add_parser("extract")
    run.add_argument("--model",required=True); run.add_argument("--url",default="https://api.openai.com/v1"); run.add_argument("--out",type=Path,required=True)
    sub.add_parser("score").add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    if a.command=="build": build(a.repo)
    elif a.command=="extract": extract(a.model,a.url,a.out)
    else: score(a.out)
