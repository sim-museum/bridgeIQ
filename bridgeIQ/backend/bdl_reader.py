"""
BDL File Reader - Read BDL log files.

BDL format is used for logging completed hands.
This reader parses BDL files and reconstructs game state from them.
"""

from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import (
    BoardState, Hand, Card, Suit, Rank, Seat,
    Vulnerability, Bid, Contract, Trick
)


@dataclass
class BDLDeal:
    """A single deal read from BDL file"""
    # Header info
    board_number: int = 0
    pavlicek_id: str = ""
    date: Optional[str] = None
    scoring_type: str = "IMP"

    # Deal info
    dealer: Seat = Seat.NORTH
    vulnerability: Vulnerability = Vulnerability.NONE
    hands: Dict[Seat, Hand] = field(default_factory=dict)

    # Auction
    auction: List[Bid] = field(default_factory=list)
    alerts: Dict[str, str] = field(default_factory=dict)

    # Contract
    contract: Optional[Contract] = None
    declarer: Optional[Seat] = None

    # Play. Each entry is {"cards": [card_str, …], "leader": Optional[Seat]}.
    # Q-Plus records the leader of each trick explicitly; older formats
    # only have the cards and the leader is computed from the contract.
    tricks: List[Dict[str, Any]] = field(default_factory=list)
    opening_lead: Optional[str] = None

    # Result
    tricks_made: int = 0
    ns_score: int = 0
    ew_score: int = 0

    # Claude commentary (if present in BDL file)
    commentary: str = ""

    # Bidding-system names pulled from the `.Bidding cnv : N/S: …` /
    # `… E/W: …` header lines, when present. Q-Plus writes the system
    # filename token ("P-P90M-A"), bridgeIQ writes the system name
    # ("Precision90M" / "BEN-NN" / etc.). Either is acceptable —
    # downstream code reads the field as opaque text for display.
    ns_bidding_system: str = ""
    ew_bidding_system: str = ""

    def to_board_state(self) -> BoardState:
        """Convert to BoardState for replay"""
        board = BoardState(
            board_number=self.board_number,
            dealer=self.dealer,
            vulnerability=self.vulnerability,
            hands=self.hands.copy(),
            auction=self.auction.copy(),
            contract=self.contract,
        )
        return board


class BDLReader:
    """Read BDL log files"""

    def __init__(self):
        self.current_section = None
        self.current_deal = None

    def read_file(self, filepath: Path) -> List[BDLDeal]:
        """Read BDL file and return list of deals"""
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return self.read_lines(lines)

    def read_lines(self, lines: List[str]) -> List[BDLDeal]:
        """Parse already-read BDL lines. Used for .bdl files and for the
        D1-prefixed deal blocks embedded in a Q-Plus .qss score export."""
        deals = []
        self.current_deal = None
        self.current_section = None

        i = 0
        while i < len(lines):
            line = lines[i].rstrip('\n\r')
            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                i += 1
                continue

            # Deal separator or start
            if stripped.startswith('===') or stripped.startswith('---') or stripped.startswith('****'):
                if self.current_deal and self._is_deal_valid(self.current_deal):
                    deals.append(self.current_deal)
                self.current_deal = BDLDeal()
                self.current_section = None
                i += 1
                continue

            # Section headers [Section Name]
            if stripped.startswith('[') and stripped.endswith(']'):
                self.current_section = stripped[1:-1].lower()
                i += 1
                continue

            # DOCTYPE header
            if stripped.startswith('DOCTYPE'):
                # Start new deal if not already started
                if self.current_deal is None:
                    self.current_deal = BDLDeal()
                i += 1
                continue

            # Handle continuation lines (key is empty, starts with spaces then colon)
            if ':' in line and self.current_deal:
                before_colon = line.split(':', 1)[0]
                after_colon = line.split(':', 1)[1]
                # BDL continuation: "             :   content"
                if before_colon.strip() == '' or before_colon.strip() == '.':
                    # Card continuation lines (during card parsing mode)
                    if hasattr(self, '_card_lines') and self._card_lines is not None:
                        self._card_lines.append(after_colon)
                        if len(self._card_lines) >= 12:
                            self._parse_card_block(self.current_deal, self._card_lines)
                            self._card_lines = None
                            self.current_section = None
                        i += 1
                        continue
                    # Trick continuation lines (e.g. Q-Plus's per-trick rows):
                    #   ``Tricks       :  1 N h2 hA+ h6 h4 H``
                    #   ``             :  2 E sK sQ sA+ s2 S``
                    # Once the Tricks header opened the section, every
                    # continuation row is another trick. End-of-block is
                    # signalled by a separator line ('===' / '---') which
                    # is handled at the top of the outer loop.
                    if self.current_section == 'tricks':
                        self._parse_play_line(after_colon, self.current_deal)
                        i += 1
                        continue
                    # Bidding continuation rows from Q-Plus:
                    #   ``Bids   :   N    E    S    W``
                    #   ``       :   -    2c~  2h   2s``
                    # ASCII rule lines like "------" or "======" must be
                    # skipped — but a row of three lone "-" (each a pass)
                    # must NOT be skipped, so we filter on whether the
                    # row contains a multi-dash/equals token.
                    if self.current_section == 'bidding':
                        clean = after_colon.strip()
                        # Pure separator rule: all chars in the line are
                        # '-' / '=' / whitespace AND any token is longer
                        # than one char (so a real "-" pass survives).
                        is_rule = (
                            clean
                            and set(clean) <= set('-= ')
                            and any(len(tok) > 1 for tok in clean.split())
                        )
                        if clean and not is_rule:
                            self._parse_bidding_line(after_colon, self.current_deal)
                        i += 1
                        continue
                    # Bidding-system continuation (`.Bidding cnv :` opens
                    # the section; the next line carries E/W).
                    if self.current_section == 'bidding cnv':
                        self._absorb_cnv_line(after_colon, self.current_deal)
                        i += 1
                        continue
                    # Commentary continuation
                    if (self.current_deal.commentary
                            and not stripped.startswith('.Bidding')
                            and not stripped.startswith('.Lead')
                            and not stripped.startswith('.Signal')
                            and not stripped.startswith('.Strength')
                            and not stripped.startswith('.Cheating')
                            and not stripped.startswith('.Source')
                            and not stripped.startswith('.description')):
                        self.current_deal.commentary += "\n" + after_colon.strip()
                        i += 1
                        continue

            # Parse content based on current section
            if self.current_deal:
                self._parse_line(stripped, line)

            i += 1

        # Add last deal
        if self.current_deal and self._is_deal_valid(self.current_deal):
            deals.append(self.current_deal)

        return deals

    def _is_deal_valid(self, deal: BDLDeal) -> bool:
        """Check if deal has enough data to be valid"""
        return len(deal.hands) >= 1 or deal.pavlicek_id

    def _parse_line(self, stripped: str, original: str):
        """Parse a single line based on current section"""
        deal = self.current_deal

        # A "Key: value" header line means a new section is starting —
        # let the auto-detector re-classify so we don't keep feeding new
        # rows into the previous section's parser. (Hand lines `N: …` /
        # `E: …` / etc. are continuations, not new sections.)
        if ':' in stripped:
            before = stripped.split(':', 1)[0].strip()
            if before and before.upper() not in ('N', 'E', 'S', 'W'):
                self._auto_detect_and_parse(stripped, original)
                return

        # Try to detect section from content if not in a named section
        if self.current_section is None:
            self._auto_detect_and_parse(stripped, original)
            return

        section = self.current_section

        if section in ['deal header', 'header']:
            self._parse_header_line(stripped, deal)
        elif section in ['hands', 'cards', 'deal']:
            self._parse_hand_line(stripped, deal)
        elif section in ['bidding', 'auction']:
            self._parse_bidding_line(stripped, deal)
        elif section in ['alerts', 'explanations']:
            self._parse_alert_line(stripped, deal)
        elif section in ['play', 'tricks']:
            self._parse_play_line(stripped, deal)
        elif section in ['result', 'score']:
            self._parse_result_line(stripped, deal)
        elif section == 'bidding cnv':
            # Continuation of `.Bidding cnv :` — the E/W row.
            # When a non-continuation line arrives the section is
            # cleared by the auto-detect path.
            self._absorb_cnv_line(stripped, deal)

    def _absorb_cnv_line(self, value: str, deal: 'BDLDeal') -> None:
        """Pull the system name out of a `.Bidding cnv` row.

        Accepts only `N/S: <name>` / `E/W: <name>`. The BDL header
        runs several `.X cnv` sections back-to-back with empty
        before-colon continuation rows, so a "bare value" guess
        would happily eat the Lead and Signal continuation rows
        too. Once both fields are filled we close the section so
        downstream rows aren't misrouted here.
        """
        text = (value or "").strip()
        if not text:
            return
        upper = text.upper()
        if upper.startswith(("N/S:", "NS:")):
            deal.ns_bidding_system = text.split(":", 1)[1].strip()
        elif upper.startswith(("E/W:", "EW:")):
            deal.ew_bidding_system = text.split(":", 1)[1].strip()
        else:
            # Non-cnv row arrived (Lead cnv, etc.) — close the
            # section so the parser routes it normally.
            self.current_section = None
            return
        if deal.ns_bidding_system and deal.ew_bidding_system:
            self.current_section = None

    def _auto_detect_and_parse(self, stripped: str, original: str):
        """Auto-detect line type and parse accordingly"""
        deal = self.current_deal

        # Header fields (Key: Value)
        if ':' in stripped and not stripped.startswith(('N:', 'E:', 'S:', 'W:')):
            key, value = stripped.split(':', 1)
            key = key.strip().lower()
            value = value.strip()

            if key in ['board', 'board number', 'board#']:
                try:
                    deal.board_number = int(value)
                except ValueError:
                    pass
            elif key in ['pavlicek', 'deal id', 'deal_id', 'deal']:
                deal.pavlicek_id = value
            elif key in ['deal-text', 'deal_text', 'description']:
                try:
                    # Try to extract board number from text like "Board #924"
                    import re
                    m = re.search(r'#(\d+)', value)
                    if m:
                        deal.board_number = int(m.group(1))
                except (ValueError, AttributeError):
                    pass
            elif key in ['date', 'datetime']:
                deal.date = value
            elif key in ['scoring', 'scoring type']:
                deal.scoring_type = value
            elif key in ['dealer']:
                try:
                    deal.dealer = Seat.from_char(value[0].upper())
                except (ValueError, IndexError):
                    pass
            elif key in ['vulnerability', 'vul', 'vuln']:
                deal.vulnerability = self._parse_vulnerability(value)
            elif key in ['contract']:
                deal.contract = self._parse_contract(value)
                # Q-Plus appends the declarer's seat name to the contract
                # row, e.g. "Contract  :  4s    West". Pull it so the
                # closed-room run attributes tricks to the right side.
                for tok in value.split():
                    upper = tok.upper()
                    if upper in ('NORTH', 'EAST', 'SOUTH', 'WEST'):
                        try:
                            deal.declarer = Seat.from_char(upper[0])
                            if deal.contract is not None:
                                deal.contract.declarer = deal.declarer
                        except Exception:
                            pass
                        break
            elif key in ['declarer']:
                try:
                    deal.declarer = Seat.from_char(value[0].upper())
                except (ValueError, IndexError):
                    pass
            elif key in ['tricks', 'tricks made']:
                try:
                    deal.tricks_made = int(value)
                except ValueError:
                    # Q-Plus packs the first trick onto the "Tricks" key
                    # row ("Tricks  :  1  N  h2  hA+ …"). Treat the value
                    # as the first trick and open the section so any
                    # continuation rows land in _parse_play_line.
                    self.current_section = 'tricks'
                    if value:
                        self._parse_play_line(value, deal)
            elif key in ['ns score', 'n/s score', 'ns']:
                try:
                    deal.ns_score = int(value)
                except ValueError:
                    pass
            elif key in ['ew score', 'e/w score', 'ew']:
                try:
                    deal.ew_score = int(value)
                except ValueError:
                    pass
            elif key in ['commentary', 'comment', 'analysis']:
                deal.commentary = value
            elif key in ['.bidding cnv', 'bidding cnv', '.bidding']:
                # `.Bidding cnv :  N/S: <system>` or
                # `.            :  E/W: <system>`. Q-Plus / bridgeIQ
                # write both as continuation rows under the same key.
                # The first line carries N/S; the second carries E/W.
                self._absorb_cnv_line(value, deal)
                # Hold the section open so the next continuation line
                # (with an empty before-colon) also lands here.
                self.current_section = 'bidding cnv'
            elif key in ['bids', 'bidding', 'auction']:
                # Q-Plus header row: "Bids   :   N    E    S    W". Open
                # the bidding section so continuation rows reach the bid
                # parser. The header itself has no real bids, but the
                # bid parser is tolerant.
                self.current_section = 'bidding'
                if value:
                    self._parse_bidding_line(value, deal)
            elif key in ['cards']:
                # Start card parsing mode - value has first North suit line
                self.current_section = 'cards'
                self._card_lines = [value]
            elif key in ['trick-prep']:
                # Reserved for header-only "Tricks: 10"-style rows. The
                # 'tricks' key itself is handled above (it tries an int
                # parse first, then falls through to per-trick parsing).
                self.current_section = 'tricks'
            elif key in ['result']:
                # Parse result: contract declarer +/-tricks score deal_id
                parts = value.split()
                if len(parts) >= 4:
                    try:
                        score = int(parts[3])
                        # Determine NS or EW score based on declarer
                        deal.ns_score = score
                    except (ValueError, IndexError):
                        pass
                self.current_section = None
            return

        # Hand line: N: S AKJ5 H Q87 D K43 C 1065
        if stripped.startswith(('N:', 'E:', 'S:', 'W:')):
            self._parse_hand_line(stripped, deal)
            return

        # Bidding line (sequence of bids)
        if self._looks_like_bidding(stripped):
            self._parse_bidding_line(stripped, deal)
            return

        # Trick line: Trick 1: SA SK S2 S3
        if stripped.lower().startswith('trick'):
            self._parse_play_line(stripped, deal)
            return

    def _looks_like_bidding(self, line: str) -> bool:
        """Check if line looks like bidding"""
        bid_patterns = ['pass', 'p', 'x', 'xx', 'dbl', 'rdbl',
                        '1c', '1d', '1h', '1s', '1n', '2c', '2d', '2h', '2s', '2n',
                        '3c', '3d', '3h', '3s', '3n', '4c', '4d', '4h', '4s', '4n',
                        '5c', '5d', '5h', '5s', '5n', '6c', '6d', '6h', '6s', '6n',
                        '7c', '7d', '7h', '7s', '7n']
        words = line.lower().split()
        bid_count = sum(1 for w in words if w in bid_patterns or w.rstrip('.') in bid_patterns)
        return bid_count >= 2 or (bid_count == 1 and len(words) <= 4)

    def _parse_header_line(self, line: str, deal: BDLDeal):
        """Parse header field"""
        if ':' not in line:
            return

        key, value = line.split(':', 1)
        key = key.strip().lower()
        value = value.strip()

        if key in ['board', 'board number']:
            try:
                deal.board_number = int(value)
            except ValueError:
                pass
        elif key in ['pavlicek', 'deal id']:
            deal.pavlicek_id = value
        elif key == 'date':
            deal.date = value
        elif key == 'scoring':
            deal.scoring_type = value
        elif key == 'dealer':
            try:
                deal.dealer = Seat.from_char(value[0].upper())
            except (ValueError, IndexError):
                pass
        elif key in ['vulnerability', 'vul']:
            deal.vulnerability = self._parse_vulnerability(value)

    def _parse_card_block(self, deal: BDLDeal, card_lines: list):
        """Parse 12-line BDL Cards block into 4 hands.

        Layout: lines 0-3 = North (S,H,D,C), lines 4-7 = West/East, lines 8-11 = South.
        """
        RANK_MAP = {'A': Rank.ACE, 'K': Rank.KING, 'Q': Rank.QUEEN, 'J': Rank.JACK,
                    'T': Rank.TEN, '10': Rank.TEN, '9': Rank.NINE, '8': Rank.EIGHT,
                    '7': Rank.SEVEN, '6': Rank.SIX, '5': Rank.FIVE, '4': Rank.FOUR,
                    '3': Rank.THREE, '2': Rank.TWO}
        SUIT_ORDER = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]

        def parse_ranks(text):
            cards = []
            for token in text.split():
                if token in RANK_MAP:
                    cards.append(token)
                elif token == '-':
                    pass  # void suit
            return cards

        def make_hand(suit_lines):
            cards = []
            for j, suit in enumerate(SUIT_ORDER):
                if j < len(suit_lines):
                    for rank_str in parse_ranks(suit_lines[j]):
                        if rank_str in RANK_MAP:
                            cards.append(Card(suit, RANK_MAP[rank_str]))
            return Hand(cards) if cards else None

        # North: lines 0-3
        n_hand = make_hand([card_lines[j] if j < len(card_lines) else '' for j in range(4)])
        if n_hand:
            deal.hands[Seat.NORTH] = n_hand

        # West/East: lines 4-7, split at largest whitespace gap
        w_suits, e_suits = [], []
        for j in range(4, 8):
            cl = card_lines[j] if j < len(card_lines) else ''
            # Find the largest gap of spaces to split West and East
            best_start, best_len = -1, 0
            in_gap, gap_start = False, 0
            for k, ch in enumerate(cl):
                if ch == ' ':
                    if not in_gap:
                        in_gap = True
                        gap_start = k
                else:
                    if in_gap:
                        gl = k - gap_start
                        if gl > best_len:
                            best_len = gl
                            best_start = gap_start
                        in_gap = False
            if in_gap:
                gl = len(cl) - gap_start
                if gl > best_len:
                    best_len = gl
                    best_start = gap_start

            if best_len >= 4 and best_start > 0:
                w_suits.append(cl[:best_start])
                e_suits.append(cl[best_start + best_len:])
            else:
                w_suits.append(cl)
                e_suits.append('')

        w_hand = make_hand(w_suits)
        e_hand = make_hand(e_suits)
        if w_hand:
            deal.hands[Seat.WEST] = w_hand
        if e_hand:
            deal.hands[Seat.EAST] = e_hand

        # South: lines 8-11
        s_hand = make_hand([card_lines[j] if j < len(card_lines) else '' for j in range(8, 12)])
        if s_hand:
            deal.hands[Seat.SOUTH] = s_hand

    def _parse_vulnerability(self, value: str) -> Vulnerability:
        """Parse vulnerability string"""
        value = value.upper().strip()
        if value in ['NONE', '-', 'O', 'LOVE']:
            return Vulnerability.NONE
        elif value in ['NS', 'N-S', 'N/S', 'NORTH-SOUTH']:
            return Vulnerability.NS
        elif value in ['EW', 'E-W', 'E/W', 'EAST-WEST']:
            return Vulnerability.EW
        elif value in ['BOTH', 'ALL', 'B']:
            return Vulnerability.BOTH
        return Vulnerability.NONE

    def _parse_hand_line(self, line: str, deal: BDLDeal):
        """Parse hand: N: S AKJ5 H Q87 D K43 C 1065"""
        if ':' not in line:
            return

        # Get seat
        seat_str = line[0].upper()
        try:
            seat = Seat.from_char(seat_str)
        except (ValueError, KeyError):
            return

        # Get cards part (after the colon)
        cards_str = line[2:].strip()

        # Parse suits
        hand = self._parse_hand_string(cards_str)
        if hand:
            deal.hands[seat] = hand

    def _parse_hand_string(self, cards_str: str) -> Optional[Hand]:
        """Parse hand string like 'S AKJ5 H Q87 D K43 C 1065' or 'AKJ5.Q87.K43.1065'"""
        cards = []

        # Try PBN format first (dot-separated)
        if '.' in cards_str and ' ' not in cards_str:
            try:
                return Hand.from_pbn(cards_str)
            except Exception:
                pass

        # Try BDL format: S AKJ5 H Q87 D K43 C 1065
        parts = cards_str.split()
        current_suit = None

        suit_chars = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                      'D': Suit.DIAMONDS, 'C': Suit.CLUBS}

        for part in parts:
            part = part.upper()

            # Check if this is a suit indicator
            if part in suit_chars:
                current_suit = suit_chars[part]
                continue

            # Parse cards in current suit
            if current_suit is not None and part != '-':
                for char in part:
                    try:
                        rank = Rank.from_char(char)
                        cards.append(Card(current_suit, rank))
                    except (ValueError, KeyError):
                        pass

        return Hand(cards) if cards else None

    def _parse_bidding_line(self, line: str, deal: BDLDeal):
        """Parse bidding line"""
        # Skip header lines
        if line.upper() in ['N  E  S  W', 'BIDDING:', 'AUCTION:']:
            return

        # Split into individual bids
        parts = line.replace(',', ' ').split()

        for part in parts:
            part = part.strip().upper()
            if not part:
                continue

            # Skip non-bid tokens
            if part in ['N', 'E', 'S', 'W', 'BIDDING:', 'AUCTION:']:
                continue

            try:
                bid = Bid.from_str(part)
                deal.auction.append(bid)
            except (ValueError, IndexError, KeyError):
                # Bid.from_str raises KeyError when an unrecognised char
                # ends up at suit_char (e.g. "10" — int(s[0])=1 then
                # s[1]='0' fails the suit lookup). Silently skip junk.
                pass

    def _parse_alert_line(self, line: str, deal: BDLDeal):
        """Parse alert: 1D = "Could be 3-card minor" """
        match = re.match(r'(.+?)\s*=\s*["\'](.+?)["\']', line)
        if match:
            bid_str, explanation = match.groups()
            deal.alerts[bid_str.strip()] = explanation.strip()
        elif '=' in line:
            parts = line.split('=', 1)
            if len(parts) == 2:
                deal.alerts[parts[0].strip()] = parts[1].strip()

    def _parse_play_line(self, line: str, deal: BDLDeal):
        """Parse a trick line.

        Two formats are supported:

          1. Legacy "Trick 1: SQ SA S2 S3" — cards only, leader computed
             later from the contract.
          2. Q-Plus "1  N    h2   hA+  h6   h4      H " — leading number,
             leader seat, four cards (lowercase suits, optional ``+``/``-``
             markers), and a trailing trump-or-suit code.

        Cards are normalised to upper-case canonical strings (e.g. ``SA``)
        so the rest of the pipeline doesn't have to know which dialect
        wrote them.
        """
        body = line
        if ':' in body:
            body = body.split(':', 1)[1]
        body = body.strip()
        if not body:
            return

        tokens = body.split()
        leader = None

        # Q-Plus prefix: <trick_no> <leader>
        # Match the trick number first so the trailing tokens are uniform
        # regardless of whether a leader letter is present.
        if tokens and tokens[0].isdigit():
            tokens = tokens[1:]
        if tokens and tokens[0].upper() in ('N', 'E', 'S', 'W'):
            try:
                leader = Seat.from_char(tokens[0].upper())
            except Exception:
                leader = None
            tokens = tokens[1:]

        cards: List[str] = []
        for raw in tokens:
            t = raw.strip().rstrip('+-').upper()
            if len(t) < 2:
                continue
            if t[0] in 'SHDC' and len(t) >= 2:
                rank = t[1:]
                if rank in ('A', 'K', 'Q', 'J', 'T', '10',
                            '2', '3', '4', '5', '6', '7', '8', '9'):
                    cards.append(t)

        if cards:
            deal.tricks.append({"cards": cards, "leader": leader})

    def _parse_result_line(self, line: str, deal: BDLDeal):
        """Parse result field"""
        if ':' not in line:
            return

        key, value = line.split(':', 1)
        key = key.strip().lower()
        value = value.strip()

        if key in ['contract']:
            deal.contract = self._parse_contract(value)
        elif key in ['tricks', 'tricks made']:
            try:
                deal.tricks_made = int(value)
            except ValueError:
                pass
        elif key in ['score', 'ns score', 'n/s']:
            try:
                deal.ns_score = int(value)
                deal.ew_score = -deal.ns_score
            except ValueError:
                pass
        elif key in ['ew score', 'e/w']:
            try:
                deal.ew_score = int(value)
            except ValueError:
                pass

    def _parse_contract(self, value: str) -> Optional[Contract]:
        """Parse contract string like '4S', '3NTX', '6HXX'"""
        if not value or value.upper() in ['PASSED', 'PASSED OUT', 'ALL PASS']:
            return None

        value = value.upper().strip()

        # Extract level
        match = re.match(r'(\d)([SHDCN]T?)(X{0,2})?', value)
        if not match:
            return None

        level = int(match.group(1))
        suit_str = match.group(2)
        dbl_str = match.group(3) or ''

        # Parse suit
        if suit_str in ['N', 'NT']:
            suit = Suit.NOTRUMP
        else:
            suit = Suit.from_char(suit_str[0])

        doubled = 'X' in dbl_str and 'XX' not in dbl_str
        redoubled = 'XX' in dbl_str

        return Contract(
            level=level,
            suit=suit,
            doubled=doubled,
            redoubled=redoubled
        )

    def load_session_log(self, log_dir: Path, session_num: int) -> List[BDLDeal]:
        """Load a specific session log file"""
        filename = log_dir / f"log-{session_num:03d}.bdl"
        if not filename.exists():
            raise FileNotFoundError(f"Log file not found: {filename}")

        return self.read_file(filename)

    def find_log_files(self, log_dir: Path) -> List[Path]:
        """Find all BDL log files in directory"""
        if not log_dir.exists():
            return []

        return sorted(log_dir.glob("*.bdl"))

    def read_all_logs(self, log_dir: Path) -> List[BDLDeal]:
        """Read all BDL files in directory"""
        all_deals = []
        for log_file in self.find_log_files(log_dir):
            try:
                deals = self.read_file(log_file)
                all_deals.extend(deals)
            except Exception as e:
                print(f"Error reading {log_file}: {e}")
        return all_deals


def load_bdl_file(filepath: str) -> List[BDLDeal]:
    """Convenience function to load BDL file"""
    reader = BDLReader()
    return reader.read_file(Path(filepath))


def load_qss_file(filepath: str) -> List[BDLDeal]:
    """Load a Q-Plus .qss teams score export as a list of deals.

    A .qss interleaves per-board metadata (DI/DD/RE/IM/…) with a full BDL
    deal block whose every line is prefixed with "D1 " (or "D1"). Those
    blocks carry the hands, auction, contract and card-by-card play — the
    same content as a .bdl log — so we strip the "D1" prefix and reuse the
    BDL parser. Returns the same BDLDeal list as load_bdl_file, so the
    closed-room ingest (hands + contract + play) works identically.
    """
    out_lines: List[str] = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\n\r')
            if line.startswith('D1 '):
                out_lines.append(line[3:])
            elif line == 'D1':
                out_lines.append('')
    if not out_lines:
        return []
    # Lead with a separator so the reader opens the FIRST deal too (the first
    # D1 block has no preceding "****", unlike later ones).
    payload = ['****\n'] + [ln + '\n' for ln in out_lines]
    return BDLReader().read_lines(payload)


def bdl_deal_to_board_run(deal: BDLDeal, table=None) -> Optional['BenBoardRun']:
    """Convert a parsed BDLDeal into a BenBoardRun for the score sheet.

    BDL stores tricks as plain card strings in play order; we recover the
    leader/winner per trick by replaying the play with the contract's trump
    suit. Returns None if the BDL is too sparse to replay (no contract or
    no hands).
    """
    from .models import BenBoardRun, BenTable
    if table is None:
        table = BenTable.CLOSED

    if not deal.hands or not deal.contract:
        return None

    declarer = deal.declarer or deal.contract.declarer
    contract = deal.contract
    if declarer is None:
        # Couldn't pin a declarer — give up rather than fabricating one.
        return None
    contract.declarer = declarer

    rebuilt_tricks: List[Trick] = []
    declarer_tricks = 0
    next_leader = Seat((declarer.value + 1) % 4)  # opening leader
    trump = contract.suit if contract.suit != Suit.NOTRUMP else None

    for entry in deal.tricks:
        # Backward compatibility: older parses stored bare lists of card
        # strings; the new format wraps them as {"cards": …, "leader": …}.
        if isinstance(entry, dict):
            trick_cards = entry.get("cards") or []
            recorded_leader = entry.get("leader")
        else:
            trick_cards = entry
            recorded_leader = None

        cards: List[Card] = []
        for s in trick_cards:
            try:
                cards.append(Card.from_str(s))
            except Exception:
                pass
        if not cards:
            continue

        leader = recorded_leader if recorded_leader is not None else next_leader
        trick = Trick(cards=cards, leader=leader)
        # Trick._determine_winner runs only when 4 cards are pushed via
        # add_card. For a fully recorded trick we replay the determination.
        # Note: Rank is an IntEnum where ACE=0, …, TWO=12, so a *lower*
        # rank value is the higher card. Compare with `<` not `>`.
        if len(cards) == 4:
            lead_suit = cards[0].suit
            winning_idx = 0
            for i in range(1, 4):
                c = cards[i]
                w = cards[winning_idx]
                if trump is not None and c.suit == trump and w.suit != trump:
                    winning_idx = i
                elif c.suit == w.suit and c.rank < w.rank:
                    winning_idx = i
            trick.winner = Seat((leader.value + winning_idx) % 4)
            if trick.winner.is_ns() == declarer.is_ns():
                declarer_tricks += 1
            next_leader = trick.winner
        rebuilt_tricks.append(trick)

    if declarer_tricks == 0 and deal.tricks_made:
        # Some BDLs only record the count and skip per-trick details;
        # fall back to the recorded trick total.
        declarer_tricks = deal.tricks_made

    return BenBoardRun(
        table=table,
        board_number=deal.board_number,
        pavlicek_id=deal.pavlicek_id,
        original_hands=dict(deal.hands),
        auction=list(deal.auction),
        tricks=rebuilt_tricks,
        contract=contract,
        declarer_tricks=declarer_tricks,
        ns_score=deal.ns_score,
        ew_score=deal.ew_score,
        played=True,
        # System tags from the BDL header so the Compare dialog can
        # show "open=X / closed=Y" without flagging the closed-room
        # run as missing metadata.
        ns_bidding_system=deal.ns_bidding_system,
        ew_bidding_system=deal.ew_bidding_system,
    )


def load_deal_by_pavlicek(log_dir: str, pavlicek_id: str) -> Optional[BDLDeal]:
    """Find and load a deal by its Pavlicek ID"""
    reader = BDLReader()
    for deal in reader.read_all_logs(Path(log_dir)):
        if deal.pavlicek_id == pavlicek_id:
            return deal
    return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        filepath = Path(sys.argv[1])
        if filepath.exists():
            reader = BDLReader()
            deals = reader.read_file(filepath)
            print(f"Read {len(deals)} deals from {filepath}")
            for i, deal in enumerate(deals[:5]):
                print(f"\nDeal {i + 1}:")
                print(f"  Board: {deal.board_number}")
                print(f"  Pavlicek: {deal.pavlicek_id}")
                print(f"  Dealer: {deal.dealer.to_char()}")
                print(f"  Vul: {deal.vulnerability.value}")
                print(f"  Hands: {len(deal.hands)}")
                print(f"  Auction: {len(deal.auction)} bids")
                print(f"  Score: NS={deal.ns_score}")
        else:
            print(f"File not found: {filepath}")
    else:
        print("Usage: python bdl_reader.py <path_to_bdl_file>")
