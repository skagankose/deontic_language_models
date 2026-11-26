"""Small executable reference for the released finite formal fixtures.

This module implements argument construction, explicit conflict/priority filtering,
targeted defeaters, and grounded acceptance. It is intentionally compact and is not
the production implementation used for the manuscript runtime measurements.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Argument:
    identifier: str
    premises: frozenset[str]
    rules: frozenset[str]
    conclusion: str
    top: str | None


def complement(literal: str) -> str:
    return literal[1:] if literal.startswith("¬") else f"¬{literal}"


def construct_arguments(theory: dict) -> tuple[list[Argument], dict[str, dict]]:
    facts = [fact for fact, value in theory.get("facts", {}).items() if value]
    rules = {rule["id"]: rule for rule in theory.get("rules", [])}
    arguments = [
        Argument(f"F:{fact}", frozenset((fact,)), frozenset(), fact, None)
        for fact in facts
    ]
    signatures = {(argument.top, argument.premises, argument.rules, argument.conclusion) for argument in arguments}

    changed = True
    while changed:
        changed = False
        by_conclusion: dict[str, list[Argument]] = {}
        for argument in arguments:
            by_conclusion.setdefault(argument.conclusion, []).append(argument)

        for rule in rules.values():
            choices = [by_conclusion.get(antecedent, []) for antecedent in rule.get("antecedents", [])]
            if any(not choice for choice in choices):
                continue
            combinations = itertools.product(*choices) if choices else [tuple()]
            for combination in combinations:
                premises = frozenset().union(*(argument.premises for argument in combination))
                used_rules = frozenset((rule["id"],)).union(
                    *(argument.rules for argument in combination)
                )
                conclusion = (
                    f"block({rule['target']})"
                    if rule["type"] == "defeater"
                    else rule["consequent"]
                )
                signature = (rule["id"], premises, used_rules, conclusion)
                if signature in signatures:
                    continue
                identifier = f"A:{rule['id']}:{len(arguments) + 1}"
                arguments.append(Argument(identifier, premises, used_rules, conclusion, rule["id"]))
                signatures.add(signature)
                changed = True
    return arguments, rules


def compile_defeats(
    theory: dict, arguments: list[Argument], rules: dict[str, dict]
) -> set[tuple[str, str]]:
    conflicts = {
        frozenset((conflict["left"], conflict["right"]))
        for conflict in theory.get("conflicts", [])
    }
    stronger = {
        (priority["stronger"], priority["weaker"])
        for priority in theory.get("priorities", [])
    }
    defeats: set[tuple[str, str]] = set()

    for attacker in arguments:
        if attacker.top is None:
            continue
        attacker_rule = rules[attacker.top]
        for target in arguments:
            if target.identifier == attacker.identifier or not target.rules:
                continue
            for attacked_rule_id in target.rules:
                declared_conflict = frozenset((attacker.top, attacked_rule_id)) in conflicts
                targeted = (
                    attacker_rule["type"] == "defeater"
                    and attacker_rule.get("target") == attacked_rule_id
                )
                if not (declared_conflict or targeted):
                    continue
                if (attacked_rule_id, attacker.top) in stronger:
                    continue
                defeats.add((attacker.identifier, target.identifier))
                break
    return defeats


def grounded_extension(arguments: list[Argument], defeats: set[tuple[str, str]]) -> set[str]:
    identifiers = {argument.identifier for argument in arguments}
    attackers = {identifier: set() for identifier in identifiers}
    attacked_by = {identifier: set() for identifier in identifiers}
    for source, target in defeats:
        attackers[target].add(source)
        attacked_by[source].add(target)

    extension: set[str] = set()
    while True:
        defended = {
            identifier
            for identifier in identifiers
            if all(
                any(attacker in attacked_by[defender] for defender in extension)
                for attacker in attackers[identifier]
            )
        }
        if defended == extension:
            return extension
        extension = defended


def reason(theory: dict) -> dict:
    arguments, rules = construct_arguments(theory)
    defeats = compile_defeats(theory, arguments, rules)
    extension = grounded_extension(arguments, defeats)
    accepted = {
        argument.conclusion
        for argument in arguments
        if argument.identifier in extension and not argument.conclusion.startswith("block(")
    }
    query = theory["query"]
    positive = query in accepted
    negative = complement(query) in accepted
    if positive == negative:
        label = "Undetermined"
    else:
        label = "Positive" if positive else "Negative"
    return {
        "label": label,
        "accepted_conclusions": sorted(accepted),
        "grounded_extension": sorted(extension),
        "defeats": sorted([list(edge) for edge in defeats]),
        "argument_count": len(arguments),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("theory", type=Path)
    args = parser.parse_args()
    print(json.dumps(reason(json.loads(args.theory.read_text(encoding="utf-8"))), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
