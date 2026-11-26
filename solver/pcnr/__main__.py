"""Command line:

    python -m pcnr run THEORY.json [--query LIT] [--absence-policy negative] [--deontic-duality]
                                   [--proof-out proof.json] [--dot proof.dot] [--full]
    python -m pcnr check PROOF.json THEORY.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import check
from .pipeline import Config, run
from .proof import to_dot
from .theory import coerce_raw, ground, normalize
from .validate import validate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pcnr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="reason over a theory file")
    r.add_argument("theory", type=Path)
    r.add_argument("--query")
    r.add_argument("--absence-policy", choices=["undetermined", "negative"], default="undetermined")
    r.add_argument("--cycle-policy", choices=["abstain_query", "drop_edges"], default="abstain_query")
    r.add_argument("--on-invalid", choices=["abstain", "drop"], default="abstain")
    r.add_argument("--deontic-duality", action="store_true")
    r.add_argument("--require-fact-sources", action="store_true")
    r.add_argument("--proof-out", type=Path)
    r.add_argument("--dot", type=Path)
    r.add_argument("--full", action="store_true", help="print the full result including the proof")
    c = sub.add_parser("check", help="independently check a proof against a theory")
    c.add_argument("proof", type=Path)
    c.add_argument("theory", type=Path)
    c.add_argument("--require-fact-sources", action="store_true")
    c.add_argument("--deontic-duality", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "run":
        cfg = Config(absence_policy=a.absence_policy, cycle_policy=a.cycle_policy, on_invalid=a.on_invalid,
                     deontic_duality=a.deontic_duality, require_fact_sources=a.require_fact_sources)
        res = run(json.loads(a.theory.read_text(encoding="utf-8")), query=a.query, config=cfg)
        out = res.to_dict()
        if a.proof_out:
            a.proof_out.write_text(json.dumps(res.proof, indent=2, ensure_ascii=False), encoding="utf-8")
        if a.dot and res.proof:
            a.dot.write_text(to_dot(res.proof), encoding="utf-8")
        if not a.full:
            out = {k: out[k] for k in ("case_id", "query", "label", "reason", "review_flag", "review_reasons",
                                       "accepted", "n_arguments", "n_defeats", "check")}
            out["diagnostics"] = [f"{d['severity']}:{d['code']}:{d['object_id']}" for d in res.diagnostics
                                  if d["severity"] != "info"]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    raw, _ = normalize(coerce_raw(json.loads(a.theory.read_text(encoding="utf-8"))))
    theory = ground(validate(raw).clean_raw)
    res = check(json.loads(a.proof.read_text(encoding="utf-8")), theory,
                require_fact_sources=a.require_fact_sources, deontic_duality=a.deontic_duality)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
