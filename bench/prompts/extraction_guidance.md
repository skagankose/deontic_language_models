Representation conventions (follow exactly):

Literals
- Use only the ground literal symbols given in the Defined terms unit (for example `Delay(case)`), plus the query literal.
- Strong negation is the prefix `¬` (for example `¬EnforceablePenalty(case)`).
- Deontic operators wrap an action literal: `Obl(a)` = a must happen; `Forb(a)` = a must not happen; `Perm(a)` = a may happen; "is not required to a" / "need not a" = `Perm(¬a)`.
- Deontic operators apply only to action literals that the Defined terms describe as actions (for example `Disclose(provider)`); never wrap other defined literals in Obl/Perm/Forb.

facts
- One fact per case-file statement (units F1, F2, ...), as a positive literal; cite that unit.
- Do not add facts that are not stated.

rules
- One rule per conditional statement. `id` = `r<clause number>`; if a clause also contains an "unless ..." exception, that exception is a second rule with id `r<clause number>b`.
- `antecedents` = only the conditions stated for that rule. An exception's antecedents are only the exception's own conditions, not those of the rule it excepts.
- `consequent` = the literal the rule concludes (an exception concludes the opposite of the rule it excepts).
- `type`: "strict" for provisions that hold "in all cases"; "defeater" for statements that another clause "does not apply" or "cannot be relied upon" (then `consequent` = `block(<id of that clause's rule>)` and `target` = that id); otherwise "defeasible".
- Include every clause, also clauses whose conditions are not met in this case and clauses on other topics.

conflicts
- List a pair `{left, right}` for every pair of rules with opposite conclusions that the text explicitly links: an exception and the rule it excepts ("unless", "notwithstanding Clause n", "subject to Clause n", "except as provided in Clause n", "Clause m prevails over / takes precedence over Clause n"), or two clauses the text says both apply.
- Do not add conflicts that the text does not link. Do not list pairs between a defeater and its target.

priorities
- For each linked pair, give `{stronger, weaker}` where the text says which one prevails: an "unless" exception prevails over its rule; "notwithstanding Clause n" in clause m means m prevails over n; "subject to Clause n" in clause m means n prevails over m; "except as provided in Clause n" in clause m means n prevails over m; a precedence statement says it directly.
- If the text says neither prevails, list the conflict and no priority. If two clauses each claim to prevail over the other, list both priorities.

sources and metadata
- `source` = the id of the unit where the object is stated; for conflicts and priorities, the unit containing the linking language.
- `confidence` in [0, 1]; `source_pointers` = all unit ids you cited.

Return one JSON object and nothing else. Do not answer the query.
