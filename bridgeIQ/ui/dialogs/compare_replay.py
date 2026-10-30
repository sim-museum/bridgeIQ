"""Side-by-side replay dialog comparing two BenBoardRuns of the same deal.

The classic single-side replay (`ReplayViewDialog`) drives one mini table
through bidding/play. CompareReplayDialog hosts two of those tables and a
single navigation bar; each step advances both panels in lockstep so the
user can compare an open-room human play against a Q-Plus closed-room.
"""

from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGridLayout, QWidget, QSplitter, QApplication, QCheckBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .dialog_style import apply_dialog_style
from .replay_view import MiniHandWidget
from backend.models import (
    BenBoardRun, Seat, Suit, Card, Trick
)


class _ReplayPanel(QWidget):
    """One side of the comparison view — header, four hand widgets, trick
    area, and tricks-won line. Driven externally by `set_position`.
    """

    # Trick area font: keep in sync with the hand-row font so the cards
    # in the centre read at the same weight as those in N/E/S/W.
    TRICK_FS = 22

    def __init__(self, run: BenBoardRun, title: str, parent=None):
        super().__init__(parent)
        self.fallback_hands = {}
        self.run = run

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Title strip — distinguishes "open" vs "closed".
        title_lbl = QLabel(f'<span style="font-size:18px"><b>{title}</b></span>')
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        # Header (contract / result)
        header = QFrame()
        header.setStyleSheet(
            "QFrame { background-color: #e0e8f0; border: 1px solid #a0a0a0;"
            " border-radius: 4px; padding: 6px; }"
        )
        h_layout = QHBoxLayout(header)
        from ..styles import get_suit_color
        if run.contract:
            c = run.contract
            sym = ['♠', '♥', '♦', '♣', 'NT'][c.suit.value]
            names = ['spades', 'hearts', 'diamonds', 'clubs', 'spades']
            if c.suit.value < 4:
                color = get_suit_color(names[c.suit.value])
                contract_html = f'{c.level}<span style="color:{color}">{sym}</span>'
            else:
                contract_html = f"{c.level}NT"
            if c.redoubled:
                contract_html += "XX"
            elif c.doubled:
                contract_html += "X"
            contract_html += f" by {c.declarer.to_char()}"
        else:
            contract_html = "Passed Out"
        contract_lbl = QLabel(f'<span style="font-size:16px"><b>{contract_html}</b></span>')
        h_layout.addWidget(contract_lbl)
        h_layout.addStretch()

        if run.contract:
            target = run.contract.target_tricks()
            diff = run.declarer_tricks - target
            res = "Made" if diff == 0 else (f"+{diff}" if diff > 0 else str(diff))
            score_str = f"{run.ns_score:+d}"
        else:
            res, score_str = "—", "0"
        result_lbl = QLabel(f'<span style="font-size:14px">{res} ({score_str})</span>')
        h_layout.addWidget(result_lbl)
        layout.addWidget(header)

        # Mini table — 4 hands in N/E/S/W with center trick area.
        table_frame = QFrame()
        table_frame.setStyleSheet(
            "QFrame { background-color: #f8f8f8; border: 1px solid #a0a0a0;"
            " border-radius: 4px; }"
        )
        grid = QGridLayout(table_frame)
        grid.setSpacing(8)

        self.north = MiniHandWidget(Seat.NORTH)
        grid.addWidget(self.north, 0, 1)
        self.west = MiniHandWidget(Seat.WEST)
        grid.addWidget(self.west, 1, 0)
        self.east = MiniHandWidget(Seat.EAST)
        grid.addWidget(self.east, 1, 2)
        self.south = MiniHandWidget(Seat.SOUTH)
        grid.addWidget(self.south, 2, 1)

        center = QFrame()
        # min-height bumped from 200 → 260 so all four cards of a
        # trick (4 lines @ ~30 px line-height + the "Trick N" header
        # + frame margins) fit without clipping. The Qt grid row
        # only grows to the tallest widget, so the trick frame
        # itself has to declare enough space for 4 rows or the
        # bottom card vanishes off the bottom edge.
        center.setStyleSheet(
            "QFrame { background-color: #e8ece8; border: 1px solid #c0c0c0;"
            " border-radius: 4px; min-width: 200px; min-height: 260px; }"
        )
        c_layout = QVBoxLayout(center)
        # 4 px top margin pulls "Trick N" hard against the top of
        # the grey frame so the previous wasted strip above it is
        # gone. AlignTop on the layout forces every widget to stack
        # from the top down — without it the QVBoxLayout default
        # vertically centres a short stack of widgets in a tall
        # frame, which was what put "Trick N" mid-frame and pushed
        # the 4th card off the bottom.
        c_layout.setContentsMargins(8, 4, 8, 8)
        c_layout.setSpacing(2)
        c_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        fs = self.TRICK_FS
        self.trick_label = QLabel(
            f'<span style="font-size:{fs}px"><b>Trick 1</b></span>'
        )
        self.trick_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        c_layout.addWidget(self.trick_label)
        self.trick_cards_label = QLabel("")
        self.trick_cards_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.trick_cards_label.setTextFormat(Qt.TextFormat.RichText)
        # WordWrap on so a long card label can't push the rest off
        # the bottom of the frame.
        self.trick_cards_label.setWordWrap(True)
        c_layout.addWidget(self.trick_cards_label)
        grid.addWidget(center, 1, 1)

        layout.addWidget(table_frame, stretch=1)

        # Tricks-won line.
        trick_line = QHBoxLayout()
        self.declarer_lbl = QLabel('<span style="font-size:16px">Declarer: 0</span>')
        trick_line.addWidget(self.declarer_lbl)
        trick_line.addStretch()
        self.defense_lbl = QLabel('<span style="font-size:16px">Defense: 0</span>')
        trick_line.addWidget(self.defense_lbl)
        layout.addLayout(trick_line)

        # Double dummy (bcalc-style): what declarer can still take from this
        # position, and a trick count on every card the player to move holds.
        self.show_dd = True
        self.dd_lbl = QLabel("")
        self.dd_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.dd_lbl)

    # ------------------------------------------------------------------
    # Position model
    #
    # Position 0 is "trick 1, no card yet". Each step adds one card to
    # the current trick; once 4 cards land we move on to trick N+1.
    # The auction is shown in its own dialog (see BiddingLogDialog),
    # so we don't intermix bids and cards in the navigation any more.
    # The panel freezes at its last card if the other side ran longer.
    # ------------------------------------------------------------------

    def total_steps(self) -> int:
        return sum(len(t.cards) for t in self.run.tricks)

    def set_position(self, step: int):
        """Render the panel at card-step `step` (0..total_steps())."""
        step = max(0, min(step, self.total_steps()))
        self._render_play(card_step=step)

    def _render_play(self, card_step: int):
        tricks = self.run.tricks
        played_cards = {s: [] for s in Seat}
        # Winning cards by seat — populated for every trick that's
        # already complete at this step. Used by MiniHandWidget to
        # paint the trick-winning card in gold rather than the
        # standard "dimmed played" treatment.
        winning_cards = {s: [] for s in Seat}

        cards_left = card_step
        current_trick_idx = 0
        cards_in_current = 0
        for ti, t in enumerate(tricks):
            n = len(t.cards)
            if cards_left >= n:
                # All four cards of this trick already played
                for ci, card in enumerate(t.cards):
                    played_cards[Seat((t.leader.value + ci) % 4)].append(card)
                # The trick is finished, so we know which card won.
                if t.winner is not None:
                    win_idx = (t.winner.value - t.leader.value) % 4
                    if 0 <= win_idx < len(t.cards):
                        winning_cards[t.winner].append(t.cards[win_idx])
                cards_left -= n
                if cards_left == 0:
                    current_trick_idx = ti
                    cards_in_current = n
                    break
                current_trick_idx = ti + 1
            else:
                for ci in range(cards_left):
                    card = t.cards[ci]
                    played_cards[Seat((t.leader.value + ci) % 4)].append(card)
                current_trick_idx = ti
                cards_in_current = cards_left
                cards_left = 0
                break

        dd = None
        if self.show_dd and self.run.contract:
            from backend import dd_cards
            hands = {s: (self.run.original_hands.get(s) if self.run.original_hands.get(s)
                         and self.run.original_hands[s].cards else self.fallback_hands.get(s))
                     for s in Seat}
            dd = dd_cards.card_tricks(hands, tricks, card_step, self.run.contract.suit)
            self._set_dd_line(dd)
        else:
            self.dd_lbl.setText("")
        self._update_hands(played_cards, winning_cards, dd)

        fs = self.TRICK_FS
        # Trick area
        if current_trick_idx < len(tricks):
            trick = tricks[current_trick_idx]
            self.trick_label.setText(
                f'<span style="font-size:{fs}px"><b>Trick {current_trick_idx + 1}</b></span>'
            )
            shown_cards = []
            for ci in range(cards_in_current):
                card = trick.cards[ci]
                seat = Seat((trick.leader.value + ci) % 4)
                shown_cards.append(
                    f'<span style="font-size:{fs}px">{seat.to_char()}: {self._fmt_card(card)}</span>'
                )
            self.trick_cards_label.setText(
                "<br>".join(shown_cards) if shown_cards else
                f'<span style="font-size:{fs}px">(lead)</span>'
            )
        else:
            self.trick_label.setText(
                f'<span style="font-size:{fs}px"><b>Complete</b></span>'
            )
            self.trick_cards_label.setText("")

        # Tricks-won
        decl_won = 0
        def_won = 0
        if self.run.contract:
            decl_ns = self.run.contract.declarer.is_ns()
            for ti in range(min(current_trick_idx, len(tricks))):
                t = tricks[ti]
                if t.winner is not None:
                    if t.winner.is_ns() == decl_ns:
                        decl_won += 1
                    else:
                        def_won += 1
        # Include current trick if all four cards landed and it's done.
        if (current_trick_idx < len(tricks)
                and cards_in_current == 4
                and tricks[current_trick_idx].winner is not None):
            if self.run.contract:
                decl_ns = self.run.contract.declarer.is_ns()
                if tricks[current_trick_idx].winner.is_ns() == decl_ns:
                    decl_won += 1
                else:
                    def_won += 1
        self.declarer_lbl.setText(
            f'<span style="font-size:16px">Declarer: {decl_won}</span>'
        )
        self.defense_lbl.setText(
            f'<span style="font-size:16px">Defense: {def_won}</span>'
        )

    def _set_dd_line(self, dd):
        """'Double dummy: declarer can still take 9 (needs 10)': green when the
        contract still makes with best play from here, red when it is gone."""
        if not dd:
            self.dd_lbl.setText("")
            return
        to_play, tr = dd
        c = self.run.contract
        best = max(tr.values())
        decl = best if to_play.is_ns() == c.declarer.is_ns() else 13 - best
        need = c.target_tricks()
        col = "#0b7a0b" if decl >= need else "#c62828"
        self.dd_lbl.setText(
            f'<span style="font-size:15px">Double dummy from here: declarer can take '
            f'<b style="color:{col}">{decl}</b> (needs {need}). Numbers on '
            f'{to_play.to_char()}\'s cards: tricks for that side if played '
            f'(<span style="color:#0b7a0b">best</span>, '
            f'<span style="color:#c62828">gives tricks away</span>).</span>')

    def _update_hands(self, played, winning=None, dd=None):
        winning = winning or {}
        for seat, w in [(Seat.NORTH, self.north), (Seat.EAST, self.east),
                        (Seat.SOUTH, self.south), (Seat.WEST, self.west)]:
            hand = self.run.original_hands.get(seat)
            # The human's own record can hold only the hands visible at the
            # table (the other pair's are empty); it is the same deal in both
            # rooms, so show them from the other room's record.
            if (not hand or not hand.cards) and self.fallback_hands.get(seat):
                hand = self.fallback_hands[seat]
            if hand:
                w.set_hand(
                    hand, played[seat], winning.get(seat, []),
                    dd[1] if (dd and dd[0] == seat) else None)

    def _fmt_card(self, card: Card) -> str:
        from ..styles import get_suit_color
        sym = {Suit.SPADES: '♠', Suit.HEARTS: '♥',
               Suit.DIAMONDS: '♦', Suit.CLUBS: '♣'}[card.suit]
        names = {Suit.SPADES: 'spades', Suit.HEARTS: 'hearts',
                 Suit.DIAMONDS: 'diamonds', Suit.CLUBS: 'clubs'}
        color = get_suit_color(names[card.suit])
        return f'<span style="color:{color}">{sym}</span>{card.rank.to_char()}'


class _BiddingLogPanel(QWidget):
    """One side's auction rendered as a 4-column N/E/S/W grid."""

    HEADER_FS = 18
    CELL_FS = 18

    def __init__(self, run: BenBoardRun, title: str,
                 dealer_idx: int | None = None, parent=None):
        """If `dealer_idx` is supplied, use it instead of deriving from
        `run.board_number` — useful when one side is loaded from a
        source that doesn't carry a board number (Q-Plus QSS files
        leave board_number=0, which would otherwise mis-place the
        first bid at the wrong column).
        """
        super().__init__(parent)
        from ..styles import get_suit_color
        self._dealer_idx_override = dealer_idx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        title_lbl = QLabel(f'<span style="font-size:18px"><b>{title}</b></span>')
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        meta_bits = []
        if run.contract:
            decl = run.contract.declarer.to_char() if run.contract.declarer else "?"
            meta_bits.append(f"Final contract: {run.contract.to_str()} by {decl}")
        if run.contract:
            target = run.contract.target_tricks()
            diff = run.declarer_tricks - target
            res = "=" if diff == 0 else (f"+{diff}" if diff > 0 else str(diff))
            meta_bits.append(f"Result: {res}  (NS {run.ns_score:+d})")
        if meta_bits:
            meta_lbl = QLabel('<span style="font-size:14px">' + ' • '.join(meta_bits) + '</span>')
            meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(meta_lbl)

        # Dealer / vulnerability / per-pair systems line — answers
        # the "why didn't both rooms bid the same?" question by
        # making the systems each side was actually playing visible
        # at a glance. Pulled from board_number for dealer/vul (the
        # canonical board → dealer/vul mapping) and from the run's
        # own ns_bidding_system / ew_bidding_system fields, which
        # are persisted per-deal so an old replay shows whatever
        # systems were configured when the deal was played.
        try:
            from backend.models import BoardState, Vulnerability
            dealer, vuln = BoardState._board_dealer_vuln(run.board_number)
            dealer_char = dealer.to_char()
            # `Vulnerability` is a string-valued Enum ("None" / "N-S"
            # / "E-W" / "Both"), so the previous int-keyed map always
            # missed and the header always reported "None" regardless
            # of the board's true vulnerability. Look up by enum
            # identity instead and shorten the display tokens.
            vul_str = {
                Vulnerability.NONE: 'None',
                Vulnerability.NS:   'NS',
                Vulnerability.EW:   'EW',
                Vulnerability.BOTH: 'Both',
            }.get(vuln, 'None')
        except Exception:
            dealer_char, vul_str = '?', '?'
        ns_sys = (getattr(run, 'ns_bidding_system', '') or '—')
        ew_sys = (getattr(run, 'ew_bidding_system', '') or '—')
        sys_lbl = QLabel(
            f'<span style="font-size:13px; color:#444">'
            f'Dealer: <b>{dealer_char}</b> • Vul: <b>{vul_str}</b>'
            f' • NS: <b>{ns_sys}</b> • EW: <b>{ew_sys}</b>'
            f'</span>'
        )
        sys_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sys_lbl)

        # The grid: header row N/E/S/W + one row per round of bidding.
        grid_frame = QFrame()
        grid_frame.setStyleSheet(
            "QFrame { background-color: #f8f8f8; border: 1px solid #a0a0a0;"
            " border-radius: 4px; padding: 8px; }"
        )
        grid = QGridLayout(grid_frame)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(2)
        for col, seat_char in enumerate(['N', 'E', 'S', 'W']):
            h = QLabel(
                f'<span style="font-size:{self.HEADER_FS}px"><b>{seat_char}</b></span>'
            )
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(
                "QLabel { background-color: #d0d0d0; border: 1px solid #888;"
                " padding: 4px 12px; }"
            )
            grid.addWidget(h, 0, col)

        # Pad cells before the dealer's first bid with em dashes so each
        # column lines up with its seat.  Prefer the explicit override
        # passed by the parent dialog (which already worked out the
        # correct dealer from whichever run carries a real board
        # number); only fall back to deriving from `run.board_number`
        # when no override is supplied.
        from backend.models import Seat as _Seat
        if self._dealer_idx_override is not None:
            dealer_idx = self._dealer_idx_override
        else:
            try:
                from backend.models import BoardState
                dealer, _ = BoardState._board_dealer_vuln(run.board_number)
                dealer_idx = dealer.value
            except Exception:
                dealer_idx = 0

        suit_symbols = {0: '♠', 1: '♥', 2: '♦', 3: '♣'}
        suit_names = {0: 'spades', 1: 'hearts', 2: 'diamonds', 3: 'clubs'}

        col = 0
        row = 1
        # Pre-fill cells before dealer's first bid with em dashes.
        for _ in range(dealer_idx):
            cell = QLabel(
                f'<span style="font-size:{self.CELL_FS}px;color:#999">—</span>'
            )
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(cell, row, col)
            col += 1

        for bid in run.auction:
            if bid.is_pass:
                text = f'<span style="font-size:{self.CELL_FS}px;color:#666">Pass</span>'
            elif bid.is_double:
                text = f'<span style="font-size:{self.CELL_FS}px;color:blue"><b>X</b></span>'
            elif bid.is_redouble:
                text = f'<span style="font-size:{self.CELL_FS}px;color:#00008B"><b>XX</b></span>'
            else:
                if bid.suit is not None and bid.suit.value < 4:
                    color = get_suit_color(suit_names[bid.suit.value])
                    sym = suit_symbols[bid.suit.value]
                    text = (
                        f'<span style="font-size:{self.CELL_FS}px"><b>{bid.level}</b>'
                        f'<span style="color:{color}">{sym}</span></span>'
                    )
                else:
                    text = f'<span style="font-size:{self.CELL_FS}px"><b>{bid.level}NT</b></span>'
            cell = QLabel(text)
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(cell, row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1

        layout.addWidget(grid_frame)
        layout.addStretch(1)


def _replay_auction_through_native_bidder(run: BenBoardRun):
    """Walk every call in a run's auction through the native bidder
    using the system that was recorded for that side. Returns a list
    of dicts, one per call:

        {
          'round': int (1-based),
          'seat':  Seat,
          'side':  'NS' | 'EW',
          'system_name': str (e.g. 'Precision90M'),
          'actual': Bid,
          'native': Bid,
          'explanation': str,
          'matches': bool,
        }

    Used by the Compare dialog's "Engine compare" report to surface
    each divergence between what was actually played and what our
    current rule-based bidder would say given the same system.
    """
    out = []
    try:
        from backend.models import BoardState, Seat
        from backend.native_bidder import NativeBidder
        from backend.bidding_systems import get_system
    except Exception:
        return out
    try:
        dealer, vuln = BoardState._board_dealer_vuln(
            run.board_number or 1)
    except Exception:
        return out

    # Resolve NS / EW system names — fall back to "SAYC" so the
    # bidder always has something to anchor on.
    ns_name = (getattr(run, 'ns_bidding_system', '') or 'SAYC') or 'SAYC'
    ew_name = (getattr(run, 'ew_bidding_system', '') or 'SAYC') or 'SAYC'
    try:
        ns_sys = get_system(ns_name)
        ew_sys = get_system(ew_name)
    except Exception:
        return out

    ns_bidder = NativeBidder(ns_sys)
    ew_bidder = NativeBidder(ew_sys)

    for i, actual in enumerate(run.auction):
        seat = Seat((dealer.value + i) % 4)
        side = 'NS' if seat in (Seat.NORTH, Seat.SOUTH) else 'EW'
        sys_name = ns_name if side == 'NS' else ew_name
        bidder = ns_bidder if side == 'NS' else ew_bidder

        synthetic = BoardState(
            board_number=run.board_number,
            dealer=dealer,
            vulnerability=vuln,
            hands=dict(run.original_hands),
            auction=list(run.auction[:i]),  # auction BEFORE this call
            contract=None,
            tricks=[],
        )
        try:
            resp = bidder.get_bid(synthetic, seat)
            native = resp.action
            explanation = (
                getattr(native, 'explanation', '') or
                getattr(resp, 'who', '') or ''
            )
        except Exception as ex:
            native = None
            explanation = f"engine error: {ex!r}"

        out.append({
            'round': i // 4 + 1,
            'seat': seat,
            'side': side,
            'system_name': sys_name,
            'actual': actual,
            'native': native,
            'explanation': explanation,
            'matches': (
                native is not None and _bids_equivalent(actual, native)
            ),
        })
    return out


def _bids_equivalent(a, b) -> bool:
    """Compare two Bid objects ignoring metadata like 'explanation'.
    Two Bids are equivalent when their kind (pass / double / redouble
    / regular) matches AND the level / strain agree."""
    if a is None or b is None:
        return False
    if a.is_pass != b.is_pass:
        return False
    if a.is_double != b.is_double:
        return False
    if a.is_redouble != b.is_redouble:
        return False
    if a.is_pass or a.is_double or a.is_redouble:
        return True
    return (a.level == b.level and a.suit == b.suit)


class BiddingLogDialog(QDialog):
    """Independent top-level window with the two auctions side-by-side.

    Spawned by CompareReplayDialog when the user clicks "Bidding logs".
    Lives in its own window so it can be dragged to a second monitor;
    the main Compare window keeps the play replay.
    """

    def __init__(self, left: BenBoardRun, right: BenBoardRun,
                 left_label: str, right_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Bidding logs: Board {left.board_number}")
        apply_dialog_style(self)
        from .dialog_style import make_detachable
        make_detachable(self)
        self.resize(900, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Both runs describe the same deal, so dealer must agree.  Use
        # whichever run carries a real (non-zero) board number to derive
        # it — Q-Plus QSS imports leave board_number=0 on the closed
        # side, which would otherwise mis-place the first bid one
        # column to the right.
        dealer_idx = self._derive_shared_dealer_idx(left, right)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(_BiddingLogPanel(left, left_label, dealer_idx=dealer_idx))
        splitter.addWidget(_BiddingLogPanel(right, right_label, dealer_idx=dealer_idx))
        splitter.setSizes([1, 1])
        layout.addWidget(splitter, stretch=1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    @staticmethod
    def _derive_shared_dealer_idx(left: BenBoardRun, right: BenBoardRun) -> int:
        """Pick the seat index of the dealer using whichever run carries
        a real board number.  Returns 0 (North) if neither does."""
        from backend.models import BoardState
        for run in (left, right):
            board_no = getattr(run, 'board_number', 0) or 0
            if board_no > 0:
                try:
                    dealer, _ = BoardState._board_dealer_vuln(board_no)
                    return dealer.value
                except Exception:
                    pass
        return 0


class CompareReplayDialog(QDialog):
    """Side-by-side replay of two BenBoardRuns sharing one nav bar.

    Both runs should describe the same deal (same `pavlicek_id` or
    `board_number`); we don't enforce it because users may want to
    compare two different sessions.
    """

    def __init__(self, left: BenBoardRun, right: BenBoardRun,
                 left_label: str = "Open (this table)",
                 right_label: str = "Closed (Q-Plus)",
                 parent=None):
        super().__init__(parent)
        self.left_run = left
        self.right_run = right

        self.setWindowTitle(
            f"Compare: Board {left.board_number}"
            if left.board_number == right.board_number
            else f"Compare: {left.board_number} vs {right.board_number}"
        )

        screen = QApplication.primaryScreen().geometry()
        self.resize(int(screen.width() * 0.85), int(screen.height() * 0.9))
        apply_dialog_style(self)

        self._step = 0

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Version-skew banner — only shown when the two runs
        # disagree on which bidding system was in use, OR when a
        # run's recorded system doesn't match the current prefs.
        # That's the rule-out for "the two engines bid differently
        # because they were running different systems all along".
        skew = self._version_skew_warnings(left, right)
        if skew:
            warn = QLabel(
                '<div style="background:#fff3cd; border:1px solid '
                '#c49a00; padding:8px 12px; color:#5a3500;'
                ' font-size:13px;"><b>System metadata check:</b><br>'
                + "<br>".join(skew)
                + '</div>'
            )
            warn.setTextFormat(Qt.TextFormat.RichText)
            warn.setWordWrap(True)
            layout.addWidget(warn)

        # Two panels in a horizontal splitter so the user can resize.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_panel = _ReplayPanel(left, left_label)
        self.right_panel = _ReplayPanel(right, right_label)
        # Show all four hands on both sides (same deal in both rooms).
        self.left_panel.fallback_hands = dict(right.original_hands or {})
        self.right_panel.fallback_hands = dict(left.original_hands or {})
        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1, 1])
        layout.addWidget(splitter, stretch=1)

        # Navigation bar.
        nav = QFrame()
        nav.setStyleSheet(
            "QFrame { background-color: #f0f0f0; border: 1px solid #c0c0c0;"
            " border-radius: 4px; padding: 6px; }"
        )
        nav_layout = QHBoxLayout(nav)

        self.start_btn = QPushButton("|<")
        self.start_btn.setToolTip("Jump to start (Home)")
        self.start_btn.clicked.connect(self._on_start)
        nav_layout.addWidget(self.start_btn)

        self.prev_trick_btn = QPushButton("<< Prev Trick")
        self.prev_trick_btn.clicked.connect(self._on_prev_trick)
        nav_layout.addWidget(self.prev_trick_btn)

        self.prev_btn = QPushButton("< Prev")
        self.prev_btn.clicked.connect(self._on_prev)
        nav_layout.addWidget(self.prev_btn)

        nav_layout.addStretch()
        self.position_lbl = QLabel("0")
        self.position_lbl.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        nav_layout.addWidget(self.position_lbl)
        nav_layout.addStretch()

        self.next_btn = QPushButton("Next >")
        self.next_btn.clicked.connect(self._on_next)
        nav_layout.addWidget(self.next_btn)

        self.next_trick_btn = QPushButton("Next Trick >>")
        self.next_trick_btn.clicked.connect(self._on_next_trick)
        nav_layout.addWidget(self.next_trick_btn)

        self.end_btn = QPushButton(">|")
        self.end_btn.setToolTip("Jump to end (End)")
        self.end_btn.clicked.connect(self._on_end)
        nav_layout.addWidget(self.end_btn)

        layout.addWidget(nav)

        bottom = QHBoxLayout()
        self.dd_cb = QCheckBox("Double-dummy tricks per card")
        self.dd_cb.setChecked(True)
        self.dd_cb.setToolTip(
            "As in bcalc: every card the player to move holds carries the most tricks "
            "that side can take if it plays that card (all four hands seen). Green = a "
            "best card, red = a card that gives tricks away. The line under each table "
            "says how many tricks declarer can still take from there.")
        self.dd_cb.toggled.connect(self._on_dd_toggled)
        bottom.addWidget(self.dd_cb)
        self.bidding_btn = QPushButton("Bidding logs")
        self.bidding_btn.setToolTip(
            "Open both auction logs in a separate window so you can drag "
            "them to a second monitor while the play replay continues here."
        )
        self.bidding_btn.clicked.connect(self._on_show_bidding_logs)
        bottom.addWidget(self.bidding_btn)

        # biq's own offline comparison — deterministic, no Claude, never times
        # out, works even when Claude is disabled.
        self.biq_compare_btn = QPushButton("biq comparison")
        self.biq_compare_btn.setToolTip(
            "biq's own side-by-side write-up of the two rooms — contracts, "
            "result, IMP swing, and where the auctions/leads diverged. "
            "Instant, offline, no Claude needed."
        )
        self.biq_compare_btn.clicked.connect(self._on_biq_comparison)
        bottom.addWidget(self.biq_compare_btn)

        # Claude critique — a scope menu so the user can pick a small, fast
        # request (quick / bidding / play) instead of the slow full critique.
        from PyQt6.QtWidgets import QMenu
        self.transcript_btn = QPushButton("Claude critique ▾")
        self.transcript_btn.setToolTip(
            "Ask Claude to compare the two rooms. Pick a scope — a quick "
            "verdict or one topic returns fast; the full critique is "
            "thorough but slow."
        )
        critique_menu = QMenu(self.transcript_btn)
        for _scope in ('quick', 'bidding', 'play', 'full'):
            _lbl = self._SCOPE_META[_scope][0]
            act = critique_menu.addAction(_lbl)
            act.triggered.connect(
                lambda _checked=False, s=_scope: self._on_annotated_transcript(s))
        self.transcript_btn.setMenu(critique_menu)
        bottom.addWidget(self.transcript_btn)

        self.engine_compare_btn = QPushButton("Engine compare")
        self.engine_compare_btn.setToolTip(
            "Walk every call in BOTH auctions through the native bidder "
            "using each side's recorded system, and report each "
            "divergence between what was actually played and what our "
            "current rule-based engine would have bid. Use this to "
            "rule out 'the rooms bid differently because each engine's "
            "spec is reading the system slightly differently'."
        )
        self.engine_compare_btn.clicked.connect(self._on_engine_compare)
        bottom.addWidget(self.engine_compare_btn)

        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self._left_label = left_label
        self._right_label = right_label
        self._bidding_dialog = None

        self._refresh()

    def _on_show_bidding_logs(self):
        """Pop the auctions into their own top-level window."""
        existing = self._bidding_dialog
        # The bidding-log dialog uses WA_DeleteOnClose (via
        # make_detachable), so after the user closes it the Python
        # wrapper outlives the underlying C++ widget. Calling
        # isVisible() on a dead wrapper raises RuntimeError
        # ("wrapped C/C++ object of type BiddingLogDialog has been
        # deleted"), which is exactly the crash the user just hit
        # on a second click. Treat that error as "stale, drop it
        # and spawn a fresh dialog".
        still_visible = False
        if existing is not None:
            try:
                still_visible = existing.isVisible()
            except RuntimeError:
                self._bidding_dialog = None
                existing = None
        if still_visible and existing is not None:
            try:
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._bidding_dialog = None
        self._bidding_dialog = BiddingLogDialog(
            self.left_run, self.right_run,
            self._left_label, self._right_label, self,
        )
        self._bidding_dialog.show()
        try:
            self._bidding_dialog.raise_()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Navigation
    #
    # Step counts always come from the *longer* run so the user can scrub
    # through the entire wider game; the shorter side just freezes at the
    # last frame it has data for.
    # ------------------------------------------------------------------

    def _on_dd_toggled(self, on):
        self.left_panel.show_dd = self.right_panel.show_dd = bool(on)
        self._refresh()

    def _max_step(self) -> int:
        return max(self.left_panel.total_steps(),
                   self.right_panel.total_steps())

    def _refresh(self):
        max_step = self._max_step()
        self._step = max(0, min(self._step, max_step))
        self.left_panel.set_position(self._step)
        self.right_panel.set_position(self._step)
        # Position label: cards-played counter + a trick counter so
        # the user can see at a glance which trick the comparison is
        # parked on. Trick number = (step - 1) // 4 + 1 for steps
        # ≥ 1; at step 0 we're about to play the opening lead, so
        # show "Trick 1" pre-emptively (more useful than "Trick 0").
        trick_no = max(1, ((max(self._step, 1) - 1) // 4) + 1)
        max_tricks = max(1, (max_step + 3) // 4)
        self.position_lbl.setText(
            f"Card {self._step}/{max_step} • "
            f"Trick {trick_no}/{max_tricks}"
        )
        self.start_btn.setEnabled(self._step > 0)
        self.prev_btn.setEnabled(self._step > 0)
        self.prev_trick_btn.setEnabled(self._step > 0)
        self.next_btn.setEnabled(self._step < max_step)
        self.next_trick_btn.setEnabled(self._step < max_step)
        self.end_btn.setEnabled(self._step < max_step)

    def _on_start(self):
        self._step = 0
        self._refresh()

    def _on_end(self):
        self._step = self._max_step()
        self._refresh()

    def _on_prev(self):
        self._step = max(0, self._step - 1)
        self._refresh()

    def _on_next(self):
        self._step = min(self._max_step(), self._step + 1)
        self._refresh()

    def _on_prev_trick(self):
        # Snap back to the start of the current trick, or one trick
        # earlier if we're already at the start. Both runs share the
        # same card-step grid (auction lives in its own dialog), so a
        # single calculation drives both panels.
        if self._step == 0:
            return
        within = (self._step - 1) % 4
        if within == 0:
            self._step = max(0, self._step - 4)
        else:
            self._step -= within
        self._refresh()

    def _on_next_trick(self):
        within = self._step % 4
        if within == 0:
            self._step = min(self._max_step(), self._step + 4)
        else:
            self._step = min(self._max_step(), self._step + (4 - within))
        self._refresh()

    # ------------------------------------------------------------------
    # Annotated transcript — Claude Opus 4.7 thinking comparison of both
    # rooms. Builds a BDL from each BenBoardRun, frames them as an
    # open-vs-closed comparison prompt, and routes through the host
    # MainWindow's _run_claude_with_dialog helper so we share the same
    # progress dialog / error handling as the Hint button.
    # ------------------------------------------------------------------

    def _run_to_bdl(self, run: BenBoardRun) -> str:
        """Build BDL text for a finished BenBoardRun by synthesising a
        BoardState and handing it to the existing build_bdl_snapshot
        formatter. viewer_seat=None so every hand is visible (this is a
        post-mortem with full information — both rooms are over)."""
        try:
            from backend.models import BoardState
            from ..game_logger import build_bdl_snapshot
        except Exception:
            return ""
        try:
            dealer, vuln = BoardState._board_dealer_vuln(run.board_number)
        except Exception:
            dealer = run.original_hands and list(run.original_hands)[0]
            vuln = None
        state = BoardState(
            board_number=run.board_number,
            dealer=dealer,
            vulnerability=vuln,
            hands=dict(run.original_hands),
            auction=list(run.auction),
            contract=run.contract,
            tricks=list(run.tricks),
            declarer_tricks=run.declarer_tricks,
        )
        result_summary = ""
        if run.contract:
            target = run.contract.target_tricks()
            diff = run.declarer_tricks - target
            verdict = ("made exactly" if diff == 0
                       else f"made +{diff}" if diff > 0
                       else f"down {abs(diff)}")
            result_summary = (
                f"Result: declarer {verdict} "
                f"({run.declarer_tricks} tricks); "
                f"NS {run.ns_score:+d}, EW {run.ew_score:+d}."
            )
        else:
            result_summary = "Result: passed out (no play)."
        return build_bdl_snapshot(
            state,
            original_hands=run.original_hands,
            viewer_seat=None,  # full information for hindsight analysis
            phase='finished' if run.played else None,
            contract=run.contract,
            result_summary=result_summary,
            deal_id=run.pavlicek_id or "",
            include_footer=False,
        )

    # Scope presets for the Claude critique. Each is (menu label, wait note,
    # timeout seconds, task block). The smaller scopes are FAST — they ask for
    # far less output, so they return well inside their timeout instead of the
    # multi-minute full critique that was timing out.
    _TASK_BLOCKS = {
        'quick': (
            "TASK: In 6–10 sentences total, give a quick verdict — which "
            "room reached the better contract, the single biggest BIDDING "
            "difference, the single biggest CARD-PLAY difference, the IMP "
            "swing and who won it, and the ONE thing the human should most "
            "take away. Be concise; do not walk the deal card by card."
        ),
        'bidding': (
            "TASK: Compare ONLY the bidding. Walk the auction round by round; "
            "for each call that DIFFERS between the rooms, say who was right "
            "and why (HCP, shape, vulnerability, system: SAYC / 2-over-1 / "
            "Precision). End with: what the human bid well, and the single "
            "auction call they should most reconsider. Ignore the card play."
        ),
        'play': (
            "TASK: Compare ONLY the card play (assume the contracts as given). "
            "Walk the play trick by trick: opening lead, declarer's plan, key "
            "defensive plays and signals. When the rooms diverge, name the "
            "principle (suit-preference, second-hand-low, hold-up, endplay, "
            "squeeze, count) that should drive the choice. End with what the "
            "human played well and their one biggest technical leak. Ignore "
            "the auction."
        ),
        'full': (
            "TASKS:\n"
            "1. Annotated bidding comparison. Walk through the auction "
            "round by round. For each call that DIFFERS between the two "
            "rooms, explain who was right and why (cite HCP, distribution, "
            "vulnerability, system: SAYC / 2-over-1 / Precision).\n\n"
            "2. Annotated card-play comparison. Walk through the play "
            "trick by trick. Note the opening lead, declarer's plan, key "
            "defensive plays, and signalling. When the two rooms diverge, "
            "explain the principle (suit-prefence, second-hand low, "
            "endplay, squeeze, count signals, etc.) that should have "
            "driven the choice.\n\n"
            "3. IMP swing. Compute the IMP difference if any and state "
            "which room came out ahead.\n\n"
            "4. WHAT THE HUMAN DID WELL — be specific. Name the seat, the "
            "round / trick, the action, and exactly why it was good "
            "(matched expert line, made the right inference, didn't fall "
            "for a trap, etc.). At least 2 items if there is anything to "
            "praise; if there's truly nothing, say so honestly.\n\n"
            "5. WHERE THE HUMAN COULD IMPROVE — concrete, actionable. "
            "Each item should name the spot (auction round or trick "
            "number), the actual choice, the better alternative, and a "
            "short principle so it transfers to similar future deals. "
            "Don't pad with generic advice; cite the specific cards."
        ),
    }
    # (menu label, timeout seconds, extended_thinking). The quick verdict
    # runs WITHOUT extended thinking — thinking is the latency driver, and a
    # 6-10 sentence verdict doesn't need it, so it returns well inside the
    # timeout instead of blowing past it as it did before. The deeper scopes
    # keep thinking on (and longer timeouts) for a more rigorous critique.
    _SCOPE_META = {
        'quick':   ("Quick verdict (fast)", 300, False),
        'bidding': ("Bidding only", 360, True),
        'play':    ("Card play only", 360, True),
        'full':    ("Full critique (slow)", 900, True),
    }

    def _build_transcript_prompt(self, scope: str = 'full') -> str:
        """Assemble the side-by-side comparison prompt for the given scope."""
        left_bdl = self._run_to_bdl(self.left_run) or "(no left BDL available)"
        right_bdl = self._run_to_bdl(self.right_run) or "(no right BDL available)"
        task = self._TASK_BLOCKS.get(scope, self._TASK_BLOCKS['full'])
        try:
            from ui.teaching_view import signalling_conventions_text
            signals = (signalling_conventions_text()
                       + "\n  (These apply wherever biq or the human "
                       "defended; a Q-Plus room cards by Q-Plus's own "
                       "agreements.)\n\n")
        except Exception:
            signals = ""
        return (
            "You are reviewing the SAME bridge deal played at two different "
            "tables in a teams match. Compare the two rooms side by side and "
            "help a human bridge player study to improve their game.\n\n"
            "Use FULL hindsight (every hand is visible to you, both rooms "
            "are complete). Open room = the table where a human played at "
            "one or more seats. Closed room = the bot reference table "
            "(Q-Plus, BridgeIQ, etc.) for the same deal — it's the "
            "yardstick.\n\n"
            f"{signals}"
            f"=== OPEN ROOM ({self._left_label}) ===\n"
            f"{left_bdl}\n\n"
            f"=== CLOSED ROOM ({self._right_label}) ===\n"
            f"{right_bdl}\n\n"
            f"{task}\n\n"
            "Format the output as Markdown with clear section headers. "
            "Use code blocks for short BDL fragments when quoting "
            "auction lines or trick cards verbatim. Keep the tone "
            "constructive and focused — the human is studying, not "
            "being scolded."
        )

    @staticmethod
    def _version_skew_warnings(left: BenBoardRun, right: BenBoardRun):
        """Return a list of warning strings when the two runs disagree
        on bidding-system metadata, or when either run was recorded
        under a system that differs from the user's current
        preferences.

        Designed to rule out "the rooms bid differently because the
        bots were actually running different specs" — the user can
        glance at the banner and know whether the divergence is a
        bidder issue (banner clean) or a configuration issue (banner
        loud).
        """
        msgs = []
        l_ns = (getattr(left, 'ns_bidding_system', '') or '').strip()
        r_ns = (getattr(right, 'ns_bidding_system', '') or '').strip()
        l_ew = (getattr(left, 'ew_bidding_system', '') or '').strip()
        r_ew = (getattr(right, 'ew_bidding_system', '') or '').strip()

        if l_ns and r_ns and l_ns != r_ns:
            msgs.append(
                f"NS system tag DIFFERS — open=<b>{l_ns}</b>, "
                f"closed=<b>{r_ns}</b>. The two rooms were recorded "
                "under different NS specs; bid divergence is "
                "expected.")
        if l_ew and r_ew and l_ew != r_ew:
            msgs.append(
                f"EW system tag DIFFERS — open=<b>{l_ew}</b>, "
                f"closed=<b>{r_ew}</b>. The two rooms were recorded "
                "under different EW specs; bid divergence is "
                "expected.")
        if not l_ns and not r_ns:
            msgs.append(
                "NS system tag is blank on BOTH runs — the bot "
                "version that actually played the deals isn't "
                "recoverable from the BDL metadata.")
        elif not l_ns:
            msgs.append(
                f"NS system tag is blank on the open-room run "
                f"(closed=<b>{r_ns}</b>).")
        elif not r_ns:
            msgs.append(
                f"NS system tag is blank on the closed-room run "
                f"(open=<b>{l_ns}</b>).")

        # Compare against the live native_bidding_system preferences
        # so a stale recording is flagged.
        try:
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
            cur_default = (getattr(prefs, 'native_bidding_system', '')
                           or '').strip()
            cur_ns = (getattr(prefs, 'ns_bidding_system', '')
                      or '').strip() or cur_default
            cur_ew = (getattr(prefs, 'ew_bidding_system', '')
                      or '').strip() or cur_default
        except Exception:
            cur_ns = cur_ew = ''

        if cur_ns and l_ns and cur_ns != l_ns:
            msgs.append(
                f"Open-room NS was recorded as <b>{l_ns}</b> but the "
                f"current preference is <b>{cur_ns}</b> — replaying "
                "this deal now would use a different system.")
        if cur_ns and r_ns and cur_ns != r_ns:
            msgs.append(
                f"Closed-room NS was recorded as <b>{r_ns}</b> but "
                f"the current preference is <b>{cur_ns}</b>.")
        if cur_ew and l_ew and cur_ew != l_ew:
            msgs.append(
                f"Open-room EW was recorded as <b>{l_ew}</b> but the "
                f"current preference is <b>{cur_ew}</b>.")
        if cur_ew and r_ew and cur_ew != r_ew:
            msgs.append(
                f"Closed-room EW was recorded as <b>{r_ew}</b> but "
                f"the current preference is <b>{cur_ew}</b>.")
        return msgs

    def _on_engine_compare(self):
        """Walk every call in both auctions through the native bidder
        and pop a side-by-side report showing where 'what was played'
        and 'what our engine would say' diverge."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
        from PyQt6.QtGui import QFont

        left_trace = _replay_auction_through_native_bidder(self.left_run)
        right_trace = _replay_auction_through_native_bidder(self.right_run)

        html = ['<div style="font-family: monospace; font-size: 13pt;">']
        html.append(
            f"<h2>Engine decision-tree compare — Board "
            f"{self.left_run.board_number}</h2>"
        )
        skew = self._version_skew_warnings(self.left_run, self.right_run)
        if skew:
            html.append(
                '<div style="background:#fff3cd; border:1px solid '
                '#c49a00; padding:8px 12px; color:#5a3500;'
                ' margin-bottom:12px;"><b>System metadata check:'
                '</b><br>' + "<br>".join(skew) + '</div>'
            )
        html.append(
            "<p>Each row is one call in the auction. <b>Actual</b> "
            "is the bid that landed in the room; <b>Native</b> is "
            "what our rule-based bidder would pick given the same "
            "auction-so-far, hand, and the system tag recorded for "
            "that side. Rows highlighted red are divergences worth "
            "investigating — that's where the rule-based "
            "interpretation of the recorded system differs from "
            "whatever engine actually drove the deal.</p>"
        )

        def render(side_label, trace):
            if not trace:
                return f"<h3>{side_label}</h3><p>(no auction data)</p>"
            rows = [f"<h3>{side_label}</h3>"]
            rows.append(
                '<table cellpadding="6" cellspacing="0" border="1" '
                'style="border-collapse: collapse; width: 100%;">'
            )
            rows.append(
                "<tr style='background:#e0e0e0;'>"
                "<th>#</th><th>Seat</th><th>Side</th>"
                "<th>System</th><th>Actual</th><th>Native</th>"
                "<th>Why (Native)</th></tr>"
            )
            for i, row in enumerate(trace):
                colour = "#ffe0e0" if not row['matches'] else "#ffffff"
                actual_str = row['actual'].symbol() if row['actual'] else "—"
                native_str = (row['native'].symbol()
                              if row['native'] else "—")
                marker = "✗" if not row['matches'] else "✓"
                rows.append(
                    f"<tr style='background:{colour};'>"
                    f"<td>{i + 1}</td>"
                    f"<td>{row['seat'].to_char()}</td>"
                    f"<td>{row['side']}</td>"
                    f"<td>{row['system_name']}</td>"
                    f"<td><b>{actual_str}</b></td>"
                    f"<td><b>{native_str}</b> {marker}</td>"
                    f"<td>{row['explanation'] or ''}</td>"
                    f"</tr>"
                )
            rows.append("</table>")
            # Quick summary line.
            misses = sum(1 for r in trace if not r['matches'])
            rows.append(
                f"<p style='color:#444;'><b>{misses}</b> of "
                f"<b>{len(trace)}</b> calls diverged from the "
                f"native bidder.</p>"
            )
            return "".join(rows)

        html.append(render(f"Open room ({self._left_label})", left_trace))
        html.append(render(f"Closed room ({self._right_label})", right_trace))
        html.append("</div>")

        dlg = QDialog(self)
        dlg.setWindowTitle(
            f"Engine decision-tree compare — Board {self.left_run.board_number}"
        )
        dlg.resize(1100, 800)
        from .dialog_style import make_detachable
        make_detachable(dlg)
        v = QVBoxLayout(dlg)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setHtml("".join(html))
        view.setFont(QFont("Arial", 11))
        v.addWidget(view, stretch=1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.close)
        v.addWidget(close)
        dlg.show()

    # ------------------------------------------------------------------
    # biq's own offline comparison — deterministic, no Claude. Compares the
    # two rooms' contracts, results, IMP swing, auctions and opening leads.
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_contract(run: BenBoardRun) -> str:
        c = run.contract
        if c is None:
            return "passed out"
        dbl = ("XX" if getattr(c, 'redoubled', False)
               else "X" if getattr(c, 'doubled', False) else "")
        try:
            strain = c.suit.symbol()
        except Exception:
            strain = str(getattr(c, 'suit', ''))
        return f"{c.level}{strain}{dbl} by {c.declarer.to_char()}"

    @staticmethod
    def _fmt_result(run: BenBoardRun) -> str:
        c = run.contract
        if c is None:
            return "—"
        try:
            diff = run.declarer_tricks - c.target_tricks()
        except Exception:
            return f"{run.declarer_tricks} tricks"
        made = ("made exactly" if diff == 0
                else f"made +{diff}" if diff > 0 else f"down {abs(diff)}")
        return f"{run.declarer_tricks} tricks — {made}"

    @staticmethod
    def _opening_lead(run: BenBoardRun) -> str:
        try:
            first = run.tricks[0].cards[0]
            return first.symbol() if hasattr(first, 'symbol') else str(first)
        except Exception:
            return "—"

    def _build_biq_comparison_html(self) -> str:
        from backend.models import diff_to_imps, BoardState, Seat
        L, R = self.left_run, self.right_run
        l_ns, r_ns = L.ns_score, R.ns_score
        # Cross-IMP: the value of the NS-score difference between the tables.
        swing = diff_to_imps(abs(l_ns - r_ns))
        if l_ns > r_ns:
            verdict = (f"Open room is <b>{swing} IMP</b> better for N/S."
                       if swing else "Rooms are level for N/S.")
        elif r_ns > l_ns:
            verdict = (f"Closed room is <b>{swing} IMP</b> better for N/S."
                       if swing else "Rooms are level for N/S.")
        else:
            verdict = "Both rooms scored the same — flat board."

        h = ['<div style="font-family: Arial; font-size: 12pt;">']
        h.append(f"<h2>biq comparison — Board {L.board_number}</h2>")
        h.append(f"<p style='font-size:13pt;'>{verdict}</p>")

        # Result table.
        h.append('<table cellpadding="6" cellspacing="0" border="1" '
                 'style="border-collapse:collapse; width:100%;">')
        h.append("<tr style='background:#e0e0e0;'><th></th>"
                 f"<th>Open ({self._left_label})</th>"
                 f"<th>Closed ({self._right_label})</th></tr>")

        def row(label, lv, rv, hi=False):
            bg = " style='background:#fff3cd;'" if hi else ""
            return (f"<tr{bg}><td><b>{label}</b></td>"
                    f"<td>{lv}</td><td>{rv}</td></tr>")

        lc, rc = self._fmt_contract(L), self._fmt_contract(R)
        h.append(row("Contract", lc, rc, hi=(lc != rc)))
        h.append(row("Result", self._fmt_result(L), self._fmt_result(R)))
        ll, rl = self._opening_lead(L), self._opening_lead(R)
        h.append(row("Opening lead", ll, rl, hi=(ll != rl)))
        h.append(row("N/S score", f"{l_ns:+d}", f"{r_ns:+d}"))
        h.append(row("E/W score", f"{L.ew_score:+d}", f"{R.ew_score:+d}"))
        h.append("</table>")

        # Auction divergence — call by call.
        try:
            dealer, _ = BoardState._board_dealer_vuln(L.board_number)
        except Exception:
            dealer = Seat.NORTH

        def _bid_str(b):
            try:
                return b.symbol()
            except Exception:
                return str(b)

        la, ra = list(L.auction), list(R.auction)
        h.append("<h3>Auction</h3>")
        if la == ra:
            h.append("<p>Both rooms bid <b>identically</b>.</p>")
        else:
            h.append('<table cellpadding="6" cellspacing="0" border="1" '
                     'style="border-collapse:collapse; width:100%;">')
            h.append("<tr style='background:#e0e0e0;'><th>#</th><th>Seat</th>"
                     "<th>Open</th><th>Closed</th></tr>")
            for i in range(max(len(la), len(ra))):
                seat = Seat((int(dealer) + i) % 4)
                lb = _bid_str(la[i]) if i < len(la) else "—"
                rb = _bid_str(ra[i]) if i < len(ra) else "—"
                bg = " style='background:#ffe0e0;'" if lb != rb else ""
                h.append(f"<tr{bg}><td>{i + 1}</td>"
                         f"<td>{seat.to_char()}</td>"
                         f"<td><b>{lb}</b></td><td><b>{rb}</b></td></tr>")
            h.append("</table>")
            h.append("<p style='color:#444;'>Rows in red are where the two "
                     "rooms made a different call.</p>")

        h.append("<p style='color:#666; font-size:10pt;'>biq's own read — "
                 "no Claude. For a coached, prose critique use "
                 "<b>Claude critique</b>.</p>")
        h.append("</div>")
        return "".join(h)

    def _on_biq_comparison(self):
        """Pop biq's own offline two-room comparison (no Claude)."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
        from PyQt6.QtGui import QFont
        from .dialog_style import make_detachable
        try:
            html = self._build_biq_comparison_html()
        except Exception as ex:
            html = f"<p>Could not build the comparison: {ex!r}</p>"
        dlg = QDialog(self)
        dlg.setWindowTitle(f"biq comparison — Board {self.left_run.board_number}")
        dlg.resize(1000, 760)
        make_detachable(dlg)
        v = QVBoxLayout(dlg)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setHtml(html)
        view.setFont(QFont("Arial", 11))
        v.addWidget(view, stretch=1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.close)
        v.addWidget(close)
        dlg.show()

    def _on_annotated_transcript(self, scope: str = 'full'):
        """Generate the side-by-side annotated transcript via Claude.

        `scope` selects how much to ask for — 'quick' / 'bidding' / 'play' /
        'full'. The smaller scopes return fast (and carry shorter timeouts) so
        the user has a request that won't time out."""
        prompt = self._build_transcript_prompt(scope)
        label, timeout, extended_thinking = self._SCOPE_META.get(
            scope, self._SCOPE_META['full'])
        host = self._find_main_window()
        title = (f"Claude critique ({label}) — Board "
                 f"{self.left_run.board_number}"
                 if self.left_run.board_number == self.right_run.board_number
                 else (f"Claude critique ({label}) — "
                       f"{self.left_run.board_number} vs "
                       f"{self.right_run.board_number}"))
        wait = ("Claude is comparing the two rooms…"
                if scope != 'full'
                else "Claude is comparing the two rooms… "
                     "(a full two-room critique can take several minutes)")
        if host is not None and hasattr(host, '_run_claude_with_dialog'):
            host._run_claude_with_dialog(
                prompt=prompt,
                title=title,
                wait_label=wait,
                timeout_seconds=timeout,
                extended_thinking=extended_thinking,
            )
            return
        # Fallback path — no MainWindow reachable (unlikely): run claude
        # inline and pop a plain scrollable dialog. Keeps the feature
        # available if the dialog ever gets shown from a different host.
        self._run_claude_inline_fallback(prompt, title)

    def _find_main_window(self):
        """Walk up the parent chain looking for the MainWindow (or any
        object with the _run_claude_with_dialog helper)."""
        node = self.parent()
        while node is not None:
            if hasattr(node, '_run_claude_with_dialog'):
                return node
            try:
                node = node.parent()
            except Exception:
                return None
        return None

    def _run_claude_inline_fallback(self, prompt: str, title: str):
        """Last-resort claude invocation when no MainWindow is reachable.
        Keeps the dialog usable as a standalone widget."""
        import shutil
        import subprocess
        if not shutil.which('claude'):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, title,
                "Claude CLI is not installed on this machine — install "
                "it to enable annotated transcripts.")
            return
        try:
            r = subprocess.run(
                ['claude', '-p', '--model', 'claude-opus-4-8',
                 '--thinking', 'enabled', '--max-turns', '1', prompt],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900,
            )
            text = (r.stdout or '').strip()
        except Exception as ex:
            text = f"(claude call failed: {ex})"
        self._show_text_dialog(title, text)

    def _show_text_dialog(self, title: str, text: str):
        """Plain scrollable dialog for the fallback path."""
        from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(900, 700)
        layout = QVBoxLayout(dlg)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text or "(no response)")
        view.setFont(QFont("Courier", 11))
        layout.addWidget(view)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        layout.addWidget(close)
        dlg.exec()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Right:
            self._on_next()
        elif key == Qt.Key.Key_Left:
            self._on_prev()
        elif key == Qt.Key.Key_PageDown or key == Qt.Key.Key_Down:
            self._on_next_trick()
        elif key == Qt.Key.Key_PageUp or key == Qt.Key.Key_Up:
            self._on_prev_trick()
        elif key == Qt.Key.Key_Home:
            self._on_start()
        elif key == Qt.Key.Key_End:
            self._on_end()
        elif key == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)
