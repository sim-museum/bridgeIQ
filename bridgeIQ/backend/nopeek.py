"""No-peek cardplay engine for biq.

Plays every card using ONLY what the seat is entitled to see — its own hand,
the dummy once it is exposed, the auction, and the cards already played. It
never solves or samples a layout it cannot legitimately see, so it never peeks
at hidden cards. The guarantee is architectural: `decide()` first REDACTS the
board so the hidden hands are physically empty, then all policy runs on the
redacted board — it is structurally incapable of looking at an opponent's (or,
on defence, partner's) cards.

This is a technique/inference engine (3rd-hand-high, 2nd-hand-low, cover an
honour, draw trumps, cash winners, keep guards, return partner's suit, signal),
not a double-dummy or Monte-Carlo solver. It is the honest player a
sophisticated audience expects; it trades the double-dummy engine's omniscient
ceiling for play that never cheats. Strength grows by adding technique, not by
peeking.

v1 is a competent baseline. Hooks for stronger technique (single-dummy belief
sampling for finesse odds, signalling) are marked TODO.
"""
from __future__ import annotations
from typing import List, Optional, Dict

from .models import Seat, Suit, Rank, Card, Hand, BoardState, Contract
from .cardplay_planner import (is_boss_card, filter_safe_discards,
                               own_side_ranks_in_suit)
from . import cardplay_plan


def _why(explain: Optional[dict], tag: str, reason: str) -> None:
    """Record WHY the card about to be returned was chosen.

    `explain` is an optional mutable dict the caller passes in when it wants
    the rationale (the UI's click-to-explain path). It is None on the hot
    rollout paths (declarer_search / defender_search call decide() thousands
    of times), so this is a zero-cost no-op there. `tag` is a short technique
    label (used as the popup's citation chip); `reason` is a one-sentence
    plain-English explanation of the actual branch taken.
    """
    if explain is not None:
        explain["tag"] = tag
        explain["reason"] = reason


# ---------------------------------------------------------------- redaction

def _dummy_seat(board: BoardState) -> Optional[Seat]:
    return board.contract.declarer.partner() if board.contract else None


def _dummy_exposed(board: BoardState, current_trick_cards: List[Card]) -> bool:
    """Dummy comes down after the opening lead."""
    return bool(board.tricks) or bool(current_trick_cards)


def redact(board: BoardState, seat: Seat,
           current_trick_cards: List[Card]) -> BoardState:
    """A copy of `board` exposing ONLY the hands `seat` is entitled to see.
    Everything else (auction, contract, completed tricks) is public and kept;
    unseen hands are replaced with empty Hands so no policy can read them."""
    entitled = {seat}
    if board.contract is not None:
        decl = board.contract.declarer
        dummy = decl.partner()
        if _dummy_exposed(board, current_trick_cards):
            entitled.add(dummy)                 # everyone sees exposed dummy
        if seat == decl:
            entitled.add(dummy)                 # declarer always sees dummy
        if seat == dummy:
            entitled.add(decl)                  # declarer controls dummy + sees own
    hands = {s: (Hand(cards=list(board.hands[s].cards))
                 if s in entitled and s in board.hands else Hand(cards=[]))
             for s in Seat}
    return BoardState(board_number=board.board_number, dealer=board.dealer,
                      vulnerability=board.vulnerability, hands=hands,
                      auction=list(board.auction), contract=board.contract,
                      tricks=board.tricks)


# ------------------------------------------------------------ card helpers

def _legal(hand: Hand, lead_suit: Optional[Suit]) -> List[Card]:
    if lead_suit is not None:
        follow = [c for c in hand.cards if c.suit == lead_suit]
        if follow:
            return _hi_lo(follow)
    return _hi_lo(hand.cards)


def _hi_lo(cards: List[Card]) -> List[Card]:
    """Sorted high → low (Ace first). rank.value: A=0..2=12, so ascending."""
    return sorted(cards, key=lambda c: (c.suit.value, c.rank.value))


def _by_suit(cards: List[Card]) -> Dict[Suit, List[Card]]:
    out: Dict[Suit, List[Card]] = {}
    for c in cards:
        out.setdefault(c.suit, []).append(c)
    for s in out:
        out[s] = sorted(out[s], key=lambda c: c.rank.value)   # high first
    return out


def _winning_index(trick: List[Card], trump: Optional[Suit]) -> int:
    """Index into `trick` of the card currently winning."""
    lead = trick[0].suit
    wi, wc = 0, trick[0]
    for i, c in enumerate(trick[1:], 1):
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


def _beats(card: Card, win_card: Card, lead: Suit, trump: Optional[Suit]) -> bool:
    """Does `card` (legal, in lead suit since we are following) beat the
    currently-winning card?"""
    win_is_trump = trump is not None and win_card.suit == trump
    if win_is_trump and card.suit != trump:
        return False                                   # can't beat a ruff in-suit
    if card.suit == win_card.suit:
        return card.rank.value < win_card.rank.value   # lower value = higher card
    return False


# ----------------------------------------------------------------- policies

def _opening_lead(board: BoardState, seat: Seat,
                  explain: Optional[dict] = None) -> Card:
    """Defender's opening lead via biq's rule-based lead engine (native_lead).
    It uses ONLY the leader's own hand + public auction/contract — no peek."""
    from . import native_lead
    try:
        d = native_lead.select_opening_lead(
            hand=board.hands[seat], contract=board.contract,
            auction=board.auction, dealer=board.dealer, leader=seat,
            vulnerability=board.vulnerability)
        if d is not None and d.card is not None:
            # Simulation checks the table lead against layouts consistent
            # with the auction and replaces it when another lead is better.
            from . import lead_sim
            sim = lead_sim.choose_opening_lead(board, seat, d.card)
            if sim is not None:
                _why(explain, "Opening lead", sim[1])
                return sim[0]
            # native_lead records the ACTUAL reasons: why this suit, why this
            # card within it. Surface both verbatim.
            reason = " ".join(r for r in (getattr(d, "suit_choice_reason", ""),
                                          getattr(d, "card_choice_reason", ""))
                              if r).strip()
            _why(explain, "Opening lead",
                 reason or "Opening lead from the standard lead tables.")
            return d.card
    except Exception:
        pass
    _why(explain, "Opening lead", "Low from the longest suit.")
    return _by_suit(board.hands[seat].cards)[
        max(_by_suit(board.hands[seat].cards),
            key=lambda s: len(_by_suit(board.hands[seat].cards)[s]))][-1]


def _safe_defender_lead(board: BoardState, seat: Seat, legal: List[Card],
                        trump: Optional[Suit],
                        explain: Optional[dict] = None) -> Card:
    """Subsequent defensive lead: don't underlead honours into a suit contract.
    Prefer top of a touching-honour sequence, the top of an AK, a singleton
    (ruff), or a low card from an honour-LESS suit; underlead only as a last
    resort. No peek (own hand + public)."""
    by = _by_suit([c for c in legal if c.suit != trump]) or _by_suit(legal)

    def seq_top(cards):
        v = [c.rank.value for c in cards]
        for i in range(len(cards) - 2):
            if v[i] + 1 == v[i + 1] and v[i + 1] + 1 == v[i + 2] \
                    and cards[i].rank.value <= 3:
                return cards[i]
        # AK doubleton+ : cash the K (lead it) vs a suit contract
        if trump is not None and len(cards) >= 2 \
                and cards[0].rank == Rank.ACE and cards[1].rank == Rank.KING:
            return cards[1]
        return None

    # 1) top of a sequence / AK in any suit
    for s in sorted(by, key=lambda s: -len(by[s])):
        top = seq_top(by[s])
        if top is not None:
            _why(explain, "Safe defensive lead",
                 "Leading the top of a touching-honour sequence (or an A-K) — "
                 "a safe, aggressive lead that never gives declarer a trick by "
                 "underleading an honour.")
            return top
    # 2) RETURN PARTNER'S SUIT — continue the suit partner attacked. Lead the
    #    higher from a remaining doubleton, else a low card (original count).
    partner = seat.partner()
    partner_suit = None
    for t in board.tricks:
        if getattr(t, "cards", None) and t.leader == partner:
            partner_suit = t.cards[0].suit
            break
    if partner_suit is not None and partner_suit != trump and partner_suit in by:
        cards = by[partner_suit]                       # high → low
        _why(explain, "Return partner's suit",
             "Continuing the suit partner attacked — returning the higher of a "
             "remaining doubleton, otherwise low to preserve count.")
        return cards[0] if len(cards) == 2 else cards[-1]
    # 3) a singleton in a side suit (ruff try) vs a suit contract
    if trump is not None:
        for s in by:
            if len(by[s]) == 1:
                _why(explain, "Singleton lead (ruff try)",
                     "Leading a singleton in a side suit, hoping to ruff the "
                     "next round.")
                return by[s][0]
    # 3) low from an honour-less suit (no A/K/Q to protect)
    honourless = [s for s in by if all(c.rank.value > 2 for c in by[s])]
    if honourless:
        s = max(honourless, key=lambda s: len(by[s]))
        _why(explain, "Safe low lead",
             "Leading low from a suit with no honour to protect — a passive, "
             "safe exit that gives nothing away.")
        return by[s][-1]
    # 4) last resort: low from the longest
    s = max(by, key=lambda s: len(by[s]))
    _why(explain, "Passive lead",
         "No safe attacking lead available — leading low from the longest suit.")
    return by[s][-1]


def _played_values(board: BoardState, suit: Suit) -> set:
    return {c.rank.value for t in board.tricks for c in t.cards
            if c.suit == suit}


def _develop_finesse(board: BoardState, seat: Seat, legal: List[Card],
                     trump: Optional[Suit], declarer: Seat,
                     explain: Optional[dict] = None) -> Optional[Card]:
    """Declarer on lead: if the OTHER declarer hand holds a split tenace over a
    missing honour (AQ / KJ / QT ...), lead a low card from THIS hand toward it.
    The finesse itself falls out of 3rd-hand-high when the other hand follows.
    Uses only the two declarer hands (both visible to declarer). No peek."""
    dummy = declarer.partner()
    other = declarer if seat == dummy else dummy
    if other not in board.hands:
        return None
    best_card, best_len = None, -1
    for suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
        if suit == trump:
            continue
        mine = [c for c in legal if c.suit == suit]
        if not mine:
            continue
        ov = {c.rank.value for c in board.hands[other].cards if c.suit == suit}
        mv = {c.rank.value for c in board.hands[seat].cards if c.suit == suit}
        played = _played_values(board, suit)
        # split tenace: other has v and v+2, the v+1 honour is missing (could
        # be with opponents). v+1 must be an honour rank (<=4 => A..T).
        finesse = any(v in ov and (v + 2) in ov and (v + 1) <= 4
                      and (v + 1) not in ov and (v + 1) not in mv
                      and (v + 1) not in played
                      for v in range(0, 11))
        if finesse and len(mine) > best_len:
            best_card, best_len = mine[-1], len(mine)   # lead our lowest toward it
    if best_card is not None:
        _why(explain, "Finesse",
             "Leading a low card toward a split tenace in the other hand "
             "(e.g. A-Q or K-J), taking the finesse against the missing honour.")
    return best_card


def _establish_suit(board: BoardState, seat: Seat, legal: List[Card],
                    trump: Optional[Suit], declarer: Seat,
                    explain: Optional[dict] = None) -> Optional[Card]:
    """Attack our longest combined side suit to set up length tricks — what a
    good declarer does after drawing trumps, before cashing scattered winners.
    Pick the longest non-trump suit (5+ combined) where we hold the control to
    establish it (>=2 of the top four ranks between the two hands). Lead a boss
    to cash/run it, the top of a touching-honour sequence to force the missing
    honour out, else a low card to concede a round and set up the length. Uses
    only the two declarer hands (visible to declarer). No peek."""
    dummy = declarer.partner()
    other = declarer if seat == dummy else dummy
    best, blen = None, 4
    for su in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
        if su == trump:
            continue
        mine = [c for c in board.hands[seat].cards if c.suit == su]
        pard = ([c for c in board.hands[other].cards if c.suit == su]
                if other in board.hands else [])
        comb = len(mine) + len(pard)
        tops = {c.rank.value for c in mine + pard if c.rank.value <= 3}
        if comb >= 5 and len(tops) >= 2 and comb > blen:
            best, blen = su, comb
    if best is None:
        return None
    here = sorted((c for c in legal if c.suit == best), key=lambda c: c.rank.value)
    if not here:
        return None                                    # suit is in partner's hand
    top = here[0]
    if is_boss_card(top, board, seat, [], declarer):
        _why(explain, "Establish long suit",
             "Attacking our longest combined side suit to set up length tricks "
             "— cashing the top winner in it.")
        return top                                     # cash/run a winner
    if (len(here) >= 2 and top.rank.value <= 3
            and here[1].rank.value == top.rank.value + 1):
        _why(explain, "Establish long suit",
             "Establishing our longest side suit — leading the top of a "
             "touching-honour sequence to force out the missing honour.")
        return top                                     # top of a sequence: force
    _why(explain, "Establish long suit",
         "Establishing our longest side suit — conceding a low round now to set "
         "up length winners later.")
    return here[-1]                                    # low: concede to set up length


def _outstanding_trumps(board: BoardState, trump: Optional[Suit],
                        declarer: Seat) -> int:
    """How many trumps the DEFENDERS still hold (no peek: 13 - our visible
    trumps - trumps already played)."""
    if trump is None:
        return 0
    dummy = declarer.partner()
    ours = sum(1 for s in (declarer, dummy) if s in board.hands
               for c in board.hands[s].cards if c.suit == trump)
    played = sum(1 for t in board.tricks for c in t.cards if c.suit == trump)
    return max(0, 13 - ours - played)


def _lead(board: BoardState, seat: Seat, legal: List[Card],
          trump: Optional[Suit], declarer: Seat,
          explain: Optional[dict] = None) -> Card:
    """On lead, not the opening lead."""
    decl_side = seat.is_ns() == declarer.is_ns()
    if decl_side:
        # 1) DRAW TRUMPS until the defenders have none — even at the cost of
        #    conceding a trump trick. Leading our highest trump forces theirs
        #    out and stops them ruffing our side-suit winners. (The old rule
        #    only drew while our trump was boss, so it abandoned the job once a
        #    defender held the master trump, then got ruffed.)
        if trump is not None and _outstanding_trumps(board, trump, declarer) > 0:
            my_trumps = sorted((c for c in legal if c.suit == trump),
                               key=lambda c: c.rank.value)
            if my_trumps:
                _why(explain, "Draw trumps",
                     "Drawing trumps — the defenders still hold some, so leading "
                     "a high trump forces theirs out and stops them ruffing our "
                     "winners.")
                return my_trumps[0]                # highest trump from this hand
        planned = cardplay_plan.planned_lead(board, seat, [])
        if planned is not None and any(c.suit == planned.suit
                                       and c.rank == planned.rank
                                       for c in legal):
            _why(explain, "Declarer plan",
                 "Following the pre-computed declarer plan for this deal.")
            return planned
        # 2) ESTABLISH the long suit (the trick source) before cashing scattered
        #    short-suit winners — a good declarer attacks length early.
        est = _establish_suit(board, seat, legal, trump, declarer, explain)
        if est is not None:
            return est
        # 3) cash a guaranteed winner (boss card) — lowest such, to keep
        #    communication; prefer the longest suit so we set up length
        bosses = [c for c in legal
                  if is_boss_card(c, board, seat, [], declarer)]
        if bosses:
            by = _by_suit(bosses)
            longest = max(by, key=lambda s: (len(by[s]), -s.value))
            _why(explain, "Cash a winner",
                 "Cashing a guaranteed winner (a master card), taking it from "
                 "our longest such suit to keep communications.")
            return by[longest][0]                      # the boss (top) to cash
        # 3) develop a finesse toward the other hand's tenace
        fin = _develop_finesse(board, seat, legal, trump, declarer, explain)
        if fin is not None:
            return fin
        # DECLARER PLANNER (sees both hands): rather than lead a passive low card
        # into nothing, CROSS to the partner hand when it holds the winners.
        from . import declarer_plan
        pc = declarer_plan.plan_lead(board, seat, declarer, trump)
        if pc is not None and any(c.suit == pc.suit and c.rank == pc.rank
                                  for c in legal):
            _why(explain, "Cross to the other hand",
                 "Crossing to the partner (dummy/declarer) hand, which holds the "
                 "winners we want to reach.")
            return pc
        # 4) otherwise lead low from our longest non-trump side suit
        by = _by_suit([c for c in legal if c.suit != trump]) or _by_suit(legal)
        if by:
            longest = max(by, key=lambda s: (len(by[s]), -s.value))
            _why(explain, "Passive lead",
                 "Nothing active to do — leading low from our longest side suit.")
            return by[longest][-1]                     # low card
        _why(explain, "Forced lead", "Leading our lowest remaining card.")
        return legal[-1]
    # defender on lead (not opening): sound technique, no underleading honours
    return _safe_defender_lead(board, seat, legal, trump, explain)


def _should_holdup(board: BoardState, seat: Seat, trick: List[Card],
                   declarer: Seat, legal: List[Card]) -> bool:
    """Declarer-side hold-up in NT: with a SINGLE stopper in the suit the
    defenders are attacking, duck the early rounds to exhaust the shorter
    defender's cards and cut their communications. Win only once we've ducked
    enough (round > our small-card count). No peek — own + dummy + played."""
    if seat.is_ns() != declarer.is_ns():
        return False
    pos = len(trick)
    leader = Seat((seat.value - pos) % 4)
    if leader.is_ns() == seat.is_ns():
        return False                                   # our side led — no hold-up
    led = trick[0].suit
    my = [c for c in legal if c.suit == led]
    if len(my) < 2:
        return False                                   # need the stopper + a small
    dummy = declarer.partner()
    other = declarer if seat == dummy else dummy
    comb = [c for c in board.hands[seat].cards if c.suit == led]
    if other in board.hands:
        comb += [c for c in board.hands[other].cards if c.suit == led]
    comb.sort(key=lambda c: c.rank.value)
    if not is_boss_card(comb[0], board, seat, trick, declarer):
        return False                                   # we don't hold the master
    if len(comb) >= 2 and is_boss_card(comb[1], board, seat, trick, declarer):
        return False                                   # two stoppers — no need
    small = len(comb) - 1                              # cards besides the stopper
    rounds_already = sum(1 for t in board.tricks
                         if t.cards and t.cards[0].suit == led)
    return small >= 1 and rounds_already < small


def _follow(board: BoardState, seat: Seat, legal: List[Card],
            trick: List[Card], trump: Optional[Suit], declarer: Seat,
            explain: Optional[dict] = None) -> Card:
    """Follow suit. legal = our cards in the led suit, high → low."""
    led = trick[0].suit
    pos = len(trick)                                   # 1=2nd,2=3rd,3=4th hand
    wi = _winning_index(trick, trump)
    win_card = trick[wi]
    win_seat = Seat((Seat((seat.value - pos) % 4).value + wi) % 4)
    our_side_winning = win_seat.is_ns() == seat.is_ns()
    low = legal[-1]
    high = legal[0]

    from . import signals
    if our_side_winning and pos in (2, 3):
        # partner/dummy winning — play a non-winning card, but SIGNAL with it
        # (attitude if partner's suit, count if an opponent's): textbook carding
        # at zero trick cost (every legal card here loses this trick anyway).
        _why(explain, "Signal",
             "Our side already wins this trick, so the card is free — playing "
             "the standard signal (attitude in partner's suit, count in "
             "declarer's) to tell partner about this suit.")
        return signals.choose_signal_card(legal, board, seat, trick, declarer,
                                           trump)
    beating = [c for c in legal if _beats(c, win_card, led, trump)]
    if not beating:
        _why(explain, "Signal",
             "Can't beat the card that's winning, so this trick is lost anyway "
             "— playing the standard signal to inform partner.")
        return signals.choose_signal_card(legal, board, seat, trick, declarer,
                                           trump)
    # HOLD-UP (NT): duck a single stopper to cut defenders' communications.
    if trump is None and _should_holdup(board, seat, trick, declarer, legal):
        _why(explain, "Hold-up",
             "Hold-up play: ducking with our single stopper to sever the "
             "defenders' communications before we have to win.")
        return low
    cheapest = beating[-1]                              # lowest card that still wins
    if pos == 1:                                       # 2nd hand
        # COVER AN HONOUR with an honour to promote our side's spot cards —
        # but not the top of a sequence the leader is visibly holding (don't
        # cover the Q from a QJ; wait for the last of touching honours).
        led_card = trick[0]
        if led_card.rank.value in (1, 2, 3):           # K/Q/J led
            honours = [c for c in beating if c.rank.value <= 3]
            leader = Seat((seat.value - pos) % 4)
            lh = board.hands.get(leader)
            seq = bool(lh and any(c.suit == led and
                                  c.rank.value == led_card.rank.value + 1
                                  for c in lh.cards))
            if honours and not seq:
                _why(explain, "Cover an honour",
                     "Covering the led honour with an honour to promote our "
                     "side's spot cards.")
                return honours[-1]                     # cheapest sufficient honour
        _why(explain, "Second hand low",
             "Second hand low — no reason to spend an honour before seeing what "
             "third hand does.")
        return low                                     # 2nd hand low otherwise
    if pos == 2:                                       # 3rd hand high (cheaply)
        _why(explain, "Third hand high",
             "Third hand high — playing the cheapest card that still wins the "
             "trick for our side.")
        return cheapest
    _why(explain, "Fourth hand",
         "Fourth hand — winning as cheaply as possible.")
    return cheapest                                    # 4th hand: win as cheaply


def _discard(board: BoardState, seat: Seat, legal: List[Card],
             trick: List[Card], declarer: Seat,
             explain: Optional[dict] = None) -> Card:
    """Void in the led suit and not ruffing: pitch a safe card that SIGNALS
    (suit-preference / attitude) instead of a bare low spot."""
    from . import signals
    safe = filter_safe_discards(legal, board, seat, trick, declarer)
    trump = (board.contract.suit
             if board.contract and board.contract.suit != Suit.NOTRUMP
             else None)
    _why(explain, "Discard",
         "Void in the led suit and can't ruff usefully — pitching a safe card "
         "that also signals (suit-preference / attitude) while keeping our "
         "winners and guards intact.")
    return signals.choose_signal_card(safe, board, seat, trick, declarer, trump)


# ------------------------------------------------------------------- entry

import os
# 10 layouts / 12 s: measured 2026-09-26 (nopeek_eval with auction, 2 seeds x
# 40 deals): 106 tricks lost vs DD, against 121 with 6 / 5 s — the user asked
# for strength over speed.
# 40 layouts (2026-10-10, qplus_agreement card benchmark, 4000 Q-Plus cards):
# defence after the opening lead gave up 56 DD tricks vs 78 / 74 with 10 (two
# runs; noise ~6), the reference 45; 3rd/4th seat 14 vs 28 / 24. At the live 12 s
# budget a defensive card took 2.6 s mean, 7 s p90, budget hit on 2 of 150.
# Declarer uses the Monte-Carlo branch (_DECL_PIMC), so this is defence only.
_AMU_WORLDS = int(os.environ.get("BIQ_AMU_WORLDS", "40"))  # sampled layouts/decision
_AMU_DEPTH = int(os.environ.get("BIQ_AMU_DEPTH", "2"))     # alpha-mu depth in tricks
_AMU_BUDGET = float(os.environ.get("BIQ_AMU_BUDGET", "12"))  # per-decision seconds
_AMU_DEFENSE = os.environ.get("BIQ_AMU_DEFENSE", "1") == "1"  # defence alpha-mu
_DEF_ROLLOUT = os.environ.get("BIQ_DEF_ROLLOUT", "0") == "1"  # defence rollout
# (realistic no-peek partner) INSTEAD of alpha-mu defence's perfect-DD partner
_DEF_ROLLOUT_LEAF = os.environ.get("BIQ_DEF_ROLLOUT_LEAF", "0") == "1"  # alpha-mu
# defence with a realistic-partner ROLLOUT leaf (vs the perfect-DD DDS leaf)
_READ_SIGNALS = os.environ.get("BIQ_READ_SIGNALS", "1") == "1"  # hard-filter the
# defence sampler by PARTNER's signals (convention-inversion); see signal_read.
# Default ON now that emitter/reader are aligned (net-positive) and the
# signal_trust auto-disable protects against an unreliable (human) partner.
_SIGNAL_MARGIN = float(os.environ.get("BIQ_SIGNAL_MARGIN", "0.15"))  # trick
# budget for RELIABLE signalling: widen the alpha-mu tie set so biq always plays
# the convention card among cards it rates within 0.15 tr of best (alpha-mu's
# near-ties are noise; the signal heuristic is a better arbiter). A/B 3 seeds:
# trick-neutral-to-positive vs margin 0 (0.786 vs 0.797). 0 = exact ties only.
_SIGNAL_SAFE = os.environ.get("BIQ_SIGNAL_SAFE", "1") == "1"
_SECOND_LOW = float(os.environ.get("BIQ_SECOND_LOW", "0.8"))  # NT defence: a
# lone A/K in 2nd seat over a led spot must beat a duck by this many tricks
# (0 = off). The reference's way in 3NT (probes 2026-10-05/07: it ducks a lone A/K,
# rises with A-K); +0.037 tr on those positions. NOTRUMP ONLY: on 695 random
# SAYC deals played no-peek in all four seats it lost in suit contracts
# (96 diverged, -0.22 tr) and was level in NT (40, -0.03 tr SE 0.14, -26 IMP).
# The reference's suit-contract habit is unprobed (BACKLOG).
# PLAYBELIEF-1: weight the declarer's sampled layouts by how likely the defenders were to
# play the cards they played (biq's own carding: a missed signal x _PLAY_SIG_MISS instead of
# a hard filter; restricted choice: an honour followed from n equivalent cards x 1/n), and
# average the double-dummy results with those weights. 0 = equal weights (as before).
_PLAY_WEIGHT = os.environ.get("BIQ_PLAY_WEIGHT", "1") == "1"
_PLAY_SIG_MISS = float(os.environ.get("BIQ_PLAY_SIG_MISS", "0.25"))
# read a signal only from a clearly high or clearly low spot (signal_read.is_clear_spot);
# a middle spot is not read (x 1). Off until its paired A/B is in (PLAYBELIEF-1 sprint 4).
_PLAY_SIG_CLEAR = os.environ.get("BIQ_PLAY_SIG_CLEAR", "0") == "1"
_PLAY_LEAD = os.environ.get("BIQ_PLAY_LEAD", "1") == "1"   # x the opening-lead factor (play_belief)
# x the honour-discard factor (play_belief); off until its paired A/B is in (PLAYBELIEF-1 sprint 3)
_PLAY_HDISC = os.environ.get("BIQ_PLAY_HDISC", "0") == "1"
_DECL_PIMC = int(os.environ.get("BIQ_DECL_PIMC", "60"))  # declarer: plain
# Monte-Carlo (DD average over this many sampled layouts) instead of alpha-mu;
# 0 = alpha-mu. 3NT probe decl1 (96 deals vs scripted reference defence): 987
# tricks / 89 made vs alpha-mu 945 / 83 (the reference 1005 / 93, DD 978); alpha-mu
# size/depth/tie-break variants were all 939-952. 298 random SAYC deals, biq
# no-peek in all four seats, paired: +0.218 tr/deal (SE 0.056), +185 IMP,
# NT +0.27 (SE 0.12), suit +0.19 (SE 0.06). About 8x faster than alpha-mu.
_DDS = None


def _get_dds():
    global _DDS
    if _DDS is None:
        from .dds import DDSolver
        _DDS = DDSolver()
    return _DDS


def _natural_declarer_card(tied: List[Card], b: BoardState, seat: Seat,
                           trick: List[Card],
                           trump: Optional[Suit]) -> Optional[Card]:
    """Human-natural choice among cards the search rates exactly equal.
    Following suit: win as cheaply as possible, else play the lowest.
    Discarding: the lowest spot, never an honour when a spot will do.
    Leading: a master card from the top (cash winners top-down), else the
    lowest card of the suit the search liked."""
    if not tied:
        return None
    # Rank.value: ACE=0 .. TWO=12, so a HIGHER value is a LOWER card.
    if trick:
        lead = trick[0].suit
        follow = [c for c in tied if c.suit == lead]
        if follow:
            wi = _winning_index(trick, trump)
            leader = Seat((seat.value - len(trick)) % 4)
            ours = Seat((leader.value + wi) % 4).is_ns() == seat.is_ns()
            winners = [c for c in follow if _beats(c, trick[wi], lead, trump)]
            pool = winners if (winners and not ours) else follow
            return max(pool, key=lambda c: c.rank.value)
        ruffs = [c for c in tied if trump is not None and c.suit == trump]
        if ruffs and len(ruffs) == len(tied):
            return max(ruffs, key=lambda c: c.rank.value)
        non_trump = [c for c in tied if c.suit != trump] or tied
        return max(non_trump, key=lambda c: c.rank.value)
    # Leading. (Only declarer's and dummy's hands are visible here.)
    gone = {(c.suit, c.rank) for t in b.tricks for c in t.cards}
    gone |= {(x.suit, x.rank) for h in b.hands.values() for x in h.cards}
    for c in sorted(tied, key=lambda c: c.rank.value):
        if all((c.suit, r) in gone for r in Rank if r.value < c.rank.value):
            return c                                  # a master: cash it
    first_suit = tied[0].suit
    same = [c for c in tied if c.suit == first_suit]
    return max(same, key=lambda c: c.rank.value)


def play_weights(b: BoardState, seat: Seat, trick: List[Card], declarer: Seat,
                 trump: Optional[Suit], samples: list, hidden) -> List[float]:
    """PLAYBELIEF-1: one weight per sampled layout (dict seat -> remaining c52 cards of the
    hidden seats) from the hidden hands' plays so far, by biq's own carding: x _PLAY_SIG_MISS
    for each readable signal the layout's hand would not have given, x 1/n for each honour a
    hidden hand followed with from n equivalent cards (restricted choice), x the honour-discard
    and opening-lead factors (play_belief)."""
    from . import signal_read, signals as _signals
    hidden = list(hidden)
    recs = {}
    if _signals.is_enabled():
        for d in hidden:
            try:
                recs[d] = signal_read.read(b, d.partner(), declarer, trump)
            except Exception:
                recs[d] = []
    from . import play_belief
    plays = play_belief.plays_so_far(b, trick, seat)
    lead_seat = b.tricks[0].leader if b.tricks else None
    played_by = {d: {c for who, c, _ in plays if who == d} for d in hidden}
    out = []
    for a in samples:
        w = 1.0
        for d in hidden:
            rem = set(a[d])
            for rec in recs.get(d, []):
                held = signal_read._hand_at(rec, rem)
                conv = signal_read._convention_card(rec, held)
                if conv is not None and conv != rec["card52"]:
                    if _PLAY_SIG_CLEAR and not signal_read.is_clear_spot(rec, held):
                        continue                      # a middle spot: not read
                    w *= _PLAY_SIG_MISS
        for d in hidden:                       # restricted choice, honour discards
            w *= play_belief.restricted_choice(plays, d, set(a[d]))[0]
            if _PLAY_HDISC:
                w *= play_belief.honour_discard(plays, d, set(a[d]))[0]
        if _PLAY_LEAD and lead_seat in hidden:  # would that hand have made the opening lead?
            w *= play_belief.lead_factor(b, set(a[lead_seat]) | played_by[lead_seat])
        out.append(w)
    return out


def _alphamu_card(b: BoardState, seat: Seat, trick: List[Card],
                  declarer: Seat, trump: Optional[Suit],
                  explain: Optional[dict] = None) -> Optional[Card]:
    """Card via alpha-mu: sample the HIDDEN layouts consistent with the play (a
    BELIEF — never the actual cards), DDS-evaluate, commit ONE line across
    indistinguishable layouts. DDS on representative samples is not peeking; the
    consistent strategy avoids PIMC's omniscient tells.

    Declaring: biq sees both its hands, the two defenders are hidden, and biq
    plays a consistent line for {declarer, dummy} to MAX declarer tricks.
    Defending: biq sees its hand + dummy; declarer AND partner are hidden; biq
    plays a consistent line for its ONE seat to MAX defensive tricks. Other
    seats (a hidden partner, the opponents) each play DD-best for their own side
    at the leaves — cooperation and competition fall out automatically."""
    from . import declarer_search, alphamu
    dummy = declarer.partner()
    defending = seat.is_ns() != declarer.is_ns()
    if defending:
        # need dummy faced to sample only declarer + partner as the hidden hands
        if dummy not in b.hands or not b.hands[dummy].cards:
            return None
        known_seats, biq_seats = {seat, dummy}, {seat}
    else:
        known_seats, biq_seats = {declarer, dummy}, {declarer, dummy}
        if _DECL_PIMC > 0:
            # Plain Monte-Carlo declarer: DD-score every legal card in K
            # sampled layouts, play the best average (natural tie-break).
            samples, hidden = declarer_search._sample_defenders(
                b, seat, trick, declarer, _DECL_PIMC, known_seats=known_seats)
            if samples:
                pbns = []
                for a in samples:
                    w = {s: (frozenset(a[s]) if s in hidden else
                             frozenset(c.code52() for c in b.hands[s].cards))
                         for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)}
                    pbns.append(alphamu._pbn(w))
                leader = Seat((seat.value - len(trick)) % 4)
                res = _get_dds().solve(alphamu._strain_i(trump), leader.value,
                                       [c.code52() for c in trick], pbns,
                                       solutions=3)
                wts = (play_weights(b, seat, trick, declarer, trump, samples, hidden)
                       if _PLAY_WEIGHT else None)
                if res:
                    n = min(len(v) for v in res.values())
                    if n > 0 and wts is not None and sum(wts[:n]) > 0:
                        tw = sum(wts[:n])
                        avg = {c: sum(w * x for w, x in zip(wts[:n], v[:n])) / tw
                               for c, v in res.items()}
                    elif n > 0:
                        avg = {c: sum(v[:n]) / n for c, v in res.items()}
                    if n > 0:
                        best = max(avg.values())
                        tied = [Card.from_code52(c) for c, m in avg.items()
                                if m >= best - 1e-9]
                        pick = _natural_declarer_card(tied, b, seat, trick, trump) \
                            if len(tied) > 1 else tied[0]
                        _why(explain, "Monte-Carlo search",
                             f"Double-dummy average over {len(pbns)} sampled layouts.")
                        return pick
    from . import signal_trust, signals as _signals
    signalling = _signals.is_enabled()
    if defending and _READ_SIGNALS and signalling and signal_trust.reading_active():
        # HARD-FILTER the sampled partner hands by partner's own signals: keep
        # only layouts biq's convention would have produced (legitimate info —
        # partner is telling us). Over-sample, filter, fall back if too few.
        # Gated by signal_trust so an unreliable partner gets auto-tuned out.
        from . import signal_read
        recs = signal_read.read(b, seat, declarer, trump)
        raw, hidden = declarer_search._sample_defenders(
            b, seat, trick, declarer, _AMU_WORLDS * 4, known_seats=known_seats)
        if recs and raw:
            partner = seat.partner()
            kept = [a for a in raw
                    if signal_read.consistent(recs, set(a[partner]))]
            samples = kept[:_AMU_WORLDS] if len(kept) >= 2 \
                else raw[:_AMU_WORLDS]
        else:
            samples = raw[:_AMU_WORLDS]
    else:
        samples, hidden = declarer_search._sample_defenders(
            b, seat, trick, declarer, _AMU_WORLDS, known_seats=known_seats)
    if not samples:
        return None
    worlds = []
    for a in samples:
        w = {}
        for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
            if s in hidden:
                w[s] = frozenset(a[s])
            else:
                w[s] = frozenset(c.suit.value * 13 + c.rank.value
                                 for c in b.hands[s].cards)
        worlds.append(w)
    amu = alphamu.AlphaMu(trump, declarer, _get_dds(), depth=_AMU_DEPTH,
                          time_budget=_AMU_BUDGET, biq_seats=biq_seats,
                          defense_rollout_leaf=(defending and _DEF_ROLLOUT_LEAF),
                          vul=b.vulnerability,
                          signal_margin=(_SIGNAL_MARGIN if signalling
                                         and defending else 0.0),
                          second_low_margin=(_SECOND_LOW if defending
                                             and trump is None else 0.0))
    # When DEFENDING, break trick-equivalent ties by the standard signal so the
    # carding reads like a real defender (zero trick cost — these tie for best).
    # Signalling OFF: no tie-break and no margin — alpha-mu's best card stands.
    tb = None
    if defending and signalling:
        from . import signals
        tb = lambda tied: signals.choose_signal_card(
            tied, b, seat, trick, declarer, trump)
    elif not defending:
        # Declaring: among EXACTLY tied cards play the natural one. Without
        # this the search's first-listed card won ties, so dummy discarded
        # the heart ACE on a spade (RUN2-004 trick 2): double-dummy neutral,
        # absurd to a human watching.
        tb = lambda tied: _natural_declarer_card(tied, b, seat, trick, trump)
    chosen = amu.choose(b, seat, trick, worlds, tiebreak=tb)
    if chosen is not None:
        side = "defence" if defending else "declarer play"
        _why(explain, "Alpha-mu search",
             f"Alpha-mu single-dummy search ({side}): sampled {len(worlds)} "
             "hidden layouts consistent with the play so far, double-dummy "
             "evaluated each, and committed to the one line that maximises "
             "tricks across all of them"
             + (" — ties broken by the standard signal."
                if defending and signalling else "."))
    return chosen


def _defense_rollout_card(b: BoardState, seat: Seat, trick: List[Card],
                          declarer: Seat, trump: Optional[Suit],
                          legal: List[Card]) -> Optional[Card]:
    """Defensive card via MC rollout with a REALISTIC no-peek partner (vs
    alpha-mu defence's perfect-DD partner). See backend.defender_search."""
    from . import defender_search
    return defender_search.best_defense(
        b, seat, trick, declarer, trump, legal, _get_dds(),
        samples=_AMU_WORLDS, time_budget=_AMU_BUDGET)


def _lead_candidates(legal: List[Card]) -> List[Card]:
    """Prune lead candidates to the distinct ones worth searching: the highest
    and lowest card of each suit (leading the 5 vs the 4 of a suit rarely
    differs), cutting rollout cost."""
    by = _by_suit(legal)
    out = []
    for s in by:
        cs = by[s]                                     # high → low
        out.append(cs[0])
        if cs[-1] is not cs[0]:
            out.append(cs[-1])
    return out


def decide(board: BoardState, seat: Seat,
           current_trick_cards: Optional[List[Card]] = None,
           search: bool = True,
           explain: Optional[dict] = None) -> Optional[Card]:
    """Pick `seat`'s card using only entitled information. Redacts first.
    `search=True` lets the DECLARER use a single-dummy lookahead at its leads;
    rollouts call back with search=False (greedy policy, no recursion).
    Pass `explain={}` to have the chosen technique recorded in
    ``explain['tag']`` / ``explain['reason']`` (used by the UI's click-to-
    explain popup); it is None on the hot rollout paths, so this is free there."""
    trick = list(current_trick_cards or [])
    b = redact(board, seat, trick)
    hand = b.hands[seat]
    if not hand.cards:
        return None
    lead_suit = trick[0].suit if trick else None
    legal = _legal(hand, lead_suit)
    if len(legal) == 1:
        _why(explain, "Forced", "Only one legal card to play.")
        return legal[0]

    trump = (board.contract.suit
             if board.contract and board.contract.suit != Suit.NOTRUMP
             else None)
    declarer = board.contract.declarer if board.contract else seat
    on_lead = not trick
    opening = on_lead and not board.tricks

    if opening:
        return _opening_lead(b, seat, explain)
    # Alpha-mu search: DDS on belief-sampled hidden layouts + a consistent line.
    # Strong (DDS evaluates vs best play) without peeking or PIMC's omniscient
    # tells. Declarer side always; defenders when BIQ_AMU_DEFENSE (the partner is
    # hidden too, so defence samples declarer + partner).
    if search and board.contract is not None:
        defending = seat.is_ns() != declarer.is_ns()
        # PURE-SIGNAL situation: defending, following the led suit, and unable to
        # win the trick. The choice among our spots is trick-neutral here, so we
        # play the STANDARD signal among ALL legal cards — exactly what
        # signal_read inverts — making biq a perfectly READABLE signaller (so a
        # partner reading these signals never filters out biq's true hand).
        # Bypass alpha-mu for this card only. Skipped when signalling is OFF
        # (preference): alpha-mu then picks the card on trick value alone.
        from . import signals as _sigsw
        # With BIQ_SIGNAL_SAFE the shortcut is only for small spots (8 and
        # below): a nine, ten or honour can still take or protect a trick,
        # so those holdings go to the search (ties still broken by signal).
        spots_only = (not _SIGNAL_SAFE
                      or all(c.rank.value >= 6 for c in legal))   # 8..2
        if (defending and _sigsw.is_enabled() and lead_suit is not None
                and legal and legal[0].suit == lead_suit and spots_only):
            wi = _winning_index(trick, trump)
            if not any(_beats(c, trick[wi], lead_suit, trump) for c in legal):
                from . import signals as _sig
                _why(explain, "Signal",
                     "Following suit but unable to win this trick, so the choice "
                     "of spot is free — playing the standard signal (attitude / "
                     "count) so partner can read our holding.")
                return _sig.choose_signal_card(legal, b, seat, trick,
                                               declarer, trump)
        amc = None
        if defending and _DEF_ROLLOUT:
            amc = _defense_rollout_card(b, seat, trick, declarer, trump, legal)
            if amc is not None:
                _why(explain, "Defensive rollout",
                     "Chose the defensive card by Monte-Carlo rollout against a "
                     "realistic (no-peek) partner.")
        elif not defending or _AMU_DEFENSE:
            amc = _alphamu_card(b, seat, trick, declarer, trump, explain)
        if amc is not None and any(c.suit == amc.suit and c.rank == amc.rank
                                   for c in legal):
            return amc
    if on_lead:
        return _lead(b, seat, legal, trump, declarer, explain)
    if any(c.suit == lead_suit for c in legal):
        return _follow(b, seat, legal, trick, trump, declarer, explain)
    return _discard(b, seat, legal, trick, declarer, explain)
