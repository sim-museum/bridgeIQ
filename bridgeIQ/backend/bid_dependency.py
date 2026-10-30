"""Dependency-expression DSL for the reference .bid-eval-in.

Parses expressions like::

    H <= 2 & S >= 3 -> HP >= 18
    C = 6 -> mC >= 6.0
    S = 4 -> mS >= 4.2
    HP~S >= 12 & sS > 0 -> TP(H) >= 24

into an AST that can be:
  * evaluated against a hand (returns True / False / "unknown"),
  * rendered back to canonical text for display,
  * serialised to JSON for persistence with the bid meaning.

Grammar (informal)::

    expression := clause ( "->" clause )?
    clause     := atom ( "&" atom )*
    atom       := variable comparator value
    variable   := one of the names below
    comparator := "=" | "==" | ">=" | "<=" | ">" | "<" | "!="
    value      := signed number (int or float)

Variables — match the spec list at .bid-eval-in:
    C  D  H  S        — raw suit length
    mC mD mH mS       — modified length (length + strength bonus)
    HP                — high-card points
    LP                — length points
    TP                — total points (no trump set)
    TP(C) TP(D) TP(H) TP(S)
                      — total points with the given trump
    TP(S1)            — total points with the longest suit as trump
    S1 S2 S3 S4       — length of longest .. shortest suit
    mS1 mS2 mS3 mS4   — modified length, longest .. shortest
    sC sD sH sS       — stopper score (≥ 0 = surely / likely stopped)
    HP~C HP~D HP~H HP~S
                      — HP outside the named suit
    (>SH) (<SH)       — longer / shorter major
    (>DC) (<DC)       — longer / shorter minor

The parser is intentionally permissive: unrecognised variables
yield a parse error with a useful message, and a runtime
evaluation against a hand that doesn't expose the requested
variable returns ``None`` (interpreted as "unknown") rather than
raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


# Canonical variable names. Order matters for the regex below
# because we need the longer names (mS1, HP~S, etc.) to match
# before the single-letter S / mS / HP variants.
_VARIABLES = [
    # Modified longest-suit metrics (must come before bare m? and S?)
    'mS1', 'mS2', 'mS3', 'mS4',
    'S1', 'S2', 'S3', 'S4',
    # TP(suit) with the parentheses captured literally
    'TP(S1)',
    'TP(C)', 'TP(D)', 'TP(H)', 'TP(S)',
    # HP outside a suit
    'HP~C', 'HP~D', 'HP~H', 'HP~S',
    # Major / minor shorthand
    '(>SH)', '(<SH)', '(>DC)', '(<DC)',
    # Modified per-suit length
    'mC', 'mD', 'mH', 'mS',
    # Stopper score
    'sC', 'sD', 'sH', 'sS',
    # Plain HP / LP / TP and raw suit length
    'HP', 'LP', 'TP',
    'C', 'D', 'H', 'S',
]


@dataclass
class Atom:
    """Single comparison: ``<variable> <comparator> <value>``."""
    variable: str
    comparator: str  # one of '=', '==', '>=', '<=', '>', '<', '!='
    value: float

    def __str__(self):
        v = self.value
        # Drop trailing .0 for integer-valued floats.
        v_str = (str(int(v)) if v == int(v) else f"{v:g}")
        # Canonicalise '==' to '='.
        op = '=' if self.comparator == '==' else self.comparator
        return f"{self.variable} {op} {v_str}"

    def evaluate(self, ctx: Dict[str, Any]) -> Optional[bool]:
        """Return True / False / None (unknown) against the given
        variable→value mapping. None propagates so partial-hand
        contexts can't accidentally read as False."""
        actual = ctx.get(self.variable)
        if actual is None:
            return None
        try:
            actual = float(actual)
        except (TypeError, ValueError):
            return None
        op = self.comparator
        if op == '=' or op == '==':
            return actual == self.value
        if op == '!=':
            return actual != self.value
        if op == '>=':
            return actual >= self.value
        if op == '<=':
            return actual <= self.value
        if op == '>':
            return actual > self.value
        if op == '<':
            return actual < self.value
        return None


@dataclass
class Clause:
    """Conjunction of atoms (``atom1 & atom2 & atom3``)."""
    atoms: List[Atom] = field(default_factory=list)

    def __str__(self):
        return ' & '.join(str(a) for a in self.atoms)

    def evaluate(self, ctx: Dict[str, Any]) -> Optional[bool]:
        """Returns True iff every atom is True; False if ANY is
        False; None if any atom is unknown and none is False
        (short-circuit on a confirmed False)."""
        saw_unknown = False
        for a in self.atoms:
            r = a.evaluate(ctx)
            if r is False:
                return False
            if r is None:
                saw_unknown = True
        return None if saw_unknown else True


@dataclass
class Dependency:
    """Optional ``antecedent -> consequent`` implication. When the
    consequent is None the clause stands as a plain assertion."""
    antecedent: Clause
    consequent: Optional[Clause] = None

    def __str__(self):
        ant = str(self.antecedent)
        if self.consequent is None:
            return ant
        return f"{ant} -> {self.consequent}"

    def evaluate(self, ctx: Dict[str, Any]) -> Optional[bool]:
        """For an implication ``A -> B``: True iff A is False, or A
        is True and B is True. Unknown propagates."""
        a = self.antecedent.evaluate(ctx)
        if self.consequent is None:
            return a
        if a is False:
            return True  # vacuously satisfied
        if a is None:
            # Antecedent unknown → result unknown unless consequent
            # is True (which guarantees implication holds anyway).
            b = self.consequent.evaluate(ctx)
            return True if b is True else None
        # a is True
        return self.consequent.evaluate(ctx)


class ParseError(ValueError):
    """Raised when a dependency expression can't be parsed."""


# ---------------------------------------------------------------------------
# Tokeniser + parser
# ---------------------------------------------------------------------------

# Build a single regex that matches one variable name. The longest
# alternative is tried first so e.g. "HP~S" doesn't get parsed as
# "HP" followed by garbage.
_VAR_RE = re.compile(
    '|'.join(re.escape(v) for v in _VARIABLES),
)
_COMPARATOR_RE = re.compile(r'(>=|<=|==|!=|=|>|<)')
_NUMBER_RE = re.compile(r'[+-]?\d+(?:\.\d+)?')


def _parse_atom(text: str) -> Atom:
    text = text.strip()
    if not text:
        raise ParseError("empty atom")
    m = _VAR_RE.match(text)
    if not m or m.start() != 0:
        raise ParseError(
            f"unknown variable at start of {text!r} — "
            f"expected one of {_VARIABLES}")
    var = m.group(0)
    rest = text[m.end():].lstrip()
    m = _COMPARATOR_RE.match(rest)
    if not m:
        raise ParseError(
            f"missing comparator after {var!r} in {text!r}")
    comp = m.group(0)
    rest = rest[m.end():].lstrip()
    m = _NUMBER_RE.match(rest)
    if not m:
        raise ParseError(
            f"missing numeric value after {comp!r} in {text!r}")
    value = float(m.group(0))
    trailing = rest[m.end():].strip()
    if trailing:
        raise ParseError(
            f"unexpected trailing text {trailing!r} in atom "
            f"{text!r}")
    return Atom(variable=var, comparator=comp, value=value)


def _parse_clause(text: str) -> Clause:
    parts = [p.strip() for p in text.split('&')]
    return Clause(atoms=[_parse_atom(p) for p in parts if p])


def parse_dependency(text: str) -> Dependency:
    """Parse one dependency line; raises ParseError on malformed
    input. Whitespace is ignored. The optional ``-> consequent``
    part is recognised; everything else is parsed as the
    antecedent / standalone assertion."""
    text = (text or "").strip()
    if not text:
        raise ParseError("empty dependency")
    if '->' in text:
        ant, _, cons = text.partition('->')
        return Dependency(
            antecedent=_parse_clause(ant),
            consequent=_parse_clause(cons),
        )
    return Dependency(antecedent=_parse_clause(text))


def parse_dependencies(text: str) -> List[Dependency]:
    """Parse a block of dependency lines (one per line, blank
    lines and ``#`` comments ignored). Raises ParseError on the
    first malformed line, with a 1-based line number prefix."""
    out: List[Dependency] = []
    for i, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            out.append(parse_dependency(line))
        except ParseError as ex:
            raise ParseError(f"line {i}: {ex}") from ex
    return out


# ---------------------------------------------------------------------------
# Hand → context dict
# ---------------------------------------------------------------------------

def hand_to_context(hand, trump: str = None) -> Dict[str, Any]:
    """Build the variable→value context for a Hand object.

    ``trump`` is one of 'C', 'D', 'H', 'S', or None (no trump set).
    The returned dict has values for every variable the parser
    knows about; variables that don't apply (e.g. TP(C) when no
    trump) get the same value as the no-trump TP.
    """
    from .models import Suit, Rank

    suit_map = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                'D': Suit.DIAMONDS, 'C': Suit.CLUBS}
    lengths: Dict[str, int] = {}
    suit_hcp: Dict[str, int] = {}
    has_ace: Dict[str, bool] = {}
    has_king: Dict[str, bool] = {}
    has_queen: Dict[str, bool] = {}

    for k, suit in suit_map.items():
        cards = hand.get_suit_cards(suit) or []
        lengths[k] = len(cards)
        suit_hcp[k] = sum(max(0, 4 - c.rank.value) for c in cards)
        has_ace[k] = any(c.rank == Rank.ACE for c in cards)
        has_king[k] = any(c.rank == Rank.KING for c in cards)
        has_queen[k] = any(c.rank == Rank.QUEEN for c in cards)

    hp = hand.hcp()
    lp = sum(max(0, l - 4) for l in lengths.values())

    def tp_for(trump_char):
        if trump_char is None or trump_char == 'NT':
            return hp + lp
        dist = 0
        for kk, l in lengths.items():
            if kk == trump_char:
                continue
            if l == 0:
                dist += 3
            elif l == 1:
                dist += 2
            elif l == 2:
                dist += 1
        return hp + lp + dist

    # Longest-suit shorthand.
    sorted_keys = sorted(lengths,
                         key=lambda k: (-lengths[k], k))
    s1, s2, s3, s4 = sorted_keys

    def mod_length(k):
        return lengths[k] + suit_hcp[k] / 10.0

    def stopper_score(k):
        # The reference four-tier: +1 surely / +0.5 likely / -0.5 unlikely / -1 surely-not.
        if has_ace[k]:
            return 1.0
        if has_king[k] and lengths[k] >= 2:
            return 1.0
        if has_queen[k] and lengths[k] >= 3:
            return 0.5
        if has_king[k] or (has_queen[k] and lengths[k] >= 2):
            return -0.5
        return -1.0

    ctx: Dict[str, Any] = {
        'HP': hp,
        'LP': lp,
        'TP': tp_for(trump),
    }
    for k in ('C', 'D', 'H', 'S'):
        ctx[k] = lengths[k]
        ctx['m' + k] = mod_length(k)
        ctx['s' + k] = stopper_score(k)
        ctx['HP~' + k] = hp - suit_hcp[k]
        ctx[f'TP({k})'] = tp_for(k)
    # Longest-suit shorthand.
    for i, k in enumerate((s1, s2, s3, s4), start=1):
        ctx[f'S{i}'] = lengths[k]
        ctx[f'mS{i}'] = mod_length(k)
    ctx['TP(S1)'] = tp_for(s1)
    # Major / minor shorthand.
    ctx['(>SH)'] = max(lengths['S'], lengths['H'])
    ctx['(<SH)'] = min(lengths['S'], lengths['H'])
    ctx['(>DC)'] = max(lengths['D'], lengths['C'])
    ctx['(<DC)'] = min(lengths['D'], lengths['C'])
    return ctx


def evaluate_dependencies(text: str, hand,
                           trump: str = None) -> List[Dict[str, Any]]:
    """Parse `text`, evaluate every line against `hand`, and return
    a list of {'line': str, 'result': True/False/None}. Useful for
    the editor's live validator + the bid-eval-out renderer."""
    deps = parse_dependencies(text)
    ctx = hand_to_context(hand, trump=trump)
    return [{'line': str(d), 'result': d.evaluate(ctx)} for d in deps]
