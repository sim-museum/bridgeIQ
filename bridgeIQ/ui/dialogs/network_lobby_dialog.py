"""
NetworkLobbyDialog — host's persistent lobby view.

After the host clicks "Start Server", this dialog opens and stays open
while guests connect, showing live seat occupancy. The host explicitly
clicks "Start Game" to begin (or the dialog auto-accepts once all four
seats are filled, mirroring pokerIQ's lobby behaviour).
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from backend.models import Seat
from .dialog_style import apply_dialog_style
from .seat_board_widget import SeatBoardWidget


class NetworkLobbyDialog(QDialog):
    """Host-side dialog that persists until the game is started or cancelled."""

    # Emitted when the host clicks Start Game (instead of finalize via accept).
    start_game_requested = pyqtSignal()

    def __init__(self, host_name: str, host_seat: Seat, port: int,
                 ip_addresses: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Network Game Lobby")
        self.setMinimumWidth(560)
        apply_dialog_style(self)

        self._host_seat = host_seat
        self._host_name = host_name
        self._port = port
        self._setup_ui(ip_addresses or [])
        # Initialize with the host already seated; everything else empty.
        initial = {s.to_char(): None for s in Seat}
        initial[host_seat.to_char()] = host_name
        self.update_seat_map(initial)

    def _setup_ui(self, ip_addresses: list):
        layout = QVBoxLayout(self)

        info_group = QGroupBox("Connection Info")
        info_layout = QVBoxLayout(info_group)
        info_layout.addWidget(QLabel(
            f"Hosting on port <b>{self._port}</b>. Share an IP address with guests:"
        ))
        if ip_addresses:
            for ip in ip_addresses:
                lbl = QLabel(f"  {ip}:{self._port}")
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                info_layout.addWidget(lbl)
        else:
            info_layout.addWidget(QLabel("  (No network interfaces found)"))
        layout.addWidget(info_group)

        self._board = SeatBoardWidget(allow_click=False)
        layout.addWidget(self._board)

        self._status_label = QLabel("Waiting for guests to join…")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self._start_btn = QPushButton("Start Game")
        self._start_btn.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self._start_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_btn)
        layout.addLayout(button_layout)

    def update_seat_map(self, seats: dict):
        """Refresh the seat board with the latest occupancy. Auto-accepts
        once all four seats are filled, matching the user's spec."""
        self._board.set_seats(
            seats,
            host_seat=self._host_seat.to_char(),
            my_seat=self._host_seat,
        )
        filled = sum(1 for v in seats.values() if v)
        if filled >= 4:
            self._status_label.setText("All seats filled — starting game…")
            # Auto-start when the lobby is full.
            self._on_start_clicked()
        else:
            self._status_label.setText(
                f"{filled}/4 seats filled. Click 'Start Game' to begin "
                "with the current line-up."
            )

    def _on_start_clicked(self):
        self.start_game_requested.emit()
        self.accept()
