"""Reading PARTNER's defensive signals — the complement to backend.signals.

biq EMITS standard signals (backend.signals); this lets it LISTEN. Reading
partner's signals is legitimate information (partner tells you publicly — not
peeking) and genuinely sharpens defence. The method is convention-INVERSION:
biq knows the convention it expects partner to use (the same standard attitude/
count it emits), so a candidate partner hand is CONSISTENT only if, played under
that convention, it would have produced the very card partner actually played.

  * read(board, seat, ...)         -> the readable signal records from partner's
                                      completed plays.
  * consistent(records, hand, ...) -> True if a hand reproduces every signal
                                      (the HARD FILTER for the sampler).
  * mis_signals(records, ...)      -> the signals partner's ACTUAL hand would
                                      NOT have produced (end-of-hand teaching
                                      feedback + the auto-disable counter).

Only CLEAR free signals are read: partner followed suit, did not win, played a
SPOT (not an honour), and had a choice — attitude when our side led the suit,
count when an opponent did. Forced cards carry no signal, so they constrain
nothing.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from .models import Seat, Suit, Card, BoardState

_HONOUR = 3   # rank.value: A=0,K=1,Q=2,J=3 honours; T=4; spots 5..12


def _c52(c: Card) -> int:
    return c.suit.value * 13 + c.rank.value


def _suit_i(c52: int) -> int:
    return c52 // 13


def _rank_i(c52: int) -> int:
    return c52 % 13


def _winner_pos(cards: List[Card], trump: Optional[Suit]) -> int:
    wi, wc, lead = 0, cards[0], cards[0].suit
    for i, c in enumerate(cards[1:], 1):
        c_t = trump is not None and c.suit == trump
        w_t = trump is not None and wc.suit == trump
        if c_t and not w_t:
            wi, wc = i, c
        elif c_t and w_t and c.rank.value < wc.rank.value:
            wi, wc = i, c
        elif not c_t and not w_t and c.suit == lead \
                and c.rank.value < wc.rank.value:
            wi, wc = i, c
    return wi


def read(board: BoardState, seat: Seat, declarer: Seat,
         trump: Optional[Suit]) -> List[dict]:
    """Readable signal records from PARTNER's completed plays. Each:
    {trick, suit, attitude(bool), card52, played} where `played` maps each later
    trick index to the c52 partner played (to reconstruct partner's hand at the
    signal)."""
    partner = seat.partner()
    # what partner played, per completed trick
    pplayed: Dict[int, int] = {}
    rows = []
    for i, t in enumerate(board.tricks):
        if not t.cards or len(t.cards) < 4:
            continue
        ppos = (partner.value - t.leader.value) % 4
        pcard = t.cards[ppos]
        pplayed[i] = _c52(pcard)
        rows.append((i, t, pcard))
    out = []
    for i, t, pcard in rows:
        led = t.cards[0].suit
        if pcard.suit != led:
            continue                                   # discard — suit-pref TODO
        if pcard.rank.value <= _HONOUR:
            continue                                   # honour — not a spot signal
        if _winner_pos(t.cards, trump) == (partner.value - t.leader.value) % 4:
            continue                                   # partner won — not a signal
        attitude = t.leader.is_ns() == seat.is_ns()    # our suit -> attitude
        from . import signals as _signals
        if _signals._SELECTIVE and any(
                tt.cards and tt.cards[0].suit == led for tt in board.tricks[:i]):
            continue                                   # first round only
        later = {j: c for j, c in pplayed.items() if j >= i}
        ppos = (partner.value - t.leader.value) % 4
        before = [_c52(c) for c in t.cards[:ppos]]
        # What partner could see of the suit's A/K apart from its own hand:
        # dummy's holding then, and every card played before its signal.
        seen = {_c52(c) for tt in board.tricks[:i] for c in tt.cards} | set(before)
        dummy = declarer.partner()
        dh = getattr(board, "hands", None) or {}
        if dummy != partner and dh.get(dummy) is not None:
            seen |= {_c52(c) for c in dh[dummy].cards}
            for tt in board.tricks[i:]:
                if tt.cards and len(tt.cards) == 4:
                    seen.add(_c52(tt.cards[(dummy.value - tt.leader.value) % 4]))
            dummy_known = True
        else:
            dummy_known = False
        out.append({"trick": i, "suit": led.value, "attitude": attitude,
                    "card52": _c52(pcard), "played": later, "before": before,
                    "trump": None if trump is None else trump.value,
                    "lead": _c52(t.cards[0]), "seen": seen,
                    "dummy_known": dummy_known})
    return out


def _could_win(rec: dict, in_suit: list) -> bool:
    """Could partner have WON the trick when it played (so its card was a real
    win-or-duck decision, not a pure signal)? Matches the emitter, which only
    signals deterministically when it cannot win."""
    led, tr = rec["suit"], rec["trump"]
    before = rec["before"]
    if tr is not None and led != tr and any(_suit_i(c) == tr for c in before):
        return False                       # ruffed before us — following can't win
    best = min((_rank_i(c) for c in before if _suit_i(c) == led), default=99)
    return any(_rank_i(c) < best for c in in_suit)   # we hold a higher card


def _convention_card(rec: dict, hand_at_trick: set) -> Optional[int]:
    """The c52 biq's convention would play from `hand_at_trick` for `rec`, or
    None if there was no free choice (so nothing to read)."""
    suit = rec["suit"]
    in_suit = [c for c in hand_at_trick if _suit_i(c) == suit]
    if _could_win(rec, in_suit):
        return None                          # was a win-or-duck choice, not a signal
    spots = [c for c in in_suit if _rank_i(c) > _HONOUR + 1] or in_suit
    if len(spots) < 2:
        return None
    from . import signals
    udca = signals.is_udca()                                 # match the emitter
    if rec["attitude"]:
        lead = rec.get("lead")
        like = signals.attitude_like(
            None if lead is None else _rank_i(lead),
            [_rank_i(c) for c in in_suit], rec["trump"] is not None)
        high = (like != udca)
    else:
        if signals._SELECTIVE and rec.get("dummy_known"):
            seen = rec["seen"] if signals._COMMON_VIEW else rec["seen"] | set(hand_at_trick)
            hidden = sum(1 for r in (0, 1) if suit * 13 + r not in seen)
            if not signals.count_wanted(hidden):
                return sorted(spots, key=_rank_i)[-1]       # no signal: lowest
        high = ((len(in_suit) % 2 == 0) != udca)             # count: even -> high
        if signals._COMMON_VIEW and high and len(rec.get("before", [])) < 3 \
                and any(_rank_i(c) != 5 for c in spots):
            spots = [c for c in spots if _rank_i(c) != 5]    # keep the 9
    spots = sorted(spots, key=_rank_i)                       # high card first
    return spots[0] if high else spots[-1]


def is_clear_spot(rec: dict, hand_at_trick: set) -> bool:
    """PLAYBELIEF-1 sprint 4: was the card played in `rec` clearly high or clearly low in
    this holding, i.e. the highest or the lowest of the spots (honours aside) it held in the
    suit? Only such a card says something about the holding; a middle spot can be the
    convention card of several holdings, so it is not read."""
    suit = rec["suit"]
    in_suit = [c for c in hand_at_trick if _suit_i(c) == suit]
    spots = [c for c in in_suit if _rank_i(c) > _HONOUR + 1] or in_suit
    if rec["card52"] not in spots or len(spots) < 3:
        return True                          # two spots: either one is high or low
    ranks = sorted(_rank_i(c) for c in spots)
    return _rank_i(rec["card52"]) in (ranks[0], ranks[-1])


def _hand_at(rec: dict, remaining: set) -> set:
    """Partner's holding when it gave `rec`: cards still held now plus every card
    partner has played from the signal trick onward."""
    return set(remaining) | set(rec["played"].values())


def consistent(records: List[dict], remaining: set) -> bool:
    """HARD FILTER: does a partner hand (c52 set of remaining cards) reproduce
    every readable signal under biq's convention?"""
    for rec in records:
        conv = _convention_card(rec, _hand_at(rec, remaining))
        if conv is not None and conv != rec["card52"]:
            return False
    return True


def mis_signals(records: List[dict], actual_remaining: set) -> List[dict]:
    """Signals partner's ACTUAL hand would NOT have produced under the
    convention, judged by direction (high vs low) — end-of-hand teaching
    feedback / auto-disable counter. Each: {trick, suit, played52, expected52}."""
    out = []
    for rec in records:
        hand = _hand_at(rec, actual_remaining)
        conv = _convention_card(rec, hand)
        if conv is None or conv == rec["card52"]:
            continue
        # Judge the DIRECTION (lowest spot = low / discourage / odd, any higher
        # spot = high), not the exact card: from 10-8-6-4 the 6 is as clear a
        # high-low as the 8, and biq itself keeps a spot the play needs.
        in_suit = [c for c in hand if _suit_i(c) == rec["suit"]]
        spots = [c for c in in_suit if _rank_i(c) > _HONOUR + 1] or in_suit
        lowest = max(spots, key=_rank_i)
        if (conv == lowest) == (rec["card52"] == lowest):
            continue
        out.append({"trick": rec["trick"], "suit": rec["suit"],
                    "played52": rec["card52"], "expected52": conv})
    return out
