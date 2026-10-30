"""
Player Configuration Dialog
Configure human/computer players and visibility settings.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QComboBox, QCheckBox, QPushButton, QRadioButton,
    QButtonGroup
)
from PyQt6.QtCore import Qt

from backend.models import Seat, PlayerType, Player
from typing import Dict

from .dialog_style import apply_dialog_style


class PlayerConfigDialog(QDialog):
    """
    Dialog for configuring players at each seat.
    Similar to BridgeIQ player configuration.
    """

    def __init__(self, players: Dict[Seat, Player], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Player Configuration")
        self.setMinimumWidth(400)
        apply_dialog_style(self)

        self.players = {seat: Player(seat=seat,
                                     player_type=p.player_type,
                                     name=p.name,
                                     visible=p.visible)
                       for seat, p in players.items()}

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Mode selection
        mode_group = QGroupBox("Game Mode")
        mode_layout = QVBoxLayout(mode_group)

        self.mode_buttons = QButtonGroup(self)

        self.four_player_radio = QRadioButton("4 Players (Human South)")
        self.four_player_radio.setChecked(True)
        self.mode_buttons.addButton(self.four_player_radio, 0)
        mode_layout.addWidget(self.four_player_radio)

        self.one_player_radio = QRadioButton("1 Player (Human South, hidden hands)")
        self.mode_buttons.addButton(self.one_player_radio, 1)
        mode_layout.addWidget(self.one_player_radio)

        self.all_computer_radio = QRadioButton("All Computer (Auto-play)")
        self.mode_buttons.addButton(self.all_computer_radio, 2)
        mode_layout.addWidget(self.all_computer_radio)

        layout.addWidget(mode_group)

        # Individual player settings
        players_group = QGroupBox("Player Settings")
        players_layout = QGridLayout(players_group)

        headers = ["Seat", "Type", "Name", "Visible"]
        for col, header in enumerate(headers):
            lbl = QLabel(header)
            lbl.setStyleSheet("font-weight: bold;")
            players_layout.addWidget(lbl, 0, col)

        self.player_widgets = {}

        for row, seat in enumerate([Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST], 1):
            player = self.players[seat]

            # Seat label
            seat_lbl = QLabel(f"{seat.to_char()} ({['North', 'East', 'South', 'West'][seat]})")
            players_layout.addWidget(seat_lbl, row, 0)

            # Type combo
            type_combo = QComboBox()
            type_combo.addItems(["Human", "Computer", "External"])
            type_combo.setCurrentIndex(
                {"human": 0, "computer": 1, "external": 2}[player.player_type.value]
            )
            type_combo.currentIndexChanged.connect(
                lambda idx, s=seat: self._on_type_changed(s, idx)
            )
            players_layout.addWidget(type_combo, row, 1)

            # Name (not editable for now)
            name_lbl = QLabel(player.name)
            players_layout.addWidget(name_lbl, row, 2)

            # Visible checkbox
            visible_check = QCheckBox()
            visible_check.setChecked(player.visible or player.player_type == PlayerType.HUMAN)
            visible_check.toggled.connect(
                lambda checked, s=seat: self._on_visible_changed(s, checked)
            )
            players_layout.addWidget(visible_check, row, 3)

            self.player_widgets[seat] = {
                'type': type_combo,
                'name': name_lbl,
                'visible': visible_check
            }

        layout.addWidget(players_group)

        # Connect mode changes
        self.mode_buttons.idToggled.connect(self._on_mode_changed)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _on_mode_changed(self, id: int, checked: bool):
        if not checked:
            return

        if id == 0:  # 4 Players
            for seat in Seat:
                widgets = self.player_widgets[seat]
                if seat == Seat.SOUTH:
                    widgets['type'].setCurrentIndex(0)  # Human
                    widgets['visible'].setChecked(True)
                else:
                    widgets['type'].setCurrentIndex(1)  # Computer
                    widgets['visible'].setChecked(True)

        elif id == 1:  # 1 Player
            for seat in Seat:
                widgets = self.player_widgets[seat]
                if seat == Seat.SOUTH:
                    widgets['type'].setCurrentIndex(0)  # Human
                    widgets['visible'].setChecked(True)
                else:
                    widgets['type'].setCurrentIndex(1)  # Computer
                    widgets['visible'].setChecked(False)

        elif id == 2:  # All Computer
            for seat in Seat:
                widgets = self.player_widgets[seat]
                widgets['type'].setCurrentIndex(1)  # Computer
                widgets['visible'].setChecked(True)

    def _on_type_changed(self, seat: Seat, idx: int):
        player_type = [PlayerType.HUMAN, PlayerType.COMPUTER, PlayerType.EXTERNAL][idx]
        self.players[seat].player_type = player_type

        # Auto-set visibility for human
        if player_type == PlayerType.HUMAN:
            self.player_widgets[seat]['visible'].setChecked(True)

    def _on_visible_changed(self, seat: Seat, checked: bool):
        self.players[seat].visible = checked

    def get_players(self) -> Dict[Seat, Player]:
        """Return the configured players"""
        for seat in Seat:
            widgets = self.player_widgets[seat]
            idx = widgets['type'].currentIndex()
            self.players[seat].player_type = [
                PlayerType.HUMAN, PlayerType.COMPUTER, PlayerType.EXTERNAL
            ][idx]
            self.players[seat].visible = widgets['visible'].isChecked()

        return self.players
