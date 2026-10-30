"""
Bid List Dialog - shows all possible bids with their meanings.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from .dialog_style import apply_dialog_style


class BidListDialog(QDialog):
    """Dialog showing all possible bids with meanings."""

    SUIT_SYMBOLS = {'S': '\u2660', 'H': '\u2665', 'D': '\u2666', 'C': '\u2663'}
    SUIT_COLORS = {'S': '#000000', 'H': '#cc0000', 'D': '#cc0000', 'C': '#000000'}

    def __init__(self, parent=None, possible_bids=None, seat=None, auction=None):
        super().__init__(parent)
        self.setWindowTitle("Possible Bids")
        self.setMinimumWidth(800)
        self.setMinimumHeight(500)
        self.resize(850, 550)
        apply_dialog_style(self)

        self.possible_bids = possible_bids or []
        self.seat = seat
        self.auction = auction or []
        self.selected_bid = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header
        seat_name = self.seat.name.capitalize() if self.seat else "Player"
        header = QLabel(f"<b>Possible bids for {seat_name}</b>")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Table of bids
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            'Bid', 'HCP', 'Length', 'Forcing', 'Alert', 'Meaning'
        ])

        # Set column widths
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 50)

        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                color: #000000;
                gridline-color: #c0c0c0;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QTableWidget::item:selected {
                background-color: #3070b0;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #e0e0e0;
                color: #000000;
                padding: 4px;
                font-weight: bold;
                border: 1px solid #c0c0c0;
            }
        """)

        self._populate_bids()
        layout.addWidget(self.table)

        # Legend
        legend_frame = QFrame()
        legend_frame.setStyleSheet("""
            QFrame {
                background-color: #f5f5f5;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
            }
        """)
        legend_layout = QHBoxLayout(legend_frame)
        legend_layout.addWidget(QLabel("Legend:"))
        legend_layout.addWidget(QLabel("F = Forcing"))
        legend_layout.addWidget(QLabel("NF = Non-Forcing"))
        legend_layout.addWidget(QLabel("GF = Game Forcing"))
        legend_layout.addWidget(QLabel("! = Alert"))
        legend_layout.addStretch()
        layout.addWidget(legend_frame)

        # Buttons
        button_layout = QHBoxLayout()

        self.select_btn = QPushButton("Select Bid")
        self.select_btn.clicked.connect(self._on_select)
        self.select_btn.setMinimumWidth(100)
        button_layout.addWidget(self.select_btn)

        button_layout.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.close_btn.setMinimumWidth(80)
        button_layout.addWidget(self.close_btn)

        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumWidth(80)
        button_layout.addWidget(self.help_btn)

        layout.addLayout(button_layout)

        # Double-click to select
        self.table.doubleClicked.connect(self._on_select)

    def _populate_bids(self):
        """Fill the table with possible bids."""
        # Generate standard bids if none provided
        if not self.possible_bids:
            self.possible_bids = self._generate_standard_bids()

        self.table.setRowCount(len(self.possible_bids))

        for row, bid_info in enumerate(self.possible_bids):
            # Bid column
            bid_text = self._format_bid(bid_info.get('bid', ''))
            bid_item = QTableWidgetItem()
            bid_item.setData(Qt.ItemDataRole.DisplayRole, bid_text)
            bid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, bid_item)

            # HCP column
            hcp = bid_info.get('hcp', '')
            hcp_item = QTableWidgetItem(str(hcp) if hcp else '')
            hcp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, hcp_item)

            # Length column
            length = bid_info.get('length', '')
            length_item = QTableWidgetItem(str(length) if length else '')
            length_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, length_item)

            # Forcing column
            forcing = bid_info.get('forcing', '')
            forcing_text = ''
            if forcing == 'game':
                forcing_text = 'GF'
            elif forcing == 'one':
                forcing_text = 'F'
            elif forcing == 'no':
                forcing_text = 'NF'
            forcing_item = QTableWidgetItem(forcing_text)
            forcing_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, forcing_item)

            # Alert column
            alert = bid_info.get('alert', False)
            alert_item = QTableWidgetItem('!' if alert else '')
            alert_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if alert:
                alert_item.setForeground(QColor('#cc0000'))
                font = alert_item.font()
                font.setBold(True)
                alert_item.setFont(font)
            self.table.setItem(row, 4, alert_item)

            # Meaning column
            meaning = bid_info.get('meaning', '')
            meaning_item = QTableWidgetItem(meaning)
            self.table.setItem(row, 5, meaning_item)

    def _format_bid(self, bid):
        """Format a bid string for display."""
        if not bid:
            return ''
        bid_upper = bid.upper()
        if bid_upper in ('PASS', 'P', '-'):
            return 'Pass'
        elif bid_upper in ('X', 'DBL', 'DOUBLE'):
            return 'Dbl'
        elif bid_upper in ('XX', 'RDBL', 'REDOUBLE'):
            return 'Rdbl'
        elif len(bid) >= 2:
            level = bid[0]
            suit = bid[1:].upper()
            if suit in ('NT', 'N'):
                return f'{level}NT'
            elif suit in self.SUIT_SYMBOLS:
                return f'{level}{self.SUIT_SYMBOLS[suit]}'
        return bid

    def _generate_standard_bids(self):
        """Generate list of standard possible bids."""
        bids = []

        # Pass
        bids.append({
            'bid': 'Pass',
            'hcp': '0+',
            'length': '',
            'forcing': 'no',
            'alert': False,
            'meaning': 'No bid'
        })

        # Level 1 bids
        for suit, name in [('C', 'Clubs'), ('D', 'Diamonds'), ('H', 'Hearts'), ('S', 'Spades')]:
            bids.append({
                'bid': f'1{suit}',
                'hcp': '12-21',
                'length': f'4+{suit}',
                'forcing': 'one',
                'alert': False,
                'meaning': f'Opening bid in {name}'
            })

        bids.append({
            'bid': '1NT',
            'hcp': '15-17',
            'length': 'Bal',
            'forcing': 'no',
            'alert': False,
            'meaning': 'Balanced hand'
        })

        # Level 2 bids
        for suit, name in [('C', 'Clubs'), ('D', 'Diamonds'), ('H', 'Hearts'), ('S', 'Spades')]:
            bids.append({
                'bid': f'2{suit}',
                'hcp': '5-11',
                'length': f'6+{suit}',
                'forcing': 'no',
                'alert': suit in ('C', 'D'),
                'meaning': f'Weak two in {name}' if suit in ('H', 'S') else 'Strong/Artificial'
            })

        bids.append({
            'bid': '2NT',
            'hcp': '20-21',
            'length': 'Bal',
            'forcing': 'no',
            'alert': False,
            'meaning': 'Balanced hand'
        })

        return bids

    def _on_select(self):
        """Handle bid selection."""
        selected_rows = self.table.selectedItems()
        if selected_rows:
            row = selected_rows[0].row()
            if row < len(self.possible_bids):
                self.selected_bid = self.possible_bids[row].get('bid')
                self.accept()

    def get_selected_bid(self):
        """Return the selected bid."""
        return self.selected_bid
