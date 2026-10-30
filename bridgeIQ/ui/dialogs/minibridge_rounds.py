"""MiniBridge bidding box — Q-Plus `.bidbox-m-p` and `.bidbox-m-s`.

Two simple dialogs that replace the standard auction when the user
has selected MiniBridge mode:

  • MiniBridgePointsDialog  — round 1: announce points.
    Click a number, then OK or click again to confirm.

  • MiniBridgeContractDialog — round 2: pick the contract.
    Click a contract row, then OK or click again to confirm.

Both expose a **Hint** button that fills in the program's
suggestion (HP from the actual hand for round 1; declarer's
suggested contract for round 2). Q-Plus's UX has the same
two-step confirmation; we keep it because the muscle memory is
already there for Q-Plus users moving over.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QButtonGroup,
)

from .dialog_style import apply_dialog_style


class MiniBridgePointsDialog(QDialog):
    """Round 1 of MiniBridge bidding — partner-side total HP.

    Spec excerpt (.bidbox-m-p):
        In the first round of MiniBridge bidding you announce
        your points. To do so, you click on a number and then
        either on Ok or a second time on the same number.

    The returned value is the side's combined HP estimate (your
    HP + your partner's announced HP). For the program's seats
    we just use the actual HCP of the hand; humans use the
    dialog to enter their own.
    """

    def __init__(self, seat_char: str = "S",
                 hint_hp: Optional[int] = None,
                 parent=None):
        super().__init__(parent)
        self.seat_char = seat_char
        self.hint_hp = hint_hp
        self._selected: Optional[int] = None
        self._committed: Optional[int] = None

        self.setWindowTitle(
            f"MiniBridge round 1 — {seat_char} announces points")
        self.setMinimumWidth(520)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            f"<b>Round 1:</b> {self.seat_char} — click your high-card "
            "points (0–40), then OK or click the same number again."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        root.addWidget(intro)

        # 7 × 6 grid of 0..40 (one extra slot for the missing 41).
        grid = QGridLayout()
        grid.setSpacing(4)
        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        for n in range(0, 41):
            btn = QPushButton(str(n))
            btn.setCheckable(True)
            btn.setMinimumSize(54, 34)
            btn.setFont(QFont("Arial", 12, QFont.Weight.Bold))
            self._buttons.addButton(btn, n)
            r, c = divmod(n, 7)
            grid.addWidget(btn, r, c)
            btn.clicked.connect(self._on_click)
        root.addLayout(grid)

        # Status / current pick.
        self.status = QLabel("")
        self.status.setFont(QFont("Arial", 11))
        root.addWidget(self.status)

        # Button row — Hint, OK, Cancel.
        btn_row = QHBoxLayout()
        hint_btn = QPushButton("Hint")
        hint_btn.setToolTip("Fill in the program's suggestion.")
        hint_btn.clicked.connect(self._on_hint)
        btn_row.addWidget(hint_btn)
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
        n = self._buttons.id(btn)
        # Q-Plus two-step confirmation: a SECOND click on the same
        # number commits, just like clicking OK after a single click.
        if self._committed is not None and self._selected == n:
            self.accept()
            return
        self._selected = n
        self._committed = n
        self.status.setText(
            f"Selected <b>{n}</b> HP — click OK or click "
            f"<b>{n}</b> again to confirm."
        )

    def _on_hint(self):
        if self.hint_hp is None:
            self.status.setText(
                "(no hint available — the hand isn't visible)")
            return
        # Visually pick the hint value's button.
        btn = self._buttons.button(int(self.hint_hp))
        if btn is not None:
            btn.setChecked(True)
            self._selected = int(self.hint_hp)
            self._committed = int(self.hint_hp)
            self.status.setText(
                f"Hint: <b>{self.hint_hp}</b> HP — click OK to confirm."
            )

    def _on_ok(self):
        if self._selected is None:
            self.status.setText(
                "(click a number first)")
            return
        self.accept()

    def get_points(self) -> Optional[int]:
        return self._selected


# ---------------------------------------------------------------------------
# Round 2 — contract pick.
# ---------------------------------------------------------------------------


_SUITS_ORDER = [('C', '♣'), ('D', '♦'),
                ('H', '♥'), ('S', '♠'),
                ('NT', 'NT')]


class MiniBridgeContractDialog(QDialog):
    """Round 2 — pick the final contract.

    Spec excerpt (.bidbox-m-s):
        In the second round of MiniBridge bidding you specify
        the contract. To do so, you click on a contract and
        then either on Ok or a second time on the same contract.

    The returned tuple is ``(level, suit_char, doubled,
    redoubled)``. Doubled / redoubled don't really make sense in
    MiniBridge (no opponents bidding) but they're surfaced for
    completeness so a teacher can drill them.
    """

    def __init__(self, hint_level: Optional[int] = None,
                 hint_suit: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self.hint_level = hint_level
        self.hint_suit = hint_suit
        self._selected: Optional[Tuple[int, str]] = None
        self._committed: Optional[Tuple[int, str]] = None
        self._doubled = False
        self._redoubled = False

        self.setWindowTitle("MiniBridge round 2 — pick the contract")
        self.setMinimumWidth(560)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "<b>Round 2:</b> click a contract, then OK or click "
            "the same contract again to confirm."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        root.addWidget(intro)

        # 7 levels × 5 strains grid.
        grid = QGridLayout()
        grid.setSpacing(4)
        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        # Header row.
        for col, (_, sym) in enumerate(_SUITS_ORDER):
            lbl = QLabel(f"<b>{sym}</b>")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, 0, col + 1)
        for level in range(1, 8):
            lbl = QLabel(f"<b>{level}</b>")
            grid.addWidget(lbl, level, 0)
            for col, (suit_char, sym) in enumerate(_SUITS_ORDER):
                btn = QPushButton(f"{level}{sym}")
                btn.setCheckable(True)
                btn.setMinimumSize(64, 36)
                btn.setFont(QFont("Arial", 12, QFont.Weight.Bold))
                # Pack the (level, suit_char) into the button so
                # _on_click can read it back.
                btn.setProperty('level', level)
                btn.setProperty('suit', suit_char)
                btn.clicked.connect(self._on_click)
                self._buttons.addButton(btn)
                grid.addWidget(btn, level, col + 1)
        root.addLayout(grid)

        # Doubled / redoubled (rarely useful in MiniBridge but the
        # spec says "specify the contract" — that includes X/XX for
        # completeness).
        flag_row = QHBoxLayout()
        from PyQt6.QtWidgets import QCheckBox
        self.doubled_check = QCheckBox("doubled")
        self.redoubled_check = QCheckBox("redoubled")
        flag_row.addWidget(self.doubled_check)
        flag_row.addWidget(self.redoubled_check)
        flag_row.addStretch()
        root.addLayout(flag_row)

        # Status.
        self.status = QLabel("")
        self.status.setFont(QFont("Arial", 11))
        root.addWidget(self.status)

        # Buttons.
        btn_row = QHBoxLayout()
        hint_btn = QPushButton("Hint")
        hint_btn.clicked.connect(self._on_hint)
        btn_row.addWidget(hint_btn)
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
        key = (btn.property('level'), btn.property('suit'))
        if self._committed is not None and self._selected == key:
            self.accept()
            return
        self._selected = key
        self._committed = key
        level, suit = key
        self.status.setText(
            f"Selected <b>{level}{suit}</b> — click OK or click "
            f"<b>{level}{suit}</b> again to confirm."
        )

    def _on_hint(self):
        if self.hint_level is None or self.hint_suit is None:
            self.status.setText(
                "(no hint available)")
            return
        for btn in self._buttons.buttons():
            if (btn.property('level') == self.hint_level
                    and btn.property('suit') == self.hint_suit):
                btn.setChecked(True)
                self._selected = (self.hint_level, self.hint_suit)
                self._committed = self._selected
                self.status.setText(
                    f"Hint: <b>{self.hint_level}{self.hint_suit}</b> "
                    "— click OK to confirm.")
                return

    def _on_ok(self):
        if self._selected is None:
            self.status.setText("(pick a contract first)")
            return
        self._doubled = self.doubled_check.isChecked()
        self._redoubled = self.redoubled_check.isChecked()
        self.accept()

    def get_contract(self) -> Optional[Tuple[int, str, bool, bool]]:
        if self._selected is None:
            return None
        level, suit = self._selected
        return (int(level), str(suit),
                bool(self._doubled), bool(self._redoubled))
