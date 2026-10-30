"""
BiddingBox - Bidding interface.
Grid of buttons for Pass, X, XX, and all contract bids 1C through 7NT.
"""

from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QCheckBox, QLineEdit,
    QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from backend.models import Bid, Suit, Seat
from typing import Optional, List


class BidButton(QPushButton):
    """A single bid button"""

    bid_made = pyqtSignal(object)  # Emits Bid

    @staticmethod
    def get_suit_colors(suit: Suit) -> tuple:
        """Get background and foreground colors for a suit."""
        from .styles import get_suit_color, get_suit_bg_color
        suit_names = {
            Suit.CLUBS: 'clubs',
            Suit.DIAMONDS: 'diamonds',
            Suit.HEARTS: 'hearts',
            Suit.SPADES: 'spades',
        }
        if suit == Suit.NOTRUMP:
            return ('#f0f0f0', '#333333')
        suit_name = suit_names.get(suit, 'spades')
        return (get_suit_bg_color(suit_name), get_suit_color(suit_name))

    def __init__(self, bid: Bid, parent=None):
        super().__init__(parent)
        self.bid = bid

        self.setFixedSize(52, 38)  # Slightly larger buttons
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._on_click)

        self._setup_style()

    def _setup_style(self):
        if self.bid.is_pass:
            self.setText("Pass")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #e8f8e8;
                    color: #006400;
                    font-weight: bold;
                    font-size: 14px;
                    border: 1px solid #888;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #d0f0d0;
                    border: 2px solid #006400;
                }
                QPushButton:disabled {
                    background-color: #e8e8e8;
                    color: #aaa;
                }
            """)
        elif self.bid.is_double:
            self.setText("X")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #f8e8e8;
                    color: #8B0000;
                    font-weight: bold;
                    font-size: 18px;
                    border: 1px solid #888;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #f0d0d0;
                    border: 2px solid #8B0000;
                }
                QPushButton:disabled {
                    background-color: #e8e8e8;
                    color: #aaa;
                }
            """)
        elif self.bid.is_redouble:
            self.setText("XX")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #e8e8f8;
                    color: #00008B;
                    font-weight: bold;
                    font-size: 15px;
                    border: 1px solid #888;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #d0d0f0;
                    border: 2px solid #00008B;
                }
                QPushButton:disabled {
                    background-color: #e8e8e8;
                    color: #aaa;
                }
            """)
        else:
            suit_symbol = self.bid.suit.symbol()
            self.setText(f"{self.bid.level}{suit_symbol}")
            bg_color, fg_color = self.get_suit_colors(self.bid.suit)
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_color};
                    color: {fg_color};
                    font-weight: bold;
                    font-size: 16px;
                    border: 1px solid #888;
                    border-radius: 3px;
                }}
                QPushButton:hover {{
                    border: 2px solid #FFD700;
                }}
                QPushButton:disabled {{
                    background-color: #e8e8e8;
                    color: #aaa;
                }}
            """)

    def _on_click(self):
        self.bid_made.emit(self.bid)

    def refresh_colors(self):
        """Refresh colors after preference change."""
        # Only update colors for suit bids, don't rebuild everything
        if not self.bid.is_pass and not self.bid.is_double and not self.bid.is_redouble:
            bg_color, fg_color = self.get_suit_colors(self.bid.suit)
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_color};
                    color: {fg_color};
                    font-weight: bold;
                    font-size: 16px;
                    border: 1px solid #888;
                    border-radius: 3px;
                }}
                QPushButton:hover {{
                    border: 2px solid #FFD700;
                }}
                QPushButton:disabled {{
                    background-color: #e8e8e8;
                    color: #aaa;
                }}
            """)


class AuctionDisplay(QFrame):
    """Display of the current auction"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.auction: List[Bid] = []
        self.dealer: Seat = Seat.NORTH

        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        self.setMinimumHeight(120)
        self.setStyleSheet("QFrame { background-color: #f0f0f0; }")

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header row - larger fonts
        header = QHBoxLayout()
        for seat_name in ['N', 'E', 'S', 'W']:
            lbl = QLabel(seat_name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Arial", 16, QFont.Weight.Bold))
            lbl.setFixedWidth(60)
            header.addWidget(lbl)
        layout.addLayout(header)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        # Scrollable auction area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.auction_widget = QWidget()
        self.auction_layout = QGridLayout(self.auction_widget)
        self.auction_layout.setSpacing(2)
        self.auction_layout.setContentsMargins(2, 2, 2, 2)

        scroll.setWidget(self.auction_widget)
        layout.addWidget(scroll)

    def set_auction(self, auction: List[Bid], dealer: Seat):
        """Update the auction display"""
        self.auction = auction
        self.dealer = dealer

        # Clear existing
        while self.auction_layout.count():
            item = self.auction_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add bids
        start_col = dealer.value
        for i, bid in enumerate(auction):
            row = (start_col + i) // 4
            col = (start_col + i) % 4

            label = QLabel(bid.symbol())
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedWidth(60)
            label.setFont(QFont("Arial", 15))

            # Color based on bid type
            if bid.is_pass:
                label.setStyleSheet("color: #006400;")
            elif bid.is_double:
                label.setStyleSheet("color: #8B0000; font-weight: bold;")
            elif bid.is_redouble:
                label.setStyleSheet("color: #00008B; font-weight: bold;")
            else:
                color = BidButton.get_suit_colors(bid.suit)[1]
                label.setStyleSheet(f"color: {color}; font-weight: bold;")

            self.auction_layout.addWidget(label, row, col)

    def add_bid(self, bid: Bid):
        """Add a single bid to the display"""
        self.auction.append(bid)
        self.set_auction(self.auction, self.dealer)


class BiddingBox(QWidget):
    """
    Complete bidding interface with bid buttons and auction display.
    BridgeIQ 17 style.
    """

    bid_selected = pyqtSignal(object)  # Emits Bid
    alert_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_bidder: Optional[Seat] = None
        self.min_bid_level = 1
        self.min_bid_suit = Suit.CLUBS
        self.can_double = False
        self.can_redouble = False
        self.bid_buttons: List[BidButton] = []
        self.suit_headers: List[QLabel] = []  # Store suit header labels for color refresh

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)

        # Current bidder label - larger
        self.bidder_label = QLabel("Bidder: -")
        self.bidder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bidder_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        main_layout.addWidget(self.bidder_label)

        # Auction display intentionally NOT shown here — the centre
        # green-felt overlay (BiddingTableWidget) already renders the
        # auction in dealer-first columns, and having a second copy in
        # the right panel was confusing. We still construct the
        # widget so set_auction() / clear() / refresh_colors() can
        # call into it without conditional guards; it's just hidden.
        self.auction_display = AuctionDisplay()
        self.auction_display.setVisible(False)

        # Bid buttons frame
        buttons_frame = QFrame()
        buttons_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        buttons_layout = QVBoxLayout(buttons_frame)
        buttons_layout.setSpacing(4)
        buttons_layout.setContentsMargins(8, 8, 8, 8)

        # Special bids row (Pass, X, XX)
        special_row = QHBoxLayout()
        special_row.setSpacing(4)

        self.pass_btn = BidButton(Bid.make_pass())
        self.pass_btn.bid_made.connect(self._on_bid)
        special_row.addWidget(self.pass_btn)

        self.double_btn = BidButton(Bid.make_double())
        self.double_btn.bid_made.connect(self._on_bid)
        special_row.addWidget(self.double_btn)

        self.redouble_btn = BidButton(Bid.make_redouble())
        self.redouble_btn.bid_made.connect(self._on_bid)
        special_row.addWidget(self.redouble_btn)

        special_row.addStretch()
        buttons_layout.addLayout(special_row)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        buttons_layout.addWidget(line)

        # Contract bids grid (7 levels x 5 suits)
        grid = QGridLayout()
        grid.setSpacing(2)

        # Suit headers - larger symbols
        suit_order = [Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES, Suit.NOTRUMP]
        suit_names = {Suit.CLUBS: 'clubs', Suit.DIAMONDS: 'diamonds',
                      Suit.HEARTS: 'hearts', Suit.SPADES: 'spades', Suit.NOTRUMP: 'notrump'}
        for col, suit in enumerate(suit_order):
            lbl = QLabel(suit.symbol())
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Arial", 22, QFont.Weight.Bold))
            color = BidButton.get_suit_colors(suit)[1]
            lbl.setStyleSheet(f"color: {color};")
            lbl.setProperty("suit_name", suit_names[suit])  # Store suit name as string for refresh
            grid.addWidget(lbl, 0, col + 1)
            self.suit_headers.append(lbl)

        # Level headers and bid buttons
        for level in range(1, 8):
            lbl = QLabel(str(level))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Arial", 16, QFont.Weight.Bold))
            grid.addWidget(lbl, level, 0)

            for col, suit in enumerate(suit_order):
                bid = Bid(level=level, suit=suit)
                btn = BidButton(bid)
                btn.bid_made.connect(self._on_bid)
                grid.addWidget(btn, level, col + 1)
                self.bid_buttons.append(btn)

        buttons_layout.addLayout(grid)

        main_layout.addWidget(buttons_frame)

        # Alert checkbox and explanation
        alert_row = QHBoxLayout()

        self.alert_check = QCheckBox("Alert")
        self.alert_check.setFont(QFont("Arial", 10))
        self.alert_check.toggled.connect(self.alert_toggled.emit)
        alert_row.addWidget(self.alert_check)

        self.explanation_edit = QLineEdit()
        self.explanation_edit.setPlaceholderText("Explanation...")
        self.explanation_edit.setFont(QFont("Arial", 10))
        self.explanation_edit.setEnabled(False)
        self.alert_check.toggled.connect(self.explanation_edit.setEnabled)
        alert_row.addWidget(self.explanation_edit)

        main_layout.addLayout(alert_row)

        # Keyboard shortcut hint
        hint = QLabel("Keys: 1c-7n, p=pass, x=dbl")
        hint.setStyleSheet("color: #666; font-size: 9px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(hint)

        # Initial state
        self.set_enabled(False)

    def _on_bid(self, bid: Bid):
        """Handle a bid button click"""
        if self.alert_check.isChecked():
            bid.alert = True
            bid.explanation = self.explanation_edit.text()
            self.alert_check.setChecked(False)
            self.explanation_edit.clear()

        self.bid_selected.emit(bid)

    def set_current_bidder(self, seat: Seat):
        """Set who is currently bidding"""
        self.current_bidder = seat
        self.bidder_label.setText(f"Bidder: {seat.to_char()}")

    def set_auction(self, auction: List[Bid], dealer: Seat):
        """Update the auction display and valid bids"""
        self.auction_display.set_auction(auction, dealer)
        self._update_valid_bids(auction)

    def _update_valid_bids(self, auction: List[Bid]):
        """Update which bids are enabled based on auction state"""
        # Find highest bid
        # Start with level 0 so all level 1 bids are valid when no bids made
        self.min_bid_level = 0
        self.min_bid_suit = Suit.CLUBS
        self.can_double = False
        self.can_redouble = False

        last_real_bid = None
        last_action = None
        opponent_bid = False
        opponent_action = False

        for i, bid in enumerate(auction):
            if not bid.is_pass:
                last_action = bid
                opponent_action = (len(auction) - i) % 2 == 1
                if not bid.is_double and not bid.is_redouble:
                    last_real_bid = bid
                    opponent_bid = (len(auction) - i) % 2 == 1

        if last_real_bid:
            # Must bid higher than last real bid
            self.min_bid_level = last_real_bid.level
            self.min_bid_suit = last_real_bid.suit
            # No special handling for NT - the comparison logic handles it:
            # After 1NT (level=1, suit=NT idx=4), any 2-level bid has level > 1

        # Determine double/redouble availability
        if last_action:
            if last_action.is_double:
                # Can redouble only an opponent's double
                self.can_redouble = opponent_action
            elif last_action.is_redouble:
                # Can't double or redouble
                pass
            elif not last_action.is_pass and opponent_bid:
                # Can double opponent's contract
                self.can_double = True

        # Update button states
        self.double_btn.setEnabled(self.can_double)
        self.redouble_btn.setEnabled(self.can_redouble)

        suit_order = [Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES, Suit.NOTRUMP]

        for btn in self.bid_buttons:
            bid = btn.bid
            enabled = False

            if bid.level > self.min_bid_level:
                enabled = True
            elif bid.level == self.min_bid_level:
                bid_suit_idx = suit_order.index(bid.suit)
                min_suit_idx = suit_order.index(self.min_bid_suit)
                # Must be strictly higher suit at same level
                enabled = bid_suit_idx > min_suit_idx

            btn.setEnabled(enabled)

    def set_enabled(self, enabled: bool):
        """Enable or disable the entire bidding box"""
        self.pass_btn.setEnabled(enabled)
        self.double_btn.setEnabled(enabled and self.can_double)
        self.redouble_btn.setEnabled(enabled and self.can_redouble)

        for btn in self.bid_buttons:
            # If disabling, disable all; if enabling, respect bid validity
            if not enabled:
                btn.setEnabled(False)

        if enabled:
            self._update_valid_bids(self.auction_display.auction)

        self.alert_check.setEnabled(enabled)
        self.explanation_edit.setEnabled(enabled and self.alert_check.isChecked())

    def add_bid(self, bid: Bid):
        """Add a bid to the auction display"""
        self.auction_display.add_bid(bid)
        self._update_valid_bids(self.auction_display.auction)

    def clear(self):
        """Clear the auction"""
        self.auction_display.set_auction([], Seat.NORTH)
        self.min_bid_level = 1
        self.min_bid_suit = Suit.CLUBS
        self.can_double = False
        self.can_redouble = False
        self.set_enabled(False)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts for bidding"""
        if not self.isEnabled():
            return

        key = event.text().lower()

        # Pass
        if key == 'p' and self.pass_btn.isEnabled():
            self._on_bid(Bid.make_pass())
            return

        # Double
        if key == 'x' and self.double_btn.isEnabled():
            # Check for XX
            if event.text() == 'X' or (hasattr(self, '_last_key') and self._last_key == 'x'):
                if self.redouble_btn.isEnabled():
                    self._on_bid(Bid.make_redouble())
                    self._last_key = None
                    return
            self._last_key = 'x'
            self._on_bid(Bid.make_double())
            return

        # Contract bids (e.g., "1c", "3n", "4s")
        if len(key) >= 2 and key[0].isdigit():
            try:
                level = int(key[0])
                suit_char = key[1].upper()
                suit_map = {'C': Suit.CLUBS, 'D': Suit.DIAMONDS,
                           'H': Suit.HEARTS, 'S': Suit.SPADES, 'N': Suit.NOTRUMP}
                if suit_char in suit_map and 1 <= level <= 7:
                    bid = Bid(level=level, suit=suit_map[suit_char])
                    # Find and click the button
                    for btn in self.bid_buttons:
                        if btn.bid.level == level and btn.bid.suit == suit_map[suit_char]:
                            if btn.isEnabled():
                                self._on_bid(bid)
                            return
            except (ValueError, KeyError):
                pass

        self._last_key = key
        super().keyPressEvent(event)

    def refresh_colors(self):
        """Refresh colors after preference change (e.g., 4-color mode toggle)."""
        from .styles import get_suit_color
        # Refresh bid buttons (just updates stylesheets, safe)
        for btn in self.bid_buttons:
            btn.refresh_colors()
        # Refresh suit header labels (just updates stylesheets, safe)
        for lbl in self.suit_headers:
            suit_name = lbl.property("suit_name")
            if suit_name and suit_name != 'notrump':
                color = get_suit_color(suit_name)
                lbl.setStyleSheet(f"color: {color};")
        # Don't refresh auction display here - it recreates widgets which
        # can cause crashes if engine worker is running. Colors will update
        # on next bid.
