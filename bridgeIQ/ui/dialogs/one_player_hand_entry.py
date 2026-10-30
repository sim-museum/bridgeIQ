"""One-Player Mode hand entry — Q-Plus `.one-player`.

When the user is in One-Player Mode the engine should know only
the named seat's hand. This dialog is the entry point that gives
the user a place to type that hand in for each deal.

Layout: a single text field per suit (♠ / ♥ / ♦ / ♣) accepting
the rank string (e.g. ``AKQ75``). Validates that:
  • each rank is one of AKQJT98765432
  • no rank repeats within a suit
  • the four suits sum to exactly 13 cards

Non-peeking is now ENFORCED structurally (no longer just a UI promise):
  • At deal start the runtime installs the user's typed hand at the named
    seat and clears the other three seats to EMPTY hands, so there is
    literally no hidden-hand data for the engine to read.
  • Every seat is flipped to a human-entered role; the engine is never
    asked to bid or play a seat whose hand it isn't allowed to know.
    Unknown seats are entered manually as they are revealed at the table
    (``_advance_bidding`` / ``_advance_play`` route them to the bid / card
    entry dialogs instead of the engine — see ``_one_player_seat_known``).
  • Only the user's own seat and (after it comes down) dummy are known to
    the engine; those are the only seats it may act for.
"""

from __future__ import annotations

from typing import Optional, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox,
)

from .dialog_style import apply_dialog_style


_RANK_ORDER = "AKQJT98765432"


class OnePlayerHandEntryDialog(QDialog):
    """Prompt the user to type in their hand suit by suit. Use
    ``get_hand()`` after exec() — returns a Hand or None on
    cancel."""

    def __init__(self, seat_char: str = "S",
                 initial: Optional[Dict[str, str]] = None,
                 parent=None):
        super().__init__(parent)
        self.seat_char = seat_char
        self.setWindowTitle(
            f"One Player Mode — enter {seat_char}'s hand")
        self.setMinimumWidth(520)
        apply_dialog_style(self)
        self._setup_ui(initial or {})

    def _setup_ui(self, initial: Dict[str, str]):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            f"<b>One Player Mode — {self.seat_char}</b><br>"
            "Type each suit's ranks in order (e.g. <tt>AKQ75</tt>). "
            "Use <tt>A K Q J T 9 8 7 6 5 4 3 2</tt> — note "
            "<tt>T</tt> for 10. The four lines must sum to 13 cards."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.fields: Dict[str, QLineEdit] = {}
        for suit_char, label in (('S', '♠ spades'),
                                  ('H', '♥ hearts'),
                                  ('D', '♦ diamonds'),
                                  ('C', '♣ clubs')):
            edit = QLineEdit()
            edit.setFont(QFont("Monospace", 13))
            edit.setText(initial.get(suit_char, ""))
            edit.textChanged.connect(self._refresh_status)
            self.fields[suit_char] = edit
            form.addRow(label + ":", edit)
        root.addLayout(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear all")
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

        self._refresh_status()

    # ------------------------------------------------------------------

    def _parse_one_suit(self, text: str) -> Optional[list]:
        """Return a list of valid rank chars, or None on a parse
        error. Whitespace and dots are ignored so the user can type
        ``A K Q 7 5`` or ``AKQ75`` interchangeably."""
        out = []
        seen = set()
        for ch in (text or "").upper().replace(' ', '').replace('.', ''):
            if ch == '-':
                # Hand-entry convention: '-' = void.
                if out:
                    return None
                return []
            if ch not in _RANK_ORDER:
                return None
            if ch in seen:
                return None
            seen.add(ch)
            out.append(ch)
        # Re-sort high → low so the user can type out of order.
        out.sort(key=_RANK_ORDER.index)
        return out

    def _refresh_status(self):
        total = 0
        bad_suits = []
        for suit_char, edit in self.fields.items():
            ranks = self._parse_one_suit(edit.text())
            if ranks is None:
                bad_suits.append(suit_char)
            else:
                total += len(ranks)
        if bad_suits:
            self.status.setText(
                f"<span style='color:#c00'>Invalid ranks in: "
                f"{', '.join(bad_suits)}</span>"
            )
            return
        if total < 13:
            self.status.setText(
                f"<span style='color:#a60'>"
                f"{total} cards — need {13 - total} more.</span>"
            )
        elif total > 13:
            self.status.setText(
                f"<span style='color:#c00'>"
                f"{total} cards — {total - 13} too many.</span>"
            )
        else:
            self.status.setText(
                "<span style='color:#080'>13 cards ✓</span>"
            )

    def _on_clear(self):
        for edit in self.fields.values():
            edit.clear()

    def _on_ok(self):
        suits_data: Dict[str, list] = {}
        for suit_char, edit in self.fields.items():
            ranks = self._parse_one_suit(edit.text())
            if ranks is None:
                QMessageBox.warning(
                    self, "Hand entry",
                    f"Invalid ranks in the {suit_char} field. "
                    f"Use A K Q J T 9 .. 2; use '-' for a void.")
                return
            suits_data[suit_char] = ranks
        total = sum(len(v) for v in suits_data.values())
        if total != 13:
            QMessageBox.warning(
                self, "Hand entry",
                f"Hand has {total} cards — must be exactly 13.")
            return
        self._parsed = suits_data
        self.accept()

    def get_hand(self):
        """Build a Hand object from the entered ranks. Returns None
        if the dialog wasn't accepted."""
        data = getattr(self, '_parsed', None)
        if not data:
            return None
        from backend.models import Hand, Card, Rank, Suit
        suit_map = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                    'D': Suit.DIAMONDS, 'C': Suit.CLUBS}
        rank_map = {
            'A': Rank.ACE, 'K': Rank.KING, 'Q': Rank.QUEEN,
            'J': Rank.JACK, 'T': Rank.TEN, '9': Rank.NINE,
            '8': Rank.EIGHT, '7': Rank.SEVEN, '6': Rank.SIX,
            '5': Rank.FIVE, '4': Rank.FOUR, '3': Rank.THREE,
            '2': Rank.TWO,
        }
        cards = []
        for sc, ranks in data.items():
            for r in ranks:
                cards.append(Card(suit_map[sc], rank_map[r]))
        return Hand(cards=cards)
