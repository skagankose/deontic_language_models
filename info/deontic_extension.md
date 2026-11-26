# How deontic logic was added to the released solver

This note documents how our solver `pcnr` (in `solver/pcnr/`) extends the authors' public reference solver
(`neuro_symbolic/baselines/symbolic_reasoner_reference.py`) to handle obligations, permissions and prohibitions.
Every example below was run against the current code (2026-11-25).

## Summary

**The gap.** The authors' solver treats every conclusion as an opaque string. Its only notion of opposition is `p` vs `¬p`,
and it doesn't care whether a string looks like `Obl(...)`. `Obl(Pay)`, `Forb(Pay)` and `Perm(Pay)` are therefore three unrelated strings.

**How we integrated deontic logic** (`solver/pcnr/literals.py`, used by the validator, the decision step and the checker):

- **Parser for deontic operators:** `Obl(a)` (a is obligatory), `Perm(a)` (a is permitted or allowed), `Forb(a)` (a is
  forbidden). Aliases such as `Obligatory`, `Permitted`, `Forbidden` and `Prohibited` map to these.
- **Normalisation, as the paper specifies:** `Forb(a)` becomes `Obl(¬a)`, so "must not disclose" and "obligated not to
  disclose" are the same literal, whichever spelling the LLM uses.
- **Normative opposition, meaning which pairs contradict each other:**
  - `Obl(a)` vs `Forb(a)`: must vs must not.
  - `Perm(a)` vs `Forb(a)`: may vs must not.
  - `Perm(¬a)` vs `Obl(a)`: need not vs must.

  `Obl(a)` and `Perm(a)` do **not** conflict. Following the paper, the solver never creates an attack from opposition on
  its own; conflicts must still be declared. The validator uses opposition to *reject* declared conflicts that aren't
  real (for example "must pay" vs "may pay"), emitting a `CONFLICT_NOT_OPPOSED` error.
- **Optional duality switch for answering questions**, used in the two deontic benchmark domains:
  - If `Forb(disclose)` is accepted, "May the provider disclose?" is answered **No**.
  - If `Obl(pay)` is accepted, "May the employer pay?" is answered **Yes**.

  Without the switch, the paper's Eq. (1) only answers No when the exact negation (`¬Perm(disclose)`) is derived, which
  never happens in practice.

**What is missing (deliberately, following the paper's "no unrestricted modal closure"):**

- no deontic inference axioms (for example "obligatory implies true"), and no new deontic conclusions derived by logic;
- no contrary-to-duty obligations (e.g. "if you fail to pay, you must pay interest") beyond what ordinary rules can express;
- no automatic conflicts: opposed rules must still be declared in conflict;
- no time-dependent or conditional operators (deadlines, "obligatory until"), and no explicit bearer of an obligation
  beyond the action's arguments.

The rest of this document gives the details.

## 1. Starting point: what the released solver does

The released solver treats every conclusion as an **opaque string**:

- The only notion of opposition is the `¬` prefix: `complement("p") = "¬p"`.
- Whether two rules conflict depends only on the explicitly declared `conflicts` list. The solver never looks inside a literal.
- `Obl(Pay(c))`, `Forb(Pay(c))` and `Perm(Pay(c))` are three unrelated strings. The solver doesn't know that "must pay" and "must not pay" contradict each other, that `Forb(a)` and `Obl(¬a)` mean the same thing, or that `Perm(a)` and `Obl(a)` are compatible.
- The query is answered **Negative** only if exactly `¬q` is accepted. So for "Must the employer pay?" (`Obl(Pay)`), an accepted prohibition `Forb(Pay)` never produces a "No".

The paper (§3.2) describes more than this: deontic literals built with Obl, Perm and Forb; the normalisation
Forb(a) = Obl(¬a); explicit normative opposition; and no unrestricted modal closure. The released code does not implement any of it.

## 2. Design principles (taken from the paper)

1. **Forb(a) is normalised to Obl(¬a).** One canonical form per meaning.
2. **Opposition is explicit, not inferred.** The reasoner creates attacks *only* from declared conflicts, never from the shape of a literal. ("Normative opposition is explicit rather than inferred from surface negation alone.")
3. **No unrestricted modal closure.** The solver does not derive new deontic conclusions from logical axioms. For example, it never derives `Perm(a)` from `Obl(a)` as a new literal.

Within these limits, deontic knowledge is used in two places: to **validate** declared conflicts, and, optionally, to **interpret the answer** to a deontic query.

## 3. What was implemented

All core logic is in `solver/pcnr/literals.py`. The other modules use it.

### 3.1 A parser for the literal language

The released solver uses raw strings. `pcnr` parses every literal into a small syntax tree:

```
literal  := NEG literal | DEONTIC "(" literal ")" | "block" "(" rule_id ")" | atom
NEG      := "¬" | "~" | "!" | "-" | "not "
DEONTIC  := Obl | Perm | Forb          aliases: Obligatory, Obligation, Permitted, Permission,
                                                Forbidden, Prohibited, Prohibition
atom     := Pred [ "(" term { "," term } ")" ]
```

Tree node types: `Atom(pred, args)`, `Neg(body)`, `Deon(op, body)`, `Block(rule)`.
Deontic operators can wrap negated actions (`Obl(¬Pay(c))`), can themselves be negated (`¬Obl(Pay(c))`), and can appear
as rule antecedents as well as consequents.

Functions: `parse()`, `render()`, `canonical()` (parse + normalise + render), `pretty()` (display form).

### 3.2 Normalisation

Applied to every literal by `canonical()`:

| Rule | Effect |
|---|---|
| `Forb(φ)` → `Obl(¬φ)` | the paper's normalisation |
| `¬¬φ` → `φ` | double strong negation removed |
| aliases → `Obl` / `Perm` / `Forb` | e.g. `Prohibited` → `Forb` → `Obl(¬…)` |
| negation spellings → `¬` | `~`, `!`, `-`, `not ` |
| whitespace | removed or standardised |

Verified examples:

| Input | Canonical form | Display form (`pretty`) |
|---|---|---|
| `Forb(Disclose(provider))` | `Obl(¬Disclose(provider))` | `Forb(Disclose(provider))` |
| `Prohibited(Pay(c))` | `Obl(¬Pay(c))` | `Forb(Pay(c))` |
| `¬Forb(Pay(c))` | `¬Obl(¬Pay(c))` | `¬Forb(Pay(c))` |
| `~Obl( Pay(c) )` | `¬Obl(Pay(c))` | `¬Obl(Pay(c))` |
| `not Perm(Pay(c))` | `¬Perm(Pay(c))` | `¬Perm(Pay(c))` |
| `Obligatory(Pay(c))` | `Obl(Pay(c))` | `Obl(Pay(c))` |

**Where normalisation is applied:** `theory.normalize()` canonicalises the query and every fact, antecedent and consequent
before validation. "Must not disclose" and "is forbidden to disclose" therefore become the same literal, and LLM outputs
using either spelling are treated identically. Proof graphs and DOT renderings use `pretty()`, so readers see `Forb(...)` again.

### 3.3 Complement

`complement(x)` adds or removes the outer `¬` on the canonical form, exactly as in the released solver but applied after normalisation:

- `complement("Obl(a)") = "¬Obl(a)"`
- `complement("Forb(a)") = "¬Obl(¬a)"`, i.e. "not forbidden"

### 3.4 Normative opposition: `opposed(a, b)`

This function defines which pairs of literals contradict each other:

| Pair | Reading | Opposed? |
|---|---|---|
| `p` vs `¬p` | plain contradiction | yes |
| `Obl(a)` vs `¬Obl(a)` | obligatory vs not obligatory | yes |
| `Obl(a)` vs `Forb(a)` (= `Obl(¬a)`) | must vs must not | **yes** |
| `Perm(a)` vs `Forb(a)` | may vs must not | **yes** |
| `Perm(¬a)` vs `Obl(a)` | need not vs must | **yes** |
| `Obl(a)` vs `Perm(a)` | must vs may | no (compatible) |
| `Perm(a)` vs `Perm(¬a)` | may vs may refrain | no (the action is optional) |

**Important:** `opposed()` is **not used by the reasoner** to create attacks; attacks still come only from declared
conflicts, as the paper requires. It is used by the validator (next section).

### 3.5 Validator: declared conflicts must be genuine

`solver/pcnr/validate.py`, stage 4 (graph validation). For every declared conflict between two derivational rules,
the validator checks `opposed(head_1, head_2)`. If the heads are not normatively opposed, it emits:

```
CONFLICT_NOT_OPPOSED  (error, blocking, repairable)
  "Perm(Deliver(c)) and Obl(Deliver(c)) are not normatively opposed"
```

The conflict is quarantined: it never enters the reasoner, and with the default settings the case is flagged and
answered Undetermined. In the full pipeline it is first sent to the LLM repair pass. This catches a common extraction
error: an LLM declaring that "must deliver" and "may deliver" conflict.

Related checks:
- `block(...)` tokens may appear only as defeater consequents (`BLOCKER_MISUSE`).
- Predicate arity is checked inside deontic wrappers too (`ARITY_MISMATCH`).
- Two *strict* rules with opposed heads make the theory inconsistent (`STRICT_CONFLICT`).

### 3.6 Answering deontic queries: the optional duality switch

The decision function (paper Eq. 1) is in `solver/pcnr/pipeline.py`:

- **Positive** if the query `q` is accepted;
- **Negative** if `complement(q)` is accepted;
- otherwise **Undetermined** (or Negative under the closed-world policy when no argument exists either way).

For deontic queries, taking Eq. 1 literally is too strict. If the grounded extension accepts `Forb(Deliver)`, the
question "Must the supplier deliver?" (`Obl(Deliver)`) is still Undetermined, because the exact literal
`¬Obl(Deliver)` is never derived. Contracts state prohibitions and permissions, not "it is not obligatory that …".

`Config(deontic_duality=True)` therefore uses the standard deontic duality and the D axiom (nothing obligatory is also
forbidden) **only when reading off the answer**:

| Query | Also counts as **Negative** if accepted | Also counts as **Positive** if accepted |
|---|---|---|
| `Obl(a)` "must a?" | `Obl(¬a)` = `Forb(a)`, `Perm(¬a)` "need not" | — |
| `Perm(a)` "may a?" | `Obl(¬a)` = `Forb(a)` | `Obl(a)` "must", which implies "may" |

Functions: `duality_negatives(q)` and `duality_positives(q)` in `literals.py`.

The switch is **off by default**, to stay faithful to the paper's "no unrestricted modal closure". It does not add
conclusions to the extension; it only widens what counts as a "Yes" or "No" answer to the query. The proof checker
(`check.py`) takes the same switch and verifies that the proof's root conclusion justifies the label under it.

In the benchmark, the two deontic domains (data disclosure: `Perm(Disclose(provider))`; overtime:
`Obl(PayOvertime(employer))`) set `deontic_duality: true` in each scenario's `decision_config`. The two plain domains do not.

### 3.7 Other modules touched

| Module | Deontic-related change |
|---|---|
| `theory.py` | `normalize()` canonicalises all literals; grounding (`substitute`) works through `Obl/Perm` wrappers, e.g. `Obl(Pay(x))` → `Obl(Pay(c1))` |
| `validate.py` | opposition check on declared conflicts; arity check inside deontic literals |
| `reasoner.py` | **unchanged in spirit**: attacks only from declared conflicts and defeater targets, exactly as the released solver |
| `pipeline.py` | decision function with optional duality |
| `check.py` | label/conclusion consistency check under the same duality setting |
| `proof.py` | `pretty()` display (`Forb(...)`) in proof graphs and DOT output |

## 4. Worked example (verified)

```
r1: Contract(c)  ⇒ Obl(Deliver(c))        "the supplier must deliver"
r2: Strike(c)    ⇒ Forb(Deliver(c))       "during a strike the supplier must not deliver"   (stored as Obl(¬Deliver(c)))
r3: Emergency(c) ⇒ Perm(Deliver(c))       "in an emergency the supplier may deliver"
conflicts: r2⋈r1 (must vs must not), r3⋈r2 (may vs must not)
priorities: r2 ≻ r1, r3 ≻ r2
```

| Query | Facts | Duality | Result | Why |
|---|---|---|---|---|
| `Forb(Deliver(c))` | Contract, Strike | off | **Positive** | query normalised to `Obl(¬Deliver(c))`; r2 defeats r1 and is accepted |
| `Obl(Deliver(c))` | Contract, Strike | off | Undetermined | `Obl(¬Deliver)` is accepted, but the exact `¬Obl(Deliver)` never is |
| `Obl(Deliver(c))` | Contract, Strike | **on** | **Negative** | accepted `Forb(Deliver)` refutes "must deliver" |
| `Obl(Deliver(c))` | Contract, Strike, Emergency | off | **Positive** | r3 (permission as an exception) defeats r2, so r1 is reinstated |
| `Perm(Deliver(c))` | Contract | off | Undetermined | only `Obl(Deliver)` is accepted, which is not literally `Perm(Deliver)` |
| `Perm(Deliver(c))` | Contract | **on** | **Positive** | "must deliver" implies "may deliver" |
| — | declared conflict r3⋈r1 | — | `CONFLICT_NOT_OPPOSED` | "may deliver" and "must deliver" do not conflict |

Row 4 shows the paper's "strong permission operating as an exception": a permission defeats a prohibition and thereby
reinstates an obligation. It needs no special machinery, only the declared conflict plus priority.

## 5. Tests

`solver/tests/test_pcnr.py`:
- `Literals`: canonical forms, complement, opposition, parse errors.
- `Deontic`:
  - forbidden-query normalisation;
  - the duality switch (Undetermined without it, Negative with it, proof passes Check);
  - permission as an exception reinstating an obligation;
  - the obligation-vs-permission conflict being rejected.

The suite has 39 tests, all passing.

## 6. What is deliberately not implemented

- **Modal closure or inference axioms:** no `Obl(a) → a`, no `Obl(a ∧ b) → Obl(a)`, no new deontic literals derived from axioms (paper: "no unrestricted modal closure").
- **Contrary-to-duty obligations** ("if you fail to pay, you must pay interest"). These can be written as ordinary rules whose antecedents mention the violation, but there is no special semantics.
- **Automatic conflicts from opposition:** opposed rules must still be declared in conflict. Two opposed rules with no declared conflict can both be accepted; `pcnr` then flags `INCONSISTENT_EXTENSION` instead of silently picking one.
- **Temporal or conditional deontic operators** (deadlines, "obligatory until"), and agents or bearers as part of the operator. The bearer is just an argument of the action, e.g. `Pay(employer)`.

## 7. Known imperfection

With duality off, a deontic query whose opposing prohibition is accepted (row 2 of the example) is labelled Undetermined
with the reason text "unresolved conflict: neither side is defended". The label is correct under Eq. 1, but the text
is imprecise: what holds is that neither the query nor its *exact* complement is accepted.
