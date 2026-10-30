"""
Connect to Server Dialog for LAN play — pokerIQ-style two-phase flow:
1. Connection form (host/port/name only).
2. Lobby with the live 4-seat board; the guest clicks an empty seat to
   claim it. Stays open until host broadcasts GAME_START.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QStackedWidget,
    QLabel, QLineEdit, QSpinBox, QPushButton, QProgressBar, QWidget, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from network import serious_games_week

from backend.models import Seat
from .dialog_style import apply_dialog_style
from .seat_board_widget import SeatBoardWidget


class ConnectServerDialog(QDialog):
    """Dialog that walks a guest through connecting and seat selection."""

    # Phase 1 → main_window: open the socket. (host, port, name)
    # The dialog no longer carries a pre-picked seat — that's claimed in
    # the lobby phase via seat_clicked.
    connect_requested = pyqtSignal(str, int, str)

    # Phase 2 → main_window: claim this seat (sends SEAT_REQUEST).
    seat_clicked = pyqtSignal(object)  # Seat enum

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Network Game")
        self.setMinimumWidth(560)
        apply_dialog_style(self)

        self._connecting = False
        self._my_seat: Seat = None
        self._setup_ui()

    def _matchmaker_refresh(self):
        self.matchmaker_list.clear()
        tables = serious_games_week.list_tables()
        for t in tables:
            seats = "%d" % t.get("players", 0) + ("/%d" % t["max_players"] if t.get("max_players") else "")
            item = QListWidgetItem("%s  \u2014  %s:%d  (%s)" % (t.get("title", "table"), t["host"], t["port"], seats))
            item.setData(Qt.ItemDataRole.UserRole, (t["host"], int(t["port"])))
            self.matchmaker_list.addItem(item)
        if not tables:
            self.matchmaker_list.addItem("(no open tables on Serious Games Week right now)")

    def _matchmaker_pick(self, item, _prev=None):
        hp = item.data(Qt.ItemDataRole.UserRole) if item else None
        if hp:
            self.host_edit.setText(hp[0])
            self.port_spin.setValue(hp[1])

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_form_page())
        self._stack.addWidget(self._build_lobby_page())
        layout.addWidget(self._stack)

    # ------------------------------------------------------------------ #
    # Phase 1: connection form
    # ------------------------------------------------------------------ #
    def _build_form_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        if serious_games_week.configured():
            sq_group = QGroupBox("Tables on Serious Games Week")
            sq_layout = QVBoxLayout(sq_group)
            self.matchmaker_list = QListWidget()
            self.matchmaker_list.setToolTip("Open tables listed on your Serious Games Week matchmaker -- pick one to fill in its address")
            self.matchmaker_list.currentItemChanged.connect(self._matchmaker_pick)
            sq_layout.addWidget(self.matchmaker_list)
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self._matchmaker_refresh)
            sq_layout.addWidget(refresh)
            layout.addWidget(sq_group)
            QTimer.singleShot(0, self._matchmaker_refresh)

        settings_group = QGroupBox("Connection Settings")
        settings_layout = QGridLayout(settings_group)

        settings_layout.addWidget(QLabel("Server address:"), 0, 0)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("IP address or hostname")
        settings_layout.addWidget(self.host_edit, 0, 1)

        settings_layout.addWidget(QLabel("Port:"), 1, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(7777)
        settings_layout.addWidget(self.port_spin, 1, 1)

        settings_layout.addWidget(QLabel("Your name:"), 2, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setText("Guest")
        settings_layout.addWidget(self.name_edit, 2, 1)

        # Backlog 55: the host's password, if the table has one
        settings_layout.addWidget(QLabel("Password:"), 3, 0)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("only if the host set one")
        settings_layout.addWidget(self.password_edit, 3, 1)
        layout.addWidget(settings_group)

        hint = QLabel(
            "After connecting you'll see the live seat board and can pick\n"
            "any seat the host hasn't already taken."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555;")
        layout.addWidget(hint)

        # Status (shown while the connection is being attempted).
        self.status_group = QGroupBox("Connection Status")
        status_layout = QVBoxLayout(self.status_group)
        self.status_label = QLabel("Not connected")
        status_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        status_layout.addWidget(self.progress_bar)
        self.status_group.setVisible(False)
        layout.addWidget(self.status_group)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c00; font-weight: bold;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        button_layout.addWidget(self.connect_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        return page

    # ------------------------------------------------------------------ #
    # Phase 2: lobby — live seat board
    # ------------------------------------------------------------------ #
    def _build_lobby_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.lobby_status = QLabel(
            "Connected. Click an empty seat to claim it."
        )
        self.lobby_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lobby_status)

        self._lobby_board = SeatBoardWidget(allow_click=True)
        self._lobby_board.seat_clicked.connect(self._on_lobby_seat_clicked)
        layout.addWidget(self._lobby_board)

        self.lobby_error = QLabel()
        self.lobby_error.setStyleSheet("color: #c00; font-weight: bold;")
        self.lobby_error.setWordWrap(True)
        self.lobby_error.setVisible(False)
        layout.addWidget(self.lobby_error)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.leave_btn = QPushButton("Leave Table")
        self.leave_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.leave_btn)
        layout.addLayout(button_layout)
        return page

    # ------------------------------------------------------------------ #
    # External API used by main_window
    # ------------------------------------------------------------------ #
    def update_seat_map(self, seats: dict, host_seat: str = "",
                        my_seat: Seat = None):
        """Apply a fresh seat list. Routed from NetworkGameController."""
        self._my_seat = my_seat
        self._lobby_board.set_seats(seats, host_seat=host_seat, my_seat=my_seat)

    def enter_lobby(self, my_seat: Seat = None):
        """Switch the dialog to the lobby (post-connection) view."""
        self._my_seat = my_seat
        self._lobby_board.set_my_seat(my_seat)
        self._stack.setCurrentIndex(1)
        title = "Network Game Lobby"
        if my_seat is not None:
            title += f" — seated at {my_seat.to_char()}"
        self.setWindowTitle(title)
        self.lobby_error.setVisible(False)
        if my_seat is None:
            self.lobby_status.setText(
                "Connected. Click an empty seat to claim it."
            )
        else:
            self.lobby_status.setText(
                f"Seated at {my_seat.to_char()}. Waiting for the host to start the game…"
            )

    def show_seat_taken_error(self, message: str):
        """Display an inline error when the user clicks a seat that is
        already taken (race condition with another guest)."""
        self.lobby_error.setText(message)
        self.lobby_error.setVisible(True)

    def show_lobby_status(self, message: str):
        self.lobby_status.setText(message)

    # ------------------------------------------------------------------ #
    # Internal handlers
    # ------------------------------------------------------------------ #
    def _on_lobby_seat_clicked(self, seat: Seat):
        if self._my_seat is not None and seat == self._my_seat:
            return  # Already mine.
        self.lobby_error.setVisible(False)
        self.lobby_status.setText(f"Requesting seat {seat.to_char()}…")
        self.seat_clicked.emit(seat)

    def _on_connect_clicked(self):
        host = self.host_edit.text().strip()
        port = self.port_spin.value()
        name = self.name_edit.text().strip() or "Guest"

        if not host:
            self.show_error("Please enter a server address")
            return

        self.set_connecting(True)
        self.connect_requested.emit(host, port, name)

    def set_connecting(self, connecting: bool):
        self._connecting = connecting
        self.status_group.setVisible(connecting)
        self.progress_bar.setVisible(connecting)
        self.connect_btn.setEnabled(not connecting)
        self.host_edit.setEnabled(not connecting)
        self.port_spin.setEnabled(not connecting)
        self.name_edit.setEnabled(not connecting)
        self.error_label.setVisible(False)
        self.status_label.setText("Connecting…" if connecting else "Not connected")

    def show_error(self, message: str):
        # If the dialog is on the lobby page, surface the error inline so
        # the user can pick a different seat instead of being kicked back
        # to the form.
        if self._stack.currentIndex() == 1:
            self.show_seat_taken_error(message)
            return
        self.error_label.setText(message)
        self.error_label.setVisible(True)
        self.set_connecting(False)
        self._stack.setCurrentIndex(0)
