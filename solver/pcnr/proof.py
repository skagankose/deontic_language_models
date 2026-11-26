"""Proof-carrying explanations Π(c, T) (paper §3.9, Table 4).

For an accepted conclusion c the generator starts from every accepted root argument, adds all
subarguments, every direct attacker (defeater) of every displayed accepted argument, and the
accepted defenders that defeat those attackers (whose own subarguments and attackers are then
displayed too, to a fixed point). Each accepted argument carries its grounded rank, so the
graph is a self-contained certificate of grounded membership: every attacker of a rank-k
argument is defeated by a displayed argument of rank < k.

Output is a superset of the authors' proof_graph.schema.json (case_id, target,
accepted_conclusion, nodes, edges, accepted_arguments, rejected_or_defeated_arguments,
undetermined_flag). No LLM touches the graph.

Proof kinds:
  acceptance  label Positive/Negative backed by an accepted argument (checkable by Check).
              `attackers_listed` declares the certified set: root(s), their subarguments and
              the defenders (closed under subarguments), each with its complete attacker list.
              Attacks on certified arguments that a priority blocked are shown as
              `attack_blocked` edges with the overriding priority.
  absence     label Negative under the closed-world policy: no argument for q or ¬q exists
              (checkable: the checker re-enumerates arguments)
  abstention  label Undetermined: arguments for both sides with their statuses (diagnostic)
"""

from __future__ import annotations

import json
from collections import defaultdict

from . import literals as L
from .reasoner import Argument, Defeat
from .theory import Theory


def _arg_node(a: Argument, status: str, rank: int | None, role: str) -> dict:
    return {"id": a.id, "type": "argument", "conclusion": a.conclusion, "top": a.top,
            "signature": a.signature, "status": status, "rank": rank, "role": role}


def _rule_node(theory: Theory, rid: str) -> dict:
    r = theory.rules[rid]
    return {"id": f"R:{rid}", "type": "rule", "label": rid, "rule_schema": r.schema_id, "rule_type": r.type,
            "antecedents": list(r.antecedents), "consequent": r.out, "source": r.source,
            "confidence": r.confidence}


def build_proof(theory: Theory, args: list[Argument], defeats: list[Defeat], rank: dict[str, int],
                status: dict[str, str], *, label: str, conclusion: str | None,
                kind: str, priorities: set[tuple[str, str]], repair_log: list[dict],
                blocked: list[Defeat] = ()) -> dict:
    by_id = {a.id: a for a in args}
    attackers_of: dict[str, list[Defeat]] = defaultdict(list)
    for d in defeats:
        attackers_of[d.target].append(d)
    blocked_on: dict[str, list[Defeat]] = defaultdict(list)
    for d in blocked:
        blocked_on[d.target].append(d)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_keys: set = set()
    attackers_listed: dict[str, list[str]] = {}

    def edge(e: dict):
        key = json.dumps(e, sort_keys=True)
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append(e)

    def show_structure(a: Argument, role: str):
        """Display argument a with its whole support tree."""
        if a.id in nodes:
            return
        nodes[a.id] = _arg_node(a, status[a.id], rank.get(a.id), role)
        if a.is_fact:
            fid = f"F:{a.conclusion}"
            f = theory.facts[a.conclusion]
            nodes.setdefault(fid, {"id": fid, "type": "fact", "label": a.conclusion, "value": True,
                                   "source": f.source, "confidence": f.confidence})
            edge({"source": fid, "target": a.id, "type": "premise"})
            return
        nodes.setdefault(f"R:{a.top}", _rule_node(theory, a.top))
        edge({"source": f"R:{a.top}", "target": a.id, "type": "applies"})
        for i, cid in enumerate(a.children):
            show_structure(by_id[cid], "support" if role != "attacker" else "attacker-support")
            edge({"source": cid, "target": a.id, "type": "support", "antecedent_index": i})

    def priority_edge(pair):
        s, w = pair
        nodes.setdefault(f"R:{s}", _rule_node(theory, s))
        nodes.setdefault(f"R:{w}", _rule_node(theory, w))
        info = theory.priority_info.get(pair)
        idx = next((i for i, e in enumerate(repair_log) if e.get("oriented") == [s, w]), None)
        if idx is not None:          # edge lay on a priority cycle: the repair decision justifies it
            prov = {"repair_log": idx, "action": repair_log[idx]["action"]}
        else:
            prov = {"priority_source": info.source if info else None, "basis": info.basis if info else None}
        edge({"source": f"R:{s}", "target": f"R:{w}", "type": "priority", **prov})

    def conflict_info(d: Defeat) -> dict:
        ra, rb = by_id[d.attacker].top, by_id[d.via].top
        c = theory.conflict_info.get(frozenset((ra, rb)))
        return {"conflict_source": c.source if c else theory.rules[ra].source}

    if kind == "acceptance":
        roots = sorted((a for a in args if a.conclusion == conclusion and status[a.id] == "IN"),
                       key=lambda a: (rank[a.id], a.id))
        frontier = list(roots)
        for r in roots:
            show_structure(r, "root")
        done: set[str] = set()
        while frontier:
            a = frontier.pop()
            if a.id in done:
                continue
            done.add(a.id)
            for sub_id in a.subs:          # all displayed accepted subarguments
                if sub_id != a.id:
                    frontier.append(by_id[sub_id])
            listed = []
            for d in attackers_of.get(a.id, ()):
                b = by_id[d.attacker]
                show_structure(b, "attacker")
                edge({"source": b.id, "target": a.id, "type": "defeat", "via": d.via,
                      "kind": d.kind, **conflict_info(d)})
                if d.priority:
                    priority_edge(d.priority)
                listed.append(b.id)
                # accepted defenders of a against b
                for dd in attackers_of.get(b.id, ()):
                    c = by_id[dd.attacker]
                    if status[c.id] == "IN":
                        show_structure(c, "defender")
                        edge({"source": c.id, "target": b.id, "type": "defeat", "via": dd.via,
                              "kind": dd.kind, **conflict_info(dd)})
                        if dd.priority:
                            priority_edge(dd.priority)
                        frontier.append(c)
            attackers_listed[a.id] = sorted(set(listed), key=lambda x: int(x[1:]))
            # counterarguments considered but overridden by a priority of the attacked rule
            for d in blocked_on.get(a.id, ()):
                b = by_id[d.attacker]
                show_structure(b, "overridden")
                edge({"source": b.id, "target": a.id, "type": "attack_blocked", "via": d.via,
                      "kind": d.kind, "blocked_by": [f"R:{d.priority[0]}", f"R:{d.priority[1]}"],
                      **conflict_info(d)})
                priority_edge(d.priority)
        cnode = "C"
        nodes[cnode] = {"id": cnode, "type": "conclusion", "label": conclusion}
        for r in roots:
            edge({"source": r.id, "target": cnode, "type": "concludes"})
        for (s, w) in sorted(priorities):
            if f"R:{s}" in nodes and f"R:{w}" in nodes:
                priority_edge((s, w))

    elif kind == "absence":
        q = theory.query
        cands = [r for r in theory.rules.values() if r.type != "defeater" and r.consequent in (q, L.complement(q))]
        for r in sorted(cands, key=lambda r: r.id):
            nodes[f"R:{r.id}"] = {**_rule_node(theory, r.id),
                                  "unmet_antecedents": [x for x in r.antecedents
                                                        if not any(a.conclusion == x for a in args)]}

    else:  # abstention
        q = theory.query
        for a in args:
            if a.conclusion in (q, L.complement(q)):
                show_structure(a, "contested")
                for d in attackers_of.get(a.id, ()):
                    show_structure(by_id[d.attacker], "attacker")
                    edge({"source": d.attacker, "target": a.id, "type": "defeat", "via": d.via,
                          "kind": d.kind, **conflict_info(d)})

    arg_nodes = [n for n in nodes.values() if n["type"] == "argument"]
    return {
        "case_id": theory.case_id or "",
        "target": theory.query,
        "accepted_conclusion": label,
        "kind": kind,
        "conclusion": conclusion,
        "nodes": list(nodes.values()),
        "edges": edges,
        "accepted_arguments": sorted((n["id"] for n in arg_nodes if n["status"] == "IN"), key=lambda x: int(x[1:])),
        "rejected_or_defeated_arguments": sorted((n["id"] for n in arg_nodes if n["status"] != "IN"), key=lambda x: int(x[1:])),
        "undetermined_flag": label == "Undetermined",
        "attackers_listed": attackers_listed,
        "repair_log": repair_log,
    }


def to_dot(proof: dict) -> str:
    """Render a proof graph as Graphviz DOT."""
    lines = ["digraph Proof {", "  rankdir=BT;", '  node [fontname="Helvetica", fontsize=10];']
    for n in proof["nodes"]:
        nid = n["id"].replace('"', "'")
        if n["type"] == "fact":
            lines.append(f'  "{nid}" [shape=note, label="{L.pretty(n["label"])}"];')
        elif n["type"] == "rule":
            lines.append(f'  "{nid}" [shape=box, style=rounded, label="{n["label"]} ({n["rule_type"]})\\n'
                         f'{", ".join(n["antecedents"])} ⇒ {n["consequent"]}"];')
        elif n["type"] == "argument":
            style = {"IN": 'style="filled", fillcolor="#d8f0d8"', "OUT": 'style="dashed"',
                     "UNDEC": 'style="dotted"'}[n["status"]]
            rank = f' r{n["rank"]}' if n.get("rank") else ""
            concl = n["conclusion"] if n["conclusion"].startswith("block(") else L.pretty(n["conclusion"])
            lines.append(f'  "{nid}" [shape=ellipse, {style}, label="{nid}{rank}\\n{concl}"];')
        else:
            lines.append(f'  "{nid}" [shape=doubleoctagon, label="{L.pretty(n["label"])}"];')
    styles = {"premise": "color=gray", "applies": "color=gray, style=dotted",
              "support": "color=black", "defeat": 'color=red, label="defeats"',
              "attack_blocked": 'color=orange, style=dashed, label="attack blocked"',
              "priority": 'color=blue, style=dashed, label="≻"', "concludes": "color=darkgreen"}
    for e in proof["edges"]:
        lines.append(f'  "{e["source"]}" -> "{e["target"]}" [{styles.get(e["type"], "")}];')
    lines.append("}")
    return "\n".join(lines)
