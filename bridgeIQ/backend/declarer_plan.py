"""No-peek DECLARER planner — global declarer-side play, never peeking.

When biq's side declares, it legitimately sees BOTH its own hand and dummy
(full information for the 26 declarer-side cards); only the defenders' 26 cards
are hidden. So the declarer can PLAN across both hands — manage entries, unblock,
cash in the right order, get to the hand that holds the winners — without ever
looking at a hidden card. Per-seat no-peek can't do this (each seat decides in
isolation), which is the bulk of its declarer gap vs the reference.

This module plans the declarer-side LEAD. It is consulted by backend.nopeek for
declarer-side seats on lead, after trumps are drawn; it returns a card to play
or None to fall through to the per-seat technique.

Piece 1 (here): CROSS TO THE WINNING HAND. When the hand on lead has no
guaranteed winner of its own to cash but the PARTNER hand does, lead a low card
in a suit partner can win, so partner takes the lead and its winners can be run.
This is the cross-hand capability per-seat play lacks; it is used as a smarter
default than leading a passive low card into nothing. Cashing this hand's OWN
winners is left to the per-seat cash-boss logic (cashing order/establishment
interact subtly and a naive global cash hurt).
"""
from __future__ import annotations
from typing import List, Optional

from .models import Seat, Suit, Card, BoardState
from .cardplay_planner import is_boss_card


def _suit_len(hand_cards: List[Card], suit: Suit) -> int:
    return sum(1 for c in hand_cards if c.suit == suit)


def plan_lead(board: BoardState, seat: Seat,
              declarer: Seat, trump: Optional[Suit]) -> Optional[Card]:
    """Declarer-side lead: CROSS to the partner hand when it holds winners this
    hand can't cash. Returns a card or None to fall through."""
    if seat.is_ns() != declarer.is_ns():
        return None
    dummy = declarer.partner()
    other = declarer if seat == dummy else dummy
    if other not in board.hands:
        return None
    mine = board.hands[seat].cards
    pard = board.hands[other].cards
    # If THIS hand has a sure winner, leave it to the per-seat cash-boss logic.
    if any(is_boss_card(c, board, seat, [], declarer) for c in mine):
        return None
    pard_bosses = [c for c in pard
                   if is_boss_card(c, board, other, [], declarer)]
    if not pard_bosses:
        return None
    # Cross via a NON-trump suit partner controls (a trump lead from here would
    # just draw trumps / not reach partner cleanly). Lead our lowest there.
    for c in sorted(pard_bosses, key=lambda c: -_suit_len(pard, c.suit)):
        if c.suit == trump:
            continue
        mine_in = [x for x in mine if x.suit == c.suit]
        if mine_in:
            return min(mine_in, key=lambda x: -x.rank.value)
    return None
