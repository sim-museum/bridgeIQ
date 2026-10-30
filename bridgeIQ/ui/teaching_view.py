#!/usr/bin/env python3
"""Instrumented (teaching / analysis) cardplay view.

An alternative to the normal table — a four-hand "cross" where each
hand is a 4-row (♠♥♦♣) × 2-column (Known | Other) grid, surrounded by
analysis panels. It is meant to be useful to BOTH a trainee (count your
tricks, where are the entries, what is partner signalling) AND a strong
player (full count, proven card placement, honour location, squeeze /
BLUE readiness) — the Detail selector (Beginner / Intermediate / Expert)
scales how much is shown.

Layout (3×3 grid; corners are analysis panels, the cross sits on the
edges + centre):

    Contract/Tricks |      NORTH       | Plan (winners/losers/trumps)
    ----------------+------------------+-----------------------------
        WEST        |  trick + detail  |            EAST
    ----------------+------------------+-----------------------------
    Count / honours |      SOUTH       | Coaching / signals

What is shown per suit row:
  • KNOWN  — cards we can PROVE are in that hand right now: exact cards
    for a face-up hand (own + dummy + Show-All), and for a hidden hand
    only cards forced there (sole seat not shown out of the suit).
  • OTHER  — derived info: inferred length range (auction ∩ play), void,
    and honour-location hints.

Highlights on Known cards: the current master (boss) of a suit, top-run
sure winners, and the trump suit. Everything degrades gracefully when
state is missing, so the view is safe to refresh at any point in a hand.
"""

import json
import re
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QComboBox, QSizePolicy, QStackedWidget, QPushButton,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from backend.models import Suit, Rank, Seat, Card, Contract, BoardState

try:
    from backend.auction_inference import infer_constraints
except Exception:                                    # pragma: no cover
    infer_constraints = None

try:
    from backend import signal_read as _signal_read
    from backend import signals as _signals
except Exception:                                    # pragma: no cover
    _signal_read = None
    _signals = None

from ui.styles import get_suit_color


SUIT_ROWS: List[Suit] = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]
SUIT_NAME = {Suit.SPADES: "spades", Suit.HEARTS: "hearts",
             Suit.DIAMONDS: "diamonds", Suit.CLUBS: "clubs"}

# Suit colours tuned for THIS view's dark background — the app's normal
# palette (black spades, dark-blue diamonds) is meant for white card faces
# and is unreadable here. Four-colour, all high-contrast on #0f2740.
TV_SUIT_COLOR = {
    Suit.SPADES:   "#e8eef6",   # near-white (black was invisible on blue)
    Suit.HEARTS:   "#ff6b6b",
    Suit.DIAMONDS: "#5aa6ff",   # blue, to match the (4-colour) card diamonds
                                # — a bright blue stays readable on the dark felt
    Suit.CLUBS:    "#57d07b",
}


def _suit_color(suit: Suit) -> str:
    return TV_SUIT_COLOR.get(suit, "#e8eef6")
HONOURS = [Rank.ACE, Rank.KING, Rank.QUEEN, Rank.JACK]
ALL_CARDS: List[Card] = [Card(s, r) for s in SUIT_ROWS for r in Rank]


def _cid(card: Card) -> int:
    """Hashable 0-51 id for a card (Card itself is an unhashable dataclass)."""
    return int(card.suit) * 13 + int(card.rank)

# Detail tiers.
BEGINNER, INTERMEDIATE, EXPERT = "Beginner", "Intermediate", "Expert"


# ---------------------------------------------------------------------------
# Per-pair carding agreement. N/S and E/W can differ; every signal read is
# decoded through the SIGNALLER's own config (ACBL Encyclopedia: one polarity
# flag flips an attitude/count read, and suit-preference is its own agreement).
# ---------------------------------------------------------------------------

@dataclass
class CardingConfig:
    attitude: str = "standard"   # standard (high encourages) | udca (low encourages)
    count: str = "standard"      # standard (hi-lo = even) | reverse (lo-hi = even)
    discards: str = "attitude"   # attitude (about the suit thrown) | lavinthal (suit-pref)
    trump_echo: str = "standard" # standard (hi-lo in trumps = odd / 3) | off
    smith: str = "off"           # off | standard (hi = liked the lead) | reverse (NT only)
    signals: bool = True         # False = the pair gives no signals (cards say nothing)


# Named presets surfaced in the view's per-pair carding selectors.
CARDING_PRESETS = {
    "Standard":          CardingConfig("standard", "standard", "attitude"),
    "Upside-down":       CardingConfig("udca",     "reverse",  "attitude"),
    "Std + Lavinthal":   CardingConfig("standard", "standard", "lavinthal"),
    "UDCA + Lavinthal":  CardingConfig("udca",     "reverse",  "lavinthal"),
}


# The preset that follows what biq's own defenders actually play
# (backend.signals): polarity from Preferences → signalling convention,
# Lavinthal discards, plain count in trumps (no trump echo), no Smith echo, and
# no signals at all when "Play defensive signals" is off. It is the default for
# both pairs, because biq expects its human partner to card the same way.
AS_BIQ_PLAYS = "As biq plays"


def _app_signalling_enabled() -> bool:
    try:
        from backend.config import get_config_manager
        return bool(getattr(get_config_manager().config.preferences,
                            "signalling_enabled", True))
    except Exception:
        return _signals.is_enabled() if _signals is not None else True


def biq_carding() -> CardingConfig:
    """The carding agreement biq's defenders play, as a CardingConfig."""
    udca = _app_default_udca()
    return CardingConfig(attitude="udca" if udca else "standard",
                         count="reverse" if udca else "standard",
                         discards="lavinthal", trump_echo="off", smith="off",
                         signals=_app_signalling_enabled())


def carding_preset(name: str) -> CardingConfig:
    """Resolve a carding selector name (AS_BIQ_PLAYS or a CARDING_PRESETS key)."""
    if name in CARDING_PRESETS:
        return CARDING_PRESETS[name]
    return biq_carding()


def _pref_value(name: str) -> str:
    """Preferences store "" for AS_BIQ_PLAYS (the Preferences dialog's
    first choice), so the two selectors stay one setting."""
    return "" if name == AS_BIQ_PLAYS else name


def carding_names() -> List[str]:
    return [AS_BIQ_PLAYS] + list(CARDING_PRESETS)


def _app_default_udca() -> bool:
    """The app's configured signalling convention (Preferences →
    signalling_convention), falling back to the BIQ_SIGNAL_UDCA switch."""
    try:
        from backend.config import get_config_manager
        conv = getattr(get_config_manager().config.preferences,
                       "signalling_convention", "") or ""
        if conv:
            return conv.lower() == "udca"
    except Exception:
        pass
    return _signals.is_udca() if _signals is not None else False


def _default_carding() -> CardingConfig:
    """Default agreement — honours the app's Preferences (then the
    BIQ_SIGNAL_UDCA switch) so the out-of-the-box reading matches the user's
    configured convention."""
    udca = _app_default_udca()
    return CardingConfig(attitude="udca" if udca else "standard",
                         count="reverse" if udca else "standard",
                         discards="attitude")


def _cfg_prefs():
    try:
        from backend.config import get_config_manager
        return get_config_manager().config.preferences
    except Exception:
        return None


def _default_preset_name(side: str = "ns") -> str:
    """Default carding preset for a side ('ns'/'ew'), from Preferences →
    Carding when set, else derived from the signalling convention."""
    p = _cfg_prefs()
    if p is not None:
        v = (getattr(p, "carding_ns", "") if side == "ns"
             else getattr(p, "carding_ew", "")) or ""
        if v in CARDING_PRESETS or v == AS_BIQ_PLAYS:
            return v
    return AS_BIQ_PLAYS


def _default_smith() -> str:
    p = _cfg_prefs()
    v = getattr(p, "smith_echo", "Off") if p is not None else "Off"
    return v if v in ("Off", "Standard", "Reverse") else "Off"


def _tv_config_path() -> Path:
    """Where the instrumented view persists its selectors (alongside the app's
    config when available, else the home dir)."""
    try:
        from backend.config import get_config_manager
        d = getattr(get_config_manager(), "config_dir", None)
        if d:
            return Path(d) / "teaching_view.json"
    except Exception:
        pass
    return Path.home() / ".biq_teaching_view.json"


# ---------------------------------------------------------------------------
# Pure inference helpers (no Qt) — kept module-level so they're testable.
# ---------------------------------------------------------------------------

def collect_play(board: BoardState):
    """Walk completed + current trick.

    Returns (played, voids, all_played):
      played[seat]      -> set(Card) the seat has played
      voids[seat]       -> set(Suit) the seat is known void in (showed out)
      all_played        -> set(Card) played by anyone
    """
    played: Dict[Seat, Set[int]] = {s: set() for s in Seat}
    voids: Dict[Seat, Set[Suit]] = {s: set() for s in Seat}
    all_played: Set[int] = set()      # set of card-ids

    def process(cards, leader):
        if not cards or leader is None:
            return
        try:
            base = int(leader)
        except (TypeError, ValueError):
            return
        led = cards[0].suit
        for i, c in enumerate(cards):
            seat = Seat((base + i) % 4)
            played[seat].add(_cid(c))
            all_played.add(_cid(c))
            if c.suit != led:
                voids[seat].add(led)

    for t in (getattr(board, "tricks", None) or []):
        process(getattr(t, "cards", None) or [], getattr(t, "leader", None))
    ct = getattr(board, "current_trick", None)
    if ct is not None:
        process(getattr(ct, "cards", None) or [], getattr(ct, "leader", None))
    return played, voids, all_played


def remaining_ranks(all_played: Set[Card]) -> Dict[Suit, Set[Rank]]:
    """Ranks of each suit still in play (not yet played)."""
    rem = {su: set(Rank) for su in SUIT_ROWS}
    for cid in all_played:                       # cid = suit*13 + rank
        rem[Suit(cid // 13)].discard(Rank(cid % 13))
    return rem


def boss_rank(rem: Dict[Suit, Set[Rank]], suit: Suit) -> Optional[Rank]:
    """The highest card of `suit` still in play (Rank value: lower = higher)."""
    rs = rem.get(suit)
    return min(rs) if rs else None


def known_layout(board: BoardState, visible: Set[Seat]):
    """Determine, per seat per suit, which cards are PROVEN present now.

    Returns a dict:
      known[seat][suit] -> List[Rank] (sorted high→low)
    plus voids, the per-seat remaining-card count, and unseen-by-suit.
    """
    played, voids, all_played = collect_play(board)
    remaining_count = {s: 13 - len(played[s]) for s in Seat}

    visible_cards: Dict[Seat, List[Card]] = {}
    seen_or_visible: Set[int] = set(all_played)        # card-ids
    for s in visible:
        h = board.hands.get(s) if board.hands else None
        cards = list(getattr(h, "cards", []) or []) if h else []
        visible_cards[s] = cards
        seen_or_visible |= {_cid(c) for c in cards}

    hidden = [s for s in Seat if s not in visible]
    unseen = [c for c in ALL_CARDS if _cid(c) not in seen_or_visible]

    # Iterative forced placement: a card is PROVEN in a hidden seat when only
    # one hidden seat can hold it — that seat is neither void in the suit nor
    # already FULL (its proven cards have reached its remaining-card count).
    # Re-loop until nothing changes, so count-completion propagates: e.g. once
    # West's other suits fill its hand, a remaining diamond is forced to East
    # even though West never "showed out" of diamonds.
    cap = {s: remaining_count[s] for s in hidden}
    proven: Dict[Seat, List[Card]] = {s: [] for s in hidden}
    remaining = list(unseen)
    changed = True
    while changed and remaining:
        changed = False
        still = []
        for c in remaining:
            cands = [s for s in hidden
                     if c.suit not in voids[s] and len(proven[s]) < cap[s]]
            if len(cands) == 1:
                proven[cands[0]].append(c)
                changed = True
            else:
                still.append(c)
        remaining = still

    known: Dict[Seat, Dict[Suit, List[Rank]]] = {
        s: {su: [] for su in SUIT_ROWS} for s in Seat}
    # inferred[seat][suit] = ranks PLACED by deduction in a HIDDEN hand (the
    # card must be there because every other hidden hand is void / full). These
    # are "known" but not "seen" — the grid outlines them differently.
    inferred: Dict[Seat, Dict[Suit, Set[Rank]]] = {
        s: {su: set() for su in SUIT_ROWS} for s in Seat}
    for s in visible:
        for c in visible_cards[s]:
            known[s][c.suit].append(c.rank)
    for s in hidden:
        for c in proven[s]:
            known[s][c.suit].append(c.rank)
            inferred[s][c.suit].add(c.rank)
    for s in Seat:
        for su in SUIT_ROWS:
            known[s][su].sort()   # ACE=0 first → high to low

    unseen_by_suit: Dict[Suit, int] = {su: 0 for su in SUIT_ROWS}
    for c in unseen:
        unseen_by_suit[c.suit] += 1

    return {
        "known": known, "inferred": inferred, "voids": voids,
        "remaining_count": remaining_count,
        "unseen_by_suit": unseen_by_suit, "all_played": all_played,
        "rem_ranks": remaining_ranks(all_played),
    }


def suit_length_text(seat: Seat, suit: Suit, visible: Set[Seat], layout,
                     constraints) -> str:
    """Short OTHER-cell length token for a suit row."""
    known = layout["known"][seat][suit]
    if seat in visible:
        return "void" if not known else f"{len(known)}"
    if suit in layout["voids"][seat]:
        return "void"
    lo, hi = 0, 13
    c = constraints.get(seat) if constraints else None
    if c is not None:
        lo, hi = c.suit_len.get(suit.value, (0, 13))
    # Max = the cards of this suit PROVEN here, plus the unseen cards of the
    # suit not yet pinned to any hidden seat (and capped by this hand's size).
    # When every unseen card of the suit is placed, uncertain == 0 → exact.
    inferred_total = sum(len(layout["known"][h][suit])
                         for h in Seat if h not in visible)
    uncertain = max(0, layout["unseen_by_suit"].get(suit, 0) - inferred_total)
    lo = max(lo, len(known))
    hi = min(hi, len(known) + uncertain, layout["remaining_count"][seat])
    if lo > hi:
        lo = hi
    if lo == hi:
        # A pinned length of exactly 0 is a void — always say so, even when
        # it was inferred (every card placed elsewhere) rather than shown out.
        return "void" if lo == 0 else f"{lo}"
    if lo <= 0 and hi >= 13:
        return "?"
    if hi >= 7 and lo >= 5:
        return f"{lo}+"
    return f"{lo}-{hi}"


def original_shape(board: BoardState, seat: Seat, visible: Set[Seat],
                   layout, constraints) -> Dict[Suit, int]:
    """ORIGINAL (13-card) suit lengths known so far for `seat`.

    Returns {suit: int} for every suit whose ORIGINAL length is now PINNED — a
    face-up hand pins all four at once; a hidden hand pins a suit when it shows
    out (void) or the count forces the length. Suits not yet determined are
    omitted. original = (cards already played in the suit) + (current length,
    when exactly known). No peeking: hidden hands flow only through `layout` /
    `suit_length_text`, never `board.hands`.
    """
    played, _v, _a = collect_play(board)
    pn = {su: 0 for su in SUIT_ROWS}
    for cid in played.get(seat, ()):              # cid = suit*13 + rank
        su = Suit(cid // 13)
        if su in pn:
            pn[su] += 1
    out: Dict[Suit, int] = {}
    for su in SUIT_ROWS:
        txt = suit_length_text(seat, su, visible, layout, constraints)
        cur = 0 if txt == "void" else (int(txt) if txt.isdigit() else None)
        if cur is not None:
            out[su] = cur + pn[su]
    return out


def shape_html(shape: Dict[Suit, int]) -> str:
    """Compact coloured shape line ♠a ♥b ♦c ♣d ('?' where not yet known).
    Empty string when nothing is known yet, so the line stays hidden."""
    if not shape:
        return ""
    parts = []
    for su in SUIT_ROWS:
        v = shape.get(su)
        parts.append(
            f"<span style='color:{_suit_color(su)}'>{su.symbol()}</span>"
            f"{v if v is not None else '?'}")
    return ("<span style='color:#7f93a6'>shape</span>&nbsp;&nbsp;"
            + "&nbsp;&nbsp;".join(parts))


def winners_in_hand(held: List[Rank], rem_of_suit: Set[Rank]) -> Set[Rank]:
    """Top-run sure winners: walk remaining ranks high→low while this
    hand holds them; stop at the first gap (a rank held elsewhere)."""
    hs = set(held)
    wins: Set[Rank] = set()
    for r in sorted(rem_of_suit):     # high → low
        if r in hs:
            wins.add(r)
        else:
            break
    return wins


def top_entry_rank(winners: Set[Rank]) -> Optional[Rank]:
    """The highest sure winner — the card that gains the lead in this hand,
    i.e. an entry to it. None if the hand has no sure winner in the suit."""
    return min(winners) if winners else None     # Rank: lower value = higher card


def stopper_rank(held: List[Rank]) -> Optional[Rank]:
    """The honour that stops a suit, by the length-guard rule: A, or Kx, or
    Qxx, or Jxxx. Returns the guarding card, or None. (An NT concept.)"""
    hs = set(held)
    n = len(held)
    if Rank.ACE in hs:
        return Rank.ACE
    if Rank.KING in hs and n >= 2:
        return Rank.KING
    if Rank.QUEEN in hs and n >= 3:
        return Rank.QUEEN
    if Rank.JACK in hs and n >= 4:
        return Rank.JACK
    return None


def side_top_tricks(board: BoardState, side_seats: Tuple[Seat, Seat],
                    visible: Set[Seat], layout) -> Optional[int]:
    """Count sure top tricks for a side (e.g. declarer+dummy) — only when
    both that side's hands are face-up. Walks each suit's remaining ranks
    from the top while the side holds them, capped by the longer holding."""
    if not all(s in visible for s in side_seats):
        return None
    rem = layout["rem_ranks"]
    total = 0
    for su in SUIT_ROWS:
        held = set(layout["known"][side_seats[0]][su]) | \
            set(layout["known"][side_seats[1]][su])
        length_cap = max(len(layout["known"][side_seats[0]][su]),
                         len(layout["known"][side_seats[1]][su]))
        run = 0
        for r in sorted(rem[su]):     # high → low
            if r in held and run < length_cap:
                run += 1
            else:
                break
        total += run
    return total


def losing_trick_count(hand_known: Dict[Suit, List[Rank]]) -> Optional[int]:
    """Standard LTC for one (visible) hand: in each suit's top 3 cards,
    a card that isn't A/K/Q is a loser."""
    losers = 0
    for su in SUIT_ROWS:
        ranks = hand_known[su][:3]
        winners = {Rank.ACE, Rank.KING, Rank.QUEEN}
        losers += sum(1 for r in ranks if r not in winners)
    return losers


def suit_loser_ranks(held: List[Rank], rem_of_suit: Set[Rank]) -> Set[Rank]:
    """Which of THIS hand's cards are losers, LTC-style. Among the top live
    ranks of the suit (capped at the hand's length, ≤3) count the ones this hand
    does NOT hold; that many of its LOWEST cards are the losers. Counted against
    the still-LIVE ranks, so once an honour has been cashed the cards below it
    stop being losers (they've been promoted). Mirrors the LTC number already
    shown in the Plan, but marks the specific cards."""
    if not held:
        return set()
    held_sorted = sorted(held)                       # high→low (ACE=0)
    live_top = sorted(rem_of_suit)                    # high→low
    n = min(3, len(held_sorted))
    missing = sum(1 for r in live_top[:n] if r not in held_sorted)
    if missing <= 0:
        return set()
    return set(held_sorted[len(held_sorted) - missing:])   # lowest `missing`


def finesse_hints(declarer, dummy, visible, layout):
    """8-ever-9-never style hints for missing honours in the declaring side's
    combined suits. Needs both hands face-up. Returns short strings."""
    if declarer is None or dummy is None \
            or declarer not in visible or dummy not in visible:
        return []
    out = []
    for su in SUIT_ROWS:
        hs = set(layout["known"][declarer][su]) | set(layout["known"][dummy][su])
        L = len(hs)
        if Rank.ACE in hs and Rank.KING in hs and Rank.QUEEN not in hs and L >= 7:
            if L >= 9:
                out.append(f"{su.symbol()}: {L} cards missing Q — play for the "
                           f"drop (9-never).")
            elif L == 8:
                out.append(f"{su.symbol()}: 8 cards missing Q — finesse "
                           f"(~50%, 8-ever).")
        elif Rank.ACE in hs and Rank.QUEEN in hs and Rank.KING not in hs and L >= 3:
            out.append(f"{su.symbol()}: missing K — finesse the Q (~50%).")
        elif Rank.KING in hs and Rank.JACK in hs and Rank.ACE not in hs \
                and Rank.QUEEN not in hs and L >= 3:
            out.append(f"{su.symbol()}: missing A & Q — finesse, ~50% each.")
    return out


def _declaring_losers(declarer, dummy, visible, layout) -> Optional[int]:
    """Approximate the declaring side's remaining losers (both hands face-up):
    per suit, the losers of the LONGER holding (the short hand's extra low cards
    are ruffed / pitched). Used to spot the rectified-count moment for a
    squeeze. Heuristic — a static LTC-style count, not a DDS play-out."""
    if None in (declarer, dummy) or declarer not in visible or dummy not in visible:
        return None
    rem = layout["rem_ranks"]
    total = 0
    for su in SUIT_ROWS:
        d = layout["known"][declarer][su]
        m = layout["known"][dummy][su]
        longer = d if len(d) >= len(m) else m
        total += len(suit_loser_ranks(longer, rem[su]))
    return total


def squeeze_threats(board, declarer, dummy, visible, layout, trump=None):
    """Threat (menace) suits = where the declaring side holds the card just
    below the master that an opponent guards; a defender guarding ≥2 threats is
    'busy' (a squeeze candidate). Needs both declaring hands face-up. Returns
    {threats: {suit: guard_seat}, busy: [seat], type, rectified, losers} or None.

    `type` classifies the position (Love's families): simple/positional (one
    busy defender), double (both defenders busy), trump (trumps still out with a
    menace), criss-cross (two one-card menaces split across the two declaring
    hands). `rectified` is True when the side is down to its last loser — the
    squeeze operates now; otherwise it's the count still to be rectified."""
    if declarer is None or dummy is None \
            or declarer not in visible or dummy not in visible:
        return None
    rem = layout["rem_ranks"]
    defenders = [s for s in Seat if s.is_ns() != declarer.is_ns()]
    threats = {}
    menace_hand = {}            # suit -> 'declarer' / 'dummy' (where our card is)
    for su in SUIT_ROWS:
        d = set(layout["known"][declarer][su])
        m = set(layout["known"][dummy][su])
        ours = d | m
        if not ours:
            continue
        boss = boss_rank(rem, su)
        if boss is None or boss in ours:
            continue                         # no master out, or we hold it (winner)
        # We hold a card; the boss (master) is the guard — who holds it?
        holder = _holder_of(layout, su, boss, visible)
        if holder in defenders:
            threats[su] = holder
            menace_hand[su] = "declarer" if (d and not m) else (
                "dummy" if (m and not d) else "both")
    from collections import Counter
    cnt = Counter(threats.values())
    busy = [d for d, c in cnt.items() if c >= 2]

    losers = _declaring_losers(declarer, dummy, visible, layout)
    rectified = (losers == 1) if losers is not None else None

    stype = None
    if threats:
        if trump is not None and rem.get(trump):
            stype = "trump"
        elif len(busy) >= 2:
            stype = "double"
        elif len(threats) >= 2 and len(busy) == 1:
            # criss-cross when the two menaces sit in DIFFERENT declaring hands
            # (each entered from the opposite hand); else a plain simple squeeze.
            hands = {menace_hand[s] for s in threats if menace_hand[s] != "both"}
            stype = "criss-cross" if hands == {"declarer", "dummy"} else "simple"
        else:
            stype = "simple/positional"
    return {"threats": threats, "busy": busy, "type": stype,
            "rectified": rectified, "losers": losers}


_HCP_VAL = {Rank.ACE: 4, Rank.KING: 3, Rank.QUEEN: 2, Rank.JACK: 1}


def signal_likelihood(board, declarer, trump, carding_for, hidden):
    """Weight function for a sampled world from the defenders' SIGNALS, read
    under each signaller's own carding agreement (`carding_for`).

    For each hidden defender, every free follow signal (signal_read.read) and
    every Lavinthal discard on a completed trick is replayed against the
    sampled hand: the hand the defender held at that trick is the sample's
    cards plus what it played from then on. A world whose holding would have
    produced the card actually played (same high/low choice under the
    convention) keeps weight 1; one that would not gets SIGNAL_MISS_WEIGHT,
    since partners sometimes false-card or misjudge. A holding with no free
    choice says nothing. Returns None when there is nothing to read, else
    f(hand: {seat: [Card]}) -> weight."""
    if declarer is None or _signal_read is None or _signals is None:
        return None
    if carding_for is None:
        carding_for = lambda s: _default_carding()
    defenders = [s for s in hidden if s.is_ns() != declarer.is_ns()]
    tricks = getattr(board, "tricks", None) or []
    checks = []          # (seat, kind, payload)
    for d in defenders:
        cfg = carding_for(d)
        if not cfg.signals:
            continue
        smith_used = set()
        if cfg.smith != "off":
            try:
                _, smith_used = smith_reads(board, declarer, trump,
                                            carding_for, set())
            except Exception:
                smith_used = set()
        try:
            recs = _signal_read.read(board, d.partner(), declarer, trump)
        except Exception:
            recs = []
        for rec in recs:
            if (rec["trick"], rec["card52"]) in smith_used:
                continue
            is_trump = trump is not None and rec["suit"] == int(trump)
            if (is_trump and cfg.trump_echo != "off"
                    and not rec["attitude"]):
                continue     # a trump echo reads odd-high: not the emitter's rule
            checks.append((d, "follow", (rec, cfg)))
        # Lavinthal discards (trump contracts: two candidate suits).
        if cfg.discards == "lavinthal" and trump is not None:
            for j, t in enumerate(tricks):
                cards = getattr(t, "cards", None) or []
                if len(cards) < 4 or t.leader is None:
                    continue
                p = (int(d) - int(t.leader)) % 4
                c = cards[p]
                if c.suit == cards[0].suit or c.suit == trump:
                    continue
                cands = [x for x in SUIT_ROWS if x != c.suit and x != trump]
                if len(cands) != 2:
                    continue
                live = _live_ranks_of_suit(board, c.suit, j, cards[:p])
                checks.append((d, "lavinthal",
                               (j, cands, _is_high(c, live))))
    if not checks:
        return None

    played_from = {}     # (seat, trick) -> c52 ids the seat played from then on
    for d in defenders:
        for j in range(len(tricks)):
            ids = set()
            for t in tricks[j:]:
                cards = getattr(t, "cards", None) or []
                if t.leader is None or len(cards) < 4:
                    continue
                ids.add(_cid(cards[(int(d) - int(t.leader)) % 4]))
            ct = getattr(board, "current_trick", None)
            if ct is not None and ct.leader is not None and ct.cards:
                p = (int(d) - int(ct.leader)) % 4
                if p < len(ct.cards):
                    ids.add(_cid(ct.cards[p]))
            played_from[(d, j)] = ids

    def weight(hand):
        w = 1.0
        for seat, kind, pay in checks:
            held = {_cid(x) for x in hand.get(seat, [])}
            if kind == "follow":
                rec, cfg = pay
                at = held | played_from.get((seat, rec["trick"]), set())
                prev = _signals.is_udca()
                try:
                    _signals.set_convention(cfg.attitude == "udca")
                    exp = _signal_read._convention_card(rec, at)
                finally:
                    _signals.set_convention(prev)
                if exp is None or exp == rec["card52"]:
                    continue
                suit = rec["suit"]
                in_suit = [x for x in at if x // 13 == suit]
                spots = ([x for x in in_suit if x % 13 > _HONOUR_VAL + 1]
                         or in_suit)                 # as signal_read does
                low = max(spots, key=lambda x: x % 13) if spots else None
                # Same direction (both "high" = not the lowest spot) agrees.
                if exp != low and rec["card52"] != low:
                    continue
                w *= SIGNAL_MISS_WEIGHT
            else:
                j, cands, high = pay
                at = held | played_from.get((seat, j), set())
                def likes(su):
                    cs = [Card(su, Rank(x % 13)) for x in at
                          if x // 13 == int(su)]
                    return _signals._like_suit(cs, trump)
                hi_like, lo_like = likes(cands[0]), likes(cands[1])
                emit_high = hi_like and not lo_like
                if emit_high != high:
                    w *= SIGNAL_MISS_WEIGHT
        return w
    return weight


# Weight of a sampled world that contradicts one signal (false-card / misjudged).
SIGNAL_MISS_WEIGHT = 0.2


def play_weight(board, hidden, signal_weight=None):
    """World weight from the hidden hands' plays (PLAYBELIEF-1): the signal weight (if any)
    times restricted choice (an honour followed from n equivalents explains the card 1/n)."""
    from backend import play_belief
    plays = play_belief.plays_so_far(board)

    def w(hand):
        x = signal_weight(hand) if signal_weight is not None else 1.0
        for s in hidden:
            x *= play_belief.restricted_choice(plays, s, {_cid(c) for c in hand[s]})[0]
        return x
    return w


def bayes_distribution(board, visible, layout, constraints,
                       samples=120, max_attempts=4000, rng=None,
                       signal_weight=None):
    """Monte-Carlo posterior over the hidden hands — a real card-by-card
    distribution, not just vacant-space counting.

    Sample full deals of the unseen cards to the hidden seats consistent with
    ALL of: each seat's exact remaining count, the proven/forced cards, the
    shown voids, and each seat's auction constraints (HCP range + per-suit
    length range). Every accepted deal is one equally-likely world; aggregating
    gives per-(seat,suit) length probabilities and per-unseen-honour location
    probabilities.

    Returns {"samples": k, "length": {seat:{suit:{len:p}}},
             "honour": {(suit,rank):{seat:p}}} or None when too few valid worlds
    were found (the caller then falls back to the vacant-space estimate).
    `rng` may be a seeded random.Random for reproducible tests.
    `signal_weight` (from signal_likelihood) weights each world by how well
    it explains the defenders' signals under their carding agreement."""
    from collections import Counter
    rng = rng or random.Random()
    hidden = [s for s in Seat if s not in visible]
    if not hidden:
        return None
    _, voids, all_played = collect_play(board)
    seen = set(all_played)
    for s in visible:
        h = board.hands.get(s) if board.hands else None
        seen |= {_cid(c) for c in (getattr(h, "cards", []) or [])}
    inferred = layout.get("inferred", {})
    fixed = {s: [] for s in hidden}
    fixed_ids = set()
    for s in hidden:
        for su in SUIT_ROWS:
            for r in inferred.get(s, {}).get(su, set()):
                c = Card(su, r)
                fixed[s].append(c)
                fixed_ids.add(_cid(c))
    need = {s: layout["remaining_count"][s] - len(fixed[s]) for s in hidden}
    if any(n < 0 for n in need.values()):
        return None
    free = [c for c in ALL_CARDS
            if _cid(c) not in seen and _cid(c) not in fixed_ids]
    if sum(need.values()) != len(free):
        return None                       # bookkeeping mismatch — bail
    unseen_honours = [Card(su, r) for su in SUIT_ROWS for r in HONOURS
                      if _cid(Card(su, r)) not in seen
                      and _cid(Card(su, r)) not in fixed_ids]

    def cons(s):
        return constraints.get(s) if constraints else None

    def suit_max(s, su):
        c = cons(s)
        return c.suit_len.get(su.value, (0, 13))[1] if c is not None else 13

    length_acc = {s: {su: Counter() for su in SUIT_ROWS} for s in hidden}
    honour_acc = {(c.suit, c.rank): Counter() for c in unseen_honours}
    card_acc = {_cid(c): Counter() for c in free}      # PLAYBELIEF-1: every unseen card
    valid = attempts = 0
    total_w = 0.0
    while valid < samples and attempts < max_attempts:
        attempts += 1
        hand = {s: list(fixed[s]) for s in hidden}
        per_suit = {s: Counter(x.suit for x in fixed[s]) for s in hidden}
        cap = dict(need)
        pool = free[:]
        rng.shuffle(pool)
        ok = True
        for c in pool:
            opts = [s for s in hidden
                    if cap[s] > 0 and c.suit not in voids[s]
                    and per_suit[s][c.suit] < suit_max(s, c.suit)]
            if not opts:
                ok = False
                break
            s = rng.choice(opts)
            hand[s].append(c)
            per_suit[s][c.suit] += 1
            cap[s] -= 1
        if not ok or any(cap[s] != 0 for s in hidden):
            continue
        good = True
        for s in hidden:
            c = cons(s)
            if c is None:
                continue
            hcp = sum(_HCP_VAL.get(x.rank, 0) for x in hand[s])
            if not (c.hcp_min <= hcp <= c.hcp_max):
                good = False
                break
            for su in SUIT_ROWS:
                lo, hi = c.suit_len.get(su.value, (0, 13))
                if not (lo <= per_suit[s][su] <= hi):
                    good = False
                    break
            if not good:
                break
        if not good:
            continue
        valid += 1
        w = signal_weight(hand) if signal_weight is not None else 1.0
        total_w += w
        for s in hidden:
            for su in SUIT_ROWS:
                length_acc[s][su][per_suit[s][su]] += w
            ids = {_cid(x) for x in hand[s]}
            for hc in unseen_honours:
                if _cid(hc) in ids:
                    honour_acc[(hc.suit, hc.rank)][s] += w
            for i in ids:
                if i in card_acc:
                    card_acc[i][s] += w
    if valid < max(8, samples // 5) or total_w <= 0:
        return None
    length = {s: {su: {ln: round(c / total_w, 3)
                       for ln, c in length_acc[s][su].items()}
                  for su in SUIT_ROWS} for s in hidden}
    honour = {k: {s: round(c / total_w, 3) for s, c in v.items()}
              for k, v in honour_acc.items() if v}
    card = {i: {s: round(c / total_w, 3) for s, c in v.items()} for i, v in card_acc.items()}
    for s in hidden:                                   # cards placed by deduction
        for x in fixed[s]:
            card[_cid(x)] = {s: 1.0}
    exp_len = {s: {su: round(sum(ln * p for ln, p in length[s][su].items()), 2)
                   for su in SUIT_ROWS} for s in hidden}
    return {"samples": valid, "length": length, "honour": honour, "card": card,
            "exp_length": exp_len, "signals": signal_weight is not None}


def honour_placement(board: BoardState, visible: Set[Seat], layout,
                     posterior=None):
    """For each unseen honour, where it can be. Returns list of
    (suit, rank, label) where label is a proven seat or a side. When a
    Bayesian `posterior` (from bayes_distribution) is supplied, the
    probabilistic labels use its sampled card-by-card probabilities instead of
    the cruder vacant-space ratio."""
    out = []
    played, voids, all_played = collect_play(board)
    seen_or_visible = set(all_played)            # card-ids
    for s in visible:
        h = board.hands.get(s) if board.hands else None
        seen_or_visible |= {_cid(c) for c in (getattr(h, "cards", []) or [])}
    hidden = [s for s in Seat if s not in visible]
    cap = {s: layout["remaining_count"][s] for s in hidden}
    # Vacant spaces: unknown cards in each hidden hand (total held minus already
    # placed). P(an unseen card sits in a hand) ∝ its vacant spaces.
    vacant = {s: max(0, layout["remaining_count"][s]
                     - sum(len(layout["known"][s][x]) for x in SUIT_ROWS))
              for s in hidden}
    for su in SUIT_ROWS:
        for r in HONOURS:
            if _cid(Card(su, r)) in seen_or_visible:
                continue
            cands = [s for s in hidden if su not in voids[s] and cap[s] > 0]
            if not cands:
                continue
            if len(cands) == 1:
                label = f"{cands[0].to_char()} (proven)"
            else:
                post = (posterior or {}).get("honour", {}).get((su, r))
                if post:
                    # Bayesian card-by-card probabilities (preferred).
                    pcts = sorted(((s, round(100 * post.get(s, 0)))
                                   for s in cands), key=lambda x: -x[1])
                else:
                    # Fall back to the vacant-space ratio.
                    tot = sum(vacant[s] for s in cands) or 1
                    pcts = sorted(((s, round(100 * vacant[s] / tot))
                                   for s in cands), key=lambda x: -x[1])
                if len(cands) == 2 and cands[0].is_ns() == cands[1].is_ns() \
                        and pcts[0][1] == pcts[1][1]:
                    label = "N/S" if cands[0].is_ns() else "E/W"
                else:
                    label = " ".join(f"{s.to_char()} {p}%" for s, p in pcts)
            out.append((su, r, label))
    return out


# ---------------------------------------------------------------------------
# Defensive signal reading (attitude / count) on PLAYED cards.
#
# Uses backend.signal_read to pick out which completed-trick plays are genuine
# free signals (followed suit, didn't win, played a spot), then interprets each
# as encourage/discourage (attitude) or even/odd (count) from PUBLIC info only —
# "high vs low" relative to the cards of that suit still outstanding when it was
# played, exactly the read a human makes at the table. Honours UDCA via the same
# switch the emitter uses, so the reading matches biq's own carding.
# ---------------------------------------------------------------------------

_HONOUR_VAL = 3   # rank.value: A/K/Q/J ≤ 3 are honours; spots are 4..12


def _card_from_c52(c52: int) -> Card:
    return Card(Suit(c52 // 13), Rank(c52 % 13))


def _live_ranks_of_suit(board, suit: Suit, upto_trick: int,
                        before_cards: List[Card]) -> List[Rank]:
    """Ranks of `suit` still unseen when a card was played: all ranks minus
    those that appeared in completed tricks before `upto_trick` and in the
    same trick before the signaller. Sorted high→low (ACE first)."""
    seen = set()
    tricks = getattr(board, "tricks", None) or []
    for j, t in enumerate(tricks):
        if j >= upto_trick:
            break
        for c in (getattr(t, "cards", None) or []):
            if c.suit == suit:
                seen.add(c.rank)
    for c in before_cards:
        if c.suit == suit:
            seen.add(c.rank)
    return [r for r in Rank if r not in seen]


def _is_high(card: Card, live: List[Rank]) -> bool:
    """Is `card` in the upper half of the still-outstanding cards of its suit?"""
    try:
        idx = live.index(card.rank)
    except ValueError:
        return False
    return idx * 2 < len(live) - 1


def _interpret(attitude: bool, high: bool, cfg: CardingConfig,
               is_trump: bool = False):
    """Return (tag, meaning) for a follow-suit signal under `cfg`.

    A count signal in the TRUMP suit is read as a trump echo: high-low in
    trumps shows an ODD number (typically three — "I can ruff"), the reverse
    of a side-suit count signal."""
    if attitude:
        positive = high if cfg.attitude == "standard" else (not high)
        return ("enc", "encourages") if positive else ("disc", "discourages")
    if is_trump and cfg.trump_echo != "off":
        # standard trump echo: high → odd (3+); flips with a reverse-count pair.
        odd = high if cfg.count == "standard" else (not high)
        return ("ruff", "3 trumps (echo)") if odd else ("te", "even trumps")
    even = high if cfg.count == "standard" else (not high)
    return ("even", "even count") if even else ("odd", "odd count")


def _interpret_discard(card: Card, trump: Optional[Suit], cfg: CardingConfig,
                       live: List[Rank]):
    """Read a DISCARD (off-suit card). Returns (tag, meaning).

    • Lavinthal/suit-preference (cfg.discards == 'lavinthal'): the rank points
      to one of the two OTHER side suits — high → higher-ranking, low → lower
      (the thrown suit and trump are excluded). Only confident when exactly two
      candidate suits remain (a trump contract); otherwise falls back to
      attitude. ACBL: suit-preference is "an unusual play of unmistakable
      significance" — so we only assert it when the agreement says so and it is
      unambiguous.
    • Attitude discard (default): high spot = likes the suit thrown, low =
      discourages it (polarity follows the attitude agreement)."""
    suit = card.suit
    high = _is_high(card, live)
    if cfg.discards == "lavinthal":
        cands = [s for s in SUIT_ROWS if s != suit and s != trump]
        if len(cands) == 2:                       # SUIT_ROWS is high→low ranked
            target = cands[0] if high else cands[1]
            return ("sp", f"suit-pref → {target.symbol()}")
        # NT or otherwise ambiguous: fall through to an attitude read.
    positive = high if cfg.attitude == "standard" else (not high)
    if positive:
        return ("enc", f"likes {suit.symbol()}")
    return ("disc", f"not {suit.symbol()}")


def signal_reads(board, declarer: Optional[Seat], trump: Optional[Suit],
                 carding_for: Optional[Callable[[Seat], CardingConfig]] = None,
                 visible: Optional[Set[Seat]] = None):
    """Readable defensive signals from COMPLETED tricks, both defenders —
    attitude/count on follows AND suit-preference/attitude on discards.

    `carding_for(seat)` supplies that seat's agreement; defaults to the
    legacy global convention. When a signaller's hand is in `visible`, each
    follow signal is also checked for HONESTY against that hand (engine's
    `signal_read.mis_signals`, decoded under the pair's own convention):
    `honest` is True (matches the convention card), False (a false-card — with
    `expected` = the honest card), or None (signaller hidden / not checkable).
    Returns chronological dicts:
      {seat, trick, card, suit, tag, meaning, kind, honest, expected}
    """
    if declarer is None:
        return []
    if carding_for is None:
        carding_for = lambda s: _default_carding()
    visible = visible or set()
    defenders = [s for s in Seat if s.is_ns() != declarer.is_ns()]
    out = []

    # Smith echo (NT) — first; its cards are removed from the count reads below.
    smith, smith_used = smith_reads(board, declarer, trump, carding_for, visible)
    out.extend(smith)

    # Follows (attitude/count) — reuse the engine's careful free-signal filter.
    if _signal_read is not None:
        for d in defenders:
            try:
                # read(seat=X) returns X.partner()'s signals; pass d.partner()
                # so the signaller is d itself.
                recs = _signal_read.read(board, d.partner(), declarer, trump)
            except Exception:
                recs = []
            cfg = carding_for(d)
            dishonest = _dishonest_map(recs, board, d, visible, cfg)
            for rec in recs:
                if (rec["trick"], rec["card52"]) in smith_used:
                    continue                      # already shown as a Smith echo
                card = _card_from_c52(rec["card52"])
                suit = Suit(rec["suit"])
                before = [_card_from_c52(c) for c in rec.get("before", [])]
                live = _live_ranks_of_suit(board, suit, rec["trick"], before)
                high = _is_high(card, live)
                is_trump = (trump is not None and suit == trump)
                tag, meaning = _interpret(rec["attitude"], high, cfg, is_trump)
                rd = {"seat": d, "trick": rec["trick"], "card": card,
                      "suit": suit, "tag": tag, "meaning": meaning,
                      "kind": "att" if rec["attitude"] else "cnt",
                      "honest": None, "expected": None}
                if d in visible:
                    if is_trump:
                        # mis_signals judges trump count by the wrong (even-high)
                        # parity; trump echo is odd-high, so check it directly.
                        rd["honest"], rd["expected"] = _trump_echo_honest(
                            board, d, card, rec["trick"], cfg, trump, visible)
                    else:
                        exp = dishonest.get((rec["trick"], rec["card52"]), "ok")
                        if exp == "ok":
                            rd["honest"] = True
                        else:
                            rd["honest"] = False
                            rd["expected"] = _card_from_c52(exp)
                out.append(rd)

    # Discards (suit-preference / attitude) — the engine's reader skips these.
    out.extend(_completed_discard_reads(board, declarer, trump,
                                        defenders, carding_for, visible))
    # Suit-preference on ruff-giving leads (trump contracts).
    out.extend(ruff_lead_reads(board, declarer, trump, carding_for, visible))
    # A pair that gives no signals (biq with "Play defensive signals" off):
    # its spot cards say nothing, so nothing is read from them.
    out = [r for r in out if carding_for(r["seat"]).signals]
    out.sort(key=lambda r: r["trick"])
    return out


def _dishonest_map(recs, board, signaller, visible, cfg) -> Dict[tuple, int]:
    """{(trick, played52): expected52} for the signaller's FALSE follow signals,
    judged against its actual visible hand. Empty unless the hand is visible.

    The engine's `mis_signals` reads `signals.is_udca()` for the convention; our
    presets move attitude+count together, so we map the pair's agreement onto
    that single switch around the call, then restore it."""
    if (_signal_read is None or signaller not in visible
            or not board.hands or board.hands.get(signaller) is None):
        return {}
    actual = {_cid(c) for c in board.hands[signaller].cards}   # current holding
    prev = _signals.is_udca() if _signals is not None else False
    try:
        if _signals is not None:
            _signals.set_convention(cfg.attitude == "udca")
        mis = _signal_read.mis_signals(recs, actual)
    except Exception:
        mis = []
    finally:
        if _signals is not None:
            _signals.set_convention(prev)
    return {(m["trick"], m["played52"]): m["expected52"] for m in mis}


def _holding_at_suit(board, seat, suit, trick_index, visible):
    """Ranks of `suit` the seat held when it played at `trick_index`: current
    face-up remaining plus any cards of the suit it played from that trick on.
    None if the hand isn't face-up."""
    if seat not in visible or not board.hands or board.hands.get(seat) is None:
        return None
    held = [c.rank for c in board.hands[seat].cards if c.suit == suit]
    for j, t in enumerate(getattr(board, "tricks", None) or []):
        if j < trick_index:
            continue
        cards = getattr(t, "cards", None) or []
        leader = getattr(t, "leader", None)
        if leader is None:
            continue
        base = int(leader)
        for p, c in enumerate(cards):
            if Seat((base + p) % 4) == seat and c.suit == suit:
                held.append(c.rank)
    return sorted(held)


def _honest_from_spots(spots: List[Rank], want_high: bool, played: Rank,
                       suit: Suit):
    """Given the available spots (sorted high→low) and whether the honest card
    is the high one, return (honest, expected_or_None)."""
    if len(spots) < 2:
        return (None, None)
    expected = spots[0] if want_high else spots[-1]
    if expected == played:
        return (True, None)
    return (False, Card(suit, expected))


def _attitude_discard_honest(board, seat, card, trick_index, cfg, visible):
    held = _holding_at_suit(board, seat, card.suit, trick_index, visible)
    if held is None:
        return (None, None)
    spots = sorted([r for r in held if r.value > _HONOUR_VAL] or held)
    like = any(r.value <= _HONOUR_VAL for r in held)        # honour present
    want_high = like if cfg.attitude == "standard" else (not like)
    return _honest_from_spots(spots, want_high, card.rank, card.suit)


_SP_VAL = {Rank.ACE: 4, Rank.KING: 3, Rank.QUEEN: 2, Rank.JACK: 1}


def _suit_pref_honest(board, seat, target, other, trick_index, visible):
    """A suit-preference signal points at `target` (over `other`). Honest when
    the signaller actually holds more values/entry in `target` than in `other`.
    Checkable only when the signaller is face-up; returns (honest, None) —
    suit-preference has no single 'expected card'. None when it can't be judged
    (signaller hidden, or equal values either way)."""
    ht = _holding_at_suit(board, seat, target, trick_index, visible)
    ho = _holding_at_suit(board, seat, other, trick_index, visible)
    if ht is None or ho is None:
        return (None, None)
    vt = sum(_SP_VAL.get(r, 0) for r in ht)
    vo = sum(_SP_VAL.get(r, 0) for r in ho)
    if vt == vo:
        return (None, None)
    return (vt > vo, None)


def _trump_echo_honest(board, seat, card, trick_index, cfg, trump, visible):
    held = _holding_at_suit(board, seat, trump, trick_index, visible)
    if held is None:
        return (None, None)
    odd = (len(held) % 2 == 1)                  # echo: high-low shows odd (3)
    want_high = odd if cfg.count == "standard" else (not odd)
    return _honest_from_spots(sorted(held), want_high, card.rank, trump)


def _smith_honest(board, seat, card, trick_index, lead_suit, want_high_like,
                  visible):
    held = _holding_at_suit(board, seat, lead_suit, trick_index, visible)
    if held is None:
        return (None, None)
    like = (len(held) >= 3) or any(r.value <= _HONOUR_VAL for r in held)
    want_high = like if want_high_like else (not like)
    spots = sorted([r for r in (_holding_at_suit(board, seat, card.suit,
                    trick_index, visible) or []) if r.value > _HONOUR_VAL]
                   or (_holding_at_suit(board, seat, card.suit, trick_index,
                                        visible) or []))
    return _honest_from_spots(spots, want_high, card.rank, card.suit)


def smith_reads(board, declarer: Optional[Seat], trump: Optional[Suit],
                carding_for: Callable[[Seat], CardingConfig],
                visible: Optional[Set[Seat]] = None):
    """Smith-echo reads (NT only). The FIRST card a defender plays in a suit the
    DECLARING side leads (other than the opening-lead suit) is an attitude signal
    about the OPENING-LEAD suit: standard high = "I liked the lead, continue it",
    low = discourage / suggest a switch; reverse Smith inverts.

    Returns (reads, used) where `used` is the set of (trick, card52) consumed so
    the caller can suppress the ordinary count read on those cards."""
    out, used = [], set()
    visible = visible or set()
    if trump is not None or declarer is None:
        return out, used                          # suit contract — N/A
    tricks = getattr(board, "tricks", None) or []
    if not tricks or not (getattr(tricks[0], "cards", None)):
        return out, used
    lead_suit = tricks[0].cards[0].suit
    leader0 = Seat(int(tricks[0].leader)) if tricks[0].leader is not None else None
    decl_ns = declarer.is_ns()
    defenders = {s for s in Seat if s.is_ns() != decl_ns}
    done = set()                                  # defenders who've had their chance
    for j, t in enumerate(tricks):
        cards = getattr(t, "cards", None) or []
        leader = getattr(t, "leader", None)
        if len(cards) < 4 or leader is None:
            continue
        if leader.is_ns() != decl_ns:
            continue                              # not a declaring-side lead
        d_suit = cards[0].suit
        if d_suit == lead_suit:
            continue                              # must be a fresh (declarer's) suit
        base = int(leader)
        for p in range(4):
            seat = Seat((base + p) % 4)
            if seat not in defenders or seat in done:
                continue
            c = cards[p]
            if c.suit != d_suit:
                continue                          # void/discard — chance not used yet
            done.add(seat)
            if c.rank.value <= _HONOUR_VAL:
                continue                          # forced honour — no spot signal
            cfg = carding_for(seat)
            if cfg.smith == "off":
                continue
            live = _live_ranks_of_suit(board, d_suit, j, cards[:p])
            high = _is_high(c, live)
            # Reverse-Smith asymmetry: only the OPENING LEADER reverses
            # (high = "I have an alternative — shift"); third hand stays
            # standard (high = continue). So the flip is leader-only.
            is_leader = (seat == leader0)
            want_high_like = not (cfg.smith == "reverse" and is_leader)
            if not want_high_like:
                high = not high
            who = "lead" if is_leader else "3rd"
            if high:
                tag, meaning = "enc", f"likes {lead_suit.symbol()} ({who}, continue)"
            else:
                tag, meaning = "disc", f"shift from {lead_suit.symbol()} ({who})"
            honest, expected = _smith_honest(board, seat, c, j, lead_suit,
                                             want_high_like, visible)
            out.append({"seat": seat, "trick": j, "card": c, "suit": d_suit,
                        "tag": tag, "meaning": meaning, "kind": "smith",
                        "honest": honest, "expected": expected})
            used.add((j, _cid(c)))
    return out, used


def _completed_discard_reads(board, declarer, trump, defenders, carding_for,
                             visible=None):
    """Read defenders' DISCARDS (off-suit, not a ruff) on completed tricks."""
    out = []
    visible = visible or set()
    for j, t in enumerate(getattr(board, "tricks", None) or []):
        cards = getattr(t, "cards", None) or []
        leader = getattr(t, "leader", None)
        if len(cards) < 4 or leader is None:
            continue
        base = int(leader)
        led = cards[0].suit
        for p in range(4):
            seat = Seat((base + p) % 4)
            if seat not in defenders:
                continue
            c = cards[p]
            if c.suit == led:
                continue                         # followed suit — handled above
            if trump is not None and c.suit == trump:
                continue                         # ruff, not a signal
            cfg = carding_for(seat)
            live = _live_ranks_of_suit(board, c.suit, j, cards[:p])
            tag, meaning = _interpret_discard(c, trump, cfg, live)
            honest = expected = None
            if tag in ("enc", "disc"):           # attitude discard — checkable
                honest, expected = _attitude_discard_honest(
                    board, seat, c, j, cfg, visible)
            elif tag == "sp":                    # suit-preference (Lavinthal)
                cands = [s for s in SUIT_ROWS if s != c.suit and s != trump]
                if len(cands) == 2:
                    hi = _is_high(c, live)
                    target = cands[0] if hi else cands[1]
                    other = cands[1] if hi else cands[0]
                    honest, expected = _suit_pref_honest(
                        board, seat, target, other, j, visible)
            out.append({"seat": seat, "trick": j, "card": c, "suit": c.suit,
                        "tag": tag, "meaning": meaning, "kind": "dsc",
                        "honest": honest, "expected": expected})
    return out


def ruff_lead_reads(board, declarer, trump, carding_for, visible=None):
    """Suit-preference on a ruff-giving LEAD (trump contracts): a defender leads
    a suit its partner is known void in (and partner still has trumps), so the
    lead's rank steers the ruff return — high → higher of the two other side
    suits, low → lower. Returns reads (kind 'sp')."""
    out = []
    visible = visible or set()
    if trump is None or declarer is None:
        return out
    _, voids, all_played = collect_play(board)
    if not remaining_ranks(all_played).get(trump):
        return out                                   # trumps gone — no ruff
    tricks = getattr(board, "tricks", None) or []
    # First trick each seat showed out of each suit (so we only read a lead as a
    # ruff-SP once partner is KNOWN void from an EARLIER trick).
    shown: Dict[Seat, Dict[Suit, int]] = {s: {} for s in Seat}
    for j, t in enumerate(tricks):
        cards = getattr(t, "cards", None) or []
        leader = getattr(t, "leader", None)
        if len(cards) < 4 or leader is None:
            continue
        base = int(leader)
        led_s = cards[0].suit
        for p, c in enumerate(cards):
            st = Seat((base + p) % 4)
            if c.suit != led_s and led_s not in shown[st]:
                shown[st][led_s] = j
    defenders = [s for s in Seat if s.is_ns() != declarer.is_ns()]
    for j, t in enumerate(tricks):
        cards = getattr(t, "cards", None) or []
        leader = getattr(t, "leader", None)
        if len(cards) < 4 or leader is None:
            continue
        ldr = Seat(int(leader))
        if ldr not in defenders:
            continue
        led = cards[0].suit
        if led == trump:
            continue
        partner = ldr.partner()
        ve = shown[partner].get(led)
        if ve is None or ve >= j or trump in voids[partner]:
            continue                                 # partner not known void yet
        cands = [s for s in SUIT_ROWS if s != led and s != trump]
        if len(cands) != 2:
            continue
        live = _live_ranks_of_suit(board, led, j, [])
        high = _is_high(cards[0], live)
        target = cands[0] if high else cands[1]
        other = cands[1] if high else cands[0]
        honest, expected = _suit_pref_honest(board, ldr, target, other, j,
                                             visible)
        out.append({"seat": ldr, "trick": j, "card": cards[0], "suit": led,
                    "tag": "sp", "meaning": f"ruff-return → {target.symbol()}",
                    "kind": "ruflead", "honest": honest, "expected": expected})
    return out


def current_trick_reads(board, declarer: Optional[Seat], trump: Optional[Suit],
                        carding_for: Optional[Callable[[Seat], CardingConfig]] = None):
    """Live read of the IN-PROGRESS trick's defender cards (the engine's reader
    only sees completed tricks). Returns {seat: {card, tag, meaning}}.

    Reads a defender that FOLLOWED suit with a spot (attitude/count) and a
    defender that DISCARDED off-suit (suit-preference / attitude). The opening
    leader's own card and honour follows carry no spot signal and are skipped."""
    out = {}
    ct = getattr(board, "current_trick", None)
    if ct is None or declarer is None:
        return out
    if carding_for is None:
        carding_for = lambda s: _default_carding()
    cards = getattr(ct, "cards", None) or []
    leader = getattr(ct, "leader", None)
    if leader is None or len(cards) < 2:
        return out
    base = int(leader)
    led = cards[0].suit
    upto = len(getattr(board, "tricks", None) or [])
    for p in range(1, len(cards)):              # skip the leader (pos 0)
        seat = Seat((base + p) % 4)
        if seat.is_ns() == declarer.is_ns():
            continue                            # declaring side — no signal
        c = cards[p]
        cfg = carding_for(seat)
        if not cfg.signals:
            continue                            # this pair gives no signals
        if c.suit == led:
            if c.rank.value <= _HONOUR_VAL:
                continue                        # honour follow — not a spot signal
            attitude = (leader.is_ns() == seat.is_ns())
            live = _live_ranks_of_suit(board, led, upto, cards[:p])
            is_trump = (trump is not None and led == trump)
            tag, meaning = _interpret(attitude, _is_high(c, live), cfg, is_trump)
        elif trump is not None and c.suit == trump:
            continue                            # ruff, not a signal
        else:
            live = _live_ranks_of_suit(board, c.suit, upto, cards[:p])
            tag, meaning = _interpret_discard(c, trump, cfg, live)
        out[seat] = {"card": c, "tag": tag, "meaning": meaning}
    return out


_SIGNAL_TAG_COLOR = {"enc": "#7ad17a", "disc": "#e08a8a",
                     "even": "#8ab8e0", "odd": "#d9b36b",
                     "sp": "#d6a3e8", "ruff": "#e8b06a", "te": "#8ab8e0"}


def _tag_html(tag: str) -> str:
    col = _SIGNAL_TAG_COLOR.get(tag, "#9fb6cc")
    return (f"<span style='color:{col}; font-size:13px; font-weight:bold'>"
            f"{tag}</span>")


# Short Other-column labels for each signal tag (the full text shows in the
# Coaching/Signals panel; the grid cell just needs a glanceable chip).
_SIGNAL_SHORT = {"enc": "likes", "disc": "drop", "even": "even", "odd": "odd",
                 "ruff": "3·ruff", "te": "tr-ct", "sp": "S-pref",
                 "smith": "Smith"}


def _signal_chip(rd: dict) -> str:
    """A vivid inverse-video chip summarising one defensive signal read for a
    suit, with a ✗ flag when the engine knows it was a false-card."""
    tag = rd.get("tag", "")
    col = _SIGNAL_TAG_COLOR.get(tag, ACC_BLUE)
    label = _SIGNAL_SHORT.get(tag) or (rd.get("meaning") or tag)
    out = _chip(label, col)
    if rd.get("honest") is False:
        out += _chip("✗", ACC_RED)
    return out


def _honour_color(p: float) -> Optional[str]:
    """Confidence colour for a missing-honour LOCATION probability (0..1):
    green > 85%, orange > 70%, red > 55%; below that it's too uncertain to
    place in a hand (it shows only in the Count/Honours panel)."""
    if p > 0.85:
        return ACC_GREEN
    if p > 0.70:
        return ACC_ORANGE
    if p > 0.55:
        return ACC_RED
    return None


def _honour_hint_map(board, visible, layout, posterior=None) -> Dict[Tuple[Seat, Suit], str]:
    """For EACH hidden seat + suit, the missing honours likely to sit there as
    COLOUR-CODED letters (green >85% · orange >70% · red >55%) with NO numbers —
    the exact percentages live in the Count/Honours panel. Proven honours are
    skipped (they already render in that hand's Known column)."""
    import re
    seat_by_char = {s.to_char(): s for s in Seat}
    acc: Dict[Tuple[Seat, Suit], List[str]] = {}
    for su, r, label in honour_placement(board, visible, layout, posterior):
        if "proven" in label:
            continue
        for sc, pc in re.findall(r'([NESW])\s+(\d+)%', label):
            seat = seat_by_char.get(sc)
            if seat is None:
                continue
            col = _honour_color(int(pc) / 100.0)
            if col:
                acc.setdefault((seat, su), []).append(
                    f"<span style='color:{col};font-weight:bold'>"
                    f"{r.to_char()}</span>")
    return {k: "".join(v) for k, v in acc.items()}


def _card_glyph(card: Card) -> str:
    col = _suit_color(card.suit)
    return (f"<span style='color:{col}; font-weight:bold'>"
            f"{card.suit.symbol()}{card.rank.to_char()}</span>")


# ---------------------------------------------------------------------------
# Qt widgets
# ---------------------------------------------------------------------------

PANEL_BG = "#101820"   # pokerIQ panel (slightly warmer)
PANEL_FG = "#eef3f7"   # pokerIQ ink
GRID_BG = "#13202a"   # pokerIQ card-dark (warmer green-ink)
MASTER_BG = "#ffd54a"  # gold — current master
TRUMP_TINT = "#1f5a3a"  # vivid green wash on the trump row
PANEL_LINE = "#2c4a3a"  # warm green-grey panel border

# pokerIQ accent palette — used for vivid, colour-coded markers and borders
# instead of faint superscripts. Each role gets its own hue.
ACC_GOLD   = "#d9b25b"   # master / declaring side
ACC_GREEN  = "#3fb950"   # entry / sure access
ACC_BLUE   = "#58a6ff"   # stopper / count
ACC_RED    = "#f85149"   # danger / knock-out
ACC_ORANGE = "#f0883e"   # cross-hand transport / suit-pref
ACC_YELLOW = "#ffd84d"   # attention
ACC_PURPLE = "#a87fff"   # busy / squeeze


def _chip(text: str, bg: str, fg: str = "#06121f", outline: bool = False) -> str:
    """An inverse-video (or outlined) inline badge — readable at a glance,
    unlike the old tiny superscripts. `outline` draws the glyph in the accent
    colour inside a thin border instead of solid fill (used for INFERRED, not
    yet seen, cards)."""
    if outline:
        return (f"<span style='color:{bg}; border:1px solid {bg};"
                f" border-radius:3px; padding:0 2px; font-size:12px;"
                f" font-weight:bold;'>{text}</span>")
    return (f"<span style='background:{bg}; color:{fg}; border-radius:3px;"
            f" padding:0 3px; font-size:12px; font-weight:bold;'>{text}</span>")


class _Panel(QFrame):
    """A small titled analysis panel for a corner of the cross."""

    def __init__(self, title: str, parent=None, accent: str = ACC_GOLD):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # A thicker accent left-border gives each panel its own hue (pokerIQ
        # colour-coded panels) without shouting.
        self.setStyleSheet(
            f"background:{PANEL_BG}; color:{PANEL_FG};"
            f" border:1px solid {PANEL_LINE}; border-left:3px solid {accent};"
            f" border-radius:6px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet(f"font-weight:bold; color:{accent};"
                                  f" border:0; font-size:15px;")
        lay.addWidget(self._title)
        self.body = QLabel("")
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setStyleSheet("border:0;")
        lay.addWidget(self.body, stretch=1)

    def set_html(self, html: str):
        self.body.setText(html)


def _scale_html(html: str, scale: float) -> str:
    """Rich text with its inline px font sizes multiplied (the zoomed hand)."""
    if scale == 1.0 or not html:
        return html
    return re.sub(r"font-size:\s*(\d+)px",
                  lambda m: f"font-size:{round(int(m.group(1)) * scale)}px", html)


class HandGridWidget(QFrame):
    """One seat's 4×2 grid: rows ♠♥♦♣, columns Known | Other. ``scale`` enlarges every
    font (the zoomed hand); ``mirror`` is a second grid that repeats every update
    (the zoom page follows the live hand). Double-click asks the view to zoom."""

    double_clicked = pyqtSignal(object)

    def __init__(self, seat: Seat, parent=None, scale: float = 1.0):
        super().__init__(parent)
        self.seat = seat
        self.scale = scale
        self.mirror: Optional["HandGridWidget"] = None
        self._state = {"title": seat.name.title(), "shape": "", "accent": PANEL_LINE, "rows": {}}
        self._accent = PANEL_LINE
        px = lambda n: round(n * scale)                         # noqa: E731
        self._px = px
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"background:{GRID_BG}; border:2px solid {PANEL_LINE};"
            f" border-radius:8px;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        self.title = QLabel(seat.name.title())
        self.title.setStyleSheet("color:#eef3f7; font-weight:bold; border:0;"
                                 f" font-size:{px(16)}px;")
        outer.addWidget(self.title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(px(10))
        grid.setVerticalSpacing(px(8))
        hdr_k = QLabel("Known"); hdr_o = QLabel("Other")
        for h in (hdr_k, hdr_o):
            h.setStyleSheet(f"color:{ACC_GREEN}; font-size:{px(14)}px; border:0;"
                            " font-weight:bold;")
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(hdr_k, 0, 1)
        grid.addWidget(hdr_o, 0, 2)

        self._suit_lbl: Dict[Suit, QLabel] = {}
        self._known_lbl: Dict[Suit, QLabel] = {}
        self._other_lbl: Dict[Suit, QLabel] = {}
        for i, su in enumerate(SUIT_ROWS, start=1):
            sl = QLabel(su.symbol())
            sl.setStyleSheet(
                f"color:{_suit_color(su)}; font-weight:bold; font-size:{px(30)}px;"
                f" border:0; background:transparent;")
            kl = QLabel("—")
            kl.setTextFormat(Qt.TextFormat.RichText)
            kl.setFont(QFont("Monospace", px(32)))   # 2× — the grids had lots of slack
            # (the view's "QLabel { font-size }" rule wins over setFont, so the zoomed
            # copy sets its size in its own stylesheet)
            kl.setStyleSheet("border:0;" if scale == 1.0 else f"border:0; font-size:{px(16)}px;")
            ol = QLabel("")
            ol.setTextFormat(Qt.TextFormat.RichText)
            ol.setWordWrap(scale != 1.0)
            ol.setStyleSheet(f"color:#9fb6cc; font-size:{px(17)}px; border:0;")
            self._suit_lbl[su] = sl
            self._known_lbl[su] = kl
            self._other_lbl[su] = ol
            grid.addWidget(sl, i, 0)
            grid.addWidget(kl, i, 1)
            grid.addWidget(ol, i, 2)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 2)
        outer.addLayout(grid)

        # Lower-right: this seat's ORIGINAL (13-card) hand shape, revealed as
        # soon as it's known — exact for a face-up hand, progressively for a
        # hidden one. Lets a defender judge count signals from the table.
        self._shape_lbl = QLabel("")
        self._shape_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._shape_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        self._shape_lbl.setStyleSheet(f"color:#9fb6cc; font-size:{px(15)}px; border:0;")
        outer.addWidget(self._shape_lbl)
        self.setMinimumWidth(230)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self.seat)
        event.accept()

    def copy_from(self, other: "HandGridWidget"):
        """Take another grid's current contents (the zoom page catching up)."""
        st = other._state
        self.set_title(st["title"])
        self.set_shape(st["shape"])
        self.set_accent(st["accent"])
        for su, (k, o, t) in st["rows"].items():
            self.set_row(su, k, o, t)

    def set_title(self, text: str):
        self._state["title"] = text
        self.title.setText(text)
        if self.mirror is not None:
            self.mirror.set_title(text)

    def set_shape(self, html: str):
        """Set the lower-right original-shape line (empty string hides it)."""
        self._state["shape"] = html or ""
        self._shape_lbl.setText(_scale_html(html or "", self.scale))
        if self.mirror is not None:
            self.mirror.set_shape(html)

    def set_accent(self, color: str):
        """Recolour the hand frame's border — declaring side gold, defenders
        green, danger hand red (pokerIQ-style coloured seat boxes)."""
        self._state["accent"] = color
        if self.mirror is not None:
            self.mirror.set_accent(color)
        if color == self._accent:
            return
        self._accent = color
        self.setStyleSheet(
            f"background:{GRID_BG}; border:2px solid {color};"
            f" border-radius:8px;")

    def set_row(self, suit: Suit, known_html: str, other_html: str,
                trump: bool):
        self._state["rows"][suit] = (known_html, other_html, trump)
        self._known_lbl[suit].setText(_scale_html(known_html, self.scale) or "—")
        self._other_lbl[suit].setText(_scale_html(other_html, self.scale) or "")
        tint = f"background:{TRUMP_TINT}; border-radius:3px;" if trump else \
            "background:transparent;"
        self._suit_lbl[suit].setStyleSheet(
            f"color:{_suit_color(suit)}; font-weight:bold; font-size:{self._px(30)}px;"
            f" border:0; {tint}")
        if self.mirror is not None:
            self.mirror.set_row(suit, known_html, other_html, trump)


ZOOM_SCALE = 1.9
ZOOM_CHOICES = ["All hands", "North", "East", "South", "West"]


class TeachingView(QWidget):
    """The instrumented cross. Call refresh(...) to repaint from state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.detail = INTERMEDIATE
        # Bigger base text throughout, and a readable (light-on-dark) tooltip —
        # the default tooltip palette renders black on this dark background.
        self.setStyleSheet(
            # warm radial-gradient backdrop, matching pokerIQ's felt glow
            "TeachingView { background:qradialgradient(cx:0.5, cy:0.0,"
            " radius:1.2, fx:0.5, fy:0.0, stop:0 #18242e, stop:0.7 #0a0e13); }"
            "QLabel { font-size: 16px; }"
            # The combo boxes (Detail / Carding N/S / E/W / Smith) need
            # explicit colours: without them the popup's currently-selected
            # row renders white-on-white (blank) while the other rows are
            # black-on-white. Pin the closed box and the popup list, and give
            # the selected row a visible blue highlight.
            "QComboBox { font-size: 15px; color:#0c0c0c; background:#ffffff;"
            " border:1px solid #5a7088; border-radius:3px; padding:2px 6px; }"
            "QComboBox QAbstractItemView { background:#ffffff; color:#0c0c0c;"
            " selection-background-color:#3070b0; selection-color:#ffffff;"
            " outline:0; }"
            "QToolTip { color:#eaf2fb; background-color:#16324f;"
            " border:1px solid #6fa8d6; padding:5px; font-size:14px; }")
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Header: title + detail selector + systems.
        header = QHBoxLayout()
        h = QLabel("Analysis view")
        h.setStyleSheet("color:#d9b25b; font-weight:bold; font-size:14px;")
        header.addWidget(h)
        header.addSpacing(12)
        header.addWidget(self._mk_label("Detail:"))
        self.detail_combo = QComboBox()
        self.detail_combo.addItems([BEGINNER, INTERMEDIATE, EXPERT])
        self.detail_combo.setCurrentText(INTERMEDIATE)
        self.detail_combo.currentTextChanged.connect(self._on_detail_changed)
        header.addWidget(self.detail_combo)

        # Per-pair carding agreement — N/S and E/W decoded independently. The
        # defaults come from Preferences → Carding (single shared source).
        header.addSpacing(12)
        header.addWidget(self._mk_label("Carding N/S:"))
        self.ns_carding_combo = QComboBox()
        self.ns_carding_combo.addItems(carding_names())
        self.ns_carding_combo.setCurrentText(_default_preset_name("ns"))
        self.ns_carding_combo.currentTextChanged.connect(self._on_carding_changed)
        header.addWidget(self.ns_carding_combo)
        header.addWidget(self._mk_label("E/W:"))
        self.ew_carding_combo = QComboBox()
        self.ew_carding_combo.addItems(carding_names())
        self.ew_carding_combo.setCurrentText(_default_preset_name("ew"))
        self.ew_carding_combo.currentTextChanged.connect(self._on_carding_changed)
        header.addWidget(self.ew_carding_combo)

        # Smith echo (NT): a defending-side agreement; one control since only
        # one side defends a given deal. Trump echo is read automatically.
        header.addWidget(self._mk_label("Smith:"))
        self.smith_combo = QComboBox()
        self.smith_combo.addItems(["Off", "Standard", "Reverse"])
        self.smith_combo.setCurrentText(_default_smith())
        self.smith_combo.currentTextChanged.connect(self._on_carding_changed)
        header.addWidget(self.smith_combo)

        # Zoom on one hand: the cross's small fonts can be hard to read
        # (double-click a hand does the same; double-click or Esc returns).
        header.addSpacing(12)
        header.addWidget(self._mk_label("Zoom:"))
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(ZOOM_CHOICES)
        self.zoom_combo.currentTextChanged.connect(self._on_zoom_changed)
        header.addWidget(self.zoom_combo)

        header.addStretch()
        self.systems_lbl = self._mk_label("")
        header.addWidget(self.systems_lbl)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(6)
        # Panels in the corners.
        self.p_contract = _Panel("Contract / Tricks", accent=ACC_GOLD)
        self.p_plan = _Panel("Plan", accent=ACC_GREEN)
        self.p_count = _Panel("Count / Honours", accent=ACC_BLUE)
        self.p_coach = _Panel("Coaching / Signals", accent=ACC_ORANGE)
        # Hand grids on the edges.
        self.hands = {s: HandGridWidget(s) for s in Seat}
        # Centre: current trick + on-lead.
        self.centre = _Panel("Table")

        grid.addWidget(self.p_contract, 0, 0)
        grid.addWidget(self.hands[Seat.NORTH], 0, 1)
        grid.addWidget(self.p_plan, 0, 2)
        grid.addWidget(self.hands[Seat.WEST], 1, 0)
        grid.addWidget(self.centre, 1, 1)
        grid.addWidget(self.hands[Seat.EAST], 1, 2)
        grid.addWidget(self.p_count, 2, 0)
        grid.addWidget(self.hands[Seat.SOUTH], 2, 1)
        grid.addWidget(self.p_coach, 2, 2)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        for r in range(3):
            grid.setRowStretch(r, 1)
        # Page 0 = the cross; page 1 = one hand zoomed (a scaled copy that
        # mirrors the live grid, so it follows the play).
        self._pages = QStackedWidget()
        cross = QWidget()
        cross.setLayout(grid)
        self._pages.addWidget(cross)
        self._zoom_page = QWidget()
        zl = QVBoxLayout(self._zoom_page)
        zl.setContentsMargins(0, 0, 0, 0)
        ztop = QHBoxLayout()
        self._zoom_lbl = QLabel("")
        self._zoom_lbl.setStyleSheet("color:#d9b25b; font-weight:bold; font-size:15px;")
        ztop.addWidget(self._zoom_lbl)
        ztop.addStretch()
        back = QPushButton("All hands")
        back.clicked.connect(lambda: self.zoom(None))
        ztop.addWidget(back)
        zl.addLayout(ztop)
        self._zoom_holder = QVBoxLayout()
        zl.addLayout(self._zoom_holder)
        # PLAYBELIEF-1: what the shown seat can infer about a zoomed HIDDEN hand.
        self.p_belief = _Panel("Beliefs: where the unseen cards are", accent=ACC_BLUE)
        self.p_belief.body.setStyleSheet("border:0; font-size:22px;")
        zl.addWidget(self.p_belief, stretch=1)
        self._pages.addWidget(self._zoom_page)
        self._zoomed: Optional[HandGridWidget] = None
        root.addWidget(self._pages, stretch=1)
        for hw in self.hands.values():
            hw.double_clicked.connect(self.zoom)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._zoom_page)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(lambda: self.zoom(None))

        legend = QLabel(
            "Known column:  "
            + _chip("A", MASTER_BG) + " master · "
            + "<span style='border:1px solid " + ACC_GREEN + ";border-radius:3px;"
              "padding:0 2px'>K</span> sure winner · "
            + "<span style='border:1px solid " + ACC_RED + ";border-radius:3px;"
              "padding:0 2px'>x</span> loser (LTC) · "
            + "<span style='border:1px dashed " + ACC_YELLOW + ";border-radius:3px;"
              "padding:0 2px'>Q</span> placed (inferred) · "
            + _chip("E", ACC_GREEN) + " entry · "
            + _chip("S", ACC_BLUE) + " stopper · "
            + _chip("KO", ACC_RED) + " knock-out · "
            + _chip("→", ACC_ORANGE) + " to partner · "
            + "<span style='background:" + TRUMP_TINT + "'>&nbsp;♣&nbsp;</span> trumps")
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setStyleSheet("color:#9fb6cc; font-size:13px;")
        root.addWidget(legend)
        tip = ("Boxed letters are role chips: E = entry (access to "
               "declarer/dummy), S = stopper (NT), KO = knock-out, → = card "
               "that reaches partner. Gold fill = current master; green box = "
               "sure winner; red box = loser; dashed box = a card PLACED by "
               "deduction (hidden hand). The Other column shows the max length "
               "range, the likely (≈) count, the latest defensive signal, and "
               "colour-coded missing honours likely here — green >85%, "
               "orange >70%, red >55% (exact % is in the Count/Honours panel).")
        for hw in self.hands.values():
            hw.setToolTip(tip)

        self._last_state = None    # cached args for re-render on detail change
        self._zoom_seat: Optional[Seat] = None
        self._posterior = None
        self._belief_ctx = None
        self._load_prefs()         # restore persisted selectors

    def reload_carding_defaults(self):
        """Re-read the carding selectors from Preferences → Carding (called
        when the user changes them in the Preferences dialog). Block the combo
        signals so this doesn't re-save stale values mid-update."""
        ns, ew, sm = (_default_preset_name("ns"), _default_preset_name("ew"),
                      _default_smith())
        for cb, val in ((self.ns_carding_combo, ns),
                        (self.ew_carding_combo, ew),
                        (self.smith_combo, sm)):
            cb.blockSignals(True)
            cb.setCurrentText(val)
            cb.blockSignals(False)
        if self._last_state is not None:
            self.refresh(**self._last_state)

    def _load_prefs(self):
        # Detail persists in the view's own json; carding lives in the shared
        # Preferences config (so the Preferences → Carding tab and these header
        # selectors are one source of truth) and is already applied above.
        try:
            data = json.loads(_tv_config_path().read_text())
        except Exception:
            return
        if data.get("detail") in (BEGINNER, INTERMEDIATE, EXPERT):
            self.detail_combo.setCurrentText(data["detail"])

    def _save_prefs(self):
        try:
            _tv_config_path().write_text(json.dumps({
                "detail": self.detail_combo.currentText(),
            }, indent=2))
        except Exception:
            pass

    def _save_carding_to_config(self):
        """Persist the header carding selectors to the shared Preferences."""
        p = _cfg_prefs()
        if p is None:
            return
        try:
            from backend.config import get_config_manager
            p.carding_ns = _pref_value(self.ns_carding_combo.currentText())
            p.carding_ew = _pref_value(self.ew_carding_combo.currentText())
            p.smith_echo = self.smith_combo.currentText()
            get_config_manager().save_preferences()
        except Exception:
            pass

    def zoom(self, seat: Optional[Seat]):
        """Show one hand enlarged (seat), or the whole cross again (None)."""
        if self._zoomed is not None:
            for hw in self.hands.values():
                if hw.mirror is self._zoomed:
                    hw.mirror = None
            self._zoom_holder.removeWidget(self._zoomed)
            self._zoomed.deleteLater()
            self._zoomed = None
        self._zoom_seat = seat
        if seat is None:
            self._pages.setCurrentIndex(0)
            name = ZOOM_CHOICES[0]
        else:
            big = HandGridWidget(seat, scale=ZOOM_SCALE)
            big.copy_from(self.hands[seat])
            big.double_clicked.connect(lambda _s: self.zoom(None))
            self.hands[seat].mirror = big
            self._zoomed = big
            self._zoom_holder.addWidget(big, alignment=Qt.AlignmentFlag.AlignTop)
            self._zoom_lbl.setText(f"{seat.name.title()} zoomed: double-click it,"
                                   f" press Esc or 'All hands' to see the four hands")
            self._pages.setCurrentIndex(1)
            self._zoom_page.setFocus()
            name = seat.name.title()
        if self.zoom_combo.currentText() != name:
            self.zoom_combo.blockSignals(True)
            self.zoom_combo.setCurrentText(name)
            self.zoom_combo.blockSignals(False)
        if seat is not None and self._last_state is not None:
            self.refresh(**self._last_state)       # the beliefs need the posterior
        else:
            self._render_beliefs()

    def _render_beliefs(self):
        """The zoom page's Beliefs panel: per unseen card the chance the zoomed (hidden) hand
        holds it, its expected suit lengths, and the latest reading of the play."""
        seat = self._zoom_seat
        if seat is None or self._belief_ctx is None:
            self.p_belief.setVisible(False)
            return
        board, visible = self._belief_ctx
        self.p_belief.setVisible(True)
        if seat in visible:
            self.p_belief.set_html("This hand is seen: its cards are exact (above).")
            return
        post = self._posterior
        if not post or "card" not in post:
            self.p_belief.set_html("Not enough consistent layouts to estimate yet.")
            return
        rows = []
        for su in SUIT_ROWS:
            cells = []
            for r in Rank:
                p_ = post["card"].get(int(su) * 13 + int(r), {}).get(seat)
                if p_ is None:
                    continue
                pct = round(100 * p_)
                col = (ACC_GREEN if pct >= 85 else ACC_ORANGE if pct >= 55
                       else "#9fb6cc" if pct >= 25 else "#5d7286")
                cells.append(f"<span style='color:{col}'>{r.to_char()}&nbsp;{pct}%</span>")
            exp = post.get("exp_length", {}).get(seat, {}).get(su)
            lead = (f"<span style='color:{_suit_color(su)};font-weight:bold'>{su.symbol()}</span>"
                    f" ≈{exp:.1f} cards: " if exp is not None else su.symbol())
            rows.append(lead + ("&nbsp;&nbsp; ".join(cells) or "none unseen"))
        try:
            from backend import play_belief
            note = play_belief.last_play_note(play_belief.plays_so_far(board),
                                              [s for s in Seat if s not in visible])
        except Exception:
            note = None
        foot = (f"<br><span style='color:{ACC_ORANGE}'>Last telling card: {note}</span>"
                if note else "")
        sig = " · signals read" if post.get("signals") else ""
        self.p_belief.set_html("<br>".join(rows) + foot
                               + f"<br><span style='color:#5d7286;font-size:16px'>"
                                 f"{post['samples']} sampled layouts, weighted by the bidding"
                                 f", restricted choice{sig}</span>")

    def _on_zoom_changed(self, text):
        self.zoom(None if text == ZOOM_CHOICES[0] else Seat[text.upper()])

    def _mk_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#9aa7b4;")
        return lbl

    def _on_detail_changed(self, text):
        self.detail = text
        self._save_prefs()
        if self._last_state is not None:
            self.refresh(**self._last_state)

    def _on_carding_changed(self, _text):
        self._save_carding_to_config()
        if self._last_state is not None:
            self.refresh(**self._last_state)

    def _carding_for(self, seat: Seat) -> CardingConfig:
        """The carding agreement of the SIGNALLER's pair, with the Smith-echo
        setting overlaid (a defending-side agreement shared by the controls)."""
        name = (self.ns_carding_combo.currentText() if seat.is_ns()
                else self.ew_carding_combo.currentText())
        base = carding_preset(name)
        smith = {"Off": "off", "Standard": "standard",
                 "Reverse": "reverse"}.get(self.smith_combo.currentText(), "off")
        return replace(base, smith=smith)

    # -- rendering ---------------------------------------------------------

    def refresh(self, **kw):
        """Repaint from the current play state (see _refresh), then the zoom page's beliefs."""
        self._belief_ctx = None
        self._refresh(**kw)
        self._render_beliefs()

    def _refresh(self, *, board: Optional[BoardState], contract: Optional[Contract],
                 declarer: Optional[Seat], dummy: Optional[Seat],
                 visible_seats: Set[Seat], ns_system=None, ew_system=None):
        """Repaint the whole view from the current play state. Safe to
        call with partial/None state (renders an empty shell)."""
        self._last_state = dict(
            board=board, contract=contract, declarer=declarer, dummy=dummy,
            visible_seats=set(visible_seats or set()),
            ns_system=ns_system, ew_system=ew_system)
        if board is None or not getattr(board, "hands", None):
            for s in Seat:
                self.hands[s].set_title(s.name.title())
                for su in SUIT_ROWS:
                    self.hands[s].set_row(su, "—", "", False)
            self.p_contract.set_html("No board.")
            for p in (self.p_plan, self.p_count, self.p_coach, self.centre):
                p.set_html("")
            return

        visible = set(visible_seats or set())
        trump = contract.suit if (contract and contract.suit != Suit.NOTRUMP) else None

        # Per-pair auction constraints (NS seats from the NS-system pass,
        # EW seats from the EW-system pass) so each side's bids are read
        # in its own system.
        constraints = {}
        if infer_constraints is not None:
            try:
                ns_c = infer_constraints(board, ns_system)
                ew_c = infer_constraints(board, ew_system)
                for s in Seat:
                    constraints[s] = ns_c[s] if s.is_ns() else ew_c[s]
            except Exception:
                constraints = {}

        layout = known_layout(board, visible)
        rem = layout["rem_ranks"]

        # System header.
        self.systems_lbl.setText(
            f"N/S: {_sys_name(ns_system)}   E/W: {_sys_name(ew_system)}")

        # Danger hand / knock-out entry — used by grids and the plan.
        danger = (danger_info(board, contract, declarer, visible, layout,
                              constraints) if self.detail != BEGINNER else None)
        # Squeeze threats / busy defender (Expert).
        squeeze = (squeeze_threats(board, declarer, dummy, visible, layout, trump)
                   if self.detail == EXPERT else None)

        # Per-(seat,suit) defensive-signal reads + probabilistic honour hints —
        # surfaced in each defender's OTHER column so the right-hand side tells
        # you what biq's carding has revealed, not just suit lengths.
        # Bayesian posterior over the hidden hands (Expert only — it's a Monte-
        # Carlo sampler). Feeds honour-location probabilities + the shape line.
        self._posterior = None
        zoom_hidden = self._zoom_seat is not None and self._zoom_seat not in visible
        if (self.detail == EXPERT or zoom_hidden) and not all(s in visible for s in Seat):
            try:
                hidden = [s for s in Seat if s not in visible]
                self._posterior = bayes_distribution(
                    board, visible, layout, constraints,
                    signal_weight=play_weight(
                        board, hidden, signal_likelihood(
                            board, declarer, trump, self._carding_for, hidden)))
            except Exception:
                self._posterior = None
        self._belief_ctx = (board, visible)

        sig_map: Dict[Tuple[Seat, Suit], dict] = {}
        hint_map: Dict[Tuple[Seat, Suit], str] = {}
        if self.detail != BEGINNER and contract is not None and declarer is not None:
            try:
                for r in signal_reads(board, declarer, trump,
                                      self._carding_for, visible):
                    sig_map[(r["seat"], r["suit"])] = r   # chronological → latest
            except Exception:
                sig_map = {}
            try:
                hint_map = _honour_hint_map(board, visible, layout,
                                            self._posterior)
            except Exception:
                hint_map = {}

        # Likely (most-probable) length per hidden seat/suit, from the posterior
        # — shown alongside the max range in each hand's Other column.
        shape_map: Dict[Tuple[Seat, Suit], int] = {}
        if self._posterior and self._posterior.get("length"):
            for s, suits in self._posterior["length"].items():
                for su, dist in suits.items():
                    if dist:
                        shape_map[(s, su)] = max(dist, key=lambda k: dist[k])

        # Hand grids.
        for s in Seat:
            self._render_hand(s, board, visible, layout, rem, trump,
                              constraints, declarer, dummy, danger, squeeze,
                              sig_map, hint_map, shape_map)

        # Book-grounded coaching note + no-peek whole-deal 13-trick plan,
        # surfaced inside the Plan panel. Computed from the SAME `visible` /
        # `layout` the rest of the view uses — never peeks. Lazy imports avoid
        # a circular import (coach_notes / whole_deal_plan import from here).
        self._coach_note_html = ""
        self._coach_plan_html = ""
        if contract is not None and declarer is not None:
            try:
                from ui import coach_notes
                from ui.whole_deal_plan import (whole_deal_plan,
                                                render_whole_deal_plan)
                self._coach_note_html = coach_notes.coaching_note(
                    board, declarer, "play", ns_system, ew_system,
                    visible, layout)
                # The whole-deal 13-trick plan is a start-of-play artifact —
                # show it only on the first trick; after that the coach note is
                # already next-card technique (the live panel facts cover the
                # rest), so the plan would just repeat stale guidance.
                if coach_notes.is_play_start(board):
                    plan = whole_deal_plan(board, declarer, declarer, dummy,
                                           contract, visible, layout)
                    self._coach_plan_html = render_whole_deal_plan(
                        plan, html=True)
            except Exception:
                self._coach_note_html = self._coach_note_html or ""

        # Panels.
        self._render_contract_panel(board, contract, declarer)
        self._render_plan_panel(board, contract, declarer, dummy, visible,
                                layout, trump, danger)
        self._render_count_panel(board, visible, layout, constraints, squeeze)
        self._render_coach_panel(board, contract, declarer, dummy, visible,
                                 layout, trump)
        self._render_centre(board, contract, declarer, trump)

    def _render_hand(self, seat, board, visible, layout, rem, trump,
                     constraints, declarer, dummy, danger=None, squeeze=None,
                     sig_map=None, hint_map=None, shape_map=None):
        w = self.hands[seat]
        sig_map = sig_map or {}
        hint_map = hint_map or {}
        shape_map = shape_map or {}
        role = ""
        accent = PANEL_LINE
        if declarer is not None and seat == declarer:
            role = " · Declarer"
            accent = ACC_GOLD
        elif dummy is not None and seat == dummy:
            role = " · Dummy"
            accent = ACC_GOLD
        elif declarer is not None and seat.is_ns() != declarer.is_ns():
            accent = ACC_GREEN          # a defender
        is_danger = bool(danger) and danger["seat"] == seat
        if is_danger:
            role += "  ⚠ danger"
            accent = ACC_RED
        w.set_accent(accent)
        if squeeze and seat in squeeze.get("busy", []):
            role += "  ◆ busy"
        n_left = layout["remaining_count"][seat]
        # HCP context: exact for a face-up hand, inferred range otherwise.
        hcp_txt = ""
        if seat in visible and board.hands.get(seat) is not None:
            hcp_txt = f"  {board.hands[seat].hcp()} HCP"
        elif self.detail != BEGINNER and constraints.get(seat) is not None:
            c = constraints[seat]
            if (c.hcp_min, c.hcp_max) != (0, 37):
                hcp_txt = f"  {c.hcp_min}-{c.hcp_max} HCP"
        w.set_title(f"{seat.name.title()}{role}  ({n_left} cards)" + hcp_txt)
        # Lower-right: original (13-card) shape, revealed as soon as it's known.
        try:
            w.set_shape(shape_html(
                original_shape(board, seat, visible, layout, constraints)))
        except Exception:
            w.set_shape("")

        for su in SUIT_ROWS:
            known = layout["known"][seat][su]
            boss = boss_rank(rem, su)
            wins = winners_in_hand(known, rem[su]) \
                if self.detail != BEGINNER else (
                    {boss} & set(known) if boss is not None else set())
            entries: Set[Rank] = set()
            stoppers: Set[Rank] = set()
            if self.detail != BEGINNER:
                # Entry: the access card to declarer's / dummy's hand.
                if seat in (declarer, dummy):
                    er = top_entry_rank(wins)
                    if er is not None:
                        entries = {er}
                # Stopper (NT only) — skip the master ace, whose gold already
                # signals control; badge the K/Q/J guards that add information.
                if trump is None:
                    sr = stopper_rank(known)
                    if sr is not None and sr != boss:
                        stoppers = {sr}
            knockout: Set[Rank] = set()
            if is_danger and danger.get("knockout") \
                    and danger["knockout"][0] == su:
                knockout = {danger["knockout"][1]}
            # Cross-hand communication entry: a low card that reaches the
            # PARTNER (declarer↔dummy) hand's master in this suit (Manley's
            # "transportation") — flagged when this hand has no winner of its own.
            cross: Set[Rank] = set()
            if self.detail == EXPERT and seat in (declarer, dummy):
                mate = dummy if seat == declarer else declarer
                if mate is not None and mate != seat and known:
                    b = boss_rank(rem, su)
                    if (b is not None and b in layout["known"][mate][su]
                            and b not in known):
                        cross = {max(known)}      # lowest card (highest value)
            inferred = layout.get("inferred", {}).get(seat, {}).get(su, set())
            # Losers (LTC) — marked for the DECLARING side, the counterpart of
            # the green sure-winner boxes.
            losers: Set[Rank] = set()
            if self.detail != BEGINNER and seat in (declarer, dummy):
                losers = suit_loser_ranks(known, rem[su]) - wins
            known_html = _render_known(known, su, wins, boss, entries,
                                       stoppers, knockout, cross, inferred,
                                       losers)
            other_html = self._other_for(seat, su, visible, layout,
                                         constraints, sig_map, hint_map,
                                         shape_map)
            w.set_row(su, known_html, other_html, trump == su)

    def _floating_ranks(self, su, visible, layout):
        """Ranks of `su` still in play that are NOT pinned to a specific hand —
        i.e. cards known to be in one of the hidden hands but not which one.
        Returned high→low (ACE first)."""
        rem = layout["rem_ranks"].get(su, set())
        held = set()
        for s in Seat:                       # visible holdings + hidden inferred
            held |= set(layout["known"][s][su])
        return sorted(rem - held)

    def _other_for(self, seat, su, visible, layout, constraints,
                   sig_map=None, hint_map=None, shape_map=None) -> str:
        if self.detail == BEGINNER:
            # Beginners: only proven voids, nothing speculative.
            if seat not in visible and su in layout["voids"][seat]:
                return "<span style='color:#9fb6cc'>void</span>"
            return ""
        sig_map = sig_map or {}
        hint_map = hint_map or {}
        shape_map = shape_map or {}
        # Max length range, then the likely (Bayesian most-probable) count.
        rng = suit_length_text(seat, su, visible, layout, constraints)
        txt = f"<span style='color:#9fb6cc'>{rng}</span>"
        likely = shape_map.get((seat, su))
        if likely is not None and seat not in visible and str(likely) != rng:
            txt += (f" <span style='color:{ACC_BLUE};font-size:13px'>"
                    f"≈{likely}</span>")
        parts = [txt]
        # Outstanding cards whose hidden hand isn't pinned down yet — they could
        # be in THIS hand or the other hidden hand. Show the actual ranks (not
        # just a length range) so you can see whether your spot beats them.
        # Capped so early-hand cells (many floaters) stay readable.
        if seat not in visible and su not in layout["voids"][seat]:
            floating = self._floating_ranks(su, visible, layout)
            if 1 <= len(floating) <= 6:
                glyphs = "".join(r.to_char() for r in floating)
                other = [s for s in Seat if s not in visible and s != seat]
                where = other[0].to_char() if len(other) == 1 else "the other hand"
                parts.append(
                    f"<span style='color:{ACC_PURPLE}' "
                    f"title='Outstanding {su.symbol()}{glyphs} — in this hand "
                    f"or {where}'>↔{glyphs}</span>")
        # What biq's defensive carding has told us about this defender's suit.
        rd = sig_map.get((seat, su))
        if rd is not None:
            parts.append(_signal_chip(rd))
        # Colour-coded missing honours likely in this hand (proven ones already
        # show in the Known column; exact % stays in the Count/Honours panel).
        hint = hint_map.get((seat, su))
        if hint:
            parts.append(hint)
        return " ".join(parts)

    def _render_contract_panel(self, board, contract, declarer):
        if contract is None:
            self.p_contract.set_html("Auction in progress.")
            return
        dec = declarer.to_char() if declarer else (
            contract.declarer.to_char() if contract.declarer else "?")
        dt = getattr(board, "declarer_tricks", 0)
        df = getattr(board, "defense_tricks", 0)
        need = max(0, contract.target_tricks() - dt)
        # The race (Bayone/Manley/Frey): both sides have a target; show the gap.
        def_need = max(0, (14 - contract.target_tricks()) - df)
        vul = getattr(getattr(board, "vulnerability", None), "name", "")
        self.p_contract.set_html(
            f"<b>{contract.to_str()}</b> by {dec}<br>"
            f"Declarer tricks: <b>{dt}</b>　Defence: <b>{df}</b><br>"
            f"<b>The race:</b> declarer needs <b>{need}</b> more, "
            f"defence needs <b>{def_need}</b> to beat it.<br>"
            f"<span style='color:#9fb6cc'>Vul: {vul}</span>")

    def _render_plan_panel(self, board, contract, declarer, dummy, visible,
                           layout, trump, danger=None):
        if contract is None or declarer is None:
            self.p_plan.set_html("")
            return
        lines = []
        # Coaching note (book-grounded, system/vuln/card-specific) leads the
        # panel; the whole-deal 13-trick plan is appended at the end.
        coach_note = getattr(self, "_coach_note_html", "")
        if coach_note:
            lines.append(f"<b style='color:{ACC_GOLD}'>Coach:</b> {coach_note}")
        side = (declarer, dummy) if dummy is not None else None
        if side is not None:
            tt = side_top_tricks(board, side, visible, layout)
            if tt is not None:
                lines.append(f"Top tricks (declaring side): <b>{tt}</b>")
        # Loser label — names which suits the red-boxed losers are in (declaring
        # side), so the marks are spelled out, not just coloured.
        rem = layout["rem_ranks"]
        per_suit = []
        ltot = 0
        for su in SUIT_ROWS:
            holds = [layout["known"][hs][su] for hs in (declarer, dummy)
                     if hs is not None and hs in visible]
            if not holds:
                continue
            longer = max(holds, key=len)     # combined losers ≈ the long hand
            n = len(suit_loser_ranks(longer, rem[su])
                    - winners_in_hand(longer, rem[su]))
            ltot += n
            if n:
                per_suit.append(f"{su.symbol()}{n}")
        if per_suit:
            lines.append(f"<span style='color:{ACC_RED}'>Losers: "
                         f"{' '.join(per_suit)}</span> (total {ltot})")
        if trump is None:
            lines.append("NT: count winners, develop the rest in your "
                         "longest suit — build before you cash.")
        else:
            if declarer in visible and board.hands.get(declarer):
                ltc = losing_trick_count({su: layout['known'][declarer][su]
                                          for su in SUIT_ROWS})
                lines.append(f"Declarer losers (LTC): <b>{ltc}</b>")
            # Reese §62: the second planning question in a suit contract.
            lines.append("<i>Shall I draw trumps? If not, why not?</i>")
            # Outstanding trumps + a-priori break (Klinger break-odds table).
            if declarer in visible and dummy is not None and dummy in visible:
                held = (len(layout['known'][declarer][trump]) +
                        len(layout['known'][dummy][trump]))
                out = max(0, len(layout['rem_ranks'][trump]) - held)
                odds = split_odds(out)
                if odds:
                    top = odds[0]
                    lines.append(f"Trumps out: <b>{out}</b> — likely "
                                 f"{top[0]} ({top[1]}%)")
                # Defenders' actual split, shown once it becomes CLEAR (a
                # defender shows out, or is face-up). The "count the hand"
                # payoff: when one defender is void the other holds them all.
                defs = [s for s in Seat if s not in (declarer, dummy)]
                voided = [d for d in defs if trump in layout['voids'][d]]
                facing = [d for d in defs if d in visible]
                if len(voided) == 1:
                    other = [d for d in defs if d not in voided][0]
                    lines.append(
                        f"<span style='color:{ACC_GREEN}'>Defenders' trumps: "
                        f"<b>{voided[0].to_char()}</b> void → "
                        f"<b>{other.to_char()}</b> holds all {out}.</span>")
                elif facing:
                    cnt = " · ".join(
                        (f"<b>{d.to_char()} {len(layout['known'][d][trump])}</b>"
                         if d in visible else f"{d.to_char()} ?") for d in defs)
                    lines.append(
                        f"<span style='color:{ACC_GREEN}'>Defenders' trumps: "
                        f"{cnt}</span>")
            # Master trump still out?
            boss_t = boss_rank(layout["rem_ranks"], trump)
            holder = _holder_of(layout, trump, boss_t, visible)
            if boss_t is not None and holder is not None and \
                    holder not in (declarer, dummy):
                lines.append(f"<span style='color:#ffcf6b'>Opponent holds "
                             f"the master trump ({trump.symbol()}"
                             f"{boss_t.to_char()}) — keep trump control.</span>")
            elif boss_t is not None:
                lines.append("Trump master is yours — draw trumps unless you "
                             "need a ruff/entry.")
        if self.detail != BEGINNER:
            entries = self._entry_summary(layout, visible, declarer, dummy)
            if entries:
                lines.append("Entries: " + entries)
        if danger is not None:
            ds = danger["seat"].to_char()
            if danger.get("mode") == "ruff":
                lines.append(
                    f"<span style='color:#ff9a9a'>Danger: <b>{ds}</b> is void "
                    f"in {danger['threat'].symbol()} and can ruff — draw "
                    f"trumps before conceding the lead.</span>")
            else:
                lines.append(
                    f"<span style='color:#ff9a9a'>Danger hand: <b>{ds}</b> "
                    f"(can run {danger['threat'].symbol()}) — keep off lead, "
                    f"don't finesse into {ds}.</span>")
            ko = danger.get("knockout")
            if ko is not None:
                lines.append(
                    f"<span style='color:#ff9a9a'>Knock out {ds}'s "
                    f"{ko[0].symbol()}{ko[1].to_char()} before "
                    f"{danger['threat'].symbol()} sets up.</span>")
        if self.detail != BEGINNER:
            hu = holdup_rule_of_seven(board, contract, declarer, dummy,
                                      visible, layout)
            if hu is not None:
                if hu["n_total"] <= 0:
                    lines.append(f"Hold-up: win {hu['suit'].symbol()}A now "
                                 f"(Rule of 7 = 0).")
                else:
                    lines.append(
                        f"Hold up {hu['suit'].symbol()}A — Rule of 7: "
                        f"7−{hu['combined']} = duck <b>{hu['n_total']}</b> "
                        f"(<b>{hu['remaining']}</b> left).")
        if self.detail == EXPERT:
            for h in finesse_hints(declarer, dummy, visible, layout):
                lines.append(f"<span style='color:#9fb6cc'>{h}</span>")
        coach_plan = getattr(self, "_coach_plan_html", "")
        if coach_plan:
            lines.append("<hr>" + coach_plan)
        self.p_plan.set_html("<br>".join(lines))

    def _entry_summary(self, layout, visible, declarer, dummy) -> str:
        """Suits where declarer/dummy hold the current master = a sure
        entry to that hand."""
        out = []
        rem = layout["rem_ranks"]
        for hand_seat, tag in ((declarer, "decl"), (dummy, "dummy")):
            if hand_seat is None or hand_seat not in visible:
                continue
            suits = []
            for su in SUIT_ROWS:
                b = boss_rank(rem, su)
                if b is not None and b in layout["known"][hand_seat][su]:
                    suits.append(su.symbol())
            if suits:
                out.append(f"{tag} {''.join(suits)}")
        return "; ".join(out)

    def _render_count_panel(self, board, visible, layout, constraints,
                            squeeze=None):
        if self.detail == BEGINNER:
            self.p_count.set_html(
                "<span style='color:#9fb6cc'>Counting aids appear at "
                "Intermediate / Expert detail.</span>")
            return
        blocks = []
        # Unseen-HCP databank (Klinger, Better Bridge With a Better Memory,
        # ARCH-C: "40 − visible HCP = the points in the two hidden hands").
        unseen = 40 - visible_hcp(board, visible)
        parts = [f"Unseen HCP <b>{unseen}</b>"]
        for s in [s for s in Seat if s not in visible]:
            c = constraints.get(s) if constraints else None
            if c is not None and (c.hcp_min, c.hcp_max) != (0, 37):
                parts.append(f"{s.to_char()}&nbsp;{c.hcp_min}-{c.hcp_max}")
            else:
                parts.append(f"{s.to_char()}&nbsp;?")
        blocks.append(" · ".join(parts))
        if self.detail != EXPERT:
            self.p_count.set_html("<br>".join(blocks))
            return
        rows = ["<table cellspacing='3'>"]
        rows.append("<tr><td></td>" +
                    "".join(f"<td><b>{su.symbol()}</b></td>" for su in SUIT_ROWS) +
                    "<td>cards</td></tr>")
        for s in Seat:
            cells = []
            for su in SUIT_ROWS:
                cells.append(f"<td>{suit_length_text(s, su, visible, layout, constraints)}</td>")
            rows.append(f"<tr><td><b>{s.to_char()}</b></td>" + "".join(cells) +
                        f"<td>{layout['remaining_count'][s]}</td></tr>")
        rows.append("</table>")
        post = getattr(self, "_posterior", None)
        # Honour placement (Bayesian probabilities when the posterior is ready).
        hp = honour_placement(board, visible, layout, post)
        if hp:
            parts = []
            for su, r, label in hp:
                parts.append(f"{su.symbol()}{r.to_char()}: {label}")
            tag = (f" <span style='color:{ACC_BLUE};font-size:12px'>"
                   f"(Bayes, {post['samples']} worlds)</span>" if post else "")
            rows.append("<br><b>Missing honours</b>" + tag + "<br>" +
                        " · ".join(parts))
        # Most-likely shape of each hidden hand, from the posterior.
        if post and post.get("length"):
            shp = []
            for s in [s for s in Seat if s not in visible]:
                lt = post["length"].get(s)
                if not lt:
                    continue
                best = []
                for su in SUIT_ROWS:
                    dist = lt.get(su) or {}
                    if dist:
                        ln = max(dist, key=lambda k: dist[k])
                        best.append(str(ln))
                    else:
                        best.append("?")
                shp.append(f"{s.to_char()} {'-'.join(best)}")
            if shp:
                rows.append("<br><b>Likely shape</b> "
                            "<span style='color:#9fb6cc;font-size:12px'>"
                            "(♠♥♦♣)</span><br>" + " · ".join(shp))
        # Squeeze threats / busy defender.
        if squeeze and squeeze.get("threats"):
            tl = " ".join(f"{su.symbol()}({d.to_char()})"
                          for su, d in squeeze["threats"].items())
            line = "<br><b>Threats</b> " + tl
            stype = squeeze.get("type")
            if stype:
                line += (f" <span style='color:{ACC_PURPLE}'>[{stype}]</span>")
            if squeeze.get("busy"):
                bb = "/".join(d.to_char() for d in squeeze["busy"])
                line += (f" — <span style='color:#e8b06a'>{bb} is busy</span>")
            # Rectified-count state — does the squeeze operate now?
            rect = squeeze.get("rectified")
            losers = squeeze.get("losers")
            if rect is True:
                line += (f"<br><span style='color:{ACC_GREEN}'>Count rectified "
                         f"(1 loser left) — squeeze operates now.</span>")
            elif rect is False and losers is not None:
                line += (f"<br><span style='color:#e8b06a'>Rectify the count: "
                         f"duck {max(0, losers - 1)} more (──{losers} losers"
                         f" left).</span>")
            rows.append(line)
        blocks.append("".join(rows))
        self.p_count.set_html("<br>".join(blocks))

    def _render_coach_panel(self, board, contract, declarer, dummy, visible,
                            layout, trump):
        blocks = []

        # --- Opening-lead inference (Rule of 11) ---------------------------
        if self.detail != BEGINNER and contract is not None:
            r11 = rule_of_eleven(board, visible)
            if r11 is not None:
                blocks.append(
                    f"<b>Lead</b> {_card_glyph(r11['card'])} "
                    f"<span style='color:#9fb6cc;font-size:12px'>(4th-best?)</span>"
                    f"<br>Rule of 11: {r11['higher']} higher out; you see "
                    f"{r11['seen']} → <b>{r11['hidden']}</b> in the hidden "
                    f"hand(s).")

        # --- Live defensive signal reads -----------------------------------
        if contract is not None and declarer is not None:
            ns_c = self.ns_carding_combo.currentText()
            ew_c = self.ew_carding_combo.currentText()
            reads = signal_reads(board, declarer, trump, self._carding_for,
                                 visible)
            cur = current_trick_reads(board, declarer, trump, self._carding_for)
            limit = {BEGINNER: 2, INTERMEDIATE: 6}.get(self.detail, 99)
            lines = []
            for r in reads[-limit:]:
                # Discard, Smith, ruff-lead and trump-echo reads name their suit.
                suffix = ("" if (r.get("kind") in ("dsc", "smith", "ruflead")
                                 or r.get("tag") in ("ruff", "te"))
                          else f" {r['suit'].symbol()}")
                # Honesty marker — only meaningful when the signaller is face-up
                # (review / Show All). ✗ flags a false-card; ✓ (Expert) confirms.
                mark = ""
                if r.get("honest") is False:
                    if r.get("expected") is not None:
                        mark = (f" <span style='color:#ff9a9a'>✗ false "
                                f"(→ {_card_glyph(r['expected'])})</span>")
                    else:    # suit-preference false-card — no single honest card
                        mark = " <span style='color:#ff9a9a'>✗ false</span>"
                elif r.get("honest") is True and self.detail == EXPERT:
                    mark = " <span style='color:#7ad17a'>✓</span>"
                lines.append(
                    f"T{r['trick'] + 1} <b>{r['seat'].to_char()}</b> "
                    f"{_card_glyph(r['card'])} → {r['meaning']}"
                    f"{suffix} {_tag_html(r['tag'])}{mark}")
            for seat, r in cur.items():
                lines.append(
                    f"now <b>{seat.to_char()}</b> {_card_glyph(r['card'])} → "
                    f"{r['meaning']} {_tag_html(r['tag'])}")
            head = (f"<b>Signals</b> "
                    f"<span style='color:#9fb6cc;font-size:12px'>"
                    f"(N/S {ns_c} · E/W {ew_c})</span>")
            if lines:
                blocks.append(head + "<br>" + "<br>".join(lines))
            elif self.detail != BEGINNER:
                blocks.append(head + "<br><span style='color:#7f96ac'>"
                              "no clear spot-card signals yet</span>")

        tips = []
        n_played = len(layout["all_played"])
        if contract is None:
            tips.append("Bidding in progress — note each call's meaning.")
        elif n_played == 0:
            if trump is None:
                tips.append("Count your sure tricks, then plan to develop "
                            "the extra ones before touching an honour.")
            else:
                tips.append("Count your losers first. Then ask: draw trumps "
                            "now, or ruff / set up a suit first?")
        else:
            boss_t = boss_rank(layout["rem_ranks"], trump) if trump else None
            holder = _holder_of(layout, trump, boss_t, visible) if trump else None
            if trump and boss_t is not None and holder is not None and \
                    holder not in (declarer, dummy):
                tips.append("An opponent still holds the master trump — don't "
                            "draw the last trump if you need it.")
            else:
                tips.append("Keep counting: track each suit's split as cards "
                            "appear, and watch the discards.")
        # Defender signalling note (intermediate+).
        if self.detail != BEGINNER:
            tips.append("<span style='color:#9fb6cc'>Carding: attitude on "
                        "partner's suit, count on declarer's, suit-preference "
                        "on discards. Set each pair's agreement in the header."
                        "</span>")
        if self.detail == EXPERT and trump is None and contract is not None:
            tips.append("<span style='color:#9fb6cc'>Squeeze check (BLUE): "
                        "both threats with one defender, rectify the count, "
                        "threat in the upper hand, entry to it.</span>")
        if tips:
            blocks.append("<br>".join(f"• {t}" for t in tips))
        self.p_coach.set_html("<hr style='border:0;border-top:1px solid #243447'>"
                              .join(blocks))

    def _render_centre(self, board, contract, declarer, trump):
        ct = getattr(board, "current_trick", None)
        played = {}
        leader = None
        if ct is not None:
            cards = getattr(ct, "cards", None) or []
            leader = getattr(ct, "leader", None)
            if leader is not None and cards:
                base = int(leader)
                for i, c in enumerate(cards):
                    played[Seat((base + i) % 4)] = c
        # Live signal tags on the defenders' cards in this very trick.
        cur_reads = current_trick_reads(board, declarer, trump,
                                        self._carding_for)
        def cell(seat):
            c = played.get(seat)
            if c is None:
                return "·"
            col = _suit_color(c.suit)
            html = (f"<span style='color:{col}; font-weight:bold'>"
                    f"{c.suit.symbol()}{c.rank.to_char()}</span>")
            r = cur_reads.get(seat)
            if r is not None:
                html += "<br>" + _tag_html(r["tag"])
            return html
        on_lead = ""
        if leader is not None and len(played) < 4:
            nxt = Seat((int(leader) + len(played)) % 4)
            on_lead = f"<br><span style='color:#9fb6cc'>On lead/to play: " \
                      f"{nxt.name.title()}</span>"
        html = (
            f"<div align='center'>"
            f"<table cellpadding='4' align='center'>"
            f"<tr><td></td><td align='center'>{cell(Seat.NORTH)}</td><td></td></tr>"
            f"<tr><td align='center'>{cell(Seat.WEST)}</td><td></td>"
            f"<td align='center'>{cell(Seat.EAST)}</td></tr>"
            f"<tr><td></td><td align='center'>{cell(Seat.SOUTH)}</td><td></td></tr>"
            f"</table>{on_lead}</div>")
        self.centre.set_html(html)


# ---------------------------------------------------------------------------
# small render helpers
# ---------------------------------------------------------------------------

def _render_known(ranks_high_to_low: List[Rank], suit: Suit,
                  winners: Set[Rank], boss: Optional[Rank],
                  entries: Optional[Set[Rank]] = None,
                  stoppers: Optional[Set[Rank]] = None,
                  knockout: Optional[Set[Rank]] = None,
                  cross: Optional[Set[Rank]] = None,
                  inferred: Optional[Set[Rank]] = None,
                  losers: Optional[Set[Rank]] = None) -> str:
    if not ranks_high_to_low:
        return "<span style='color:#5f7689'>—</span>"
    entries = entries or set()
    stoppers = stoppers or set()
    knockout = knockout or set()
    cross = cross or set()
    inferred = inferred or set()
    losers = losers or set()
    col = _suit_color(suit)
    parts = []
    for r in ranks_high_to_low:
        ch = r.to_char()
        style = f"color:{col};"
        if boss is not None and r == boss:
            style += (f"background:{MASTER_BG}; color:#06121f; font-weight:bold;"
                      f" border-radius:3px; padding:0 3px;")
        elif r in winners:
            # sure winner — vivid green box (was a faint underline)
            style += (f"color:{col}; border:1px solid {ACC_GREEN};"
                      f" border-radius:3px; padding:0 2px; font-weight:bold;")
        elif r in losers:
            # a loser (LTC) — red box, the counterpart of the green winner box
            style += (f"color:{col}; border:1px solid {ACC_RED};"
                      f" border-radius:3px; padding:0 2px;")
        elif r in inferred:
            # PLACED by deduction (hand is hidden but this card must be here) —
            # dashed outline so it reads as "known, not seen".
            style += (f"color:{col}; border:1px dashed {ACC_YELLOW};"
                      f" border-radius:3px; padding:0 2px;")
        # Vivid inverse-video role chips replace the old superscripts.
        badge = ""
        if r in entries:        # access to this hand
            badge += _chip("E", ACC_GREEN)
        if r in stoppers:       # guards the suit (NT)
            badge += _chip("S", ACC_BLUE)
        if r in knockout:       # danger hand's entry to knock out
            badge += _chip("KO", ACC_RED)
        if r in cross:          # low card that reaches partner's winner
            badge += _chip("→", ACC_ORANGE)
        parts.append(f"<span style='{style}'>{ch}</span>{badge}")
    return " ".join(parts)


# A-priori vacant-space split odds for N cards missing (rounded %). Used for
# the "how will this suit break?" chip (Klinger, Bridge for a Better Memory,
# p34: "odd numbers break evenly, even break oddly"; Bayone ch.28).
_SPLIT_ODDS = {
    2: [("1-1", 52), ("2-0", 48)],
    3: [("2-1", 78), ("3-0", 22)],
    4: [("3-1", 50), ("2-2", 40), ("4-0", 10)],
    5: [("3-2", 68), ("4-1", 28), ("5-0", 4)],
    6: [("4-2", 48), ("3-3", 36), ("5-1", 15), ("6-0", 1)],
    7: [("4-3", 62), ("5-2", 31), ("6-1", 7)],
}


def split_odds(missing: int):
    return _SPLIT_ODDS.get(missing)


def visible_hcp(board, visible) -> int:
    tot = 0
    for s in visible:
        h = board.hands.get(s) if board.hands else None
        if h is not None:
            tot += h.hcp()
    return tot


def rule_of_eleven(board, visible):
    """If the hand opened with a spot lead (treated as 4th-best), return the
    Rule-of-11 read: {suit, card, higher, seen, hidden}. `higher` = cards above
    the lead in the other three hands (11 − pip); `seen` = those visible to us
    (excluding the leader); `hidden` = the rest, i.e. in the concealed hands.

    ACBL Encyclopedia "Rule of Eleven"; Sheinwold 29th Day. Only meaningful for
    a genuine 4th-best spot lead, so we fire only on a spot of 9 or below."""
    tricks = getattr(board, "tricks", None) or []
    ct = getattr(board, "current_trick", None)
    lead = leader = None
    if tricks and (getattr(tricks[0], "cards", None)):
        lead, leader = tricks[0].cards[0], tricks[0].leader
    elif not tricks and ct is not None and (getattr(ct, "cards", None)):
        lead, leader = ct.cards[0], ct.leader
    if lead is None or leader is None:
        return None
    pip = 14 - int(lead.rank)            # A=14 … 2=2
    if pip > 9:                          # honour / 10 — not a clean 4th-best read
        return None
    higher = 11 - pip
    if higher <= 0:
        return None
    seen = 0
    for s in visible:
        if s == leader:
            continue                     # Rule of 11 counts the OTHER three hands
        h = board.hands.get(s) if board.hands else None
        if not h:
            continue
        seen += sum(1 for c in h.cards
                    if c.suit == lead.suit and (14 - int(c.rank)) > pip)
    return {"suit": lead.suit, "card": lead, "higher": higher,
            "seen": seen, "hidden": max(0, higher - seen)}


def _opening_leader(board, declarer: Seat) -> Seat:
    tricks = getattr(board, "tricks", None) or []
    if tricks and getattr(tricks[0], "leader", None) is not None:
        return Seat(int(tricks[0].leader))
    ct = getattr(board, "current_trick", None)
    if not tricks and ct is not None and getattr(ct, "leader", None) is not None:
        return Seat(int(ct.leader))
    return Seat((int(declarer) + 1) % 4)            # LHO by default


def _threat_suit(board, declarer: Seat) -> Optional[Suit]:
    """The defenders' threat suit at NT = the opening-lead suit (their long
    suit). None until a defender has led."""
    tricks = getattr(board, "tricks", None) or []
    lead = None
    if tricks and getattr(tricks[0], "cards", None):
        if getattr(tricks[0], "leader", None) is None:
            return None
        if Seat(int(tricks[0].leader)).is_ns() == declarer.is_ns():
            return None
        lead = tricks[0].cards[0]
    else:
        ct = getattr(board, "current_trick", None)
        if not tricks and ct is not None and getattr(ct, "cards", None) \
                and getattr(ct, "leader", None) is not None \
                and Seat(int(ct.leader)).is_ns() != declarer.is_ns():
            lead = ct.cards[0]
    return lead.suit if lead is not None else None


def _est_length(seat, suit, visible, layout, constraints) -> float:
    """Best estimate of a seat's length in a suit (exact if face-up)."""
    if seat in visible:
        return float(len(layout["known"][seat][suit]))
    if suit in layout["voids"][seat]:
        return 0.0
    lo, hi = 0, 13
    c = constraints.get(seat) if constraints else None
    if c is not None:
        lo, hi = c.suit_len.get(suit.value, (0, 13))
    known = len(layout["known"][seat][suit])
    lo = max(lo, known)
    hi = min(hi, layout["unseen_by_suit"].get(suit, 0) + known)
    if lo > hi:
        lo = hi
    return (lo + hi) / 2.0


def _knockout_entry(layout, danger_seat, threat, visible):
    """A PROVEN side-suit entry in the danger hand to knock out before the
    threat suit sets up: an Ace (sure), or a King if the hand is face-up.
    Returns (suit, rank) or None."""
    for su in SUIT_ROWS:
        if su == threat:
            continue
        if Rank.ACE in layout["known"][danger_seat][su]:
            return (su, Rank.ACE)
    if danger_seat in visible:
        for su in SUIT_ROWS:
            if su == threat:
                continue
            if Rank.KING in layout["known"][danger_seat][su]:
                return (su, Rank.KING)
    return None


def danger_info(board, contract, declarer, visible, layout, constraints):
    """At NT, the danger hand (the defender who can run the threat suit) and the
    entry to knock out. Returns {seat, threat, knockout, leader} or None.

    Danger hand = the defender longer in the threat suit (the opening-lead
    suit); ties default to the opening leader, who led from length."""
    if contract is None or declarer is None:
        return None
    if contract.suit != Suit.NOTRUMP:
        # Suit contract: the danger is a RUFF — a defender void in a side suit
        # who still has trumps. Remedy is to draw trumps, so no knock-out entry.
        trump = contract.suit
        if trump in layout["voids"]:                 # sanity
            pass
        out_trumps = bool(layout["rem_ranks"].get(trump))
        if not out_trumps:
            return None                              # trumps gone — no ruff danger
        for d in [s for s in Seat if s.is_ns() != declarer.is_ns()]:
            if trump in layout["voids"][d]:
                continue                             # can't ruff (void in trumps)
            for su in SUIT_ROWS:
                if su == trump:
                    continue
                if su in layout["voids"][d]:         # proven side-suit void
                    return {"seat": d, "threat": su, "knockout": None,
                            "leader": _opening_leader(board, declarer),
                            "mode": "ruff"}
        return None
    threat = _threat_suit(board, declarer)
    if threat is None:
        return None
    leader = _opening_leader(board, declarer)
    defenders = [s for s in Seat if s.is_ns() != declarer.is_ns()]
    if len(defenders) != 2:
        return None
    a, b = defenders
    ea, eb = (_est_length(a, threat, visible, layout, constraints),
              _est_length(b, threat, visible, layout, constraints))
    if abs(ea - eb) < 0.5:
        ds = leader if leader in defenders else (a if ea >= eb else b)
    else:
        ds = a if ea > eb else b
    return {"seat": ds, "threat": threat,
            "knockout": _knockout_entry(layout, ds, threat, visible),
            "leader": leader, "mode": "run"}


def holdup_rule_of_seven(board, contract, declarer, dummy, visible, layout):
    """Rule of 7 (NT): the number of times to hold up the ace in the danger
    (opening-lead) suit = 7 − (combined declarer + dummy length in that suit),
    so the danger hand is exhausted of the suit. Returns a dict or None.

    Needs both declarer + dummy face-up and the declaring side to still hold the
    ace (the stopper being held up). Length is the ORIGINAL holding (current +
    cards already played), so the count is stable as the suit is played out."""
    if (contract is None or declarer is None or dummy is None
            or contract.suit != Suit.NOTRUMP):
        return None
    if declarer not in visible or dummy not in visible:
        return None
    threat = _threat_suit(board, declarer)
    if threat is None:
        return None
    if (Rank.ACE not in layout["known"][declarer][threat]
            and Rank.ACE not in layout["known"][dummy][threat]):
        return None                              # ace already played / not held
    played, _, _ = collect_play(board)

    def orig_len(seat):
        cur = len(layout["known"][seat][threat])
        gone = sum(1 for cid in played[seat] if Suit(cid // 13) == threat)
        return cur + gone

    combined = orig_len(declarer) + orig_len(dummy)
    n_total = max(0, 7 - combined)
    rounds = 0
    for t in (getattr(board, "tricks", None) or []):
        cards = getattr(t, "cards", None) or []
        if cards and cards[0].suit == threat:
            rounds += 1
    ct = getattr(board, "current_trick", None)
    if ct is not None and getattr(ct, "cards", None) \
            and ct.cards[0].suit == threat:
        rounds += 1
    return {"suit": threat, "combined": combined, "n_total": n_total,
            "rounds": rounds, "remaining": max(0, n_total - rounds)}


def _holder_of(layout, suit, rank, visible) -> Optional[Seat]:
    """Which seat is PROVEN to hold a given card now (or None)."""
    if rank is None:
        return None
    for s in Seat:
        if rank in layout["known"][s][suit]:
            return s
    return None


def _sys_name(system) -> str:
    if system is None:
        return "?"
    return getattr(system, "name", None) or str(system)


# ---------------------------------------------------------------------------
# Whole-deal instrumentation summary — a plain-text rundown of the deal through
# EVERY panel lens, for feeding to an external reviewer (Claude) so its
# commentary is grounded in biq's own instrumentation rather than generic.
# ---------------------------------------------------------------------------

def _review_carding_for():
    """carding_for callback using the configured Preferences → Carding defaults."""
    def f(seat: Seat) -> CardingConfig:
        name = _default_preset_name("ns" if seat.is_ns() else "ew")
        base = carding_preset(name)
        smith = {"Off": "off", "Standard": "standard",
                 "Reverse": "reverse"}.get(_default_smith(), "off")
        return replace(base, smith=smith)
    return f


def carding_text(cfg: CardingConfig, as_biq: bool = False) -> str:
    """Plain-English statement of one pair's carding agreement. `as_biq` adds
    the selective first-round rules biq's own defenders follow."""
    if not cfg.signals:
        return ("gives NO defensive signals: when it cannot win it plays its "
                "lowest card, so spot cards carry no message.")
    parts = [
        "attitude: " + ("a high spot encourages, a low spot discourages"
                        if cfg.attitude == "standard" else
                        "upside-down (a LOW spot encourages, a high spot "
                        "discourages)"),
        "count: " + ("high-low shows an even number, low-high an odd number"
                     if cfg.count == "standard" else
                     "upside-down (low-high shows an even number, high-low "
                     "an odd number)"),
        "discards: " + ("attitude about the suit thrown"
                        if cfg.discards == "attitude" else
                        "Lavinthal suit-preference (in a suit contract a "
                        "high spot asks for the higher-ranking of the other "
                        "two side suits, a low spot for the lower one or "
                        "shows no preference; at notrump the same, between "
                        "the two highest-ranking other suits)"),
        "trumps: " + ("trump echo (high-low in trumps shows three)"
                      if cfg.trump_echo != "off" else
                      "ordinary count, no trump echo"),
        "Smith echo: " + {"off": "not played",
                          "standard": "on (high on declarer's first suit "
                                      "in NT = liked the opening lead)",
                          "reverse": "reverse (low = liked the opening "
                                     "lead)"}.get(cfg.smith, "not played"),
    ]
    out = "; ".join(parts) + "."
    if as_biq:
        out += (" biq signals selectively and on the FIRST round of a suit "
                "only: attitude when partner leads, encouraging only with "
                "the honour that matters (after the ace, the king; after "
                "the king, the ace or queen, or the jack from J-x / J-x-x; "
                "after a spot lead, the A, K or Q); count when declarer "
                "leads, and only while the suit's ace or king is out of "
                "sight (not in dummy, not yet played); a high count card "
                "keeps the 9 back (9-7 shows with the 7) except in fourth "
                "seat. Otherwise it plays its lowest spot, which means no "
                "signal. Honours played to a trick and cards that could win "
                "the trick are not signals.")
    return out


def signalling_conventions_text() -> str:
    """The defensive carding each pair uses (Preferences → Carding, the same
    agreements the analysis view decodes with), for Claude prompts."""
    smith = {"Off": "off", "Standard": "standard",
             "Reverse": "reverse"}.get(_default_smith(), "off")
    names = {side: _default_preset_name(side) for side in ("ns", "ew")}
    sides = ([("ns", "Both pairs")] if names["ns"] == names["ew"]
             else [("ns", "N/S"), ("ew", "E/W")])
    lines = []
    for side, label in sides:
        name = names[side]
        cfg = replace(carding_preset(name), smith=smith)
        lines.append(f"  • {label} ({name}): "
                     f"{carding_text(cfg, as_biq=(name == AS_BIQ_PLAYS))}")
    return ("DEFENSIVE SIGNALLING CONVENTIONS (the carding agreements in use; "
            "read every defender's spot card through its OWN pair's "
            "agreement, and draw your inferences about the defenders' "
            "unseen cards from them):\n" + "\n".join(lines))


def instrumentation_summary(original_hands, played_board, contract,
                            declarer, dummy) -> str:
    """Build a structured, plain-text instrumentation rundown of a COMPLETED
    deal, covering each feature of the instrumented view:
    counting, the declarer plan (top tricks, losers/LTC, draw-trumps, trump
    split, entries, hold-up, danger hand, finesses), squeeze ingredients, the
    opening-lead Rule-of-11 read, and the full defensive-signal log with
    honesty. `original_hands` is the deal as dealt; `played_board` carries the
    completed tricks. Robust: any section that can't be computed is skipped."""
    out: List[str] = []

    def sec(title):
        out.append("")
        out.append(title)

    allv = set(Seat)
    trump = contract.suit if (contract and contract.suit != Suit.NOTRUMP) else None

    # Board (as dealt) for the static counting / planning lenses.
    ob = None
    try:
        ob = BoardState(
            board_number=getattr(played_board, "board_number", 0),
            dealer=getattr(played_board, "dealer", Seat.NORTH),
            vulnerability=getattr(played_board, "vulnerability", None),
            hands={s: original_hands[s] for s in Seat})
        if contract is not None:
            ob.contract = contract
    except Exception:
        ob = None

    # 1. COUNTING — HCP + shape of every hand.
    try:
        sec("COUNTING (deal as dealt):")
        for s in Seat:
            h = original_hands[s]
            shp = "-".join(str(sum(1 for c in h.cards if c.suit == su))
                           for su in SUIT_ROWS)
            out.append(f"  {s.to_char()}: {h.hcp()} HCP  shape {shp} (S-H-D-C)")
    except Exception:
        pass

    # 2. DECLARER PLAN — top tricks, losers, draw-trumps, trump split, entries,
    #    hold-up, danger hand, finesses (all from the as-dealt layout).
    if ob is not None and declarer is not None:
        try:
            layout = known_layout(ob, allv)
            rem = layout["rem_ranks"]
            sec("DECLARER PLAN:")
            if dummy is not None:
                tt = side_top_tricks(ob, (declarer, dummy), allv, layout)
                if tt is not None:
                    out.append(f"  Top tricks (declaring side): {tt}")
            ltc = losing_trick_count({su: layout['known'][declarer][su]
                                      for su in SUIT_ROWS})
            out.append(f"  Declarer LTC: {ltc}")
            # losers per suit (declaring side; combined ≈ the long hand)
            per = []
            for su in SUIT_ROWS:
                holds = [layout['known'][hs][su] for hs in (declarer, dummy)
                         if hs is not None]
                longer = max(holds, key=len) if holds else []
                n = len(suit_loser_ranks(longer, rem[su])
                        - winners_in_hand(longer, rem[su]))
                if n:
                    per.append(f"{su.symbol()}{n}")
            if per:
                out.append(f"  Losers by suit: {' '.join(per)}")
            if trump is None:
                out.append("  Strain: notrump — count winners, develop the long suit.")
            else:
                out.append(f"  Trump: {trump.symbol()}. Draw trumps unless a ruff/"
                           f"entry needs them first.")
                if dummy is not None:
                    held = (len(layout['known'][declarer][trump]) +
                            len(layout['known'][dummy][trump]))
                    o = max(0, len(rem[trump]) - held)
                    odds = split_odds(o)
                    if odds:
                        out.append(f"  Trumps out: {o} — a-priori {odds[0][0]} "
                                   f"{odds[0][1]}%")
                    defs = [s for s in Seat if s not in (declarer, dummy)]
                    out.append("  Defenders' trumps: " + " ".join(
                        f"{d.to_char()}={len(layout['known'][d][trump])}"
                        for d in defs))
            ent = []
            for hs, tag in ((declarer, "declarer"), (dummy, "dummy")):
                if hs is None:
                    continue
                suits = [su.symbol() for su in SUIT_ROWS
                         if boss_rank(rem, su) in layout['known'][hs][su]]
                if suits:
                    ent.append(f"{tag} {''.join(suits)}")
            if ent:
                out.append("  Entries (hold the master): " + "; ".join(ent))
            hu = holdup_rule_of_seven(ob, contract, declarer, dummy, allv, layout)
            if hu is not None and hu.get("n_total", 0) > 0:
                out.append(f"  Hold-up (Rule of 7): duck {hu['n_total']} in "
                           f"{hu['suit'].symbol()}")
            dg = danger_info(ob, contract, declarer, allv, layout, {})
            if dg is not None:
                out.append(f"  Danger hand: {dg['seat'].to_char()} "
                           f"(threat {dg['threat'].symbol()})")
            for h in finesse_hints(declarer, dummy, allv, layout):
                out.append(f"  Finesse: {h}")
            sq = squeeze_threats(ob, declarer, dummy, allv, layout, trump)
            if sq and sq.get("threats"):
                tl = " ".join(f"{su.symbol()}({d.to_char()})"
                              for su, d in sq["threats"].items())
                line = f"  Squeeze: threats {tl}"
                if sq.get("type"):
                    line += f" [{sq['type']}]"
                if sq.get("busy"):
                    line += " busy " + "/".join(d.to_char() for d in sq["busy"])
                out.append(line)
        except Exception:
            pass

    # 3. OPENING LEAD — Rule of 11 read.
    try:
        r11 = rule_of_eleven(played_board, allv)
        if r11 is not None:
            sec("OPENING LEAD:")
            out.append(f"  {r11['card'].suit.symbol()}{r11['card'].rank.to_char()}"
                       f" as 4th-best → Rule of 11: {r11['higher']} higher out.")
    except Exception:
        pass

    # 4. DEFENSIVE SIGNALS — the full read log with honesty (deal complete, so
    #    every signaller's holding is reconstructable).
    try:
        reads = signal_reads(played_board, declarer, trump,
                             _review_carding_for(), allv)
        if reads:
            sec("DEFENSIVE SIGNALS (what each card said):")
            for r in reads:
                suit = "" if r.get("kind") in ("dsc", "smith", "ruflead") \
                    or r.get("tag") in ("ruff", "te") else f" {r['suit'].symbol()}"
                mark = ""
                if r.get("honest") is False:
                    mark = " [FALSE-CARD]"
                elif r.get("honest") is True:
                    mark = " [honest]"
                out.append(
                    f"  T{r['trick'] + 1} {r['seat'].to_char()} "
                    f"{r['card'].suit.symbol()}{r['card'].rank.to_char()} → "
                    f"{r['meaning']}{suit} ({r['tag']}){mark}")
    except Exception:
        pass

    return "\n".join(out).strip()
