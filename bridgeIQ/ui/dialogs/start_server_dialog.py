"""
Start Server Dialog for LAN play.
Allows the host to configure server settings and start hosting a game.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QComboBox, QPushButton
)
from PyQt6.QtNetwork import QNetworkInterface, QAbstractSocket
from PyQt6.QtCore import Qt

from backend.models import Seat
from .dialog_style import apply_dialog_style

from typing import Optional


class StartServerDialog(QDialog):
    """
    Dialog for starting a LAN server.

    Shows local IP addresses and allows the host to:
    - Set port (default 7777)
    - Enter player name
    - Select their seat
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Network Server")
        self.setMinimumWidth(400)
        apply_dialog_style(self)

        self._port = 7777
        self._player_name = "Host"
        self._seat = Seat.SOUTH

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Network info group
        net_group = QGroupBox("Network Information")
        net_layout = QVBoxLayout(net_group)

        # Local IP addresses
        ip_label = QLabel("Your IP address(es):")
        ip_label.setStyleSheet("font-weight: bold;")
        net_layout.addWidget(ip_label)

        ip_addresses = self._get_local_ip_addresses()
        if ip_addresses:
            for ip in ip_addresses:
                ip_lbl = QLabel(f"  {ip}")
                ip_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                net_layout.addWidget(ip_lbl)
        else:
            net_layout.addWidget(QLabel("  (No network interfaces found)"))

        layout.addWidget(net_group)

        # Server settings group
        settings_group = QGroupBox("Server Settings")
        settings_layout = QGridLayout(settings_group)

        # Port
        settings_layout.addWidget(QLabel("Port:"), 0, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(self._port)
        settings_layout.addWidget(self.port_spin, 0, 1)

        # Player name
        settings_layout.addWidget(QLabel("Your name:"), 1, 0)
        self.name_edit = QLineEdit(self._player_name)
        settings_layout.addWidget(self.name_edit, 1, 1)

        # Seat selection
        settings_layout.addWidget(QLabel("Your seat:"), 2, 0)
        self.seat_combo = QComboBox()
        self.seat_combo.addItem("South (S)", Seat.SOUTH)
        self.seat_combo.addItem("North (N)", Seat.NORTH)
        self.seat_combo.addItem("East (E)", Seat.EAST)
        self.seat_combo.addItem("West (W)", Seat.WEST)
        self.seat_combo.currentIndexChanged.connect(self._on_seat_changed)
        settings_layout.addWidget(self.seat_combo, 2, 1)

        # Partner seat (auto-calculated, for information only)
        settings_layout.addWidget(QLabel("Partner seat:"), 3, 0)
        self.partner_label = QLabel("North (N)")
        self.partner_label.setStyleSheet("font-style: italic;")
        settings_layout.addWidget(self.partner_label, 3, 1)

        # Backlog 55: optional table password (blank = an open table)
        settings_layout.addWidget(QLabel("Password:"), 4, 0)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("optional -- blank = open table")
        settings_layout.addWidget(self.password_edit, 4, 1)

        layout.addWidget(settings_group)

        # Instructions
        info_label = QLabel(
            "When you start the server, share your IP address and port with the\n"
            "other players. Each guest will pick any seat that is still free."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin: 10px 0;")
        layout.addWidget(info_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.start_btn = QPushButton("Start Server")
        self.start_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

        # Initialize partner display
        self._update_partner_display()

    def _get_local_ip_addresses(self) -> list:
        """Get list of local IP addresses."""
        addresses = []
        for interface in QNetworkInterface.allInterfaces():
            flags = interface.flags()
            if not (flags & QNetworkInterface.InterfaceFlag.IsUp):
                continue
            if flags & QNetworkInterface.InterfaceFlag.IsLoopBack:
                continue

            for entry in interface.addressEntries():
                ip = entry.ip()
                # Only include IPv4 addresses
                if ip.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                    addresses.append(ip.toString())

        return addresses

    def _on_seat_changed(self, index: int):
        """Handle seat selection change."""
        self._update_partner_display()

    def _update_partner_display(self):
        """Update partner seat display based on current selection."""
        seat = self.seat_combo.currentData()
        if seat is not None:
            partner = seat.partner()
            seat_names = {
                Seat.NORTH: "North (N)",
                Seat.EAST: "East (E)",
                Seat.SOUTH: "South (S)",
                Seat.WEST: "West (W)",
            }
            self.partner_label.setText(seat_names[partner])

    def get_settings(self) -> dict:
        """
        Get the configured server settings.

        Returns:
            Dict with 'port', 'name', and 'seat'
        """
        return {
            'port': self.port_spin.value(),
            'name': self.name_edit.text().strip() or "Host",
            'seat': self.seat_combo.currentData(),
            'password': self.password_edit.text(),
        }
