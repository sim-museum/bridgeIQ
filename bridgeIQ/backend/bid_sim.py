"""Hybrid bidding: the rules propose a call, simulation checks it at judgment
points and overrides it when another call is clearly better.

How a decision is made (only when `should_simulate` says this is a judgment
point, e.g. compete / sell out / double / invite / game — never an opening,
a convention in progress, or a forcing situation):

1. SAMPLE the three hidden hands. A layout is kept only if biq's OWN rule
   bidder, holding it, would have made every call that seat actually made
   (passes included). So each call's meaning is exactly what biq's rules do
   with it — the bidder and its reader can never disagree (the drift behind
   many of the 2026-09 match losses). `auction_inference`'s hand-written
   HCP/length limits only steer the search (soft), they never decide.
   Sampling is Metropolis-style card swaps between the hidden hands, starting
   from a layout found by local search, so each sample costs a few bidder
   calls instead of a full rejection loop.
2. CANDIDATES: the rule's call plus a few natural alternatives — pass,
   double, the partnership's suits at the cheapest level and at game, 3NT.
3. ROLLOUT each candidate on every sampled layout: the auction is finished by
   the rule bidder at all four seats, so partner's reaction to the candidate
   (including misreading it) is part of its value.
4. SCORE the final contracts double-dummy with +-1 trick uncertainty (real
   play is not double-dummy), as IMPs against the rule's call. Override only
   if the best candidate gains at least MARGIN IMPs on average.

Switches (environment): BIQ_BID_SIM=0 disables; BIQ_BID_SIM_SAMPLES (64),
BIQ_BID_SIM_BUDGET seconds per decision (6.0, a safety cap: the work is
fixed-count so the same position always gets the same call),
BIQ_BID_SIM_MARGIN IMPs (0.8).
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import time
from typing import Dict, List, Optional, Tuple

from .models import Bid, BoardState, Card, Hand, Rank, Seat, Suit, Vulnerability

ENABLED = os.environ.get("BIQ_BID_SIM", "1") == "1"
N_SAMPLES = int(os.environ.get("BIQ_BID_SIM_SAMPLES", "64"))
BUDGET = float(os.environ.get("BIQ_BID_SIM_BUDGET", "6.0"))
MARGIN = float(os.environ.get("BIQ_BID_SIM_MARGIN", "0.8"))
MIN_SAMPLES = 12
T_MIN = float(os.environ.get("BIQ_BID_SIM_TMIN", "2.0"))
KEEP_ACTION = os.environ.get("BIQ_SIM_KEEP_ACTION", "1") == "1"


def set_enabled(on: bool) -> None:
    """Preference switch (File > Preferences > Mouse & Play)."""
    global ENABLED
    ENABLED = bool(on)

_SUITS = (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)
_CARDS = [Card(Suit(c // 13), Rank(c % 13)) for c in range(52)]
_HCP = [4, 3, 2, 1] + [0] * 9
_STRAIN = {Suit.SPADES: "S", Suit.HEARTS: "H", Suit.DIAMONDS: "D",
           Suit.CLUBS: "C", Suit.NOTRUMP: "NT"}
_IMPTAB = [20, 50, 90, 130, 170, 220, 270, 320, 370, 430, 500, 600, 750,
           900, 1100, 1300, 1500, 1750, 2000, 2250, 2500, 3000, 3500, 4000]

# Rule explanations that mark a convention or a slam sequence: the rules own
# these; a natural alternative would be misread.
_CONVENTIONAL = ("after partner's forcing pass", "partner's second x is takeout",
                 "take out partner's double", "reopening 1nt (11-14)",
                 # sprints 189 / 190 (sim check 261020_s193: it passed 5 reopenings
                 # and a 2NT, 5 of 6 away from the reference's call):
                 ": reopening, ", "partner's takeout x of 1nt", "strong 1c, partner's x of", "(partner's 3-level overcall)", "after my game raise (", "kokish: 3h, heart support", "kokish: hearts and ", "balancing x of their 1", "1s-2c: ", ": four of their ", "(strong 1c)", "kokish 2h: hearts or", "overcall over weak 2d: ", "leaping michaels 4", "at the three level", "): x, 5-7 hcp", "x, 5-7 hcp, short in theirs", "advance partner's x of their strong 1c", "accept partner's jump after my negative x", "six-card suit, four of their", "x of their 1nt overcall", " over their x: ", " over their 1nt: ", "pass their 1nt: ", "after partner's 3", "over their jump: ", "4nt rkc for ", "jump shift over the forcing 1nt", "probe for 3nt over partner's 3", "major over partner's new 3", "three trumps over their bid, ", "over their overcall", "reopening with a six-card ", "forcing auction, no pass at the three level", "cue-bid after my negative x", "of theirs: no penalty pass", "ask after my 2nt limit raise", "preemptive jump raise of the overcall: ", "stayman after 1c-1h-1nt", "weak jump overcall: six ", "for partner's overcall over their 4", "for 3nt (after my probe)", "partner's 3h probe", "partner's 3s probe", "answering the 3h probe", "no stopper for 3nt (partner's probe)", "four-card raise, ", "too weak for a negative x at the two level", "back to my four-card major over partner's second x", "and shortness over their 4", "partner's 3h was competitive", "partner's 3s was competitive", "trumps and shortness", "negative x, 8+ hcp, no clear positive", "invitational raise after 1d-1", "invitation after 1d-1", "after partner's forcing jump", "over partner's forcing jump after my 1nt", "over the weak two: six ", "seven-card suit over partner's preempt", "over their 4h: ", "over their 4s: ", "balancing: no x of their", "kokish: 2nt, big balanced", "precision 2d-2nt relay", "stayman: five-card", "(stayman", 
    # sprints 132-133 (the sim passed them away from the reference's call):
    "is a cue below game", "in competition after my negative x",
    # sprints 141-142:
    "hcp, no shortness", "pass the weak two: misfit",
    # sprints 141 / 147 (sim check 02:35):
    "three trumps opposite a six-card major", ": four h and five", ": four s and five",
    # sprint 167 (sim check 07:10): the LAW jump raise over their 2-level overcall
    "competitive jump raise (4 trumps",
    # sprint 174: 5M / 6M over their 4-level raise of their preempt (the reference's calls)
    "with values: ", "seven-card side suit,",
    # sprint 177: game over their weak two with a seven-card major
    "seven-card major, ",
    # sprint 180: the overcaller's five-level rebid after their raise
    "-card suit, short in their",
    "rkc", "blackwood", "keycard", "key card", "king", "gerber", "cuebid",
    "cue-bid", "cue bid", "control", "asking", "relay", "stayman", "transfer",
    "jacoby", "lebensohl", "michaels", "landy", "ghestem", "unusual",
    "kokish", "drury", "bergen", "truscott", "splinter", "smolen",
    "new minor", "fourth-suit", "fourth suit", "nmf", "puppet", "waiting",
    "strong, artificial", "strong artificial", "quant", "grand", "slam",
    "super-accept", "negative double", "responsive", "support double",
    "ucb", "unassuming", "sandwich", "forcing",
    "their strong 1c",            # The reference's defence to a strong club, kept as is
    "2nt-3s", "both minors", "three-suiter",   # The reference's 2NT / 1NT structures
    "for partner's takeout x", "cue of theirs", "behind them",   # sprints 35 / 40 (10-19 sim check)
    "preference to partner's first suit", "instead of a takeout double",
    "no raise to the",                                            # sprints 64 / 80 / 82 (10-21 sim check)
    "accept the invitation (",                                    # sprint 96
    "passed-hand limit raise",                                    # sprint 105
    "Stayman over 1C-1H-1NT", "Stayman answer", "after Stayman",  # sprint 106
    "(Stayman first)",                                            # sprint 114
    "2NT inquiry (feature ask), balanced",                        # sprint 116
    "over the forcing 1NT", "opposite 18-19", "opposite the jump rebid",
    "preference, doubleton", "Pass 1NT: 5-3-3-2 minimum",          # sprint 117
    "over the double: 4-card major", "over the double: 5-card major",  # sprint 113
    "(partner's both majors)", "both majors (5+S, 4+H)", "opener chose the major",  # sprint 119
    "natural over the balanced 1H",                               # sprint 123
    "Cue raise: ", "opposite the cue raise", "accept the cue raise",  # sprint 127
    "six-card suit over their 1NT, to play",                      # sprint 126
    "balanced with four clubs", "game try after the raise (",     # sprints 103 / 99
    "control, fit for partner's",                                 # sprint 130
    "slam try after opener's jump raise",                         # sprint 108
    "No weak jump overcall", "Pass the raise: 4 trumps",          # sprints 100 / 99
    "weak 5-5", "weak two-suiter", "weak, six-card suit over 1NT", "weak, five of partner's minor",  # sprint 110
    "checkback over the 2NT rebid", "Checkback answer",          # sprint 102
    # rotation 3 (sim check 261029 s294: it lowered 6 of 7 game raises of partner's weak two in
    # competition to 3M / pass, away from the reference's 4M, and passed two seven-card 4S):
    "four trumps opposite the weak two", "seven-card weak two, partner raised",
)


def _imp(diff: float) -> int:
    a = abs(diff)
    n = 0
    for t in _IMPTAB:
        if a >= t:
            n += 1
        else:
            break
    return n if diff >= 0 else -n


def _key(b: Bid) -> str:
    if b.is_pass:
        return "P"
    if b.is_double:
        return "X"
    if b.is_redouble:
        return "XX"
    return f"{b.level}{_STRAIN.get(b.suit, '?')}"


def _hand_of(cards: List[int]) -> Hand:
    return Hand(cards=[_CARDS[c] for c in cards])


def _code(card: Card) -> int:
    return int(card.suit) * 13 + int(card.rank)


# ---------------------------------------------------------------- scoring

def duplicate_score(level: int, suit: Suit, tricks: int, vul: bool,
                    dbl: int) -> int:
    """Duplicate score for the declaring side (dbl: 0, 1 = X, 2 = XX)."""
    need = level + 6
    if tricks >= need:
        per = 20 if suit in (Suit.CLUBS, Suit.DIAMONDS) else 30
        trick_pts = (per * level + (10 if suit == Suit.NOTRUMP else 0)) * (1, 2, 4)[dbl]
        s = trick_pts + ((500 if vul else 300) if trick_pts >= 100 else 50)
        if level == 6:
            s += 750 if vul else 500
        if level == 7:
            s += 1500 if vul else 1000
        if dbl:
            s += (50, 50, 100)[dbl]
            s += (tricks - need) * (200 if vul else 100) * dbl
        else:
            s += (tricks - need) * per
        return s
    down = need - tricks
    if not dbl:
        return -down * (100 if vul else 50)
    if vul:
        pen = 200 + 300 * (down - 1)
    else:
        pen = 100 + 200 * min(down - 1, 2) + 300 * max(down - 3, 0)
    return -pen * dbl


def _side_vul(vul: Vulnerability, seat: Seat) -> bool:
    return (vul == Vulnerability.BOTH
            or (vul == Vulnerability.NS and seat.is_ns())
            or (vul == Vulnerability.EW and not seat.is_ns()))


def final_contract(auction: List[Bid], dealer: Seat):
    """(level, strain, declarer, doubled) or None when passed out."""
    seat, last, last_seat, dbl, first = dealer, None, None, 0, {}
    for b in auction:
        if b.is_double:
            dbl = 1
        elif b.is_redouble:
            dbl = 2
        elif not b.is_pass:
            last, last_seat, dbl = b, seat, 0
            first.setdefault((seat.is_ns(), b.suit), seat)
        seat = seat.next()
    if last is None:
        return None
    return last.level, last.suit, first[(last_seat.is_ns(), last.suit)], dbl


_NOISE = ((-1, 0.2), (0, 0.6), (1, 0.2))
_ORDER = [Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES, Suit.NOTRUMP]


def _exp_score(level, suit, t, vul, dbl, noise=True) -> float:
    if not noise:
        return float(duplicate_score(level, suit, t, vul, dbl))
    return sum(p * duplicate_score(level, suit, max(0, min(13, t + d)), vul, dbl)
               for d, p in _NOISE)


def robust_result(contract, tab, vulnerability, noise: bool = True,
                  competent_ns: Optional[bool] = None) -> float:
    """N/S expected score of `contract` (level, strain, declarer, dbl) when
    the OTHER side plays competently: it doubles when that pays and may
    outbid with its best double-dummy contract (which can then be doubled).

    Rollouts finish the auction with biq's rule bidder, which gets confused
    after interference (misses doubles, loses its own fit). Scoring the
    rollout contract as-is made junk bids look good — the live paired run of
    2026-09-26 lost 11-12 IMP on 3NT with 4 HCP, X with 2 HCP, 3H with 2 HCP.
    `tab` must hold every strain for the defending side's seats.

    `competent_ns`: which side gets the competent reply (True = N/S, False =
    E/W, None = whichever side defends). In a decision it must be the
    OPPONENTS only: giving our own side a perfect later double/outbid made
    passing look better than it is (we may have just ended the auction)."""
    if contract is None:
        return 0.0
    level, suit, decl, dbl = contract
    decl_ns = decl.is_ns()
    t = tab[decl.name[0]][_STRAIN[suit]]
    v = _side_vul(vulnerability, decl)
    if competent_ns is not None and competent_ns == decl_ns:
        # The competent side is declaring: nobody improves on the rollout.
        sc = _exp_score(level, suit, t, v, dbl, noise)
        return sc if decl_ns else -sc
    # (a) The defenders double when it pays for them (noise-aware).
    if dbl == 0:
        plain = _exp_score(level, suit, t, v, 0, noise)
        doubled = _exp_score(level, suit, t, v, 1, noise)
        decl_score = min(plain, doubled)
    else:
        decl_score = _exp_score(level, suit, t, v, dbl, noise)
    # (b) ...or outbid with their best contract above ours.
    defenders = [s for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)
                 if s.is_ns() != decl_ns]
    dv = _side_vul(vulnerability, defenders[0])
    best_def = -decl_score                        # their score for defending
    for st in _ORDER:
        lvl = level if _ORDER.index(st) > _ORDER.index(suit) else level + 1
        if lvl > 7:
            continue
        for d in defenders:
            try:
                tt = tab[d.name[0]][_STRAIN[st]]
            except KeyError:
                continue
            # the original declaring side may double them in turn
            sc = min(_exp_score(lvl, st, tt, dv, 0, noise),
                     _exp_score(lvl, st, tt, dv, 1, noise))
            if sc > best_def:
                best_def = sc
    # best_def is from the defenders' view; convert to N/S.
    return best_def if not decl_ns else -best_def


def _over(auction: List[Bid]) -> bool:
    if len(auction) >= 4 and all(b.is_pass for b in auction[-4:]):
        return True
    return (len(auction) >= 4 and all(b.is_pass for b in auction[-3:])
            and any(not b.is_pass for b in auction[:-3]))


# ---------------------------------------------------------------- trigger

def should_simulate(state, rule_bid: Bid, system) -> bool:
    from . import native_bidder as nb
    if state.opener_seat is None:
        return False                                  # openings: rules
    if rule_bid.alert:
        return False
    why = (rule_bid.explanation or "").lower()
    if any(k.lower() in why for k in _CONVENTIONAL):   # entries with capitals matched nothing before 10-21
        return False
    ours = (state.seat, state.seat.partner())
    for s, b in state.bids:
        if b.is_pass or b.is_double or b.is_redouble:
            continue
        if s in ours and b.alert:
            return False                              # our convention running
        if s in ours and b.suit == Suit.NOTRUMP and b.level >= 4:
            return False                              # slam machinery
    if (state.last_level or 0) >= 6:
        return False
    # First response to partner's opening in an uncontested auction: the
    # response structure is well defined, leave it to the rules.
    partner_opened = state.opener_seat == state.seat.partner()
    if partner_opened and not state.my_bids and not state.opp_overcalled:
        return False
    try:
        if nb._partner_was_forcing(state):
            return False
        if nb._slam_already_explored(state):
            return False
    except Exception:
        return False
    # A pass that ends the auction, or any call once someone has shown
    # something: worth checking.
    return True


# ---------------------------------------------------------------- sampling

def _qplus_bad(dealer: Seat, auction: List[Bid], idx: int, seat: Seat,
               actual: Bid, cards: List[int]) -> Optional[int]:
    """1/0 = the hand does/doesn't fit what Q-Plus shows with `actual` here
    (qplus_model profiles); None = no profile, fall back to biq's rules."""
    from . import qplus_model
    ctx = qplus_model.context([_key(b) for b in auction[:idx]],
                              dealer.value, seat.value)
    p = qplus_model.profile(ctx, _key(actual))
    if p is None:
        return None
    lens = [0, 0, 0, 0]
    for x in cards:
        lens[x // 13] += 1
    hcp = sum(_HCP[x % 13] for x in cards)
    return 0 if qplus_model.fits(p, hcp, lens) else 1


class _Sampler:
    """Hidden-hand sampler conditioned on biq's own rule bidder (and, for
    Q-Plus opponents with BIQ_OPP_MODEL=qplus, on Q-Plus's recorded calls)."""

    def __init__(self, state, hand: Hand, system, rng: random.Random,
                 deadline: float):
        from . import native_bidder as nb
        self.nb = nb
        self.state = state
        self.system = system
        self.rng = rng
        self.deadline = deadline
        self.me = state.seat
        self.dealer = state.dealer
        self.vul = state.vulnerability
        self.auction = [b for _s, b in state.bids]
        self.hidden = [s for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)
                       if s != self.me]
        mine = {_code(c) for c in hand.cards}
        self.unknown = [c for c in range(52) if c not in mine]
        # Each hidden seat holds 13 cards (bidding: nothing played yet).
        self.calls: Dict[Seat, List[Tuple[int, Bid]]] = {s: [] for s in self.hidden}
        seat = self.dealer
        for i, b in enumerate(self.auction):
            if seat in self.calls:
                self.calls[seat].append((i, b))
            seat = seat.next()
        self.cache: Dict[Tuple[Seat, Tuple[int, ...]], int] = {}
        from . import qplus_model
        self.opp_model = qplus_model.enabled()
        self.limits = self._learned_limits()

    def _learned_limits(self):
        """Per-seat HCP / suit-length ranges LEARNED from biq's own bidder:
        deal the seat random hands from the unseen cards and keep those with
        which the rules make every call it made. Being derived from the same
        rules as the exact check, these guide the search toward layouts that
        pass it (a Michaels 2H becomes spades + a minor, not hearts).
        Falls back to the hand-written auction_inference limits for a seat
        whose calls are too rare to learn in the time available."""
        written = self._soft_limits()
        out = {}
        for seat in self.hidden:
            if not self.calls[seat]:
                continue
            hits = []
            tries = 0
            # Fixed work (not time) so results don't depend on machine load.
            while len(hits) < 60 and tries < 2500 and time.time() < self.deadline:
                tries += 1
                cards = self.rng.sample(self.unknown, 13)
                if self._mismatch(seat, cards) == 0:
                    hits.append(cards)
            if len(hits) >= 12:
                hcps = sorted(sum(_HCP[c % 13] for c in h) for h in hits)
                lens = [[sum(1 for c in h if c // 13 == sv) for h in hits]
                        for sv in range(4)]

                class _L:  # same shape as SeatConstraints
                    pass
                lim = _L()
                lim.hcp_min, lim.hcp_max = hcps[0], hcps[-1]
                lim.suit_len = {sv: (min(lens[sv]), max(lens[sv]))
                                for sv in range(4)}
                out[seat] = lim
            elif seat in written:
                out[seat] = written[seat]
        return out

    def _soft_limits(self):
        try:
            from .auction_inference import infer_constraints
            board = BoardState(dealer=self.dealer, vulnerability=self.vul,
                               auction=list(self.auction))
            cons = infer_constraints(board, self.system)
            return {s: cons[s] for s in self.hidden}
        except Exception:
            return {}

    # soft (hand-written) limits: guidance only
    def _soft(self, seat: Seat, cards: List[int]) -> float:
        c = self.limits.get(seat)
        if c is None:
            return 0.0
        hcp = sum(_HCP[x % 13] for x in cards)
        v = max(0, c.hcp_min - hcp) + max(0, hcp - c.hcp_max)
        if c.suit_len:
            lens = [0, 0, 0, 0]
            for x in cards:
                lens[x // 13] += 1
            for sv, (lo, hi) in c.suit_len.items():
                v += max(0, lo - lens[sv]) + max(0, lens[sv] - hi)
        return float(v)

    # exact: biq's bidder must reproduce the seat's calls
    def _mismatch(self, seat: Seat, cards: List[int]) -> int:
        key = (seat, tuple(sorted(cards)))
        got = self.cache.get(key)
        if got is not None:
            return got
        nb = self.nb
        e = None
        bad = 0
        opp_q = self.opp_model and seat not in (self.me, self.me.partner())
        for idx, actual in self.calls[seat]:
            if opp_q:
                q = _qplus_bad(self.dealer, self.auction, idx, seat, actual, cards)
                if q is not None:
                    bad += q
                    continue
            if e is None:
                e = nb.evaluate_hand(_hand_of(cards))
            st = nb.parse_auction(seat, self.dealer, self.auction[:idx],
                                  vulnerability=self.vul)
            try:
                b = nb.decide_bid(st, e, self.system)
            except Exception:
                bad += 1
                continue
            if _key(b) != _key(actual):
                bad += 1
        self.cache[key] = bad
        return bad

    def _weight_mism(self, seat: Seat, m: int) -> float:
        # Partner is biq: its calls are known exactly. Opponents are another
        # program; a call off biq's rules is less informative.
        return (3.0 if seat == self.me.partner() else 1.0) * m

    def _cost(self, hands: Dict[Seat, List[int]]) -> Tuple[float, float]:
        exact = sum(self._weight_mism(s, self._mismatch(s, hands[s]))
                    for s in self.hidden)
        soft = sum(self._soft(s, hands[s]) for s in self.hidden)
        return exact, soft

    def _deal(self) -> Dict[Seat, List[int]]:
        cards = list(self.unknown)
        self.rng.shuffle(cards)
        return {s: cards[13 * i:13 * (i + 1)] for i, s in enumerate(self.hidden)}

    def _swap(self, hands):
        a, b = self.rng.sample(self.hidden, 2)
        i, j = self.rng.randrange(13), self.rng.randrange(13)
        hands[a][i], hands[b][j] = hands[b][j], hands[a][i]
        return a, b, i, j

    def _seat_cost(self, seat: Seat, cards: List[int]) -> Tuple[float, float]:
        return (self._weight_mism(seat, self._mismatch(seat, cards)),
                self._soft(seat, cards))

    def _find_start(self) -> Optional[Tuple[Dict[Seat, List[int]], float]]:
        """Local search to a layout the bidder reproduces (or nearly). Costs
        are kept per seat and only the two seats a swap touches are redone."""
        best = None
        for restart in range(6):
            # The limits only guide, and hand-written ones can be WRONG (they
            # read a Michaels 2H as five hearts): their weight fades to zero
            # over the restarts so they can never block an exact layout.
            sw = (0.5, 0.5, 0.25, 0.0, 0.0, 0.0)[restart]
            hands = self._deal()
            cost = {x: self._seat_cost(x, hands[x]) for x in self.hidden}
            exact = sum(c[0] for c in cost.values())
            soft = sum(c[1] for c in cost.values())
            temp = 1.0
            for _step in range(1500):
                if exact == 0 and (soft <= 2 or sw == 0):
                    break
                if time.time() > self.deadline:
                    break
                a, b, i, j = self._swap(hands)
                ca, cb = self._seat_cost(a, hands[a]), self._seat_cost(b, hands[b])
                e2 = exact - cost[a][0] - cost[b][0] + ca[0] + cb[0]
                s2 = soft - cost[a][1] - cost[b][1] + ca[1] + cb[1]
                old = exact * 4 + soft * sw
                new = e2 * 4 + s2 * sw
                if new <= old or self.rng.random() < math.exp((old - new) / temp):
                    exact, soft = e2, s2
                    cost[a], cost[b] = ca, cb
                else:
                    hands[a][i], hands[b][j] = hands[b][j], hands[a][i]
                temp = max(0.05, temp * 0.995)
            if best is None or exact < best[1]:
                best = ({x: list(h) for x, h in hands.items()}, exact)
            if exact == 0 or time.time() > self.deadline:
                break
        return best

    def sample(self, n: int) -> List[Dict[Seat, List[int]]]:
        start = self._find_start()
        if start is None:
            return []
        hands, level = start
        # How much of the auction the layouts could NOT reproduce (0 = all
        # calls explained by biq's rules). Callers get more conservative
        # when opponents made calls outside biq's rule space.
        self.level = level
        # The chain stays inside the set the start reached: every kept layout
        # reproduces the auction at least that well.
        exact_of = {x: self._seat_cost(x, hands[x])[0] for x in self.hidden}
        exact = sum(exact_of.values())
        out = []
        thin = 10
        steps = 0
        while len(out) < n and time.time() < self.deadline:
            a, b, i, j = self._swap(hands)
            ea = self._weight_mism(a, self._mismatch(a, hands[a]))
            eb = self._weight_mism(b, self._mismatch(b, hands[b]))
            e2 = exact - exact_of[a] - exact_of[b] + ea + eb
            if e2 > level:
                hands[a][i], hands[b][j] = hands[b][j], hands[a][i]
            else:
                exact, exact_of[a], exact_of[b] = e2, ea, eb
            steps += 1
            if steps % thin == 0:
                out.append({x: list(h) for x, h in hands.items()})
        return out


# ---------------------------------------------------------------- candidates

def _legal(nb, b: Bid, state) -> bool:
    try:
        return nb._is_legal_bid(b, state)
    except Exception:
        return False


def _freely_bid_game(state) -> bool:
    """The opponents' contract is a game (or slam) they bid without being
    pushed: our side never bid above the 2-level. Textbook: don't double a
    freely bid game without a sure trump trick stack. The simulation's
    doubles here lost live (FRESH64O RANDOM-029: X of 1H-1S-2S-4S, made,
    -13/-14 in runs 26 and 27; RANDOM-032: 1S-(2C)-3S-4S doubled, made, -8);
    double-dummy defence on sampled layouts flatters the doubler."""
    ours = (state.seat, state.seat.partner())
    last_bid = None
    our_max = 0
    for s, b in state.bids:
        if b.is_pass or b.is_double or b.is_redouble or b.suit is None:
            continue
        last_bid = (s, b)
        if s in ours:
            our_max = max(our_max, b.level)
    if last_bid is None or last_bid[0] in ours:
        return False
    b = last_bid[1]
    game = (b.level >= 6
            or (b.suit == Suit.NOTRUMP and b.level >= 3)
            or (b.suit in (Suit.HEARTS, Suit.SPADES) and b.level >= 4)
            or (b.suit in (Suit.CLUBS, Suit.DIAMONDS) and b.level >= 5))
    return game and our_max <= 2


def candidates(state, hand: Hand, system, rule_bid: Bid) -> List[Bid]:
    """The rule's call plus natural alternatives the hand can justify.

    Values/fit/length limits keep out junk calls (3NT on 4 HCP, a double on
    2, a 3-level raise on 2) that only look good when the rollout opponents
    misplay; the live paired run lost ~45 IMP on exactly those."""
    from . import native_bidder as nb
    e = nb.evaluate_hand(hand)
    out: List[Bid] = [rule_bid]
    seen = {_key(rule_bid)}
    try:
        pmin = nb._partner_min_hcp(state)
    except Exception:
        pmin = 0
    try:
        plens = nb._partner_suit_lengths(state, system)
    except Exception:
        plens = {}

    def justified(b: Bid) -> bool:
        if b.is_pass:
            return True
        if b.is_double:
            return e.hcp >= 10 or (e.hcp >= 8 and pmin >= 10)
        if b.suit == Suit.NOTRUMP:
            return e.hcp + pmin >= (23 if b.level >= 3 else 19)
        n = e.suit_lengths.get(b.suit, 0)
        fit = n + plens.get(b.suit, 0)
        if b.level <= 2:
            return n >= 5 or fit >= 8
        # 3-level and up: LAW (trumps >= level + 6) or real values.
        return (fit >= b.level + 6 or n >= b.level + 3
                or (fit >= 8 and e.hcp + pmin >= 20 + 2 * (b.level - 3)))

    # A preempter (weak two / 3-level opening) does not bid again on its own
    # once partner has passed: the simulation re-bid 3H after 2H-P-P-(2S)
    # and went two down doubled (live run 21, RANDOM-062). Textbook.
    op = state.opening_bid
    preempted = (op is not None and state.opener_seat == state.seat
                 and op.suit not in (None, Suit.NOTRUMP)
                 and (op.level == 3 or (op.level == 2 and op.suit != Suit.CLUBS))
                 and all(b.is_pass for b in state.partner_bids))

    def add(b: Bid):
        k = _key(b)
        if k in seen or not _legal(nb, b, state):
            return
        if preempted and not b.is_pass:
            return
        if not (b.is_pass or b.is_double) and b.level > 5:
            return
        if not justified(b):
            return
        seen.add(k)
        out.append(b)

    # Passing partner's takeout double turns it into penalties, and passing
    # instead of a natural overcall gives up the auction: double-dummy
    # defence makes both look better than they play. In the Precision run
    # vs the reference (2026-10-04) the simulation's passes over the rules'
    # takeout-double advances came on boards that lost 78 IMP in 8, over
    # natural overcalls 41 IMP in 13. BIQ_SIM_KEEP_ACTION=0 restores.
    rwhy = (rule_bid.explanation or "").lower()
    keep_action = (KEEP_ACTION and not rule_bid.is_pass
                   and ("advance takeout" in rwhy
                        or ("overcall" in rwhy and "raise" not in rwhy
                            and "rebid" not in rwhy)))
    if not keep_action:
        add(nb.passb(why="Simulation candidate: pass"))
    last = state.last_non_pass
    # A penalty double only as an alternative to PASSING. Replacing the rules'
    # own bid with a double (doubling their 4-level save instead of bidding
    # our 4S) lost 22 IMP on four FRESH64I boards; doubling where the rules
    # would pass won 11 on five boards (live paired runs, 2026-09-28).
    rule_is_bid = not (rule_bid.is_pass or rule_bid.is_double
                       or rule_bid.is_redouble)
    if (last is not None and last[0] not in (state.seat, state.seat.partner())
            and not rule_is_bid
            and not last[1].is_double and not last[1].is_redouble
            and not _freely_bid_game(state)):
        add(nb.double(why="Simulation candidate: double"))
    opp_suits = set(state.suit_bid_by_opps)
    try:
        plens = nb._partner_suit_lengths(state, system)
    except Exception:
        plens = {}
    suits = []
    for s in _SUITS:
        mine = e.suit_lengths.get(s, 0)
        if s in opp_suits and s not in state.suit_bid_by_partner \
                and s not in state.suit_bid_by_me:
            continue                                  # would be a cue-bid
        if plens.get(s, 0) + mine >= 8 or mine >= 6 \
                or (s in state.suit_bid_by_me and mine >= 5):
            suits.append(s)
    for s in suits:
        lvl = nb._cheapest_level_over_auction(state, s)
        game = 4 if s in (Suit.HEARTS, Suit.SPADES) else 5
        if lvl <= 5:
            add(nb.bid(lvl, s, why=f"Simulation candidate: {lvl}{s.to_char()}"))
        if lvl < game <= 5:
            add(nb.bid(game, s, why=f"Simulation candidate: {game}{s.to_char()}"))
    add(nb.bid(3, Suit.NOTRUMP, why="Simulation candidate: 3NT"))
    return out[:8]


# ---------------------------------------------------------------- rollout

def _rollout(nb, state, system, hands: Dict[Seat, Hand], evals, first: Bid):
    auction = [b for _s, b in state.bids] + [first]
    seat = state.seat.next()
    for _ in range(24):
        if _over(auction):
            break
        st = nb.parse_auction(seat, state.dealer, list(auction),
                              vulnerability=state.vulnerability)
        try:
            b = nb.decide_bid(st, evals[seat], system)
            b = nb._legalize_bid(b, st)
        except Exception:
            b = nb.passb()
        auction.append(b)
        seat = seat.next()
    return final_contract(auction, state.dealer)


# ---------------------------------------------------------------- decision

def _seed(state, hand: Hand) -> int:
    key = ",".join(sorted(f"{int(c.suit)}{int(c.rank):02d}" for c in hand.cards))
    key += "|" + " ".join(_key(b) for _s, b in state.bids) + f"|{state.seat.value}"
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def maybe_override(state, hand: Hand, system, rule_bid: Bid,
                   budget: Optional[float] = None) -> Bid:
    """Return the rule's call, or a better one found by simulation."""
    if not ENABLED or hand is None or not hand.cards or len(hand.cards) != 13:
        return rule_bid
    try:
        if not should_simulate(state, rule_bid, system):
            return rule_bid
        return _decide(state, hand, system, rule_bid,
                       BUDGET if budget is None else budget)
    except Exception:
        return rule_bid


def _decide(state, hand: Hand, system, rule_bid: Bid, budget: float) -> Bid:
    from . import native_bidder as nb
    from .dds import DDSolver
    t0 = time.time()
    rng = random.Random(_seed(state, hand))
    cands = candidates(state, hand, system, rule_bid)
    if len(cands) < 2:
        return rule_bid
    # Deadline is only a safety cap; the work itself is fixed-count.
    sampler = _Sampler(state, hand, system, rng, t0 + budget * 3)
    layouts = sampler.sample(N_SAMPLES)
    if len(layouts) < MIN_SAMPLES:
        return rule_bid
    unexplained = getattr(sampler, "level", 0) > 0
    if unexplained:
        # An opponent made a call biq's rules can't produce with any sampled
        # hand (the reference style, e.g. 1C-(1H)-X-(2D)-4S). The samples then
        # treat it as noise, the opponents look like they overbid, and a
        # penalty double looks great: FRESH64F lost 35 IMP on three such
        # doubles of making contracts. Don't double what we can't read.
        keep = [0] + [k for k in range(1, len(cands)) if not cands[k].is_double]
        cands = [cands[k] for k in keep]
        if len(cands) < 2:
            return rule_bid
    my_cards = [_code(c) for c in hand.cards]
    # Rollouts.
    results = []                     # per layout: list of contracts per cand
    for lay in layouts:
        hands = {s: _hand_of(lay[s]) for s in lay}
        hands[state.seat] = hand
        evals = {s: nb.evaluate_hand(h) for s, h in hands.items()}
        results.append([_rollout(nb, state, system, hands, evals, c) for c in cands])
        if time.time() > t0 + budget * 3:
            break
    if len(results) < MIN_SAMPLES:
        return rule_bid
    layouts = layouts[:len(results)]
    # Full double-dummy tables: the robust scorer needs every strain for the
    # side that may outbid.
    pbns = []
    for lay in layouts:
        full = dict(lay)
        full[state.seat] = my_cards
        pbns.append(_pbn(full))
    tables = DDSolver().solve_dd_tables(pbns)
    # Expected score for our side with +-1 trick uncertainty.
    ours_ns = state.seat.is_ns()
    gains = [0.0] * len(cands)
    wins = [0] * len(cands)
    per = [[] for _ in cands]                    # per-layout IMP vs rule
    for row, tab in zip(results, tables):
        scores = []
        for c in row:
            ns = robust_result(c, tab, state.vulnerability,
                               competent_ns=not ours_ns)
            scores.append(ns if ours_ns else -ns)
        base = scores[0]
        for k, sc in enumerate(scores):
            g = _imp(sc - base)
            gains[k] += g
            per[k].append(g)
            wins[k] += 1 if g > 0 else 0
    n = len(results)
    avg = [g / n for g in gains]
    best = max(range(len(cands)), key=lambda k: avg[k])
    # Stricter bar for a penalty double (double-dummy defence flatters the
    # doubler) and for any override when the auction wasn't fully explained.
    margin, tmin = MARGIN, T_MIN
    if cands[best].is_double:
        margin, tmin = max(margin, 2.0), max(tmin, 3.0)
    if unexplained:
        margin, tmin = max(margin, 2 * MARGIN), max(tmin, 3.0)
    if best == 0 or avg[best] < margin:
        return rule_bid
    # Significance: the gain must be at least T_MIN standard errors, or a
    # coin-flip difference on 64 layouts overrides good rules (A/B board 6:
    # 3NT "+1.0" over a cold 4H with a 9-card fit).
    xs = per[best]
    var = sum((x - avg[best]) ** 2 for x in xs) / max(1, n - 1)
    se = math.sqrt(var / n) if n > 1 else float("inf")
    if se > 0 and avg[best] / se < tmin:
        return rule_bid
    chosen = cands[best]
    out = Bid(level=chosen.level, suit=chosen.suit, is_pass=chosen.is_pass,
              is_double=chosen.is_double, is_redouble=chosen.is_redouble,
              explanation=(f"Simulation: {_key(chosen)} gains {avg[best]:+.1f} "
                           f"IMP over the rules' {_key(rule_bid)} on {n} "
                           f"hands consistent with the auction (rules: "
                           f"{rule_bid.explanation or 'no reason given'})"))
    return out


def _pbn(hands: Dict[Seat, List[int]]) -> str:
    ranks = "AKQJT98765432"
    parts = []
    for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
        by = [[], [], [], []]
        for c in hands[s]:
            by[c // 13].append(c % 13)
        parts.append(".".join("".join(ranks[r] for r in sorted(x)) for x in by))
    return "N:" + " ".join(parts)


# ---------------------------------------------------------------- card play

_SAYC = None


# The system the card-play sampler checks hidden hands' calls against. It
# was always SAYC, so in Precision matches every strong 1C / 11-15 2C /
# 14-16 1NT "mismatched" every sampled hand and the auction filter was noise.
# Set by the table manager / Q-NET client; BIQ_PLAY_SYSTEM overrides.
_PLAY_SYSTEM = None


def set_play_system(name: Optional[str]) -> None:
    global _PLAY_SYSTEM
    name = os.environ.get("BIQ_PLAY_SYSTEM") or name
    if not name:
        _PLAY_SYSTEM = None
        return
    from .bidding_systems import get_system
    _PLAY_SYSTEM = get_system(name)


def play_system():
    if _PLAY_SYSTEM is None and os.environ.get("BIQ_PLAY_SYSTEM"):
        set_play_system(None)
    return _PLAY_SYSTEM


def auction_mismatch(dealer: Seat, vul: Vulnerability, auction: List[Bid],
                     seat: Seat, cards: List[int], system=None,
                     cache: Optional[dict] = None, opponent: bool = False) -> int:
    """How many of `seat`'s calls biq's rule bidder would NOT make holding
    `cards` (its original 13, as card codes). 0 = fully consistent.
    `opponent`: the seat is a Q-Plus opponent; with BIQ_OPP_MODEL=qplus its
    calls are checked against Q-Plus's recorded calls where known."""
    global _SAYC
    from . import native_bidder as nb
    if system is None:
        if _SAYC is None:
            from .bidding_systems import get_system
            _SAYC = get_system("SAYC")
        system = _SAYC
    key = (seat, tuple(sorted(cards)))
    if cache is not None and key in cache:
        return cache[key]
    from . import qplus_model
    opp_q = opponent and qplus_model.enabled()
    e = nb.evaluate_hand(_hand_of(cards))
    bad = 0
    s = dealer
    for i, actual in enumerate(auction):
        if s == seat:
            if opp_q:
                q = _qplus_bad(dealer, auction, i, seat, actual, cards)
                if q is not None:
                    bad += q
                    s = s.next()
                    continue
            st = nb.parse_auction(seat, dealer, auction[:i], vulnerability=vul)
            try:
                b = nb.decide_bid(st, e, system)
            except Exception:
                b = None
            if b is None or _key(b) != _key(actual):
                bad += 1
        s = s.next()
    if cache is not None:
        cache[key] = bad
    return bad
