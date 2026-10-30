"""No-peek DECLARER search — single-dummy Monte-Carlo with policy rollout.

When declaring, biq sees both its hands; only the defenders' cards are hidden.
This samples plausible defender layouts (a BELIEF from the play so far — voids
shown, remaining counts, vacant places — never the actual cards), and for each
candidate declarer lead plays the deal to the end with the no-peek POLICY for
all four seats, averaging declarer tricks. It returns the lead with the best
average. No DDS and no peeking: the choice is the expected-best over beliefs and
every rollout decision uses only that seat's entitled view (the rollout calls
nopeek.decide with search=False, so each seat still reasons single-dummy).

This is the non-peeking analogue of PIMC: PIMC double-dummy-solves each sample
(strategy fusion); this rolls out the actual policy, which is a real one-ply
policy improvement over greedy play and cannot fuse strategies.
"""
from __future__ import annotations
import random
from typing import List, Optional

from .models import Seat, Suit, Rank, Card, Hand, BoardState, Trick

_ALL = (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)


def _c52(c: Card) -> int:
    return c.suit.value * 13 + c.rank.value


def _winner(cards: List[Card], leader: Seat, trump: Optional[Suit]) -> Seat:
    wi, wc = 0, cards[0]
    lead = cards[0].suit
    for i, c in enumerate(cards[1:], 1):
        c_t = trump is not None and c.suit == trump
        w_t = trump is not None and wc.suit == trump
        if c_t and not w_t:
            wi, wc = i, c
        elif c_t and w_t and c.rank.value < wc.rank.value:
            wi, wc = i, c
        elif not c_t and not w_t and c.suit == lead \
                and c.rank.value < wc.rank.value:
            wi, wc = i, c
    return Seat((leader.value + wi) % 4)


import os as _os
# Card play conditioned on the BIDDING: oversample the hidden hands and keep
# the layouts whose original 13 cards biq's own rules would have bid the
# way those seats actually bid (BIQ_PLAY_AUCTION=0 disables).
_PLAY_AUCTION = _os.environ.get("BIQ_PLAY_AUCTION", "1") == "1"
_OVERSAMPLE = int(_os.environ.get("BIQ_PLAY_OVERSAMPLE", "8"))
# Minimum suit lengths the hidden hands' NATURAL calls showed, dealt first so
# the sampled layouts honour them. Ranking random deals by auction fit could
# not find a weak-two opener's six hearts when he had to hold all six unseen
# ones (~1 deal in 100; the reference board 80: dummy ducked the singleton-heart
# lead and the defence got a ruff, -3 tricks). BIQ_PLAY_MINLEN=0 disables.
_PLAY_MINLEN = _os.environ.get("BIQ_PLAY_MINLEN", "1") == "1"


def _min_lengths(board: BoardState, seats) -> dict:
    """{seat: {suit: n}}: the length each hidden seat's natural suit calls
    promised (original 13 cards). Conservative: openings, simple overcalls,
    1-level responses and rebids of one's own suit; artificial calls (strong
    or short 1C, Precision 2D, anything in a 1NT/2C-opened auction, cue bids
    of the opponents' suit) promise nothing here."""
    auction = list(getattr(board, "auction", []) or [])
    out = {d: {} for d in seats}
    if not auction:
        return out
    strong_club = False
    try:
        from . import bid_sim
        sysm = bid_sim.play_system()
        strong_club = getattr(sysm, "strong_open_call", "2C") == "1C"
        maj_min = getattr(sysm, "one_major_card_min", 5) if sysm else 5
    except Exception:
        maj_min = 5
    seat = board.dealer
    opener = None
    first_call = {}
    bid_by = {}                # seat -> suits it has bid
    for b in auction:
        if not (b.is_pass or b.is_double or b.is_redouble) and b.suit is not None:
            side_ns = seat.is_ns()
            if opener is None:
                opener = seat
                opening = b
            if b.suit == Suit.NOTRUMP:
                bid_by.setdefault(seat, []).append(b.suit)
                seat = seat.next()
                continue
            need = 0
            opp_suits = {x for s_, xs in bid_by.items() for x in xs
                         if s_.is_ns() != side_ns}
            if b.suit in opp_suits:
                need = 0                                   # cue bid
            elif seat == opener and len(bid_by.get(seat, [])) == 0:
                if b.level == 1 and b.suit in (Suit.HEARTS, Suit.SPADES):
                    need = maj_min
                elif b.level == 2 and b.suit in (Suit.HEARTS, Suit.SPADES):
                    need = 6
                elif b.level == 2 and b.suit == Suit.CLUBS and strong_club:
                    need = 6
                elif b.level == 3:
                    need = 6
                elif b.level == 4 and b.suit in (Suit.HEARTS, Suit.SPADES):
                    need = 7
            elif opener is not None and opener.is_ns() != side_ns \
                    and not bid_by.get(seat):
                op_nt = opening.suit == Suit.NOTRUMP or (
                    opening.level == 2 and opening.suit == Suit.CLUBS
                    and not strong_club)
                if not op_nt and b.level <= 3:
                    need = 5                               # natural overcall
            elif opener is not None and opener.is_ns() == side_ns \
                    and seat != opener and not bid_by.get(seat) \
                    and opening.suit != Suit.NOTRUMP \
                    and not (opening.level == 1 and opening.suit == Suit.CLUBS
                             and strong_club) and b.level == 1:
                need = 4                                   # 1-level response
            elif b.suit in bid_by.get(seat, []):
                need = 5                                   # rebid own suit
            if seat in out and need:
                out[seat][b.suit] = max(out[seat].get(b.suit, 0), need)
            bid_by.setdefault(seat, []).append(b.suit)
        seat = seat.next()
    return out


def _rank(s):
    return {Suit.CLUBS: 0, Suit.DIAMONDS: 1, Suit.HEARTS: 2, Suit.SPADES: 3,
            Suit.NOTRUMP: 4}[s]


def _sample_defenders(board: BoardState, seat: Seat, current_trick: List[Card],
                      declarer: Seat, k: int, known_seats=None):
    """k hidden-hand layouts; when an auction is known, the k most consistent
    with it out of k*_OVERSAMPLE drawn (see _sample_defenders_raw)."""
    auction = list(getattr(board, "auction", []) or [])
    if not _PLAY_AUCTION or len(auction) < 4 or k <= 0:
        return _sample_defenders_raw(board, seat, current_trick, declarer, k,
                                     known_seats)
    raw, hidden = _sample_defenders_raw(board, seat, current_trick, declarer,
                                        k * _OVERSAMPLE, known_seats)
    if len(raw) <= k:
        return raw, hidden
    try:
        from . import bid_sim
        played = {d: [] for d in hidden}
        for t in board.tricks:
            for i, c in enumerate(t.cards):
                p = Seat((t.leader.value + i) % 4)
                if p in played:
                    played[p].append(_c52(c))
        if current_trick:
            lead = Seat((seat.value - len(current_trick)) % 4)
            for i, c in enumerate(current_trick):
                p = Seat((lead.value + i) % 4)
                if p in played:
                    played[p].append(_c52(c))
        cache: dict = {}
        partner = seat.partner()

        def cost(a):
            tot = 0.0
            for d in hidden:
                orig = list(a[d]) + played[d]
                if len(orig) != 13:
                    continue
                m = bid_sim.auction_mismatch(board.dealer, board.vulnerability,
                                             auction, d, orig,
                                             system=bid_sim.play_system(),
                                             cache=cache,
                                             opponent=(d != partner))
                tot += (3.0 if d == partner else 1.0) * m
            return tot
        ranked = sorted(range(len(raw)), key=lambda i: (cost(raw[i]), i))
        return [raw[i] for i in ranked[:k]], hidden
    except Exception:
        return raw[:k], hidden


def _sample_defenders_raw(board: BoardState, seat: Seat, current_trick: List[Card],
                          declarer: Seat, k: int, known_seats=None):
    """k plausible assignments of the unseen cards to the HIDDEN seats, honouring
    remaining counts, shown-out voids, and vacant-places weighting.

    `known_seats` are the seats whose cards we legitimately see; the unseen cards
    are dealt among the rest. Default {declarer, dummy} = declaring (defenders
    hidden). For DEFENCE pass {biq_seat, dummy} so declarer + partner are the
    hidden seats sampled."""
    dummy = declarer.partner()
    decl_side = set(known_seats) if known_seats is not None else {declarer, dummy}
    defenders = [s for s in _ALL if s not in decl_side]
    known = {_c52(c) for s in decl_side for c in board.hands.get(s, Hand()).cards}
    played = {_c52(c) for t in board.tricks for c in t.cards}
    played |= {_c52(c) for c in current_trick}
    unseen = [x for x in range(52) if x not in known and x not in played]

    cur_leader = Seat((seat.value - len(current_trick)) % 4) if current_trick \
        else seat

    def played_by(d):
        n = sum(1 for t in board.tricks
                for i in range(len(t.cards))
                if Seat((t.leader.value + i) % 4) == d)
        n += sum(1 for i in range(len(current_trick))
                 if Seat((cur_leader.value + i) % 4) == d)
        return n
    counts = {d: 13 - played_by(d) for d in defenders}

    shown = {d: set() for d in defenders}
    for t in board.tricks:
        if t.cards:
            led = t.cards[0].suit
            for i, c in enumerate(t.cards):
                p = Seat((t.leader.value + i) % 4)
                if p in defenders and c.suit != led:
                    shown[p].add(led)
    if current_trick:
        led = current_trick[0].suit
        for i, c in enumerate(current_trick):
            p = Seat((cur_leader.value + i) % 4)
            if p in defenders and c.suit != led:
                shown[p].add(led)

    # Reserve the cards the hidden hands' calls promised (still unplayed).
    reserve = {}
    if _PLAY_MINLEN:
        try:
            mins = _min_lengths(board, defenders)
        except Exception:
            mins = {}
        played_suit = {d: {} for d in defenders}
        for t in board.tricks:
            for i, c in enumerate(t.cards):
                p = Seat((t.leader.value + i) % 4)
                if p in played_suit:
                    played_suit[p][c.suit] = played_suit[p].get(c.suit, 0) + 1
        for i, c in enumerate(current_trick):
            p = Seat((cur_leader.value + i) % 4)
            if p in played_suit:
                played_suit[p][c.suit] = played_suit[p].get(c.suit, 0) + 1
        avail = {}
        for x in unseen:
            avail[Suit(x // 13)] = avail.get(Suit(x // 13), 0) + 1
        for d in defenders:
            for su, n in mins.get(d, {}).items():
                r_ = n - played_suit[d].get(su, 0)
                if r_ > 0 and su not in shown[d]:
                    reserve[(d, su)] = r_
        # Infeasible (more promised than unseen, or than a hand holds):
        # drop the reservations rather than sample nothing.
        need_suit = {}
        for (d, su), r_ in reserve.items():
            need_suit[su] = need_suit.get(su, 0) + r_
        need_seat = {}
        for (d, su), r_ in reserve.items():
            need_seat[d] = need_seat.get(d, 0) + r_
        if any(need_suit[su] > avail.get(su, 0) for su in need_suit) or \
                any(need_seat[d] > counts[d] for d in need_seat):
            reserve = {}

    out = []
    for _ in range(k):
        rem = dict(counts)
        assign = {d: [] for d in defenders}
        pool = list(unseen)
        random.shuffle(pool)
        for (d, su), r_ in reserve.items():
            got = [x for x in pool if x // 13 == su.value][:r_]
            for x in got:
                pool.remove(x)
                assign[d].append(x)
                rem[d] -= 1
        ok = True
        for c52 in pool:
            su = Suit(c52 // 13)
            elig = [d for d in defenders
                    if rem[d] > 0 and su not in shown[d]]
            if not elig:
                ok = False
                break
            ch = random.choices(elig, weights=[rem[d] for d in elig])[0]
            assign[ch].append(c52)
            rem[ch] -= 1
        if ok:
            out.append(assign)
    return out, defenders


def _rollout(board: BoardState, seat: Seat, current_trick: List[Card],
             declarer: Seat, trump: Optional[Suit], assign, defenders,
             candidate: Card) -> int:
    """Play the deal to the end with the no-peek policy after `seat` plays
    `candidate`; return the declarer side's total trick count."""
    from . import nopeek
    decl_ns = declarer.is_ns()
    hands = {}
    for s in _ALL:
        if s in defenders:
            hands[s] = Hand(cards=[Card.from_code52(c) for c in assign[s]])
        else:
            hands[s] = Hand(cards=list(board.hands[s].cards))
    rb = BoardState(board_number=1, dealer=declarer,
                    vulnerability=board.vulnerability, hands=hands,
                    auction=list(board.auction), contract=board.contract,
                    tricks=[Trick(cards=list(t.cards), leader=t.leader,
                                  winner=t.winner) for t in board.tricks])
    dt = sum(1 for t in rb.tricks if t.winner and t.winner.is_ns() == decl_ns)

    leader = Seat((seat.value - len(current_trick)) % 4) if current_trick else seat
    trick = list(current_trick) + [candidate]
    rb.hands[seat].cards = [c for c in rb.hands[seat].cards
                            if not (c.suit == candidate.suit
                                    and c.rank == candidate.rank)]
    cur = seat.next()
    while True:
        rb.current_trick = Trick(cards=list(trick), leader=leader)
        while len(trick) < 4:
            card = nopeek.decide(rb, cur, list(trick), search=False)
            rb.hands[cur].cards = [c for c in rb.hands[cur].cards
                                   if not (c.suit == card.suit
                                           and c.rank == card.rank)]
            trick.append(card)
            cur = cur.next()
        w = _winner(trick, leader, trump)
        rb.tricks.append(Trick(cards=list(trick), leader=leader, winner=w))
        rb.current_trick = None
        dt += (w.is_ns() == decl_ns)
        if not any(rb.hands[s].cards for s in _ALL):
            break
        leader = w
        cur = w
        trick = []
    return dt


def best_lead(board: BoardState, seat: Seat, declarer: Seat,
              trump: Optional[Suit], candidates: List[Card],
              samples: int = 16) -> Optional[Card]:
    """Pick the declarer-side card that yields the most declarer tricks averaged
    over sampled defender layouts (policy rollout). `board` must be REDACTED
    (defender hands empty) so we never peek. Returns None if sampling fails."""
    smp, defenders = _sample_defenders(board, seat, [], declarer, samples)
    if not smp:
        return None
    # de-dup candidates that are equivalent (same suit, touching ranks collapse
    # to the lowest) to cut rollout cost — keep distinct suit/rank choices.
    best_c, best_v = None, None
    for cand in candidates:
        total = 0
        for a in smp:
            total += _rollout(board, seat, [], declarer, trump, a, defenders,
                              cand)
        avg = total / len(smp)
        if best_v is None or avg > best_v:
            best_v, best_c = avg, cand
    return best_c
