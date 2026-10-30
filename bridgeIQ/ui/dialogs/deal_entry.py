"""
Deal Entry Dialog - Enter bridge deals in various formats.
Supports Pickboard, PBN, and hand code formats.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QComboBox, QGridLayout, QFrame,
    QMessageBox, QTextEdit, QRadioButton, QButtonGroup
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from backend.models import (
    Seat, Suit, Vulnerability, Hand, Card, Rank, BoardState
)
from backend.pavlicek import number_to_deal, parse_deal_number
from typing import Optional, Dict

from .dialog_style import apply_dialog_style


class DealEntryDialog(QDialog):
    """Dialog for entering a bridge deal in various formats."""

    def __init__(self, parent=None, board: Optional[BoardState] = None):
        super().__init__(parent)
        self.board = board
        self.result_board: Optional[BoardState] = None

        self.setWindowTitle("Enter Deal")
        self.setMinimumWidth(550)
        self.setMinimumHeight(450)
        apply_dialog_style(self)
        self._setup_ui()

        if board:
            self._load_board(board)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Deal info
        info_group = QGroupBox("Deal Information")
        info_layout = QGridLayout()

        info_layout.addWidget(QLabel("Board Number:"), 0, 0)
        self.board_num_entry = QLineEdit("1")
        self.board_num_entry.setMaximumWidth(80)
        info_layout.addWidget(self.board_num_entry, 0, 1)

        info_layout.addWidget(QLabel("Dealer:"), 0, 2)
        self.dealer_combo = QComboBox()
        self.dealer_combo.addItems(["North", "East", "South", "West"])
        info_layout.addWidget(self.dealer_combo, 0, 3)

        info_layout.addWidget(QLabel("Vulnerability:"), 0, 4)
        self.vuln_combo = QComboBox()
        self.vuln_combo.addItems(["None", "N/S", "E/W", "Both"])
        info_layout.addWidget(self.vuln_combo, 0, 5)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # Format selection
        format_group = QGroupBox("Input Format")
        format_layout = QVBoxLayout()

        self.format_buttons = QButtonGroup(self)

        self.pbn_radio = QRadioButton("PBN Deal String")
        self.pbn_radio.setChecked(True)
        self.format_buttons.addButton(self.pbn_radio, 0)
        format_layout.addWidget(self.pbn_radio)

        pbn_hint = QLabel("  Example: N:AKQ2.KJ3.T92.A85 J74.A95.Q84.KQ73 T986.QT2.AK5.J42 53.8764.J763.T96")
        pbn_hint.setStyleSheet("color: #606060; font-size: 11px;")
        pbn_hint.setWordWrap(True)
        format_layout.addWidget(pbn_hint)

        self.code_radio = QRadioButton("Pavlicek/Pickboard Code")
        self.format_buttons.addButton(self.code_radio, 1)
        format_layout.addWidget(self.code_radio)

        code_hint = QLabel("  Example: 6agBeN!sWCn#IdjB (base-72) or large decimal number")
        code_hint.setStyleSheet("color: #606060; font-size: 11px;")
        format_layout.addWidget(code_hint)

        self.lin_radio = QRadioButton("LIN/Hand Record Format")
        self.format_buttons.addButton(self.lin_radio, 2)
        format_layout.addWidget(self.lin_radio)

        lin_hint = QLabel("  Example: S:AKQ2,HKJ3,DT92,CA85|...")
        lin_hint.setStyleSheet("color: #606060; font-size: 11px;")
        format_layout.addWidget(lin_hint)

        format_group.setLayout(format_layout)
        layout.addWidget(format_group)

        # Main input area
        input_group = QGroupBox("Enter Deal")
        input_layout = QVBoxLayout()

        self.deal_input = QTextEdit()
        self.deal_input.setPlaceholderText(
            "Enter deal in selected format...\n\n"
            "PBN: N:AKQ2.KJ3.T92.A85 J74.A95.Q84.KQ73 T986.QT2.AK5.J42 53.8764.J763.T96\n\n"
            "Code: Pavlicek number or base-72 code\n\n"
            "Hands are separated by spaces, suits by dots (SHDC order)"
        )
        self.deal_input.setMinimumHeight(120)
        self.deal_input.setFont(QFont("Courier New", 11))
        self.deal_input.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #a0a0a0;
                padding: 8px;
            }
        """)
        input_layout.addWidget(self.deal_input)

        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        # Preview area
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout()

        self.preview_label = QLabel()
        self.preview_label.setFont(QFont("Courier New", 10))
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.preview_label.setMinimumHeight(100)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #f8f8f8;
                border: 1px solid #c0c0c0;
                padding: 8px;
            }
        """)
        self.preview_label.setText("Enter a deal above and click Parse to preview...")
        preview_layout.addWidget(self.preview_label)

        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # Buttons
        button_layout = QHBoxLayout()

        parse_btn = QPushButton("Parse")
        parse_btn.clicked.connect(self._parse_input)
        parse_btn.setMinimumWidth(80)
        button_layout.addWidget(parse_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_all)
        clear_btn.setMinimumWidth(80)
        button_layout.addWidget(clear_btn)

        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._on_ok)
        ok_btn.setMinimumWidth(80)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setMinimumWidth(80)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _load_board(self, board: BoardState):
        """Load existing board into dialog."""
        self.board_num_entry.setText(str(board.board_number))

        dealer_idx = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST].index(board.dealer)
        self.dealer_combo.setCurrentIndex(dealer_idx)

        vuln_map = {
            Vulnerability.NONE: 0,
            Vulnerability.NS: 1,
            Vulnerability.EW: 2,
            Vulnerability.BOTH: 3
        }
        self.vuln_combo.setCurrentIndex(vuln_map.get(board.vulnerability, 0))

        # Convert to PBN and show
        pbn = self._board_to_pbn(board)
        self.deal_input.setText(pbn)
        self._update_preview(board)

    def _board_to_pbn(self, board: BoardState) -> str:
        """Convert a board to PBN deal string."""
        seats = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]
        hands = []
        for seat in seats:
            if seat in board.hands:
                hand = board.hands[seat]
                suit_strs = []
                for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
                    cards = [c for c in hand.cards if c.suit == suit]
                    cards.sort(key=lambda c: c.rank.value, reverse=True)
                    suit_str = "".join(c.rank.to_char() for c in cards)
                    suit_strs.append(suit_str if suit_str else "-")
                hands.append(".".join(suit_strs))
            else:
                hands.append("-.-.-.--")
        return "N:" + " ".join(hands)

    def _clear_all(self):
        """Clear all entries."""
        self.deal_input.clear()
        self.preview_label.setText("Enter a deal above and click Parse to preview...")
        self.result_board = None

    def _parse_input(self):
        """Parse input based on selected format."""
        text = self.deal_input.toPlainText().strip()
        if not text:
            self.preview_label.setText("No input to parse")
            return

        board = None
        error = None

        # Try selected format first
        format_id = self.format_buttons.checkedId()

        if format_id == 0:  # PBN
            board, error = self._parse_pbn(text)
        elif format_id == 1:  # Pavlicek/Code
            board, error = self._parse_code(text)
        elif format_id == 2:  # LIN
            board, error = self._parse_lin(text)

        # If selected format fails, try auto-detect
        if board is None and error:
            # Try other formats
            for parser in [self._parse_pbn, self._parse_code, self._parse_lin]:
                board, _ = parser(text)
                if board:
                    break

        if board:
            self.result_board = board
            self._update_preview(board)
        else:
            self.preview_label.setText(f"Parse error: {error}\n\nTry a different format or check your input.")
            self.result_board = None

    def _parse_pbn(self, text: str) -> tuple:
        """Parse PBN deal string."""
        try:
            # Handle various PBN formats
            text = text.strip()

            # Remove [Deal "..."] wrapper if present
            if text.startswith('[Deal'):
                import re
                match = re.search(r'"([^"]+)"', text)
                if match:
                    text = match.group(1)

            board = BoardState.from_pbn_deal(text)
            return board, None
        except Exception as e:
            return None, str(e)

    def _parse_code(self, text: str) -> tuple:
        """Parse Pavlicek/Pickboard code."""
        try:
            text = text.strip()
            deal_num = parse_deal_number(text)
            if deal_num:
                hands = number_to_deal(deal_num)
                board = BoardState(
                    board_number=1,
                    dealer=Seat.NORTH,
                    vulnerability=Vulnerability.NONE,
                    hands=hands
                )
                return board, None
            return None, "Invalid deal code"
        except Exception as e:
            return None, str(e)

    def _parse_lin(self, text: str) -> tuple:
        """Parse LIN/hand record format."""
        try:
            text = text.strip()

            # LIN format: md|3SAK2HKJ3DT92CA85,SJ74HA95DQ84CKQ73,...|
            if 'md|' in text.lower():
                import re
                match = re.search(r'md\|(\d)([^|]+)\|', text, re.IGNORECASE)
                if match:
                    dealer_num = int(match.group(1))
                    hands_str = match.group(2)
                    # Parse hands
                    # This is simplified - full LIN parsing would be more complex
                    pass

            # Simple format: S:AKQ2,HKJ3,DT92,CA85|...
            if '|' in text and ':' in text:
                hands = {}
                parts = text.split('|')
                seat_order = [Seat.SOUTH, Seat.WEST, Seat.NORTH, Seat.EAST]

                for i, part in enumerate(parts[:4]):
                    if ':' in part:
                        part = part.split(':')[1]
                    cards = self._parse_lin_hand(part)
                    if cards:
                        hands[seat_order[i]] = Hand(cards=cards)

                if len(hands) == 4:
                    board = BoardState(
                        board_number=1,
                        dealer=Seat.NORTH,
                        vulnerability=Vulnerability.NONE,
                        hands=hands
                    )
                    return board, None

            return None, "Could not parse LIN format"
        except Exception as e:
            return None, str(e)

    def _parse_lin_hand(self, text: str) -> list:
        """Parse a single hand in LIN format (e.g., SAKQ2,HKJ3,DT92,CA85)."""
        cards = []
        current_suit = None
        suit_map = {'S': Suit.SPADES, 'H': Suit.HEARTS, 'D': Suit.DIAMONDS, 'C': Suit.CLUBS}

        text = text.replace(',', '')
        for char in text.upper():
            if char in suit_map:
                current_suit = suit_map[char]
            elif current_suit and char in 'AKQJT98765432':
                try:
                    rank = Rank.from_char(char)
                    cards.append(Card(current_suit, rank))
                except:
                    pass

        return cards if len(cards) == 13 else None

    def _update_preview(self, board: BoardState):
        """Update the preview display."""
        lines = []

        # North
        if Seat.NORTH in board.hands:
            n = self._format_hand(board.hands[Seat.NORTH])
            lines.append(f"        North")
            lines.append(f"        {n}")

        lines.append("")

        # West and East
        w = self._format_hand(board.hands.get(Seat.WEST))
        e = self._format_hand(board.hands.get(Seat.EAST))
        lines.append(f"West              East")
        lines.append(f"{w}    {e}")

        lines.append("")

        # South
        if Seat.SOUTH in board.hands:
            s = self._format_hand(board.hands[Seat.SOUTH])
            lines.append(f"        South")
            lines.append(f"        {s}")

        self.preview_label.setText("\n".join(lines))

    def _format_hand(self, hand: Optional[Hand]) -> str:
        """Format a hand for display."""
        if not hand:
            return "-.-.-.--"

        suit_strs = []
        for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
            cards = [c for c in hand.cards if c.suit == suit]
            cards.sort(key=lambda c: c.rank.value, reverse=True)
            suit_str = "".join(c.rank.to_char() for c in cards)
            suit_strs.append(suit_str if suit_str else "-")
        return ".".join(suit_strs)

    def _on_ok(self):
        """Handle OK button."""
        # Parse if not already done
        if self.result_board is None:
            self._parse_input()

        if self.result_board is None:
            QMessageBox.warning(self, "Invalid Deal",
                              "Please enter a valid deal and click Parse first.")
            return

        # Update board number and other settings
        try:
            board_num = int(self.board_num_entry.text())
        except ValueError:
            board_num = 1

        dealer = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST][self.dealer_combo.currentIndex()]
        vuln = [Vulnerability.NONE, Vulnerability.NS, Vulnerability.EW, Vulnerability.BOTH][self.vuln_combo.currentIndex()]

        self.result_board = BoardState(
            board_number=board_num,
            dealer=dealer,
            vulnerability=vuln,
            hands=self.result_board.hands
        )

        self.accept()

    def get_board(self) -> Optional[BoardState]:
        """Get the entered board."""
        return self.result_board
