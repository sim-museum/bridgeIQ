"""Q-Plus-style "Print current deal" dialog.

Mirrors the Q-Plus spec at BRIDGE.HLQ ``.printing`` (manual lines
1930-1940): the user picks a card-layout option on the left and
checks which extra sections to include on the right, then clicks
**Print…** to send it to a printer (with **Preview** as a side
option). Only the current deal is printable.

Implementation notes:
  * Renders to an HTML document loaded into a ``QTextDocument`` so
    Qt's print-support module can paginate and target any printer
    the OS knows about.
  * Card-layout options:
      - DIAGRAM  — classic 4-corner deal diagram (N/W/E/S).
      - COMPACT  — one row per hand, ``S: ♠AKQ ♥KQ32 ♦Q865 ♣65``.
  * The "extras" checkboxes track exactly the toggles Q-Plus
    offers: contract+result, auction, played tricks, remark/notes.
"""

from __future__ import annotations

from typing import Optional, Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextDocument
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QRadioButton, QCheckBox, QButtonGroup,
)

from .dialog_style import apply_dialog_style


# Reuse the suit-color helper from the rest of the UI so the
# printout matches whatever colour preference is active.
try:
    from ..styles import get_suit_color as _get_suit_color
except Exception:
    def _get_suit_color(name):
        return {'spades': '#000', 'hearts': '#c00',
                'diamonds': '#06c', 'clubs': '#080'}.get(name, '#000')


_SUIT_SYMBOLS = {0: '♠', 1: '♥',
                 2: '♦', 3: '♣'}
_SUIT_NAMES = {0: 'spades', 1: 'hearts',
               2: 'diamonds', 3: 'clubs'}
_SEAT_CHARS = ['N', 'E', 'S', 'W']
_SEAT_FULL = {'N': 'North', 'E': 'East',
              'S': 'South', 'W': 'West'}


class PrintCurrentHandDialog(QDialog):
    """Print-options dialog for the current deal.

    `original_hands` (dict[Seat, Hand]) is the pre-play snapshot —
    used for the printed diagram so cards already played still show.
    Falls back to ``board.hands`` when the snapshot is missing.
    """

    def __init__(self, board, original_hands=None, contract=None,
                 result_str: str = "", banner_text: str = "",
                 remark: str = "", parent=None):
        super().__init__(parent)
        self.board = board
        self.original_hands = original_hands or (
            dict(board.hands) if board is not None else {})
        self.contract = contract or (
            getattr(board, 'contract', None) if board else None)
        self.result_str = result_str
        self.banner_text = banner_text
        self.remark = remark

        self.setWindowTitle("Print current hand")
        self.setMinimumWidth(560)
        apply_dialog_style(self)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        intro = QLabel(
            "<i>Only the current deal can be printed.</i> Pick a "
            "layout on the left and the sections to include on the "
            "right."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # Two-column layout: HOW switches on the left, WHICH on the right.
        cols = QHBoxLayout()
        cols.setSpacing(14)

        # HOW — card layout group.
        how_group = QGroupBox("Card layout")
        how_box = QVBoxLayout(how_group)
        self.layout_group = QButtonGroup(self)
        self.layout_diagram = QRadioButton("Diagram (N/W-E/S)")
        self.layout_diagram.setChecked(True)
        self.layout_compact = QRadioButton("Compact (one line per hand)")
        for rb in (self.layout_diagram, self.layout_compact):
            how_box.addWidget(rb)
            self.layout_group.addButton(rb)
        how_box.addStretch()
        cols.addWidget(how_group, stretch=1)

        # WHICH — extras group.
        which_group = QGroupBox("Include")
        which_box = QVBoxLayout(which_group)
        self.include_header = QCheckBox(
            "Header (board / dealer / vulnerability)")
        self.include_header.setChecked(True)
        self.include_contract = QCheckBox("Contract and result")
        self.include_contract.setChecked(True)
        self.include_auction = QCheckBox("Auction")
        self.include_auction.setChecked(True)
        self.include_tricks = QCheckBox("Played tricks")
        self.include_tricks.setChecked(True)
        self.include_remark = QCheckBox("Remark / notes")
        self.include_remark.setChecked(bool(self.remark))
        for cb in (self.include_header, self.include_contract,
                   self.include_auction, self.include_tricks,
                   self.include_remark):
            which_box.addWidget(cb)
        which_box.addStretch()
        cols.addWidget(which_group, stretch=1)

        root.addLayout(cols)

        # Button row.
        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton("Preview…")
        self.preview_btn.clicked.connect(self._on_preview)
        btn_row.addWidget(self.preview_btn)

        btn_row.addStretch()

        self.print_btn = QPushButton("Print…")
        self.print_btn.setDefault(True)
        self.print_btn.clicked.connect(self._on_print)
        btn_row.addWidget(self.print_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_print(self):
        """Send the rendered HTML to the user's printer of choice."""
        try:
            from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
        except Exception as ex:
            self._show_error(
                f"Qt print support is not available on this system "
                f"({ex!r}). Use Preview to copy the deal into your "
                "own document instead.")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        doc = self._build_document()
        try:
            doc.print(printer)
        except Exception as ex:
            self._show_error(f"Printing failed: {ex!r}")
            return
        self.accept()

    def _on_preview(self):
        """Open Qt's built-in print preview window."""
        try:
            from PyQt6.QtPrintSupport import (
                QPrinter, QPrintPreviewDialog)
        except Exception as ex:
            self._show_error(
                f"Qt print support is not available on this system "
                f"({ex!r}).")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        preview = QPrintPreviewDialog(printer, self)
        preview.resize(900, 1000)

        def _paint(prn):
            self._build_document().print(prn)
        preview.paintRequested.connect(_paint)
        preview.exec()

    def _show_error(self, msg: str):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Print", msg)

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def _build_document(self) -> QTextDocument:
        html = self._build_html()
        doc = QTextDocument()
        doc.setHtml(html)
        # Use a sensible default font for printed output — the
        # dialog runs at the screen font otherwise.
        doc.setDefaultFont(QFont("Arial", 11))
        return doc

    def _build_html(self) -> str:
        parts: List[str] = []
        parts.append(
            "<html><head><meta charset='utf-8'></head><body "
            "style='font-family: Arial, sans-serif;'>"
        )

        if self.include_header.isChecked():
            parts.append(self._render_header_html())

        parts.append(self._render_cards_html())

        if self.include_contract.isChecked():
            parts.append(self._render_contract_html())

        if self.include_auction.isChecked() and getattr(
                self.board, 'auction', None):
            parts.append(self._render_auction_html())

        if self.include_tricks.isChecked() and getattr(
                self.board, 'tricks', None):
            parts.append(self._render_tricks_html())

        if self.include_remark.isChecked() and (self.remark or "").strip():
            parts.append(self._render_remark_html())

        parts.append("</body></html>")
        return "".join(parts)

    # ---- sections ---------------------------------------------------

    def _render_header_html(self) -> str:
        from backend.models import Vulnerability
        b = self.board
        board_no = getattr(b, 'board_number', '')
        try:
            dealer_char = b.dealer.to_char()
        except Exception:
            dealer_char = '?'
        vul_map = {
            Vulnerability.NONE: 'None',
            Vulnerability.NS: 'NS',
            Vulnerability.EW: 'EW',
            Vulnerability.BOTH: 'Both',
        }
        try:
            vul_str = vul_map.get(b.vulnerability, str(b.vulnerability))
        except Exception:
            vul_str = '—'
        return (
            f"<h2 style='margin: 0 0 4px 0;'>Board {board_no}</h2>"
            f"<p style='margin: 0 0 12px 0; color: #444;'>"
            f"Dealer: <b>{dealer_char}</b> &nbsp; "
            f"Vulnerability: <b>{vul_str}</b></p>"
        )

    def _render_cards_html(self) -> str:
        if self.layout_diagram.isChecked():
            return self._render_diagram_html()
        return self._render_compact_html()

    def _render_diagram_html(self) -> str:
        """Classic 4-corner bridge diagram. North on top row, S on
        bottom row centred, W and E on the middle row.

        Implemented as a 3x3 table so QTextDocument's table renderer
        (which lacks proper grid-layout) still lines the four hands
        up correctly on paper.
        """
        hands = self.original_hands or {}
        from backend.models import Seat
        def cell(seat) -> str:
            hand = hands.get(seat)
            return self._hand_block_html(seat.to_char() if seat else '?',
                                         hand)
        n = cell(Seat.NORTH)
        e = cell(Seat.EAST)
        s = cell(Seat.SOUTH)
        w = cell(Seat.WEST)
        return (
            "<table cellpadding='8' cellspacing='0' "
            "style='border-collapse: collapse; width: 100%; "
            "margin: 6px 0 12px 0;'>"
            f"<tr><td></td><td style='text-align: center;'>{n}</td>"
            "<td></td></tr>"
            f"<tr><td>{w}</td><td></td><td>{e}</td></tr>"
            f"<tr><td></td><td style='text-align: center;'>{s}</td>"
            "<td></td></tr>"
            "</table>"
        )

    def _hand_block_html(self, seat_char: str, hand) -> str:
        if hand is None or not getattr(hand, 'cards', None):
            return (f"<div style='font-family: monospace;'>"
                    f"<b>{seat_char}</b><br>— — — —</div>")
        # Group by suit; sort high → low.
        from backend.models import Suit
        groups: Dict[int, list] = {0: [], 1: [], 2: [], 3: []}
        for c in hand.cards:
            try:
                groups[c.suit.value].append(c.rank.to_char())
            except Exception:
                continue
        rows = []
        for sv in (0, 1, 2, 3):
            sym = _SUIT_SYMBOLS[sv]
            colour = _get_suit_color(_SUIT_NAMES[sv])
            ranks = ' '.join(groups[sv]) if groups[sv] else '—'
            rows.append(
                f"<div><span style='color:{colour}'>{sym}</span>"
                f"&nbsp;{ranks}</div>"
            )
        return (
            f"<div style='font-family: monospace; line-height: 1.3;'>"
            f"<b>{seat_char}</b><br>" + "".join(rows) + "</div>"
        )

    def _render_compact_html(self) -> str:
        """One-line-per-hand. Used by the COMPACT layout switch."""
        hands = self.original_hands or {}
        from backend.models import Seat
        lines = []
        for seat in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
            hand = hands.get(seat)
            if hand is None:
                continue
            groups: Dict[int, list] = {0: [], 1: [], 2: [], 3: []}
            for c in hand.cards:
                try:
                    groups[c.suit.value].append(c.rank.to_char())
                except Exception:
                    continue
            suit_bits = []
            for sv in (0, 1, 2, 3):
                colour = _get_suit_color(_SUIT_NAMES[sv])
                ranks = ''.join(groups[sv]) if groups[sv] else '—'
                suit_bits.append(
                    f"<span style='color:{colour}'>"
                    f"{_SUIT_SYMBOLS[sv]}</span>{ranks}"
                )
            lines.append(
                f"<div style='font-family: monospace;'>"
                f"<b>{seat.to_char()}:</b> "
                + ' &nbsp; '.join(suit_bits)
                + "</div>"
            )
        return ("<div style='margin: 6px 0 12px 0;'>"
                + "".join(lines) + "</div>")

    # ---- contract / auction / tricks / remark ----------------------

    def _render_contract_html(self) -> str:
        if self.contract is None:
            return ("<p><b>Contract:</b> Passed out</p>")
        try:
            decl = self.contract.declarer.to_char()
        except Exception:
            decl = '?'
        contract_str = (self.contract.to_str()
                        if hasattr(self.contract, 'to_str')
                        else '—')
        banner = self.banner_text or ''
        result = self.result_str or ''
        bits = [f"<b>Contract:</b> {contract_str} by {decl}"]
        if result:
            bits.append(f"<b>Result:</b> {result}")
        if banner:
            bits.append(f"<i>{banner}</i>")
        return "<p style='margin: 8px 0;'>" + ' &nbsp; • &nbsp; '.join(
            bits) + "</p>"

    def _render_auction_html(self) -> str:
        from backend.models import Seat
        try:
            dealer = self.board.dealer
            dealer_idx = dealer.value
        except Exception:
            dealer_idx = 0
        rows = [
            "<table cellpadding='4' cellspacing='0' "
            "style='border-collapse: collapse; margin: 8px 0;'>"
            "<tr style='background:#e0e0e0;'>"
        ]
        for char in _SEAT_CHARS:
            rows.append(f"<th>{char}</th>")
        rows.append("</tr>")

        col = 0
        cells: list = []
        # Pad cells before the dealer's first call with em dashes.
        for _ in range(dealer_idx):
            cells.append("—")
        for b in self.board.auction:
            cells.append(self._format_bid_html(b))
        # Fill to a multiple of 4 with blanks.
        while len(cells) % 4:
            cells.append("")
        rows.append("<tr>")
        for i, c in enumerate(cells):
            if i and i % 4 == 0:
                rows.append("</tr><tr>")
            rows.append(
                f"<td style='border: 1px solid #aaa; padding: 2px 10px;"
                f" text-align: center;'>{c}</td>"
            )
        rows.append("</tr></table>")
        return "<div><b>Auction</b>" + "".join(rows) + "</div>"

    @staticmethod
    def _format_bid_html(bid) -> str:
        if bid is None:
            return ""
        if getattr(bid, 'is_pass', False):
            return "Pass"
        if getattr(bid, 'is_double', False):
            return "X"
        if getattr(bid, 'is_redouble', False):
            return "XX"
        # Regular call: level + suit symbol with colour.
        try:
            sv = bid.suit.value
        except Exception:
            return str(bid)
        if sv == 4:
            return f"{bid.level}NT"
        sym = _SUIT_SYMBOLS.get(sv, '?')
        colour = _get_suit_color(_SUIT_NAMES.get(sv, 'spades'))
        return (f"{bid.level}<span style='color:{colour}'>{sym}</span>")

    def _render_tricks_html(self) -> str:
        rows = ["<table cellpadding='4' cellspacing='0' "
                "style='border-collapse: collapse; margin: 8px 0;'>"
                "<tr style='background:#e0e0e0;'>"
                "<th>#</th>"]
        for c in _SEAT_CHARS:
            rows.append(f"<th>{c}</th>")
        rows.append("<th>Won by</th></tr>")
        for i, tr in enumerate(self.board.tricks, start=1):
            row = [f"<tr><td style='border: 1px solid #aaa; padding: 2px"
                   f" 10px; text-align: center;'>{i}</td>"]
            played = {0: '', 1: '', 2: '', 3: ''}
            try:
                leader = tr.leader
                for j, card in enumerate(tr.cards):
                    seat_idx = (leader.value + j) % 4
                    played[seat_idx] = self._format_card_html(card)
            except Exception:
                pass
            for seat_idx in range(4):
                row.append(
                    f"<td style='border: 1px solid #aaa; padding: 2px"
                    f" 10px; text-align: center;'>{played[seat_idx]}"
                    f"</td>"
                )
            winner = tr.winner.to_char() if tr.winner else '?'
            row.append(
                f"<td style='border: 1px solid #aaa; padding: 2px 10px;"
                f" text-align: center;'><b>{winner}</b></td></tr>"
            )
            rows.append("".join(row))
        rows.append("</table>")
        return "<div><b>Played tricks</b>" + "".join(rows) + "</div>"

    @staticmethod
    def _format_card_html(card) -> str:
        try:
            sv = card.suit.value
            rank = card.rank.to_char()
        except Exception:
            return str(card)
        if sv == 4:
            return rank
        sym = _SUIT_SYMBOLS.get(sv, '?')
        colour = _get_suit_color(_SUIT_NAMES.get(sv, 'spades'))
        return f"<span style='color:{colour}'>{sym}</span>{rank}"

    def _render_remark_html(self) -> str:
        # Escape minimal HTML special chars so a user-typed remark
        # can't break the printout layout.
        text = (self.remark or "").replace('&', '&amp;').replace(
            '<', '&lt;').replace('>', '&gt;')
        return (f"<p style='margin: 12px 0 4px 0;'><b>Remark</b></p>"
                f"<p style='white-space: pre-wrap;'>{text}</p>")
