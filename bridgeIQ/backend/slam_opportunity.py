"""Slam-opportunity detector.

Decides whether the side to bid plausibly has a slam available, so the reference-style Simulation pop-up can be triggered automatically. The
detector is intentionally conservative: false positives interrupt the
user, false negatives just mean they have to open the dialog manually
from the menu.

Heuristic (any one is sufficient):

  * Combined estimated HCP ≥ 30 from own hand + partner's minimum
    from their opening / response.
  * Own hand has slam-strength shape (≥ 17 HCP with a 6-card suit,
    or ≥ 19 HCP balanced) and partner has shown an opening.
  * Auction itself already contains slam-try machinery — RKC ask,
    Gerber, jump-shift, quantitative 4NT — meaning *someone* on the
    side has decided slam is in scope.
"""

from __future__ import annotations

from typing import Optional

from .models import BoardState, Seat, Bid, Suit


# Rough partner-minimum HCP by opening bid. Standard SAYC-ish ranges.
_PARTNER_MIN_HCP = {
    # 1-level openings
    ("1", "C"): 12, ("1", "D"): 12, ("1", "H"): 12, ("1", "S"): 12,
    ("1", "N"): 15,                # 15-17 NT
    # Strong / forcing openings
    ("2", "C"): 22,                # strong, artificial
    ("2", "N"): 20,                # 20-21 NT
    ("3", "N"): 25,                # gambling / strong NT variants
    # Weak openings — slam unlikely opposite, low floor
    ("2", "D"): 5, ("2", "H"): 5, ("2", "S"): 5,
    ("3", "C"): 5, ("3", "D"): 5, ("3", "H"): 5, ("3", "S"): 5,
}


def _opening_min_hcp(b: Bid) -> int:
    if b.is_pass or b.is_double or b.is_redouble or b.suit is None:
        return 0
    key = (str(b.level), b.suit.to_char() if b.suit != Suit.NOTRUMP else "N")
    return _PARTNER_MIN_HCP.get(key, 0)


def _seat_at_index(dealer: Seat, idx: int) -> Seat:
    s = dealer
    for _ in range(idx):
        s = s.next()
    return s


def _partner_min_hcp_from_auction(board: BoardState, seat: Seat) -> int:
    """Estimate partner's minimum HCP from the bids they've made.

    Uses opening bid only — response bids span too wide a range to
    pin down a useful floor without modelling the whole system.
    """
    partner = seat.partner()
    best = 0
    for i, b in enumerate(board.auction):
        bidder = _seat_at_index(board.dealer, i)
        if bidder != partner:
            continue
        if b.is_pass or b.is_double or b.is_redouble:
            continue
        # First non-pass bid by partner is treated as their opening.
        best = max(best, _opening_min_hcp(b))
        break
    return best


def _auction_shows_slam_try(board: BoardState, seat: Seat) -> bool:
    """Look for explicit slam-try machinery from our side."""
    me = seat
    partner = seat.partner()
    for i, b in enumerate(board.auction):
        bidder = _seat_at_index(board.dealer, i)
        if bidder not in (me, partner):
            continue
        if b.is_pass or b.is_double or b.is_redouble:
            continue
        # 4NT (Blackwood / RKC), 4C over NT (Gerber), or any 5-level
        # natural suit bid by our side is a slam try / commitment.
        if b.level == 4 and b.suit == Suit.NOTRUMP:
            return True
        if b.level == 4 and b.suit == Suit.CLUBS:
            # Gerber only after a NT bid by partner.
            prev_partner_nt = any(
                pb.suit == Suit.NOTRUMP
                for pj, pb in enumerate(board.auction[:i])
                if _seat_at_index(board.dealer, pj) == partner
                and not pb.is_pass
            )
            if prev_partner_nt:
                return True
        if b.level >= 5 and b.suit is not None and b.suit != Suit.NOTRUMP:
            return True
        # Jump-shift response (level 2+ over partner's opening, new suit
        # at a higher level than necessary) — too system-dependent to
        # detect reliably here; skip.
    return False


def _last_real_level(board: BoardState) -> int:
    for b in reversed(board.auction):
        if not (b.is_pass or b.is_double or b.is_redouble):
            return b.level
    return 0


def has_slam_opportunity(board: BoardState, seat: Seat) -> bool:
    """Return True if the side to bid plausibly has a slam available.

    Conservative — only fires when there's a clear reason to consider
    slam. The dialog is interruptive, so silent false negatives are
    preferable to noisy false positives.
    """
    if board is None or seat is None:
        return False
    if not board.auction:
        return False
    # Already past slam decision point — no point popping the dialog
    # once the side has signed off at 6 or 7.
    if _last_real_level(board) >= 6:
        return False
    hand = board.hands.get(seat)
    if hand is None or not hand.cards:
        return False

    # 1) Explicit slam-try machinery already on the table.
    if _auction_shows_slam_try(board, seat):
        return True

    own_hcp = hand.hcp()
    partner_min = _partner_min_hcp_from_auction(board, seat)
    combined_min = own_hcp + partner_min

    # 2) Combined HCP looks slammish (33 is the textbook 6NT floor,
    #    relax to 30 because partner often has more than their
    #    advertised minimum and a fit adds tricks).
    if combined_min >= 30 and own_hcp >= 11:
        return True

    # 3) Own hand is strong enough to drive slam opposite a minimum
    #    opener — 17+ with a long suit, or 19+ balanced.
    suit_lens = {s: 0 for s in (Suit.SPADES, Suit.HEARTS,
                                Suit.DIAMONDS, Suit.CLUBS)}
    for c in hand.cards:
        suit_lens[c.suit] += 1
    has_6_card_suit = any(n >= 6 for n in suit_lens.values())
    dist = tuple(sorted(suit_lens.values(), reverse=True))
    is_balanced = dist in ((4, 3, 3, 3), (4, 4, 3, 2), (5, 3, 3, 2))
    if partner_min >= 12:  # partner has at least opened
        if own_hcp >= 17 and has_6_card_suit:
            return True
        if own_hcp >= 19 and is_balanced:
            return True

    return False
