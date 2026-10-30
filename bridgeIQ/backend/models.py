"""
Data models for bridge game state.
"""

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Optional, Dict, Any
import numpy as np


class Suit(IntEnum):
    SPADES = 0
    HEARTS = 1
    DIAMONDS = 2
    CLUBS = 3
    NOTRUMP = 4

    @classmethod
    def from_char(cls, c: str) -> 'Suit':
        return {'S': cls.SPADES, 'H': cls.HEARTS, 'D': cls.DIAMONDS,
                'C': cls.CLUBS, 'N': cls.NOTRUMP}[c.upper()]

    def to_char(self) -> str:
        return 'SHDCN'[self]

    def symbol(self) -> str:
        return ['♠', '♥', '♦', '♣', 'NT'][self]

    def pbn_char(self) -> str:
        """Return PBN format character (S, H, D, C)"""
        return 'SHDCN'[self]


class Rank(IntEnum):
    ACE = 0
    KING = 1
    QUEEN = 2
    JACK = 3
    TEN = 4
    NINE = 5
    EIGHT = 6
    SEVEN = 7
    SIX = 8
    FIVE = 9
    FOUR = 10
    THREE = 11
    TWO = 12

    @classmethod
    def from_char(cls, c: str) -> 'Rank':
        mapping = {'A': 0, 'K': 1, 'Q': 2, 'J': 3, 'T': 4, '10': 4,
                   '9': 5, '8': 6, '7': 7, '6': 8, '5': 9, '4': 10, '3': 11, '2': 12}
        return cls(mapping[c.upper()])

    def to_char(self) -> str:
        return 'AKQJT98765432'[self]


class Seat(IntEnum):
    NORTH = 0
    EAST = 1
    SOUTH = 2
    WEST = 3

    @classmethod
    def from_char(cls, c: str) -> 'Seat':
        return {'N': cls.NORTH, 'E': cls.EAST, 'S': cls.SOUTH, 'W': cls.WEST}[c.upper()]

    def to_char(self) -> str:
        return 'NESW'[self]

    def next(self) -> 'Seat':
        return Seat((self + 1) % 4)

    def partner(self) -> 'Seat':
        return Seat((self + 2) % 4)

    def is_ns(self) -> bool:
        return self in (Seat.NORTH, Seat.SOUTH)


class Vulnerability(Enum):
    NONE = 'None'
    NS = 'N-S'
    EW = 'E-W'
    BOTH = 'Both'

    def is_vulnerable(self, seat: Seat) -> bool:
        if self == Vulnerability.NONE:
            return False
        if self == Vulnerability.BOTH:
            return True
        if self == Vulnerability.NS:
            return seat.is_ns()
        return not seat.is_ns()


@dataclass
class Card:
    suit: Suit
    rank: Rank

    def __post_init__(self):
        # Ensure suit and rank are proper enum types
        if isinstance(self.suit, int):
            self.suit = Suit(self.suit)
        if isinstance(self.rank, int):
            self.rank = Rank(self.rank)

    @classmethod
    def from_str(cls, s: str) -> 'Card':
        """Parse card from string like 'SA', 'H2', 'DT'"""
        return cls(Suit.from_char(s[0]), Rank.from_char(s[1:]))

    def to_str(self) -> str:
        return f"{self.suit.to_char()}{self.rank.to_char()}"

    def code52(self) -> int:
        """Return card code in 0-51 range"""
        return self.suit * 13 + self.rank

    @classmethod
    def from_code52(cls, code: int) -> 'Card':
        return cls(Suit(code // 13), Rank(code % 13))

    def symbol(self) -> str:
        return f"{self.suit.symbol()}{self.rank.to_char()}"

    def hcp(self) -> int:
        """High card points for this card"""
        return max(0, 4 - self.rank)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize card to dict for network transmission."""
        return {
            "suit": self.suit.to_char(),
            "rank": self.rank.to_char(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Card':
        """Deserialize card from dict."""
        return cls(
            suit=Suit.from_char(data["suit"]),
            rank=Rank.from_char(data["rank"]),
        )


@dataclass
class Hand:
    cards: List[Card] = field(default_factory=list)

    @classmethod
    def from_pbn(cls, pbn: str) -> 'Hand':
        """Parse hand from PBN format like 'AK97.KQ2.T93.AK7'"""
        cards = []
        suits = pbn.split('.')
        for suit_idx, suit_cards in enumerate(suits):
            for rank_char in suit_cards:
                if rank_char == '-':
                    continue
                cards.append(Card(Suit(suit_idx), Rank.from_char(rank_char)))
        return cls(cards)

    def to_pbn(self) -> str:
        """Convert to PBN format"""
        suits = [[], [], [], []]
        for card in sorted(self.cards, key=lambda c: (c.suit, c.rank)):
            suits[card.suit].append(card.rank.to_char())
        return '.'.join([''.join(s) if s else '-' for s in suits])

    def hcp(self) -> int:
        """Total high card points"""
        return sum(c.hcp() for c in self.cards)

    def suit_length(self, suit: Suit) -> int:
        return sum(1 for c in self.cards if c.suit == suit)

    def has_card(self, card: Card) -> bool:
        return card in self.cards

    def remove_card(self, card: Card) -> bool:
        if card in self.cards:
            self.cards.remove(card)
            return True
        return False

    def get_suit_cards(self, suit: Suit) -> List[Card]:
        return [c for c in self.cards if c.suit == suit]

    def to_dict(self, hidden: bool = False) -> Dict[str, Any]:
        """Serialize hand to dict for network transmission.

        Args:
            hidden: If True, only send card count (for opponents' hands)
        """
        if hidden:
            return {"hidden": True, "count": len(self.cards)}
        return {"cards": [c.to_dict() for c in self.cards]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Hand':
        """Deserialize hand from dict."""
        if data.get("hidden"):
            # Return empty hand placeholder for hidden hands
            return cls(cards=[])
        return cls(cards=[Card.from_dict(c) for c in data["cards"]])


@dataclass
class Bid:
    level: int = 0
    suit: Optional[Suit] = None
    is_pass: bool = False
    is_double: bool = False
    is_redouble: bool = False
    alert: bool = False
    explanation: str = ""

    @classmethod
    def make_pass(cls) -> 'Bid':
        return cls(is_pass=True)

    @classmethod
    def make_double(cls) -> 'Bid':
        return cls(is_double=True)

    @classmethod
    def make_redouble(cls) -> 'Bid':
        return cls(is_redouble=True)

    @classmethod
    def from_str(cls, s: str) -> 'Bid':
        s = s.upper().strip()
        # The reference appends '~' to alerted bids; strip it before parsing.
        s = s.rstrip('~').strip()
        if s in ('PASS', 'P', '--', '-'):
            # The reference uses bare '-' as a pass placeholder in BDL bidding rows.
            return cls.make_pass()
        if s in ('X', 'DBL', 'DOUBLE', 'DB'):
            return cls.make_double()
        if s in ('XX', 'RDBL', 'REDOUBLE', 'RD'):
            return cls.make_redouble()
        level = int(s[0])
        suit_char = s[1] if len(s) > 1 else 'N'
        if suit_char == 'N' and len(s) > 2 and s[2] == 'T':
            suit_char = 'N'
        return cls(level=level, suit=Suit.from_char(suit_char))

    def to_str(self) -> str:
        if self.is_pass:
            return 'PASS'
        if self.is_double:
            return 'X'
        if self.is_redouble:
            return 'XX'
        suit_str = 'NT' if self.suit == Suit.NOTRUMP else self.suit.to_char()
        return f"{self.level}{suit_str}"

    def to_ben_str(self) -> str:
        """Convert to BEN format"""
        if self.is_pass:
            return 'PASS'
        if self.is_double:
            return 'X'
        if self.is_redouble:
            return 'XX'
        suit_str = 'N' if self.suit == Suit.NOTRUMP else self.suit.to_char()
        return f"{self.level}{suit_str}"

    def symbol(self) -> str:
        if self.is_pass:
            return 'Pass'
        if self.is_double:
            return 'X'
        if self.is_redouble:
            return 'XX'
        return f"{self.level}{self.suit.symbol()}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize bid to dict for network transmission."""
        return {
            "level": self.level,
            "suit": self.suit.to_char() if self.suit is not None else None,
            "is_pass": self.is_pass,
            "is_double": self.is_double,
            "is_redouble": self.is_redouble,
            "alert": self.alert,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Bid':
        """Deserialize bid from dict."""
        suit = Suit.from_char(data["suit"]) if data.get("suit") else None
        return cls(
            level=data.get("level", 0),
            suit=suit,
            is_pass=data.get("is_pass", False),
            is_double=data.get("is_double", False),
            is_redouble=data.get("is_redouble", False),
            alert=data.get("alert", False),
            explanation=data.get("explanation", ""),
        )


@dataclass
class Contract:
    level: int
    suit: Suit
    doubled: bool = False
    redoubled: bool = False
    declarer: Seat = Seat.SOUTH

    def to_str(self) -> str:
        suit_str = 'NT' if self.suit == Suit.NOTRUMP else self.suit.to_char()
        dbl = 'XX' if self.redoubled else ('X' if self.doubled else '')
        return f"{self.level}{suit_str}{dbl}"

    def target_tricks(self) -> int:
        return 6 + self.level

    def to_dict(self) -> Dict[str, Any]:
        """Serialize contract to dict for network transmission."""
        return {
            "level": self.level,
            "suit": self.suit.to_char() if self.suit is not None else None,
            "doubled": self.doubled,
            "redoubled": self.redoubled,
            "declarer": self.declarer.to_char(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Contract':
        """Deserialize contract from dict."""
        return cls(
            level=data["level"],
            suit=Suit.from_char(data["suit"]) if data.get("suit") else Suit.NOTRUMP,
            doubled=data.get("doubled", False),
            redoubled=data.get("redoubled", False),
            declarer=Seat.from_char(data["declarer"]),
        )


@dataclass
class Trick:
    cards: List[Card] = field(default_factory=list)
    leader: Seat = Seat.NORTH
    winner: Optional[Seat] = None

    def add_card(self, card: Card, trump: Optional[Suit] = None):
        self.cards.append(card)
        if len(self.cards) == 4:
            self.winner = self._determine_winner(trump)

    def _determine_winner(self, trump: Optional[Suit]) -> Seat:
        lead_suit = self.cards[0].suit
        winning_idx = 0
        winning_card = self.cards[0]


        for i, card in enumerate(self.cards[1:], 1):
            is_trump = trump is not None and card.suit == trump
            winning_is_trump = trump is not None and winning_card.suit == trump

            if is_trump and not winning_is_trump:
                winning_idx = i
                winning_card = card
            elif is_trump and winning_is_trump and card.rank < winning_card.rank:
                winning_idx = i
                winning_card = card
            elif not is_trump and not winning_is_trump:
                if card.suit == lead_suit and card.rank < winning_card.rank:
                    winning_idx = i
                    winning_card = card

        # Use .value explicitly to ensure correct arithmetic with IntEnum
        winner_seat = Seat((self.leader.value + winning_idx) % 4)
        return winner_seat

    def is_complete(self) -> bool:
        return len(self.cards) == 4


class PlayerType(Enum):
    HUMAN = 'human'
    COMPUTER = 'computer'
    EXTERNAL = 'external'


@dataclass
class Player:
    seat: Seat
    player_type: PlayerType = PlayerType.COMPUTER
    name: str = ""
    hand: Optional[Hand] = None
    visible: bool = False

    def __post_init__(self):
        if not self.name:
            self.name = self.seat.to_char()


@dataclass
class BoardState:
    """Complete state of a bridge board"""
    board_number: int = 1
    dealer: Seat = Seat.NORTH
    vulnerability: Vulnerability = Vulnerability.NONE
    hands: Dict[Seat, Hand] = field(default_factory=dict)
    auction: List[Bid] = field(default_factory=list)
    contract: Optional[Contract] = None
    tricks: List[Trick] = field(default_factory=list)
    current_trick: Optional[Trick] = None
    declarer_tricks: int = 0
    defense_tricks: int = 0

    @classmethod
    def from_pbn_deal(cls, pbn: str, board_num: int = 1) -> 'BoardState':
        """Parse deal from PBN format like 'N:AK97.KQ2.T93.AK7 ...'"""
        parts = pbn.split(':')
        if len(parts) == 2:
            first_seat = Seat.from_char(parts[0])
            hands_str = parts[1]
        else:
            first_seat = Seat.NORTH
            hands_str = pbn

        hand_strs = hands_str.strip().split()
        hands = {}
        for i, hand_str in enumerate(hand_strs):
            seat = Seat((first_seat + i) % 4)
            hands[seat] = Hand.from_pbn(hand_str)

        dealer, vuln = cls._board_dealer_vuln(board_num)
        return cls(board_number=board_num, dealer=dealer, vulnerability=vuln, hands=hands)

    @staticmethod
    def _board_dealer_vuln(number: int) -> tuple:
        dealers = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]
        vulns = [
            Vulnerability.NONE, Vulnerability.NS, Vulnerability.EW, Vulnerability.BOTH,
            Vulnerability.NS, Vulnerability.EW, Vulnerability.BOTH, Vulnerability.NONE,
            Vulnerability.EW, Vulnerability.BOTH, Vulnerability.NONE, Vulnerability.NS,
            Vulnerability.BOTH, Vulnerability.NONE, Vulnerability.NS, Vulnerability.EW
        ]
        return dealers[(number - 1) % 4], vulns[(number - 1) % 16]

    def to_pbn_deal(self) -> str:
        """Convert to PBN deal string"""
        hands = []
        for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
            if seat in self.hands:
                hands.append(self.hands[seat].to_pbn())
            else:
                hands.append('')
        return f"N:{' '.join(hands)}"

    def to_ben_deal(self) -> str:
        """Convert to BEN deal format (space-separated hands starting from N)"""
        hands = []
        for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
            if seat in self.hands:
                hands.append(self.hands[seat].to_pbn())
            else:
                hands.append('')
        return ' '.join(hands)

    def auction_to_ben(self) -> str:
        """Convert auction to BEN format"""
        dealer_char = self.dealer.to_char()
        vuln_str = {
            Vulnerability.NONE: 'None',
            Vulnerability.NS: 'N-S',
            Vulnerability.EW: 'E-W',
            Vulnerability.BOTH: 'Both'
        }[self.vulnerability]

        bids = ' '.join(b.to_ben_str() for b in self.auction)
        return f"{dealer_char} {vuln_str} {bids}".strip()

    def get_current_bidder(self) -> Seat:
        """Get seat of player to bid next"""
        return Seat((self.dealer + len(self.auction)) % 4)

    def is_auction_complete(self) -> bool:
        """Check if auction is finished"""
        if len(self.auction) < 4:
            return False
        last_three = self.auction[-3:]
        return all(b.is_pass for b in last_three)

    def is_passed_out(self) -> bool:
        """Check if deal was passed out"""
        return (len(self.auction) == 4 and
                all(b.is_pass for b in self.auction))

    def get_ns_hcp(self) -> int:
        total = 0
        for seat in [Seat.NORTH, Seat.SOUTH]:
            if seat in self.hands:
                total += self.hands[seat].hcp()
        return total

    def get_ew_hcp(self) -> int:
        total = 0
        for seat in [Seat.EAST, Seat.WEST]:
            if seat in self.hands:
                total += self.hands[seat].hcp()
        return total

    def remaining_cards_by_suit(self) -> Dict[Suit, List[Rank]]:
        """Cards still outstanding (not yet played in any trick).

        Walks completed tricks plus any in-progress trick. For each suit
        returns ranks in display order (ACE first). Useful for "what's
        still out" annotations in the play UI and for AI/Claude prompts
        that would otherwise have to recount from the trick history.
        """
        played: set = set()
        for trick in self.tricks:
            for card in trick.cards:
                played.add((card.suit, card.rank))
        if self.current_trick:
            for card in self.current_trick.cards:
                played.add((card.suit, card.rank))
        result: Dict[Suit, List[Rank]] = {}
        for suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
            result[suit] = [r for r in Rank if (suit, r) not in played]
        return result

    def to_dict(self, hidden_seats: List[Seat] = None) -> Dict[str, Any]:
        """Serialize board state to dict for network transmission.

        Args:
            hidden_seats: List of seats whose hands should be hidden
        """
        if hidden_seats is None:
            hidden_seats = []

        hands_dict = {}
        for seat, hand in self.hands.items():
            hidden = seat in hidden_seats
            hands_dict[seat.to_char()] = hand.to_dict(hidden=hidden)

        result = {
            "board_number": self.board_number,
            "dealer": self.dealer.to_char(),
            "vulnerability": self.vulnerability.value,
            "hands": hands_dict,
            "auction": [b.to_dict() for b in self.auction],
            "declarer_tricks": self.declarer_tricks,
            "defense_tricks": self.defense_tricks,
        }

        if self.contract:
            result["contract"] = self.contract.to_dict()

        if self.current_trick:
            result["current_trick"] = {
                "leader": self.current_trick.leader.to_char(),
                "cards": [c.to_dict() for c in self.current_trick.cards],
            }

        result["tricks"] = [
            {
                "leader": t.leader.to_char(),
                "cards": [c.to_dict() for c in t.cards],
                "winner": t.winner.to_char() if t.winner else None,
            }
            for t in self.tricks
        ]

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BoardState':
        """Deserialize board state from dict."""
        # Parse hands
        hands = {}
        for seat_char, hand_data in data.get("hands", {}).items():
            seat = Seat.from_char(seat_char)
            hands[seat] = Hand.from_dict(hand_data)

        # Parse vulnerability
        vuln_str = data.get("vulnerability", "None")
        vuln_map = {
            "None": Vulnerability.NONE,
            "N-S": Vulnerability.NS,
            "E-W": Vulnerability.EW,
            "Both": Vulnerability.BOTH,
        }
        vulnerability = vuln_map.get(vuln_str, Vulnerability.NONE)

        # Parse auction
        auction = [Bid.from_dict(b) for b in data.get("auction", [])]

        # Parse contract
        contract = None
        if "contract" in data and data["contract"]:
            contract = Contract.from_dict(data["contract"])

        # Parse tricks
        tricks = []
        for t_data in data.get("tricks", []):
            trick = Trick(
                leader=Seat.from_char(t_data["leader"]),
                cards=[Card.from_dict(c) for c in t_data["cards"]],
                winner=Seat.from_char(t_data["winner"]) if t_data.get("winner") else None,
            )
            tricks.append(trick)

        # Parse current trick
        current_trick = None
        if "current_trick" in data and data["current_trick"]:
            ct_data = data["current_trick"]
            current_trick = Trick(
                leader=Seat.from_char(ct_data["leader"]),
                cards=[Card.from_dict(c) for c in ct_data["cards"]],
            )

        return cls(
            board_number=data.get("board_number", 1),
            dealer=Seat.from_char(data.get("dealer", "N")),
            vulnerability=vulnerability,
            hands=hands,
            auction=auction,
            contract=contract,
            tricks=tricks,
            current_trick=current_trick,
            declarer_tricks=data.get("declarer_tricks", 0),
            defense_tricks=data.get("defense_tricks", 0),
        )


class BenTable(Enum):
    """Which table in a teams match"""
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class BenBoardRun:
    """A single play-through of a board at one table"""
    table: BenTable
    board_number: int
    pavlicek_id: str
    original_hands: Dict[Seat, Hand]
    auction: List[Bid] = field(default_factory=list)
    tricks: List[Trick] = field(default_factory=list)
    contract: Optional[Contract] = None
    declarer_tricks: int = 0
    ns_score: int = 0
    ew_score: int = 0
    played: bool = False
    # Blank means "unrecorded" — the bidding-log header renders that
    # as "—" so the user immediately sees the metadata is missing
    # instead of being told the deal was played by "BEN-NN" (the
    # legacy neural net) when it was actually the native rule-based
    # bidder running e.g. Precision90M.
    ns_bidding_system: str = ""
    ew_bidding_system: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for network transport (host → guest closed-room sync)."""
        return {
            "table": self.table.value,
            "board_number": self.board_number,
            "pavlicek_id": self.pavlicek_id,
            "hands": {s.to_char(): h.to_dict() for s, h in self.original_hands.items()},
            "auction": [b.to_dict() for b in self.auction],
            "tricks": [
                {
                    "leader": t.leader.to_char(),
                    "cards": [c.to_dict() for c in t.cards],
                    "winner": t.winner.to_char() if t.winner else None,
                }
                for t in self.tricks
            ],
            "contract": self.contract.to_dict() if self.contract else None,
            "declarer_tricks": self.declarer_tricks,
            "ns_score": self.ns_score,
            "ew_score": self.ew_score,
            "played": self.played,
            "ns_bidding_system": self.ns_bidding_system,
            "ew_bidding_system": self.ew_bidding_system,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BenBoardRun':
        hands = {Seat.from_char(sc): Hand.from_dict(hd)
                 for sc, hd in (data.get("hands") or {}).items()}
        tricks = []
        for t in data.get("tricks") or []:
            tricks.append(Trick(
                leader=Seat.from_char(t["leader"]),
                cards=[Card.from_dict(c) for c in t.get("cards") or []],
                winner=Seat.from_char(t["winner"]) if t.get("winner") else None,
            ))
        contract = Contract.from_dict(data["contract"]) if data.get("contract") else None
        return cls(
            table=BenTable(data.get("table", "open")),
            board_number=data.get("board_number", 0),
            pavlicek_id=data.get("pavlicek_id", ""),
            original_hands=hands,
            auction=[Bid.from_dict(b) for b in data.get("auction") or []],
            tricks=tricks,
            contract=contract,
            declarer_tricks=data.get("declarer_tricks", 0),
            ns_score=data.get("ns_score", 0),
            ew_score=data.get("ew_score", 0),
            played=data.get("played", False),
            ns_bidding_system=data.get("ns_bidding_system", ""),
            ew_bidding_system=data.get("ew_bidding_system", ""),
        )


# IMP conversion table for teams scoring
IMP_TABLE = [
    (20, 0), (50, 1), (90, 2), (130, 3), (170, 4),
    (220, 5), (270, 6), (320, 7), (370, 8), (430, 9),
    (500, 10), (600, 11), (750, 12), (900, 13), (1100, 14),
    (1300, 15), (1500, 16), (1750, 17), (2000, 18), (2250, 19),
    (2500, 20), (3000, 21), (3500, 22), (4000, 23), (float('inf'), 24)
]


def diff_to_imps(diff: int) -> int:
    """Convert score difference to IMPs."""
    abs_diff = abs(diff)
    for threshold, imps in IMP_TABLE:
        if abs_diff < threshold:
            return imps if diff >= 0 else -imps
    return 24 if diff >= 0 else -24


@dataclass
class BenTeamsMatch:
    """A teams match with Open and Closed room results"""
    match_id: str
    board_runs: Dict[int, Dict[BenTable, BenBoardRun]] = field(default_factory=dict)
    ns_team_name: str = "N/S Team"
    ew_team_name: str = "E/W Team"
    current_board: int = 1
    num_boards: int = 16
    # Blank means "unrecorded"; the construction sites in main_window
    # and match_controller now feed the real per-pair system names
    # from preferences rather than this legacy "BEN-NN" placeholder.
    ns_bidding_system: str = ""
    ew_bidding_system: str = ""

    def get_imp_swing(self, board_num: int) -> int:
        """Calculate IMP swing for a board (positive = human did better).

        Returns the IMP difference between Open and Closed room results.
        In our setup, both rooms play the same hands with N/S perspective,
        so we compare: did human (open room) do better than computer (closed room)?
        """
        if board_num not in self.board_runs:
            return 0

        runs = self.board_runs[board_num]
        open_run = runs.get(BenTable.OPEN)
        closed_run = runs.get(BenTable.CLOSED)

        if not open_run or not closed_run:
            return 0

        if not open_run.played or not closed_run.played:
            return 0

        # Both rooms play the same hands - compare N/S scores
        # Positive swing means human (open room) did better than computer (closed room)
        diff = open_run.ns_score - closed_run.ns_score

        return diff_to_imps(diff)

    def get_total_imps(self) -> tuple:
        """Get total IMPs for each team.

        Returns:
            (ns_imps, ew_imps) tuple
        """
        ns_total = 0
        for board_num in self.board_runs:
            swing = self.get_imp_swing(board_num)
            if swing > 0:
                ns_total += swing
            else:
                ns_total += swing  # swing is negative for EW

        ew_total = -ns_total
        return (max(0, ns_total), max(0, ew_total))
