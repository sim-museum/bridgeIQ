"""Auction-semantic primitives — Option 1 of the slam-architecture roadmap.

Problem: biq's bidder re-derives "what's going on in this auction" at
every decision site by re-walking state.bids with ad-hoc heuristics
(_agreed_trump, _is_rkc_context, _trump_set_level, splinter detection,
GF detection scattered through each system path…). This causes:
  * The same primitive disagrees across sites (e.g. _agreed_trump's
    fallback returns the last suit bid; _trump_set_level rejects single
    mentions — Deal 39 phantom slam came from this disagreement).
  * Adding any new convention requires touching N call sites.
  * Each convention's edge cases hide in a different function.

Solution: walk state.bids ONCE per decide_bid call, materialise the
auction's high-level meaning as a typed AuctionContext, and let every
downstream site read from the context rather than re-derive.

Phase A (this file): trump_set + slam_zone_entered + gf_established +
controls_shown. Forcing-context derivation is sketched but not yet
populated. Convention-state and captain are placeholders for Phase B.

Migration policy: existing functions like _agreed_trump and
_is_rkc_context remain as wrappers calling the context. Sites can
migrate at their own pace; the context is computed once per decide_bid
and threaded through.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from .models import Bid, Seat, Suit

if TYPE_CHECKING:
    from .native_bidder import AuctionState  # noqa: F401


class TrumpMechanism(Enum):
    """How was the trump suit established?

    Different mechanisms have different conventional follow-up. A
    direct raise leaves the auction natural; a Jacoby 2NT response
    expects opener to bid a structured shortness/range message; a
    splinter expects partner to evaluate trump quality.
    """
    DIRECT_RAISE = "direct_raise"       # 1H-2H, 1H-3H, 1S-2S
    JUMP_RAISE = "jump_raise"           # 1H-3H (limit), 1S-4S (preempt/strong)
    SPLINTER = "splinter"               # 1H-4C/4D/3S, 1S-4C/4D/4H
    JACOBY_2NT = "jacoby_2nt"           # 1M-2NT (artificial M agreement)
    TRANSFER_ACCEPT = "transfer_accept" # 1NT-2H-2S (1NT-2D-2H also)
    REBID_AGREEMENT = "rebid_agreement" # 1C-1H-2H (opener raises responder)


class ForcingContext(Enum):
    """What kind of action is legal here?

    INVITATIONAL allows pass; FORCING_ONE_ROUND requires a bid this
    round only; GAME_FORCE requires the partnership to reach game;
    SLAM_FORCE means slam-going (someone has committed via cuebid /
    4NT). COMPETITIVE means opponents have intervened.
    """
    NONE = "none"
    INVITATIONAL = "invitational"
    FORCING_ONE_ROUND = "forcing_one_round"
    GAME_FORCE = "game_force"
    SLAM_FORCE = "slam_force"
    COMPETITIVE = "competitive"


@dataclass
class TrumpAgreement:
    """A concrete trump-agreement event in the auction."""
    suit: Suit
    level: int            # the level at which trump was set
    mechanism: TrumpMechanism
    setter: Seat          # which seat made the trump-setting call
    bid_index: int        # position of the setting bid in state.bids


@dataclass
class AuctionContext:
    """Derived auction-semantic primitives, computed once per decide.

    All fields default to None / empty so partial population during
    incremental migration is safe. Sites that read a still-not-derived
    field should fall back to legacy ad-hoc detection.
    """
    # Trump
    trump: Optional[TrumpAgreement] = None

    # GF / slam zone
    gf_established: bool = False
    slam_zone_entered: bool = False        # a 4NT-as-RKC, cuebid, splinter,
                                            # Jacoby 2NT, or 5NT-GSF has been
                                            # bid by the partnership.

    # Controls each seat has shown via cuebid (Phase 3 + future)
    controls_shown: Dict[Seat, List[Suit]] = field(default_factory=dict)

    # Last 4NT bid (if any) and whether RKC/quantitative/other
    last_4nt_bidder: Optional[Seat] = None
    last_4nt_is_rkc: bool = False
    last_4nt_is_quantitative: bool = False

    # NT-rebid by opener — used to disambiguate 4NT and shut down
    # quantitative-mistaken-as-RKC paths.
    opener_rebid_nt: bool = False

    # Opener's first bid (cached for many lookups)
    opening_bid: Optional[Bid] = None
    opener_seat: Optional[Seat] = None
    opening_was_strong_artificial: bool = False

    # Forcing context for the next bid (per the seat about to bid)
    forcing: ForcingContext = ForcingContext.NONE

    # Convention-being-executed slot. Currently populated for:
    #   • "GERBER" — 4C ace-ask after partner's OPENING 1NT/2NT/3NT.
    #   • "STAYMAN" — 2C asking for major after partner's 1NT/2NT.
    #   • "JACOBY_TRANSFER" — 2D/2H/2S/3D over partner's 1NT (or 3-of-suit
    #     over 2NT).
    #   • "FOURTH_SUIT_FORCING" — responder's bid of the 4th unbid suit
    #     at 2+ level after three suits already bid by partnership.
    #   • "RKC" — once 4NT-as-RKC has been bid.
    # Future: SMOLEN, LEBENSOHL, NMF, DRURY, BERGEN, ...
    convention: Optional[str] = None
    convention_step: int = 0

    # Captain — the seat in charge of slam-go decisions. Initially the
    # opener; transitions to whoever shows extras / RKC asker / cuebid
    # initiator. Used by downstream sites that need to know "who
    # decides 4NT vs cuebid vs sign-off here".
    captain: Optional[Seat] = None

    # Gerber-specific: the 4C bid if classified as Gerber, and the
    # bidder + responder for the step-response routing.
    last_4c_is_gerber: bool = False
    last_4c_bidder: Optional[Seat] = None

    # Legacy fallback: the last NATURAL suit bid by the partnership
    # before the first 4NT (if any). Used by _agreed_trump when no
    # true trump-set was found, to preserve the historical "last
    # suit = implicit trump" behaviour for single-mention auctions
    # like 1H-3S-4NT (responder jump-shift then opener Blackwood).
    fallback_last_suit: Optional[Suit] = None


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

_NT = Suit.NOTRUMP
_MAJORS = (Suit.HEARTS, Suit.SPADES)

# Strong 2C, opener rebid NT, then our side bid a suit at the 4 level: the
# 4NT that follows asks keycards, as the reference answers it (2C-2D-2H-2S-2NT-3D-
# 3H-4C-4D-4H-4NT: 5C), not a quantitative invite to pass.
STRONG_2C_4NT_RKC = os.environ.get("BIQ_STRONG_2C_4NT_RKC", "1") == "1"
BERGEN_NOT_GF = os.environ.get("BIQ_BERGEN_OPENER", "1") == "1"
A1_NOT_RAISE = os.environ.get("BIQ_FORCING_FIXES", "1") == "1"

# The system of the decision being made (set by native_bidder.decide_bid), so
# callers that pass no system still read 1NT-2D/2H right: natural weak
# takeouts in Standard Acol (no transfers), Jacoby transfers elsewhere.
_CURRENT_SYSTEM = None


def set_current_system(system) -> None:
    global _CURRENT_SYSTEM
    _CURRENT_SYSTEM = system


def _weak_minor_transfer(opening, b) -> bool:
    """1NT-3C as the (weak) transfer to diamonds (the reference F-FRA-M
    `nt.minor_transfer_3c_for_diamonds`): alerted, not a jump shift."""
    sy = _CURRENT_SYSTEM
    return (opening is not None and opening.level == 1 and opening.suit == Suit.NOTRUMP
            and b.level == 3 and b.suit == Suit.CLUBS and sy is not None
            and hasattr(sy, "has") and sy.has("nt.minor_transfer_3c_for_diamonds"))


def _plays_transfers(system, nt_level: int) -> bool:
    rr = getattr(system, "raw_rules", None) if system is not None else None
    if not rr:
        return True
    pre = "nt.transfers" if nt_level == 1 else "nt2.transfers"
    return any(k.startswith(pre) for k in rr)
_SUITS_LO_TO_HI = (Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES)
_CUEBID_SUIT_RANK = {Suit.CLUBS: 0, Suit.DIAMONDS: 1,
                     Suit.HEARTS: 2, Suit.SPADES: 3,
                     Suit.NOTRUMP: 4}


def derive_context(state: 'AuctionState', system=None) -> AuctionContext:
    """Single-pass walk of state.bids → fully-populated AuctionContext.

    The order of operations matters: we detect trump-set as early as
    possible (so subsequent suit bids can be classified as cuebids,
    not new trumps), then carry the trump forward when classifying
    4NT, 5NT, and slam-zone entries.
    """
    ctx = AuctionContext()

    # --- Opening identification -----------------------------------
    for s, b in state.bids:
        if b.is_pass:
            continue
        ctx.opener_seat = s
        ctx.opening_bid = b
        ctx.opening_was_strong_artificial = (
            b.level == 1 and b.suit == Suit.CLUBS and b.alert
        )
        break

    if ctx.opener_seat is None:
        return ctx  # passout — nothing to derive

    partner_of_opener = ctx.opener_seat.partner()
    opener_side = {ctx.opener_seat, partner_of_opener}
    # "My side" — the partnership of the seat about to bid. The legacy
    # `_trump_set_level` and `_agreed_trump` worked off MY-side bids
    # so that competitive auctions where I am the overcaller still
    # find my own trump-set. Use the same convention here.
    my_side = {state.seat, state.seat.partner()}
    ns = {Seat.NORTH, Seat.SOUTH}

    # --- Walk bids, tracking primitives ---------------------------
    # Per-side bookkeeping for trump detection: who's bid each suit.
    seats_bid_suit: Dict[Suit, Set[Seat]] = {}
    # Per-side bookkeeping for cuebid detection (controls shown).
    cuebids_after_trump: Dict[Seat, List[Suit]] = {}

    # Track opener's bids (after first) for the opener_rebid_nt flag.
    opener_post_first_bids: List[Bid] = []
    opener_first_seen = False

    # Cut-off: the FIRST 4NT in the auction ends trump-set / fallback
    # consideration. Bids at or after 4NT are RKC machinery, not
    # natural — without the cut-off, the responder's `5♣ = 0/3 keys`
    # answer would mis-promote to trump candidate.
    first_4nt_idx = None
    fallback_natural = False        # an unalerted suit bid seen by my side
    for idx, (s, b) in enumerate(state.bids):
        if b.level == 4 and b.suit == _NT and not b.is_pass:
            first_4nt_idx = idx
            break

    for idx, (s, b) in enumerate(state.bids):
        # 4NT tracking and opener-NT-rebid tracking run REGARDLESS of
        # the 4NT cut-off (legacy behavior treats opener's own 4NT as
        # an NT-rebid for quantitative-gate purposes).
        # Track opener's bids AFTER first (used for opener_rebid_nt
        # quantitative-gate). Exclude bids AT/AFTER the first 4NT —
        # opener's own 4NT is the RKC bid being classified, not a
        # prior quantitative-NT-rebid. (Deal 33 fix: Smolen 3H +
        # opener's 4NT was being misclassified quantitative because
        # the 4NT itself counted as an NT-rebid.)
        if (s == ctx.opener_seat
                and not b.is_pass
                and not b.is_double
                and not b.is_redouble
                and (first_4nt_idx is None or idx < first_4nt_idx)):
            if not opener_first_seen:
                opener_first_seen = True
            else:
                opener_post_first_bids.append(b)
        if first_4nt_idx is not None and idx >= first_4nt_idx:
            # Past the 4NT cut-off — skip trump / cuebid / fallback
            # detection beyond this point. The 4NT itself (RKC vs
            # quantitative) is classified AFTER this loop completes,
            # so post-loop trump-set inference (Jacoby 2NT, splinter)
            # can affect the classification.
            continue
        if b.is_pass or b.is_double or b.is_redouble:
            continue

        # Strong artificial 1C: a positive NT answer (1C-1NT 8-13 bal,
        # 1C-2NT, 1C-3NT) is game forcing too. It must be caught here,
        # before NT bids are skipped below: 1C-1NT-2S-3S was passed out
        # with 19 + 9 HCP (the reference Precision match FRESH64H/I, boards 47
        # and 2).
        if (not ctx.gf_established
                and ctx.opening_was_strong_artificial
                and s == partner_of_opener and s in my_side
                and b.suit == _NT
                and len(opener_post_first_bids) == 0
                and not any(not pb.is_pass for ps, pb in state.bids[:idx]
                            if ps == s)):
            ctx.gf_established = True

        # Skip artificial / NT for trump detection
        if b.suit is None or b.suit == _NT:
            continue
        if s not in my_side:
            continue  # opponents (from MY-side POV) — skip

        # Fallback-last-suit tracking (used by _agreed_trump legacy path).
        # An alerted call after a natural suit is a feature / cue-bid, not
        # a trump suggestion: 2S-2NT-3C(feature)-4NT is RKCB for spades.
        if not b.alert:
            ctx.fallback_last_suit = b.suit
            fallback_natural = True
        elif not fallback_natural:
            ctx.fallback_last_suit = b.suit

        # --- Trump-set detection (only fires once) ---------------
        if ctx.trump is None:
            # Pattern 1: partner is raising my major suit at any level
            if b.suit in _MAJORS:
                prior_seats = seats_bid_suit.get(b.suit, set())
                if prior_seats and s not in prior_seats:
                    # Determine mechanism by level relative to prior bid.
                    prior_levels = []
                    for ps, pb in state.bids[:idx]:
                        if (ps in my_side
                                and pb.suit == b.suit
                                and not pb.is_pass):
                            prior_levels.append(pb.level)
                    min_prior = min(prior_levels) if prior_levels else 1
                    if b.level - min_prior >= 2:
                        mech = TrumpMechanism.JUMP_RAISE
                    else:
                        mech = TrumpMechanism.DIRECT_RAISE
                    # Special case: if opener's MAJOR rebid raises
                    # responder's response, that's REBID_AGREEMENT.
                    if (s == ctx.opener_seat
                            and len(opener_post_first_bids) == 1
                            and opener_post_first_bids[0] is b):
                        mech = TrumpMechanism.REBID_AGREEMENT
                    ctx.trump = TrumpAgreement(
                        suit=b.suit, level=b.level, mechanism=mech,
                        setter=s, bid_index=idx,
                    )
                    # NB: a natural major RAISE does NOT establish game force.
                    # A single raise is 6-9, a jump (limit) raise 10-12, a
                    # competitive raise even less — all invitational at most.
                    # (This block used to set gf_established for any 1-level-
                    # opening raise, which forced biq to game over a simple
                    # raise — e.g. 1S-2S rebid as 3NT on a 12-HCP minimum;
                    # test_gf_established.py guards the three raise cases.)
                    # The genuinely game-forcing raises set gf in their own
                    # blocks: Jacoby 2NT (JACOBY_2NT) and splinter, below.
                    if mech in (TrumpMechanism.JUMP_RAISE,):
                        ctx.slam_zone_entered = b.level >= 4

        # Track this seat's bid in suit (always — used for cuebid
        # classification and trump-mechanism lookup).
        seats_bid_suit.setdefault(b.suit, set()).add(s)

        # --- Game-force trigger detection -----------------------
        # Various conventional bids that establish game force on the
        # spot. Once `gf_established` is set, it stays True.
        if not ctx.gf_established:
            partner_of_bidder = s.partner()
            # 2/1 GF: responder's 2-of-new-suit over partner's 1-of-
            # major opening establishes game force in both 2/1 GF
            # systems and modern SAYC. Matches the legacy
            # _partner_was_forcing detection (no system gate — the
            # convention is now universal across both system families
            # in practice).
            if (ctx.opening_bid is not None
                    and ctx.opening_bid.level == 1
                    and ctx.opening_bid.suit in _MAJORS
                    and s == partner_of_opener
                    and len(opener_post_first_bids) == 0
                    # ^ this is responder's INITIAL response
                    and b.level == 2
                    and b.suit is not None
                    and b.suit != _NT
                    and b.suit != ctx.opening_bid.suit):
                ctx.gf_established = True

            # French (resp.jump_shift_strong): responder's jump
            # to two of a new suit is game forcing; biq passed 1D-2S-3D-3S
            # (French run 261014 bd 137, the reference made 6C).
            sy = system if system is not None else _CURRENT_SYSTEM
            if (ctx.opening_bid is not None
                    and ctx.opening_bid.level == 1
                    and ctx.opening_bid.suit not in (None, _NT)
                    and s == partner_of_opener
                    and len(opener_post_first_bids) == 0
                    and b.level == 2 and b.suit not in (None, _NT)
                    and _CUEBID_SUIT_RANK[b.suit] > _CUEBID_SUIT_RANK[ctx.opening_bid.suit]
                    and not any(not (x.is_pass) for s2, x in state.bids[:idx]
                                if s2 not in opener_side)
                    and sy is not None and hasattr(sy, "has")
                    and sy.has("resp.jump_shift_strong")):
                ctx.gf_established = True

            # Jump shift by responder: only alerted at level 3+ counts
            # as GF here. Level-2 new suits are already covered by the
            # 2/1-GF branch above. Unalerted level-3 jumps are weak
            # (preemptive) in many systems and must not auto-establish
            # GF, so we require the alert flag.
            #
            # Phase D narrowing: level-2 alerted "jump shifts" (e.g.
            # 1D-2S in Precision) were also being treated as GF here,
            # but on random corpus 39477 this turned three deals biq
            # used to win on (Deal 20/42/78 — biq stopped low while
            # The reference over-bid) into ties or losses. The 2/1-GF branch
            # already covers level-2 new-suit responses to 1M; the
            # alerted-level-2 case for other openings is left for
            # future tightening.
            if (ctx.opening_bid is not None
                    and ctx.opening_bid.level == 1
                    and s == partner_of_opener
                    and len(opener_post_first_bids) == 0
                    and b.suit is not None
                    and b.suit != _NT
                    and b.suit != ctx.opening_bid.suit
                    and b.level >= 3
                    and b.alert
                    and not _weak_minor_transfer(ctx.opening_bid, b)
                    # Bergen 1M-3C / 3D: a constructive / limit raise, not a
                    # jump shift (1S-3C-3S: the reference passes, biq "raised").
                    and not (BERGEN_NOT_GF and b.level == 3
                             and ctx.opening_bid.suit in (Suit.HEARTS, Suit.SPADES)
                             and b.suit in (Suit.CLUBS, Suit.DIAMONDS))):
                ctx.gf_established = True

            # Strong artificial 1C opening: any positive response
            # (anything other than the negative 1D) establishes GF.
            if (ctx.opening_was_strong_artificial
                    and s == partner_of_opener
                    and len(opener_post_first_bids) == 0
                    and not (b.level == 1 and b.suit == Suit.DIAMONDS)):
                ctx.gf_established = True

            # Strong 2C opening (SAYC/Acol/French) with positive
            # response — same as above. The 2C opener is artificial
            # 22+ HCP; partner's positive response forces game.
            if (ctx.opening_bid is not None
                    and ctx.opening_bid.level == 2
                    and ctx.opening_bid.suit == Suit.CLUBS
                    and ctx.opening_bid.alert
                    and s == partner_of_opener
                    and len(opener_post_first_bids) == 0
                    # Negative 2D response keeps the auction non-GF
                    # until next round; positive response is GF.
                    and not (b.level == 2 and b.suit == Suit.DIAMONDS)):
                ctx.gf_established = True

            # Reverse by opener: 1-of-suit, then a NEW SUIT at the
            # 2-level that's HIGHER-rank than opener's first suit.
            # One-round forcing, shows extras (15+).
            if (s == ctx.opener_seat
                    and len(opener_post_first_bids) == 1
                    and opener_post_first_bids[0] is b
                    and b.level == 2
                    and b.suit is not None
                    and b.suit != _NT
                    and ctx.opening_bid is not None
                    and ctx.opening_bid.suit is not None
                    and ctx.opening_bid.suit != _NT
                    and _CUEBID_SUIT_RANK.get(b.suit, -1)
                        > _CUEBID_SUIT_RANK.get(ctx.opening_bid.suit, 99)):
                # NOTE: a reverse is one-round-forcing, not GF. We mark
                # it via the forcing field at the end (FORCING_ONE_ROUND).
                # Don't set gf_established here.
                pass

        # --- Cuebid tracking (after trump is set) ---------------
        if ctx.trump is not None and b.suit not in (ctx.trump.suit, _NT):
            # Any side-suit bid after trump-set by the partnership is
            # a cuebid showing first-round control.
            if (b.level >= 4 or
                    (b.level >= 3 and b.suit != ctx.trump.suit)):
                cuebids_after_trump.setdefault(s, []).append(b.suit)
                ctx.slam_zone_entered = True

    # --- Trump-set via Jacoby 2NT (MY partnership opened a major and
    # MY-side responder bid 2NT alerted as artificial).
    if (ctx.trump is None and ctx.opening_bid is not None
            and ctx.opener_seat in my_side):
        opener_major = (ctx.opening_bid.suit
                        if ctx.opening_bid.level == 1
                        and ctx.opening_bid.suit in _MAJORS else None)
        if opener_major is not None:
            for idx, (s, b) in enumerate(state.bids):
                if (s == partner_of_opener and b.suit == _NT
                        and b.level == 2 and b.alert):
                    ctx.trump = TrumpAgreement(
                        suit=opener_major, level=2,
                        mechanism=TrumpMechanism.JACOBY_2NT,
                        setter=s, bid_index=idx,
                    )
                    ctx.gf_established = True
                    ctx.slam_zone_entered = True
                    break

    # --- Trump-set via splinter (alerted jump in new suit by partner
    # of major opener). Conceptually correct — splinter agrees opener's
    # major — but TEMPORARILY DISABLED: with splinter as trump-set,
    # _is_rkc_context routes opener's 4NT through RKC, but opener's
    # _generic_responder_rebid splinter-response code at native_bidder.py
    # line ~4720 has a different hand-eval gate (controls ≥ 4 + losers
    # ≤ 5) that signs off in 4M when not met. Deal 65 in slam corpus
    # 59517: opener with 17 HCP / 2 controls correctly recognises
    # splinter but signs off 4M; the resulting 4M down was a worse
    # contract than the legacy 4NT accidental landing. Re-enable once
    # opener's splinter slam-eval is reworked (playing-tricks model
    # rather than strict controls gate).
    # Phase F: re-enabled now that the splinter slam-eval at
    # _generic_responder_rebid:~4970 uses the playing-tricks + active-
    # HCP gate instead of the strict controls≥4 gate that wrongly
    # signed off in 4M on Deal 65.
    if ctx.trump is None and ctx.opening_bid is not None:
        opener_major = (ctx.opening_bid.suit
                        if ctx.opening_bid.level == 1
                        and ctx.opening_bid.suit in _MAJORS else None)
        if opener_major is not None:
            for idx, (s, b) in enumerate(state.bids):
                if (s == partner_of_opener
                        and b.alert
                        and b.level >= 3
                        and b.suit is not None
                        and b.suit not in (_NT, opener_major)
                        # Bergen 1M-3C / 3D is a raise, not a splinter.
                        and not (BERGEN_NOT_GF and b.level == 3
                                 and b.suit in (Suit.CLUBS, Suit.DIAMONDS))):
                    ctx.trump = TrumpAgreement(
                        suit=opener_major, level=b.level,
                        mechanism=TrumpMechanism.SPLINTER,
                        setter=s, bid_index=idx,
                    )
                    ctx.gf_established = True
                    break

    # --- Opener NT-rebid flag (used by quantitative-4NT detection) ---
    ctx.opener_rebid_nt = any(
        b.suit == _NT for b in opener_post_first_bids)

    # --- Controls shown ----------------------------------------------
    if cuebids_after_trump:
        ctx.controls_shown = dict(cuebids_after_trump)

    # --- Stayman / Jacoby transfer detection (after 1NT/2NT opening) ---
    # These are common conventions that are 90%+ standard across systems
    # — once detected, they unlock structured opener-response logic and
    # captain transitions in downstream sites.
    if (ctx.opening_bid is not None
            and ctx.opening_bid.suit == _NT
            and ctx.opening_bid.level in (1, 2)
            and ctx.opener_seat in my_side):
        # Walk for partner's first non-pass call after the opening NT.
        partner_first_response = None
        partner_first_idx = None
        for idx, (s, b) in enumerate(state.bids):
            if s != partner_of_opener:
                continue
            if b.is_pass or b.is_double or b.is_redouble:
                continue
            partner_first_response = b
            partner_first_idx = idx
            break
        if partner_first_response is not None:
            # Stayman: 2C over 1NT, 3C over 2NT.
            if (partner_first_response.suit == Suit.CLUBS
                    and partner_first_response.level
                        == ctx.opening_bid.level + 1):
                ctx.convention = "STAYMAN"
            elif not _plays_transfers(system if system is not None else _CURRENT_SYSTEM,
                                      ctx.opening_bid.level):
                pass                        # natural (Standard Acol: no transfers)
            # Jacoby transfer to hearts: 2D over 1NT, 3D over 2NT.
            elif (partner_first_response.suit == Suit.DIAMONDS
                    and partner_first_response.level
                        == ctx.opening_bid.level + 1):
                ctx.convention = "JACOBY_TRANSFER"
            # Jacoby transfer to spades: 2H over 1NT, 3H over 2NT.
            elif (partner_first_response.suit == Suit.HEARTS
                    and partner_first_response.level
                        == ctx.opening_bid.level + 1):
                ctx.convention = "JACOBY_TRANSFER"
            # Texas transfer to hearts (over 1NT): 4D = transfer to 4H.
            # Texas transfer to spades: 4H = transfer to 4S.
            elif (ctx.opening_bid.level == 1
                    and partner_first_response.level == 4
                    and partner_first_response.suit
                        in (Suit.DIAMONDS, Suit.HEARTS)
                    and partner_first_response.alert):
                ctx.convention = "JACOBY_TRANSFER"

    # --- Fourth-Suit Forcing: 3 different suits bid by partnership,
    # then the 4th suit at the 2-level+ by responder (artificial GF).
    # Detected only when:
    #   • Partnership has bid 3 distinct suits in the auction
    #   • Responder's bid is the 4th (unbid) suit at 2+ level
    #   • Auction is below 4NT
    if ctx.convention is None and first_4nt_idx is None:
        partnership_suits_bid = set()
        last_partner_bid_idx = None
        last_partner_bid = None
        for idx, (s, b) in enumerate(state.bids):
            if s not in my_side:
                continue
            if b.is_pass or b.is_double or b.is_redouble:
                continue
            if b.suit in (None, _NT):
                continue
            partnership_suits_bid.add(b.suit)
            if s == partner_of_opener:
                last_partner_bid_idx = idx
                last_partner_bid = b
        if (last_partner_bid is not None
                and len(partnership_suits_bid) == 4
                and last_partner_bid.suit in partnership_suits_bid
                and last_partner_bid.level >= 2
                # Last bid must have been the 4th-suit add-on
                and len({_b.suit for _s, _b in state.bids[:last_partner_bid_idx]
                         if _s in my_side
                         and _b.suit not in (None, _NT)
                         and not _b.is_pass}) == 3):
            ctx.convention = "FOURTH_SUIT_FORCING"
            ctx.gf_established = True

    # --- A1: responder's 3rd natural suit at the 3-level = game force.
    # 1-of-a-SUIT opening, a 1-level natural response, opener's natural rebid,
    # then responder introduces a NEW (3rd) natural suit at level 3 — a
    # non-jump forced up by opener's 2-level rebid. A 3-level new suit by
    # responder shows game-forcing values (you don't go there to invite). The
    # underbidding leak: biq parked in a partscore here because no other
    # trigger covers it (it's a 2/1 over a MINOR / the 3rd suit, not the 4th).
    # Gating on a 1-of-SUIT opening + 1-level response structurally excludes
    # NT-opening transfer/Stayman auctions — the class that broke the reverted
    # structural net. Uncontested only. (Deal 3928-79: 1D-1S-2D-3C.)
    if (not ctx.gf_established
            and ctx.opening_bid is not None
            and ctx.opening_bid.level == 1
            and ctx.opening_bid.suit not in (None, _NT)
            and not any(s not in my_side and not b.is_pass
                        for s, b in state.bids)):
        resp = [(i, b) for i, (s, b) in enumerate(state.bids)
                if s == partner_of_opener and not b.is_pass
                and not b.is_double and not b.is_redouble]
        # NB: the 3-level bid's alert flag is NOT checked — a non-jump new
        # suit at the 3-level is natural by construction, and biq's own
        # responder-GF-net marks its forcing 3C with alert=True, which A1 must
        # still recognise. Transfers/Stayman are excluded by the 1-of-SUIT
        # opening gate above, not by this flag.
        if (len(resp) == 2
                and not resp[0][1].alert
                and resp[0][1].level == 1
                and resp[0][1].suit not in (None, _NT)
                and resp[1][1].level == 3
                and resp[1][1].suit not in (None, _NT)
                and resp[1][1].suit not in (ctx.opening_bid.suit,
                                            resp[0][1].suit)
                # A raise of opener's rebid suit (1D-1H-2C-3C) is a limit
                # raise, not a third suit.
                and not (A1_NOT_RAISE and resp[1][1].suit in
                         {b.suit for s, b in state.bids[:resp[1][0]]
                          if s == ctx.opener_seat})):
            idx3, b3 = resp[1]
            prior = [b for s, b in state.bids[:idx3]
                     if not b.is_pass and not b.is_double
                     and not b.is_redouble and b.suit is not None]
            if prior:
                pc = prior[-1]
                r3 = _CUEBID_SUIT_RANK.get(b3.suit, -1)
                rpc = _CUEBID_SUIT_RANK.get(pc.suit, 99)
                min_lvl = pc.level + (0 if r3 > rpc else 1)
                if b3.level == min_lvl:          # non-jump → forcing to game
                    ctx.gf_established = True

    # --- Gerber detection: 4C as direct ace-ask after partner's
    # opening 1NT / 2NT / 3NT, no opp intervention. Looks STRICTLY at:
    #   • The PARTNERSHIP's first non-pass bid is 1NT/2NT/3NT.
    #   • Partner's second-or-later non-pass bid is 4C JUMP (skips
    #     levels above the opening NT — 1NT-2C would not be Gerber).
    #   • No opp intervention before the 4C.
    # In SAYC, 4C after Stayman/Jacoby is splinter/quantitative, so
    # we require the 4C to come IMMEDIATELY after the opening NT.
    for idx, (s, b) in enumerate(state.bids):
        if b.is_pass or b.is_double or b.is_redouble:
            continue
        if not (b.level == 4 and b.suit == Suit.CLUBS):
            continue
        # The bidder must be partner of the opener
        if s != partner_of_opener:
            continue
        # Opening must be 1NT/2NT/3NT
        if not (ctx.opening_bid is not None
                and ctx.opening_bid.suit == _NT
                and ctx.opening_bid.level <= 3):
            break
        # Gambling 3NT (open.3nt.gambling): 4C is pass-or-correct, not Gerber.
        sy = system if system is not None else _CURRENT_SYSTEM
        if (ctx.opening_bid.level == 3 and sy is not None and hasattr(sy, "has")
                and sy.has("open.3nt.gambling")):
            break
        # No opp intervention between opening and the 4C
        opp_acted = False
        for _idx, (_s, _b) in enumerate(state.bids[:idx]):
            if _s in opener_side:
                continue
            if not (_b.is_pass or _b.is_double or _b.is_redouble):
                opp_acted = True
                break
        if opp_acted:
            break
        # Partner's intermediate bids — only the OPENING NT should
        # appear; nothing else by partner before the 4C.
        partner_pre_bids = [(_s, _b) for _s, _b in state.bids[:idx]
                            if _s == s
                            and not _b.is_pass
                            and not _b.is_double
                            and not _b.is_redouble]
        if partner_pre_bids:
            break  # responder already acted with a non-pass — not Gerber
        ctx.last_4c_is_gerber = True
        ctx.last_4c_bidder = s
        ctx.convention = "GERBER"
        break

    # --- ForcingContext derivation ---------------------------------
    # Combines the gf_established, slam_zone_entered, and one-round
    # forcing detections into a single field that downstream sites
    # can gate on. Order matters: more-committed forcings override
    # less-committed.
    if ctx.slam_zone_entered:
        ctx.forcing = ForcingContext.SLAM_FORCE
    elif ctx.gf_established:
        ctx.forcing = ForcingContext.GAME_FORCE
    else:
        # Check if opener's last bid was a reverse (one-round forcing)
        # — detected during the main loop but not promoted to GF.
        # Also check overcaller-side competitive context.
        opps_acted = False
        for s, b in state.bids:
            if s not in my_side and not b.is_pass:
                opps_acted = True
                break
        if opps_acted:
            ctx.forcing = ForcingContext.COMPETITIVE
        else:
            # Reverse detection: opener's 2nd bid was a HIGHER-rank
            # new suit at 2-level.
            if (len(opener_post_first_bids) >= 1
                    and ctx.opening_bid is not None
                    and ctx.opening_bid.suit not in (None, _NT)):
                r = opener_post_first_bids[0]
                if (r.level == 2 and r.suit not in (None, _NT,
                                                     ctx.opening_bid.suit)
                        and _CUEBID_SUIT_RANK.get(r.suit, -1)
                            > _CUEBID_SUIT_RANK.get(ctx.opening_bid.suit, 99)):
                    ctx.forcing = ForcingContext.FORCING_ONE_ROUND

    # --- Captain detection ----------------------------------------
    # Captain = the seat in charge of slam-go decisions. Transitions:
    #   • Default: opener.
    #   • After a GF response (responder's 2/1, splinter, Jacoby 2NT):
    #     responder becomes captain (showed extras; opener describes).
    #   • After 4NT (RKC): the 4NT bidder is captain (they read the
    #     response and decide).
    #   • After cuebid initiation: the cuebid initiator is captain
    #     until partner responds; then it's mutual until 4NT.
    if ctx.opener_seat is not None:
        ctx.captain = ctx.opener_seat
        # Responder takes over after a GF-establishing first bid that
        # showed extras (NOT a simple 2/1 — that goes to opener describing).
        # Practically: splinter, Jacoby 2NT, jump-shift → responder
        # becomes captain. Otherwise opener stays captain.
        if ctx.trump is not None and ctx.trump.setter == partner_of_opener:
            if ctx.trump.mechanism in (
                    TrumpMechanism.SPLINTER,
                    TrumpMechanism.JACOBY_2NT):
                ctx.captain = partner_of_opener
        # RKC asker (4NT bidder) takes over once they've bid 4NT.
        if ctx.last_4nt_bidder is not None and ctx.last_4nt_is_rkc:
            ctx.captain = ctx.last_4nt_bidder

    # --- 4NT classification (LAST — ctx.trump may have been set by
    # post-loop Jacoby 2NT / splinter detection above). Apply ALL
    # the gates from the legacy _is_rkc_context here so callers can
    # read ctx.last_4nt_is_rkc / .is_quantitative directly.
    if first_4nt_idx is not None:
        s_4nt, _b_4nt = state.bids[first_4nt_idx]
        ctx.last_4nt_bidder = s_4nt
        ctx.slam_zone_entered = True

        # The natural call immediately before the 4NT (skip passes/X).
        prev_natural_nt = False
        for _k in range(first_4nt_idx - 1, -1, -1):
            _ps, _pb = state.bids[_k]
            if _pb.is_pass or _pb.is_double or _pb.is_redouble:
                continue
            prev_natural_nt = (_pb.suit == _NT)
            break

        # Gate 1: no trump candidate at all → quantitative.
        if ctx.trump is None and ctx.fallback_last_suit is None:
            ctx.last_4nt_is_quantitative = True
        # Gate 2: no FORMAL trump-set, opener rebid NT, and opening
        # wasn't strong artificial → quantitative (Deal 39 pattern).
        elif (ctx.trump is None
                and ctx.opener_rebid_nt
                and not ctx.opening_was_strong_artificial
                and not (STRONG_2C_4NT_RKC
                         and _suit_at_4_after_strong_2c_nt(state, ctx, first_4nt_idx))):
            ctx.last_4nt_is_quantitative = True
        # Gate 3: no trump agreed and the call right before the 4NT was a
        # NT bid — the auction is in notrump (no fit), so 4NT is a
        # quantitative slam invite, not keycards. Covers the strong-
        # artificial 1C balanced auctions (1C-1NT/2NT-4NT, 1C-1M-2NT-4NT)
        # that Gate 2 excludes.
        elif ctx.trump is None and prev_natural_nt:
            ctx.last_4nt_is_quantitative = True
        else:
            ctx.last_4nt_is_rkc = True

    return ctx


def _suit_at_4_after_strong_2c_nt(state: 'AuctionState', ctx: AuctionContext,
                                  idx_4nt: int) -> bool:
    """Strong (alerted) 2C opening, opener's NT rebid, then a suit bid at
    the 4 level by the opening side before the 4NT at `idx_4nt`."""
    op = ctx.opening_bid
    if not (op is not None and op.level == 2 and op.suit == Suit.CLUBS and op.alert):
        return False
    side = {ctx.opener_seat, ctx.opener_seat.partner()}
    nt = [i for i, (s, b) in enumerate(state.bids[:idx_4nt])
          if s == ctx.opener_seat and b.suit == _NT and not b.is_pass
          and not b.is_double and not b.is_redouble]
    return bool(nt) and any(
        s in side and b.level == 4 and b.suit not in (None, _NT)
        and not b.is_pass and not b.is_double and not b.is_redouble
        for s, b in state.bids[nt[-1] + 1:idx_4nt])


def trump_set_via(state: 'AuctionState') -> Optional[TrumpAgreement]:
    """Convenience wrapper — returns the trump agreement object or None.

    Replacement for the legacy `_trump_set_level` that returned
    (level, suit). Callers that only need the suit can do
    `t.suit if t else None`. Callers that need the level use `t.level`.
    """
    return derive_context(state).trump
