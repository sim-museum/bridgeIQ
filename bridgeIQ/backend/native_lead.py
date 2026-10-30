"""Rule-based opening lead engine — reference-style, no neural network.

The opening lead has two pieces:

  1. **Suit selection**: pick the most promising suit to attack.
     Influenced heavily by the auction (partner's bid suits, opp's
     bid suits, NT vs trump contract, declarer strength).
  2. **Card selection**: given the chosen suit, pick the spot card
     by lead convention (4th best, top of sequence, top of
     doubleton, MUD, A from AKxx, etc.).

This module replaces the NN-based `botopeninglead.BotLead`. It
uses no `tensorflow` / `keras` / `nn.*` imports.

Public entry point:

    select_opening_lead(hand, contract, auction, vulnerability) -> Card

`hand` is the leader's hand (LHO of declarer).
`contract` carries level / suit / declarer.
`auction` is the full sequence of Bids (used to find partner's
bid suits and opp's bid suits).
`vulnerability` is used for marginal decisions (we lean a bit
more conservative when vulnerable — fewer aggressive low-card
leads into the opps' suits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import (
    Bid, Card, Contract, Hand, Rank, Seat, Suit, Vulnerability,
)


# ---------------------------------------------------------------------------
# Helpers: rank → token, suit holding rendering
# ---------------------------------------------------------------------------


# Rank token in the order used by `validate_lead`-style holding strings
# (high → low). Lower-case letters never appear; we use 'x' for any
# spot card below T.
_RANK_TOKEN = {
    Rank.ACE: "A", Rank.KING: "K", Rank.QUEEN: "Q", Rank.JACK: "J",
    Rank.TEN: "T", Rank.NINE: "9", Rank.EIGHT: "8", Rank.SEVEN: "7",
    Rank.SIX: "6", Rank.FIVE: "5", Rank.FOUR: "4", Rank.THREE: "3",
    Rank.TWO: "2",
}
# Rank ordering, high to low. Used for sequence detection.
_RANK_ORDER = (
    Rank.ACE, Rank.KING, Rank.QUEEN, Rank.JACK, Rank.TEN,
    Rank.NINE, Rank.EIGHT, Rank.SEVEN, Rank.SIX, Rank.FIVE,
    Rank.FOUR, Rank.THREE, Rank.TWO,
)


def _suit_cards(hand: Hand, suit: Suit) -> List[Card]:
    """Return cards of `suit` in the hand, sorted high → low."""
    return sorted((c for c in hand.cards if c.suit == suit),
                  key=lambda c: _RANK_ORDER.index(c.rank))


def _suit_token_string(cards: List[Card]) -> str:
    """Render a sorted suit holding as a high-to-low token string:
    ``KJ74`` for ♠ K J 7 4. Used by sequence-style pattern matching."""
    return "".join(_RANK_TOKEN[c.rank] for c in cards)


# ---------------------------------------------------------------------------
# Auction parsing — what suits did partner / opps bid?
# ---------------------------------------------------------------------------


def _partner_suits(auction: List[Bid], leader: Seat, dealer: Seat
                   ) -> List[Suit]:
    """Suits partner showed: natural bids, plus the two suits promised by an
    Unusual 2NT or a Michaels cue-bid overcall of the opponents' opening.
    (RUN2-062: partner's Unusual 2NT showed clubs, South led a spade from
    Q984 against 3NT instead and the contract made; a club beat it.)"""
    partner = leader.partner()
    out = _bid_suits_by_seat(auction, dealer, partner)
    order = [Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES]
    seat, opening = dealer, None
    for b in auction:
        if not b.is_pass and opening is None:
            opening = (seat, b)
        elif (seat == partner and opening is not None
              and opening[0] not in (partner, leader)
              and not b.is_pass and not b.is_double and not b.is_redouble
              and opening[1].suit not in (None, Suit.NOTRUMP)
              and opening[1].level == 1):
            op_suit = opening[1].suit
            shown: List[Suit] = []
            if b.level == 2 and b.suit == Suit.NOTRUMP:      # Unusual 2NT
                shown = [x for x in order if x != op_suit][:2]
            elif b.level == 2 and b.suit == op_suit:         # Michaels
                if op_suit in (Suit.CLUBS, Suit.DIAMONDS):
                    shown = [Suit.HEARTS, Suit.SPADES]
                else:
                    shown = [Suit.SPADES if op_suit == Suit.HEARTS
                             else Suit.HEARTS]
                if op_suit in shown:
                    shown = []
            for x in shown:
                if x not in out:
                    out.append(x)
            if op_suit in out and b.suit == op_suit:
                out.remove(op_suit)                         # the cue isn't a suit
            break
        seat = seat.next()
    return out


def _opp_suits(auction: List[Bid], leader: Seat, dealer: Seat
               ) -> List[Suit]:
    """Suits the opponents (declarer + dummy) bid NATURALLY.

    Q-NET does not carry alerts, so artificial calls are inferred: a cue-bid
    of a suit my side bid first; any suit bid after that side has already
    bid notrump (Stayman, transfers, relays, fourth-suit probes); answers
    after their own 4NT; and a strong 2C opening with its 2D waiting reply.
    Treating those as real suits steered leads away from the right suit
    (run 5: RANDOM-033 would not lead its own KT432 after the opponents
    cue-bid clubs; RANDOM-018 avoided AT92 of spades after a relay 2S)."""
    opps = (leader.next(), leader.next().next().next())
    ours = (leader, leader.partner())
    out: List[Suit] = []
    our_suits: List[Suit] = []
    their_nt = False
    their_4nt = False
    seat = dealer
    first_call = True
    strong_2c = False
    for b in auction:
        natural = (not b.is_pass and not b.is_double and not b.is_redouble
                   and b.suit is not None)
        if natural and seat in ours and b.suit != Suit.NOTRUMP:
            if b.suit not in our_suits:
                our_suits.append(b.suit)
        elif natural and seat in opps:
            if b.suit == Suit.NOTRUMP:
                their_nt = True
                if b.level == 4:
                    their_4nt = True
            else:
                artificial = (b.suit in our_suits or their_nt or their_4nt
                              or (first_call and b.level == 2
                                  and b.suit == Suit.CLUBS)
                              or (strong_2c and b.level == 2
                                  and b.suit == Suit.DIAMONDS))
                if first_call and b.level == 2 and b.suit == Suit.CLUBS:
                    strong_2c = True
                if not artificial and b.suit not in out:
                    out.append(b.suit)
        if not b.is_pass:
            first_call = False
        seat = seat.next()
    return out


def _declarer_suits(auction: List[Bid], dealer: Seat, declarer: Seat
                    ) -> List[Suit]:
    """Suits declarer itself bid (leading these is worse than dummy's)."""
    return _bid_suits_by_seat(auction, dealer, declarer)


def _bid_suits_by_seat(auction: List[Bid], dealer: Seat,
                       target: Seat) -> List[Suit]:
    out: List[Suit] = []
    seat = dealer
    for b in auction:
        if seat == target and not b.is_pass and not b.is_double \
                and not b.is_redouble \
                and b.suit is not None and b.suit != Suit.NOTRUMP:
            if b.suit not in out:
                out.append(b.suit)
        seat = seat.next()
    return out


# ---------------------------------------------------------------------------
# Card-selection (within a chosen suit) — reference-style conventions.
# ---------------------------------------------------------------------------


def _pick_card_from_suit(cards: List[Card], is_trump_contract: bool,
                         partners_suit: bool = False,
                         trump_suit: Optional[Suit] = None) -> Card:
    """Choose the spot card to lead from a given suit holding.

    `cards` is high-to-low in the suit. `is_trump_contract` toggles
    suit-vs-NT conventions. `partners_suit` weakens the
    "don't underlead aces" rule (partner's suit always wins out).
    """
    n = len(cards)
    if n == 0:
        raise ValueError("can't lead from an empty suit")
    token = _suit_token_string(cards)
    is_nt = not is_trump_contract

    # Singleton — only card. Caller decides whether to lead the suit
    # at all (we don't lead trump singletons usually); here we just
    # return it.
    if n == 1:
        return cards[0]

    # Doubleton — top of nothing / honor; conventional in trump.
    if n == 2:
        # AK → A (cash and look at dummy / partner's signal)
        if token == "AK":
            return cards[0]
        # KQ, QJ, JT, T9 → top
        if token in ("KQ", "QJ", "JT", "T9"):
            return cards[0]
        # Hx (one honor + small) → top against trump, sometimes 4th
        # against NT. We default to top against trump, low against NT.
        first_is_honor = cards[0].rank in (Rank.ACE, Rank.KING,
                                            Rank.QUEEN, Rank.JACK)
        if first_is_honor:
            return cards[0] if is_trump_contract else cards[1]
        # xx — top of nothing.
        return cards[0]

    # ---- 3+ card suits ----

    # Strong 3+ sequences: AKQ/KQJ/QJT/JT9/T98 → top.
    SEQUENCES_TOP = ("AKQ", "AKJ", "KQJ", "QJT", "JT9", "T98")
    for seq in SEQUENCES_TOP:
        if token.startswith(seq):
            return cards[0]
    # AK / KQ / QJ with longer suit — top of 2-card sequence is the
    # standard lead at any length, not just doubletons. AKx+ → A
    # (cash and look); KQx+ → K (forcing the Ace); QJx+ → Q (when
    # no K shown).
    if token.startswith("AK"):
        return cards[0]
    if token.startswith("KQ"):
        return cards[0]
    if token.startswith("QJ"):
        return cards[0]
    if token.startswith("JT"):
        return cards[0]
    # Interior sequence: lead the TOP of the internal touching honours
    # (the high card sits detached above the run). KJTx → J, AJTx → J,
    # AT9x → T — i.e. cards[1], the second card. (Leading cards[2], the
    # bottom of the touching pair, is a textbook error a good player spots.)
    if token.startswith("KJT"):
        return cards[1]  # the J — top of the JT interior sequence
    if token.startswith("AJT"):
        return cards[1]  # the J
    if token.startswith("AT9"):
        return cards[1]  # the T

    # Top of nothing — long suit of small cards (no honor higher
    # than T).
    if all(c.rank not in (Rank.ACE, Rank.KING, Rank.QUEEN, Rank.JACK)
           for c in cards):
        # MUD-style for 3-card; top for 2; against NT often 2nd from
        # 4+ small.
        if n == 3:
            return cards[1]  # middle of 3 small (MUD)
        # 4+ small → second highest against NT; top of nothing
        # against trump.
        return cards[1] if is_nt else cards[0]

    # Suit headed by an Ace but no K or Q: don't underlead the Ace
    # in a suit contract unless it's partner's suit. Lead 4th best
    # against NT (vs trump contracts prefer a different suit; caller
    # should usually skip the suit, but if forced, lead low fourth).
    if cards[0].rank == Rank.ACE \
            and Rank.KING not in (c.rank for c in cards) \
            and Rank.QUEEN not in (c.rank for c in cards):
        if is_trump_contract and not partners_suit:
            # Lead the ace (top) — better than underleading.
            return cards[0]
        # 4th-best from longest
        return _fourth_best(cards)

    # Suit headed by K or Q without supporting sequence — lead small
    # (4th best against NT, low from honor against trump).
    if cards[0].rank in (Rank.KING, Rank.QUEEN, Rank.JACK):
        if is_nt:
            return _fourth_best(cards)
        # vs trump: low from honor (3rd or 4th best).
        return _fourth_best(cards)

    # Default: 4th best from longest suit.
    return _fourth_best(cards)


def _fourth_best(cards: List[Card]) -> Card:
    """Return 4th-highest card; if fewer than 4, return the lowest."""
    return cards[3] if len(cards) >= 4 else cards[-1]


# ---------------------------------------------------------------------------
# Suit selection — pick which suit to lead.
# ---------------------------------------------------------------------------


@dataclass
class _SuitScore:
    suit: Suit
    score: float
    explanation: str


def _score_suit_for_lead(hand: Hand, suit: Suit, contract: Contract,
                         partner_suits: List[Suit],
                         opp_suits: List[Suit],
                         vul: bool) -> Optional[_SuitScore]:
    """Score a candidate suit. Higher = better lead.

    Returns None if the suit shouldn't be considered (e.g. trump
    suit + no compelling reason).
    """
    cards = _suit_cards(hand, suit)
    if not cards:
        return None
    n = len(cards)
    token = _suit_token_string(cards)
    is_nt = (contract.suit == Suit.NOTRUMP)
    is_trump_suit = (suit == contract.suit and not is_nt)
    bid_by_partner = suit in partner_suits
    bid_by_opp = suit in opp_suits

    score = 0.0
    parts: List[str] = []

    # Trump suit: don't lead unless partner doubled (we don't model
    # double-of-trump-shows-trumps yet) or holding 0/1 trump.
    if is_trump_suit:
        return None

    # Partner's suit is the most attractive lead in BOTH contract
    # types — partner has shown values there.
    if bid_by_partner:
        score += 50
        parts.append("partner bid")

    # Opp-bid suit: leading INTO their suit is dangerous; demote.
    if bid_by_opp and not bid_by_partner:
        score -= 20
        parts.append("opps bid")

    if is_nt:
        # NT contract: long suits with high cards are great.
        score += n * 6
        parts.append(f"length-{n}")
        # Sequences
        for seq, bonus in (("AKQ", 30), ("KQJ", 28), ("QJT", 25),
                           ("JT9", 22), ("AKJ", 20), ("KQT", 18),
                           ("AK", 12), ("KQ", 12), ("QJ", 10)):
            if token.startswith(seq):
                score += bonus
                parts.append(f"+seq {seq}")
                break
        # Single honor in a long suit (5+) — still good
        honors = sum(1 for c in cards if c.rank in (Rank.ACE, Rank.KING,
                                                    Rank.QUEEN, Rank.JACK))
        score += honors * 3
    else:
        # Suit contract: prefer sequences and singletons; avoid
        # underleading aces.
        # Singleton in unbid suit — strong unless trump shortage.
        trump_len = len(_suit_cards(hand, contract.suit))
        if n == 1 and not bid_by_opp and trump_len <= 3:
            score += 35
            parts.append("singleton")
        # Top sequences — safest active lead.
        for seq, bonus in (("AKQ", 30), ("KQJ", 30), ("QJT", 28),
                           ("JT9", 22), ("AK", 25), ("KQ", 20),
                           ("QJ", 15), ("JT", 10)):
            if token.startswith(seq):
                score += bonus
                parts.append(f"+seq {seq}")
                break
        # Doubleton with no honor: secondary preference (top of
        # nothing, hoping for a ruff later).
        if n == 2 and cards[0].rank.value > Rank.TEN.value \
                and not bid_by_opp:
            # value is the wrong comparison — Rank enum values are
            # high → low (ACE=0, TWO=12), so smaller value = higher
            # card. Convert to honor check explicitly:
            pass
        if n == 2 and all(c.rank not in (Rank.ACE, Rank.KING, Rank.QUEEN,
                                          Rank.JACK) for c in cards) \
                and not bid_by_opp:
            score += 8
            parts.append("doubleton-xx")
        # Long suit with mid honors — okay but not preferred
        # against a trump contract.
        score += n * 1.5
        # Ace-headed-no-K — heavy penalty if underleading would be
        # required (and we'd avoid leading the ace itself unless
        # partner's suit).
        if cards[0].rank == Rank.ACE \
                and Rank.KING not in (c.rank for c in cards) \
                and not bid_by_partner:
            score -= 8
            parts.append("Axxx (avoid underlead)")
        # Vulnerable: be a touch more conservative — slight penalty
        # for risky-shape suits (long but no honor).
        if vul and n >= 4 and all(c.rank not in (Rank.ACE, Rank.KING,
                                                  Rank.QUEEN) for c in cards):
            score -= 3

    return _SuitScore(suit=suit, score=score,
                      explanation=", ".join(parts) if parts else "neutral")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class LeadDecision:
    card: Card
    suit_choice_reason: str
    card_choice_reason: str


def select_opening_lead(hand: Hand, contract: Contract,
                        auction: List[Bid], dealer: Seat,
                        leader: Seat,
                        vulnerability: Vulnerability) -> LeadDecision:
    """Pick the opening lead for `hand` against `contract`.

    The full auction is passed in so the engine can spot partner's
    bid suits and opponent-bid suits.
    """
    partner_suits = _partner_suits(auction, leader, dealer)
    opp_suits = _opp_suits(auction, leader, dealer)
    vul = vulnerability.is_vulnerable(leader)

    decl_suits = [x for x in _declarer_suits(auction, dealer, contract.declarer)
                  if x in opp_suits]
    my_suits = _bid_suits_by_seat(auction, dealer, leader)
    candidates: List[_SuitScore] = []
    for suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
        sc = _score_suit_for_lead(hand, suit, contract,
                                  partner_suits, opp_suits, vul)
        if sc is not None:
            # Leading into DECLARER's own suit is worse than into dummy's
            # (run 5 RANDOM-018: dQ from QJ7 into declarer's 1D opening).
            if suit in decl_suits and suit not in partner_suits:
                sc.score -= 12
                sc.explanation += ", declarer's suit"
            # Against NT my own bid suit is the natural attack.
            if (contract.suit == Suit.NOTRUMP and suit in my_suits
                    and suit not in opp_suits and len(_suit_cards(hand, suit)) >= 5):
                sc.score += 10
                sc.explanation += ", my suit"
            candidates.append(sc)

    if not candidates:
        # No legal lead (shouldn't happen for a real hand). Just
        # pick anything.
        return LeadDecision(card=hand.cards[0],
                            suit_choice_reason="fallback",
                            card_choice_reason="first card available")

    candidates.sort(key=lambda s: s.score, reverse=True)
    best = candidates[0]
    cards_in_suit = _suit_cards(hand, best.suit)
    is_trump = (contract.suit != Suit.NOTRUMP)
    partners = (best.suit in partner_suits)
    card = _pick_card_from_suit(cards_in_suit, is_trump,
                                partners_suit=partners,
                                trump_suit=contract.suit
                                if is_trump else None)
    holding = _suit_token_string(cards_in_suit)
    return LeadDecision(
        card=card,
        suit_choice_reason=f"{best.suit.to_char()} {best.explanation} "
                           f"(score {best.score:.1f})",
        card_choice_reason=f"from {holding}: lead "
                           f"{_RANK_TOKEN[card.rank]}",
    )
