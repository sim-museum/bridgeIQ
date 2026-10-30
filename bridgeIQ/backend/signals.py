"""Defensive SIGNALLING — standard (textbook) attitude / count / suit-preference.

A good player reads a defender's spot cards as a running commentary. biq's no-peek
defence used to play the LOWEST card whenever it couldn't win (signalling was a
TODO), so its carding told partner nothing — the clearest "this is a bot" tell.
This picks the signalling card AMONG TRICK-EQUIVALENT candidates, so it costs no
tricks while making the play read like a real defender:

  * ATTITUDE on partner's suit (partner led or is winning): a HIGH spot
    ENCOURAGES (I have something / want it continued), a LOW spot discourages.
  * COUNT when following an opponent's suit without trying to win: high spot =
    even remaining, low spot = odd (present count).
  * SUIT-PREFERENCE / attitude on a DISCARD (mixed suits): pitch in the suit you
    care least about, the rank giving Lavinthal suit-preference for the others.

All "standard signals" (high = encourage / even) — the most universally read, in
keeping with biq's textbook-not-eccentric philosophy. The single entry point is
choose_signal_card(); per-seat carding and the alpha-mu tie-break both call it.
"""
from __future__ import annotations
import os
from typing import List, Optional

from .models import Seat, Suit, Rank, Card, BoardState

_HONOUR = 3   # rank.value: A=0,K=1,Q=2,J=3 are honours; T=4; spots are 5..12

# Signalling CONVENTION (the config setting every serious app exposes). False =
# STANDARD (high spot encourages / high-low shows even count); True = UPSIDE-DOWN
# (UDCA: low spot encourages / low-high shows even). The emitter AND the reader
# (signal_read) both consult this, so biq's signals and its reading of partner
# always agree. Set from the GUI preference via set_convention().
_UDCA = os.environ.get("BIQ_SIGNAL_UDCA", "0") == "1"

# Signalling ON/OFF (GUI preference "Play defensive signals"; env BIQ_SIGNALLING).
# OFF = biq never spends a card on a signal: choose_signal_card() returns the
# plain lowest card, nopeek skips its signal-only shortcuts, the alpha-mu
# signal margin is 0 (no near-tie widening) and partner-signal reading is off.
# Use this when the signal must never take precedence over winning a trick.
_ENABLED = os.environ.get("BIQ_SIGNALLING", "1") == "1"


def set_convention(udca: bool) -> None:
    global _UDCA
    _UDCA = bool(udca)


def is_udca() -> bool:
    return _UDCA


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = bool(enabled)


def is_enabled() -> bool:
    return _ENABLED


def lowest_card(cands: List[Card]) -> Card:
    """Plain no-signal play: the lowest-ranking candidate (a discard pitches
    the lowest card of the longest candidate suit)."""
    by: dict = {}
    for c in cands:
        by.setdefault(c.suit, []).append(c)
    suit = max(by, key=lambda s: (len(by[s]), -s.value))
    return max(by[suit], key=lambda c: c.rank.value)      # rank.value 12 = the 2


def _winner_seat(trick: List[Card], leader: Seat, trump: Optional[Suit]) -> Seat:
    wi, wc, lead = 0, trick[0], trick[0].suit
    for i, c in enumerate(trick[1:], 1):
        c_t = trump is not None and c.suit == trump
        w_t = trump is not None and wc.suit == trump
        if c_t and not w_t:
            wi, wc = i, c
        elif c_t and w_t and c.rank.value < wc.rank.value:
            wi, wc = i, c
        elif not c_t and not w_t and c.suit == lead \
                and c.rank.value < wc.rank.value:
            wi, wc = i, c
    return Seat((leader.value + wi) % 4)


# Never signal with an honour or the ten: with no spot to spend, play the
# lowest card. K-T under declarer's ace used to "signal" with the KING (count:
# even = high), throwing away a trick; live matches showed 22 such cards.
_SAFE = os.environ.get("BIQ_SIGNAL_SAFE", "1") == "1"


def _hl(cands: List[Card], high: bool) -> Card:
    """Among `cands`, return the high or low SPOT (keep honours back when there
    is a spot to spend). rank.value: 0=A (high) .. 12=2 (low)."""
    spots = [c for c in cands if c.rank.value > _HONOUR + 1]
    if not spots:
        if _SAFE:
            return max(cands, key=lambda c: c.rank.value)   # lowest card
        spots = list(cands)
    spots = sorted(spots, key=lambda c: c.rank.value)   # high card first
    return spots[0] if high else spots[-1]


def _like_suit(suit_cards: List[Card], trump: Optional[Suit]) -> bool:
    """Do we want partner to continue this suit? Encourage when we hold an
    honour to contribute, or (in a trump contract) a doubleton to ruff."""
    if any(c.rank.value <= _HONOUR for c in suit_cards):
        return True
    return trump is not None and len(suit_cards) == 2


def _discard_signal(cands: List[Card], board: BoardState, seat: Seat,
                    trump: Optional[Suit]) -> Card:
    """Mixed-suit candidates (a discard): pitch in the suit we care least about
    (no honour, longest), giving Lavinthal suit-preference within it — a HIGH
    pitch points to the higher-ranking of the other suits, LOW to the lower."""
    by: dict = {}
    for c in cands:
        by.setdefault(c.suit, []).append(c)
    # prefer a suit with no honour; among those the longest (most expendable)
    def honourless(s):
        return not any(c.rank.value <= _HONOUR
                       for c in board.hands[seat].cards if c.suit == s)
    suits = sorted(by, key=lambda s: (not honourless(s), -len(by[s]), s.value))
    suit = suits[0]
    group = sorted(by[suit], key=lambda c: c.rank.value)        # high → low
    # Suit-preference: high pitch = like the higher of the OTHER two side suits.
    others = sorted((s for s in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS,
                                 Suit.CLUBS) if s != suit and s != trump),
                    key=lambda s: s.value)
    if len(others) >= 2:
        hi_like = _like_suit([c for c in board.hands[seat].cards
                              if c.suit == others[0]], trump)
        lo_like = _like_suit([c for c in board.hands[seat].cards
                              if c.suit == others[1]], trump)
        if hi_like and not lo_like:
            return group[0]            # high spot → points to higher suit
        if lo_like and not hi_like:
            return group[-1]           # low spot → points to lower suit
    return group[-1]                   # default: low (discouraging the pitch suit)


# SELECTIVE signals (the reference's carding, and the textbook advice "give count
# only if partner needs it"): count only while the ace or king of the suit is
# out of the signaller's sight, so partner may hold it and face a hold-up
# decision; attitude only on our own side's lead, and "encourage" only with the
# honour that matters opposite the card partner led. Otherwise play the lowest
# spot, which then means "no signal". signal_read uses the same two helpers.
_SELECTIVE = os.environ.get("BIQ_SIGNAL_SELECTIVE", "1") == "1"
# The reference's finer carding, measured with scripted probe deals (tools/
# qplus_probe.py, Oct 2026): no doubleton "ruff" signal; after partner's K the
# J encourages too (from J-x or J-x-x, not J-x-x-x); count is given while the
# suit's A or K is out of the COMMON view (dummy and played cards), so an
# honour in the signaller's own hand still calls for count; a high count card
# keeps the 9 back (9-7 -> 7, 9-7-5-4 -> 7) except in fourth seat (9-6 -> 9).
_COMMON_VIEW = os.environ.get("BIQ_SIGNAL_COMMON_VIEW", "1") == "1"


def attitude_like(lead_rank: Optional[int], ranks: List[int],
                  trump_contract: bool) -> bool:
    """Encourage partner's lead? `lead_rank` is the rank.value partner led (A=0)
    or None, `ranks` my rank.values in the suit. Selective: after an honour
    lead only a higher honour or the next one down counts (A led: the K; K led:
    the A or Q; Q/J/T led: an honour above it); after a spot lead any of A/K/Q.
    A doubleton still asks for a ruff in a trump contract."""
    if trump_contract and len(ranks) == 2 and not (_COMMON_VIEW and _SELECTIVE):
        return True
    if not _SELECTIVE or lead_rank is None:
        return any(r <= _HONOUR for r in ranks)
    if lead_rank == 0:
        return 1 in ranks
    if lead_rank == 1:
        if _COMMON_VIEW and 3 in ranks and len(ranks) <= 3:
            return True
        return 0 in ranks or 2 in ranks
    if lead_rank <= 4:
        return any(r < lead_rank and r <= 2 for r in ranks)
    return any(r <= 2 for r in ranks)


def count_wanted(hidden_ak: int) -> bool:
    """Give count? Only while partner may hold the ace or king (`hidden_ak` =
    how many of the two the signaller cannot see)."""
    return not _SELECTIVE or hidden_ak > 0


def _hidden_ak(board: BoardState, seat: Seat, trick: List[Card],
               declarer: Seat, led: Suit) -> int:
    """How many of the led suit's A and K the signaller cannot see: not in its
    own hand, not in dummy, not played to an earlier trick or this one."""
    seen = set() if _COMMON_VIEW else {c.rank.value for c in board.hands[seat].cards if c.suit == led}
    dummy = declarer.partner()
    if dummy != seat and board.hands.get(dummy) is not None:
        seen |= {c.rank.value for c in board.hands[dummy].cards if c.suit == led}
    for t in board.tricks:
        seen |= {c.rank.value for c in t.cards if c.suit == led}
    seen |= {c.rank.value for c in trick if c.suit == led}
    return sum(1 for r in (0, 1) if r not in seen)


def choose_signal_card(cands: List[Card], board: BoardState, seat: Seat,
                       trick: List[Card], declarer: Seat,
                       trump: Optional[Suit]) -> Card:
    """Among TRICK-EQUIVALENT candidates `cands`, return the one carrying the
    standard signal. Same-suit follows → attitude (partner's suit) or count
    (opponent's suit); mixed suits / on a discard → suit-preference."""
    if len(cands) <= 1:
        return cands[0]
    if not _ENABLED:
        return lowest_card(cands)
    pos = len(trick)
    if pos == 0 or len({c.suit for c in cands}) > 1:
        return _discard_signal(cands, board, seat, trump)
    led = trick[0].suit
    leader = Seat((seat.value - pos) % 4)
    winner = _winner_seat(trick, leader, trump)
    suit_cards = [c for c in board.hands[seat].cards if c.suit == led]
    if _SELECTIVE:
        if any(t.cards and t.cards[0].suit == led for t in board.tricks):
            return _hl(cands, high=False)    # signals are first-round only
        if leader.is_ns() == seat.is_ns():                # ATTITUDE: our lead
            like = attitude_like(trick[0].rank.value,
                                 [c.rank.value for c in suit_cards],
                                 trump is not None)
            return _hl(cands, high=(like != _UDCA))
        if not count_wanted(_hidden_ak(board, seat, trick, declarer, led)):
            return _hl(cands, high=False)                 # no signal: lowest
        even = (len(suit_cards) % 2 == 0)
        high = even != _UDCA
        if _COMMON_VIEW and high and pos < 3:                     # 4th seat plays the 9
            keep9 = [c for c in cands if c.rank.value != 5]          # 5 = the nine
            if keep9 and any(c.rank.value > _HONOUR + 1 for c in keep9):
                return _hl(keep9, high=True)
        return _hl(cands, high=high)
    partner_context = leader.is_ns() == seat.is_ns() \
        or winner.is_ns() == seat.is_ns()
    if partner_context:                       # ATTITUDE
        like = _like_suit(suit_cards, trump)
        return _hl(cands, high=(like != _UDCA))          # UDCA flips encourage
    even = (len(suit_cards) % 2 == 0)                     # COUNT
    return _hl(cands, high=(even != _UDCA))               # std: even=high spot
