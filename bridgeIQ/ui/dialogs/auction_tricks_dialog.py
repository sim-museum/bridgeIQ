"""
Auction and Played Tricks Dialog - shows the complete auction record
and all played tricks.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget, QTableWidget,
    QTableWidgetItem, QHeaderView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from .dialog_style import apply_dialog_style
from ..styles import get_suit_color


class AuctionTricksDialog(QDialog):
    """Dialog showing complete auction and played tricks record."""

    SUIT_SYMBOLS = {
        'S': '\u2660', 'H': '\u2665', 'D': '\u2666', 'C': '\u2663',
        'SPADES': '\u2660', 'HEARTS': '\u2665', 'DIAMONDS': '\u2666', 'CLUBS': '\u2663',
        'NT': 'NT', 'NOTRUMP': 'NT'
    }

    # Font size for suit symbols (larger for visibility)
    SYMBOL_FONT_SIZE = 24

    def _get_suit_color(self, suit_key: str) -> str:
        """Get suit color based on current preference (4-color or legacy)."""
        suit_map = {
            'S': 'spades', 'H': 'hearts', 'D': 'diamonds', 'C': 'clubs',
            'SPADES': 'spades', 'HEARTS': 'hearts', 'DIAMONDS': 'diamonds', 'CLUBS': 'clubs',
            'NT': 'spades', 'NOTRUMP': 'spades'  # NT uses black
        }
        suit_name = suit_map.get(suit_key.upper(), 'spades')
        return get_suit_color(suit_name)

    def __init__(self, parent=None, board=None, tricks=None,
                 viewer_seat=None, dummy_seat=None,
                 visible_seats=None):
        super().__init__(parent)
        self.setWindowTitle("Record (current)")
        self.setMinimumWidth(650)
        self.setMinimumHeight(600)
        self.resize(700, 650)
        apply_dialog_style(self)

        # Detach from the parent window: Qt.Dialog is a "tool" window
        # that some window managers (Mutter / KWin) keep glued to the
        # parent's screen, so it can't be dragged onto a second monitor.
        # Promote to a real top-level Qt.Window with min/max/close
        # buttons; the caller is expected to use show() instead of
        # exec() so the user can interact with the main window in
        # parallel. Qt.WA_DeleteOnClose makes sure each open/close
        # cycle still cleans up properly.
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowSystemMenuHint |
            Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.board = board
        self.tricks = tricks or []
        # Seats whose hole cards the viewer is allowed to see — used
        # to filter the per-hand panel down to cards the viewer can't
        # already see. `visible_seats` is the preferred input (a set
        # of Seat) so the caller can include declarer's hand when the
        # viewer is dummy. `viewer_seat` + `dummy_seat` are kept as
        # legacy fallback inputs; if both are None we show every
        # seat.
        self.viewer_seat = viewer_seat
        self.dummy_seat = dummy_seat
        if visible_seats is not None:
            self.visible_seats = set(visible_seats)
        else:
            self.visible_seats = {
                s for s in (viewer_seat, dummy_seat) if s is not None
            }

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Auction table
        auction_frame = QFrame()
        auction_frame.setStyleSheet("""
            QFrame {
                background-color: #e8e8f0;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
            }
        """)
        auction_layout = QVBoxLayout(auction_frame)

        # Header row
        header_layout = QHBoxLayout()
        fs = self.SYMBOL_FONT_SIZE
        for seat in ['N', 'E', 'S', 'W']:
            lbl = QLabel(f'<b style="font-size:{fs}px">{seat}</b>')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setMinimumWidth(70)
            header_layout.addWidget(lbl)
        auction_layout.addLayout(header_layout)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #808080;")
        auction_layout.addWidget(sep)

        # Auction bids
        self.auction_area = QWidget()
        self.auction_grid = QGridLayout(self.auction_area)
        self.auction_grid.setSpacing(4)
        self._populate_auction()
        auction_layout.addWidget(self.auction_area)

        layout.addWidget(auction_frame)

        # Tricks section (if any tricks played)
        if self.tricks:
            tricks_frame = QFrame()
            tricks_frame.setStyleSheet("""
                QFrame {
                    background-color: #f0f0e8;
                    border: 1px solid #a0a0a0;
                    border-radius: 4px;
                }
            """)
            tricks_layout = QVBoxLayout(tricks_frame)

            # Tricks header
            tricks_header = QHBoxLayout()
            tricks_header.addWidget(QLabel(""))  # Leader column
            for seat in ['N', 'E', 'S', 'W']:
                lbl = QLabel(f'<b style="font-size:{self.SYMBOL_FONT_SIZE}px">{seat}</b>')
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setMinimumWidth(60)
                tricks_header.addWidget(lbl)
            tricks_layout.addLayout(tricks_header)

            # Tricks grid
            self.tricks_area = QWidget()
            self.tricks_grid = QGridLayout(self.tricks_area)
            self.tricks_grid.setSpacing(4)
            self._populate_tricks()
            tricks_layout.addWidget(self.tricks_area)

            layout.addWidget(tricks_frame)

        # Outstanding cards by suit — useful for spotting "I hold all
        # the remaining diamonds" situations at a glance.
        if self.board is not None:
            remaining_frame = self._build_remaining_frame()
            if remaining_frame is not None:
                layout.addWidget(remaining_frame)

        layout.addStretch()

    def _build_remaining_frame(self):
        """Build the per-unseen-hand breakdown: one row per seat the
        viewer can't see, listing each suit's exact ranks where forced,
        a count, or a (min-max) range. Returns None if there's nothing
        useful to show (no board, or every seat is visible)."""
        try:
            remaining = self.board.remaining_cards_by_suit()
        except (AttributeError, Exception):
            return None
        if not remaining:
            return None

        unseen_seats = self._unseen_seats()
        if not unseen_seats:
            return None

        # Filter the suit-by-suit remaining lists to drop cards already
        # visible in any of the visible hands.
        visible_cards = set()
        for s in self.visible_seats:
            try:
                hand = self.board.hands.get(s) if self.board else None
            except Exception:
                hand = None
            if hand is None:
                continue
            for c in getattr(hand, 'cards', []) or []:
                visible_cards.add((c.suit, c.rank))
        if visible_cards:
            remaining = {
                suit: [r for r in ranks if (suit, r) not in visible_cards]
                for suit, ranks in remaining.items()
            }

        from backend.models import Suit
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #f0e8e8; border: 1px solid #a0a0a0;"
            " border-radius: 4px; }"
        )
        v = QVBoxLayout(frame)
        suit_order = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]
        suit_names_full = {
            Suit.SPADES: 'SPADES', Suit.HEARTS: 'HEARTS',
            Suit.DIAMONDS: 'DIAMONDS', Suit.CLUBS: 'CLUBS',
        }
        self._add_per_hand_rows(
            v, remaining, unseen_seats, suit_order, suit_names_full
        )
        return frame

    def _unseen_seats(self):
        """Return the seats the viewer can't see (everything except
        the seats in `visible_seats`), preserving N-E-S-W order.
        Returns [] if we don't have a board or every seat is visible."""
        if not self.board or not self.board.hands:
            return []
        from backend.models import Seat
        if not self.visible_seats:
            return []
        return [
            s for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST)
            if s not in self.visible_seats and s in self.board.hands
        ]

    def _played_and_voids(self, seats):
        """Walk the trick history and return (played_count, voids) where
        played_count[seat] = #cards seat has played, and voids[seat] is
        the set of Suits seat is known to be void in (showed out).
        """
        played = {s: 0 for s in seats}
        voids = {s: set() for s in seats}
        from backend.models import Seat

        def _walk(trick):
            if not trick or not trick.cards:
                return
            lead_suit = trick.cards[0].suit
            for i, card in enumerate(trick.cards):
                seat = Seat((trick.leader.value + i) % 4)
                if seat in played:
                    played[seat] += 1
                    if card.suit != lead_suit:
                        voids[seat].add(lead_suit)

        for trick in self.board.tricks:
            _walk(trick)
        if self.board.current_trick:
            _walk(self.board.current_trick)
        return played, voids

    def _add_per_hand_rows(self, v, remaining, unseen_seats,
                           suit_order, suit_names_full):
        """Append per-unseen-hand suit-distribution rows to the layout v.

        For each unseen seat we solve a small CSP: each suit's count is
        in [0, suit_total], constrained by (a) shown-out → 0, (b) suit
        totals summing to suit_total across unseen hands, (c) suit
        counts within a hand summing to that hand's remaining cards.
        Iterates a few times to fixed-point — enough for 2-3 hands x 4
        suits.
        """
        played, voids = self._played_and_voids(unseen_seats)
        rem_total = {s: 13 - played[s] for s in unseen_seats}
        suit_total = {suit: len(remaining.get(suit, [])) for suit in suit_order}

        # bounds[seat][suit] = [lo, hi]
        bounds = {seat: {} for seat in unseen_seats}
        for seat in unseen_seats:
            for suit in suit_order:
                if suit in voids[seat]:
                    bounds[seat][suit] = [0, 0]
                else:
                    bounds[seat][suit] = [0, min(suit_total[suit], rem_total[seat])]

        for _ in range(8):
            changed = False
            # Tighten lowers from suit totals
            for suit in suit_order:
                for seat in unseen_seats:
                    others_max = sum(
                        bounds[h][suit][1] for h in unseen_seats if h != seat
                    )
                    new_lo = max(bounds[seat][suit][0], suit_total[suit] - others_max)
                    if new_lo > bounds[seat][suit][0]:
                        bounds[seat][suit][0] = new_lo
                        changed = True
            # Tighten uppers from hand-size totals
            for seat in unseen_seats:
                sum_lo = sum(bounds[seat][s][0] for s in suit_order)
                for suit in suit_order:
                    cap = rem_total[seat] - (sum_lo - bounds[seat][suit][0])
                    new_hi = min(bounds[seat][suit][1], max(0, cap))
                    if new_hi < bounds[seat][suit][1]:
                        bounds[seat][suit][1] = new_hi
                        changed = True
            # Tighten lowers from hand-size totals (forced cards)
            for seat in unseen_seats:
                sum_hi = sum(bounds[seat][s][1] for s in suit_order)
                for suit in suit_order:
                    floor = rem_total[seat] - (sum_hi - bounds[seat][suit][1])
                    if floor > bounds[seat][suit][0]:
                        bounds[seat][suit][0] = floor
                        changed = True
            if not changed:
                break

        from PyQt6.QtWidgets import QLabel
        from backend.models import Seat
        fs = self.SYMBOL_FONT_SIZE

        seat_char = {Seat.NORTH: 'N', Seat.EAST: 'E',
                     Seat.SOUTH: 'S', Seat.WEST: 'W'}
        for seat in unseen_seats:
            parts = [
                f'<b style="font-size:{fs}px">{seat_char[seat]}</b>'
                f'<span style="font-size:{fs}px;color:#666">'
                f' ({rem_total[seat]} cards):</span>  '
            ]
            for suit in suit_order:
                lo, hi = bounds[seat][suit]
                symbol = self.SUIT_SYMBOLS.get(suit_names_full[suit], '?')
                color = self._get_suit_color(suit_names_full[suit])
                # If every other unseen hand is void in this suit, the
                # remaining ranks are all in this seat — list them.
                others_all_void = all(
                    bounds[h][suit][1] == 0
                    for h in unseen_seats if h != seat
                )
                ranks = remaining.get(suit, [])
                if lo == hi == 0:
                    body = '—'
                elif others_all_void and ranks:
                    body = ' '.join(r.to_char() for r in ranks) + f' ({len(ranks)})'
                elif lo == hi:
                    body = f'({lo})'
                else:
                    body = f'({lo}-{hi})'
                parts.append(
                    f'<span style="color:{color};font-size:{fs}px">{symbol}</span>'
                    f'<span style="font-size:{fs}px"> {body}</span>'
                    f'<span style="font-size:{fs}px">   </span>'
                )
            v.addWidget(QLabel(''.join(parts)))

    def _populate_auction(self):
        """Fill in the auction bids."""
        if not self.board or not self.board.auction:
            return

        # Determine dealer position
        dealer = self.board.dealer
        dealer_offset = dealer.value  # N=0, E=1, S=2, W=3

        # Fill in the auction
        row = 0
        col = dealer_offset

        for bid in self.board.auction:
            bid_text = self._format_bid(bid)
            lbl = QLabel(bid_text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.auction_grid.addWidget(lbl, row, col)

            col += 1
            if col >= 4:
                col = 0
                row += 1

        # Add dashes for positions before dealer in first row
        fs = self.SYMBOL_FONT_SIZE
        for i in range(dealer_offset):
            lbl = QLabel(f'<span style="font-size:{fs}px">-</span>')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.auction_grid.addWidget(lbl, 0, i)

        # Add "?" for current bidder if auction not complete
        if not self._is_auction_complete():
            lbl = QLabel(f'<span style="font-size:{fs}px">?</span>')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.auction_grid.addWidget(lbl, row, col)

    def _format_bid(self, bid):
        """Format a bid for display with colored suit symbol."""
        fs = self.SYMBOL_FONT_SIZE
        if bid.is_pass:
            return f'<span style="font-size:{fs}px">-</span>'
        elif bid.is_double:
            return f'<span style="font-size:{fs}px">X</span>'
        elif bid.is_redouble:
            return f'<span style="font-size:{fs}px">XX</span>'
        else:
            level = bid.level
            suit = bid.suit
            if suit:
                suit_name = suit.name.upper()
                symbol = self.SUIT_SYMBOLS.get(suit_name, suit_name[0])
                color = self._get_suit_color(suit_name)
                return f'<span style="font-size:{fs}px">{level}</span><span style="color:{color};font-size:{fs}px">{symbol}</span>'
            else:
                return f'<span style="font-size:{fs}px">{level}NT</span>'

    def _is_auction_complete(self):
        """Check if the auction is complete (3 consecutive passes after a bid)."""
        if not self.board or not self.board.auction:
            return False

        auction = self.board.auction
        if len(auction) < 4:
            return False

        # Check for 3 consecutive passes at end
        pass_count = 0
        has_bid = False
        for bid in auction:
            if not bid.is_pass:
                has_bid = True
                pass_count = 0
            else:
                pass_count += 1

        return has_bid and pass_count >= 3

    def _populate_tricks(self):
        """Fill in the played tricks."""
        if not self.tricks:
            return

        fs = self.SYMBOL_FONT_SIZE
        for trick_num, trick in enumerate(self.tricks):
            # Leader indicator
            leader = trick.get('leader', '')
            leader_lbl = QLabel(f'<b style="font-size:{fs}px">{leader}</b>')
            self.tricks_grid.addWidget(leader_lbl, trick_num, 0)

            # Cards played (in order N, E, S, W)
            cards = trick.get('cards', {})
            for col, seat in enumerate(['N', 'E', 'S', 'W']):
                card = cards.get(seat, '')
                if card:
                    card_text = self._format_card(card)
                    # Highlight winning card
                    winner = trick.get('winner', '')
                    if seat == winner:
                        card_text = f"<b>{card_text}</b>"
                else:
                    card_text = f'<span style="font-size:{fs}px">?</span>'
                lbl = QLabel(card_text)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tricks_grid.addWidget(lbl, trick_num, col + 1)

    def _format_card(self, card):
        """Format a card for display."""
        fs = self.SYMBOL_FONT_SIZE
        if hasattr(card, 'suit') and hasattr(card, 'rank'):
            suit = card.suit
            rank = card.rank

            suit_name = suit.name.upper()
            symbol = self.SUIT_SYMBOLS.get(suit_name, suit_name[0])
            color = self._get_suit_color(suit_name)

            rank_char = self._rank_to_char(rank)
            return f'<span style="color:{color};font-size:{fs}px">{symbol}</span><span style="font-size:{fs}px">{rank_char}</span>'
        elif isinstance(card, str):
            # String format like "SA" or "H10"
            if len(card) >= 2:
                suit = card[0].upper()
                rank = card[1:]
                symbol = self.SUIT_SYMBOLS.get(suit, suit)
                color = self._get_suit_color(suit)
                return f'<span style="color:{color};font-size:{fs}px">{symbol}</span><span style="font-size:{fs}px">{rank}</span>'
        return f'<span style="font-size:{fs}px">{str(card)}</span>'

    def _rank_to_char(self, rank):
        """Convert rank to display character.

        The model's Rank IntEnum is ace-high-low (ACE=0, KING=1, …,
        TWO=12) so its value is NOT the printed rank. Earlier this
        method assumed the opposite mapping (12=A) which made every
        card render with the wrong rank: an Ace came out as "2", a
        Five came out as "J", and the Cards-still-out list ended up
        inconsistent with the played-tricks display. Just delegate
        to Rank.to_char() which already uses the canonical
        'AKQJT98765432' table.
        """
        if hasattr(rank, 'to_char'):
            try:
                return rank.to_char()
            except Exception:
                pass
        # Fall-through for plain ints / strings — try the model's
        # Rank class so the same canonical table is used.
        try:
            from backend.models import Rank
            return Rank(int(rank)).to_char()
        except Exception:
            return str(rank)
