"""
SeatBoardWidget — graphical 4-seat picker shared by the host lobby and the
guest's connect dialog.

Layout mimics a bridge table: North on top, South on bottom, East on right,
West on left. Each seat is a clickable button that displays the occupant's
name (or "Empty"); seats already taken by other players are disabled.
"""

from PyQt6.QtWidgets import QWidget, QGridLayout, QPushButton, QLabel
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont

from backend.models import Seat


SEAT_ORDER = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]


class SeatBoardWidget(QWidget):
    """A clickable 4-seat board that visualizes the current lobby state."""

    # Emitted when the user clicks an enabled seat button.
    seat_clicked = pyqtSignal(object)  # Seat enum

    def __init__(self, parent=None, allow_click: bool = True):
        super().__init__(parent)
        self._allow_click = allow_click
        # When set, this seat is rendered as "you" — the local user's seat.
        self._my_seat: Seat = None
        # Last seat map applied; remembered so we can re-render when my_seat
        # or host_seat changes after the initial set_seats() call.
        self._seats: dict = {s.to_char(): None for s in Seat}
        self._host_seat: str = ""
        self._setup_ui()

    def _setup_ui(self):
        grid = QGridLayout(self)
        grid.setContentsMargins(20, 20, 20, 20)
        grid.setHorizontalSpacing(40)
        grid.setVerticalSpacing(15)

        title = QLabel("Choose your seat")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        grid.addWidget(title, 0, 0, 1, 3)

        self._buttons: dict = {}
        for seat in SEAT_ORDER:
            btn = QPushButton()
            btn.setMinimumSize(140, 70)
            btn.setFont(QFont("Arial", 10))
            btn.clicked.connect(lambda _checked=False, s=seat: self._on_clicked(s))
            self._buttons[seat] = btn

        # Cell positions inside the inner 3×3 grid (0-indexed inside the
        # 3-wide outer layout that already has the title at row 0).
        # Row 1 = North (top), Row 2 = E/W middle, Row 3 = South (bottom).
        grid.addWidget(self._buttons[Seat.NORTH], 1, 1)
        grid.addWidget(self._buttons[Seat.WEST],  2, 0)
        grid.addWidget(self._buttons[Seat.EAST],  2, 2)
        grid.addWidget(self._buttons[Seat.SOUTH], 3, 1)

        self._refresh_buttons()

    def _on_clicked(self, seat: Seat):
        if not self._allow_click:
            return
        # Don't fire for taken seats — visually disabled, but be defensive.
        char = seat.to_char()
        occupant = self._seats.get(char)
        if occupant and (self._my_seat is None or seat != self._my_seat):
            return
        self.seat_clicked.emit(seat)

    def set_seats(self, seats: dict, host_seat: str = "",
                  my_seat: Seat = None):
        """Update the visualized occupancy.

        Args:
            seats: {seat_char: name_or_None}
            host_seat: seat char of the host (annotated with " (host)")
            my_seat: the local user's seat — rendered with a "(you)" suffix
                and a highlighted style. Pass None to omit the marker.
        """
        # Normalize so missing keys still render as empty seats.
        normalized = {s.to_char(): None for s in Seat}
        normalized.update(seats or {})
        self._seats = normalized
        self._host_seat = (host_seat or "").upper()
        self._my_seat = my_seat
        self._refresh_buttons()

    def set_my_seat(self, seat: Seat):
        """Update only the local-user marker. Useful after the host has
        confirmed a guest's seat assignment."""
        self._my_seat = seat
        self._refresh_buttons()

    def _refresh_buttons(self):
        seat_full_names = {Seat.NORTH: 'North', Seat.EAST: 'East',
                           Seat.SOUTH: 'South', Seat.WEST: 'West'}
        for seat in SEAT_ORDER:
            btn = self._buttons[seat]
            char = seat.to_char()
            occupant = self._seats.get(char)
            is_mine = (self._my_seat is not None and seat == self._my_seat)
            is_host = (char == self._host_seat)

            label_lines = [f"{seat_full_names[seat]} ({char})"]
            if occupant:
                suffix = []
                if is_mine:
                    suffix.append("you")
                if is_host:
                    suffix.append("host")
                tag = f" ({', '.join(suffix)})" if suffix else ""
                label_lines.append(f"{occupant}{tag}")
            else:
                label_lines.append("Empty")
            btn.setText("\n".join(label_lines))

            if is_mine:
                btn.setEnabled(True)
                btn.setStyleSheet(
                    "QPushButton { background-color: #88ccff; color: #000; "
                    "font-weight: bold; border: 2px solid #4488cc; "
                    "border-radius: 6px; }"
                )
            elif occupant:
                btn.setEnabled(False)
                btn.setStyleSheet(
                    "QPushButton { background-color: #f5b6b6; color: #444; "
                    "border: 1px solid #aa6666; border-radius: 6px; } "
                    "QPushButton:disabled { color: #444; }"
                )
            else:
                btn.setEnabled(self._allow_click)
                btn.setStyleSheet(
                    "QPushButton { background-color: #b9f0b9; color: #000; "
                    "border: 1px solid #408040; border-radius: 6px; } "
                    "QPushButton:hover { background-color: #95e095; }"
                )
