"""Repeat Deal dialog — Q-Plus `.repeat-f` / `.repeat-w`.

Lets the user replay the just-finished deal under different
parameters: start from the auction or skip straight to play with the
same contract, let the user re-bid by hand or let the computer drive
it, and decide whether the repeat result lands as a NEW scoring-table
row or replaces an existing one.

The dialog only collects choices — the caller is responsible for
acting on them (resetting board state, kicking the auction, applying
the scoring-table option).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QRadioButton,
    QCheckBox, QPushButton, QButtonGroup,
)

from .dialog_style import apply_dialog_style


@dataclass
class RepeatChoice:
    start_with_play: bool          # True = skip auction, False = re-bid
    by_user: bool                  # True = user drives; else computer
    computer_stepwise: bool        # only meaningful when by_user is False
    entry_mode: str                # 'new' / 'replace1' / 'replace2'


class RepeatDealDialog(QDialog):
    """Q-Plus-style Repeat Deal modal. Use :py:meth:`get_choice`
    after a successful exec to read the picks back."""

    def __init__(self, parent=None,
                 have_first_row: bool = True,
                 have_second_row: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Repeat Deal")
        apply_dialog_style(self)
        self._setup_ui(have_first_row, have_second_row)

    def _setup_ui(self, have_first_row: bool, have_second_row: bool):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Start radios — auction vs play.
        start_box = QGroupBox("Start with")
        start_layout = QVBoxLayout(start_box)
        self.start_auction_radio = QRadioButton("Start with auction")
        self.start_auction_radio.setChecked(True)
        self.start_play_radio = QRadioButton(
            "Start with play (same auction)")
        start_grp = QButtonGroup(self)
        start_grp.addButton(self.start_auction_radio)
        start_grp.addButton(self.start_play_radio)
        start_layout.addWidget(self.start_auction_radio)
        start_layout.addWidget(self.start_play_radio)
        root.addWidget(start_box)

        # Repeat-by radios.
        repeat_box = QGroupBox("Repeat")
        repeat_layout = QVBoxLayout(repeat_box)
        self.by_user_radio = QRadioButton("by user")
        self.by_user_radio.setChecked(True)
        self.by_step_radio = QRadioButton("by computer — stepwise")
        self.by_auto_radio = QRadioButton("by computer — automatic")
        repeat_grp = QButtonGroup(self)
        repeat_grp.addButton(self.by_user_radio)
        repeat_grp.addButton(self.by_step_radio)
        repeat_grp.addButton(self.by_auto_radio)
        repeat_layout.addWidget(self.by_user_radio)
        repeat_layout.addWidget(self.by_step_radio)
        repeat_layout.addWidget(self.by_auto_radio)
        root.addWidget(repeat_box)

        # Scoring-table behaviour.
        self.entry_new_cb = QCheckBox(
            "Repeated deal is entered in scoring table")
        self.entry_new_cb.setChecked(True)
        root.addWidget(self.entry_new_cb)
        self.entry_replace1_cb = QCheckBox(
            "Repeated deal replaces 1. played deal in scoring table")
        self.entry_replace1_cb.setEnabled(have_first_row)
        root.addWidget(self.entry_replace1_cb)
        self.entry_replace2_cb = QCheckBox(
            "Repeated deal replaces 2. played deal in scoring table")
        self.entry_replace2_cb.setEnabled(have_second_row)
        root.addWidget(self.entry_replace2_cb)

        # The three checkboxes are mutually exclusive — wire them so
        # ticking one un-ticks the others. (Plain checkboxes rather
        # than radios because Q-Plus uses checkbox glyphs in the
        # original dialog and we keep the cosmetics.)
        def _enforce_exclusive(_state):
            sender = self.sender()
            if not sender.isChecked():
                # Don't let the user untick all three — re-tick the
                # default ("new").
                if (not self.entry_new_cb.isChecked()
                        and not self.entry_replace1_cb.isChecked()
                        and not self.entry_replace2_cb.isChecked()):
                    self.entry_new_cb.setChecked(True)
                return
            for cb in (self.entry_new_cb,
                       self.entry_replace1_cb,
                       self.entry_replace2_cb):
                if cb is not sender:
                    cb.setChecked(False)
        for cb in (self.entry_new_cb,
                   self.entry_replace1_cb,
                   self.entry_replace2_cb):
            cb.toggled.connect(_enforce_exclusive)

        # Buttons.
        btns = QHBoxLayout()
        ok_btn = QPushButton("Ok")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btns.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        btns.addStretch()
        root.addLayout(btns)

    def get_choice(self) -> RepeatChoice:
        if self.entry_replace1_cb.isChecked():
            mode = 'replace1'
        elif self.entry_replace2_cb.isChecked():
            mode = 'replace2'
        else:
            mode = 'new'
        return RepeatChoice(
            start_with_play=self.start_play_radio.isChecked(),
            by_user=self.by_user_radio.isChecked(),
            computer_stepwise=self.by_step_radio.isChecked(),
            entry_mode=mode,
        )
