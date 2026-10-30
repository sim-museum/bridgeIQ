"""Infer per-seat constraints from a bid sequence.

The MC card-play engine already uses simple first-bid constraints
(`_derive_hcp_constraints`, `_derive_shape_constraints` in engine.py)
to filter implausible sample hands. This module extends that idea
to the full auction:

  * Opener's rebid pins HCP tighter (12-14 / 16-18 / 19-21 etc.)
  * Responder's call pins responder's HCP and often shape.
  * Stayman / transfer / Jacoby 2NT / splinter sequences add
    confident length and HCP inferences.

For each seat we maintain (hcp_min, hcp_max) and per-suit
(min_len, max_len). When subsequent bids tighten a range,
we narrow it; if a later bid contradicts an earlier inference
(rare — should only happen if the bidder strayed from system),
we drop the constraint rather than reject the sample.

The module also returns human-readable explanation strings for
each constraint — used by the teaching UI to show
"East's 1NT shows 15-17 HCP balanced."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .models import Bid, BoardState, Seat, Suit, Vulnerability


@dataclass
class SeatConstraints:
    """Inferred constraints on what one seat's hand may look like."""
    hcp_min: int = 0
    hcp_max: int = 37
    # For each suit value (0-3), (min_len, max_len).
    suit_len: Dict[int, Tuple[int, int]] = field(default_factory=dict)
    # Natural-language reasons, in the order they were added.
    reasons: List[str] = field(default_factory=list)

    def tighten_hcp(self, lo: int, hi: int, reason: str):
        new_lo = max(self.hcp_min, lo)
        new_hi = min(self.hcp_max, hi)
        if new_lo <= new_hi:
            self.hcp_min, self.hcp_max = new_lo, new_hi
            self.reasons.append(reason)

    def set_suit_len(self, suit: Suit, lo: int, hi: int,
                     reason: str):
        cur_lo, cur_hi = self.suit_len.get(suit.value, (0, 13))
        new_lo = max(cur_lo, lo)
        new_hi = min(cur_hi, hi)
        if new_lo <= new_hi:
            self.suit_len[suit.value] = (new_lo, new_hi)
            self.reasons.append(reason)


def _is_natural_suit_bid(b: Bid) -> bool:
    return (not b.is_pass and not b.is_double
            and not b.is_redouble
            and b.suit is not None and b.suit != Suit.NOTRUMP)


def _is_nt(b: Bid) -> bool:
    return (not b.is_pass and not b.is_double
            and not b.is_redouble
            and b.suit == Suit.NOTRUMP)


def _suit_char(s: Suit) -> str:
    return {Suit.SPADES: "♠", Suit.HEARTS: "♥",
            Suit.DIAMONDS: "♦", Suit.CLUBS: "♣"}[s]


def infer_constraints(board: BoardState, system) -> Dict[Seat, SeatConstraints]:
    """Walk the auction once, building per-seat constraints.

    Returns {Seat: SeatConstraints}. Seats with no inferences
    end up with the default (0-37 HCP, no suit-length pins).
    """
    out: Dict[Seat, SeatConstraints] = {
        s: SeatConstraints() for s in Seat}
    auction = list(getattr(board, "auction", []) or [])
    if not auction:
        return out

    nt_min = getattr(system, "one_nt_min_hcp", 15) if system else 15
    nt_max = getattr(system, "one_nt_max_hcp", 17) if system else 17
    strong_call = getattr(system, "strong_open_call", "2C") \
        if system else "2C"
    raw_rules = (getattr(system, "raw_rules", set())
                 if system else set())
    is_precision = strong_call == "1C"
    weak_nt = nt_min <= 12  # 12-14 range = Acol weak NT

    # Walk and tag each bid with seat + role (opener / responder / etc.)
    seats_in_order: List[Seat] = []
    seat = board.dealer
    for _ in auction:
        seats_in_order.append(seat)
        seat = Seat((seat + 1) % 4)

    # Find opener (first non-pass-non-X-non-XX bid).
    opener_idx: Optional[int] = None
    for i, b in enumerate(auction):
        if (not b.is_pass and not b.is_double
                and not b.is_redouble):
            opener_idx = i
            break
    if opener_idx is None:
        # Pass-out — everyone capped low.
        for s in Seat:
            out[s].tighten_hcp(0, 11,
                               "passed throughout (no opener)")
        return out

    opener_seat = seats_in_order[opener_idx]
    opener_partner = opener_seat.partner()
    opener_lho = Seat((opener_seat + 1) % 4)
    opener_rho = Seat((opener_seat + 3) % 4)

    # First, mark everyone who passed throughout as "weak hand".
    for s in Seat:
        bids_by_seat = [auction[i] for i, sx in
                        enumerate(seats_in_order) if sx == s]
        if all(b.is_pass for b in bids_by_seat) and bids_by_seat:
            # Capped at "couldn't open / overcall / respond" — call
            # it 0-9 HCP. 10-11 hands sometimes pass too but the
            # 0-9 floor catches the obvious cases.
            out[s].tighten_hcp(0, 9,
                               "passed throughout")

    # Annotate the opener's opening bid.
    op_bid = auction[opener_idx]
    _annotate_opening(out[opener_seat], op_bid, system,
                      nt_min, nt_max, strong_call,
                      raw_rules, is_precision, weak_nt)

    # Walk subsequent bids, annotating each seat's actions.
    # We track: opener's rebid, responder's response (and rebid),
    # competitive bids by opponents.
    opener_acts = 0   # 0 = haven't seen opener act after opening
    responder_acts = 0
    responder_first_bid: Optional[Bid] = None
    last_opener_bid_idx = opener_idx
    # Last natural suit bid by our side that ESTABLISHED a fit —
    # used to interpret 4NT as keycard for that suit.
    agreed_trump: Optional[Suit] = None
    for i in range(opener_idx + 1, len(auction)):
        b = auction[i]
        s = seats_in_order[i]
        # Process doubles selectively:
        #   • negative double — responder's first call (handled
        #     in _annotate_response)
        #   • takeout/penalty double by an opp — handled in
        #     _annotate_overcall
        # Everyone else's doubles (opener's penalty / responder's
        # later double) are passed-over.
        if b.is_pass:
            continue
        if b.is_double or b.is_redouble:
            is_neg_double_by_responder = (
                s == opener_partner and responder_acts == 0)
            is_opp_double = (
                s != opener_seat and s != opener_partner)
            if not (is_neg_double_by_responder or is_opp_double):
                continue
        if s == opener_seat:
            opener_acts += 1
            _annotate_opener_rebid(
                out[opener_seat], op_bid, b,
                last_opener_bid_idx, i, auction,
                seats_in_order, system, opener_acts,
                responder_first_bid, out[opener_partner])
            last_opener_bid_idx = i
        elif s == opener_partner:
            responder_acts += 1
            if responder_acts == 1:
                responder_first_bid = b
                # Look back for an opponent's overcall between
                # opener and responder — if present, this
                # response could be a negative double.
                intervening = None
                for j in range(opener_idx + 1, i):
                    bj = auction[j]
                    sj = seats_in_order[j]
                    if (sj != opener_partner
                            and not bj.is_pass
                            and not bj.is_double
                            and not bj.is_redouble):
                        intervening = bj
                        break
                # Did responder pass earlier in the auction? Used
                # for Drury detection (passed-hand 2C raise).
                responder_passed_first = any(
                    seats_in_order[j] == opener_partner
                    and auction[j].is_pass
                    for j in range(0, i))
                _annotate_response(
                    out[opener_partner], op_bid, b, system,
                    nt_min, nt_max,
                    intervening_bid=intervening,
                    responder_passed_first=responder_passed_first)
            else:
                _annotate_responder_rebid(
                    out[opener_partner], op_bid,
                    responder_first_bid, b, auction, i,
                    seats_in_order, system,
                    out[opener_seat],
                    opener_seat=opener_seat)
        else:
            # Opponent action (overcall / X / preempt-type response).
            _annotate_overcall(out[s], b, system, opening=op_bid)
        # Track an agreed trump suit (last natural suit bid by our
        # side that the partner subsequently raised). Lets us
        # interpret 4NT as RKC keycard for THIS suit.
        if (s in (opener_seat, opener_partner)
                and not b.is_pass and not b.is_double
                and not b.is_redouble
                and b.suit is not None
                and b.suit != Suit.NOTRUMP):
            # Heuristic: a partner-raises-partner bid agrees the
            # suit. Stayman / transfer / new-suit-by-opener
            # responses don't yet agree.
            # NB: `or agreed_trump` would break here because
            # `Suit.SPADES.value == 0`, making the enum falsy.
            _new_trump = _maybe_agreed_trump(
                auction[:i + 1], seats_in_order[:i + 1],
                opener_seat, opener_partner)
            if _new_trump is not None:
                agreed_trump = _new_trump
        # 4NT or 5NT — handle keycard / king-ask responses.
        if (not b.is_pass and not b.is_double
                and not b.is_redouble
                and b.suit == Suit.NOTRUMP
                and b.level in (4, 5)
                and s in (opener_seat, opener_partner)):
            _annotate_blackwood_ask(out[s], b, agreed_trump)
        # 5C/5D/5H/5S response to a partner's 4NT — keycard reply.
        if (b.level == 5 and b.suit is not None
                and b.suit != Suit.NOTRUMP and i >= 1
                and s in (opener_seat, opener_partner)):
            prev = auction[i - 1] if i >= 1 else None
            # Look back for the most recent 4NT bid; if it was
            # partner's, this is the keycard reply.
            for j in range(i - 1, -1, -1):
                jb = auction[j]
                if (jb.suit == Suit.NOTRUMP and jb.level == 4
                        and not jb.is_pass):
                    js = seats_in_order[j]
                    if js == s.partner():
                        _annotate_keycard_response(
                            out[s], b, agreed_trump)
                    break
                if not jb.is_pass:
                    break
    return out


def _maybe_agreed_trump(auction_so_far: List[Bid],
                        seats_so_far: List[Seat],
                        opener_seat: Seat,
                        opener_partner: Seat) -> Optional[Suit]:
    """Return the most recent suit that our side has agreed on as
    trump (one side bid it, the other raised it).

    Cheap heuristic: walk the auction, track each suit bid by each
    side; if both sides have bid the same suit at consecutive
    rounds, treat it as agreed.
    """
    last_suit_by_seat: Dict[Seat, Optional[Suit]] = {
        opener_seat: None, opener_partner: None}
    for i, b in enumerate(auction_so_far):
        s = seats_so_far[i]
        if s not in (opener_seat, opener_partner):
            continue
        if (b.is_pass or b.is_double or b.is_redouble
                or b.suit is None or b.suit == Suit.NOTRUMP):
            continue
        last_suit_by_seat[s] = b.suit
    # If both seats have last-bid the same suit, that's "agreed".
    if (last_suit_by_seat[opener_seat] is not None
            and last_suit_by_seat[opener_seat]
            == last_suit_by_seat[opener_partner]):
        return last_suit_by_seat[opener_seat]
    return None


def opener_partner_seat_for_rebid(seats_in_order: List[Seat],
                                  rebid_idx: int) -> Optional[Seat]:
    """Given that auction[rebid_idx] is an opener's rebid, return the
    seat of opener's partner (responder). Cheap inference: opener
    is the seat at `rebid_idx`, partner is (opener + 2) % 4."""
    if rebid_idx < 0 or rebid_idx >= len(seats_in_order):
        return None
    opener = seats_in_order[rebid_idx]
    return Seat((opener.value + 2) % 4)


def _annotate_opening(c: SeatConstraints, b: Bid, system,
                      nt_min: int, nt_max: int,
                      strong_call: str, raw_rules: Set[str],
                      is_precision: bool, weak_nt: bool):
    """Pin HCP + shape from a single opening bid."""
    if b.is_pass or b.is_double or b.is_redouble or b.suit is None:
        return

    suit_c = _suit_char(b.suit) if b.suit != Suit.NOTRUMP else "NT"

    if b.level == 1:
        if b.suit == Suit.NOTRUMP:
            c.tighten_hcp(nt_min, nt_max,
                          f"1NT opening = {nt_min}-{nt_max} HCP balanced")
            # Balanced shape: each suit 2-5 (allows 5cM/5cm in NT).
            for su in (Suit.SPADES, Suit.HEARTS,
                       Suit.DIAMONDS, Suit.CLUBS):
                c.set_suit_len(su, 2, 5,
                               f"1NT promises balanced "
                               f"(2-5 cards each suit)")
            return
        if is_precision and b.suit == Suit.CLUBS:
            c.tighten_hcp(16, 37,
                          "Precision 1♣ = 16+ HCP (artificial)")
            return
        if is_precision:
            # Precision other 1-of-suit openings: limited.
            c.tighten_hcp(10, 15,
                          f"Precision 1{suit_c} = 10-15 HCP "
                          "(limited opening)")
        else:
            c.tighten_hcp(10, 21,
                          f"1{suit_c} opening = 10-21 HCP")
        if b.suit in (Suit.HEARTS, Suit.SPADES):
            c.set_suit_len(b.suit, 5, 13,
                           f"1{suit_c} promises 5+ {suit_c} "
                           "(5-card majors)")
        elif b.suit in (Suit.CLUBS, Suit.DIAMONDS) and not is_precision:
            # Natural minor opening: at least 3 in the minor (4
            # with 4-card better-minor variants); deny 5-card major.
            min_len = 3
            c.set_suit_len(b.suit, min_len, 13,
                           f"1{suit_c} promises {min_len}+ {suit_c}")
            c.set_suit_len(Suit.HEARTS, 0, 4,
                           f"1{suit_c} (not 1M) denies 5-card heart")
            c.set_suit_len(Suit.SPADES, 0, 4,
                           f"1{suit_c} (not 1M) denies 5-card spade")
        return

    if b.level == 2:
        # Acol strong twos (`open.2d.strong` / `open.2major.strong`): 6+ suit, 18-22.
        if ((b.suit == Suit.DIAMONDS and "open.2d.strong" in raw_rules)
                or (b.suit in (Suit.HEARTS, Suit.SPADES) and "open.2major.strong" in raw_rules)):
            c.tighten_hcp(17, 23, f"Strong 2{suit_c} = 18-22 HCP, 6+ {suit_c} (forcing)")
            c.set_suit_len(b.suit, 6, 8, f"Strong 2{suit_c} promises 6+ {suit_c}")
            return
        # Benjamin (F-FRA-M): 2C = 22-23 balanced or a strong suit, 2D = 24+
        # or equivalent, game forcing.
        if "open.strong_2c.benjamin" in raw_rules and b.suit in (Suit.CLUBS, Suit.DIAMONDS):
            if b.suit == Suit.CLUBS:
                c.tighten_hcp(19, 24, "Benjamin 2♣ = 22-23 balanced or a strong suit")
            else:
                c.tighten_hcp(21, 37, "Benjamin 2♦ = 24+ or equivalent (game force)")
            return
        if b.suit == Suit.CLUBS:
            if strong_call == "2C":
                c.tighten_hcp(22, 37,
                              "Strong 2♣ = 22+ HCP (artificial)")
            else:
                c.tighten_hcp(11, 15,
                              "Precision 2♣ = 11-15 HCP, 6+ ♣")
                c.set_suit_len(Suit.CLUBS, 6, 13,
                               "Precision 2♣ promises 6+ ♣")
            return
        if b.suit == Suit.DIAMONDS:
            # Could be weak 2 or Precision 2D 3-suiter.
            if any(k.startswith("open.2d.three_suiter") for k in raw_rules):
                c.tighten_hcp(11, 15,
                              "Precision 2♦ = 11-15 HCP three-suiter")
                c.set_suit_len(Suit.DIAMONDS, 0, 1,
                               "Precision 2♦ promises 0-1 ♦")
                c.set_suit_len(Suit.HEARTS, 3, 6,
                               "Precision 2♦ promises 3+ ♥")
                c.set_suit_len(Suit.SPADES, 3, 6,
                               "Precision 2♦ promises 3+ ♠")
            else:
                c.tighten_hcp(5, 11,
                              "Weak 2♦ = 5-10 HCP, 6+ ♦")
                c.set_suit_len(Suit.DIAMONDS, 5, 6,
                               "Weak 2♦ promises 6+ ♦")
            return
        if b.suit == Suit.NOTRUMP:
            lo2 = getattr(system, "two_nt_min_hcp", 20) if system else 20
            hi2 = getattr(system, "two_nt_max_hcp", 21) if system else 21
            c.tighten_hcp(lo2, hi2 + 1,
                          f"2NT opening = {lo2}-{hi2} HCP balanced")
            for su in (Suit.SPADES, Suit.HEARTS,
                       Suit.DIAMONDS, Suit.CLUBS):
                c.set_suit_len(su, 2, 5,
                               "2NT balanced (2-5 each suit)")
            return
        # 2♥ / 2♠ weak twos
        c.tighten_hcp(5, 11,
                      f"Weak 2{suit_c} = 5-10 HCP, 6+ {suit_c}")
        c.set_suit_len(b.suit, 5, 6,
                       f"Weak 2{suit_c} promises 6+ {suit_c}")
        return

    if b.level == 3 and b.suit != Suit.NOTRUMP:
        c.tighten_hcp(4, 10,
                      f"3{suit_c} preempt = 5-10 HCP")
        c.set_suit_len(b.suit, 6, 7,
                       f"3{suit_c} preempt = 7-card suit")
        return
    if b.level == 4 and b.suit != Suit.NOTRUMP:
        c.tighten_hcp(4, 10,
                      f"4{suit_c} preempt = 5-10 HCP")
        c.set_suit_len(b.suit, 7, 8,
                       f"4{suit_c} preempt = 7-8 card suit")
        return


def _annotate_opener_rebid(c: SeatConstraints, opening: Bid,
                           rebid: Bid, opening_idx: int,
                           rebid_idx: int, auction: List[Bid],
                           seats_in_order: List[Seat], system,
                           rebid_count: int,
                           responder_bid: Optional[Bid],
                           responder_c: Optional[SeatConstraints] = None):
    """Tighten opener's HCP based on the rebid pattern.

    Common patterns we infer:
      * 1m-1M-1NT     → 12-14 HCP balanced
      * 1m-1M-2NT     → 18-19 HCP balanced
      * 1m-1M-3NT     → 19-21 HCP balanced
      * 1m-1M-2m      → 12-15 HCP, 6+ in the minor (rebid own suit)
      * 1M-2M-3M      → 16-18 HCP game try
      * 1M-2M-4M      → 18+ HCP game force
      * 1m-1M-reverse → 17+ HCP
    """
    if rebid.is_pass or rebid.is_double or rebid.is_redouble:
        return
    if opening.suit is None:
        return

    # ---- Opener's response to Stayman / Jacoby transfer ----
    # These don't require rebid_count == 1; they fire whenever
    # responder bid 2C/2D/2H and opener responds in turn.
    if (opening.level == 1 and opening.suit == Suit.NOTRUMP
            and responder_bid is not None):
        # 1NT - 2C - 2D: opener denies a 4-card major.
        if (responder_bid.level == 2
                and responder_bid.suit == Suit.CLUBS
                and rebid.level == 2 and rebid.suit == Suit.DIAMONDS):
            c.set_suit_len(Suit.HEARTS, 0, 3,
                           "1NT-2♣-2♦ denies a 4-card major")
            c.set_suit_len(Suit.SPADES, 0, 3,
                           "1NT-2♣-2♦ denies a 4-card major")
            return
        # 1NT - 2C - 2H: opener shows 4+ hearts (may also have 4 ♠).
        if (responder_bid.level == 2
                and responder_bid.suit == Suit.CLUBS
                and rebid.level == 2 and rebid.suit == Suit.HEARTS):
            c.set_suit_len(Suit.HEARTS, 4, 5,
                           "1NT-2♣-2♥ promises 4+ ♥")
            return
        # 1NT - 2C - 2S: opener shows 4+ spades (and denies 4 ♥ in
        # most treatments).
        if (responder_bid.level == 2
                and responder_bid.suit == Suit.CLUBS
                and rebid.level == 2 and rebid.suit == Suit.SPADES):
            c.set_suit_len(Suit.SPADES, 4, 5,
                           "1NT-2♣-2♠ promises 4+ ♠")
            c.set_suit_len(Suit.HEARTS, 0, 3,
                           "1NT-2♣-2♠ denies 4 ♥ (else would have "
                           "bid 2♥ first)")
            return
        # 1NT - 2D - 2H: transfer acceptance, no new info on opener
        # beyond the 1NT.
        if (responder_bid.level == 2
                and responder_bid.suit == Suit.DIAMONDS
                and rebid.level == 2 and rebid.suit == Suit.HEARTS):
            return
        # 1NT - 2H - 2S: spade transfer acceptance.
        if (responder_bid.level == 2
                and responder_bid.suit == Suit.HEARTS
                and rebid.level == 2 and rebid.suit == Suit.SPADES):
            return

    # ---- Opener's response to Jacoby 2NT (1M-2NT) ----
    # Standard J2NT replies:
    #   3-of-shortness suit  = singleton/void (game-force values)
    #   3-of-own-major       = 16-17 HCP no shortness (extras)
    #   3NT                  = 18-19 HCP balanced extras
    #   4-of-own-major       = minimum, no shortness, signoff
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and responder_bid is not None
            and responder_bid.level == 2
            and responder_bid.suit == Suit.NOTRUMP):
        if rebid.level == 3 and rebid.suit not in (None, Suit.NOTRUMP):
            if rebid.suit == opening.suit:
                c.tighten_hcp(16, 17,
                              f"After J2NT, 3{_suit_char(rebid.suit)} = "
                              "16-17 HCP no shortness (extras)")
            else:
                # 3-of-side-suit = singleton or void in that suit.
                c.set_suit_len(rebid.suit, 0, 1,
                               f"After J2NT, 3{_suit_char(rebid.suit)} "
                               f"shows 0-1 {_suit_char(rebid.suit)} "
                               "(shortness)")
            return
        if rebid.level == 3 and rebid.suit == Suit.NOTRUMP:
            c.tighten_hcp(18, 19,
                          "After J2NT, 3NT = 18-19 HCP balanced extras")
            return
        if (rebid.level == 4 and rebid.suit == opening.suit):
            c.tighten_hcp(11, 15,
                          f"After J2NT, 4{_suit_char(opening.suit)} = "
                          "minimum signoff, no shortness")
            return

    # ---- Opener's reply to partner's splinter ----
    # After 1M-4-of-side (splinter showing 4+ trumps, 0-1 in
    # cuebid suit, 13+ HCP), opener:
    #   4M                  = minimum signoff (12-14 HCP, no slam)
    #   4NT                 = RKC slam-try (15+ HCP)
    #   cuebid of new suit  = slam interest, showing control
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and responder_bid is not None
            and responder_bid.level == 4
            and responder_bid.suit is not None
            and responder_bid.suit != Suit.NOTRUMP
            and responder_bid.suit != opening.suit):
        # 4-of-own-major = signoff (minimum)
        if rebid.level == 4 and rebid.suit == opening.suit:
            c.tighten_hcp(12, 14,
                          f"4{_suit_char(opening.suit)} signoff "
                          "after partner's splinter = minimum "
                          "(12-14 HCP, no slam interest)")
            return
        # 4NT = RKC slam try
        if rebid.level == 4 and rebid.suit == Suit.NOTRUMP:
            c.tighten_hcp(15, 21,
                          "4NT after partner's splinter = RKC "
                          "slam try, 15+ HCP")
            return

    # ---- Cuebid after agreed-major fit (control-showing) ----
    # After 1M-3M (limit raise) or 1M-2NT (Jacoby 2NT), opener's
    # 4-of-new-suit shows first-round control (the ace, or void).
    # Pattern: opening = 1M, responder_bid = 3M raise or 2NT,
    # this rebid is 4-of-new-suit (not opener's major, not NT).
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and responder_bid is not None
            and ((responder_bid.level == 3
                  and responder_bid.suit == opening.suit)
                 or (responder_bid.level == 2
                     and responder_bid.suit == Suit.NOTRUMP))
            and rebid.level == 4 and rebid.suit is not None
            and rebid.suit != Suit.NOTRUMP
            and rebid.suit != opening.suit):
        # Cuebid: shows the ace of rebid.suit (first-round control).
        # We don't have "honor" constraints — the cleanest way is
        # to mark suit-length 1-13 + reason. The HCP floor goes up
        # because cuebidding implies extras.
        c.tighten_hcp(15, 21,
                      f"Cuebid 4{_suit_char(rebid.suit)} after "
                      f"{_suit_char(opening.suit)} fit = "
                      "first-round control + extras")
        return

    # ---- NMF opener responses ----
    # After 1m - 1M - 1NT - 2-of-OTHER-minor (NMF inquiry), opener
    # describes:  2M = 3-card support, 2-other-major = 4 there,
    # 2NT = no fit minimum, 3M = 3-card support + max.
    # Fires at rebid_count == 2 (opener's response to NMF).
    if (auction is not None and seats_in_order is not None
            and rebid_idx > 0):
        responder_last_bid: Optional[Bid] = None
        responder_first_major: Optional[Suit] = None
        responder_seat = opener_partner_seat_for_rebid(
            seats_in_order, rebid_idx)
        if responder_seat is not None:
            for j in range(rebid_idx - 1, -1, -1):
                if seats_in_order[j] == responder_seat:
                    bj = auction[j]
                    if (not bj.is_pass and not bj.is_double
                            and not bj.is_redouble):
                        if responder_last_bid is None:
                            responder_last_bid = bj
                        if (bj.level == 1 and bj.suit
                                in (Suit.HEARTS, Suit.SPADES)):
                            responder_first_major = bj.suit
        if (opening.level == 1
                and opening.suit in (Suit.CLUBS, Suit.DIAMONDS)
                and responder_last_bid is not None
                and responder_last_bid.level == 2
                and responder_last_bid.suit
                in (Suit.CLUBS, Suit.DIAMONDS)
                and responder_last_bid.suit != opening.suit
                and responder_first_major is not None):
            other_major = (Suit.SPADES
                           if responder_first_major == Suit.HEARTS
                           else Suit.HEARTS)
            if (rebid.level == 2
                    and rebid.suit == responder_first_major):
                c.set_suit_len(
                    rebid.suit, 3, 3,
                    f"NMF reply 2{_suit_char(rebid.suit)} = "
                    "exactly 3-card support for responder's major")
                c.tighten_hcp(
                    12, 14,
                    "NMF 2M reply = minimum (12-14 HCP)")
                return
            if (rebid.level == 2 and rebid.suit == other_major):
                c.set_suit_len(
                    other_major, 4, 4,
                    f"NMF reply 2{_suit_char(other_major)} shows "
                    "4 cards in this major (no 3-card support)")
                return
            if rebid.level == 2 and rebid.suit == Suit.NOTRUMP:
                c.tighten_hcp(
                    12, 13,
                    "NMF 2NT reply = no major fit, minimum 12-13")
                return
            if (rebid.level == 3
                    and rebid.suit == responder_first_major):
                c.set_suit_len(
                    rebid.suit, 3, 3,
                    f"NMF jump 3{_suit_char(rebid.suit)} = 3-card "
                    "support + maximum (14 HCP)")
                c.tighten_hcp(
                    14, 14,
                    "NMF jump-reply = 14 HCP maximum")
                return

    # Only handle the first rebid here (the most common case).
    if rebid_count != 1:
        return

    # 1NT rebid by minor opener after major response.
    if (opening.level == 1
            and opening.suit in (Suit.CLUBS, Suit.DIAMONDS)
            and rebid.level == 1 and rebid.suit == Suit.NOTRUMP):
        c.tighten_hcp(12, 14,
                      "1NT rebid = 12-14 HCP balanced")
        # Caps each suit at 5 (balanced shape).
        for su in (Suit.SPADES, Suit.HEARTS,
                   Suit.DIAMONDS, Suit.CLUBS):
            c.set_suit_len(su, 0, 5,
                           "1NT rebid promises balanced")
        return

    # 2NT jump rebid: 18-19 (after 1m-1M or 1M-1Y response).
    if rebid.level == 2 and rebid.suit == Suit.NOTRUMP:
        c.tighten_hcp(18, 19,
                      "2NT jump rebid = 18-19 HCP balanced")
        for su in (Suit.SPADES, Suit.HEARTS,
                   Suit.DIAMONDS, Suit.CLUBS):
            c.set_suit_len(su, 0, 5,
                           "2NT jump rebid promises balanced")
        return

    # 3NT jump rebid: 19-21 (after 1m-1M response).
    if rebid.level == 3 and rebid.suit == Suit.NOTRUMP:
        c.tighten_hcp(19, 21,
                      "3NT jump rebid = 19-21 HCP balanced")
        return

    # Simple rebid of own suit at 2-level: 12-15 HCP, 6+ in that suit.
    if (rebid.level == 2 and rebid.suit == opening.suit
            and rebid.suit not in (None, Suit.NOTRUMP)):
        c.tighten_hcp(12, 15,
                      f"Simple rebid of own {_suit_char(opening.suit)} "
                      "= 12-15 HCP")
        c.set_suit_len(opening.suit, 6, 13,
                       f"Rebid of own {_suit_char(opening.suit)} "
                       "promises 6+ cards")
        return

    # ---- Long-suit game try (1M - 2M - 3-of-new-suit by opener) ----
    # After partner raises 1M to 2M (showing 6-9), opener with
    # 14-16 HCP asks for help in a side suit by bidding it at the
    # 3-level. Promises 3+ in that suit (typically length) and
    # invites partner to bid game with extras or trump quality.
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and responder_bid is not None
            and responder_bid.level == 2
            and responder_bid.suit == opening.suit
            and rebid.level == 3
            and rebid.suit is not None
            and rebid.suit != Suit.NOTRUMP
            and rebid.suit != opening.suit):
        c.tighten_hcp(14, 16,
                      f"Long-suit game try 3{_suit_char(rebid.suit)} "
                      "= 14-16 HCP, asks responder for help in "
                      f"{_suit_char(rebid.suit)}")
        return

    # 1m-1M-1OM: opener bids the OTHER major at the 1-level.
    # Shows 4+ in the new major; non-reverse range (12-17 HCP).
    if (opening.level == 1
            and opening.suit in (Suit.CLUBS, Suit.DIAMONDS)
            and responder_bid is not None
            and responder_bid.level == 1
            and responder_bid.suit in (Suit.HEARTS, Suit.SPADES)
            and rebid.level == 1
            and rebid.suit in (Suit.HEARTS, Suit.SPADES)
            and rebid.suit != responder_bid.suit):
        c.tighten_hcp(12, 17,
                      f"1{_suit_char(rebid.suit)} rebid shows 4+ "
                      f"{_suit_char(rebid.suit)}, 12-17 HCP "
                      "(non-reverse)")
        c.set_suit_len(rebid.suit, 4, 13,
                       f"1{_suit_char(rebid.suit)} rebid promises 4+ "
                       f"{_suit_char(rebid.suit)}")
        return

    # Jump rebid of own suit: 16-18 HCP, 6+.
    if (rebid.level == 3 and rebid.suit == opening.suit
            and rebid.suit not in (None, Suit.NOTRUMP)):
        c.tighten_hcp(16, 18,
                      f"Jump rebid of own {_suit_char(opening.suit)} "
                      "= 16-18 HCP, 6+ cards")
        c.set_suit_len(opening.suit, 6, 13,
                       f"Jump rebid promises 6+ "
                       f"{_suit_char(opening.suit)}")
        return

    # 4-of-major rebid: meaning depends on what responder did.
    if (rebid.level == 4
            and rebid.suit in (Suit.HEARTS, Suit.SPADES)
            and rebid.suit == opening.suit):
        # If responder raised already (1M-2M / 1M-3M / 1M-4M),
        # opener's 4M is ACCEPTING the raise, not a jump rebid.
        if (responder_bid is not None
                and responder_bid.suit == opening.suit):
            if responder_bid.level == 3:
                # 1M-3M-4M: accept limit raise — 14+ HCP (else pass)
                c.tighten_hcp(14, 21,
                              f"After limit raise, 4{_suit_char(opening.suit)} "
                              "= 14+ HCP accepting game")
            elif responder_bid.level == 2:
                # 1M-2M-4M: jump to game from simple raise — 18+ HCP
                c.tighten_hcp(18, 21,
                              f"After simple raise, 4{_suit_char(opening.suit)} "
                              "= 18+ HCP driving to game")
            # 1M-4M-4M: nonsensical; fall through to natural.
            return
        # No prior raise → genuine jump rebid showing 19+ + 6-card suit.
        c.tighten_hcp(19, 37,
                      f"4{_suit_char(opening.suit)} jump rebid "
                      "= 19+ HCP, 6+ cards")
        c.set_suit_len(opening.suit, 6, 13,
                       "4M jump rebid promises 6+")
        return

    # Simple raise of partner's major (1M-1Y - 2M would be NS-NS,
    # not opener's rebid; skip).

    # New-suit rebid at the 2-level — could be reverse (17+) or
    # simple new suit (12-17). We need to compare suit ranks.
    if (rebid.level == 2 and rebid.suit is not None
            and rebid.suit != Suit.NOTRUMP
            and rebid.suit != opening.suit):
        # Determine if it's a "reverse" — second suit higher in
        # rank than the opening suit.
        rank_order = [Suit.CLUBS, Suit.DIAMONDS,
                      Suit.HEARTS, Suit.SPADES]
        op_rank = rank_order.index(opening.suit)
        new_rank = rank_order.index(rebid.suit)
        # If responder bid at the 1-level and opener now rebids a
        # higher-ranking suit at the 2-level → reverse.
        is_reverse = (responder_bid is not None
                      and responder_bid.level == 1
                      and new_rank > op_rank)
        if is_reverse:
            c.tighten_hcp(17, 21,
                          f"Reverse 2{_suit_char(rebid.suit)} = 17+ HCP, "
                          "longer first suit")
            c.set_suit_len(opening.suit, 5, 13,
                           "Reverse promises 5+ in first suit")
            c.set_suit_len(rebid.suit, 4, 13,
                           "Reverse promises 4+ in second suit")


def _annotate_response(c: SeatConstraints, opening: Bid,
                       response: Bid, system,
                       nt_min: int = 15, nt_max: int = 17,
                       intervening_bid: Optional[Bid] = None,
                       responder_passed_first: bool = False):
    """Constrain responder from their first call.

    `intervening_bid` is the opponent's overcall (if any) that came
    between opener and responder. Lets us recognize negative doubles
    and other interference-specific responses.
    """
    # Negative double over an overcall: responder doubles for
    # takeout, promising values + 4+ in the UNBID major(s).
    if (response.is_double and intervening_bid is not None
            and not intervening_bid.is_pass
            and not intervening_bid.is_double
            and not intervening_bid.is_redouble
            and intervening_bid.suit is not None
            and opening.suit is not None):
        # The unbid major(s): the major not bid by opener or overcaller.
        bid_majors = set()
        if opening.suit in (Suit.HEARTS, Suit.SPADES):
            bid_majors.add(opening.suit)
        if intervening_bid.suit in (Suit.HEARTS, Suit.SPADES):
            bid_majors.add(intervening_bid.suit)
        unbid_majors = ({Suit.HEARTS, Suit.SPADES} - bid_majors)
        if unbid_majors:
            tags = "/".join(_suit_char(m)
                             for m in sorted(unbid_majors,
                                             key=lambda x: x.value))
            c.tighten_hcp(6, 18,
                          f"Negative double over "
                          f"{intervening_bid.level}{_suit_char(intervening_bid.suit)} "
                          f"= 6+ HCP, 4+ {tags}")
            for m in unbid_majors:
                c.set_suit_len(m, 4, 13,
                               f"Negative double promises 4+ "
                               f"{_suit_char(m)}")
        else:
            c.tighten_hcp(6, 18,
                          "Negative double = 6+ HCP, "
                          "takeout values")
        return
    if response.is_pass or response.is_double or response.is_redouble:
        return
    if opening.suit is None or response.suit is None:
        return

    # Lebensohl after weak-2 doubled:  weak2 - (X by partner) -
    # responder bids 2NT as a "pass-or-correct" relay forcing
    # partner to bid 3C. Shows 0-7 HCP with a long minor.
    # NB: the bidder of THIS response is the partner of the doubler
    # — i.e. RHO of the original 2-opener.
    if (opening.level == 2
            and opening.suit in (Suit.HEARTS, Suit.SPADES, Suit.DIAMONDS)
            and intervening_bid is not None
            and intervening_bid.is_double
            and response.level == 2 and response.suit == Suit.NOTRUMP):
        c.tighten_hcp(0, 7,
                      f"Lebensohl 2NT after takeout dbl of weak "
                      f"2{_suit_char(opening.suit)} = 0-7 HCP, "
                      "relays to 3♣ pass-or-correct")
        return

    # Drury: passed-hand 2♣ over partner's 3rd/4th-seat 1M opening
    # = 3-card limit raise, 10-12 HCP. Only fires when this is the
    # responder's FIRST non-pass call AND they passed earlier.
    if (responder_passed_first
            and opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and response.level == 2
            and response.suit == Suit.CLUBS):
        c.tighten_hcp(10, 12,
                      f"Drury 2♣ as a passed hand = 10-12 HCP "
                      f"with 3+ card support for partner's "
                      f"{_suit_char(opening.suit)} (artificial)")
        c.set_suit_len(opening.suit, 3, 13,
                       "Drury promises 3+ card support for "
                       "partner's major")
        return

    # Western Cuebid: 1m/1M-(overcall)-cuebid-of-overcall-suit
    # by responder = limit raise+ of partner's suit with support.
    # 10+ HCP, 3+ trumps in partner's suit (for major openings),
    # or game-forcing values with stoppers (for minor openings).
    if (intervening_bid is not None
            and not intervening_bid.is_pass
            and not intervening_bid.is_double
            and not intervening_bid.is_redouble
            and intervening_bid.suit is not None
            and intervening_bid.suit != Suit.NOTRUMP
            and response.suit == intervening_bid.suit):
        c.tighten_hcp(10, 21,
                      f"Cuebid {response.level}{_suit_char(response.suit)} "
                      f"of overcaller's suit = limit raise+ "
                      f"with support for partner's "
                      f"{_suit_char(opening.suit)}")
        if opening.suit in (Suit.HEARTS, Suit.SPADES):
            c.set_suit_len(opening.suit, 3, 13,
                           "Cuebid limit raise = 3+ trumps")
        return

    # 1m / 1M - 1-of-suit response: 6+ HCP, 4+ in the response suit.
    if opening.level == 1 and response.level == 1 \
            and response.suit != Suit.NOTRUMP:
        c.tighten_hcp(6, 18,
                      f"1{_suit_char(response.suit)} response "
                      "= 6+ HCP, 4+ in suit")
        if response.suit in (Suit.HEARTS, Suit.SPADES):
            c.set_suit_len(response.suit, 4, 13,
                           f"1{_suit_char(response.suit)} response "
                           "= 4+ in major")
        else:
            c.set_suit_len(response.suit, 4, 13,
                           f"1{_suit_char(response.suit)} response "
                           "= 4+ in minor")
        return

    # 1m / 1M - 1NT response: 6-9 HCP (or 6-12 in 2/1 forcing-NT).
    if opening.level == 1 and response.level == 1 \
            and response.suit == Suit.NOTRUMP:
        # Could be semi-forcing/forcing in 2/1; range is 6-12.
        c.tighten_hcp(6, 12,
                      "1NT response = 6-9 (or 6-12 forcing NT) HCP")
        return

    # 1m / 1M - 2NT (Jacoby 2NT or natural inv).
    if opening.level == 1 and response.level == 2 \
            and response.suit == Suit.NOTRUMP:
        # Could be J2NT (13+ GF, 4+ trumps) or natural (11-12).
        # Leave HCP loose since style-dependent.
        c.tighten_hcp(11, 21,
                      "2NT response = invitational+ (11+)")
        return

    # 1m / 1M - 3NT: 13-15 game.
    if opening.level == 1 and response.level == 3 \
            and response.suit == Suit.NOTRUMP:
        c.tighten_hcp(13, 15,
                      "3NT response = 13-15 HCP game")
        return

    # 1M - 2M raise: 6-9 (constructive) up to 10 HCP, 3+ support.
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and response.level == 2 and response.suit == opening.suit):
        c.tighten_hcp(6, 10,
                      f"2{_suit_char(opening.suit)} simple raise "
                      "= 6-9 HCP")
        c.set_suit_len(opening.suit, 3, 13,
                       f"Raise promises 3+ "
                       f"{_suit_char(opening.suit)}")
        return

    # 1M - 3M limit raise: 10-12 HCP, 4+ support.
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and response.level == 3 and response.suit == opening.suit):
        c.tighten_hcp(10, 12,
                      f"3{_suit_char(opening.suit)} limit raise "
                      "= 10-12 HCP")
        c.set_suit_len(opening.suit, 4, 13,
                       "Limit raise promises 4+ trumps")
        return

    # 1M - 4M preemptive raise: 0-9 HCP, 5+ trumps.
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and response.level == 4 and response.suit == opening.suit):
        c.tighten_hcp(0, 9,
                      f"4{_suit_char(opening.suit)} preempt raise "
                      "= 0-9 HCP, 5+ trumps")
        c.set_suit_len(opening.suit, 5, 13,
                       "Preempt raise = 5+ trumps")
        return

    # 1m / 1M - 2-of-new-suit (2/1 GF) response: 13+ HCP.
    # Skip when opener was 1NT — those are Stayman/transfer
    # responses and have their own (later) checks below.
    if (opening.level == 1
            and opening.suit != Suit.NOTRUMP
            and response.level == 2
            and response.suit is not None
            and response.suit != Suit.NOTRUMP
            and response.suit != opening.suit):
        c.tighten_hcp(11, 21,
                      f"2{_suit_char(response.suit)} response "
                      "= invitational+ (11+ in Std., GF in 2/1)")
        c.set_suit_len(response.suit, 4, 13,
                       f"2{_suit_char(response.suit)} response "
                       "= 4+ cards (often 5+)")
        return

    # ---- After 1NT opening ----
    if opening.level == 1 and opening.suit == Suit.NOTRUMP:
        # 1NT - 2C (Stayman): responder promises ≥1 4-card major.
        # Strength is 8+ HCP (invitational+); transfer-only with 0-7
        # would not use Stayman.
        if response.level == 2 and response.suit == Suit.CLUBS:
            c.tighten_hcp(8, 21,
                          "Stayman 2♣ over 1NT = 8+ HCP, ≥1 4-card major")
            # Either spades or hearts is 4+. We can't pin which to
            # 4+ but we can cap each at ≤4 if the hand were
            # absolutely guaranteed to have ONE — we leave the
            # specific suit unconstrained and rely on the "≥1
            # 4-card major" intent in the HCP reason for teaching.
            return
        # No transfers (Standard Acol): 2D/2H/2S are natural weak takeouts.
        from .auction_context import _plays_transfers
        if (response.level == 2 and response.suit in (Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES)
                and not _plays_transfers(system, 1)):
            c.tighten_hcp(0, 11, f"1NT-2{_suit_char(response.suit)} = weak takeout")
            c.set_suit_len(response.suit, 5, 13,
                           f"1NT-2{_suit_char(response.suit)} weak takeout = 5+ cards")
            return
        # 1NT - 2D (Jacoby transfer to hearts): 5+ hearts, any HCP.
        if response.level == 2 and response.suit == Suit.DIAMONDS:
            c.set_suit_len(Suit.HEARTS, 5, 13,
                           "Jacoby transfer 2♦ over 1NT = 5+ ♥")
            return
        # 1NT - 2H (Jacoby transfer to spades): 5+ spades.
        if response.level == 2 and response.suit == Suit.HEARTS:
            c.set_suit_len(Suit.SPADES, 5, 13,
                           "Jacoby transfer 2♥ over 1NT = 5+ ♠")
            return
        # 1NT - 2S (range ask / minor relay in some styles): leave loose.
        # 1NT - 4D (Texas transfer to 4H): 6+ hearts, game values.
        if response.level == 4 and response.suit == Suit.DIAMONDS:
            c.tighten_hcp(10, 18,
                          "Texas 4♦ over 1NT = 6+ ♥, game values")
            c.set_suit_len(Suit.HEARTS, 6, 13,
                           "Texas 4♦ promises 6+ ♥")
            return
        # 1NT - 4H (Texas transfer to 4S).
        if response.level == 4 and response.suit == Suit.HEARTS:
            c.tighten_hcp(10, 18,
                          "Texas 4♥ over 1NT = 6+ ♠, game values")
            c.set_suit_len(Suit.SPADES, 6, 13,
                           "Texas 4♥ promises 6+ ♠")
            return
        # 1NT - 4NT (quantitative): 16-17 HCP balanced inviting 6NT.
        if response.level == 4 and response.suit == Suit.NOTRUMP:
            c.tighten_hcp(16, 17,
                          "4NT over 1NT = quantitative, 16-17 HCP")
            for su in (Suit.SPADES, Suit.HEARTS,
                       Suit.DIAMONDS, Suit.CLUBS):
                c.set_suit_len(su, 2, 5,
                               "4NT quant promises balanced")
            return
        # 1NT - 3C / 3D (some styles = minor invite or 6+ minor).
        # Leave loose.

    # ---- Benjamin 2D responses (F-FRA-M) ----
    if (opening.level == 2 and opening.suit == Suit.DIAMONDS
            and getattr(system, "benjamin", False)):
        if response.level == 2 and response.suit == Suit.HEARTS:
            c.tighten_hcp(0, 7, "2♥ over Benjamin 2♦ = 0-7 HCP")
        elif response.level == 2 and response.suit == Suit.SPADES:
            c.tighten_hcp(8, 21, "2♠ over Benjamin 2♦ = 8+ HCP")
        elif response.level == 2 and response.suit == Suit.NOTRUMP:
            c.tighten_hcp(8, 10, "2NT over Benjamin 2♦ = 8-10 balanced")
        elif response.level == 3 and response.suit == Suit.NOTRUMP:
            c.tighten_hcp(11, 13, "3NT over Benjamin 2♦ = 11-13 balanced")
        elif response.level == 3 and response.suit is not None:
            c.tighten_hcp(3, 8, "3-level answer to Benjamin 2♦ = good six-card suit, 0-8")
            c.set_suit_len(response.suit, 6, 6, "good six-card suit")
        return
    # 2C - 2D = waiting (could be 0-7 OR 8+ without a 5-card suit).
    if (opening.level == 2 and opening.suit == Suit.CLUBS
            and getattr(system, "strong_open_call", "2C") == "2C"
            and response.level == 2 and response.suit == Suit.DIAMONDS):
        c.tighten_hcp(0, 21,
                      "2♦ waiting response to 2♣ — either 0-7 HCP "
                      "OR 8+ without a 5-card suit")
        return
    # 2C - 2H / 2S = positive: 5+ in the major, 8+ HCP, 1½+ controls.
    if (opening.level == 2 and opening.suit == Suit.CLUBS
            and getattr(system, "strong_open_call", "2C") == "2C"
            and response.level == 2
            and response.suit in (Suit.HEARTS, Suit.SPADES)):
        c.tighten_hcp(8, 21,
                      f"2{_suit_char(response.suit)} positive over "
                      "strong 2♣ = 5+ cards, 8+ HCP, 1½+ controls")
        c.set_suit_len(response.suit, 5, 13,
                       "Positive response promises 5+ in the suit")
        return
    # 2C - 3C / 3D = positive in a minor (similar requirements,
    # one level higher to leave 2NT free for a balanced positive
    # in some styles).
    if (opening.level == 2 and opening.suit == Suit.CLUBS
            and getattr(system, "strong_open_call", "2C") == "2C"
            and response.level == 3
            and response.suit in (Suit.CLUBS, Suit.DIAMONDS)):
        c.tighten_hcp(8, 21,
                      f"3{_suit_char(response.suit)} positive over "
                      "strong 2♣ = 5+ cards, 8+ HCP")
        c.set_suit_len(response.suit, 5, 13,
                       "Positive in minor promises 5+ in the suit")
        return

    # ---- Responses to weak 2 / 3-level preempt ----
    # Weak 2X - 2NT (Ogust / feature ask): invitational+ with
    # ~14+ HCP asking opener about hand quality.
    if (opening.level == 2
            and opening.suit in (Suit.HEARTS, Suit.SPADES, Suit.DIAMONDS)
            and response.level == 2 and response.suit == Suit.NOTRUMP):
        c.tighten_hcp(14, 21,
                      f"2NT over weak 2{_suit_char(opening.suit)} = "
                      "14+ HCP forcing inquiry (Ogust / feature)")
        return
    # Weak 2X - 3X (same suit) = preemptive raise / lead-directing,
    # 0-9 HCP with 3+ trumps (often blocking the opponents).
    if (opening.level == 2
            and opening.suit in (Suit.HEARTS, Suit.SPADES, Suit.DIAMONDS)
            and response.level == 3 and response.suit == opening.suit):
        c.tighten_hcp(0, 12,
                      f"3{_suit_char(opening.suit)} raise of weak two "
                      "= preemptive / lead-directing, 3+ trumps")
        c.set_suit_len(opening.suit, 3, 13,
                       "Raise of weak two = 3+ trumps")
        return
    # 3X preempt - 5X (jump to game): partner has values for game,
    # expects opener to have 5+ trumps and a decent suit.
    if (opening.level == 3
            and opening.suit in (Suit.CLUBS, Suit.DIAMONDS,
                                  Suit.HEARTS, Suit.SPADES)
            and response.level == 5 and response.suit == opening.suit):
        c.tighten_hcp(10, 21,
                      f"5{_suit_char(opening.suit)} game raise of "
                      "3-level preempt = 10+ HCP, expects to make")
        return

    # ---- Splinter response to 1M (1H-4C/4D/4S, 1S-4C/4D/4H) ----
    # A double-jump in a NEW suit over partner's 1-of-major shows:
    #   • 4+ trumps in opener's major
    #   • Singleton or void in the cuebid suit
    #   • Game-forcing (13+ HCP)
    if (opening.level == 1
            and opening.suit in (Suit.HEARTS, Suit.SPADES)
            and response.level == 4
            and response.suit is not None
            and response.suit != Suit.NOTRUMP
            and response.suit != opening.suit):
        # Filter the 4-of-opener's-major case (handled above).
        # The bid here is a genuine new-suit jump.
        c.tighten_hcp(13, 19,
                      f"Splinter 4{_suit_char(response.suit)} over "
                      f"1{_suit_char(opening.suit)} = 13+ HCP, "
                      f"4+{_suit_char(opening.suit)}, "
                      f"0-1 {_suit_char(response.suit)}")
        c.set_suit_len(opening.suit, 4, 13,
                       "Splinter promises 4+ trumps")
        c.set_suit_len(response.suit, 0, 1,
                       f"Splinter promises 0-1 "
                       f"{_suit_char(response.suit)}")
        return


def _annotate_responder_rebid(c: SeatConstraints, opening: Bid,
                              first_response: Optional[Bid],
                              rebid: Bid,
                              auction: List[Bid] = None,
                              rebid_idx: int = -1,
                              seats_in_order: List[Seat] = None,
                              system=None,
                              opener_c: Optional[SeatConstraints] = None,
                              opener_seat: Optional[Seat] = None):
    """Tighten responder from their second call."""
    # Look back for opener's most recent bid before this rebid —
    # needed to distinguish NMF (after 1NT rebid), 4SF (after
    # 1-of-OM rebid), Smolen (after 2D-Stayman reply), etc.
    opener_rebid: Optional[Bid] = None
    if (auction is not None and seats_in_order is not None
            and opener_seat is not None and rebid_idx > 0):
        for j in range(rebid_idx - 1, -1, -1):
            if seats_in_order[j] == opener_seat:
                bj = auction[j]
                if (not bj.is_pass and not bj.is_double
                        and not bj.is_redouble):
                    opener_rebid = bj
                    break
    if rebid.is_pass or rebid.is_double or rebid.is_redouble:
        return
    if rebid.suit is None:
        return

    # ---- Reverse follow-ups (responder's reaction) ----
    # After 1m-1M-2-of-higher-suit (opener's reverse showing 17+
    # and longer first suit), responder's rebid clarifies the
    # path to game/slam:
    #   2-of-own-major     = 6-card major rebid (denies 4 of opener's)
    #   2NT                = "Lebensohl"-style signoff invite (5-7 HCP)
    #   3-of-opener-suit   = preference to opener's first suit
    #   3NT                = 9-10 HCP balanced, going to game
    if (opening.level == 1
            and opening.suit in (Suit.CLUBS, Suit.DIAMONDS)
            and opener_rebid is not None
            and opener_rebid.level == 2
            and opener_rebid.suit is not None
            and opener_rebid.suit != Suit.NOTRUMP
            and opener_rebid.suit != opening.suit
            and first_response is not None
            and first_response.level == 1
            and first_response.suit in (Suit.HEARTS, Suit.SPADES)):
        # Is opener_rebid a reverse? (Higher rank than opening.suit.)
        rank_order = [Suit.CLUBS, Suit.DIAMONDS,
                      Suit.HEARTS, Suit.SPADES]
        is_reverse = (rank_order.index(opener_rebid.suit)
                      > rank_order.index(opening.suit))
        if is_reverse:
            if (rebid.level == 2
                    and rebid.suit == first_response.suit):
                c.set_suit_len(first_response.suit, 6, 13,
                               f"Rebidding own major after a "
                               "reverse = 6+ card suit")
                return
            if rebid.level == 2 and rebid.suit == Suit.NOTRUMP:
                c.tighten_hcp(5, 8,
                              "2NT after a reverse = minimum "
                              "(5-7 HCP), wanting to stop low")
                return
            if (rebid.level == 3
                    and rebid.suit == opening.suit):
                c.set_suit_len(opening.suit, 3, 13,
                               "Preference back to opener's first "
                               "suit (3+ cards there)")
                return

    # ---- Stayman over 2NT (1NT-2C parallel after 2NT opener
    # or 2C-2D-2NT). Responder bids 3C to ask for 4-card major.
    if (rebid.level == 3 and rebid.suit == Suit.CLUBS
            and opener_rebid is not None
            and opener_rebid.level == 2
            and opener_rebid.suit == Suit.NOTRUMP):
        c.tighten_hcp(4, 21,
                      "Stayman 3♣ over 2NT-rebid = ≥1 4-card major, "
                      "looking for major fit")
        return

    # ---- New Minor Forcing (NMF) ----
    # 1m - 1M - 1NT - 2-of-OTHER-minor by responder = forcing
    # inquiry asking opener for 3-card support of M or for a
    # 4-card OTHER major. Promises ~11+ HCP.
    if (opening.level == 1
            and opening.suit in (Suit.CLUBS, Suit.DIAMONDS)
            and first_response is not None
            and first_response.level == 1
            and first_response.suit in (Suit.HEARTS, Suit.SPADES)
            and opener_rebid is not None
            and opener_rebid.level == 1
            and opener_rebid.suit == Suit.NOTRUMP
            and rebid.level == 2
            and rebid.suit in (Suit.CLUBS, Suit.DIAMONDS)
            and rebid.suit != opening.suit):
        c.tighten_hcp(11, 21,
                      f"NMF 2{_suit_char(rebid.suit)} = 11+ HCP, "
                      "asks opener for 3-card support or a 4-card "
                      "other major (artificial)")
        return

    # ---- Fourth-Suit Forcing (4SF) ----
    # After 1m-1M-1OM, responder bids the 4th suit (the only one
    # not yet bid) as an artificial GF inquiry: 12+ HCP.
    if (opening.level == 1
            and opening.suit is not None
            and opening.suit != Suit.NOTRUMP
            and first_response is not None
            and first_response.level == 1
            and first_response.suit is not None
            and first_response.suit != Suit.NOTRUMP
            and opener_rebid is not None
            and opener_rebid.level == 1
            and opener_rebid.suit is not None
            and opener_rebid.suit != Suit.NOTRUMP
            and rebid.level == 2
            and rebid.suit is not None
            and rebid.suit != Suit.NOTRUMP):
        bid_suits = {opening.suit, first_response.suit, opener_rebid.suit}
        if (len(bid_suits) == 3 and rebid.suit not in bid_suits):
            c.tighten_hcp(12, 21,
                          f"4SF 2{_suit_char(rebid.suit)} = "
                          "12+ HCP, GF artificial inquiry "
                          "(fourth suit forcing)")
            return

    # ---- Smolen ----
    # 1NT - 2C - 2D - 3-of-major: 5-card OTHER major + 4-card
    # bid major + game-forcing values. Lets opener choose strain.
    if (opening.level == 1 and opening.suit == Suit.NOTRUMP
            and first_response is not None
            and first_response.level == 2
            and first_response.suit == Suit.CLUBS
            and opener_rebid is not None
            and opener_rebid.level == 2
            and opener_rebid.suit == Suit.DIAMONDS
            and rebid.level == 3
            and rebid.suit in (Suit.HEARTS, Suit.SPADES)):
        other_major = (Suit.SPADES if rebid.suit == Suit.HEARTS
                       else Suit.HEARTS)
        c.tighten_hcp(10, 21,
                      f"Smolen 3{_suit_char(rebid.suit)} = "
                      f"4 {_suit_char(rebid.suit)}, "
                      f"5+ {_suit_char(other_major)}, GF values")
        c.set_suit_len(rebid.suit, 4, 4,
                       "Smolen promises exactly 4 in the BID major")
        c.set_suit_len(other_major, 5, 13,
                       "Smolen promises 5+ in the OTHER major")
        return

    # ---- After Stayman: responder's rebid clarifies HCP ----
    if (opening.level == 1 and opening.suit == Suit.NOTRUMP
            and first_response is not None
            and first_response.level == 2
            and first_response.suit == Suit.CLUBS):
        # 1NT-2C-2D-3NT: 10-15 HCP balanced game
        if rebid.level == 3 and rebid.suit == Suit.NOTRUMP:
            c.tighten_hcp(10, 15,
                          "1NT-Stayman-3NT = 10-15 HCP, no major fit")
            return
        # 1NT-2C-2D-2NT: 8-9 HCP invitational
        if rebid.level == 2 and rebid.suit == Suit.NOTRUMP:
            c.tighten_hcp(8, 9,
                          "1NT-Stayman-2NT = 8-9 HCP invitational")
            return
        # ---- Stayman with major-suit response by opener ----
        # 1NT-2C-2{H/S}-? — opener showed a 4-card major. The 'major'
        # variable below is opener's revealed major (or None when
        # responder's rebid doesn't fit a follow-up pattern).
        # We need access to opener's intermediate bid here, but the
        # full auction isn't passed in. Use the responder's rebid
        # suit to infer the context — most patterns are unambiguous
        # given just (rebid.suit, rebid.level).
        # 1NT-2C-2X-3X (3X = same as opener's major) — invitational
        # raise of opener's major, 8-9 HCP, 4-card trump support.
        if (rebid.level == 3
                and rebid.suit in (Suit.HEARTS, Suit.SPADES)):
            c.tighten_hcp(8, 9,
                          f"After Stayman, jump to 3{_suit_char(rebid.suit)} "
                          f"= 8-9 HCP invitational raise, 4 "
                          f"{_suit_char(rebid.suit)}")
            c.set_suit_len(rebid.suit, 4, 5,
                           "Stayman + raise of opener's major = "
                           "exactly 4 trumps")
            return
        # 1NT-2C-2X-4X — drive to game in opener's major.
        if (rebid.level == 4
                and rebid.suit in (Suit.HEARTS, Suit.SPADES)):
            c.tighten_hcp(10, 15,
                          f"After Stayman, 4{_suit_char(rebid.suit)} = "
                          "10-15 HCP game, 4-card support")
            c.set_suit_len(rebid.suit, 4, 5,
                           "Stayman + 4-of-major = exactly 4 trumps")
            return

    # ---- After Jacoby transfer: responder's rebid clarifies length+HCP
    from .auction_context import _plays_transfers
    if (opening.level == 1 and opening.suit == Suit.NOTRUMP
            and _plays_transfers(system, 1)
            and first_response is not None
            and first_response.level == 2
            and first_response.suit in (Suit.DIAMONDS, Suit.HEARTS)):
        transfer_target = (Suit.HEARTS
                           if first_response.suit == Suit.DIAMONDS
                           else Suit.SPADES)
        # ...-3M jump = 6+ in major, invitational (8-9)
        if rebid.level == 3 and rebid.suit == transfer_target:
            c.tighten_hcp(8, 9,
                          f"1NT-transfer-3{_suit_char(transfer_target)} "
                          "= 6+ cards, 8-9 HCP invitational")
            c.set_suit_len(transfer_target, 6, 13,
                           "Transfer + jump shows 6+ cards")
            return
        # ...-4M = 6+ in major, game values
        if rebid.level == 4 and rebid.suit == transfer_target:
            c.tighten_hcp(10, 15,
                          f"1NT-transfer-4{_suit_char(transfer_target)} "
                          "= 6+ cards, game values")
            c.set_suit_len(transfer_target, 6, 13,
                           "Transfer + game shows 6+ cards")
            return
        # ...-2NT/3NT = 5-card major + game values
        if rebid.suit == Suit.NOTRUMP and rebid.level in (2, 3):
            c.tighten_hcp(8 if rebid.level == 2 else 10,
                          17,
                          f"1NT-transfer-{rebid.level}NT = "
                          "5 cards in major, choice of game")
            return

    # 4-of-major from responder = signoff / preempt-type
    if (rebid.level == 4
            and rebid.suit in (Suit.HEARTS, Suit.SPADES)
            and first_response is not None
            and first_response.suit == rebid.suit):
        c.tighten_hcp(10, 19,
                      "4M signoff/raise = 10-19 HCP")


def _annotate_blackwood_ask(c: SeatConstraints, b: Bid,
                            agreed_trump: Optional[Suit]):
    """Tighten the 4NT-asker's HCP — slam interest implies extras."""
    if agreed_trump is None:
        # 4NT without an agreed suit may be quantitative; handled
        # elsewhere. Don't constrain here.
        return
    if b.level == 4:
        c.tighten_hcp(15, 25,
                      f"4NT after {_suit_char(agreed_trump)} fit "
                      "= RKC Blackwood, slam interest (~15+ HCP)")
    elif b.level == 5:
        # 5NT = king-ask after a positive keycard response;
        # implies all 5 keycards held by partnership.
        c.tighten_hcp(15, 25,
                      "5NT king-ask = all 5 keycards present, slam+ values")


def _annotate_keycard_response(c: SeatConstraints, b: Bid,
                               agreed_trump: Optional[Suit]):
    """Pin trump-king holding from a 1430-style keycard reply.

    Doesn't change HCP much (the responder's HCP is already
    constrained by earlier bids), but the response COULD tell us
    whether the trump K is held — which matters for opening leads
    + finesse direction.

    1430 responses:
      5♣ = 1 or 4 keycards
      5♦ = 0 or 3 keycards
      5♥ = 2 or 5, no trump Q
      5♠ = 2 or 5, with trump Q
    """
    if agreed_trump is None or b.suit is None:
        return
    # This is more "honor-level" inference than HCP/length.
    # For now we just leave a teaching reason — the MC sampler
    # can't easily filter on specific honors without changing
    # its representation.
    if b.suit == Suit.CLUBS and b.level == 5:
        c.reasons.append(
            "5♣ keycard reply = 1 or 4 keycards (out of 5)")
    elif b.suit == Suit.DIAMONDS and b.level == 5:
        c.reasons.append(
            "5♦ keycard reply = 0 or 3 keycards")
    elif b.suit == Suit.HEARTS and b.level == 5:
        c.reasons.append(
            "5♥ keycard reply = 2 or 5 keycards, no trump Q")
    elif b.suit == Suit.SPADES and b.level == 5:
        c.reasons.append(
            "5♠ keycard reply = 2 or 5 keycards + trump Q")


def _annotate_overcall(c: SeatConstraints, b: Bid, system,
                       opening: Optional[Bid] = None):
    """Constrain an opponent from their first non-pass call."""
    # Takeout double of an opening bid: 12+ HCP with shortness
    # in opener's suit + support for the other suits. The classic
    # "double for takeout" shape.
    if b.is_double and opening is not None and opening.suit is not None:
        c.tighten_hcp(12, 21,
                      f"Takeout double of {opening.level}"
                      f"{_suit_char(opening.suit) if opening.suit != Suit.NOTRUMP else 'NT'} "
                      "= 12+ HCP, shortness in opener's suit, "
                      "support for the other 3 suits")
        if opening.suit != Suit.NOTRUMP:
            c.set_suit_len(opening.suit, 0, 2,
                           "Takeout double promises 0-2 cards "
                           "in opener's suit")
        return
    if b.is_pass or b.is_double or b.is_redouble:
        return
    if b.suit is None:
        return
    if b.level == 1 and b.suit == Suit.NOTRUMP:
        c.tighten_hcp(15, 18,
                      "1NT overcall = 15-18 HCP balanced")
        for su in (Suit.SPADES, Suit.HEARTS,
                   Suit.DIAMONDS, Suit.CLUBS):
            c.set_suit_len(su, 2, 5,
                           "1NT overcall = balanced")
        return
    if b.level == 1:
        c.tighten_hcp(8, 16,
                      f"1{_suit_char(b.suit)} overcall = 8-16 HCP")
        c.set_suit_len(b.suit, 5, 13,
                       f"1-level overcall = 5+ {_suit_char(b.suit)}")
        return
    if b.level == 2 and b.suit != Suit.NOTRUMP:
        c.tighten_hcp(10, 16,
                      f"2{_suit_char(b.suit)} overcall = 10-16 HCP")
        c.set_suit_len(b.suit, 5, 13,
                       f"2-level overcall = 5+ {_suit_char(b.suit)} "
                       "(usually 6)")
        return
    if b.level == 2 and b.suit == Suit.NOTRUMP:
        # Unusual 2NT or natural 2NT overcall.
        c.tighten_hcp(0, 37,
                      "2NT overcall — natural or Unusual (style-dependent)")
        return
    if b.level == 3 and b.suit != Suit.NOTRUMP:
        c.tighten_hcp(4, 10,
                      f"3{_suit_char(b.suit)} preempt overcall "
                      "= 5-10 HCP")
        c.set_suit_len(b.suit, 6, 7, "3-level preempt = 7 cards")
        return


# ---------------------------------------------------------------------------
# Play-time tightening — refines constraints as cards are played.
# ---------------------------------------------------------------------------

_HCP_OF_RANK_VAL = (4, 3, 2, 1)   # A,K,Q,J → HCP; others = 0


def tighten_from_play(board: BoardState,
                      constraints: Dict[Seat, SeatConstraints]
                      ) -> Dict[Seat, SeatConstraints]:
    """Update `constraints` in place using info from played cards.

    Three sources of inference at play-time:

      1. **Show-outs.** When a seat plays off-suit on a led suit,
         their original holding in that suit is exactly the number
         of cards they've already played of it (usually 0).

      2. **Played honor → HCP floor.** Each seat's starting HCP is
         at least the sum of HCP played from that seat's hand.
         (If East has already played the ♥A, East had ≥ 4 HCP to
         start.)

      3. **Played cards cap original suit-length-MAX.** If a seat
         has already played 3 spades, their original spade length
         was ≥ 3 (a floor, not a ceiling).

    `tighten_from_play` does NOT model cross-seat HCP-sum inference
    or count signals from defensive carding — those would tighten
    further but require more state.
    """
    # Collect plays in chronological order with the seat that
    # played each card.
    plays: List[Tuple[Seat, "Card"]] = []
    for trick in (getattr(board, "tricks", None) or []):
        if not trick.cards:
            continue
        seat = trick.leader
        for c in trick.cards:
            plays.append((seat, c))
            seat = Seat((seat + 1) % 4)
    cur = getattr(board, "current_trick", None)
    if cur is not None and cur.cards:
        seat = cur.leader
        for c in cur.cards:
            plays.append((seat, c))
            seat = Seat((seat + 1) % 4)

    if not plays:
        return constraints

    # Per-seat aggregates.
    played_by_seat: Dict[Seat, List["Card"]] = {s: [] for s in Seat}
    played_hcp: Dict[Seat, int] = {s: 0 for s in Seat}
    for seat, card in plays:
        played_by_seat[seat].append(card)
        if card.rank.value <= 3:
            played_hcp[seat] += _HCP_OF_RANK_VAL[card.rank.value]

    # Detect show-outs.
    shown_out: Dict[Seat, Set[Suit]] = {s: set() for s in Seat}
    all_tricks = list(getattr(board, "tricks", None) or [])
    if cur is not None and cur.cards:
        all_tricks.append(cur)
    for trick in all_tricks:
        if not trick.cards:
            continue
        lead_suit = trick.cards[0].suit
        seat = trick.leader
        for c in trick.cards:
            if c.suit != lead_suit and seat != trick.leader:
                shown_out[seat].add(lead_suit)
            seat = Seat((seat + 1) % 4)

    # Tighten each seat's constraints.
    for s in Seat:
        c = constraints.setdefault(s, SeatConstraints())
        # 1. Show-out → original suit length = exactly cards
        # played of that suit by this seat.
        for su in shown_out[s]:
            played_count = sum(
                1 for card in played_by_seat[s] if card.suit == su)
            c.set_suit_len(su, played_count, played_count,
                           f"Showed out of {_suit_char(su)} after "
                           f"playing {played_count} (original length "
                           f"= {played_count})")
        # 2. Floor on original length per suit: must be ≥ played
        # cards of that suit.
        for suit_val in (0, 1, 2, 3):
            su = Suit(suit_val)
            if su in shown_out[s]:
                continue  # already pinned exact above
            played_count = sum(
                1 for card in played_by_seat[s] if card.suit == su)
            if played_count > 0:
                cur_lo, cur_hi = c.suit_len.get(suit_val, (0, 13))
                if played_count > cur_lo:
                    c.set_suit_len(
                        su, played_count, cur_hi,
                        f"Played {played_count} {_suit_char(su)} "
                        f"so far — original length ≥ {played_count}")
        # 3. HCP floor from honors already played.
        if played_hcp[s] > c.hcp_min:
            c.tighten_hcp(
                played_hcp[s], c.hcp_max,
                f"Played {played_hcp[s]} HCP — starting HCP ≥ "
                f"{played_hcp[s]}")
    return constraints


# ---------------------------------------------------------------------------
# Adapters — return constraints in the legacy dict shapes engine.py expects.
# ---------------------------------------------------------------------------

def hcp_ranges_from_constraints(constraints
                                ) -> Dict[Seat, Tuple[int, int]]:
    """Adapter: {Seat: (hcp_min, hcp_max)} — engine.py's sampler
    consumes this directly."""
    return {s: (c.hcp_min, c.hcp_max)
            for s, c in constraints.items()}


def shape_ranges_from_constraints(constraints
                                  ) -> Dict[Seat, Dict[int, Tuple[int, int]]]:
    """Adapter: {Seat: {suit_value: (min, max)}}."""
    return {s: dict(c.suit_len) for s, c in constraints.items()}


# ---------------------------------------------------------------------------
# Teaching-mode explanation.
# ---------------------------------------------------------------------------

def explain_each_bid(board: BoardState, system) -> List[Dict]:
    """Walk the auction once; for each bid, return what it showed.

    Returns one dict per bid:
        {'index': int, 'bidder': Seat, 'bid': Bid,
         'reasons': [str, ...]}
    where `reasons` is the list of natural-language inference rules
    that fired specifically for THIS bid (the delta added to the
    bidder's accumulated reasoning when this bid was made).

    Implementation: replay the auction one bid at a time, calling
    infer_constraints on each prefix and diffing the bidder's
    reasons. Cheap — no DDS calls anywhere on this path.

    Used by the UI's Auction Interpretation dialog (View menu).
    """
    auction = list(getattr(board, "auction", None) or [])
    if not auction:
        return []
    seats_in_order: List[Seat] = []
    seat = board.dealer
    for _ in auction:
        seats_in_order.append(seat)
        seat = Seat((seat + 1) % 4)

    out: List[Dict] = []
    accumulated = {s: 0 for s in Seat}
    for i in range(1, len(auction) + 1):
        partial = BoardState(
            board_number=getattr(board, "board_number", 0),
            dealer=board.dealer,
            vulnerability=board.vulnerability,
            hands=board.hands,
            auction=auction[:i])
        constraints = infer_constraints(partial, system)
        bidder = seats_in_order[i - 1]
        reasons = constraints[bidder].reasons
        # Strip "passed throughout" — it's a partial-auction
        # artifact, not a real per-bid inference.
        reasons = [r for r in reasons
                   if "passed throughout" not in r.lower()]
        delta = reasons[accumulated[bidder]:]
        accumulated[bidder] = len(reasons)
        out.append({
            "index": i - 1,
            "bidder": bidder,
            "bid": auction[i - 1],
            "reasons": delta,
        })
    return out


def explain(board: BoardState, system) -> List[str]:
    """One-line natural-language explanation per seat with inferences.

    Output suitable for showing to a learning user in the
    cardplay UI:

        "From the auction:
          • South (1NT) shows 15-17 HCP balanced (2-5 each suit)
          • North (3NT) shows 13-15 HCP for game
          • East (passed throughout) ≤ 9 HCP"

    Includes play-time tightening (show-outs etc.) when the board
    has tricks played.
    """
    constraints = infer_constraints(board, system)
    constraints = tighten_from_play(board, constraints)
    lines: List[str] = []
    seat_names = {Seat.NORTH: "North", Seat.EAST: "East",
                  Seat.SOUTH: "South", Seat.WEST: "West"}
    for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
        c = constraints[s]
        if (c.hcp_min, c.hcp_max) == (0, 37) and not c.suit_len:
            continue
        parts: List[str] = []
        if (c.hcp_min, c.hcp_max) != (0, 37):
            parts.append(f"{c.hcp_min}-{c.hcp_max} HCP")
        for suit_val, (lo, hi) in sorted(c.suit_len.items()):
            su = Suit(suit_val)
            if (lo, hi) == (0, 13):
                continue
            parts.append(f"{lo}-{hi} {_suit_char(su)}")
        if parts:
            why = "; ".join(c.reasons[-3:])  # last few reasons
            lines.append(f"{seat_names[s]}: {', '.join(parts)}  "
                         f"[{why}]")
    return lines
