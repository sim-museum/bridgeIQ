"""
PBN (Portable Bridge Notation) Exporter for BridgeIQ.

Exports deals, auctions, and play records to standard PBN format.
"""

from pathlib import Path
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass

from .models import Seat, Suit, Vulnerability, Contract, BoardState, Hand, Card, Bid


@dataclass
class PBNEvent:
    """Information about a bridge event/session"""
    name: str = "BridgeIQ Session"
    site: str = "Local"
    date: str = ""

    def __post_init__(self):
        if not self.date:
            self.date = datetime.now().strftime("%Y.%m.%d")


class PBNExporter:
    """Export deals and results to PBN format"""

    def __init__(self, version: str = "2.1"):
        self.version = version
        self.event = PBNEvent()

    def export_deal(self, board: BoardState, filepath: Path,
                    include_auction: bool = True,
                    include_play: bool = True):
        """Export single deal to PBN file"""
        lines = self._generate_pbn_lines(board, include_auction, include_play)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def export_session(self, boards: List[BoardState], filepath: Path,
                       include_auction: bool = True,
                       include_play: bool = True):
        """Export multiple deals to single PBN file"""
        all_lines = [f"% PBN {self.version}", ""]

        for board in boards:
            lines = self._generate_pbn_lines(board, include_auction, include_play,
                                             include_header=False)
            all_lines.extend(lines)
            all_lines.append("")  # Blank line between deals

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_lines))

    def _generate_pbn_lines(self, board: BoardState,
                            include_auction: bool = True,
                            include_play: bool = True,
                            include_header: bool = True) -> List[str]:
        """Generate PBN format lines for a deal"""
        lines = []

        if include_header:
            lines.append(f"% PBN {self.version}")
            lines.append("")

        # Required tags
        lines.append(f'[Event "{self.event.name}"]')
        lines.append(f'[Site "{self.event.site}"]')
        lines.append(f'[Date "{self.event.date}"]')
        lines.append(f'[Board "{board.board_number}"]')
        lines.append(f'[West ""]')
        lines.append(f'[North ""]')
        lines.append(f'[East ""]')
        lines.append(f'[South ""]')
        lines.append(f'[Dealer "{board.dealer.to_char()}"]')
        lines.append(f'[Vulnerable "{self._vul_to_pbn(board.vulnerability)}"]')
        lines.append(f'[Deal "{self._hands_to_pbn(board.hands, board.dealer)}"]')

        # Contract and declarer (if auction complete)
        if board.contract:
            lines.append(f'[Declarer "{board.contract.declarer.to_char()}"]')
            lines.append(f'[Contract "{self._contract_to_pbn(board.contract)}"]')

        # Result (if play complete)
        if board.declarer_tricks is not None and len(board.tricks) == 13:
            lines.append(f'[Result "{board.declarer_tricks}"]')

        # Scoring
        lines.append(f'[Scoring "IMP"]')

        # Auction
        if include_auction and board.auction:
            lines.append(f'[Auction "{board.dealer.to_char()}"]')
            auction_lines = self._format_auction_pbn(board.auction, board.dealer)
            lines.extend(auction_lines)

        # Play
        if include_play and board.tricks:
            opening_leader = board.contract.declarer.next() if board.contract else board.dealer
            lines.append(f'[Play "{opening_leader.to_char()}"]')
            play_lines = self._format_play_pbn(board.tricks)
            lines.extend(play_lines)

        return lines

    def _vul_to_pbn(self, vulnerability: Vulnerability) -> str:
        """Convert vulnerability to PBN format"""
        vul_map = {
            Vulnerability.NONE: "None",
            Vulnerability.NS: "NS",
            Vulnerability.EW: "EW",
            Vulnerability.BOTH: "All"
        }
        return vul_map.get(vulnerability, "None")

    def _hands_to_pbn(self, hands: dict, dealer: Seat) -> str:
        """Format hands in PBN Deal format

        Format: N:SAK.HQJ.DT98.C765 SAT.H987.DQJ6.C432 S765.HK.DAK7.CAKQJ SQJ98.HAT6.D54.CT98
        """
        def format_hand(hand: Hand) -> str:
            suits = []
            for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
                suit_cards = hand.get_suit_cards(suit)
                # Sort by rank descending
                suit_cards_sorted = sorted(suit_cards, key=lambda c: c.rank.value, reverse=True)
                cards_str = ''.join(c.rank.to_char() for c in suit_cards_sorted)
                if not cards_str:
                    cards_str = '-'
                suits.append(cards_str)
            return '.'.join(suits)

        # Order: N, E, S, W (always starting from North)
        order = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]
        hand_strings = [format_hand(hands[seat]) for seat in order]
        return f"N:{' '.join(hand_strings)}"

    def _contract_to_pbn(self, contract: Contract) -> str:
        """Convert contract to PBN format (e.g., '3NTX', '4HXX')"""
        level = str(contract.level)
        suit = contract.suit.pbn_char() if contract.suit != Suit.NOTRUMP else "NT"

        result = f"{level}{suit}"
        if contract.redoubled:
            result += "XX"
        elif contract.doubled:
            result += "X"

        return result

    def _format_auction_pbn(self, auction: List[Bid], dealer: Seat) -> List[str]:
        """Format auction for PBN"""
        lines = []
        bids_per_line = []

        for bid in auction:
            bid_str = self._bid_to_pbn(bid)
            bids_per_line.append(bid_str)

            # Output 4 bids per line
            if len(bids_per_line) == 4:
                lines.append(' '.join(bids_per_line))
                bids_per_line = []

        # Output remaining bids
        if bids_per_line:
            lines.append(' '.join(bids_per_line))

        return lines

    def _bid_to_pbn(self, bid: Bid) -> str:
        """Convert bid to PBN format"""
        if bid.is_pass:
            return "Pass"
        elif bid.is_double:
            return "X"
        elif bid.is_redouble:
            return "XX"
        else:
            suit = bid.suit.pbn_char() if bid.suit != Suit.NOTRUMP else "NT"
            return f"{bid.level}{suit}"

    def _format_play_pbn(self, tricks: list) -> List[str]:
        """Format play for PBN

        Each trick on one line: S2 HK D3 C4
        """
        lines = []
        for trick in tricks:
            cards = [self._card_to_pbn(card) for card in trick.cards]
            lines.append(' '.join(cards))
        return lines

    def _card_to_pbn(self, card: Card) -> str:
        """Convert card to PBN format (e.g., 'SA' for Ace of Spades)"""
        suit = card.suit.pbn_char()
        rank = card.rank.to_char()
        return f"{suit}{rank}"


class PBNParser:
    """Parse PBN format files"""

    def __init__(self):
        self.boards: List[BoardState] = []

    def parse_file(self, filepath: Path) -> List[BoardState]:
        """Parse PBN file and return list of boards"""
        self.boards = []

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split into separate deals (blank line separates)
        deal_texts = content.split('\n\n')

        for deal_text in deal_texts:
            if not deal_text.strip():
                continue

            board = self._parse_deal(deal_text)
            if board:
                self.boards.append(board)

        return self.boards

    def _parse_deal(self, text: str) -> Optional[BoardState]:
        """Parse a single deal from PBN text"""
        tags = {}

        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                # Parse tag
                match = line[1:-1]
                if ' ' in match:
                    key, value = match.split(' ', 1)
                    # Remove quotes from value
                    value = value.strip('"')
                    tags[key] = value

        if 'Deal' not in tags:
            return None

        try:
            board = BoardState.from_pbn_deal(tags['Deal'])

            # Apply other tags
            if 'Board' in tags:
                try:
                    board.board_number = int(tags['Board'])
                except ValueError:
                    pass

            if 'Dealer' in tags:
                dealer_map = {'N': Seat.NORTH, 'E': Seat.EAST,
                             'S': Seat.SOUTH, 'W': Seat.WEST}
                if tags['Dealer'] in dealer_map:
                    board.dealer = dealer_map[tags['Dealer']]

            if 'Vulnerable' in tags:
                vul_map = {
                    'None': Vulnerability.NONE,
                    'NS': Vulnerability.NS,
                    'EW': Vulnerability.EW,
                    'All': Vulnerability.BOTH,
                    'Both': Vulnerability.BOTH
                }
                if tags['Vulnerable'] in vul_map:
                    board.vulnerability = vul_map[tags['Vulnerable']]

            return board

        except Exception:
            return None
