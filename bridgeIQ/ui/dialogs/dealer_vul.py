"""Dealer / Vulnerability + Board Number pickers — Q-Plus
`.set-dealer` and `.seednum-d/-f`.

Two small modals that let the user override the dealer, the
vulnerability, or the board number of the current deal without
re-typing the 52 cards. They mutate the controller's BoardState in
place — the auction has to be reset because changing the dealer
mid-auction would otherwise leave bids misattributed.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QRadioButton,
    QPushButton, QLabel, QSpinBox, QButtonGroup,
)

from .dialog_style import apply_dialog_style
from backend.models import Seat, Vulnerability


_SEAT_LABELS = [(Seat.NORTH, "North"), (Seat.EAST, "East"),
                (Seat.SOUTH, "South"), (Seat.WEST, "West")]
_VUL_LABELS = [(Vulnerability.NONE, "None"),
               (Vulnerability.NS, "N-S"),
               (Vulnerability.EW, "E-W"),
               (Vulnerability.BOTH, "Both")]


class DealerVulnerabilityDialog(QDialog):
    """Pick a new dealer + vulnerability for the current board.

    ``initial_dealer`` / ``initial_vul`` pre-select the current
    values. Returned via :py:meth:`get_choice` after an accepted
    exec.
    """

    def __init__(self, initial_dealer: Seat = Seat.NORTH,
                 initial_vul: Vulnerability = Vulnerability.NONE,
                 parent=None):
        super().__init__(parent)
        self._dealer = initial_dealer
        self._vul = initial_vul
        self.setWindowTitle("Dealer and Vulnerability")
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        intro = QLabel(
            "Change the dealer and vulnerability for the current "
            "deal. The auction is reset.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        seats_box = QGroupBox("Dealer")
        seats_layout = QHBoxLayout(seats_box)
        self._seat_group = QButtonGroup(self)
        for seat, label in _SEAT_LABELS:
            rb = QRadioButton(label)
            if seat == self._dealer:
                rb.setChecked(True)
            rb.toggled.connect(
                lambda checked, s=seat:
                    self._dealer_picked(s) if checked else None)
            self._seat_group.addButton(rb)
            seats_layout.addWidget(rb)
        root.addWidget(seats_box)

        vul_box = QGroupBox("Vulnerability")
        vul_layout = QHBoxLayout(vul_box)
        self._vul_group = QButtonGroup(self)
        for vul, label in _VUL_LABELS:
            rb = QRadioButton(label)
            if vul == self._vul:
                rb.setChecked(True)
            rb.toggled.connect(
                lambda checked, v=vul:
                    self._vul_picked(v) if checked else None)
            self._vul_group.addButton(rb)
            vul_layout.addWidget(rb)
        root.addWidget(vul_box)

        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btns.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        root.addLayout(btns)

    def _dealer_picked(self, seat: Seat):
        self._dealer = seat

    def _vul_picked(self, vul: Vulnerability):
        self._vul = vul

    def get_choice(self) -> Tuple[Seat, Vulnerability]:
        return self._dealer, self._vul


class BoardNumberDialog(QDialog):
    """Pick a new board number. Q-Plus uses the board number to
    derive both dealer and vulnerability per the standard 16-board
    rotation (the same table BoardState._board_dealer_vuln uses);
    we honour that by default but the user can also disable the
    auto-derivation if they just want to relabel the board.
    """

    def __init__(self, initial: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Board Number")
        apply_dialog_style(self)
        self._setup_ui(initial)

    def _setup_ui(self, initial: int):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        intro = QLabel(
            "Board number determines the default dealer and "
            "vulnerability under the standard 16-board rotation. "
            "Pick a number to relabel and re-rotate the current "
            "deal.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Board number:"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 9999)
        self.spin.setValue(max(1, int(initial)))
        row.addWidget(self.spin)
        row.addStretch()
        root.addLayout(row)

        self.rotate_check = QPushButton(
            "Apply standard dealer/vul rotation")
        self.rotate_check.setCheckable(True)
        self.rotate_check.setChecked(True)
        root.addWidget(self.rotate_check)

        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btns.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        root.addLayout(btns)

    def get_board_number(self) -> int:
        return int(self.spin.value())

    def apply_rotation(self) -> bool:
        return bool(self.rotate_check.isChecked())
