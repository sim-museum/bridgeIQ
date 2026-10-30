"""System-aware bid meaning lookup for the Information-about-bids window.

Given a (bid, auction, seat, dealer, BiddingSystem) tuple, returns

    (points_str, suit_length_str, help_text, is_artificial)

where `help_text` is a short label like "Stayman", "Precision strong 1C",
"Jacoby transfer to hearts", etc. The same return shape was previously
emitted by `MainWindow._get_bid_interpretation`; this module replaces
the SAYC-hardcoded version with logic that reads the active
`BiddingSystem` spec so the labels change when the user switches
between SAYC / Precision / Acol / 2-over-1 / etc.

Kept separate from `native_bidder.py` because that module *chooses* the
bid (and needs the hand) — this one *labels* it (auction context only).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .bidding_systems import BiddingSystem, get_system
from .models import Bid, Seat, Suit
from .native_bidder import parse_auction


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def describe_bid(bid: Bid,
                 auction_before: List[Bid],
                 seat: Seat,
                 dealer: Seat,
                 system: BiddingSystem) -> Tuple[str, str, str, bool]:
    """Return (points, length, help, is_artificial) for a bid in context."""

    # Parse the auction *before* the bid we're labelling, so my_bids /
    # partner_bids do not yet contain the call we're describing.
    state = parse_auction(seat, dealer, list(auction_before))
    auction = auction_before

    # --- Special calls ----------------------------------------------------
    if bid.is_pass:
        return _describe_pass(auction, seat, dealer, system)
    if bid.is_double:
        return _describe_double(auction, seat, dealer, system)
    if bid.is_redouble:
        return ("10+", "", "Redouble", False)

    # --- Opening calls (no prior non-pass) --------------------------------
    if state.opening_bid is None or all(b.is_pass for b in auction):
        return _describe_opening(bid, system)

    # --- Conventional calls over partner's opening ------------------------
    opener_seat = state.opener_seat
    opening = state.opening_bid
    we_opened = (opener_seat is not None
                 and opener_seat in (seat, seat.partner()))

    if we_opened and opening is not None:
        partner_opened = (opener_seat == seat.partner())
        partner_bids = state.partner_bids
        my_bids = state.my_bids

        if partner_opened and not my_bids:
            res = _describe_response_to_partner_opening(bid, opening, system, state)
            if res is not None:
                return res

        # Opener's rebid after partner's response.
        if opener_seat == seat and len(my_bids) == 1:
            res = _describe_opener_rebid(bid, opening, partner_bids, system)
            if res is not None:
                return res

    # --- Overcalls (RHO/LHO opened, we are overcalling) -------------------
    if not we_opened and state.opening_bid is not None:
        res = _describe_overcall(bid, state.opening_bid, system, state,
                                 auction)
        if res is not None:
            return res

    # --- Fallback: generic strength / length estimate ---------------------
    return _describe_generic(bid)


# ---------------------------------------------------------------------------
# Passes / doubles
# ---------------------------------------------------------------------------

def _describe_pass(auction, seat, dealer, system):
    if not auction:
        return ("0-11", "", "No opening values", False)
    if all(b.is_pass for b in auction):
        return ("0-11", "", "Pass — no opening values", False)
    return ("0-5", "", "Pass — no further action", False)


def _describe_double(auction, seat, dealer, system):
    # Find the last non-pass call to decide takeout vs penalty.
    last = None
    for b in reversed(auction):
        if not b.is_pass and not b.is_double and not b.is_redouble:
            last = b
            break
    if last is None:
        return ("10+", "", "Double", False)

    # If the doubled call is a 1-level natural opening by RHO and we
    # have not yet bid, it's a takeout double in every system we model.
    state = parse_auction(seat, dealer, auction)
    we_have_bid = bool(state.my_bids)

    if last.level >= 1 and last.level <= 2 and not we_have_bid \
            and last.suit not in (Suit.NOTRUMP, None):
        return ("12+", "3+ other suits", "Takeout double", False)

    if last.level == 1 and last.suit == Suit.NOTRUMP:
        return ("15+", "Balanced or strong", "Penalty double of 1NT", False)

    # High-level / after both sides have bid → penalty / cooperative.
    return ("10+", "", "Penalty / cooperative double", False)


# ---------------------------------------------------------------------------
# Openings
# ---------------------------------------------------------------------------

def _describe_opening(bid: Bid, system: BiddingSystem):
    level, suit = bid.level, bid.suit
    nt_min, nt_max = system.one_nt_min_hcp, system.one_nt_max_hcp
    maj_min = system.one_major_card_min
    sys_name = system.name

    # 1-level openings ------------------------------------------------------
    if level == 1:
        if suit == Suit.CLUBS:
            if system.strong_open_call == "1C":
                lo = system.strong_open_min_hcp
                return (f"{lo}+", "Any", f"{sys_name}: strong artificial 1♣", True)
            return ("12-21", "♣ 3+", f"1♣ opening ({sys_name})", False)
        if suit == Suit.DIAMONDS:
            if system.strong_open_call == "1C":
                lo = system.one_diamond_min_hcp
                hi = system.one_diamond_max_hcp
                return (f"{lo}-{hi}", "♦ 2+", f"{sys_name}: limited 1♦ opening", False)
            return ("12-21", "♦ 3+", f"1♦ opening ({sys_name})", False)
        if suit == Suit.HEARTS:
            return ("12-21", f"♥ {maj_min}+", f"1♥ opening ({sys_name})", False)
        if suit == Suit.SPADES:
            return ("12-21", f"♠ {maj_min}+", f"1♠ opening ({sys_name})", False)
        if suit == Suit.NOTRUMP:
            extras = []
            if system.one_nt_allow_five_card_heart \
                    or system.one_nt_allow_five_card_spade:
                extras.append("5cM ok")
            if system.one_nt_allow_six_card_minor:
                extras.append("6cm ok")
            shape = "Balanced" + ("/" + ",".join(extras) if extras else "")
            return (f"{nt_min}-{nt_max}", shape,
                    f"1NT opening, {nt_min}-{nt_max} balanced", False)

    # 2-level openings ------------------------------------------------------
    if level == 2:
        if suit == Suit.CLUBS:
            if system.strong_open_call == "2C":
                return (f"{system.strong_open_min_hcp}+", "Any",
                        f"{sys_name}: strong artificial 2♣, game forcing", True)
            # Precision: 2♣ is limited long-club opening
            return (f"{system.precision_two_clubs_min_hcp}-"
                    f"{system.precision_two_clubs_max_hcp}",
                    "♣ 6+",
                    f"{sys_name}: 6+ clubs, limited", False)
        if suit == Suit.DIAMONDS:
            if system.weak_two_diamonds:
                return (f"{system.weak_two_min_hcp}-{system.weak_two_max_hcp}",
                        "♦ 6", f"Weak two, 6 diamonds", False)
            if system.strong_open_call == "1C":
                return ("10-15", "3-suiter",
                        f"{sys_name}: 2♦ three-suiter ({system.precision_two_diamonds_shape})",
                        True)
            return ("22+", "Game force", f"Strong 2♦ ({sys_name})", True)
        if suit in (Suit.HEARTS, Suit.SPADES):
            if system.weak_two_majors:
                sym = suit.symbol()
                return (f"{system.weak_two_min_hcp}-{system.weak_two_max_hcp}",
                        f"{sym} 6", f"Weak two, 6 {sym}", False)
            sym = suit.symbol()
            return ("18-22", f"{sym} 5+",
                    f"Strong/intermediate 2{sym} ({sys_name})", False)
        if suit == Suit.NOTRUMP:
            return (f"{system.two_nt_min_hcp}-{system.two_nt_max_hcp}",
                    "Balanced",
                    f"2NT opening, "
                    f"{system.two_nt_min_hcp}-{system.two_nt_max_hcp} balanced",
                    False)

    # 3-level openings: preempts / gambling 3NT ----------------------------
    if level == 3:
        if suit == Suit.NOTRUMP and system.has("open.3nt.gambling_any"):
            return ("9-13", "Solid minor",
                    "Gambling 3NT: solid 7+ card minor, no outside stop", True)
        sym = suit.symbol() if suit is not None else "?"
        return ("5-10", f"{sym} 7+", f"Preempt, 7+ {sym}", False)

    # 4-level openings: South-African Texas / Namyats sometimes here
    if level == 4 and suit in (Suit.HEARTS, Suit.SPADES):
        sym = suit.symbol()
        return ("8-13", f"{sym} 8+", f"Preempt 4{sym}, 8+ card suit", False)

    # 4NT solid-minor opening (Precision70 `open.4nt.solid_minor`).
    if level == 4 and suit == Suit.NOTRUMP \
            and system.has("open.4nt.solid_minor"):
        return ("15-19", "Solid 8+ minor",
                "Solid-minor 4NT: ace-asking with self-sufficient minor", True)

    return _describe_generic(bid)


# ---------------------------------------------------------------------------
# Responses to partner's opening
# ---------------------------------------------------------------------------

def _describe_response_to_partner_opening(bid, opening, system, state):
    """Return tuple or None — None means "no convention match; use generic"."""

    # Responses to 1NT openings -------------------------------------------
    if opening.level == 1 and opening.suit == Suit.NOTRUMP:
        return _describe_response_to_1nt(bid, system)

    # Responses to 2NT --------------------------------------------------------
    if opening.level == 2 and opening.suit == Suit.NOTRUMP:
        return _describe_response_to_2nt(bid, system)

    # Responses to strong 2♣ -------------------------------------------------
    if (opening.level == 2 and opening.suit == Suit.CLUBS
            and system.strong_open_call == "2C"):
        return _describe_response_to_strong_2c(bid, system)

    # Responses to Precision 1♣ ---------------------------------------------
    if (opening.level == 1 and opening.suit == Suit.CLUBS
            and system.strong_open_call == "1C"):
        return _describe_response_to_precision_1c(bid, system)

    # Responses to weak twos -------------------------------------------------
    if opening.level == 2 and opening.suit in (Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES) \
            and ((opening.suit == Suit.DIAMONDS and system.weak_two_diamonds)
                 or (opening.suit in (Suit.HEARTS, Suit.SPADES) and system.weak_two_majors)):
        return _describe_response_to_weak_two(bid, opening, system)

    # Responses to one-of-a-suit opening (SAYC-shape) -----------------------
    if opening.level == 1 and opening.suit in (Suit.CLUBS, Suit.DIAMONDS,
                                               Suit.HEARTS, Suit.SPADES):
        return _describe_response_to_1_suit(bid, opening, system)

    return None


def _describe_response_to_1nt(bid, system):
    level, suit = bid.level, bid.suit
    if level == 2 and suit == Suit.CLUBS:
        if system.has("nt.stayman_garbage") and system.has("nt.stayman"):
            return ("0+", "4-4 majors / 4cM",
                    "Stayman (incl. garbage with 4-4 majors)", True)
        if system.has("nt.stayman"):
            return ("8+", "4+ major", "Stayman, asks for 4-card major", True)
        return ("0-7", "♣ 5+", "Natural 2♣ over 1NT", False)
    if level == 2 and suit == Suit.DIAMONDS:
        if system.has("nt.transfers.always") \
                or system.has("nt.transfers"):
            return ("0+", "♥ 5+", "Jacoby transfer to hearts", True)
        return ("0-7", "♦ 5+", "Drop-dead 2♦", False)
    if level == 2 and suit == Suit.HEARTS:
        if system.has("nt.transfers.always") \
                or system.has("nt.transfers"):
            return ("0+", "♠ 5+", "Jacoby transfer to spades", True)
        return ("0-7", "♥ 5+", "Drop-dead 2♥", False)
    if level == 2 and suit == Suit.SPADES:
        if (system.has("nt.minor_transfer_2s_for_clubs")
                or system.has("nt.minor_transfers")
                or system.has("nt.2s_clubs_transfer")):
            return ("0+", "♣ 6+", "Minor-suit transfer to clubs", True)
        return ("0-7", "♠ 5+", "Drop-dead 2♠", False)
    if level == 2 and suit == Suit.NOTRUMP:
        return ("8-9", "Balanced", "Invitational, 8-9 HCP", False)
    if level == 3 and suit == Suit.NOTRUMP:
        return ("10-15", "Balanced", "Sign-off in 3NT", False)
    if level == 3 and suit in (Suit.CLUBS, Suit.DIAMONDS):
        return ("10+", f"{suit.symbol()} 6+", "Invitational minor", False)
    if level == 3 and suit in (Suit.HEARTS, Suit.SPADES):
        if system.has("nt.3major_natural"):
            return ("10-15", f"{suit.symbol()} 6+", "Natural game force in major", False)
        return ("10+", f"{suit.symbol()} 5", "Slammish slam-try in major", False)
    if level == 4 and suit == Suit.CLUBS:
        if system.has("slam.gerber"):
            return ("Slam", "", "Gerber, asks for aces", True)
    if level == 4 and suit in (Suit.HEARTS, Suit.SPADES):
        if system.has("nt.texas"):
            tgt = Suit.HEARTS if suit == Suit.DIAMONDS else Suit.SPADES
            sym = "♥" if suit == Suit.HEARTS else "♠"
            return ("10-15", f"{sym} 6+",
                    "Texas transfer to major game", True)
    return _describe_generic(bid)


def _describe_response_to_2nt(bid, system):
    level, suit = bid.level, bid.suit
    if level == 3 and suit == Suit.CLUBS:
        if system.has("nt2.stayman_3c") or system.has("nt2.stayman"):
            return ("4+", "4+ major", "Stayman over 2NT", True)
    if level == 3 and suit in (Suit.DIAMONDS, Suit.HEARTS):
        if system.has("nt2.jacoby_transfers"):
            tgt = "♥" if suit == Suit.DIAMONDS else "♠"
            return ("0+", f"{tgt} 5+", f"Jacoby transfer to {tgt} over 2NT", True)
    if level == 3 and suit == Suit.NOTRUMP:
        return ("4-10", "Balanced", "Sign-off 3NT", False)
    return _describe_generic(bid)


def _describe_response_to_strong_2c(bid, system):
    level, suit = bid.level, bid.suit
    if level == 2 and suit == Suit.DIAMONDS:
        if system.has("resp.strong_2c.2d_negative") \
                or system.has("resp.strong_2c.2d_waiting"):
            return ("0-7", "Any", "Waiting / negative response to 2♣", True)
        return ("0+", "♦ 5+", "Natural 2♦", False)
    if level == 2 and suit == Suit.NOTRUMP:
        return ("8+", "Balanced", "Positive 2NT, balanced 8+ HCP", False)
    if level == 2 and suit in (Suit.HEARTS, Suit.SPADES):
        return ("8+", f"{suit.symbol()} 5+",
                "Positive: 5+ suit with 2 of top 3 honours", False)
    if level == 3:
        return ("8+", f"{suit.symbol()} 5+" if suit else "", "Positive response", False)
    return _describe_generic(bid)


def _describe_response_to_precision_1c(bid, system):
    level, suit = bid.level, bid.suit
    if level == 1 and suit == Suit.DIAMONDS:
        return ("0-7", "Any", "Precision: negative response to 1♣", True)
    if level == 1 and suit == Suit.HEARTS:
        return ("8+", "♥ 5+", "Precision: 5+ hearts, 8+ HCP", False)
    if level == 1 and suit == Suit.SPADES:
        return ("8+", "♠ 5+", "Precision: 5+ spades, 8+ HCP", False)
    if level == 1 and suit == Suit.NOTRUMP:
        if system.has("resp.strong_1c.switch_1h_1nt"):
            return ("8-13", "Balanced", "Precision: 8-13 balanced", False)
        return ("8-10", "Balanced", "Precision: balanced positive", False)
    if level == 2 and suit == Suit.CLUBS:
        return ("8+", "♣ 5+", "Precision: 5+ clubs, 8+ HCP", False)
    if level == 2 and suit == Suit.DIAMONDS:
        return ("8+", "♦ 5+", "Precision: 5+ diamonds, 8+ HCP", False)
    if level == 2 and suit == Suit.NOTRUMP:
        return ("11+", "Balanced", "Precision: 11+ balanced, slam interest", False)
    return _describe_generic(bid)


def _describe_response_to_weak_two(bid, opening, system):
    level, suit = bid.level, bid.suit
    if level == 2 and suit == Suit.NOTRUMP:
        if system.has("rebid.weak2.ogust") or system.has("resp.weak2.ogust_any"):
            return ("14+", "Inquiry", "Ogust: asks opener about quality / range", True)
        return ("14+", "Inquiry", "Feature ask over weak 2", True)
    if suit == opening.suit and level == opening.level + 1:
        return ("5-10", f"{suit.symbol()} 3+", "Preemptive raise", False)
    return _describe_generic(bid)


def _describe_response_to_1_suit(bid, opening, system):
    level, suit = bid.level, bid.suit
    op_suit = opening.suit
    op_is_major = op_suit in (Suit.HEARTS, Suit.SPADES)
    two_over_one = system.two_over_one_min_hcp

    if level == 1 and suit == Suit.NOTRUMP:
        return ("6-10", "Balanced",
                "1NT response, denies 4-card major / fit", False)

    if level == 1 and suit is not None and suit != Suit.NOTRUMP:
        return ("6+", f"{suit.symbol()} 4+",
                f"New suit at 1-level, 4+ {suit.symbol()}", False)

    if level == 2 and suit == op_suit:
        return ("6-10", f"{suit.symbol()} 3+", "Simple raise", False)

    if level == 2 and suit == Suit.CLUBS and op_suit == Suit.DIAMONDS \
            and system.has("resp.inverted_minors"):
        return ("10+", "♣ 5+", "Inverted minor raise (forcing)", True)
    if level == 2 and suit == Suit.DIAMONDS and op_suit == Suit.CLUBS \
            and system.has("resp.inverted_minors"):
        return ("10+", "♦ 5+", "Inverted minor raise (forcing)", True)

    if level == 2 and suit == Suit.NOTRUMP:
        if op_is_major and system.has("resp.major.truscott_2nt"):
            return ("12+", "Fit",
                    "Jacoby 2NT, GF with 4+ trump support", True)
        if op_is_major and system.has("resp.major.2nt_limit_raise"):
            return ("10-12", f"{op_suit.symbol()} 3+",
                    "Limit raise (Jacoby 2NT as limit)", True)
        return ("13-15", "Balanced", "Natural 2NT, balanced game force", False)

    if level == 2 and suit is not None and suit != op_suit \
            and suit != Suit.NOTRUMP:
        if two_over_one >= 11:
            return ("11+/13+", f"{suit.symbol()} 5+",
                    "2-over-1 — game forcing", False)
        return (f"{two_over_one}+", f"{suit.symbol()} 4+",
                "New suit at 2-level, forcing one round", False)

    if level == 3 and suit == op_suit:
        if op_is_major and system.has("resp.bergen_raises"):
            return ("10-12", f"{suit.symbol()} 4+", "Bergen raise (limit)", True)
        return ("10-12", f"{suit.symbol()} 4+", "Limit raise", False)

    if level == 3 and op_is_major and suit != op_suit \
            and suit != Suit.NOTRUMP \
            and system.has("resp.major.splinters"):
        return ("12-15", f"{op_suit.symbol()} 4+, void/sing in suit",
                "Splinter: GF raise with shortness", True)

    # Truscott 3NT (the reference `resp.major.truscott_3nt`): 1M-3NT shows
    # balanced 13-15 with 3-card support.
    if level == 3 and suit == Suit.NOTRUMP and op_is_major \
            and system.has("resp.major.truscott_3nt"):
        return ("13-15", "Balanced, 3-card support",
                "Truscott 3NT: 13-15 balanced with 3-card support", True)

    if level == 4 and suit == op_suit and op_is_major:
        return ("5-10", f"{suit.symbol()} 5+",
                "Preemptive raise to game", False)

    return _describe_generic(bid)


# ---------------------------------------------------------------------------
# Opener's rebid
# ---------------------------------------------------------------------------

def _describe_opener_rebid(bid, opening, partner_bids, system):
    level, suit = bid.level, bid.suit
    if not partner_bids:
        return None
    resp = partner_bids[0]

    if level == 1 and suit == Suit.NOTRUMP \
            and not opening.suit == Suit.NOTRUMP:
        lo, hi = system.rebid_one_nt_range
        return (f"{lo}-{hi}", "Balanced",
                f"1NT rebid, {lo}-{hi} balanced no major fit", False)

    if level == 2 and suit == Suit.NOTRUMP:
        lo, hi = system.jump_rebid_two_nt_range
        return (f"{lo}-{hi}", "Balanced",
                f"2NT jump rebid, {lo}-{hi} balanced", False)

    return None


# ---------------------------------------------------------------------------
# Overcalls
# ---------------------------------------------------------------------------

_BID_ORDER = [Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES, Suit.NOTRUMP]


def _is_jump(bid, auction) -> bool:
    """True when `bid` is at least one level higher than the cheapest legal
    bid of its denomination over the last contract bid."""
    prev = next((b for b in reversed(auction)
                 if not b.is_pass and not b.is_double and not b.is_redouble),
                None)
    if prev is None:
        return False
    cheapest = (prev.level
                if _BID_ORDER.index(bid.suit) > _BID_ORDER.index(prev.suit)
                else prev.level + 1)
    return bid.level > cheapest


def _describe_advance(bid, state):
    """Later calls by the side that did not open: partner's raises of our
    overcall and our own rebids. None falls through to the generic label."""
    level, suit = bid.level, bid.suit
    if suit is None or suit == Suit.NOTRUMP:
        return None
    sym = suit.symbol()
    partner_named = any(b.suit == suit for b in state.partner_bids
                        if not b.is_double and not b.is_redouble)
    i_named = any(b.suit == suit for b in state.my_bids
                  if not b.is_double and not b.is_redouble)
    if partner_named and i_named:
        return ("", f"{sym} fit", "Competing further in the agreed suit",
                False)
    if partner_named:
        return ("6-10" if level <= 2 else "8+", f"{sym} 3+",
                "Raise of partner's overcall (competitive)", False)
    if i_named:
        return ("8-16", f"{sym} 6+",
                "Rebid of own suit (competitive)", False)
    return None


def _describe_overcall(bid, opening, system, state, auction):
    level, suit = bid.level, bid.suit

    # Only our side's first action is an overcall; later calls are advances
    # (partner's raise, our competitive rebid), never weak jump overcalls.
    if state.my_bids or state.partner_bids:
        return _describe_advance(bid, state)

    # Over 1NT openings: Landy / DONT / Cappelletti -----------------------
    if opening.level == 1 and opening.suit == Suit.NOTRUMP:
        if level == 2 and suit == Suit.CLUBS and system.has("def.1nt.landy"):
            return ("10+", "Both majors", "Landy: 4-4+ in the majors", True)
        if level == 2 and suit == Suit.CLUBS \
                and (system.has("def.1nt.dont") or system.has("def.1nt.cappelletti")):
            return ("10+", "Unknown 1-suiter",
                    "Conventional overcall of 1NT", True)
        return ("10+", f"{suit.symbol() if suit else 'NT'} 5+",
                f"Overcall of 1NT", False)

    # Cuebid of opener's suit = Michaels / Ghestem ------------------------
    if suit == opening.suit:
        if system.has("def.michaels"):
            if opening.suit in (Suit.CLUBS, Suit.DIAMONDS):
                return ("8+", "Both majors", "Michaels: 5-5+ majors", True)
            if opening.suit == Suit.HEARTS:
                return ("8+", "♠ + minor", "Michaels: spades + minor", True)
            if opening.suit == Suit.SPADES:
                return ("8+", "♥ + minor", "Michaels: hearts + minor", True)

    # 2NT direct overcall = Unusual ---------------------------------------
    if level == 2 and suit == Suit.NOTRUMP and system.has("def.unusual_nt"):
        if opening.suit in (Suit.CLUBS, Suit.DIAMONDS):
            return ("8+", "Both majors", "Unusual NT: 5-5+ majors", True)
        return ("8+", "Both minors", "Unusual NT: 5-5+ minors", True)

    # Defence to strong artificial 1C -------------------------------------
    if (opening.level == 1 and opening.suit == Suit.CLUBS
            and bid.is_double
            and system.has("def.strong_1c.double_majors")):
        return ("8+", "Both majors", "Double of strong 1♣ shows majors", True)

    # Jump overcalls = weak preempt --------------------------------------
    if suit is not None and suit != Suit.NOTRUMP and level >= 2:
        if system.has("def.weak_jump_overcalls.all_suits") \
                or system.has("def.weak_jump_overcalls"):
            if _is_jump(bid, auction):
                return ("5-10", f"{suit.symbol()} 6+",
                        "Weak jump overcall", False)

    # Default overcall labels --------------------------------------------
    if level == 1 and suit is not None and suit != Suit.NOTRUMP:
        return ("8-16", f"{suit.symbol()} 5+", "1-level overcall, 5+ suit", False)
    if level == 1 and suit == Suit.NOTRUMP:
        return ("15-18", "Balanced", "1NT overcall, balanced 15-18", False)
    if level == 2 and suit is not None and suit != Suit.NOTRUMP:
        return ("10-16", f"{suit.symbol()} 5+",
                "2-level overcall, 5+ suit", False)

    return None


# ---------------------------------------------------------------------------
# Generic fallback for unclassified suit / NT bids
# ---------------------------------------------------------------------------

def _describe_generic(bid):
    level = bid.level
    suit = bid.suit
    if suit == Suit.NOTRUMP:
        return (f"{10 + level * 3}-{12 + level * 3}", "Balanced",
                f"{level}NT bid", False)
    sym = suit.symbol() if suit is not None else "?"
    length = "4+" if level <= 2 else "5+"
    return (f"{8 + level * 2}+", f"{sym} {length}",
            f"{level}{sym} bid", False)


# ---------------------------------------------------------------------------
# Convenience: load a system from a preferences object
# ---------------------------------------------------------------------------

def system_for_prefs(prefs, side: Optional[str] = None) -> BiddingSystem:
    """Resolve the active `BiddingSystem` from the user's preferences.

    Honours `prefs.bidding_engine` and `prefs.native_bidding_system`.
    When `side` is 'NS' or 'EW', the matching per-pair override
    (`prefs.ns_bidding_system` / `prefs.ew_bidding_system`) wins;
    empty string falls back to the shared `native_bidding_system`.

    When the bidding engine is BEN (the neural net) the labels are
    still useful, so we describe bids using the configured native
    system anyway — that's the system the user has *expressed a
    preference for*, even if BEN isn't bound to it. Falls back to
    SAYC when prefs is missing or malformed.
    """
    name = ""
    try:
        if side == 'NS':
            name = getattr(prefs, 'ns_bidding_system', '') or ''
        elif side == 'EW':
            name = getattr(prefs, 'ew_bidding_system', '') or ''
        if not name:
            name = getattr(prefs, 'native_bidding_system', 'Precision90M') or 'Precision90M'
    except Exception:
        name = 'SAYC'
    return get_system(name)
