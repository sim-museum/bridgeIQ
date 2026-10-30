"""Strategic cardplay planner — overrides DDS for discard decisions.

The MC+DDS engine evaluates each card on freshly-sampled hypothetical
hands. For a discard situation (out of the led suit), the DDS scores
for honors vs small cards differ only by per-sample noise — which
can systematically push the engine to discard winners (saw biq pitch
the ♥A, ♦K, ♦Q, ♦A in a single 3NT deal on which the reference made 12 tricks).

This module adds two pre-DDS filters for the discard-only path:

1. Boss-card protection: a card is "boss" if no higher rank of its
   suit can be in opponents' hands. Boss cards are guaranteed future
   winners — never discard one.

2. Top-of-holding protection: protect the upper half of own-side's
   combined holding in each suit. These are either top-tricks
   already (honors) or top-of-length cards that anchor a length
   establishment. Discarding them throws away trick sources. The
   bottom half (small cards) is safe to discard — the suit-establishment
   plan only needs the long count, not the specific small ranks.

Only declarer/dummy seats get protection in this MVP, since the
planner needs to see both hands of the side it plans for. Defender
planning needs partner-hand inference (later).
"""

from typing import List, Optional, Set
from .models import Card, Hand, Suit, Rank, Seat, BoardState

# Rank.value: ACE=0 (highest) ... TWO=12 (lowest). Iterate high-to-low.
_RANK_ORDER = [Rank.ACE, Rank.KING, Rank.QUEEN, Rank.JACK,
               Rank.TEN, Rank.NINE, Rank.EIGHT, Rank.SEVEN,
               Rank.SIX, Rank.FIVE, Rank.FOUR, Rank.THREE, Rank.TWO]

# Threshold for "length suit." 5 = standard rule of thumb; with 5+
# cards combined the opponents have ≤8, so a 4-4 split leaves them
# with 4 each and the long side has 1 length trick after honors are
# cashed. With 6+, length potential grows.
_LENGTH_SUIT_MIN = 5


def _partner_of(seat: Seat) -> Seat:
    return Seat((seat + 2) % 4)


def _is_declarer_side(seat: Seat, declarer: Seat) -> bool:
    return seat == declarer or seat == _partner_of(declarer)


def _ranks_in_hand(hand: Hand, suit: Suit) -> Set[Rank]:
    return {c.rank for c in hand.cards if c.suit == suit}


def _played_ranks_in_suit(board: BoardState,
                          current_trick_cards: List[Card],
                          suit: Suit) -> Set[Rank]:
    """Ranks of `suit` known to have been played already.
    Includes the current trick and any completed tricks the board
    tracks (the MC harness doesn't track completed tricks, so this is
    typically just the current-trick cards — that's fine; the boss
    test is conservative either way)."""
    played = set()
    for c in current_trick_cards:
        if c.suit == suit:
            played.add(c.rank)
    for trick in getattr(board, "tricks", []) or []:
        cards = getattr(trick, "cards", None)
        if not cards:
            continue
        for c in cards:
            if c.suit == suit:
                played.add(c.rank)
    return played


def _opponent_could_hold(rank: Rank, suit: Suit, board: BoardState,
                         seat: Seat, current_trick_cards: List[Card],
                         declarer: Seat) -> bool:
    """True if `rank` of `suit` is unaccounted for from `seat`'s
    perspective — i.e., could still be in either opponent's hand."""
    if rank in _ranks_in_hand(board.hands[seat], suit):
        return False
    if _is_declarer_side(seat, declarer):
        partner = _partner_of(seat)
        if rank in _ranks_in_hand(board.hands[partner], suit):
            return False
    if rank in _played_ranks_in_suit(board, current_trick_cards, suit):
        return False
    return True


def is_boss_card(card: Card, board: BoardState, seat: Seat,
                 current_trick_cards: List[Card],
                 declarer: Seat) -> bool:
    """Returns True if no higher-ranked card of `card.suit` could be
    in opponents' hands. Such a card is a guaranteed future winner
    (modulo trumping, which only matters in suit contracts when the
    suit isn't trumps)."""
    for r in _RANK_ORDER:
        if r.value >= card.rank.value:
            break  # at-or-below card's rank — irrelevant
        if _opponent_could_hold(r, card.suit, board, seat,
                                current_trick_cards, declarer):
            return False  # a higher rank could be with opponents
    return True


def own_side_ranks_in_suit(board: BoardState, seat: Seat,
                           declarer: Seat, suit: Suit) -> List[Rank]:
    """Combined ranks of `suit` in seat + partner's remaining hand,
    sorted high to low (Ace first)."""
    own = [c.rank for c in board.hands[seat].cards if c.suit == suit]
    if _is_declarer_side(seat, declarer):
        partner = _partner_of(seat)
        own.extend(c.rank for c in board.hands[partner].cards
                   if c.suit == suit)
    own.sort(key=lambda r: r.value)  # ACE=0 first → high to low
    return own


def is_top_of_holding(card: Card, board: BoardState, seat: Seat,
                     declarer: Seat) -> bool:
    """Returns True if `card.rank` is in the upper half of own-side's
    combined holding in `card.suit`. These are honors + top-of-length
    cards — the trick sources for the suit. Discarding any of them
    surrenders trick potential.

    Upper half is rounded up: a 5-card combined holding protects the
    top 3 ranks. A 1-card holding protects that singleton (always
    "top"). An even split (e.g. 6 cards) protects the top 3.
    """
    ranks = own_side_ranks_in_suit(board, seat, declarer, card.suit)
    if not ranks:
        return False
    # Top half (rounded up) — protect more rather than less.
    midpoint = (len(ranks) + 1) // 2
    return card.rank in ranks[:midpoint]


def should_protect_from_discard(card: Card, board: BoardState, seat: Seat,
                                current_trick_cards: List[Card],
                                declarer: Seat) -> bool:
    """Returns True if `card` should be protected from discarding.
    Only applies to declarer-side seats in this MVP; returns False
    for defenders (defender planning needs partner-hand inference
    and is deferred)."""
    if not _is_declarer_side(seat, declarer):
        return False
    if is_boss_card(card, board, seat, current_trick_cards, declarer):
        return True
    if is_top_of_holding(card, board, seat, declarer):
        return True
    return False


def filter_safe_discards(candidates: List[Card], board: BoardState,
                         seat: Seat, current_trick_cards: List[Card],
                         declarer: Seat) -> List[Card]:
    """Return the subset of `candidates` that the planner considers
    safe to discard. If every candidate is protected (no safe choice),
    return the candidates list unchanged so the caller can fall back
    to its own tie-break."""
    safe = [c for c in candidates
            if not should_protect_from_discard(c, board, seat,
                                               current_trick_cards,
                                               declarer)]
    return safe if safe else candidates
