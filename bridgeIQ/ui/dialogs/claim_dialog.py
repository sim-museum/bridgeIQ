"""
Claim Dialog - allows player to claim remaining tricks.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QGroupBox, QRadioButton,
    QButtonGroup
)
from PyQt6.QtCore import Qt
from .dialog_style import apply_dialog_style


class ClaimDialog(QDialog):
    """Dialog for claiming remaining tricks."""

    def __init__(self, parent=None, remaining_tricks=13, declarer_tricks=0,
                 defense_tricks=0, is_declarer=True):
        super().__init__(parent)
        self.setWindowTitle("Claim or Concede")
        self.setMinimumWidth(350)
        apply_dialog_style(self)

        self.remaining_tricks = remaining_tricks
        self.declarer_tricks = declarer_tricks
        self.defense_tricks = defense_tricks
        self.is_declarer = is_declarer
        self.claimed_tricks = 0
        self.verify = False

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Current trick count
        info_layout = QHBoxLayout()
        info_layout.addWidget(QLabel(f"Declarer has won: {self.declarer_tricks} tricks"))
        info_layout.addStretch()
        info_layout.addWidget(QLabel(f"Defense has won: {self.defense_tricks} tricks"))
        layout.addLayout(info_layout)

        remaining_label = QLabel(f"<b>Remaining tricks: {self.remaining_tricks}</b>")
        remaining_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(remaining_label)

        # Claim input
        claim_group = QGroupBox("Claim")
        claim_layout = QVBoxLayout(claim_group)

        spin_layout = QHBoxLayout()
        if self.is_declarer:
            spin_layout.addWidget(QLabel("Declarer will make"))
        else:
            spin_layout.addWidget(QLabel("Defense will take"))

        self.tricks_spin = QSpinBox()
        self.tricks_spin.setRange(0, self.remaining_tricks)
        self.tricks_spin.setValue(self.remaining_tricks if self.is_declarer else 0)
        self.tricks_spin.setMinimumWidth(60)
        spin_layout.addWidget(self.tricks_spin)

        spin_layout.addWidget(QLabel("of the remaining tricks"))
        spin_layout.addStretch()
        claim_layout.addLayout(spin_layout)

        layout.addWidget(claim_group)

        # Action selection
        action_group = QGroupBox("Action")
        action_layout = QVBoxLayout(action_group)

        self.action_buttons = QButtonGroup(self)

        self.accept_radio = QRadioButton("Accept claim (computer agrees)")
        self.accept_radio.setChecked(True)
        self.action_buttons.addButton(self.accept_radio, 0)
        action_layout.addWidget(self.accept_radio)

        self.verify_radio = QRadioButton("Verify claim (computer checks)")
        self.action_buttons.addButton(self.verify_radio, 1)
        action_layout.addWidget(self.verify_radio)

        layout.addWidget(action_group)

        # Buttons
        button_layout = QHBoxLayout()

        self.ok_btn = QPushButton("Claim")
        self.ok_btn.clicked.connect(self._on_ok)
        self.ok_btn.setMinimumWidth(80)
        button_layout.addWidget(self.ok_btn)

        # Concede the rest in one click: the asking side takes none of the
        # remaining tricks (Q-Plus pairs Claim with Concede).
        self.concede_btn = QPushButton("Concede Rest")
        self.concede_btn.setToolTip(
            "Give up all remaining tricks to the other side.")
        self.concede_btn.clicked.connect(self._on_concede)
        self.concede_btn.setMinimumWidth(90)
        button_layout.addWidget(self.concede_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setMinimumWidth(80)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumWidth(80)
        button_layout.addWidget(self.help_btn)

        layout.addLayout(button_layout)

    def _on_ok(self):
        self.claimed_tricks = self.tricks_spin.value()
        self.verify = self.verify_radio.isChecked()
        self.accept()

    def _on_concede(self):
        """Concede the rest — the asking side takes 0 of the remaining tricks
        (declarer concedes all to the defence, or vice-versa). Always accepted,
        never 'verified' (you can't be wrong about conceding)."""
        self.claimed_tricks = 0
        self.verify = False
        self.accept()

    def get_claimed_tricks(self):
        """Return the number of tricks claimed for declarer."""
        if self.is_declarer:
            return self.claimed_tricks
        else:
            # If defense is claiming, return remaining minus what they claim
            return self.remaining_tricks - self.claimed_tricks
