"""What one seat can know about another seat's hand: the reference's Evaluate,
"ask <asker> about <seat>".

A hand the asker can see (its own; dummy once the opening lead is down) is
evaluated exactly, from its ORIGINAL 13 cards. Any other hand is a belief:
the asker deals the unseen cards many times, keeping only layouts that fit
everything it knows (its own cards, dummy, every card played, and what each
hidden seat's calls and plays showed: auction_inference.infer_constraints +
tighten_from_play), and reports ranges, expectations and honour odds over
those layouts. Nothing the asker cannot see is read.
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .models import BoardState, Card, Hand, Rank, Seat, Suit

SUITS = (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)
SEATS = (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)
_HCP = {Rank.ACE: 4, Rank.KING: 3, Rank.QUEEN: 2, Rank.JACK: 1}


def played_cards(board: BoardState) -> Dict[Seat, List[Card]]:
    """Cards each seat has played so far (finished tricks + the current one)."""
    out: Dict[Seat, List[Card]] = {s: [] for s in SEATS}
    tricks = list(getattr(board, "tricks", None) or [])
    cur = getattr(board, "current_trick", None)
    if cur is not None and cur.cards and cur not in tricks:
        tricks.append(cur)
    for t in tricks:
        for k, c in enumerate(t.cards):
            out[Seat((t.leader.value + k) % 4)].append(c)
    return out


def original_hand(board: BoardState, seat: Seat) -> Hand:
    """The seat's 13 starting cards: what it holds now plus what it played."""
    now = list(board.hands[seat].cards) if seat in board.hands else []
    return Hand(cards=now + played_cards(board)[seat])


def dummy_revealed(board: BoardState) -> bool:
    if board.contract is None:
        return False
    cur = getattr(board, "current_trick", None)
    return bool(getattr(board, "tricks", None)) or bool(cur and cur.cards)


def visible_to(board: BoardState, asker: Seat, seat: Seat) -> bool:
    if asker == seat:
        return True
    return (dummy_revealed(board)
            and seat == board.contract.declarer.partner())


# --- per-hand measures (used for the exact hand and for every layout) -------

def _stopper(cards: List[Card]) -> float:
    """1 sure stopper, 0.5 likely / possible, 0 none (the reference categories
    > 0 / >= 0 & <= 0 / < 0)."""
    n = len(cards)
    ranks = {c.rank for c in cards}
    if Rank.ACE in ranks or (Rank.KING in ranks and n >= 2):
        return 1.0
    if (Rank.QUEEN in ranks and n >= 3) or Rank.KING in ranks \
            or (Rank.QUEEN in ranks and n >= 2):
        return 0.5
    return 0.0


def _measures(cards: List[Card]) -> dict:
    by = {s: [c for c in cards if c.suit == s] for s in SUITS}
    return {
        "hp": sum(_HCP.get(c.rank, 0) for c in cards),
        "len": {s: len(by[s]) for s in SUITS},
        "suit_hp": {s: sum(_HCP.get(c.rank, 0) for c in by[s]) for s in SUITS},
        "honours": {s: {r: any(c.rank == r for c in by[s])
                        for r in (Rank.ACE, Rank.KING, Rank.QUEEN)}
                    for s in SUITS},
        "stop": {s: _stopper(by[s]) for s in SUITS},
        "aces": sum(1 for c in cards if c.rank == Rank.ACE),
        "kings": sum(1 for c in cards if c.rank == Rank.KING),
    }


def length_points(lens) -> int:
    return sum(max(0, n - 4) for n in lens)


def total_points(hp: int, lens: Tuple[int, int, int, int],
                 trump: Optional[Suit]) -> int:
    """HP + length points, plus shortness in side suits with a trump suit
    (void 3, singleton 2, doubleton 1), as the dialog always counted."""
    tp = hp + length_points(lens)
    if trump is not None:
        for s, n in zip(SUITS, lens):
            if s != trump:
                tp += {0: 3, 1: 2, 2: 1}.get(n, 0)
    return tp


# --- the sampler ------------------------------------------------------------

def _constraints(board: BoardState, ns_system, ew_system):
    """{seat: SeatConstraints} from the calls and the play so far, each seat
    read in its own side's system."""
    from . import auction_inference as ai
    out = {}
    for side_sys, seats in ((ns_system, (Seat.NORTH, Seat.SOUTH)),
                            (ew_system, (Seat.EAST, Seat.WEST))):
        try:
            c = ai.infer_constraints(board, side_sys)
            c = ai.tighten_from_play(board, c)
        except Exception:
            c = {}
        for s in seats:
            out[s] = c.get(s)
    return out


def show_outs(board: BoardState) -> Dict[Seat, Dict[Suit, int]]:
    """Hard facts from the play: a seat that discarded on a led suit held
    exactly the cards of that suit it had played before."""
    out: Dict[Seat, Dict[Suit, int]] = {s: {} for s in SEATS}
    count: Dict[Seat, Dict[Suit, int]] = {s: {u: 0 for u in SUITS} for s in SEATS}
    tricks = list(getattr(board, "tricks", None) or [])
    cur = getattr(board, "current_trick", None)
    if cur is not None and cur.cards and cur not in tricks:
        tricks.append(cur)
    for t in tricks:
        led = t.cards[0].suit if t.cards else None
        for k, c in enumerate(t.cards):
            p = Seat((t.leader.value + k) % 4)
            if c.suit != led and led not in out[p]:
                out[p][led] = count[p][led]
            count[p][c.suit] += 1
    return out


def _bounds(cons, seat, outs, slack_hp=2, slack_len=1):
    """Steering bounds for the sampler: what the calls / play showed, loosened
    (biq's real hands stray a little outside hand-written inferences), and
    show-outs exactly."""
    c = cons.get(seat)
    if c is None:
        hp = (0, 37)
        lens = {s: (0, 13) for s in SUITS}
    else:
        hp = (max(0, c.hcp_min - slack_hp), min(37, c.hcp_max + slack_hp))
        lens = {}
        for s in SUITS:
            lo, hi = c.suit_len.get(s.value, (0, 13))
            lens[s] = (max(0, lo - slack_len), min(13, hi + slack_len))
    for s, k in outs.get(seat, {}).items():
        lens[s] = (k, k)
    return hp, lens


def sample_layouts(board: BoardState, asker: Seat, bounds: Dict,
                   n: int = 400, rng: Optional[random.Random] = None,
                   max_steps: int = 30000, cost=None,
                   budget: Optional[float] = None) -> List[Dict[Seat, List[Card]]]:
    """Up to `n` layouts of the cards `asker` cannot see, uniformly among
    those within `bounds` ({seat: ((hp_lo, hp_hi), {Suit: (lo, hi)})}) and,
    when given, with cost(seat, cards) == 0 for every hidden seat: random
    swaps between hidden seats, kept only when the total doesn't grow.
    `budget` caps the seconds spent."""
    import time as _time
    t_end = _time.time() + budget if budget else None
    rng = rng or random.Random(0)
    played = played_cards(board)
    known: Dict[Seat, List[Card]] = {s: list(played[s]) for s in SEATS}
    full = {asker}
    if dummy_revealed(board):
        full.add(board.contract.declarer.partner())
    for s in full:
        known[s] = original_hand(board, s).cards
    seen = {c.code52() for s in SEATS for c in known[s]}
    pool = [Card.from_code52(i) for i in range(52) if i not in seen]
    hidden = [s for s in SEATS if s not in full]
    need = {s: 13 - len(known[s]) for s in hidden}
    if not hidden or sum(need.values()) != len(pool):
        return []

    def viol(seat, cards):
        v = _viol(seat, cards)
        if v == 0 and cost is not None:
            v = cost(seat, cards)
        elif cost is not None:
            v = 10 * v + 10       # hard facts first
        return v

    def _viol(seat, cards):
        (lo, hi), lens = bounds[seat]
        m_hp = sum(_HCP.get(c.rank, 0) for c in cards)
        v = max(0, lo - m_hp) + max(0, m_hp - hi)
        for s in SUITS:
            k = sum(1 for c in cards if c.suit == s)
            a, b = lens[s]
            v += max(0, a - k) + max(0, k - b)
        return v

    cards = list(pool)
    rng.shuffle(cards)
    hands, i = {}, 0
    for s in hidden:
        hands[s] = cards[i:i + need[s]]
        i += need[s]
    cur = {s: viol(s, known[s] + hands[s]) for s in hidden}
    movable = [s for s in hidden if need[s]]
    if len(movable) < 2:
        return ([{s: known[s] + list(hands[s]) for s in hidden}]
                if sum(cur.values()) == 0 else [])
    out = []
    steps = 0
    while len(out) < n and steps < max_steps:
        steps += 1
        if t_end is not None and steps % 50 == 0 and _time.time() > t_end:
            break
        a, b = rng.sample(movable, 2)
        ia, ib = rng.randrange(need[a]), rng.randrange(need[b])
        hands[a][ia], hands[b][ib] = hands[b][ib], hands[a][ia]
        va = viol(a, known[a] + hands[a])
        vb = viol(b, known[b] + hands[b])
        if va + vb <= cur[a] + cur[b]:
            cur[a], cur[b] = va, vb
        else:
            hands[a][ia], hands[b][ib] = hands[b][ib], hands[a][ia]
        if steps % 5 == 0 and sum(cur.values()) == 0:
            out.append({s: known[s] + list(hands[s]) for s in hidden})
    return out


def _bid_consistent(board: BoardState, layouts, systems, keep: int):
    """The layouts in which every hidden seat's hand would make the calls
    that seat made, by biq's own bidder (bid_sim.auction_mismatch; the
    robots ARE biq). Returns (kept, mismatches of the kept)."""
    from . import bid_sim
    if not board.auction or not layouts:
        return layouts[:keep], 0
    cache: dict = {}
    scored = []
    for lay in layouts:
        cost = 0
        for s, cards in lay.items():
            if len(cards) != 13:
                continue
            cost += bid_sim.auction_mismatch(
                board.dealer, board.vulnerability, board.auction, s,
                [c.code52() for c in cards],
                system=systems.get(s), cache=cache)
        scored.append((cost, lay))
    best = min(c for c, _ in scored)
    return [lay for c, lay in scored if c == best][:keep], best


# --- the evaluation ---------------------------------------------------------

def evaluate(board: BoardState, asker: Seat, seat: Seat,
             ns_system=None, ew_system=None, n: int = 300,
             seed: int = 0, budget: float = 4.0) -> dict:
    """`asker`'s view of `seat`'s hand. Keys: exact (bool), hand (exact only),
    hp / aces / kings: (min, exp, max); suits: {Suit: {len: (min, exp, max),
    hp_exp, pA, pK, pQ, stop}}; p_void, p_single; lens_hp: [(hp, lens), ...]
    for recomputing TP per trump; reasons (what the calls / play showed);
    samples, relaxed."""
    cons = _constraints(board, ns_system, ew_system)
    c = cons.get(seat)
    reasons = list(c.reasons) if c is not None else []

    mismatch = 0
    if visible_to(board, asker, seat):
        hands = [original_hand(board, seat).cards]
        exact, relaxed = True, False
    else:
        exact = False
        rng = random.Random(seed)
        outs = show_outs(board)
        systems = {s: (ns_system if s.is_ns() else ew_system) for s in SEATS}
        # Steer by biq's own bidder: a layout counts when every hidden
        # seat's hand would make that seat's calls (the robots ARE biq;
        # hand-written inferences hold the real hand only ~70% of the time).
        from . import bid_sim
        cache: dict = {}

        def mismatch_cost(s, cards):
            if not board.auction:
                return 0
            return bid_sim.auction_mismatch(
                board.dealer, board.vulnerability, board.auction, s,
                [c.code52() for c in cards], system=systems.get(s),
                cache=cache)
        hard = {s: _bounds({}, s, outs) for s in SEATS}
        layouts = sample_layouts(board, asker, hard, n=n, rng=rng,
                                 max_steps=200000, cost=mismatch_cost,
                                 budget=budget)
        relaxed = len(layouts) < max(20, n // 10)
        if relaxed:
            # Not enough agreeing layouts in time: the loosened written
            # inferences, keeping the layouts the bidder likes best.
            bounds = {s: _bounds(cons, s, outs) for s in SEATS}
            more = sample_layouts(board, asker, bounds, n=4 * n, rng=rng,
                                  budget=budget / 2)
            more, mismatch = _bid_consistent(board, more, systems, n)
            layouts = layouts + more if mismatch == 0 else (layouts or more)
        hands = [lay[seat] for lay in layouts if seat in lay]
    if not hands:
        return {"exact": False, "samples": 0, "reasons": reasons,
                "relaxed": relaxed, "mismatch": mismatch}

    ms = [_measures(h) for h in hands]
    N = len(ms)

    def rng3(vals):
        return (min(vals), sum(vals) / N, max(vals))

    out = {
        "exact": exact,
        "hand": Hand(cards=hands[0]) if exact else None,
        "samples": N,
        "relaxed": relaxed,
        "mismatch": mismatch,
        "reasons": reasons,
        "hp": rng3([m["hp"] for m in ms]),
        "aces": rng3([m["aces"] for m in ms]),
        "kings": rng3([m["kings"] for m in ms]),
        "p_void": sum(1 for m in ms if min(m["len"].values()) == 0) / N,
        "p_single": sum(1 for m in ms if 1 in m["len"].values()) / N,
        "lens_hp": [(m["hp"], tuple(m["len"][s] for s in SUITS)) for m in ms],
        "suits": {},
    }
    for s in SUITS:
        lens = [m["len"][s] for m in ms]
        out["suits"][s] = {
            "len": rng3(lens),
            "hp_exp": sum(m["suit_hp"][s] for m in ms) / N,
            "pA": sum(m["honours"][s][Rank.ACE] for m in ms) / N,
            "pK": sum(m["honours"][s][Rank.KING] for m in ms) / N,
            "pQ": sum(m["honours"][s][Rank.QUEEN] for m in ms) / N,
            "stop": sum(m["stop"][s] for m in ms) / N,
        }
    return out
