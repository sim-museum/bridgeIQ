"""No-peek DEFENCE search — MC rollout with a REALISTIC (no-peek) partner.

The committed alpha-mu defence (backend.alphamu, biq_seats={seat}) is strong but
its DDS leaf plays the hidden PARTNER double-dummy — a perfect partner biq's real
(no-peek) partner can't deliver, so some plays backfire (live +0.75 tr/board vs
The reference, worse than declarer's −0.55). This evaluates biq's candidate card by
ROLLING OUT the rest of the deal with a realistic partner:

  * biq + partner play the ACTUAL no-peek policy (nopeek.decide, greedy);
  * the OPPONENTS (declarer + dummy) play DD-best — a strong, reference-like
    declarer that sees the sampled layout.

biq samples plausible declarer+partner layouts (a BELIEF from the play — voids,
counts, vacant places; never the real cards) and commits the card with the
lowest average declarer trick-count. No peeking, no strategy-fusion tells: the
partner is modelled as it really plays, not as an oracle.
"""
from __future__ import annotations
import time
from typing import Dict, List, Optional

from .models import Seat, Suit, Card, Hand, BoardState, Trick
from .dds import DDSolver
from .declarer_search import _c52, _winner, _sample_defenders

_ALL = (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)
_RANKS = "AKQJT98765432"          # index = rank.value (0=A .. 12=2)


def _strain_i(trump: Optional[Suit]) -> int:
    # DDS/BEN: 1=S,2=H,3=D,4=C,5=NT. Suit enum S=0,H=1,D=2,C=3.
    return 5 if trump is None else trump.value + 1


def _pbn(hands: Dict[Seat, List[Card]]) -> str:
    """PBN of the remaining cards, seat order N,E,S,W."""
    order = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]

    def one(cards):
        by = [[], [], [], []]
        for c in cards:
            by[c.suit.value].append(c.rank.value)
        return ".".join("".join(_RANKS[r] for r in sorted(s)) for s in by)
    return "N:" + " ".join(one(hands[s]) for s in order)


def _dd_card(dds: DDSolver, strain_i: int, hands: Dict[Seat, List[Card]],
             seat: Seat, leader: Seat, trick_cards: List[Card],
             cache: dict) -> Card:
    """DD-best card for `seat`, which (in the rollout) sees the whole sampled
    deal. `trick_cards` = cards played so far this trick, in lead order."""
    lead_suit = trick_cards[0].suit if trick_cards else None
    in_suit = [c for c in hands[seat] if lead_suit is not None
               and c.suit == lead_suit]
    legal = in_suit if in_suit else list(hands[seat])
    if len(legal) == 1:
        return legal[0]
    trick52 = tuple(_c52(c) for c in trick_cards)
    pbn = _pbn(hands)
    key = (leader.value, trick52, pbn)
    best = cache.get(key)
    if best is None:
        res = dds.solve(strain_i, leader.value, list(trick52), [pbn],
                        solutions=1)
        best = list(res.keys())
        cache[key] = best
    legal52 = {_c52(c): c for c in legal}
    for c52 in best:
        if c52 in legal52:
            return legal52[c52]
    return legal[0]


def _rollout(board: BoardState, seat: Seat, current_trick: List[Card],
             declarer: Seat, trump: Optional[Suit],
             world_hands: Dict[Seat, List[Card]], candidate: Card,
             dds: DDSolver, strain_i: int, cache: dict) -> int:
    """biq (`seat`) plays `candidate`, then the deal is played to the end with
    biq + partner on the no-peek policy and declarer + dummy on DD-best. Returns
    the DECLARER side's total trick count (lower = better defence)."""
    from . import nopeek
    decl_ns = declarer.is_ns()
    opp = {declarer, declarer.partner()}
    hands = {s: Hand(cards=list(world_hands[s])) for s in _ALL}
    rb = BoardState(board_number=1, dealer=declarer,
                    vulnerability=board.vulnerability, hands=hands,
                    auction=list(board.auction), contract=board.contract,
                    tricks=[Trick(cards=list(t.cards), leader=t.leader,
                                  winner=t.winner) for t in board.tricks])
    dt = sum(1 for t in rb.tricks if t.winner and t.winner.is_ns() == decl_ns)

    leader = Seat((seat.value - len(current_trick)) % 4) if current_trick \
        else seat
    trick = list(current_trick) + [candidate]
    rb.hands[seat].cards = [c for c in rb.hands[seat].cards
                            if not (c.suit == candidate.suit
                                    and c.rank == candidate.rank)]
    cur = seat.next()
    while True:
        rb.current_trick = Trick(cards=list(trick), leader=leader)
        while len(trick) < 4:
            if cur in opp:                       # strong declarer: DD-best
                hd = {s: rb.hands[s].cards for s in _ALL}
                card = _dd_card(dds, strain_i, hd, cur, leader, trick, cache)
            else:                                # biq + partner: no-peek policy
                card = nopeek.decide(rb, cur, list(trick), search=False)
            rb.hands[cur].cards = [c for c in rb.hands[cur].cards
                                   if not (c.suit == card.suit
                                           and c.rank == card.rank)]
            trick.append(card)
            cur = cur.next()
        w = _winner(trick, leader, trump)
        rb.tricks.append(Trick(cards=list(trick), leader=leader, winner=w))
        rb.current_trick = None
        dt += (w.is_ns() == decl_ns)
        if not any(rb.hands[s].cards for s in _ALL):
            break
        leader = cur = w
        trick = []
    return dt


def rollout_from(hands52: Dict[Seat, frozenset], leader: Seat, declarer: Seat,
                 trump: Optional[Suit], vul, dds: DDSolver, cache: dict) -> int:
    """Play a LEAF position (alpha-mu horizon) to the end and return the
    DECLARER side's trick count from here. biq's side (the defenders) play the
    no-peek policy; declarer + dummy play DD-best. `hands52` = remaining cards
    per seat (frozenset c52); `leader` is on lead at a trick boundary.

    This is alpha-mu's leaf with a REALISTIC partner instead of DDS's perfect
    one: keep the strong consistent biq search above the horizon, model the
    hidden partner as it really plays below it."""
    from . import nopeek
    from .models import Contract
    decl_ns = declarer.is_ns()
    opp = {declarer, declarer.partner()}
    hands = {s: Hand(cards=[Card.from_code52(x) for x in hands52[s]])
             for s in _ALL}
    # Reconstruct already-played cards into placeholder tricks so redact() sees
    # dummy as exposed (the void/count inference off these is approximate — a
    # leaf estimate; the remaining hands, which drive technique, are exact).
    in_hand = set()
    for s in _ALL:
        in_hand |= set(hands52[s])
    played = [Card.from_code52(x) for x in range(52) if x not in in_hand]
    placeholder = [Trick(cards=played[i:i + 4], leader=Seat.NORTH,
                         winner=_winner(played[i:i + 4], Seat.NORTH, trump))
                   for i in range(0, len(played) - 3, 4)]
    rb = BoardState(board_number=1, dealer=declarer, vulnerability=vul,
                    hands=hands, auction=[],
                    contract=Contract(level=1, suit=(trump or Suit.NOTRUMP),
                                      declarer=declarer),
                    tricks=placeholder)
    strain_i = _strain_i(trump)
    dt = 0
    cur, trick = leader, []
    while any(rb.hands[s].cards for s in _ALL):
        rb.current_trick = Trick(cards=list(trick), leader=leader)
        while len(trick) < 4:
            if cur in opp:
                hd = {s: rb.hands[s].cards for s in _ALL}
                card = _dd_card(dds, strain_i, hd, cur, leader, trick, cache)
            else:
                card = nopeek.decide(rb, cur, list(trick), search=False)
            rb.hands[cur].cards = [c for c in rb.hands[cur].cards
                                   if not (c.suit == card.suit
                                           and c.rank == card.rank)]
            trick.append(card)
            cur = cur.next()
        w = _winner(trick, leader, trump)
        rb.tricks.append(Trick(cards=list(trick), leader=leader, winner=w))
        rb.current_trick = None
        dt += (w.is_ns() == decl_ns)
        leader = cur = w
        trick = []
    return dt


def best_defense(board: BoardState, seat: Seat, current_trick: List[Card],
                 declarer: Seat, trump: Optional[Suit], legal: List[Card],
                 dds: DDSolver, samples: int = 6,
                 time_budget: float = 5.0) -> Optional[Card]:
    """Pick biq's defensive card with the lowest average declarer trick-count
    over sampled declarer+partner layouts (no-peek partner, DD opponents).
    `board` must be REDACTED (biq's view: own + dummy populated, the hidden two
    empty) — we sample those two. Returns a Card or None (sampling failed /
    budget exhausted before any candidate scored)."""
    dummy = declarer.partner()
    smp, hidden = _sample_defenders(board, seat, current_trick, declarer,
                                    samples, known_seats={seat, dummy})
    if not smp:
        return None
    strain_i = _strain_i(trump)
    cache: dict = {}
    deadline = time.time() + time_budget
    # Pre-build the sampled worlds once (shared across candidates).
    worlds = []
    for a in smp:
        w = {}
        for s in _ALL:
            if s in hidden:
                w[s] = [Card.from_code52(x) for x in a[s]]
            else:
                w[s] = list(board.hands[s].cards)
        worlds.append(w)

    best_c, best_v = None, None
    for cand in legal:
        if best_c is not None and time.time() > deadline:
            break                                # out of time — keep best so far
        total = 0
        for w in worlds:
            total += _rollout(board, seat, current_trick, declarer, trump,
                              w, cand, dds, strain_i, cache)
        avg = total / len(worlds)
        if best_v is None or avg < best_v:       # MINIMISE declarer tricks
            best_v, best_c = avg, cand
    return best_c
