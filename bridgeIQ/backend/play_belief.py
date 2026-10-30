"""PLAYBELIEF-1: how likely a hidden hand was to make the plays seen, card by card.

Shared by the Monte-Carlo declarer (backend.nopeek: weights its sampled layouts) and the
analysis view (ui.teaching_view: weights its posterior and shows the conclusions). Cards are
0-51 ids, suit * 13 + rank value (ace = 0).

Restricted choice: a hand that follows suit with an honour while holding n cards equivalent
to it (same suit, every rank in between held or already played) chose that one of n, so a
layout with n equivalents explains the card 1/n as well as one where it was the only one.
"""
from __future__ import annotations

import os
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .models import Seat

Play = Tuple[Seat, int, Optional[bool]]  # (who, card id, followed suit; None = led)
HONOUR_MAX = 4                         # rank values 0..4 = A K Q J T


def plays_so_far(board, trick: Optional[Sequence] = None, seat: Optional[Seat] = None) -> List[Play]:
    """Every card played so far, in order, with who played it and whether it followed suit.
    `trick` (cards of the current trick) and `seat` (the player to act) add the current trick
    when the board's own current_trick is not filled in."""
    out: List[Play] = []
    tricks = [(t.leader, list(t.cards)) for t in board.tricks if t.cards]
    cur = getattr(board, "current_trick", None)
    if trick:
        lead = Seat((seat.value - len(trick)) % 4) if seat is not None else cur.leader
        tricks.append((lead, list(trick)))
    elif cur is not None and getattr(cur, "cards", None):
        tricks.append((cur.leader, list(cur.cards)))
    for leader, cards in tricks:
        for i, c in enumerate(cards):
            out.append((Seat((leader.value + i) % 4), c.suit.value * 13 + c.rank.value,
                        None if i == 0 else c.suit == cards[0].suit))
    return out


def equivalents(card: int, holding: Set[int], gone: Set[int]) -> int:
    """Cards in `holding` equivalent to `card`, the card itself included."""
    su, r = card // 13, card % 13
    n = 1
    for step in (-1, 1):
        k = r + step
        while 0 <= k < 13:
            c = su * 13 + k
            if c in holding:
                n += 1
            elif c not in gone:
                break
            k += step
    return n


def restricted_choice(plays: Iterable[Play], seat: Seat, remaining: Set[int]) -> Tuple[float, list]:
    """The factor for `seat`'s honour follows given its `remaining` cards in one layout, and
    the cards concerned [(card, n)] where it chose one of n equivalents."""
    plays = list(plays)
    hold = set(remaining) | {c for who, c, _ in plays if who == seat}
    gone: Set[int] = set()
    w, notes = 1.0, []
    for who, c, followed in plays:
        if who == seat:
            if followed and c % 13 <= HONOUR_MAX:
                n = equivalents(c, hold, gone)
                if n > 1:
                    w /= n
                    notes.append((c, n))
            hold.discard(c)
        gone.add(c)
    return w, notes


HONOUR_DISCARD_MISS = float(os.environ.get("BIQ_PLAY_HDISC_MISS", "0.5"))  # an honour discarded
# although the hand held the card one above (biq's value, set by its own A/B)


def honour_discard(plays: Iterable[Play], seat: Seat, remaining: Set[int]) -> Tuple[float, list]:
    """The factor for `seat`'s honour discards given its `remaining` cards in one layout: a
    discarded K, Q, J or T is the top of what the hand held there, so a layout in which the hand
    still held the card one above at that moment explains the discard less well (x
    HONOUR_DISCARD_MISS: falsecards happen). Returns the
    factor and the discards concerned [(card, card above)]."""
    plays = list(plays)
    hold = set(remaining) | {c for who, c, _ in plays if who == seat}
    w, notes = 1.0, []
    for who, c, followed in plays:
        if who != seat:
            continue
        hold.discard(c)
        if followed is False and 1 <= c % 13 <= HONOUR_MAX and (c - 1) in hold:
            w *= HONOUR_DISCARD_MISS
            notes.append((c, c - 1))
    return w, notes


def last_play_note(plays: Sequence[Play], hidden: Iterable[Seat]) -> Optional[str]:
    """A one-line reading of the latest telling card a hidden hand played (an honour followed,
    or a discard), for the analysis view; spot cards that followed suit are skipped."""
    hidden = set(hidden)
    for who, c, followed in reversed(list(plays)):
        if who not in hidden:
            continue
        name = "AKQJT98765432"[c % 13]
        if followed and c % 13 <= HONOUR_MAX:
            return (f"{who.name.title()} followed with the {name}: if it also holds the card next to"
                    f" it, it had a choice (restricted choice), so it is less likely to hold it")
        if followed is False:
            return f"{who.name.title()} discarded the {name} (out of the suit led)"
    return None


LEAD_CARD_MISS = 0.3     # the rules would lead another card of that suit from this hand
LEAD_SUIT_MISS = 0.6     # the rules would lead another suit from this hand


def lead_factor(board, leader_original: Set[int]) -> float:
    """How well a layout's opening-leader hand explains the opening lead seen, by biq's own lead
    rules (backend.native_lead): x LEAD_SUIT_MISS when the rules would lead another suit, x
    LEAD_CARD_MISS when they would lead another card of the suit led. 1.0 without a lead or
    without the auction."""
    if not board.tricks or not board.tricks[0].cards or board.contract is None:
        return 1.0
    from .models import Card, Hand, Rank, Suit
    from . import native_lead
    lead = board.tricks[0].cards[0]
    try:
        hand = Hand(cards=[Card(Suit(c // 13), Rank(c % 13)) for c in sorted(leader_original)])
        dec = native_lead.select_opening_lead(hand, board.contract, list(board.auction or []),
                                              board.dealer, board.tricks[0].leader,
                                              board.vulnerability)
    except Exception:
        return 1.0
    if dec.card.suit != lead.suit:
        return LEAD_SUIT_MISS
    return 1.0 if dec.card.rank == lead.rank else LEAD_CARD_MISS
