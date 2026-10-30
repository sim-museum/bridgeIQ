"""
Evaluate dialog - asks which player to query about which hand.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QRadioButton, QButtonGroup, QGroupBox, QFrame, QCheckBox
)
from PyQt6.QtCore import Qt
from .dialog_style import apply_dialog_style


class EvaluateDialog(QDialog):
    """Dialog to select who to ask and about whom to evaluate."""

    def __init__(self, parent=None, current_seat=None, my_seat=None):
        super().__init__(parent)
        self.setWindowTitle("Evaluate")
        self.setMinimumWidth(550)
        self.setMinimumHeight(350)
        self.resize(600, 400)
        apply_dialog_style(self)
        self.setStyleSheet(self.styleSheet() + """
            QLabel { font-size: 15px; }
            QPushButton { font-size: 14px; padding: 8px 14px; }
            QRadioButton { font-size: 14px; }
        """)

        self.current_seat = current_seat
        # Default question: me about my partner (Q-Plus asks "who" about
        # "whom"; any of the 16 pairs, one at a time).
        self.my_seat = my_seat
        self.ask_who = None
        self.about_whom = None
        self.own_hand = False
        # Q-Plus result-type options (card play).
        self.want_conventional = False
        self.want_tricks_hidden = False
        self.want_tricks_open = False

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Create the grid for seat selection
        grid_frame = QFrame()
        grid_frame.setStyleSheet("""
            QFrame {
                background-color: #e8e8e8;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
            }
        """)
        grid_layout = QGridLayout(grid_frame)
        grid_layout.setSpacing(8)
        grid_layout.setContentsMargins(10, 10, 10, 10)

        # Headers
        seats = ['North', 'East', 'South', 'West']
        grid_layout.addWidget(QLabel("ask who:"), 0, 0)

        for col, seat in enumerate(seats):
            lbl = QLabel(f"<b>{seat}</b>")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid_layout.addWidget(lbl, 0, col + 1)

        # "about:" row with radio buttons
        grid_layout.addWidget(QLabel("about:"), 1, 0)

        # ONE exclusive group over all 16 buttons: a single question at a
        # time (per-column groups let a button stay on in every column).
        self.choice_group = QButtonGroup(self)
        self.choice_group.setExclusive(True)
        mine = self.my_seat.name.capitalize() if self.my_seat is not None else None
        partner = (self.my_seat.partner().name.capitalize()
                   if self.my_seat is not None else None)

        for col, seat in enumerate(seats):
            vbox = QVBoxLayout()
            for row_seat in seats:
                radio = QRadioButton(row_seat)
                radio.setProperty("ask_seat", seat)
                radio.setProperty("about_seat", row_seat)
                self.choice_group.addButton(radio)
                vbox.addWidget(radio)
                if seat == mine and row_seat == partner:
                    radio.setChecked(True)

            container = QWidget()
            container.setLayout(vbox)
            grid_layout.addWidget(container, 1, col + 1)

        layout.addWidget(grid_frame)

        # Q-Plus result-type options (used during card play). Leaving all
        # unchecked falls back to biq's hand point-count evaluation.
        result_group = QGroupBox("During play, show (instead of point count)")
        result_layout = QVBoxLayout(result_group)
        self.chk_conventional = QCheckBox("Conventional meaning of cards")
        self.chk_tricks_hidden = QCheckBox(
            "Expected tricks (hidden hands — legal knowledge)")
        self.chk_tricks_open = QCheckBox(
            "Expected tricks (open hands — double dummy)")
        for c in (self.chk_conventional, self.chk_tricks_hidden,
                  self.chk_tricks_open):
            result_layout.addWidget(c)
        layout.addWidget(result_group)

        # Buttons
        button_layout = QHBoxLayout()

        self.ok_btn = QPushButton("Ok")
        self.ok_btn.clicked.connect(self._on_ok)
        self.ok_btn.setMinimumWidth(80)
        button_layout.addWidget(self.ok_btn)

        self.own_hand_btn = QPushButton("Own Hand")
        self.own_hand_btn.clicked.connect(self._on_own_hand)
        self.own_hand_btn.setMinimumWidth(80)
        button_layout.addWidget(self.own_hand_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setMinimumWidth(80)
        button_layout.addWidget(self.cancel_btn)

        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumWidth(80)
        button_layout.addWidget(self.help_btn)

        layout.addLayout(button_layout)

    def _get_selected(self):
        """The chosen question as Seats (ask_who, about_whom)."""
        from backend.models import Seat
        checked = self.choice_group.checkedButton()
        if checked is None:
            return False
        self.ask_who = Seat[checked.property("ask_seat").upper()]
        self.about_whom = Seat[checked.property("about_seat").upper()]
        return True

    def _capture_result_modes(self):
        self.want_conventional = self.chk_conventional.isChecked()
        self.want_tricks_hidden = self.chk_tricks_hidden.isChecked()
        self.want_tricks_open = self.chk_tricks_open.isChecked()

    def _any_result_mode(self):
        return (self.chk_conventional.isChecked()
                or self.chk_tricks_hidden.isChecked()
                or self.chk_tricks_open.isChecked())

    def _on_ok(self):
        self._capture_result_modes()
        # A result-mode checkbox alone (no seat picked) is a valid request.
        if self._get_selected() or self._any_result_mode():
            self.own_hand = False
            self.accept()

    def _on_own_hand(self):
        self._capture_result_modes()
        self.own_hand = True
        self.accept()


# Import QWidget for container
from PyQt6.QtWidgets import QWidget
