"""
Deal Converter Dialog - Convert between hand code and PBN formats.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QGroupBox, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from .dialog_style import apply_dialog_style

from backend.models import BoardState, Seat, Hand
from backend.pavlicek import (
    deal_to_hand_code, hand_code_to_deal,
    deal_to_number, number_to_deal, format_deal_base72,
    base72_to_int, int_to_base72
)


class DealConverterDialog(QDialog):
    """Dialog to convert between hand code (base-72) and PBN formats.

    Provides two text areas and conversion buttons to convert between:
    - Compact hand code (base-72 encoded Pavlicek number)
    - PBN deal format (N:hand E:hand S:hand W:hand)

    Also allows loading the converted deal into the table.
    """

    load_deal = pyqtSignal(object)  # Emits BoardState to load

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Convert Hand Code / PBN")
        self.setMinimumSize(550, 450)
        apply_dialog_style(self)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Info label
        info = QLabel(
            "Convert between compact hand codes and PBN deal notation.\n"
            "Hand codes use base-72 encoding of the Pavlicek deal number."
        )
        info.setWordWrap(True)
        info.setFont(QFont("Arial", 10))
        layout.addWidget(info)

        # Hand code section
        code_group = QGroupBox("Hand Code (Base-72)")
        code_layout = QVBoxLayout(code_group)

        self.code_edit = QTextEdit()
        self.code_edit.setMaximumHeight(60)
        self.code_edit.setPlaceholderText("Enter hand code here (e.g., 1aB2cD3eF4)")
        self.code_edit.setFont(QFont("Courier", 11))
        code_layout.addWidget(self.code_edit)

        layout.addWidget(code_group)

        # Conversion buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        code_to_pbn_btn = QPushButton("Code \u2192 PBN")
        code_to_pbn_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        code_to_pbn_btn.clicked.connect(self._on_code_to_pbn)
        btn_layout.addWidget(code_to_pbn_btn)

        pbn_to_code_btn = QPushButton("PBN \u2192 Code")
        pbn_to_code_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        pbn_to_code_btn.clicked.connect(self._on_pbn_to_code)
        btn_layout.addWidget(pbn_to_code_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # PBN section
        pbn_group = QGroupBox("PBN Deal Format")
        pbn_layout = QVBoxLayout(pbn_group)

        self.pbn_edit = QTextEdit()
        self.pbn_edit.setMaximumHeight(100)
        self.pbn_edit.setPlaceholderText(
            "Enter PBN deal here:\n"
            "N:AKQ.JT9.876.5432 QJT.876.543.AKQJ 987.AKQ.AKQ.T987 6543.5432.JT92.6"
        )
        self.pbn_edit.setFont(QFont("Courier", 10))
        pbn_layout.addWidget(self.pbn_edit)

        layout.addWidget(pbn_group)

        # Deal info section
        info_group = QGroupBox("Deal Information")
        info_layout = QVBoxLayout(info_group)

        self.deal_info = QLabel("No deal loaded")
        self.deal_info.setFont(QFont("Courier", 10))
        self.deal_info.setWordWrap(True)
        self.deal_info.setMinimumHeight(80)
        info_layout.addWidget(self.deal_info)

        layout.addWidget(info_group)

        # Action buttons
        action_layout = QHBoxLayout()

        load_btn = QPushButton("Load into Table")
        load_btn.setFont(QFont("Arial", 11))
        load_btn.clicked.connect(self._on_load)
        action_layout.addWidget(load_btn)

        action_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        action_layout.addWidget(close_btn)

        layout.addLayout(action_layout)

        # Store the currently converted deal
        self._current_hands = None

    def _on_code_to_pbn(self):
        """Convert hand code to PBN format."""
        code = self.code_edit.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "No Code", "Please enter a hand code to convert.")
            return

        try:
            # Try base-72 first
            hands = hand_code_to_deal(code)
            self._current_hands = hands

            # Generate PBN string
            pbn = self._hands_to_pbn(hands)
            self.pbn_edit.setText(pbn)

            # Show deal info
            self._show_deal_info(hands)

        except Exception as e:
            QMessageBox.warning(self, "Conversion Error",
                              f"Could not convert hand code:\n{e}")

    def _on_pbn_to_code(self):
        """Convert PBN format to hand code."""
        pbn = self.pbn_edit.toPlainText().strip()
        if not pbn:
            QMessageBox.warning(self, "No PBN", "Please enter a PBN deal to convert.")
            return

        try:
            # Parse PBN
            hands = self._pbn_to_hands(pbn)
            self._current_hands = hands

            # Generate hand code
            code = deal_to_hand_code(hands)
            self.code_edit.setText(code)

            # Show deal info
            self._show_deal_info(hands)

        except Exception as e:
            QMessageBox.warning(self, "Conversion Error",
                              f"Could not convert PBN deal:\n{e}")

    def _pbn_to_hands(self, pbn: str) -> dict:
        """Parse PBN deal string to hands dict."""
        # Handle "N:hand hand hand hand" format
        pbn = pbn.strip()

        if ':' in pbn:
            parts = pbn.split(':')
            first_seat = Seat.from_char(parts[0].strip())
            hands_str = parts[1].strip()
        else:
            first_seat = Seat.NORTH
            hands_str = pbn

        hand_strs = hands_str.split()

        if len(hand_strs) != 4:
            raise ValueError(f"Expected 4 hands, got {len(hand_strs)}")

        hands = {}
        for i, hand_str in enumerate(hand_strs):
            seat = Seat((first_seat.value + i) % 4)
            hands[seat] = Hand.from_pbn(hand_str)

        return hands

    def _hands_to_pbn(self, hands: dict) -> str:
        """Convert hands dict to PBN deal string."""
        hand_strs = []
        for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
            if seat in hands:
                hand_strs.append(hands[seat].to_pbn())
            else:
                hand_strs.append("-.-.-.—")

        return f"N:{' '.join(hand_strs)}"

    def _show_deal_info(self, hands: dict):
        """Display information about the deal."""
        lines = []

        # Calculate HCP for each player
        for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
            if seat in hands:
                hand = hands[seat]
                hcp = hand.hcp()
                dist = self._get_distribution(hand)
                lines.append(f"{seat.to_char()}: {hcp} HCP, {dist}")

        # Total HCP check
        total_hcp = sum(hands[s].hcp() for s in hands if s in hands)
        lines.append(f"\nTotal HCP: {total_hcp}")

        # Pavlicek number
        try:
            deal_num = deal_to_number(hands)
            base72 = format_deal_base72(deal_num)
            lines.append(f"\nPavlicek (base-72): {base72}")
        except Exception:
            pass

        self.deal_info.setText("\n".join(lines))

    def _get_distribution(self, hand: Hand) -> str:
        """Get distribution string for a hand (e.g., '5-4-3-1')."""
        from backend.models import Suit
        lengths = []
        for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
            lengths.append(hand.suit_length(suit))
        lengths.sort(reverse=True)
        return "-".join(str(l) for l in lengths)

    def _on_load(self):
        """Load the current deal into the table."""
        if self._current_hands is None:
            QMessageBox.warning(self, "No Deal",
                              "Please convert a hand code or PBN first.")
            return

        # Create a BoardState
        board = BoardState(
            board_number=1,
            hands=self._current_hands
        )

        # Set dealer and vulnerability based on board number
        dealer, vuln = BoardState._board_dealer_vuln(1)
        board.dealer = dealer
        board.vulnerability = vuln

        self.load_deal.emit(board)
        self.accept()

    def set_deal(self, hands: dict):
        """Set a deal to display (for editing/viewing)."""
        self._current_hands = hands

        # Update PBN display
        pbn = self._hands_to_pbn(hands)
        self.pbn_edit.setText(pbn)

        # Update code display
        try:
            code = deal_to_hand_code(hands)
            self.code_edit.setText(code)
        except Exception:
            self.code_edit.setText("")

        # Show info
        self._show_deal_info(hands)
