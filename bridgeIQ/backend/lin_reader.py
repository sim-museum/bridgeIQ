"""
LIN file reader (BBO vugraph format).

LIN is the de-facto standard format BBO uses to publish vugraph archives.
Each file is a sequence of pipe-delimited tag/value pairs; the same record
holds the deal, the auction, and the play, so a parsed LIN deal carries
enough information to rebuild a BenBoardRun and ingest it as a closed
room into the score sheet.

Only the tags we need are interpreted; unknown ones are ignored. This is
not a complete LIN parser — it handles the read paths we actually use:
  qx  board+room marker (e.g. ``o1`` = open table board 1, ``c1`` = closed)
  md  the deal (dealer + four hands, comma separated, suits in S/H/D/C order)
  sv  vulnerability (o/n/e/b → none / N-S / E-W / both)
  mb  one auction call
  pc  one card played
  pg  page break / trick separator
  rs  per-board result strings (e.g. ``3NS=,3CN=``)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    Bid, BenBoardRun, BenTable, BoardState, Card, Contract,
    Hand, Rank, Seat, Suit, Trick, Vulnerability
)
from .scoring import calculate_contract_score


# BBO LIN seats are numbered: 1=south, 2=west, 3=north, 4=east.
_LIN_SEAT_BY_NUM = {
    '1': Seat.SOUTH,
    '2': Seat.WEST,
    '3': Seat.NORTH,
    '4': Seat.EAST,
}

_LIN_VUL = {
    'o': Vulnerability.NONE,
    '0': Vulnerability.NONE,
    'n': Vulnerability.NS,
    'e': Vulnerability.EW,
    'b': Vulnerability.BOTH,
}

_RANK_BY_CHAR = {
    'A': Rank.ACE, 'K': Rank.KING, 'Q': Rank.QUEEN, 'J': Rank.JACK,
    'T': Rank.TEN, '9': Rank.NINE, '8': Rank.EIGHT, '7': Rank.SEVEN,
    '6': Rank.SIX, '5': Rank.FIVE, '4': Rank.FOUR, '3': Rank.THREE,
    '2': Rank.TWO,
}

_SUIT_BY_CHAR = {
    'S': Suit.SPADES, 'H': Suit.HEARTS,
    'D': Suit.DIAMONDS, 'C': Suit.CLUBS,
}


@dataclass
class LinDeal:
    """One deal lifted out of a LIN stream."""
    board_number: int = 0
    room: str = "open"           # "open" or "closed"
    dealer: Seat = Seat.NORTH
    vulnerability: Vulnerability = Vulnerability.NONE
    hands: Dict[Seat, Hand] = field(default_factory=dict)
    auction: List[Bid] = field(default_factory=list)
    tricks: List[Trick] = field(default_factory=list)
    contract: Optional[Contract] = None
    declarer: Optional[Seat] = None
    declarer_tricks: int = 0
    ns_score: int = 0
    ew_score: int = 0


def _parse_hand_string(text: str) -> Hand:
    """Parse a single LIN hand: 'SAKQ7HJ32D...C..'."""
    cards: List[Card] = []
    current_suit: Optional[Suit] = None
    for ch in text.strip():
        up = ch.upper()
        if up in _SUIT_BY_CHAR:
            current_suit = _SUIT_BY_CHAR[up]
        elif up in _RANK_BY_CHAR and current_suit is not None:
            cards.append(Card(current_suit, _RANK_BY_CHAR[up]))
    return Hand(cards)


def _parse_md(value: str) -> Tuple[Seat, Dict[Seat, Hand]]:
    """Parse the ``md`` tag: dealer digit + comma-separated hands.

    BBO records three or four hands; when only three are given the fourth
    is the complement of the rest of the deck. Hands are listed clockwise
    starting from the seat indicated by the leading digit.
    """
    if not value:
        return Seat.NORTH, {}
    head = value[0]
    rest = value[1:].rstrip(',')
    first_seat = _LIN_SEAT_BY_NUM.get(head, Seat.SOUTH)
    parts = [p for p in rest.split(',') if p]
    hands: Dict[Seat, Hand] = {}
    seat = first_seat
    for part in parts[:4]:
        hands[seat] = _parse_hand_string(part)
        seat = seat.next()

    # Fill the missing seat with whatever's left of the 52-card deck.
    # Card isn't hashable, so build the membership check on (suit, rank)
    # tuples and reconstitute Cards for the leftover set.
    if len(hands) == 3:
        all_pairs = {(s, r) for s in (Suit.SPADES, Suit.HEARTS,
                                       Suit.DIAMONDS, Suit.CLUBS)
                     for r in (Rank.TWO, Rank.THREE, Rank.FOUR, Rank.FIVE,
                               Rank.SIX, Rank.SEVEN, Rank.EIGHT, Rank.NINE,
                               Rank.TEN, Rank.JACK, Rank.QUEEN, Rank.KING,
                               Rank.ACE)}
        used = {(c.suit, c.rank) for h in hands.values() for c in h.cards}
        missing_seat = next(s for s in Seat if s not in hands)
        leftover = sorted(all_pairs - used,
                          key=lambda sr: (sr[0].value, sr[1].value))
        hands[missing_seat] = Hand([Card(s, r) for s, r in leftover])

    # BBO's dealer convention: the FIRST hand listed isn't necessarily
    # dealer — in older LIN dumps, the digit after `md|` is actually the
    # dealer, but in newer ones it's the first-hand seat. Treat it as
    # dealer; when it's wrong the auction parser still attributes calls
    # correctly (we replay starting from this seat).
    return first_seat, hands


def _parse_bid(token: str) -> Optional[Bid]:
    """Parse a LIN ``mb`` value into a Bid. ``!``-marked alerts are
    stripped before parsing."""
    t = token.split('!', 1)[0].strip().lower()
    if not t:
        return None
    if t == 'p':
        return Bid(is_pass=True)
    if t == 'd':
        return Bid(is_double=True)
    if t == 'r':
        return Bid(is_redouble=True)
    if len(t) >= 2 and t[0].isdigit():
        level = int(t[0])
        suit_char = t[1]
        if suit_char == 'n':
            return Bid(level=level, suit=Suit.NOTRUMP)
        if suit_char in 'shdc':
            return Bid(level=level, suit=_SUIT_BY_CHAR[suit_char.upper()])
    return None


def _parse_card(token: str) -> Optional[Card]:
    """Parse a LIN ``pc`` value (e.g. ``H4`` or ``hT``)."""
    t = token.strip().upper()
    if len(t) != 2:
        return None
    if t[0] not in _SUIT_BY_CHAR or t[1] not in _RANK_BY_CHAR:
        return None
    return Card(_SUIT_BY_CHAR[t[0]], _RANK_BY_CHAR[t[1]])


def _derive_contract_and_declarer(
    auction: List[Bid], dealer: Seat
) -> Tuple[Optional[Contract], Optional[Seat]]:
    """Walk the auction; the last non-pass/double bid sets the strain
    and level, and the first bidder of that strain on the same side is
    declarer."""
    if not auction:
        return None, None
    seat = dealer
    last_normal_idx = -1
    last_normal_bid: Optional[Bid] = None
    for i, b in enumerate(auction):
        if not (b.is_pass or b.is_double or b.is_redouble):
            last_normal_idx = i
            last_normal_bid = b
        seat = seat.next()
    if last_normal_bid is None:
        return None, None

    # Find first bidder of the winning strain on the winning side.
    winning_side_ns = None
    bidder_at_idx = []
    s = dealer
    for _ in auction:
        bidder_at_idx.append(s)
        s = s.next()
    last_bidder = bidder_at_idx[last_normal_idx]
    winning_side_ns = last_bidder.is_ns()

    declarer: Optional[Seat] = None
    for i, b in enumerate(auction[: last_normal_idx + 1]):
        if (b.is_pass or b.is_double or b.is_redouble
                or b.suit != last_normal_bid.suit):
            continue
        bidder = bidder_at_idx[i]
        if bidder.is_ns() == winning_side_ns:
            declarer = bidder
            break
    if declarer is None:
        declarer = last_bidder

    doubled = False
    redoubled = False
    for b in auction[last_normal_idx + 1:]:
        if b.is_double:
            doubled = True
            redoubled = False
        elif b.is_redouble:
            redoubled = True

    contract = Contract(
        level=last_normal_bid.level,
        suit=last_normal_bid.suit,
        doubled=doubled,
        redoubled=redoubled,
        declarer=declarer,
    )
    return contract, declarer


def _replay_tricks(
    cards_in_order: List[Card],
    contract: Optional[Contract],
    declarer: Optional[Seat],
) -> Tuple[List[Trick], int]:
    """Replay a flat list of plays into Trick objects.

    Returns (tricks, declarer_tricks). Each trick records its leader and
    winner. The opening leader is declarer.next(); subsequent leaders
    are last trick's winner.
    """
    if not cards_in_order or contract is None or declarer is None:
        return [], 0

    trump = contract.suit if contract.suit != Suit.NOTRUMP else None
    declarer_side_ns = declarer.is_ns()
    leader = declarer.next()
    tricks: List[Trick] = []
    declarer_tricks = 0

    i = 0
    while i + 4 <= len(cards_in_order):
        cards = cards_in_order[i:i + 4]
        # Determine winner: highest trump, else highest of led suit.
        lead_suit = cards[0].suit
        winning_idx = 0
        for j in range(1, 4):
            c = cards[j]
            w = cards[winning_idx]
            if trump is not None and c.suit == trump and w.suit != trump:
                winning_idx = j
            elif c.suit == w.suit and c.rank < w.rank:
                # Rank is IntEnum where ACE=0 is highest, so smaller value wins.
                winning_idx = j
        winner = Seat((leader.value + winning_idx) % 4)
        tricks.append(Trick(cards=cards, leader=leader, winner=winner))
        if winner.is_ns() == declarer_side_ns:
            declarer_tricks += 1
        leader = winner
        i += 4

    return tricks, declarer_tricks


def parse_lin_text(text: str) -> List[LinDeal]:
    """Parse a LIN string and return a list of LinDeal objects.

    The LIN stream may contain multiple boards back-to-back. Each is
    bounded implicitly by the next ``qx|`` marker (or end-of-stream).
    """
    tokens = text.replace('\r', '').replace('\n', '').split('|')
    # Tokens come in (key, value) pairs.
    pairs: List[Tuple[str, str]] = []
    i = 0
    while i + 1 < len(tokens):
        key = tokens[i].strip().lower()
        val = tokens[i + 1]
        if key:
            pairs.append((key, val))
        i += 2

    deals: List[LinDeal] = []
    current: Optional[LinDeal] = None
    play_buffer: List[Card] = []

    def _commit():
        nonlocal current, play_buffer
        if current is None:
            return
        # Derive contract from auction if we don't have it yet.
        if current.contract is None and current.auction:
            c, d = _derive_contract_and_declarer(current.auction, current.dealer)
            if c is not None:
                current.contract = c
                current.declarer = d
        tricks, decl_tricks = _replay_tricks(
            play_buffer, current.contract, current.declarer
        )
        if tricks:
            current.tricks = tricks
            current.declarer_tricks = decl_tricks
        # Score from contract+tricks if we have it. ``rs`` may already
        # have populated ns_score; only compute when missing.
        if (current.ns_score == 0 and current.ew_score == 0
                and current.contract is not None
                and current.declarer is not None):
            vulnerable = current.vulnerability.is_vulnerable(current.declarer)
            score = calculate_contract_score(
                current.contract, current.declarer_tricks, vulnerable
            )
            if current.declarer.is_ns():
                current.ns_score = score
                current.ew_score = -score
            else:
                current.ew_score = score
                current.ns_score = -score
        deals.append(current)
        current = None
        play_buffer = []

    rs_values: List[str] = []  # per-board ``rs`` results (whole-tournament-level)

    for key, val in pairs:
        if key == 'qx':
            # New board boundary
            _commit()
            current = LinDeal()
            v = val.strip().lower()
            if v.startswith('o') or v.startswith('c'):
                current.room = 'open' if v[0] == 'o' else 'closed'
                num_part = ''.join(ch for ch in v[1:] if ch.isdigit())
                if num_part:
                    current.board_number = int(num_part)
            else:
                num_part = ''.join(ch for ch in v if ch.isdigit())
                if num_part:
                    current.board_number = int(num_part)
            play_buffer = []
            continue

        if current is None:
            # Stash header-level rs for later; otherwise ignore.
            if key == 'rs':
                rs_values = [s.strip() for s in val.split(',') if s.strip()]
            continue

        if key == 'md':
            dealer, hands = _parse_md(val)
            current.dealer = dealer
            current.hands = hands
        elif key == 'sv':
            current.vulnerability = _LIN_VUL.get(val.strip().lower(),
                                                 Vulnerability.NONE)
        elif key == 'mb':
            b = _parse_bid(val)
            if b is not None:
                current.auction.append(b)
        elif key == 'pc':
            c = _parse_card(val)
            if c is not None:
                play_buffer.append(c)
        elif key == 'rs':
            rs_values = [s.strip() for s in val.split(',') if s.strip()]

    _commit()

    # If we collected tournament-level rs results, attach them by board
    # index when the deal didn't carry its own.
    for idx, deal in enumerate(deals):
        if idx < len(rs_values) and deal.ns_score == 0 and deal.ew_score == 0:
            ns, ew = _interpret_rs(rs_values[idx], deal)
            if ns is not None:
                deal.ns_score = ns
                deal.ew_score = ew or -ns

    return deals


def _interpret_rs(token: str, deal: LinDeal):
    """A LIN rs token looks like ``3NTNS=`` or ``5DXE-1``. Return (ns_score, ew_score)
    or (None, None) if the token isn't parseable. Best-effort — any
    failure leaves the deal's existing scores alone.
    """
    if not token:
        return None, None
    try:
        # Find the suit/level portion vs the result portion.
        # token = level + strain + (X|XX|None) + declarer + result
        # e.g. "3NTNS=", "5DXE-1", "4HN+1"
        import re
        m = re.match(r'^(\d)(NT|[CDHS])(XX|X)?([NSEW])(=|-\d+|\+\d+)$',
                     token, flags=re.IGNORECASE)
        if not m:
            return None, None
        level = int(m.group(1))
        strain = m.group(2).upper()
        dbl = (m.group(3) or '').upper()
        decl_char = m.group(4).upper()
        result_part = m.group(5)

        suit = Suit.NOTRUMP if strain == 'NT' else _SUIT_BY_CHAR[strain]
        contract = Contract(
            level=level, suit=suit,
            doubled=(dbl == 'X'),
            redoubled=(dbl == 'XX'),
            declarer=Seat.from_char(decl_char),
        )
        target = contract.target_tricks()
        if result_part == '=':
            tricks_made = target
        elif result_part.startswith('+'):
            tricks_made = target + int(result_part[1:])
        else:
            tricks_made = target + int(result_part)

        vulnerable = deal.vulnerability.is_vulnerable(contract.declarer)
        score = calculate_contract_score(contract, tricks_made, vulnerable)
        if contract.declarer.is_ns():
            return score, -score
        return -score, score
    except Exception:
        return None, None


def lin_deal_to_board_run(deal: LinDeal,
                          table: BenTable = BenTable.CLOSED) -> Optional[BenBoardRun]:
    """Convert a LinDeal into a BenBoardRun suitable for the score sheet."""
    if not deal.hands or deal.contract is None or deal.declarer is None:
        return None
    contract = deal.contract
    contract.declarer = deal.declarer
    return BenBoardRun(
        table=table,
        board_number=deal.board_number,
        pavlicek_id="",
        original_hands=dict(deal.hands),
        auction=list(deal.auction),
        tricks=list(deal.tricks),
        contract=contract,
        declarer_tricks=deal.declarer_tricks,
        ns_score=deal.ns_score,
        ew_score=deal.ew_score,
        played=bool(deal.tricks),
    )


def load_lin_file(path: str) -> List[LinDeal]:
    """Read a LIN file from disk."""
    return parse_lin_text(Path(path).read_text(encoding='utf-8',
                                                errors='replace'))
