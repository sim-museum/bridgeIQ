"""Opening leads by simulation.

The rule-based lead engine (`native_lead`) scores suits with fixed weights and
cannot see what the auction says about where the high cards are. This module:

1. samples the three hidden hands (partner, declarer, dummy) with the same
   bidder-consistent sampler the hybrid bidder uses (`bid_sim._Sampler`):
   every kept layout is one where biq's own rules make the calls those seats
   actually made;
2. solves each layout double-dummy for every card the leader holds;
3. picks the lead with the best average result for the defence, measured in
   IMPs against the rule engine's lead, so the conventional card stays unless
   another lead is genuinely better. Within the chosen suit the conventional
   card (top of sequence, fourth best, ...) is kept when it is as good.

BIQ_LEAD_SIM=0 turns it off; BIQ_LEAD_SIM_SAMPLES (48) and
BIQ_LEAD_SIM_BUDGET seconds (8.0) size it.
"""
from __future__ import annotations

import os
import random
import time
from typing import Optional, Tuple

from .models import BoardState, Card, Seat, Suit

ENABLED = os.environ.get("BIQ_LEAD_SIM", "1") == "1"
N_SAMPLES = int(os.environ.get("BIQ_LEAD_SIM_SAMPLES", "48"))
BUDGET = float(os.environ.get("BIQ_LEAD_SIM_BUDGET", "8.0"))
MIN_SAMPLES = 16


def set_enabled(on: bool) -> None:
    """Preference switch (File > Preferences > Mouse & Play)."""
    global ENABLED
    ENABLED = bool(on)
_DDS_STRAIN = {Suit.SPADES: 1, Suit.HEARTS: 2, Suit.DIAMONDS: 3,
               Suit.CLUBS: 4, Suit.NOTRUMP: 5}


def choose_opening_lead(board: BoardState, seat: Seat, rule_card: Card,
                        system=None) -> Optional[Tuple[Card, str]]:
    """Best opening lead for `seat`, or None to keep the rule engine's."""
    if not ENABLED or board.contract is None:
        return None
    try:
        return _choose(board, seat, rule_card, system)
    except Exception:
        return None


def _choose(board, seat, rule_card, system):
    from . import bid_sim, native_bidder as nb, native_lead
    from .bidding_systems import get_system
    from .dds import DDSolver
    hand = board.hands[seat]
    if len(hand.cards) != 13 or len(board.auction) < 4:
        return None
    system = system or get_system("SAYC")
    t0 = time.time()
    state = nb.parse_auction(seat, board.dealer, list(board.auction),
                             vulnerability=board.vulnerability)
    seed = bid_sim._seed(state, hand) ^ 0x1EAD
    sampler = bid_sim._Sampler(state, hand, system, random.Random(seed),
                               t0 + BUDGET * 3)   # safety cap only
    layouts = sampler.sample(N_SAMPLES)
    if len(layouts) < MIN_SAMPLES:
        return None
    mine = [bid_sim._code(c) for c in hand.cards]
    pbns = []
    for lay in layouts:
        full = dict(lay)
        full[seat] = mine
        pbns.append(bid_sim._pbn(full))
    c = board.contract
    res = DDSolver().solve(_DDS_STRAIN[c.suit], seat.value, [], pbns,
                           solutions=3)
    n = len(pbns)
    dbl = 2 if getattr(c, "redoubled", False) else (1 if getattr(c, "doubled", False) else 0)
    vul = bid_sim._side_vul(board.vulnerability, c.declarer)

    def defence_score(def_tricks: int) -> int:
        return -bid_sim.duplicate_score(c.level, c.suit, 13 - def_tricks, vul, dbl)

    scores = {}
    for code, tricks in res.items():
        if code not in mine or len(tricks) != n:
            continue
        scores[code] = [defence_score(t) for t in tricks]
    rc = bid_sim._code(rule_card)
    if rc not in scores or len(scores) < 2:
        return None
    base = scores[rc]
    gain = {code: sum(bid_sim._imp(a - b) for a, b in zip(sc, base)) / n
            for code, sc in scores.items()}
    tricks_avg = {code: sum(res[code]) / n for code in scores}
    best = max(gain, key=lambda k: (gain[k], tricks_avg[k]))
    if gain[best] <= 0.05:
        return None                                   # keep the rule lead
    # Conventional card within the chosen suit when it is as good.
    suit = best // 13
    in_suit = sorted([x for x in mine if x // 13 == suit], key=lambda x: x % 13)
    try:
        conv = native_lead._pick_card_from_suit(
            [bid_sim._CARDS[x] for x in in_suit],
            c.suit != Suit.NOTRUMP,
            trump_suit=c.suit if c.suit != Suit.NOTRUMP else None)
        cc = bid_sim._code(conv)
        if cc in gain and gain[cc] >= gain[best] - 0.05:
            best = cc
    except Exception:
        pass
    card = bid_sim._CARDS[best]
    why = (f"Simulated lead: {n} layouts consistent with the auction, "
           f"double-dummy; {card.suit.to_char()}{'AKQJT98765432'[card.rank]} "
           f"averages {tricks_avg[best]:.2f} defensive tricks, "
           f"{gain[best]:+.1f} IMP over the table lead "
           f"({rule_card.suit.to_char()}{'AKQJT98765432'[rule_card.rank]}).")
    return card, why
