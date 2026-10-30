"""One-Player Mode per-trick card entry — Q-Plus `.one-player`.

When the user is in One-Player Mode the engine knows only the named
seat's hand (and dummy's, after the opening lead). For every card the
OTHER seats play we ask the user which card it actually was — the
program never invents one and never peeks at a hand it wasn't given.

This dialog handles a single card pick. Layout is a 4-row × 13-column
grid (suits ♠ ♥ ♦ ♣ × ranks A K Q J T 9 .. 2). Played cards are
greyed out so the user can't accidentally re-enter one.
"""

from __future__ import annotations

from typing import Optional, Iterable, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QButtonGroup,
)

from .dialog_style import apply_dialog_style


_RANK_ORDER = "AKQJT98765432"
_SUITS = (('S', '♠'), ('H', '♥'), ('D', '♦'), ('C', '♣'))


class OnePlayerCardEntryDialog(QDialog):
    """Ask the user which card a given seat just played.

    ``played`` is an iterable of ``(suit_char, rank_char)`` tuples for
    cards already played in this deal. They render disabled so the
    user can't pick them again. ``lead_suit`` is informational only —
    we don't enforce follow-suit at this layer (Q-Plus lets you
    record actual play including revokes).
    """

    def __init__(self, seat_char: str,
                 played: Optional[Iterable[Tuple[str, str]]] = None,
                 lead_suit: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self.seat_char = seat_char
        self._played = set(played or ())
        self.lead_suit = lead_suit
        self._selected: Optional[Tuple[str, str]] = None

        self.setWindowTitle(
            f"One Player Mode — enter {seat_char}'s card")
        self.setMinimumWidth(620)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        lead = (f" (led: {self.lead_suit})"
                if self.lead_suit else "")
        intro = QLabel(
            f"<b>{self.seat_char} to play{lead}.</b> "
            "Click the card that was actually played.")
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        root.addWidget(intro)

        grid = QGridLayout()
        grid.setSpacing(3)
        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        for col, rank in enumerate(_RANK_ORDER):
            lbl = QLabel(f"<b>{rank}</b>")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, 0, col + 1)
        for row, (suit_char, sym) in enumerate(_SUITS, start=1):
            lbl = QLabel(f"<b>{sym}</b>")
            grid.addWidget(lbl, row, 0)
            for col, rank in enumerate(_RANK_ORDER):
                btn = QPushButton(f"{sym}{rank}")
                btn.setCheckable(True)
                btn.setMinimumSize(40, 28)
                btn.setFont(QFont("Arial", 10))
                btn.setProperty('suit', suit_char)
                btn.setProperty('rank', rank)
                btn.clicked.connect(self._on_click)
                if (suit_char, rank) in self._played:
                    btn.setEnabled(False)
                    btn.setStyleSheet("color: #888;")
                self._buttons.addButton(btn)
                grid.addWidget(btn, row, col + 1)
        root.addLayout(grid)

        self.status = QLabel("")
        root.addWidget(self.status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    def _on_click(self):
        btn = self.sender()
        suit = btn.property('suit')
        rank = btn.property('rank')
        # Second click on the same card commits — Q-Plus muscle memory.
        if self._selected == (suit, rank):
            self.accept()
            return
        self._selected = (suit, rank)
        sym = dict(_SUITS).get(suit, suit)
        self.status.setText(
            f"Selected <b>{sym}{rank}</b> — click OK or click "
            f"<b>{sym}{rank}</b> again to confirm.")

    def _on_ok(self):
        if self._selected is None:
            self.status.setText("(pick a card first)")
            return
        self.accept()

    def get_card(self):
        """Build a Card from the chosen suit/rank, or None on cancel."""
        if self._selected is None:
            return None
        from backend.models import Card, Rank, Suit
        suit_map = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                    'D': Suit.DIAMONDS, 'C': Suit.CLUBS}
        rank_map = {
            'A': Rank.ACE, 'K': Rank.KING, 'Q': Rank.QUEEN,
            'J': Rank.JACK, 'T': Rank.TEN, '9': Rank.NINE,
            '8': Rank.EIGHT, '7': Rank.SEVEN, '6': Rank.SIX,
            '5': Rank.FIVE, '4': Rank.FOUR, '3': Rank.THREE,
            '2': Rank.TWO,
        }
        suit, rank = self._selected
        return Card(suit_map[suit], rank_map[rank])
