"""Strategic cardplay planner — above DDS.

biq's MC+DDS engine plays each card *locally optimally against a
probabilistic guess of the layout*. That's an architectural ceiling:
no per-card decision can encode "I need to draw trumps and cash my
top tricks in the right order to make 4 spades". This module is the
beginning of a textbook-anchored plan-builder that operates above
DDS, similar to how `native_bidder.py` operates above the simple
"first legal bid" fallback.

Built incrementally, one rule per phase, with measured impact each
time. This file is Phase 1 only — the trump-drawing lead rule.

Phase 1 (this file): declarer's trump-drawing lead.
Phase 2 (later): follow-suit rules (second-hand-low, unblock
  prevention from dummy).
Phase 3 (later): suit-establishment plans, entry counting.
Phase 4 (later): defender signaling.
Phase 5 (later): endplay / squeeze recognition.
"""

from typing import List, Optional
from .models import Card, Hand, Suit, Rank, Seat, BoardState

# Plan 2 — per-bot planner configs for multi-bot self-play.
# Each config gates which phases fire. Pass via set_planner_config()
# before each call into the planner from the engine; the planner
# reads the global. Defaults match the current committed-best state
# (Phase 15 + 23 + the rules each kept).


from dataclasses import dataclass


@dataclass
class PlannerConfig:
    """Plan 2 — config for one biq bot variant.

    Each flag gates whether a specific phase's rule fires. Default
    = current committed-best state. Variants for self-play set
    different combinations.
    """
    phase_1_trump_lead: bool = True
    phase_2_defender_follow: bool = True
    phase_3_opening_lead: bool = True
    phase_4d_defender_cover: bool = True
    phase_5_cash_short: bool = True
    phase_7_8_plan_unblock: bool = True
    phase_9_length_dev: bool = True
    phase_11_return_partner: bool = True
    phase_13_4th_best: bool = True
    phase_15_extended_cover: bool = True
    phase_23_entries: bool = True
    # Optional/experimental phases — off by default:
    phase_25_k_loc_finesse: bool = False


_active_config = PlannerConfig()


def set_planner_config(config: PlannerConfig) -> None:
    """Plan 2 — set the active planner config before each engine call.
    The planner reads from the module-global; tests/benchmarks can
    swap configs per seat to A/B different variants head-to-head.
    """
    global _active_config
    _active_config = config


def get_planner_config() -> PlannerConfig:
    return _active_config

_RANK_HI_TO_LO = [Rank.ACE, Rank.KING, Rank.QUEEN, Rank.JACK,
                  Rank.TEN, Rank.NINE, Rank.EIGHT, Rank.SEVEN,
                  Rank.SIX, Rank.FIVE, Rank.FOUR, Rank.THREE,
                  Rank.TWO]


def _partner(seat: Seat) -> Seat:
    return Seat((seat + 2) % 4)


def _is_declarer_side(seat: Seat, declarer: Seat) -> bool:
    return seat == declarer or seat == _partner(declarer)


def _trump_cards_sorted_high(hand: Hand, trump: Suit) -> List[Card]:
    """Cards of the trump suit in `hand`, sorted high to low (Ace
    first). Rank.value ordering is ACE=0 ... TWO=12, so sort
    ascending by .value."""
    return sorted([c for c in hand.cards if c.suit == trump],
                  key=lambda c: c.rank.value)


def _played_trump_ranks(board: BoardState, trump: Suit,
                         current_trick_cards: List[Card]) -> set:
    """Ranks of the trump suit already played (completed tricks +
    current trick)."""
    played = set()
    for trick in getattr(board, "tricks", []) or []:
        for c in (getattr(trick, "cards", []) or []):
            if c.suit == trump:
                played.add(c.rank)
    for c in current_trick_cards or []:
        if c.suit == trump:
            played.add(c.rank)
    return played


def _highest_outstanding_opp_trump(board: BoardState, seat: Seat,
                                    declarer: Seat, trump: Suit,
                                    current_trick_cards: List[Card]
                                    ) -> Optional[Rank]:
    """Return the highest trump rank that could be in an opponent's
    hand right now, or None if opponents are out of trumps.
    "Visible" = own side's hands (declarer + dummy, both visible
    when declarer-side computes the plan) + played cards."""
    visible_ranks = set(_played_trump_ranks(board, trump,
                                              current_trick_cards))
    own_side = (declarer, _partner(declarer))
    for s in own_side:
        for c in board.hands[s].cards:
            if c.suit == trump:
                visible_ranks.add(c.rank)
    for r in _RANK_HI_TO_LO:
        if r not in visible_ranks:
            return r
    return None  # all 13 trump ranks accounted for on our side or played


def _opponent_trump_count(board: BoardState, declarer: Seat,
                           trump: Suit,
                           current_trick_cards: List[Card]) -> int:
    own_side = (declarer, _partner(declarer))
    own_count = sum(1 for s in own_side
                    for c in board.hands[s].cards
                    if c.suit == trump)
    played_count = len(_played_trump_ranks(board, trump,
                                            current_trick_cards))
    return max(0, 13 - own_count - played_count)


def planned_lead(board: BoardState, seat: Seat,
                  current_trick_cards: Optional[List[Card]] = None
                  ) -> Optional[Card]:
    """Return the strategic-plan card for a LEAD decision, or None
    to fall through to MC+DDS.

    Phase 1 — declarer's trump-drawing lead. In a suit contract,
    when declarer or dummy is leading and opponents still hold
    trumps, lead the highest trump from this hand if it would win
    the trick (higher than any outstanding opponent trump).

    The textbook rule: 'Draw trumps before cashing side suits' and
    'Lead top trumps from the long hand'. This handles the Deal 17
    family (declarer led ♠7 from ♠AQ732 instead of ♠A).

    Returns None for: defender plays, NT contracts, opponents-out-of-
    trumps positions, hands where our highest trump can't beat
    opponents' highest. In those cases MC+DDS is left to choose.
    """
    if board.contract is None:
        return None
    if current_trick_cards:
        return None  # this is a follow-suit, not a lead
    trump = board.contract.suit
    if trump == Suit.NOTRUMP:
        return None  # no trump → no trump-drawing rule
    declarer = board.contract.declarer
    if not _is_declarer_side(seat, declarer):
        return None  # defender lead — leave to MC+DDS / future phase
    opp_trumps = _opponent_trump_count(board, declarer, trump,
                                        current_trick_cards or [])
    if opp_trumps <= 0:
        return None  # trumps drawn; no work for this rule
    my_trumps = _trump_cards_sorted_high(board.hands[seat], trump)
    if not my_trumps:
        return None  # no trumps to lead
    my_highest = my_trumps[0]
    opp_highest = _highest_outstanding_opp_trump(
        board, seat, declarer, trump, current_trick_cards or [])
    if opp_highest is None:
        return None  # contradiction with opp_trumps > 0; bail safely
    # Only cash if our highest beats their highest.
    if my_highest.rank.value < opp_highest.value:
        return my_highest
    return None


def _card_beats(candidate: Card, current_winner: Card,
                 lead_suit: Suit, trump: Optional[Suit]) -> bool:
    """Returns True if `candidate` would win over `current_winner`
    given the lead suit and trump suit (None for NT)."""
    cand_is_trump = trump is not None and candidate.suit == trump
    win_is_trump = trump is not None and current_winner.suit == trump
    if cand_is_trump and not win_is_trump:
        return True
    if win_is_trump and not cand_is_trump:
        return False
    if cand_is_trump and win_is_trump:
        return candidate.rank.value < current_winner.rank.value
    # Neither trump: only lead-suit cards can win
    if candidate.suit != lead_suit:
        return False
    if current_winner.suit != lead_suit:
        return True  # shouldn't happen in normal play, but be safe
    return candidate.rank.value < current_winner.rank.value


def _winning_index(trick_cards: List[Card],
                    trump: Optional[Suit]) -> int:
    """Index (0..n-1) of the card currently winning a partial or
    full trick."""
    if not trick_cards:
        return 0
    lead = trick_cards[0].suit
    best_idx = 0
    best = trick_cards[0]
    for i in range(1, len(trick_cards)):
        if _card_beats(trick_cards[i], best, lead, trump):
            best_idx = i
            best = trick_cards[i]
    return best_idx


def _lowest_in_suit(cards: List[Card]) -> Card:
    """Lowest rank from a list of single-suit cards (ACE=0 highest,
    TWO=12 lowest in Rank.value)."""
    return max(cards, key=lambda c: c.rank.value)


def _highest_in_suit(cards: List[Card]) -> Card:
    return min(cards, key=lambda c: c.rank.value)


def _max_opp_rank_in_suit(suit: Suit, board: BoardState, seat: Seat,
                           current_trick_cards: List[Card],
                           declarer: Seat) -> Optional[Rank]:
    """Highest rank of `suit` that could still be in opponents'
    hands from `seat`'s point of view. Visible = own hand + visible
    partner's hand (dummy if declarer-side; dummy if defender, since
    dummy is open) + played cards."""
    visible = set()
    visible |= {c.rank for c in board.hands[seat].cards if c.suit == suit}
    dummy = Seat((declarer + 2) % 4)
    if _is_declarer_side(seat, declarer):
        partner = _partner(seat)
        visible |= {c.rank for c in board.hands[partner].cards
                    if c.suit == suit}
    else:
        # Defender: dummy is open to all
        visible |= {c.rank for c in board.hands[dummy].cards
                    if c.suit == suit}
    for trick in getattr(board, "tricks", []) or []:
        for c in (getattr(trick, "cards", []) or []):
            if c.suit == suit:
                visible.add(c.rank)
    for c in current_trick_cards or []:
        if c.suit == suit:
            visible.add(c.rank)
    for r in _RANK_HI_TO_LO:
        if r not in visible:
            return r
    return None


def _partner_safe(partner_card: Card, board: BoardState, seat: Seat,
                   declarer: Seat, current_trick_cards: List[Card],
                   trump: Optional[Suit]) -> bool:
    """True if partner's already-played card cannot be beaten by any
    opponent card still possibly in play. Trump-aware: a non-trump
    partner-card is unsafe if opponents could trump."""
    # If partner's card is in the lead suit but opponents could
    # trump (suit contract, opponents short in lead suit), partner
    # is not safe. For Phase 2 we approximate: if trump != lead suit
    # and there's any chance opps are out of lead, we can't be sure.
    # Be conservative — if any unaccounted opponent trump and they
    # might be out of lead, consider partner unsafe.
    lead_suit = current_trick_cards[0].suit if current_trick_cards else partner_card.suit
    # Highest opp rank in the lead suit (the suit partner-card needs
    # to defend against).
    opp_max_lead = _max_opp_rank_in_suit(
        lead_suit, board, seat, current_trick_cards, declarer)
    if opp_max_lead is not None:
        if partner_card.suit == lead_suit:
            if partner_card.rank.value > opp_max_lead.value:
                return False  # opp could overtake in lead suit
        else:
            # Partner played off-suit (ruff in suit contract).
            # If it's a ruff, we have to ask: could opp over-ruff?
            if partner_card.suit == trump:
                opp_max_tr = _max_opp_rank_in_suit(
                    trump, board, seat, current_trick_cards, declarer)
                if opp_max_tr is not None and opp_max_tr.value < partner_card.rank.value:
                    return False
    # Suit-contract ruff risk: opponents trump our non-trump winner.
    if (trump is not None and partner_card.suit != trump):
        opp_max_trump = _max_opp_rank_in_suit(
            trump, board, seat, current_trick_cards, declarer)
        if opp_max_trump is not None:
            # Opponents could have a trump; partner not safe.
            return False
    return True


def _cover_honor_with_next(my_in_suit: List[Card],
                            lead_card: Card) -> Optional[Card]:
    """Phase 4 + 15 — cover-an-honor.

    When LHO leads a J/Q/K and I hold a higher honor, cover.
    Plays the LOWEST acceptable cover (preserve higher honors):

    - Lead J: cover with Q first, then K. Not A.
    - Lead Q: cover with K first, then A.
    - Lead K: cover with A.

    Original Phase 4 only used the immediate next-higher honor
    (Q for J, K for Q, A for K). Phase 15 extends to allow K
    covering J when Q is missing, and A covering Q when K is
    missing — covers the common "I hold a singleton high honor"
    case without over-firing the Ace.
    """
    if lead_card.suit not in (Suit.SPADES, Suit.HEARTS,
                                Suit.DIAMONDS, Suit.CLUBS):
        return None
    cover_options = {
        Rank.JACK: [Rank.QUEEN, Rank.KING],
        Rank.QUEEN: [Rank.KING, Rank.ACE],
        Rank.KING: [Rank.ACE],
    }.get(lead_card.rank)
    if not cover_options:
        return None
    for target in cover_options:  # prefer earlier (lower) options
        for c in my_in_suit:
            if c.rank == target:
                return c
    return None


def _unblock_card(my_in_suit: List[Card], winning_card: Card,
                   board: BoardState, seat: Seat,
                   declarer: Seat) -> Optional[Card]:
    """Phase 3 rule — declarer-side unblock to preserve partner's
    length, tightly gated.

    Returns a card to UNBLOCK (a higher card to drop), or None.

    A first-cut version fired whenever own-side was shorter than
    partner; that over-fired in NT (−0.50 tricks/deal) because
    "drop the K under partner's Q" is wrong when entries to the
    long hand exist elsewhere. Tightened to the classic unblock
    case only:

    - I have ≤ 2 cards in this suit (singleton or doubleton —
      i.e., I will likely be void within 1-2 rounds and can't
      provide later entries via this suit).
    - Partner has ≥ 3 cards (genuine length to preserve).
    - Partner's winning card is an actual honor (T or higher) —
      a winning spot card isn't worth dropping high under.
    - I have a card HIGHER than partner's winning card.

    Drops the LOWEST of my higher cards (preserve absolute boss).
    """
    if not _is_declarer_side(seat, declarer):
        return None
    if len(my_in_suit) > 2:
        return None
    partner = _partner(seat)
    suit = winning_card.suit
    partner_count = sum(1 for c in board.hands[partner].cards
                        if c.suit == suit)
    if partner_count < 3:
        return None
    # Honor check: Rank.value 0..4 = A,K,Q,J,T
    if winning_card.rank.value > 4:
        return None
    higher = [c for c in my_in_suit
              if c.rank.value < winning_card.rank.value]
    if not higher:
        return None
    return _lowest_in_suit(higher)


def planned_follow(board: BoardState, seat: Seat,
                    current_trick_cards: List[Card]) -> Optional[Card]:
    """Return the strategic-plan card for a FOLLOW-SUIT decision,
    or None to fall through to MC+DDS.

    Phase 2 rules (defender-mostly, all safety-gated):

    1. Don't overtake partner's safe winner. Universal — but only
       when partner's played card can't be beaten by any remaining
       opponent card (accounts for ruff threat in suit contracts).

    2. Second-hand-low (defender). When defender is 2nd-to-play,
       play lowest of suit.

    3. Third-hand-high (defender). When defender is 3rd-to-play and
       partner is NOT safe, play highest of suit to push declarer.

    4. Fourth-hand cheap-win-or-low (defender). Last-to-play: lowest
       card that beats the current winner, else lowest of suit.

    Declarer-side 2nd/3rd/4th-hand play is left to MC+DDS — those
    decisions depend on the declarer's strategic plan (entries, suit
    establishment, finesse position), which Phases 3+ will encode.
    Only the safety-gated Rule 1 applies universally.
    """
    if not current_trick_cards:
        return None  # this is a lead
    if board.contract is None:
        return None
    lead_suit = current_trick_cards[0].suit
    my_in_suit = [c for c in board.hands[seat].cards
                  if c.suit == lead_suit]
    if not my_in_suit:
        return None  # discarding — handled by cardplay_planner
    if len(my_in_suit) == 1:
        return my_in_suit[0]  # forced
    declarer = board.contract.declarer
    trump = (board.contract.suit
             if board.contract.suit != Suit.NOTRUMP else None)
    position = len(current_trick_cards) + 1  # 1..4
    leader = Seat((seat.value - (position - 1)) % 4)
    win_idx = _winning_index(current_trick_cards, trump)
    winning_seat = Seat((leader.value + win_idx) % 4)
    winning_card = current_trick_cards[win_idx]
    partner_seat = Seat((seat.value + 2) % 4)
    is_defender = not _is_declarer_side(seat, declarer)

    # Phase 8 — Plan-aware declarer-side unblock. Runs BEFORE
    # Rule 1 (don't overtake) so it can override when dropping a
    # high card preserves partner's length tricks (per the plan's
    # length_winners count for the suit).
    if winning_seat == partner_seat:
        unblock = _plan_aware_unblock(board, seat, declarer,
                                       winning_card, my_in_suit)
        if unblock is not None:
            return unblock

    # Rule 1 — Don't overtake partner's SAFE winner. Safety-gated:
    # only fires when partner's card can't be beaten by any
    # remaining opponent card (no ruff threat in suit contracts,
    # no higher card outstanding in the lead suit).
    if winning_seat == partner_seat:
        if _partner_safe(winning_card, board, seat, declarer,
                          current_trick_cards, trump):
            return _lowest_in_suit(my_in_suit)
        # Partner not safe — fall through to position-specific logic.

    # (Phase 18 attempt — declarer hold-up at T1 in NT — regressed
    # tricks/deal -0.138. In biq-vs-biq cardplay mode the defender
    # always has communications to exploit the held-up Ace; hold-up
    # only pays off vs human opponents who can't reach partner.
    # Reverted.)

    # Defender-only rules below. Two iterations of universal 3rd-
    # hand rules regressed (universal cheap-winner −0.942, sequence-
    # aware defender-only −0.836). The "play highest" rule from
    # Phase 2 v2 is robust; reverted.
    if not is_defender:
        return None

    # Phase 4 — Cover an honor at defender 2nd hand. When LHO/RHO
    # leads a J/Q/K and I hold the next-higher honor, cover.
    # Promotes lower cards in partner's hand.
    if position == 2:
        lead_card = current_trick_cards[0]
        cover = _cover_honor_with_next(my_in_suit, lead_card)
        if cover is not None:
            return cover

    # Rule 2 — Second hand low (defender).
    if position == 2:
        return _lowest_in_suit(my_in_suit)

    # Rule 3 — Third hand high (defender) when partner not safe.
    if position == 3:
        if _card_beats(_highest_in_suit(my_in_suit), winning_card,
                        lead_suit, trump):
            return _highest_in_suit(my_in_suit)
        return _lowest_in_suit(my_in_suit)

    # Rule 4 — Fourth hand cheap-win-or-low (defender).
    if position == 4:
        beat = [c for c in my_in_suit
                if _card_beats(c, winning_card, lead_suit, trump)]
        if beat:
            return _lowest_in_suit(beat)
        return _lowest_in_suit(my_in_suit)

    return None


def _cards_by_suit(hand) -> dict:
    out = {Suit.SPADES: [], Suit.HEARTS: [],
           Suit.DIAMONDS: [], Suit.CLUBS: []}
    for c in hand.cards:
        out[c.suit].append(c)
    return out


def _three_card_sequence_top(cards: List[Card]) -> Optional[Card]:
    """If `cards` contains 3 consecutive ranks (e.g. KQJ, QJT, JT9),
    return the highest card of that sequence. Otherwise None."""
    if len(cards) < 3:
        return None
    sorted_cards = sorted(cards, key=lambda c: c.rank.value)
    # Walk down looking for any 3-run of consecutive values
    for i in range(len(sorted_cards) - 2):
        a, b, c = sorted_cards[i], sorted_cards[i+1], sorted_cards[i+2]
        if (a.rank.value + 1 == b.rank.value
                and b.rank.value + 1 == c.rank.value):
            return a
    return None


def _partner_bid_suit(board: BoardState, seat: Seat) -> Optional[Suit]:
    """Find partner's first real suit bid in the auction. Returns
    Suit or None. Doubles, redoubles, NT bids, and passes are
    skipped."""
    auction = getattr(board, "auction", None) or []
    if not auction:
        return None
    dealer = board.dealer
    partner = _partner(seat)
    for i, bid in enumerate(auction):
        bidder = Seat((dealer.value + i) % 4)
        if bidder != partner:
            continue
        if bid.is_pass or bid.is_double or bid.is_redouble:
            continue
        if bid.suit is None or bid.suit == Suit.NOTRUMP:
            continue
        return bid.suit
    return None


from dataclasses import dataclass


@dataclass
class SuitAnalysis:
    """Phase 7 + 23 — per-suit analysis used by the declarer plan.

    Phase 23 adds entry counting: top winners held specifically in
    each hand (declarer vs dummy). An entry to a hand is a card
    held in that hand that wins when led — i.e., a top winner sat
    on that side. Used by the unblock rule and suit-establishment
    timing.
    """
    suit: Suit
    declarer_count: int
    dummy_count: int
    own_count: int   # declarer + dummy combined
    opp_count: int   # 13 - own
    top_winners: int    # consecutive top ranks held (A, AK, AKQ, ...)
    length_winners: int # expected extras above top after balanced split
    expected_winners: int  # top + length
    entries_declarer: int = 0  # top-winner cards in declarer's hand
    entries_dummy: int = 0     # top-winner cards in dummy's hand


@dataclass
class DeclarerPlan:
    """Phase 7 — textbook 'plan your play' state computed at trick 1
    (or any later point) for the declarer-side perspective."""
    contract: object
    target_tricks: int
    per_suit: dict  # Dict[Suit, SuitAnalysis]
    total_winners: int
    deficit: int  # target - total (>0 = need more from establishment)


def build_declarer_plan(board: BoardState,
                          declarer: Seat) -> Optional[DeclarerPlan]:
    """Compute the declarer's plan from declarer+dummy's current
    holdings. Done from declarer-side's perspective (both visible).

    Length-winner estimate is conservative: assumes opps split
    balanced (max opp holding ≈ ceil(opp_count / 2)).
    """
    if board.contract is None:
        return None
    contract = board.contract
    target = contract.level + 6
    dummy = _partner(declarer)
    per_suit = {}
    for suit in (Suit.SPADES, Suit.HEARTS,
                 Suit.DIAMONDS, Suit.CLUBS):
        d_count = sum(1 for c in board.hands[declarer].cards
                      if c.suit == suit)
        dum_count = sum(1 for c in board.hands[dummy].cards
                        if c.suit == suit)
        own = d_count + dum_count
        opp = 13 - own  # ignores cards already played; OK for plan
        combined_ranks = set()
        for s in (declarer, dummy):
            for c in board.hands[s].cards:
                if c.suit == suit:
                    combined_ranks.add(c.rank)
        # (Phase 26 attempt — pass played_ranks for dynamic counts
        # — regressed IMP -0.428. Mid-deal plan recomputation with
        # played cards shifted the plan-aware unblock decisions in
        # the wrong direction. Reverted to static count.)
        top = _count_top_winners(combined_ranks)
        played_ranks = set()  # kept for entries calc below
        # Length above the top winners after balanced split:
        # max_opp_holding ≈ ceil(opp / 2). Once tops cash, remaining
        # own = own - top; length = own - top - max_opp_holding (if
        # opp_holding can be exhausted within our top plays).
        max_opp_holding = (opp + 1) // 2
        # We need enough top tricks to keep up with opp's longest
        # holding. If top >= max_opp_holding, length potential =
        # own - top - max_opp_holding. Else 0.
        if top >= max_opp_holding:
            length = max(0, own - top - max_opp_holding)
        else:
            length = 0
        # Phase 23 + 26: count entry-cards in each hand. The top
        # `top` ranks must be picked dynamically, walking from the
        # highest unplayed rank that own-side holds. After A has
        # played, K becomes the new top winner; static
        # _RANK_HI_TO_LO[:top] would still report A as the top set
        # and entries_declarer would be 0 even though K is in
        # declarer's hand.
        top_rank_set = set()
        if top > 0:
            collected = 0
            for r in _RANK_HI_TO_LO:
                if collected >= top:
                    break
                if r in combined_ranks:
                    top_rank_set.add(r)
                    collected += 1
                elif r in played_ranks:
                    continue
                else:
                    break
        entries_d = sum(1 for c in board.hands[declarer].cards
                        if c.suit == suit and c.rank in top_rank_set)
        entries_dum = sum(1 for c in board.hands[dummy].cards
                          if c.suit == suit and c.rank in top_rank_set)
        per_suit[suit] = SuitAnalysis(
            suit=suit, declarer_count=d_count, dummy_count=dum_count,
            own_count=own, opp_count=opp, top_winners=top,
            length_winners=length, expected_winners=top + length,
            entries_declarer=entries_d, entries_dummy=entries_dum)
    total = sum(s.expected_winners for s in per_suit.values())
    return DeclarerPlan(
        contract=contract, target_tricks=target,
        per_suit=per_suit, total_winners=total,
        deficit=target - total)


def _plan_aware_unblock(board: BoardState, seat: Seat,
                          declarer: Seat,
                          winning_card: Card,
                          my_in_suit: List[Card]) -> Optional[Card]:
    """Phase 8 + 23 — plan-aware declarer-side unblock with entry
    counting.

    Returns a card to drop (unblock), or None. Fires when:
    - I'm declarer-side and partner (visible) just played a card
      that is currently winning the trick.
    - Partner has MORE cards in this suit than I do.
    - I have a card HIGHER than partner's, that would otherwise
      block partner's length on subsequent rounds.
    - The plan says this suit has length_winners > 0 (so the length
      is worth preserving).
    - Phase 23 refinement: partner has NO OTHER entries (top
      winners in their hand from other suits). If they do, we can
      reach the long hand via the other entry without unblocking
      here.

    Drops the LOWEST of my higher cards (preserve absolute boss).
    """
    if not _is_declarer_side(seat, declarer):
        return None
    plan = build_declarer_plan(board, declarer)
    if plan is None:
        return None
    suit = winning_card.suit
    sa = plan.per_suit.get(suit)
    if sa is None or sa.length_winners == 0:
        return None
    partner = _partner(seat)
    partner_count = sum(1 for c in board.hands[partner].cards
                        if c.suit == suit)
    if len(my_in_suit) >= partner_count:
        return None
    higher = [c for c in my_in_suit
              if c.rank.value < winning_card.rank.value]
    if not higher:
        return None
    # Phase 23: only unblock if partner has no other entries.
    partner_other_entries = 0
    for other_suit in (Suit.SPADES, Suit.HEARTS,
                       Suit.DIAMONDS, Suit.CLUBS):
        if other_suit == suit:
            continue
        other_sa = plan.per_suit.get(other_suit)
        if other_sa is None:
            continue
        partner_other_entries += (
            other_sa.entries_declarer if partner == declarer
            else other_sa.entries_dummy)
    if partner_other_entries > 0:
        return None
    return _lowest_in_suit(higher)


def _count_top_winners(combined_ranks: set,
                        played_ranks: Optional[set] = None) -> int:
    """Number of consecutive winners we hold from the current top
    downward.

    Phase 26 — dynamic version. Walk ranks high to low:
    - Rank in own hand → count it as a top winner.
    - Rank already played by anyone → skip (rank is dead).
    - Rank not in own hand and not played → opp still has it; stop.

    Without `played_ranks` (None): legacy behavior counting from Ace.
    With `played_ranks`: accounts for cards already gone, so K
    correctly becomes the new top winner after A is played.
    """
    played = played_ranks or set()
    count = 0
    for r in _RANK_HI_TO_LO:
        if r in combined_ranks:
            count += 1
        elif r in played:
            continue
        else:
            break
    return count


def planned_lead_cash_short_side(board: BoardState, seat: Seat
                                   ) -> Optional[Card]:
    """Phase 5 — cash high from the short side.

    For declarer or dummy on lead in NT, or in a suit contract after
    trumps are drawn, find a suit where:
    - My hand is SHORTER than partner's (would block otherwise).
    - Combined holding has at least one consecutive top winner.
    - I have a top winner in my hand.

    Lead my LOWEST top winner from that suit. Preserves partner's
    length and the absolute boss for the last round.

    Handles the AK-doubleton-opposite-Qxxxxx class of patterns. Does
    not fire for trump-drawing leads (Phase 1's job) or when own-side
    is equal-length in every non-trump suit.
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if not _is_declarer_side(seat, declarer):
        return None
    contract_suit = board.contract.suit  # NOTRUMP if NT
    partner = _partner(seat)
    best = None
    best_asymmetry = 0
    for suit in (Suit.SPADES, Suit.HEARTS,
                 Suit.DIAMONDS, Suit.CLUBS):
        if suit == contract_suit:
            continue  # trump — Phase 1 handles
        my_cards = [c for c in board.hands[seat].cards
                    if c.suit == suit]
        partner_cards = [c for c in board.hands[partner].cards
                         if c.suit == suit]
        if not my_cards or len(my_cards) >= len(partner_cards):
            continue  # not short or void
        combined_ranks = {c.rank for c in my_cards + partner_cards}
        top_count = _count_top_winners(combined_ranks)
        if top_count == 0:
            continue
        # My top winners = my cards with rank-value < top_count
        # (Rank.ACE.value=0, etc.)
        my_top = [c for c in my_cards if c.rank.value < top_count]
        if not my_top:
            continue
        asymmetry = len(partner_cards) - len(my_cards)
        if asymmetry > best_asymmetry:
            best_asymmetry = asymmetry
            # Lead the LOWEST of my top winners — saves the boss
            best = max(my_top, key=lambda c: c.rank.value)
    return best


def _opp_who_bid_suit(board: BoardState, suit: Suit,
                       declarer: Seat) -> Optional[Seat]:
    """Phase 25 — find which opponent bid `suit` during the auction.
    Returns the opponent seat or None. Used by suit-combination
    rules to localise a missing honor (RHO bidder → K likely with
    RHO → finesse position favorable)."""
    auction = getattr(board, "auction", None) or []
    if not auction:
        return None
    dealer = board.dealer
    for i, bid in enumerate(auction):
        bidder = Seat((dealer.value + i) % 4)
        if _is_declarer_side(bidder, declarer):
            continue
        if bid.is_pass or bid.is_double or bid.is_redouble:
            continue
        if bid.suit == suit:
            return bidder
    return None


def infer_k_with_rho(board: BoardState, suit: Suit,
                      declarer: Seat) -> bool:
    """Phase 25 — infer whether the King of `suit` is likely sitting
    with RHO of declarer (the favorable position for finessing AQ
    in dummy).

    Heuristic: if an opp bid `suit` and that opp is RHO of declarer,
    high confidence K is there.
    """
    bidder = _opp_who_bid_suit(board, suit, declarer)
    if bidder is None:
        return False
    rho_of_declarer = Seat((declarer.value + 3) % 4)
    return bidder == rho_of_declarer


def planned_lead_finesse(board: BoardState, seat: Seat
                          ) -> Optional[Card]:
    """Phase 24 + 25 — lead small toward a finesse position, gated
    on K-location inference.

    Fires when declarer-side has the AQ-no-K position in partner's
    hand AND auction inference says K is likely with RHO of declarer
    (the favorable position for the finesse). Without the inference
    gate the blind rule regressed; with it we only fire when we have
    a positive signal about K's location.

    Lead my LOWEST. Partner plays Q (finesse succeeds since K is
    likely sitting in the slot that already played low).
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if not _is_declarer_side(seat, declarer):
        return None
    contract_suit = board.contract.suit
    if contract_suit != Suit.NOTRUMP:
        opp_trumps = _opponent_trump_count(
            board, declarer, contract_suit, [])
        if opp_trumps > 0:
            return None
    partner = _partner(seat)
    for suit in (Suit.SPADES, Suit.HEARTS,
                 Suit.DIAMONDS, Suit.CLUBS):
        if suit == contract_suit:
            continue
        my_cards = [c for c in board.hands[seat].cards
                    if c.suit == suit]
        partner_cards = [c for c in board.hands[partner].cards
                         if c.suit == suit]
        if not my_cards or not partner_cards:
            continue
        my_ranks = {c.rank for c in my_cards}
        partner_ranks = {c.rank for c in partner_cards}
        combined = my_ranks | partner_ranks
        # AQ on partner's side, K missing entirely, I have neither
        if (Rank.ACE in partner_ranks
                and Rank.QUEEN in partner_ranks
                and Rank.KING not in combined
                and Rank.ACE not in my_ranks
                and Rank.QUEEN not in my_ranks):
            # Phase 25 gate: only finesse when inference points to
            # RHO holding the K (favorable position).
            if infer_k_with_rho(board, suit, declarer):
                return _lowest_in_suit(my_cards)
    return None


def planned_lead_develop_length(board: BoardState, seat: Seat
                                  ) -> Optional[Card]:
    """Phase 9 — declarer-side length development.

    Fires when:
    - Declarer-side on lead, no current trick.
    - Plan deficit > 0 (we need more tricks beyond our cashed
      winners).
    - In a suit contract, trumps must already be drawn (opp's count
      = 0) — otherwise let Phase 1 handle drawing.
    - There's a non-trump suit with length_winners > 0 (length
      worth establishing).
    - I'm on the LONG side of that suit (otherwise let partner lead
      from there).

    Lead my LOWEST card in that suit — duck to opponents, who win
    the trick but exhaust their high cards. Next time the suit is
    led, our length wins.

    Textbook "duck once" rule for AKxxx-vs-Qx, AKQxx-vs-Jx, etc.
    Does not override Phase 1 (trump-drawing) or Phase 5 (cash
    from short side); those run earlier in planned_card.
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if not _is_declarer_side(seat, declarer):
        return None
    contract_suit = board.contract.suit
    # If suit contract, only develop length after trumps drawn.
    if contract_suit != Suit.NOTRUMP:
        opp_trumps = _opponent_trump_count(
            board, declarer, contract_suit, [])
        if opp_trumps > 0:
            return None
    plan = build_declarer_plan(board, declarer)
    if plan is None or plan.deficit <= 0:
        return None
    best_suit = None
    best_length = 0
    for suit, sa in plan.per_suit.items():
        if suit == contract_suit:
            continue
        if sa.length_winners > best_length:
            best_length = sa.length_winners
            best_suit = suit
    if best_suit is None:
        return None
    sa = plan.per_suit[best_suit]
    is_long_side = ((seat == declarer
                     and sa.declarer_count > sa.dummy_count)
                    or (seat == _partner(declarer)
                        and sa.dummy_count > sa.declarer_count))
    if not is_long_side:
        return None
    my_cards = [c for c in board.hands[seat].cards
                if c.suit == best_suit]
    if not my_cards:
        return None
    return _lowest_in_suit(my_cards)


def planned_lead_toward_partner_trump(board: BoardState, seat: Seat
                                        ) -> Optional[Card]:
    """Phase 21 — declarer-side lead a small trump TOWARD partner's
    boss trump. Complements Phase 1 (which leads high from own
    hand). When partner holds the highest outstanding trump and
    opponents still hold trumps, lead my lowest trump so partner's
    boss wins the next trick (which also lets us continue drawing
    trumps on the next lead).

    Fires when:
    - Declarer-side on lead, suit contract.
    - Opponents still hold trumps.
    - Partner (visible) holds a trump higher than any opp trump.
    - I have at least one trump to lead.
    - I do NOT hold the boss trump (else Phase 1 fires instead).
    """
    if board.contract is None:
        return None
    if board.contract.suit == Suit.NOTRUMP:
        return None
    declarer = board.contract.declarer
    if not _is_declarer_side(seat, declarer):
        return None
    trump = board.contract.suit
    opp_trumps = _opponent_trump_count(board, declarer, trump, [])
    if opp_trumps <= 0:
        return None
    opp_top = _highest_outstanding_opp_trump(
        board, seat, declarer, trump, [])
    if opp_top is None:
        return None
    my_trumps = [c for c in board.hands[seat].cards if c.suit == trump]
    if not my_trumps:
        return None
    my_top = min(my_trumps, key=lambda c: c.rank.value)
    if my_top.rank.value < opp_top.value:
        return None  # I have the boss — Phase 1 handles
    partner = _partner(seat)
    partner_trumps = [c for c in board.hands[partner].cards
                      if c.suit == trump]
    if not partner_trumps:
        return None
    partner_top = min(partner_trumps, key=lambda c: c.rank.value)
    if partner_top.rank.value >= opp_top.value:
        return None  # partner doesn't have boss either
    # Partner has the boss; lead my lowest trump
    return max(my_trumps, key=lambda c: c.rank.value)


def planned_lead_trump_disrupt(board: BoardState, seat: Seat
                                 ) -> Optional[Card]:
    """Phase 20 — defender leads trump to disrupt declarer's
    crossruff plan.

    Fires when:
    - I'm a defender on lead (post-T1).
    - Suit contract.
    - Dummy holds a 5+ side suit (likely crossruff source).
    - I have 3+ trumps (can afford to lead one).

    Leads my LOWEST trump. Standard defensive technique: drawing
    declarer's/dummy's trumps cuts off ruffing tricks.
    """
    if board.contract is None:
        return None
    if board.contract.suit == Suit.NOTRUMP:
        return None
    declarer = board.contract.declarer
    if _is_declarer_side(seat, declarer):
        return None
    if not getattr(board, "tricks", None):
        return None
    trump = board.contract.suit
    dummy = Seat((declarer.value + 2) % 4)
    dummy_max_side = max(
        (sum(1 for c in board.hands[dummy].cards if c.suit == s)
         for s in (Suit.SPADES, Suit.HEARTS,
                   Suit.DIAMONDS, Suit.CLUBS)
         if s != trump), default=0)
    if dummy_max_side < 5:
        return None
    my_trumps = [c for c in board.hands[seat].cards
                 if c.suit == trump]
    if len(my_trumps) < 3:
        return None
    return _lowest_in_suit(my_trumps)


def planned_lead_partner_bid_suit(board: BoardState, seat: Seat
                                    ) -> Optional[Card]:
    """Phase 14 — defender leads partner's bid suit (overcall or
    other suit-naming bid) when on lead post-T1.

    Fires when:
    - I'm a defender on lead, post-T1.
    - Partner bid a suit during the auction.
    - That suit isn't trumps (don't lead trump).
    - I hold cards in that suit.

    Lead-style by length: 4th-best if 4+, low from honor if 3-card,
    top of doubleton, singleton. Same convention as opening lead
    in partner's suit (Phase 3 Rule 1).

    Runs before Phase 13 (lead 4th-best from MY longest) — partner's
    bid suit is the higher-priority signal.
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if _is_declarer_side(seat, declarer):
        return None
    if not getattr(board, "tricks", None):
        return None  # T1 already handled
    partner_suit = _partner_bid_suit(board, seat)
    if partner_suit is None:
        return None
    trump = (board.contract.suit
             if board.contract.suit != Suit.NOTRUMP else None)
    if trump is not None and partner_suit == trump:
        return None
    my_cards = [c for c in board.hands[seat].cards
                if c.suit == partner_suit]
    if not my_cards:
        return None
    if len(my_cards) >= 4:
        sorted_cards = sorted(my_cards, key=lambda c: c.rank.value)
        return sorted_cards[3]
    if len(my_cards) == 3:
        return _lowest_in_suit(my_cards)
    if len(my_cards) == 2:
        return _highest_in_suit(my_cards)
    return my_cards[0]


def planned_lead_defender_continuation(board: BoardState, seat: Seat
                                        ) -> Optional[Card]:
    """Phase 13 — defender's mid-deal lead (after T1, not returning
    partner's suit).

    Fires when:
    - I'm a defender on lead.
    - Phase 11 (return partner's suit) didn't fire (partner wasn't
      T1 leader, or I have no cards in partner's suit left).
    - I have a non-trump suit with 4+ cards.

    Lead 4th best from my longest non-trump suit. Continues
    development from MY long suit when partner's suit is dead.
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if _is_declarer_side(seat, declarer):
        return None
    if not getattr(board, "tricks", None):
        return None  # T1 already handled by planned_opening_lead
    trump = (board.contract.suit
             if board.contract.suit != Suit.NOTRUMP else None)
    hand = board.hands[seat]
    by_suit = _cards_by_suit(hand)
    # 4th-best from longest non-trump (5+ cards — Phase 17 tightens
    # 4+ rule.
    non_trump_lengths = {
        s: len(cs) for s, cs in by_suit.items()
        if s != trump}
    if not non_trump_lengths:
        return None
    longest = max(non_trump_lengths, key=non_trump_lengths.get)
    if non_trump_lengths[longest] < 4:
        return None
    cards = sorted(by_suit[longest], key=lambda c: c.rank.value)
    return cards[3]


def planned_lead_toward_strength(board: BoardState, seat: Seat
                                  ) -> Optional[Card]:
    """Phase 12 — declarer-side: lead a low card from the hand
    WITHOUT the honor, toward partner's K/Q/J.

    Fires when:
    - Declarer-side on lead.
    - In a suit contract, trumps must be drawn first.
    - Partner has a K (and I don't have A), Q (and I don't have A
      or K), or J (and I don't have A, K, Q) in a non-trump suit.
    - I have cards in that suit to lead.

    Lead my LOWEST card in that suit — partner's honor wins if
    opp's higher card is offside (the classic "lead toward an
    honor" finesse position).

    Skipped if Phase 5 (cash short side) or Phase 9 (develop
    length) would have fired first.
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if not _is_declarer_side(seat, declarer):
        return None
    contract_suit = board.contract.suit
    if contract_suit != Suit.NOTRUMP:
        opp_trumps = _opponent_trump_count(
            board, declarer, contract_suit, [])
        if opp_trumps > 0:
            return None
    partner = _partner(seat)
    # Define honor priority and "blockers" — what I shouldn't have
    # to make leading toward partner's honor useful.
    honor_blockers = {
        Rank.KING: {Rank.ACE},
        Rank.QUEEN: {Rank.ACE, Rank.KING},
        Rank.JACK: {Rank.ACE, Rank.KING, Rank.QUEEN},
    }
    for suit in (Suit.SPADES, Suit.HEARTS,
                 Suit.DIAMONDS, Suit.CLUBS):
        if suit == contract_suit:
            continue
        my_cards = [c for c in board.hands[seat].cards
                    if c.suit == suit]
        partner_cards = [c for c in board.hands[partner].cards
                         if c.suit == suit]
        if not my_cards or not partner_cards:
            continue
        my_ranks = {c.rank for c in my_cards}
        partner_ranks = {c.rank for c in partner_cards}
        for honor, blockers in honor_blockers.items():
            if honor in partner_ranks and not (my_ranks & blockers):
                return _lowest_in_suit(my_cards)
    return None


def planned_lead_return_partner_suit(board: BoardState, seat: Seat
                                       ) -> Optional[Card]:
    """Phase 11 — defender returns partner's opening lead suit.

    Fires when:
    - I'm a defender on lead (not T1 — partner's opening lead
      already happened).
    - Partner led the opening trick.
    - I still hold cards in partner's suit.
    Lead my LOWEST card in partner's suit. Continues partner's
    attack on the established suit.

    (Phase 19 — skip when dummy holds a higher card — was tested
    and washed within noise; reverted to keep the rule simple.)
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if _is_declarer_side(seat, declarer):
        return None
    tricks = getattr(board, "tricks", None)
    if not tricks:
        return None
    t1 = tricks[0]
    t1_cards = getattr(t1, "cards", None) or []
    if not t1_cards:
        return None
    t1_leader = getattr(t1, "leader", None)
    partner = _partner(seat)
    if t1_leader != partner:
        return None
    t1_lead_suit = t1_cards[0].suit
    my_cards = [c for c in board.hands[seat].cards
                if c.suit == t1_lead_suit]
    if not my_cards:
        return None
    return _lowest_in_suit(my_cards)


def planned_opening_lead(board: BoardState, seat: Seat
                          ) -> Optional[Card]:
    """Phase 3 — defender's opening-lead rules (textbook).

    Fires only when:
    - There's a contract.
    - Seat is LHO of declarer (the standard opening leader).
    - No card has been played yet (board.tricks is empty AND no
      current trick cards — caller's responsibility).

    Lead-selection priority:
    1. Partner's bid suit — 4th-best if 4+, top of 3-card sequence,
       else top of doubleton, else low from honor in 3-card.
    2. Top of a 3-card sequence (KQJ, QJT, JT9) in any non-trump
       suit.
    3. 4th-best from the longest non-trump suit, if 4+ cards.

    Returns None if no rule fires cleanly (then MC+DDS picks).
    Avoids leading trumps and avoids singletons/short suits when no
    clear textbook lead applies — those need more context (ruff
    play, suit-preference signal expectations) than Phase 3 has.
    """
    if board.contract is None:
        return None
    declarer = board.contract.declarer
    if _is_declarer_side(seat, declarer):
        return None
    if seat != Seat((declarer.value + 1) % 4):
        return None  # not LHO; not the opening leader
    if getattr(board, "tricks", None):
        return None  # not T1
    hand = board.hands[seat]
    trump = (board.contract.suit
             if board.contract.suit != Suit.NOTRUMP else None)
    by_suit = _cards_by_suit(hand)

    # Rule 1: Partner's bid suit
    partner_suit = _partner_bid_suit(board, seat)
    if partner_suit is not None and by_suit.get(partner_suit):
        cards = by_suit[partner_suit]
        if trump is None or partner_suit != trump:
            # Top of 3-card sequence in partner's suit
            seq = _three_card_sequence_top(cards)
            if seq is not None:
                return seq
            if len(cards) >= 4:
                # 4th-best
                sorted_hi_lo = sorted(cards, key=lambda c: c.rank.value)
                return sorted_hi_lo[3]
            if len(cards) == 2:
                # Top of doubleton
                return min(cards, key=lambda c: c.rank.value)
            if len(cards) == 3:
                # Low from honor (Hxx) — lowest of three
                return max(cards, key=lambda c: c.rank.value)
            # Singleton — lead it (mild signal; partner expects honor here)
            if len(cards) == 1:
                return cards[0]

    # Rule 2: Top of a 3-card sequence in any non-trump suit
    for suit, cards in by_suit.items():
        if suit == trump:
            continue
        seq = _three_card_sequence_top(cards)
        if seq is not None:
            return seq

    # (Phase 10 attempt — lead A from AK against suit contract —
    # regressed tricks/deal by 0.060 on 518 deals. Telling
    # declarer "I have AK" via the lead gives them info they
    # exploit. Reverted.)

    # Rule 3: 4th best from longest non-trump (4+ cards). Phase 16
    # (HCP tiebreak) and Phase 17 (5+ minimum) both regressed; 4+
    # threshold with first-match wins is the right rule.
    non_trump_lengths = {
        s: len(cs) for s, cs in by_suit.items()
        if s != trump}
    if non_trump_lengths:
        longest = max(non_trump_lengths, key=non_trump_lengths.get)
        if non_trump_lengths[longest] >= 4:
            cards = sorted(by_suit[longest], key=lambda c: c.rank.value)
            return cards[3]

    return None


def planned_card(board: BoardState, seat: Seat,
                  current_trick_cards: Optional[List[Card]] = None
                  ) -> Optional[Card]:
    """Top-level entry point for the strategic planner. Returns a
    card to play, or None to fall through to MC+DDS.

    Dispatches LEAD vs FOLLOW. Phase 1 handles declarer's trump-
    drawing LEAD; Phase 3 handles defender's opening-lead T1; Phase
    2 handles FOLLOW (don't-overtake-partner, second/third-hand
    defender, fourth-hand cheap-win-or-low).
    """
    cfg = get_planner_config()
    if current_trick_cards:
        return planned_follow(board, seat, current_trick_cards)
    if cfg.phase_3_opening_lead:
        op_lead = planned_opening_lead(board, seat)
        if op_lead is not None:
            return op_lead
    if cfg.phase_11_return_partner:
        p11 = planned_lead_return_partner_suit(board, seat)
        if p11 is not None:
            return p11
    if cfg.phase_13_4th_best:
        p13 = planned_lead_defender_continuation(board, seat)
        if p13 is not None:
            return p13
    if cfg.phase_1_trump_lead:
        p1 = planned_lead(board, seat, current_trick_cards)
        if p1 is not None:
            return p1
    if cfg.phase_5_cash_short:
        p5 = planned_lead_cash_short_side(board, seat)
        if p5 is not None:
            return p5
    if cfg.phase_9_length_dev:
        p9 = planned_lead_develop_length(board, seat)
        if p9 is not None:
            return p9
    if cfg.phase_25_k_loc_finesse:
        p25 = planned_lead_finesse(board, seat)
        if p25 is not None:
            return p25
    # (Phase 25 — K-loc-gated AQ finesse — regressed in biq-vs-biq
    # benchmark by -0.067 tricks. Improvement to declarer is matched
    # by biq's defense seeing the same improvement; net hurts the
    # NS IMP metric. The K-loc inference module + finesse function
    # are retained as dead code for use against external opponents
    # where biq's defender isn't there to negate biq's declarer.)
    # (Phase 12 attempt — lead toward partner's K/Q/J — regressed
    # tricks/deal -0.127 and NT -0.300. MC was picking better
    # already; planner rule fires when it shouldn't. Reverted.)
    return None
