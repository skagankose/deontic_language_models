"""Literal language L (paper §3.2, Definition 1).

Grammar (whitespace-insensitive):

    literal  := NEG literal | DEONTIC "(" literal ")" | "block" "(" rule_id ")" | atom
    NEG      := "¬" | "~" | "!" | "-" | "not "
    DEONTIC  := "Obl" | "Perm" | "Forb"     (aliases: Obligatory, Permitted, Forbidden, Prohibited)
    atom     := Pred [ "(" term { "," term } ")" ]

Normalisation (paper: "We normalize Forb(a) as Obl(¬a)"):
    Forb(φ)  -> Obl(¬φ)
    ¬¬φ      -> φ

Normative opposition is *explicit* in the paper: the reasoner never creates attacks from
surface form. `opposed()` is used only by the validator, to check that a declared conflict
between two derivational rules is well-founded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

NEG = "¬"
DEONTIC_ALIASES = {
    "Obl": "Obl", "Obligatory": "Obl", "Obligation": "Obl",
    "Perm": "Perm", "Permitted": "Perm", "Permission": "Perm",
    "Forb": "Forb", "Forbidden": "Forb", "Prohibited": "Forb", "Prohibition": "Forb",
}
_PRED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TERM = re.compile(r"[^,()\s¬]+")
_VARIABLE = re.compile(r"^(?:[xyz][0-9]*|\?[A-Za-z_][A-Za-z0-9_]*)$")


class LiteralError(ValueError):
    pass


@dataclass(frozen=True)
class Atom:
    pred: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Neg:
    body: "Node"


@dataclass(frozen=True)
class Deon:
    op: str  # "Obl" | "Perm" | "Forb" (Forb only before normalisation)
    body: "Node"


@dataclass(frozen=True)
class Block:
    rule: str


Node = Union[Atom, Neg, Deon, Block]


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def _ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def _expect(self, ch: str) -> None:
        self._ws()
        if not self.s.startswith(ch, self.i):
            raise LiteralError(f"expected {ch!r} at position {self.i} in {self.s!r}")
        self.i += len(ch)

    def literal(self) -> Node:
        self._ws()
        rest = self.s[self.i:]
        for prefix in ("¬", "~", "!"):
            if rest.startswith(prefix):
                self.i += len(prefix)
                return Neg(self.literal())
        if rest.startswith("-") and len(rest) > 1 and rest[1].isalpha():
            self.i += 1
            return Neg(self.literal())
        m = re.match(r"(?i:not)\s+(?=[A-Za-z_¬~!])", rest)
        if m:
            self.i += m.end()
            return Neg(self.literal())
        m = _PRED.match(rest)
        if not m:
            raise LiteralError(f"expected predicate at position {self.i} in {self.s!r}")
        name = m.group(0)
        self.i += m.end()
        self._ws()
        has_parens = self.s.startswith("(", self.i)
        if name == "block" and has_parens:
            self.i += 1
            self._ws()
            m = _TERM.match(self.s[self.i:])
            if not m:
                raise LiteralError(f"block() needs a rule id in {self.s!r}")
            self.i += m.end()
            self._expect(")")
            return Block(m.group(0))
        if name in DEONTIC_ALIASES and has_parens:
            self.i += 1
            body = self.literal()
            self._expect(")")
            return Deon(DEONTIC_ALIASES[name], body)
        if not has_parens:
            return Atom(name, ())
        self.i += 1
        args: list[str] = []
        self._ws()
        if self.s.startswith(")", self.i):
            self.i += 1
            return Atom(name, ())
        while True:
            self._ws()
            m = _TERM.match(self.s[self.i:])
            if not m:
                raise LiteralError(f"expected term at position {self.i} in {self.s!r}")
            args.append(m.group(0))
            self.i += m.end()
            self._ws()
            if self.s.startswith(",", self.i):
                self.i += 1
                continue
            self._expect(")")
            return Atom(name, tuple(args))

    def parse(self) -> Node:
        node = self.literal()
        self._ws()
        if self.i != len(self.s):
            raise LiteralError(f"trailing text {self.s[self.i:]!r} in {self.s!r}")
        return node


def _norm(node: Node) -> Node:
    if isinstance(node, Neg):
        body = _norm(node.body)
        return body.body if isinstance(body, Neg) else Neg(body)
    if isinstance(node, Deon):
        if node.op == "Forb":
            return Deon("Obl", _norm(Neg(node.body)))
        return Deon(node.op, _norm(node.body))
    return node


def parse(text: str) -> Node:
    """Parse and normalise a literal. Raises LiteralError."""
    if not isinstance(text, str) or not text.strip():
        raise LiteralError(f"empty or non-string literal: {text!r}")
    return _norm(_Parser(text.strip()).parse())


def render(node: Node) -> str:
    if isinstance(node, Atom):
        return f"{node.pred}({', '.join(node.args)})" if node.args else node.pred
    if isinstance(node, Neg):
        return NEG + render(node.body)
    if isinstance(node, Deon):
        return f"{node.op}({render(node.body)})"
    return f"block({node.rule})"


def canonical(text: str) -> str:
    """Canonical string form: parsed, Forb normalised, whitespace fixed."""
    return render(parse(text))


def pretty(text: str) -> str:
    """Human display: shows Obl(¬a) as Forb(a)."""
    def go(n: Node) -> str:
        if isinstance(n, Deon) and n.op == "Obl" and isinstance(n.body, Neg):
            return f"Forb({go(n.body.body)})"
        if isinstance(n, Deon):
            return f"{n.op}({go(n.body)})"
        if isinstance(n, Neg):
            return NEG + go(n.body)
        return render(n)
    return go(parse(text))


def complement(text: str) -> str:
    node = parse(text)
    return render(node.body if isinstance(node, Neg) else Neg(node))


def is_blocker(text: str) -> bool:
    try:
        return isinstance(parse(text), Block)
    except LiteralError:
        return False


def opposed(a: str, b: str) -> bool:
    """Normative opposition (used only to validate declared conflicts).

    p vs ¬p; Obl(φ) vs Obl(¬φ) [= Obl vs Forb]; Perm(φ) vs Obl(¬φ) [= Perm(a) vs Forb(a),
    and Perm(¬a) vs Obl(a)].
    """
    na, nb = parse(a), parse(b)
    if render(nb) == complement(render(na)):
        return True
    if isinstance(na, Deon) and isinstance(nb, Deon):
        neg_a = render(_norm(Neg(na.body)))
        neg_b = render(_norm(Neg(nb.body)))
        if na.op == "Obl" and nb.op == "Obl" and render(nb.body) == neg_a:
            return True
        if na.op == "Perm" and nb.op == "Obl" and render(nb.body) == neg_a:
            return True
        if na.op == "Obl" and nb.op == "Perm" and render(na.body) == neg_b:
            return True
    return False


def duality_negatives(query: str) -> set[str]:
    """Literals whose acceptance refutes `query` under the D axiom / O-P duality.

    Off by default (paper: "No unrestricted modal closure is assumed").
    Obl(φ) is refuted by Obl(¬φ) and Perm(¬φ); Perm(φ) is refuted by Obl(¬φ).
    """
    node = parse(query)
    out: set[str] = set()
    if isinstance(node, Deon):
        neg_body = _norm(Neg(node.body))
        if node.op == "Obl":
            out |= {render(Deon("Obl", neg_body)), render(Deon("Perm", neg_body))}
        elif node.op == "Perm":
            out.add(render(Deon("Obl", neg_body)))
    return out


def duality_positives(query: str) -> set[str]:
    """Under D: Obl(φ) entails Perm(φ)."""
    node = parse(query)
    if isinstance(node, Deon) and node.op == "Perm":
        return {render(Deon("Obl", node.body))}
    return set()


# ---- terms, variables, grounding -------------------------------------------------------

def atoms(node: Node):
    if isinstance(node, Atom):
        yield node
    elif isinstance(node, (Neg, Deon)):
        yield from atoms(node.body)


def terms(text: str) -> list[str]:
    return [t for a in atoms(parse(text)) for t in a.args]


def is_variable(term: str, declared: frozenset[str] = frozenset()) -> bool:
    return term in declared or bool(_VARIABLE.match(term))


def variables(text: str, declared: frozenset[str] = frozenset()) -> set[str]:
    return {t for t in terms(text) if is_variable(t, declared)}


def substitute(text: str, binding: dict[str, str]) -> str:
    def go(n: Node) -> Node:
        if isinstance(n, Atom):
            return Atom(n.pred, tuple(binding.get(t, t) for t in n.args))
        if isinstance(n, Neg):
            return Neg(go(n.body))
        if isinstance(n, Deon):
            return Deon(n.op, go(n.body))
        return n
    return render(go(parse(text)))


def signatures(text: str) -> list[tuple[str, int]]:
    """(predicate, arity) pairs, for symbol-table validation."""
    return [(a.pred, len(a.args)) for a in atoms(parse(text))]
