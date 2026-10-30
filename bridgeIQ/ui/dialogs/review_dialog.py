"""
Review Dialog - step through a completed hand to review bidding and play.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QGroupBox, QGridLayout,
    QSplitter, QWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from .dialog_style import apply_dialog_style


class ReviewDialog(QDialog):
    """Dialog for reviewing a completed deal."""

    SUIT_SYMBOLS = {
        'S': '\u2660', 'H': '\u2665', 'D': '\u2666', 'C': '\u2663',
        'SPADES': '\u2660', 'HEARTS': '\u2665', 'DIAMONDS': '\u2666', 'CLUBS': '\u2663'
    }
    SUIT_COLORS = {
        'S': '#000000', 'H': '#cc0000', 'D': '#cc0000', 'C': '#000000',
        'SPADES': '#000000', 'HEARTS': '#cc0000', 'DIAMONDS': '#cc0000', 'CLUBS': '#000000'
    }

    def __init__(self, parent=None, board=None, auction=None, tricks=None,
                 contract=None, engine=None, original_hands=None,
                 table_view=None):
        super().__init__(parent)
        self.setWindowTitle("Review")
        self.setMinimumWidth(820)
        self.setMinimumHeight(560)
        apply_dialog_style(self)

        self.board = board
        self.auction = auction or []
        self.tricks = tricks or []
        self.contract = contract
        self.engine = engine
        self.current_position = 0
        self.max_position = len(self.auction) + len(self.tricks) * 4
        # The main-window TableView and the pre-play 13-card snapshot
        # let us animate the table as the user steps through positions:
        # each navigation tick reshapes the four hands to "remaining
        # cards" and places the in-progress trick in the centre. Either
        # being None falls back to text-only review (the old behaviour).
        self.original_hands = original_hands
        self.table_view = table_view

        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Navigation controls
        nav_frame = QFrame()
        nav_frame.setStyleSheet("""
            QFrame {
                background-color: #e8e8e8;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
            }
        """)
        nav_layout = QHBoxLayout(nav_frame)

        # Q-Plus-style button row: |<  <<-trick  <-card  -card->  ->-trick  >|
        # Tooltips spell out what each button does (the Q-Plus
        # original relied on iconography most users found cryptic).
        self.first_btn = QPushButton("|<")
        self.first_btn.setToolTip("Jump to start of deal")
        self.first_btn.setMaximumWidth(40)
        self.first_btn.clicked.connect(self._go_first)
        nav_layout.addWidget(self.first_btn)

        self.prev_trick_btn = QPushButton("<<")
        self.prev_trick_btn.setToolTip("Back one trick")
        self.prev_trick_btn.setMaximumWidth(40)
        self.prev_trick_btn.clicked.connect(self._go_prev_trick)
        nav_layout.addWidget(self.prev_trick_btn)

        self.prev_btn = QPushButton("<")
        self.prev_btn.setToolTip("Back one card / bid")
        self.prev_btn.setMaximumWidth(40)
        self.prev_btn.clicked.connect(self._go_prev)
        nav_layout.addWidget(self.prev_btn)

        self.position_label = QLabel("Position: 0 / 0")
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.position_label.setMinimumWidth(180)
        nav_layout.addWidget(self.position_label)

        self.next_btn = QPushButton(">")
        self.next_btn.setToolTip("Forward one card / bid")
        self.next_btn.setMaximumWidth(40)
        self.next_btn.clicked.connect(self._go_next)
        nav_layout.addWidget(self.next_btn)

        self.next_trick_btn = QPushButton(">>")
        self.next_trick_btn.setToolTip("Forward one trick")
        self.next_trick_btn.setMaximumWidth(40)
        self.next_trick_btn.clicked.connect(self._go_next_trick)
        nav_layout.addWidget(self.next_trick_btn)

        self.last_btn = QPushButton(">|")
        self.last_btn.setToolTip("Jump to end of deal")
        self.last_btn.setMaximumWidth(40)
        self.last_btn.clicked.connect(self._go_last)
        nav_layout.addWidget(self.last_btn)

        nav_layout.addStretch()

        self.analysis_btn = QPushButton("Analysis")
        self.analysis_btn.clicked.connect(self._on_analysis)
        nav_layout.addWidget(self.analysis_btn)

        self.actions_btn = QPushButton("Actions")
        self.actions_btn.clicked.connect(self._on_actions)
        nav_layout.addWidget(self.actions_btn)

        layout.addWidget(nav_frame)

        # Main content area
        content_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Auction display
        auction_group = QGroupBox("Auction")
        auction_layout = QVBoxLayout(auction_group)
        self.auction_display = QLabel()
        self.auction_display.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.auction_display.setWordWrap(True)
        self.auction_display.setStyleSheet("""
            QLabel {
                background-color: #ffffff;
                padding: 10px;
                font-family: monospace;
            }
        """)
        auction_layout.addWidget(self.auction_display)
        content_splitter.addWidget(auction_group)

        # Play display
        play_group = QGroupBox("Play")
        play_layout = QVBoxLayout(play_group)
        self.play_display = QLabel()
        self.play_display.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.play_display.setWordWrap(True)
        self.play_display.setStyleSheet("""
            QLabel {
                background-color: #ffffff;
                padding: 10px;
                font-family: monospace;
            }
        """)
        play_layout.addWidget(self.play_display)
        content_splitter.addWidget(play_group)

        layout.addWidget(content_splitter)

        # Current action display
        action_frame = QFrame()
        action_frame.setStyleSheet("""
            QFrame {
                background-color: #f0f0f0;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
            }
        """)
        action_layout = QHBoxLayout(action_frame)
        action_layout.addWidget(QLabel("Current action:"))
        self.action_label = QLabel("<i>Start of deal</i>")
        self.action_label.setStyleSheet("font-weight: bold;")
        action_layout.addWidget(self.action_label)
        action_layout.addStretch()
        layout.addWidget(action_frame)

        # Result display
        if self.contract:
            result_text = f"Contract: {self.contract}"
            result_label = QLabel(f"<b>{result_text}</b>")
            result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(result_label)

        # Buttons
        button_layout = QHBoxLayout()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setMinimumWidth(80)
        button_layout.addWidget(self.close_btn)

        button_layout.addStretch()

        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumWidth(80)
        button_layout.addWidget(self.help_btn)

        layout.addLayout(button_layout)

    def _format_auction(self):
        """Format the auction for display."""
        if not self.auction:
            return "<i>No auction</i>"

        # Determine dealer
        dealer_offset = 0
        if self.board and hasattr(self.board, 'dealer'):
            dealer_offset = self.board.dealer.value

        html = "<table style='border-collapse: collapse;'>"
        html += "<tr><th>N</th><th>E</th><th>S</th><th>W</th></tr>"

        # Add dashes for positions before dealer
        html += "<tr>"
        for i in range(4):
            if i < dealer_offset:
                html += "<td style='padding: 2px 8px; text-align: center;'>-</td>"
            else:
                break

        # Add bids
        bids_shown = 0
        for i, bid in enumerate(self.auction):
            pos_in_row = (dealer_offset + i) % 4
            if pos_in_row == 0 and i > 0:
                html += "</tr><tr>"

            # Highlight current bid
            bid_num = i + 1
            style = "padding: 2px 8px; text-align: center;"
            if bid_num <= self.current_position:
                bid_str = self._format_bid(bid)
            else:
                bid_str = "?"
                style += " color: #808080;"

            html += f"<td style='{style}'>{bid_str}</td>"

        html += "</tr></table>"
        return html

    def _format_bid(self, bid):
        """Format a single bid."""
        if hasattr(bid, 'is_pass') and bid.is_pass:
            return "Pass"
        elif hasattr(bid, 'is_double') and bid.is_double:
            return "X"
        elif hasattr(bid, 'is_redouble') and bid.is_redouble:
            return "XX"
        elif hasattr(bid, 'level') and hasattr(bid, 'suit'):
            level = bid.level
            if bid.suit:
                suit_name = bid.suit.name.upper()
                symbol = self.SUIT_SYMBOLS.get(suit_name, suit_name[0])
                color = self.SUIT_COLORS.get(suit_name, '#000000')
                return f"{level}<span style='color:{color}'>{symbol}</span>"
            return f"{level}NT"
        return str(bid)

    def _format_play(self):
        """Format the play for display.

        Consumes Trick objects (``cards: List[Card]``, ``leader:
        Seat``, ``winner: Seat``). Cards are laid out under the seat
        that played them — derived from ``leader + position`` so the
        N/E/S/W columns line up regardless of who led each trick.
        Already-revealed cards render normally; future cards show
        '?'. The winner gets a gold background, matching the
        replay viewer.
        """
        if not self.tricks:
            return "<i>No play</i>"

        cards_in_auction = len(self.auction)
        cards_shown = max(0, self.current_position - cards_in_auction)

        html = ("<table style='border-collapse: collapse;'>"
                "<tr><th>#</th><th>N</th><th>E</th><th>S</th>"
                "<th>W</th></tr>")

        seats = ['N', 'E', 'S', 'W']
        card_count = 0
        for trick_num, trick in enumerate(self.tricks):
            html += (f"<tr><td style='padding: 2px 4px;'>"
                     f"<b>{trick_num + 1}</b></td>")
            cards_list = list(getattr(trick, 'cards', []) or [])
            leader = getattr(trick, 'leader', None)
            winner = getattr(trick, 'winner', None)
            leader_idx = 0
            if leader is not None and hasattr(leader, 'value'):
                leader_idx = int(leader.value)
            winner_char = (seats[int(winner.value)]
                           if winner is not None
                           and hasattr(winner, 'value') else '')
            cards_by_seat = {}
            for j, c in enumerate(cards_list):
                seat_idx = (leader_idx + j) % 4
                cards_by_seat[seats[seat_idx]] = (c, j)
            trick_complete_at = card_count + len(cards_list) - 1
            for seat in seats:
                entry = cards_by_seat.get(seat)
                style = "padding: 2px 8px; text-align: center;"
                if entry is not None:
                    card, j_in_trick = entry
                    card_index_global = card_count + j_in_trick
                    if card_index_global < cards_shown:
                        card_str = self._format_card(card) if card else "-"
                        if (seat == winner_char
                                and trick_complete_at < cards_shown):
                            style += (" font-weight: bold;"
                                      " background-color: #ffd24a;"
                                      " color: #202020;")
                    else:
                        card_str = "?"
                        style += " color: #808080;"
                else:
                    card_str = ""
                html += f"<td style='{style}'>{card_str}</td>"
            card_count += len(cards_list)
            html += "</tr>"
        html += "</table>"
        return html

    def _format_card(self, card):
        """Format a single card."""
        if hasattr(card, 'suit') and hasattr(card, 'rank'):
            suit_name = card.suit.name.upper()
            symbol = self.SUIT_SYMBOLS.get(suit_name, suit_name[0])
            color = self.SUIT_COLORS.get(suit_name, '#000000')
            rank_char = self._rank_to_char(card.rank)
            return f"<span style='color:{color}'>{symbol}</span>{rank_char}"
        return str(card) if card else "-"

    def _rank_to_char(self, rank):
        """Convert rank to display character."""
        if hasattr(rank, 'value'):
            rank_val = rank.value
        else:
            rank_val = rank
        rank_chars = {12: 'A', 11: 'K', 10: 'Q', 9: 'J', 8: 'T'}
        return rank_chars.get(rank_val, str(rank_val + 2))

    def _update_action_label(self):
        """Update the current action label."""
        if self.current_position == 0:
            self.action_label.setText("<i>Start of deal</i>")
        elif self.current_position <= len(self.auction):
            bid_idx = self.current_position - 1
            if bid_idx < len(self.auction):
                bid = self.auction[bid_idx]
                bid_str = self._format_bid(bid)
                self.action_label.setText(f"Bid: {bid_str}")
        else:
            card_pos = self.current_position - len(self.auction) - 1
            trick_num = card_pos // 4
            card_in_trick = card_pos % 4
            self.action_label.setText(f"Trick {trick_num + 1}, card {card_in_trick + 1}")

    def _go_first(self):
        self.current_position = 0
        self._update_display()

    def _go_prev(self):
        if self.current_position > 0:
            self.current_position -= 1
            self._update_display()

    def _go_next(self):
        if self.current_position < self.max_position:
            self.current_position += 1
            self._update_display()

    def _go_last(self):
        self.current_position = self.max_position
        self._update_display()

    def _trick_boundary(self, n_back: int) -> int:
        """Return the position at the boundary of the ``n_back``-th
        trick before the current position (negative = forward)."""
        auction_len = len(self.auction)
        # Position is # bids + # cards (1-based linear counter).
        if self.current_position <= auction_len:
            # Inside auction.
            if n_back > 0:
                return 0
            # Forward from auction: jump to "end of trick 1".
            return min(self.max_position, auction_len + 4)
        cards_in = self.current_position - auction_len
        current_trick = (cards_in - 1) // 4  # 0-based
        target_trick = current_trick - n_back
        if target_trick < 0:
            return auction_len  # boundary between auction and play
        target_end = auction_len + (target_trick + 1) * 4
        if target_end > self.max_position:
            target_end = self.max_position
        return max(0, target_end)

    def _go_prev_trick(self):
        self.current_position = self._trick_boundary(1)
        self._update_display()

    def _go_next_trick(self):
        self.current_position = self._trick_boundary(-1)
        self._update_display()

    def _on_analysis(self):
        """Open the Deal Analysis modal — Q-Plus `.analys-ctl`.
        Picks the action set to evaluate (auction / card play, per
        seat) and on Start opens the Evaluation of Actions list."""
        from .deal_analysis import DealAnalysisDialog
        dlg = DealAnalysisDialog(
            self,
            board=self.board,
            auction=self.auction,
            tricks=self.tricks,
            engine=self.engine,
        )
        dlg.exec()

    def closeEvent(self, event):
        """Restore the table to its post-hand view when the dialog closes.

        While the dialog is open we mutate the live table_view to mirror
        the review position; on close we put it back the way it was before
        the review opened. A completed deal leaves the table blank (four
        full 13-card hands overflow the screen), so re-seed the end-of-hand
        snapshot for F2 / Open All Hands, then hide everything again.
        """
        if self.table_view is not None and self.original_hands:
            try:
                self.table_view.show_end_of_hand_view(
                    self.original_hands, self.tricks)
                self.table_view.hide_all_hands()
            except Exception:
                pass
        super().closeEvent(event)

    def _on_actions(self):
        """Q-Plus `.actionlist` — preparing user actions in advance.
        Not implemented in bridgeIQ; the live blunder check covers
        the equivalent ground in real time."""
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Actions",
            "Action preparation isn't implemented in bridgeIQ — "
            "the live blunder check during play covers the same "
            "ground (Configuration → Preferences → "
            "Blunder check).")

    def _update_display(self):
        """Override of the inherited stub so we can also light up the
        new prev/next-trick buttons based on context."""
        # Position label + auction + play.
        self.position_label.setText(
            f"Position: {self.current_position} / {self.max_position}")
        self.first_btn.setEnabled(self.current_position > 0)
        self.prev_btn.setEnabled(self.current_position > 0)
        self.next_btn.setEnabled(self.current_position < self.max_position)
        self.last_btn.setEnabled(self.current_position < self.max_position)
        self.prev_trick_btn.setEnabled(
            self.current_position > len(self.auction))
        self.next_trick_btn.setEnabled(
            self.current_position < self.max_position
            and len(self.tricks) > 0)
        self.auction_display.setText(self._format_auction())
        self.play_display.setText(self._format_play())
        self._update_action_label()
        self._sync_table_view()

    def _sync_table_view(self):
        """Push the current review position to the main-window table.

        While the position is still in the auction phase the table is
        left alone (no card has been played yet). Once we cross into
        the play phase, each step removes cards from the hands and
        drops them into the centre trick area on the main window.
        Falls back silently when no table_view / original_hands were
        provided.
        """
        if self.table_view is None or not self.original_hands:
            return
        auction_len = len(self.auction)
        cards_played = max(0, self.current_position - auction_len)
        try:
            self.table_view.show_review_position(
                self.original_hands, self.tricks, cards_played)
        except Exception:
            # The animation is purely cosmetic; never let it break the
            # text-only review flow.
            pass
