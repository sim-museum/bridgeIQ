"""Single-dummy declarer search for biq (alpha-mu style).

biq's default cardplay is PIMC: sample hidden layouts, double-dummy-solve each,
average, play the max. PIMC has two named flaws (Frank & Basin 1998):
STRATEGY FUSION — each world is solved omnisciently, so biq assumes it will
always guess right later and never values gathering a count — and NON-LOCALITY.
A sophisticated player sees the tells (omniscient discards, guesses taken before
the count is in) and scoffs. This module plays WITHOUT using hidden-card
knowledge for biq's own decisions.

The property that matters: at every biq decision, biq must choose ONE card that
is the same across all the layouts it cannot distinguish. So biq never "sees"
the opponents' cards. The search:

  * MAX = biq's side (declarer + dummy; their cards are known and identical in
    every sampled world). At a MAX node biq commits a single card for the whole
    world-group.
  * MIN = the defenders (hidden, vary per world). Modelled as tough: each plays
    its double-dummy-best card for its own world. This is biq *assuming strong
    opponents*, not biq cheating — biq's own choices stay consistent.
  * INFORMATION: at each trick boundary the world-group is SPLIT by the cards
    biq actually observed (the defenders' plays). Worlds that now look different
    to biq go into different groups, so biq's later play can legitimately branch
    on the count it has gathered. This is what makes deferring a guess valuable.
  * Double-dummy is used ONLY as the value estimate at the search horizon
    (depth tricks deep), never as biq's visible play.

v1 scope: biq DECLARING (controls declarer + dummy). Defence (hidden partner)
is a later phase; callers fall back to PIMC when biq is not declarer-side.
"""
from __future__ import annotations
import os
import time
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from .models import Seat, Suit, Rank, Card, BoardState
from .dds import DDSolver


class _Budget(Exception):
    """Raised inside the search when the per-decision time budget is spent, so
    choose() can return the best card found so far (bounds live latency)."""

_RANKS = "AKQJT98765432"   # index = rank.value (0=A .. 12=2); lower value = higher card


# ---- low-level card helpers (work in c52 = suit.value*13 + rank.value) ----

def _suit(c52: int) -> int:
    return c52 // 13


def _rank(c52: int) -> int:
    return c52 % 13


def _strain_i(trump: Optional[Suit]) -> int:
    # DDS: 1=S,2=H,3=D,4=C,5=NT. Suit enum S=0,H=1,D=2,C=3.
    return 5 if trump is None else trump.value + 1


def _legal(hand: frozenset, lead_suit: Optional[int]) -> List[int]:
    """Legal cards from a c52 set given the suit led this trick (None = on lead)."""
    if lead_suit is not None:
        follow = [c for c in hand if _suit(c) == lead_suit]
        if follow:
            return sorted(follow)
    return sorted(hand)


# Search only one card of each run of touching cards (KQJ -> one branch). They
# score the same in every world, so this only saves time — time the 12 s
# budget otherwise runs out on, leaving later candidates unevaluated.
_COLLAPSE = os.environ.get("BIQ_AMU_COLLAPSE", "1") == "1"


# Root ordering: score every candidate with a one-trick search first, then run
# the full-depth search best-first, so a budget stop drops the least promising
# cards instead of whichever came last in suit order (clubs).
_ORDER = os.environ.get("BIQ_AMU_ORDER", "1") == "1"


def _runs(cards: List[int]) -> Dict[int, List[int]]:
    """{representative: all cards of its touching run} (rep = lowest card)."""
    by: Dict[int, List[int]] = {}
    for c in cards:
        by.setdefault(c // 13, []).append(c % 13)
    out: Dict[int, List[int]] = {}
    for su, ranks in by.items():
        ranks.sort()
        i = 0
        while i < len(ranks):
            j = i
            while j + 1 < len(ranks) and ranks[j + 1] == ranks[j] + 1:
                j += 1
            out[su * 13 + ranks[j]] = [su * 13 + r for r in ranks[i:j + 1]]
            i = j + 1
    return out


def _collapse(cards: List[int]) -> List[int]:
    """Collapse touching (consecutive-rank) cards in each suit to one
    representative (the lowest of the run) — they are interchangeable for play,
    so the search needn't branch on all of them. Big cut to the branching factor
    with no loss (e.g. KQJ -> J, AJT87 -> A,T,7)."""
    by = {}
    for c in cards:
        by.setdefault(c // 13, []).append(c % 13)
    out = []
    for su, ranks in by.items():
        ranks.sort()                                   # rank.value ascending
        i = 0
        while i < len(ranks):
            j = i
            while j + 1 < len(ranks) and ranks[j + 1] == ranks[j] + 1:
                j += 1
            out.append(su * 13 + ranks[j])             # lowest card of the run
            i = j + 1
    return out


def _trick_winner(cards: List[Tuple[Seat, int]], trump: Optional[int]) -> Seat:
    """Winner of a complete 4-card trick. cards = [(seat, c52)] in play order."""
    lead_suit = _suit(cards[0][1])
    best_seat, best_c = cards[0]
    for seat, c in cards[1:]:
        cs = _suit(c)
        if trump is not None and cs == trump and _suit(best_c) != trump:
            best_seat, best_c = seat, c
        elif cs == _suit(best_c) and _rank(c) < _rank(best_c):
            best_seat, best_c = seat, c
    return best_seat


def _pbn(hands: Dict[Seat, frozenset]) -> str:
    """PBN of the REMAINING cards. Seat order N,E,S,W."""
    order = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]

    def one(cs):
        by = [[], [], [], []]
        for c in cs:
            by[_suit(c)].append(_rank(c))
        return ".".join("".join(_RANKS[r] for r in sorted(s)) for s in by)
    return "N:" + " ".join(one(hands[s]) for s in order)


_SIGNAL_SAME_CLASS = __import__("os").environ.get("BIQ_SIGNAL_SAME_CLASS", "1") == "1"


def _beats52(a: int, b: int, led: int, trump) -> bool:
    """Does card code a beat the winning card code b? (code = suit*13+rank,
    rank 0 = ace)."""
    sa, sb = a // 13, b // 13
    if sa == sb:
        return a < b
    return trump is not None and sa == trump


class AlphaMu:
    """Single-dummy declarer search. One instance per decision is fine; it owns
    a DDS handle and a small cache."""

    def __init__(self, trump: Optional[Suit], declarer: Seat,
                 dds: Optional[DDSolver] = None, depth: int = 2,
                 time_budget: float = 5.0, biq_seats=None,
                 defense_rollout_leaf: bool = False, vul=None,
                 signal_margin: float = 0.0, second_low_margin: float = 0.0):
        self.trump_suit = trump
        self.trump = None if trump is None else trump.value
        self.strain = _strain_i(trump)
        self.declarer = declarer
        self.dummy = declarer.partner()
        # Seat(s) biq controls and plays a CONSISTENT line for (MAX). Declaring:
        # {declarer, dummy}. Defending: biq's one seat. Every OTHER seat plays
        # DD-best for its OWN side at the leaves, so a hidden partner cooperates
        # and the opponents compete — for free, via the per-seat DDS solve.
        self.max_seats = set(biq_seats) if biq_seats is not None \
            else {declarer, self.dummy}
        # Objective = tricks for biq's SIDE (declarer tricks when declaring,
        # defensive tricks when defending).
        self.biq_ns = next(iter(self.max_seats)).is_ns()
        # Leaf with a REALISTIC partner: when DEFENDING, evaluate the horizon by
        # rolling out (partner = no-peek, opponents = DD) instead of the DDS
        # leaf (which plays the hidden partner double-dummy = a perfect partner).
        self._roll_leaf = defense_rollout_leaf \
            and self.biq_ns != declarer.is_ns()
        self._vul = vul
        self._roll_cache: Dict = {}
        # How far below the best a card may score and still be SIGNAL-tie-broken.
        # 0 = only exact ties (biq signals rarely/noisily). A small margin
        # (~0.1-0.2 tr) makes biq a RELIABLE signaller — it always plays the
        # convention card among near-equal spots — at a bounded trick cost.
        self.signal_margin = signal_margin
        # Second hand low (defence): how much better a lone A/K must score
        # than a duck before biq rises with it. 0 = off. See choose().
        self.second_low_margin = second_low_margin
        self.dds = dds or DDSolver()
        self.depth = depth
        self.time_budget = time_budget
        self._deadline = None
        self._leaf_cache: Dict[Tuple, int] = {}

    def _tick(self):
        if self._deadline is not None and time.time() > self._deadline:
            raise _Budget

    def _is_max(self, seat: Seat) -> bool:
        return seat in self.max_seats

    # ---- leaf: double-dummy declarer-future tricks for one world ----
    def _decl_future(self, hands: Dict[Seat, frozenset], leader: Seat) -> int:
        """Tricks the DECLARER side takes from here to the end of the hand,
        double-dummy, with `leader` on lead and no trick in progress."""
        key = (leader.value, _pbn(hands))
        cached = self._leaf_cache.get(key)
        if cached is not None:
            return cached
        self._tick()
        per_hand = len(hands[leader])
        if per_hand == 0:
            self._leaf_cache[key] = 0
            return 0
        if self._roll_leaf:
            # realistic-partner rollout: returns DECLARER tricks from here; biq
            # is defending (biq_ns != declarer), so biq's side = per_hand - decl.
            from . import defender_search
            decl_tr = defender_search.rollout_from(
                hands, leader, self.declarer, self.trump_suit, self._vul,
                self.dds, self._roll_cache)
            decl = per_hand - decl_tr
            self._leaf_cache[key] = decl
            return decl
        res = self.dds.solve(self.strain, leader.value, [], [_pbn(hands)],
                             solutions=1)
        toplay_tricks = max((t[0] for t in res.values()), default=0)
        decl = toplay_tricks if leader.is_ns() == self.biq_ns \
            else per_hand - toplay_tricks
        self._leaf_cache[key] = decl
        return decl

    # ---- MIN: a defender's double-dummy-best card in its own world ----
    def _defender_card(self, hands: Dict[Seat, frozenset], seat: Seat,
                       leader: Seat, trick52: List[int]) -> int:
        legal = _legal(hands[seat], _suit(trick52[0]) if trick52 else None)
        if len(legal) == 1:
            return legal[0]
        self._tick()
        res = self.dds.solve(self.strain, leader.value, list(trick52),
                             [_pbn(hands)], solutions=1)
        # DDS returns the best card(s) for the side to play (the defender):
        # most stubborn defence. Pick one that is actually legal here.
        for c52 in res:
            if c52 in legal:
                return c52
        return legal[0]

    # ---- core: play one trick across a world-group, then recurse ----
    def _play_trick(self, group: List[Tuple[int, Dict[Seat, frozenset]]],
                    leader: Seat, decl_tricks: int, depth: int,
                    max_choice: Optional[List[int]] = None) \
            -> Dict[int, float]:
        """Play a full trick for every world in `group` (which all share biq's
        information, so same leader/decl_tricks), splitting by observed defender
        cards at the end, and recurse. Returns {widx: expected total declarer
        tricks}. `max_choice`, if given, forces biq's plays this trick (used so
        the ROOT can fix its first card and read the value)."""
        # Per-world in-progress trick + working hands.
        wtrick: Dict[int, List[Tuple[Seat, int]]] = {w: [] for w, _ in group}
        whands: Dict[int, Dict[Seat, frozenset]] = {
            w: dict(h) for w, h in group}
        forced = list(max_choice) if max_choice else []
        fi = 0
        seat = leader
        for _step in range(4):
            if self._is_max(seat):
                # MAX: one card for the whole group (biq can't tell worlds apart).
                lead_suit = (_suit(wtrick[group[0][0]][0][1])
                             if wtrick[group[0][0]] else None)
                hand0 = whands[group[0][0]][seat]
                cands = _legal(hand0, lead_suit)
                if forced and fi < len(forced):
                    chosen = forced[fi]
                    fi += 1
                    cards_to_try = [chosen]
                elif len(cands) == 1:
                    cards_to_try = [cands[0]]
                else:
                    cards_to_try = _collapse(cands) if _COLLAPSE else cands
                if len(cards_to_try) == 1:
                    c = cards_to_try[0]
                    for w, _ in group:
                        wtrick[w].append((seat, c))
                        whands[w][seat] = whands[w][seat] - {c}
                else:
                    # branch: evaluate each candidate by continuing the trick,
                    # pick the one maximising mean declarer tricks over the group.
                    best_val, best_c = None, None
                    for c in cards_to_try:
                        sub = []
                        for w, _ in group:
                            hh = dict(whands[w])
                            hh[seat] = hh[seat] - {c}
                            sub.append((w, hh, wtrick[w] + [(seat, c)]))
                        vals = self._continue_trick(sub, seat.next(), leader,
                                                    decl_tricks, depth)
                        mean = sum(vals.values()) / len(vals)
                        if best_val is None or mean > best_val:
                            best_val, best_c, best_vals = mean, c, vals
                    return best_vals
                seat = seat.next()
            else:
                # MIN: each world's defender plays its own DD-best card.
                for w, _ in group:
                    tr = [c for _, c in wtrick[w]]
                    c = self._defender_card(whands[w], seat, leader, tr)
                    wtrick[w].append((seat, c))
                    whands[w][seat] = whands[w][seat] - {c}
                seat = seat.next()
        # trick complete for all worlds → resolve + split by observed cards
        return self._resolve_and_recurse(group, wtrick, whands, decl_tricks,
                                         depth)

    def _continue_trick(self, partial, seat, leader, decl_tricks, depth):
        """Finish a trick already partway through (after a MAX branch), for a
        list of (widx, hands, trick_so_far). Mirrors _play_trick's loop."""
        wtrick = {w: list(tr) for w, _, tr in partial}
        whands = {w: dict(h) for w, h, _ in partial}
        idx = {w: (w, whands[w]) for w, _, _ in partial}
        order = [(w, whands[w]) for w, _, _ in partial]
        while len(wtrick[order[0][0]]) < 4:
            if self._is_max(seat):
                lead_suit = (_suit(wtrick[order[0][0]][0][1])
                             if wtrick[order[0][0]] else None)
                hand0 = whands[order[0][0]][seat]
                cands = _legal(hand0, lead_suit)
                if len(cands) == 1:
                    c = cands[0]
                    for w, _ in order:
                        wtrick[w].append((seat, c))
                        whands[w][seat] = whands[w][seat] - {c}
                else:
                    best_val, best_vals = None, None
                    for c in (_collapse(cands) if _COLLAPSE else cands):
                        sub = [(w, {**whands[w], seat: whands[w][seat] - {c}},
                                wtrick[w] + [(seat, c)]) for w, _ in order]
                        vals = self._continue_trick(sub, seat.next(), leader,
                                                    decl_tricks, depth)
                        mean = sum(vals.values()) / len(vals)
                        if best_val is None or mean > best_val:
                            best_val, best_vals = mean, vals
                    return best_vals
                seat = seat.next()
            else:
                for w, _ in order:
                    tr = [c for _, c in wtrick[w]]
                    c = self._defender_card(whands[w], seat, leader, tr)
                    wtrick[w].append((seat, c))
                    whands[w][seat] = whands[w][seat] - {c}
                seat = seat.next()
        return self._resolve_and_recurse(order, wtrick, whands, decl_tricks,
                                         depth)

    def _resolve_and_recurse(self, group, wtrick, whands, decl_tricks, depth):
        # Split worlds by the defenders' observed cards (what biq actually sees);
        # within a split the trick winner is identical → shared next leader.
        splits: Dict[Tuple, List[Tuple[int, Dict[Seat, frozenset]]]] = \
            defaultdict(list)
        for w, _ in group:
            obs = tuple(c for s, c in wtrick[w] if not self._is_max(s))
            splits[obs].append((w, whands[w]))
        out: Dict[int, float] = {}
        for _obs, sub in splits.items():
            w0 = sub[0][0]
            winner = _trick_winner(wtrick[w0], self.trump)
            dt = decl_tricks + (1 if winner.is_ns() == self.biq_ns
                                else 0)
            if depth - 1 <= 0 or len(whands[w0][winner]) == 0:
                for w, hh in sub:
                    out[w] = dt + self._decl_future(hh, winner)
            else:
                res = self._play_trick(sub, winner, dt, depth - 1)
                out.update(res)
        return out

    # ---- entry point ----
    def choose(self, board: BoardState, seat: Seat,
               current_trick_cards: List[Card],
               worlds: List[Dict[Seat, frozenset]],
               tiebreak=None) -> Optional[Card]:
        """Pick biq's card for `seat` (must be declarer-side). `worlds` are full
        determinizations: dict[Seat]->frozenset(c52) of REMAINING cards, all
        consistent with biq's information (MAX seats identical across worlds)."""
        if seat not in self.max_seats or not worlds:
            return None
        lead_suit = current_trick_cards[0].suit.value if current_trick_cards \
            else None
        my = worlds[0][seat]
        cands = _legal(my, lead_suit)
        if len(cands) <= 1:
            return Card.from_code52(cands[0]) if cands else None
        runs = _runs(cands) if _COLLAPSE else {c: [c] for c in cands}
        if _COLLAPSE:
            cands = list(runs)

        leader = seat
        trick52: List[int] = []
        if current_trick_cards:
            leader = Seat((seat.value - len(current_trick_cards)) % 4)
            trick52 = [c.code52() for c in current_trick_cards]

        # Build the root group: each world carries hands with the in-progress
        # trick's cards already removed, plus the partial trick replayed so the
        # search continues mid-trick if biq is not on lead.
        group = [(i, dict(w)) for i, w in enumerate(worlds)]
        scored = []                      # (c52, mean) for every evaluated card
        best_c, best_val = None, None
        if _ORDER and self.depth > 1 and len(cands) > 2:
            # Cheap pass: one-trick lookahead + double-dummy leaf, at most a
            # third of the budget; then search best-first.
            self._deadline = time.time() + self.time_budget / 3
            quick = {}
            try:
                for c in cands:
                    partial = []
                    for i, w in group:
                        hh = dict(w)
                        hh[seat] = hh[seat] - {c}
                        tr = [(Seat((leader.value + k) % 4), cc)
                              for k, cc in enumerate(trick52)] + [(seat, c)]
                        partial.append((i, hh, tr))
                    v = self._continue_trick(partial, seat.next(), leader, 0, 1)
                    quick[c] = sum(v.values()) / len(v)
            except _Budget:
                pass
            cands = sorted(cands, key=lambda c: -quick.get(c, -1.0))
        self._deadline = time.time() + self.time_budget
        for c in cands:
            # force biq's `seat` card = c, replay the rest of this trick + search
            partial = []
            for i, w in group:
                hh = dict(w)
                hh[seat] = hh[seat] - {c}
                tr = [(Seat((leader.value + k) % 4), cc)
                      for k, cc in enumerate(trick52)] + [(seat, c)]
                partial.append((i, hh, tr))
            try:
                vals = self._continue_trick(partial, seat.next(), leader, 0,
                                            self.depth)
            except _Budget:
                import os as _os
                if _os.environ.get("BIQ_AMU_DEBUG"):
                    import sys as _sys
                    print(f"[amu] budget hit after {len(scored)}/{len(cands)} cards",
                          file=_sys.stderr, flush=True)
                break                # out of time — keep the best evaluated so far
            mean = sum(vals.values()) / len(vals)
            scored.append((c, mean))
            if best_val is None or mean > best_val:
                best_val, best_c = mean, c
        if best_c is None:
            return None
        # SECOND HAND LOW: a spot led, biq second to play, the search's best
        # is a lone A or K (no touching honour). The DD leaves let declarer
        # read every card, so they never credit the guess a duck leaves him.
        # Played out no-peek on 130 the reference probe positions, rising cost 0.9
        # tr when it scored < 0.05 better and 0.2-0.3 tr up to 0.65; it paid
        # only above ~0.65 (2026-10-07). Duck unless the A/K clears the margin.
        if self.second_low_margin > 0 and len(trick52) == 1 \
                and trick52[0] % 13 >= 4 and best_c // 13 == trick52[0] // 13 \
                and best_c % 13 <= 1 and best_c + 1 not in my \
                and not (best_c % 13 == 1 and best_c - 1 in my):
            lows = [(c, m) for c, m in scored
                    if c // 13 == best_c // 13 and c % 13 >= 5
                    and m >= best_val - self.second_low_margin]
            if lows:
                top = max(m for _, m in lows)
                ducks = [Card.from_code52(x) for c, m in lows
                         if m >= top - (self.signal_margin or 1e-9)
                         for x in runs.get(c, [c])]
                pick = tiebreak(ducks) if tiebreak is not None and \
                    len(ducks) > 1 else None
                return pick or max(ducks, key=lambda c: c.rank.value)
        # TIE-BREAK by signal: among cards that tie for the best value (no trick
        # cost), let the caller pick the one carrying the standard signal.
        if tiebreak is not None and best_val is not None:
            margin = self.signal_margin if self.signal_margin > 0 else 1e-9
            if _SIGNAL_SAME_CLASS:
                # The margin may swap one SPOT for another, never a card that
                # does something else: winning the trick instead of losing it,
                # an honour or ten for a spot, or (on lead) another suit. biq
                # ducked A84, A975, A32 for a signal (2026-10-05 the reference runs:
                # 41 costly signals against the reference's 12).
                led52 = trick52[0] // 13 if trick52 else None

                def _wins(x):
                    if not trick52:
                        return None
                    w = trick52[0]
                    for y in trick52[1:]:
                        if _beats52(y, w, led52, self.trump):
                            w = y
                    return _beats52(x, w, led52, self.trump)

                def _cls(x):
                    return (_wins(x), x % 13 <= 4,
                            x // 13 if not trick52 else None)
                bc = _cls(best_c)
                tied = [Card.from_code52(x) for c, m in scored
                        for x in runs.get(c, [c])
                        if m >= best_val - 1e-9
                        or (m >= best_val - margin and _cls(x) == bc)]
            else:
                tied = [Card.from_code52(x) for c, m in scored
                        if m >= best_val - margin for x in runs.get(c, [c])]
            if len(tied) > 1:
                pick = tiebreak(tied)
                if pick is not None:
                    return pick
        return Card.from_code52(best_c)
