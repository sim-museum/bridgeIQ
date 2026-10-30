"""
Replay View Dialog - Replay viewer with navigation controls.
"""

from typing import Optional, List
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout, QWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .dialog_style import apply_dialog_style

from backend.models import (
    BenBoardRun, BoardState, Seat, Suit, Card, Trick, Contract, Hand
)


class MiniHandWidget(QWidget):
    """Small widget displaying a hand's cards."""

    FONT_SIZE = 28  # Large font for 1920x1080 readability

    def __init__(self, seat: Seat, parent=None):
        super().__init__(parent)
        self.seat = seat
        self.cards_by_suit: dict = {s: [] for s in Suit if s != Suit.NOTRUMP}

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        # Seat label
        self.seat_label = QLabel(f'<span style="font-size:{self.FONT_SIZE}px"><b>{self.seat.to_char()}</b></span>')
        self.seat_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.seat_label)

        # Suit lines
        from ..styles import get_suit_color
        self.suit_labels = {}
        suit_symbols = {Suit.SPADES: '\u2660', Suit.HEARTS: '\u2665',
                       Suit.DIAMONDS: '\u2666', Suit.CLUBS: '\u2663'}
        suit_names = {Suit.SPADES: 'spades', Suit.HEARTS: 'hearts',
                     Suit.DIAMONDS: 'diamonds', Suit.CLUBS: 'clubs'}

        fs = self.FONT_SIZE
        for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
            sym = suit_symbols[suit]
            color = get_suit_color(suit_names[suit])
            lbl = QLabel(f'<span style="color:{color};font-size:{fs}px">{sym}</span> <span style="font-size:{fs}px">-</span>')
            self.suit_labels[suit] = lbl
            layout.addWidget(lbl)

    def set_hand(self, hand: Hand, played_cards: List[Card] = None,
                 winning_cards: List[Card] = None, dd_tricks: dict = None):
        """Set the hand to display.

        Args:
            hand: The hand to display
            played_cards: Cards already played (shown dimmed)
            winning_cards: Cards that WON their trick (highlighted in
                gold). Subset of played_cards; the gold style takes
                precedence over the played-but-not-winner style.
            dd_tricks: {(suit, rank): tricks} for the seat to play (bcalc-style,
                backend.dd_cards): each playable card gets a small number, the
                most tricks this side can take if it plays that card; green for
                the best card(s), red for a card that gives tricks away.
        """
        if played_cards is None:
            played_cards = []
        if winning_cards is None:
            winning_cards = []

        from ..styles import get_suit_color
        suit_symbols = {Suit.SPADES: '\u2660', Suit.HEARTS: '\u2665',
                       Suit.DIAMONDS: '\u2666', Suit.CLUBS: '\u2663'}
        suit_names = {Suit.SPADES: 'spades', Suit.HEARTS: 'hearts',
                     Suit.DIAMONDS: 'diamonds', Suit.CLUBS: 'clubs'}

        winners_set = {(c.suit, c.rank) for c in winning_cards}

        fs = self.FONT_SIZE
        for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
            sym = suit_symbols[suit]
            color = get_suit_color(suit_names[suit])

            suit_cards = sorted([c for c in hand.cards if c.suit == suit],
                               key=lambda c: c.rank)

            if not suit_cards:
                card_str = "-"
            else:
                parts = []
                for card in suit_cards:
                    rank = card.rank.to_char()
                    if (card.suit, card.rank) in winners_set:
                        # Trick-winning card \u2014 gold background, dark
                        # text. Keep the suit-coloured pip elsewhere
                        # so the highlight reads at a glance.
                        parts.append(
                            "<span style='background-color:#ffd24a;"
                            "color:#202020;padding:0 3px;"
                            "border-radius:2px;font-weight:bold'>"
                            f"{rank}</span>")
                    elif card in played_cards:
                        parts.append(
                            "<span style='background-color:#404040;"
                            "color:#fff;padding:0 2px;"
                            f"border-radius:2px'>{rank}</span>")
                    elif dd_tricks and (card.suit, card.rank) in dd_tricks:
                        n = dd_tricks[(card.suit, card.rank)]
                        best = max(dd_tricks.values())
                        col = "#0b7a0b" if n == best else "#c62828"
                        parts.append(
                            f"{rank}<sub style='font-size:{int(fs * 0.65)}px;"
                            f"color:{col};font-weight:bold'>{n}</sub>")
                    else:
                        parts.append(rank)
                card_str = " ".join(parts)

            self.suit_labels[suit].setText(
                f'<span style="color:{color};font-size:{fs}px">{sym}</span> <span style="font-size:{fs}px">{card_str}</span>'
            )


class ReplayViewDialog(QDialog):
    """Replay viewer with navigation controls.

    Shows a mini table view of a completed board and allows
    stepping through the play trick by trick or card by card.
    """

    def __init__(self, board_run: BenBoardRun, parent=None):
        super().__init__(parent)

        self.board_run = board_run
        self.current_trick_idx = 0
        self.current_card_idx = 0  # Within current trick

        self.setWindowTitle(f"Replay: Board {board_run.board_number}")

        # Size to fill most of screen height
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().geometry()
        self.resize(850, int(screen.height() * 0.9))

        apply_dialog_style(self)

        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header with contract info
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #e0e8f0;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        header_layout = QHBoxLayout(header_frame)

        # Contract with 4-color suits
        from ..styles import get_suit_color
        if self.board_run.contract:
            contract = self.board_run.contract
            suit_symbols = ['\u2660', '\u2665', '\u2666', '\u2663', 'NT']
            suit_names = ['spades', 'hearts', 'diamonds', 'clubs', 'spades']
            suit_sym = suit_symbols[contract.suit.value]
            if contract.suit.value < 4:
                color = get_suit_color(suit_names[contract.suit.value])
                contract_str = f'{contract.level}<span style="color:{color}">{suit_sym}</span>'
            else:
                contract_str = f"{contract.level}NT"
            if contract.redoubled:
                contract_str += "XX"
            elif contract.doubled:
                contract_str += "X"
            contract_str += f" by {contract.declarer.to_char()}"
        else:
            contract_str = "Passed Out"

        contract_label = QLabel(f'<span style="font-size:20px"><b>{contract_str}</b></span>')
        header_layout.addWidget(contract_label)

        header_layout.addStretch()

        # Result
        if self.board_run.contract:
            target = self.board_run.contract.target_tricks()
            diff = self.board_run.declarer_tricks - target
            if diff == 0:
                result_str = "Made"
            elif diff > 0:
                result_str = f"+{diff}"
            else:
                result_str = f"{diff}"
            score_str = f"{self.board_run.ns_score:+d}"
        else:
            result_str = "-"
            score_str = "0"

        result_label = QLabel(f'<span style="font-size:18px">Result: {result_str} ({score_str})</span>')
        header_layout.addWidget(result_label)

        layout.addWidget(header_frame)

        # Table display - 4 hands arranged N/E/S/W
        table_frame = QFrame()
        table_frame.setStyleSheet("""
            QFrame {
                background-color: #f8f8f8;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
            }
        """)
        table_layout = QGridLayout(table_frame)
        table_layout.setSpacing(10)

        # North (row 0, col 1)
        self.north_hand = MiniHandWidget(Seat.NORTH)
        table_layout.addWidget(self.north_hand, 0, 1)

        # West (row 1, col 0)
        self.west_hand = MiniHandWidget(Seat.WEST)
        table_layout.addWidget(self.west_hand, 1, 0)

        # Center area - current trick
        center_frame = QFrame()
        center_frame.setStyleSheet("""
            QFrame {
                background-color: #e8ece8;
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                min-width: 120px;
                min-height: 80px;
            }
        """)
        center_layout = QVBoxLayout(center_frame)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.trick_label = QLabel('<span style="font-size:16px"><b>Trick 1</b></span>')
        self.trick_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.trick_label)

        self.trick_cards_label = QLabel("")
        self.trick_cards_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.trick_cards_label)

        table_layout.addWidget(center_frame, 1, 1)

        # East (row 1, col 2)
        self.east_hand = MiniHandWidget(Seat.EAST)
        table_layout.addWidget(self.east_hand, 1, 2)

        # South (row 2, col 1)
        self.south_hand = MiniHandWidget(Seat.SOUTH)
        table_layout.addWidget(self.south_hand, 2, 1)

        layout.addWidget(table_frame)

        # Tricks won display
        tricks_layout = QHBoxLayout()
        self.declarer_tricks_label = QLabel('<span style="font-size:16px">Declarer: 0</span>')
        tricks_layout.addWidget(self.declarer_tricks_label)

        tricks_layout.addStretch()

        self.defense_tricks_label = QLabel('<span style="font-size:16px">Defense: 0</span>')
        tricks_layout.addWidget(self.defense_tricks_label)

        layout.addLayout(tricks_layout)

        # Navigation controls
        nav_frame = QFrame()
        nav_frame.setStyleSheet("""
            QFrame {
                background-color: #f0f0f0;
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        nav_layout = QHBoxLayout(nav_frame)

        self.prev_trick_btn = QPushButton("<< Prev Trick")
        self.prev_trick_btn.clicked.connect(self._on_prev_trick)
        nav_layout.addWidget(self.prev_trick_btn)

        self.prev_card_btn = QPushButton("< Prev Card")
        self.prev_card_btn.clicked.connect(self._on_prev_card)
        nav_layout.addWidget(self.prev_card_btn)

        nav_layout.addStretch()

        self.position_label = QLabel("1/13")
        self.position_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        nav_layout.addWidget(self.position_label)

        nav_layout.addStretch()

        self.next_card_btn = QPushButton("Next Card >")
        self.next_card_btn.clicked.connect(self._on_next_card)
        nav_layout.addWidget(self.next_card_btn)

        self.next_trick_btn = QPushButton("Next Trick >>")
        self.next_trick_btn.clicked.connect(self._on_next_trick)
        nav_layout.addWidget(self.next_trick_btn)

        layout.addWidget(nav_frame)

        # Double dummy line + toggle (bcalc-style trick counts on the cards)
        self.dd_lbl = QLabel("")
        self.dd_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.dd_lbl.setWordWrap(True)
        layout.addWidget(self.dd_lbl)

        # Close button
        button_layout = QHBoxLayout()
        from PyQt6.QtWidgets import QCheckBox
        self.dd_cb = QCheckBox("Double-dummy tricks per card")
        self.dd_cb.setChecked(True)
        self.dd_cb.setToolTip(
            "As in bcalc: every card the player to move holds carries the most tricks "
            "that side can take if it plays that card. Green = best, red = gives tricks away.")
        self.dd_cb.toggled.connect(lambda _on: self._update_display())
        button_layout.addWidget(self.dd_cb)
        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _update_display(self):
        """Update the display based on current position."""
        tricks = self.board_run.tricks

        # Calculate which cards have been played
        played_cards = {seat: [] for seat in Seat}
        winning_cards = {seat: [] for seat in Seat}

        # Count through tricks and cards
        for t_idx in range(self.current_trick_idx):
            if t_idx < len(tricks):
                trick = tricks[t_idx]
                leader = trick.leader
                for c_idx, card in enumerate(trick.cards):
                    seat = Seat((leader.value + c_idx) % 4)
                    played_cards[seat].append(card)
                if trick.winner is not None:
                    win_idx = (trick.winner.value - leader.value) % 4
                    if 0 <= win_idx < len(trick.cards):
                        winning_cards[trick.winner].append(
                            trick.cards[win_idx])

        # Add cards from current trick up to current_card_idx
        if self.current_trick_idx < len(tricks):
            trick = tricks[self.current_trick_idx]
            leader = trick.leader
            for c_idx in range(min(self.current_card_idx, len(trick.cards))):
                card = trick.cards[c_idx]
                seat = Seat((leader.value + c_idx) % 4)
                played_cards[seat].append(card)

        # Double dummy (bcalc-style) for the player to move
        dd = None
        if getattr(self, "dd_cb", None) is not None and self.dd_cb.isChecked() \
                and self.board_run.contract:
            from backend import dd_cards
            step = sum(len(t.cards) for t in tricks[:self.current_trick_idx]) + (
                min(self.current_card_idx, len(tricks[self.current_trick_idx].cards))
                if self.current_trick_idx < len(tricks) else 0)
            dd = dd_cards.card_tricks(self.board_run.original_hands, tricks, step,
                                      self.board_run.contract.suit)
        if dd:
            c = self.board_run.contract
            best = max(dd[1].values())
            decl = best if dd[0].is_ns() == c.declarer.is_ns() else 13 - best
            col = "#0b7a0b" if decl >= c.target_tricks() else "#c62828"
            self.dd_lbl.setText(
                f'<span style="font-size:15px">Double dummy from here: declarer can take '
                f'<b style="color:{col}">{decl}</b> (needs {c.target_tricks()}); numbers on '
                f"{dd[0].to_char()}'s cards = tricks for that side if played.</span>")
        elif getattr(self, "dd_lbl", None) is not None:
            self.dd_lbl.setText("")

        # Update hands
        for seat, widget in [(Seat.NORTH, self.north_hand),
                            (Seat.EAST, self.east_hand),
                            (Seat.SOUTH, self.south_hand),
                            (Seat.WEST, self.west_hand)]:
            hand = self.board_run.original_hands.get(seat)
            if hand:
                widget.set_hand(
                    hand, played_cards[seat], winning_cards[seat],
                    dd[1] if (dd and dd[0] == seat) else None)

        # Update trick display
        if tricks and self.current_trick_idx < len(tricks):
            trick = tricks[self.current_trick_idx]
            self.trick_label.setText(f'<span style="font-size:16px"><b>Trick {self.current_trick_idx + 1}</b></span>')

            # Show cards played in current trick
            leader = trick.leader
            card_strs = []
            for c_idx in range(min(self.current_card_idx, len(trick.cards))):
                card = trick.cards[c_idx]
                seat = Seat((leader.value + c_idx) % 4)
                card_strs.append(f'<span style="font-size:16px">{seat.to_char()}: {self._format_card(card)}</span>')

            self.trick_cards_label.setText("<br>".join(card_strs) if card_strs else '<span style="font-size:16px">(lead)</span>')
        else:
            self.trick_label.setText('<span style="font-size:16px"><b>Complete</b></span>')
            self.trick_cards_label.setText("")

        # Update tricks won
        declarer_tricks = 0
        defense_tricks = 0

        if self.board_run.contract and tricks:
            declarer_ns = self.board_run.contract.declarer.is_ns()
            for t_idx in range(self.current_trick_idx):
                if t_idx < len(tricks):
                    trick = tricks[t_idx]
                    if trick.winner:
                        if trick.winner.is_ns() == declarer_ns:
                            declarer_tricks += 1
                        else:
                            defense_tricks += 1

        self.declarer_tricks_label.setText(f'<span style="font-size:16px">Declarer: {declarer_tricks}</span>')
        self.defense_tricks_label.setText(f'<span style="font-size:16px">Defense: {defense_tricks}</span>')

        # Update position label
        total_tricks = len(tricks) if tricks else 0
        self.position_label.setText(f'<span style="font-size:16px"><b>{self.current_trick_idx + 1}/{total_tricks}</b></span>')

        # Update button states
        self.prev_trick_btn.setEnabled(self.current_trick_idx > 0)
        self.prev_card_btn.setEnabled(self.current_trick_idx > 0 or self.current_card_idx > 0)
        self.next_card_btn.setEnabled(
            self.current_trick_idx < len(tricks) and
            (self.current_card_idx < 4 or self.current_trick_idx < len(tricks) - 1)
        )
        self.next_trick_btn.setEnabled(self.current_trick_idx < len(tricks) - 1)

    def _format_card(self, card: Card) -> str:
        """Format a card for display with 4-color suits."""
        from ..styles import get_suit_color
        suit_symbols = {Suit.SPADES: '\u2660', Suit.HEARTS: '\u2665',
                       Suit.DIAMONDS: '\u2666', Suit.CLUBS: '\u2663'}
        suit_names = {Suit.SPADES: 'spades', Suit.HEARTS: 'hearts',
                     Suit.DIAMONDS: 'diamonds', Suit.CLUBS: 'clubs'}
        color = get_suit_color(suit_names[card.suit])
        sym = suit_symbols[card.suit]
        rank = card.rank.to_char()
        return f'<span style="color:{color}">{sym}</span>{rank}'

    def _on_prev_trick(self):
        """Go to previous trick — show all 4 cards."""
        if self.current_trick_idx > 0:
            self.current_trick_idx -= 1
            self.current_card_idx = 4  # Show complete trick
            self._update_display()

    def _on_prev_card(self):
        """Go to previous card."""
        if self.current_card_idx > 0:
            self.current_card_idx -= 1
        elif self.current_trick_idx > 0:
            self.current_trick_idx -= 1
            self.current_card_idx = 4  # Show all cards of previous trick
        self._update_display()

    def _on_next_card(self):
        """Go to next card."""
        tricks = self.board_run.tricks
        if self.current_trick_idx < len(tricks):
            trick = tricks[self.current_trick_idx]
            if self.current_card_idx < len(trick.cards):
                self.current_card_idx += 1
            elif self.current_trick_idx < len(tricks) - 1:
                self.current_trick_idx += 1
                self.current_card_idx = 0
        self._update_display()

    def _on_next_trick(self):
        """Go to next trick — show all 4 cards."""
        tricks = self.board_run.tricks
        if self.current_trick_idx < len(tricks) - 1:
            self.current_trick_idx += 1
            self.current_card_idx = 4  # Show complete trick
            self._update_display()

    def _on_prev_trick_full(self):
        """Go to previous trick — show all 4 cards."""
        if self.current_trick_idx > 0:
            self.current_trick_idx -= 1
            self.current_card_idx = 4  # Show complete trick
            self._update_display()

    def keyPressEvent(self, event):
        """Keyboard shortcuts for replay navigation."""
        key = event.key()
        from PyQt6.QtCore import Qt
        if key == Qt.Key.Key_Right:
            self._on_next_card()
        elif key == Qt.Key.Key_Left:
            self._on_prev_card()
        elif key == Qt.Key.Key_PageDown or key == Qt.Key.Key_Down:
            self._on_next_trick()
        elif key == Qt.Key.Key_PageUp or key == Qt.Key.Key_Up:
            self._on_prev_trick_full()
        elif key == Qt.Key.Key_Home:
            self.current_trick_idx = 0
            self.current_card_idx = 0
            self._update_display()
        elif key == Qt.Key.Key_End:
            if self.board_run.tricks:
                self.current_trick_idx = len(self.board_run.tricks) - 1
                self.current_card_idx = 4
                self._update_display()
        elif key == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)
