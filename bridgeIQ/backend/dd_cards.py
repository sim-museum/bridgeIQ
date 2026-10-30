"""Double-dummy trick count for every playable card at a replay position (bcalc-style).

For the seat to play after `step` cards of a played board, each legal card maps to the most tricks
that seat's side can take in the whole deal if it plays that card (tricks already won + the
double-dummy future, all four hands seen). The best card(s) keep the maximum; a card with a lower
number gives tricks away. Used by the replay and the compare-replay (open vs closed room) views.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import Card, Hand, Rank, Seat, Suit

_CACHE: Dict[tuple, Optional[tuple]] = {}


def position(original_hands: Dict[Seat, Hand], tricks: List, step: int):
    """(remaining cards per seat as c52 sets, current-trick leader, current-trick cards, seat to
    play, tricks won so far per side {True: NS, False: EW}) after `step` cards, or None when the
    play is over or the record is incomplete."""
    remaining = {s: {c.suit.value * 13 + c.rank.value for c in (original_hands.get(s) or Hand([])).cards}
                 for s in Seat}
    if any(len(v) != 13 for v in remaining.values()):
        return None
    won = {True: 0, False: 0}
    left = step
    for t in tricks:
        n = len(t.cards)
        if left >= n == 4:
            for i, c in enumerate(t.cards):
                remaining[Seat((t.leader.value + i) % 4)].discard(c.suit.value * 13 + c.rank.value)
            if t.winner is None:
                return None
            won[t.winner.is_ns()] += 1
            left -= n
            continue
        cur = t.cards[:left]
        for i, c in enumerate(cur):
            remaining[Seat((t.leader.value + i) % 4)].discard(c.suit.value * 13 + c.rank.value)
        to_play = Seat((t.leader.value + len(cur)) % 4)
        if not remaining[to_play]:
            return None
        return remaining, t.leader, [c.suit.value * 13 + c.rank.value for c in cur], to_play, won
    return None


def _pbn(remaining) -> str:
    ranks = "AKQJT98765432"
    parts = []
    for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
        by = [[], [], [], []]
        for c in remaining[s]:
            by[c // 13].append(c % 13)
        parts.append(".".join("".join(ranks[r] for r in sorted(x)) for x in by))
    return "N:" + " ".join(parts)


def card_tricks(original_hands: Dict[Seat, Hand], tricks: List, step: int,
                trump: Optional[Suit]) -> Optional[Tuple[Seat, Dict[Tuple[Suit, Rank], int]]]:
    """(seat to play, {(suit, rank): total tricks for that seat's side}) at `step`, or None."""
    pos = position(original_hands, tricks, step)
    if pos is None:
        return None
    remaining, leader, cur, to_play, won = pos
    pbn = _pbn(remaining)
    strain = 5 if (trump is None or trump == Suit.NOTRUMP) else trump.value + 1
    key = (pbn, strain, leader.value, tuple(cur))
    if key not in _CACHE:
        from .dds import DDSolver
        try:
            res = DDSolver().solve(strain, leader.value, cur, [pbn], solutions=3)
        except Exception:
            res = {}
        _CACHE[key] = tuple((c, v[0]) for c, v in res.items() if v)
    side = to_play.is_ns()
    out = {(Suit(c // 13), Rank(c % 13)): won[side] + f for c, f in _CACHE[key]}
    return (to_play, out) if out else None


def declarer_dd(original_hands, tricks, step, trump, declarer: Seat) -> Optional[int]:
    """Most tricks the declarer's side can still finish with from this position (double dummy)."""
    got = card_tricks(original_hands, tricks, step, trump)
    if not got:
        return None
    to_play, tr = got
    best = max(tr.values())
    return best if to_play.is_ns() == declarer.is_ns() else 13 - best
