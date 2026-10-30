"""
Information Tables Dialogs - IMP table, probabilities, etc.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QTabWidget, QWidget, QHeaderView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .dialog_style import apply_dialog_style


class IMPTableDialog(QDialog):
    """Display IMP conversion table"""

    IMP_TABLE = [
        ("0-10", 0), ("20-40", 1), ("50-80", 2), ("90-120", 3),
        ("130-160", 4), ("170-210", 5), ("220-260", 6), ("270-310", 7),
        ("320-360", 8), ("370-420", 9), ("430-490", 10), ("500-590", 11),
        ("600-740", 12), ("750-890", 13), ("900-1090", 14), ("1100-1290", 15),
        ("1300-1490", 16), ("1500-1740", 17), ("1750-1990", 18), ("2000-2240", 19),
        ("2250-2490", 20), ("2500-2990", 21), ("3000-3490", 22), ("3500-3990", 23),
        ("4000+", 24)
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IMP Conversion Table")
        self.setMinimumWidth(350)
        self.setMinimumHeight(500)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("International Match Points (IMP) Scale")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(12)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Table
        table = QTableWidget(len(self.IMP_TABLE), 2)
        table.setHorizontalHeaderLabels(["Point Difference", "IMPs"])
        table.verticalHeader().setVisible(False)

        for row, (points, imps) in enumerate(self.IMP_TABLE):
            points_item = QTableWidgetItem(points)
            points_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, points_item)

            imps_item = QTableWidgetItem(str(imps))
            imps_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 1, imps_item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)

        # Note
        note = QLabel("Note: Negative differences give negative IMPs")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(note)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class ProbabilitiesDialog(QDialog):
    """Display suit break probabilities"""

    PROBABILITIES = [
        (2, [("1-1", 52), ("2-0", 48)]),
        (3, [("2-1", 78), ("3-0", 22)]),
        (4, [("3-1", 50), ("2-2", 41), ("4-0", 10)]),
        (5, [("3-2", 68), ("4-1", 28), ("5-0", 4)]),
        (6, [("4-2", 48), ("3-3", 36), ("5-1", 15), ("6-0", 1)]),
        (7, [("4-3", 62), ("5-2", 31), ("6-1", 7), ("7-0", 0.5)]),
        (8, [("5-3", 47), ("4-4", 33), ("6-2", 17), ("7-1", 3), ("8-0", 0.2)]),
    ]

    # A priori probabilities of total points
    HCP_DISTRIBUTION = [
        (0, 0.36), (1, 0.79), (2, 1.36), (3, 2.46), (4, 3.85),
        (5, 5.19), (6, 6.55), (7, 8.03), (8, 8.89), (9, 9.36),
        (10, 9.41), (11, 8.94), (12, 8.03), (13, 6.91), (14, 5.69),
        (15, 4.42), (16, 3.31), (17, 2.36), (18, 1.61), (19, 1.04),
        (20, 0.64), (21, 0.38), (22, 0.21), (23, 0.11), (24, 0.056),
        (25, 0.026), (26, 0.012), (27, 0.0049), (28, 0.0019),
        (29, 0.00067), (30, 0.00022), (31, 0.000064), (32, 0.000017),
        (33, 0.0000039), (34, 0.0000008), (35, 0.00000013), (36, 0.000000018),
        (37, 0.0000000020)
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bridge Probabilities")
        self.setMinimumWidth(500)
        self.setMinimumHeight(550)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget for different tables
        tabs = QTabWidget()

        # Suit breaks tab
        suit_tab = QWidget()
        suit_layout = QVBoxLayout(suit_tab)

        suit_title = QLabel("Suit Break Probabilities")
        suit_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        suit_title.setFont(font)
        suit_layout.addWidget(suit_title)

        # Calculate total rows
        total_rows = sum(len(breaks) for _, breaks in self.PROBABILITIES)
        suit_table = QTableWidget(total_rows, 3)
        suit_table.setHorizontalHeaderLabels(["Cards Out", "Distribution", "Probability %"])
        suit_table.verticalHeader().setVisible(False)

        row = 0
        for cards_out, distributions in self.PROBABILITIES:
            first_row_for_group = True
            for distrib, prob in distributions:
                if first_row_for_group:
                    cards_item = QTableWidgetItem(str(cards_out))
                    cards_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    suit_table.setItem(row, 0, cards_item)
                    first_row_for_group = False
                else:
                    suit_table.setItem(row, 0, QTableWidgetItem(""))

                distrib_item = QTableWidgetItem(distrib)
                distrib_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                suit_table.setItem(row, 1, distrib_item)

                prob_item = QTableWidgetItem(f"{prob}%")
                prob_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                suit_table.setItem(row, 2, prob_item)
                row += 1

        header = suit_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        suit_layout.addWidget(suit_table)

        tabs.addTab(suit_tab, "Suit Breaks")

        # HCP distribution tab
        hcp_tab = QWidget()
        hcp_layout = QVBoxLayout(hcp_tab)

        hcp_title = QLabel("A Priori HCP Distribution")
        hcp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hcp_title.setFont(font)
        hcp_layout.addWidget(hcp_title)

        hcp_table = QTableWidget(len(self.HCP_DISTRIBUTION), 2)
        hcp_table.setHorizontalHeaderLabels(["HCP", "Probability %"])
        hcp_table.verticalHeader().setVisible(False)

        for row, (hcp, prob) in enumerate(self.HCP_DISTRIBUTION):
            hcp_item = QTableWidgetItem(str(hcp))
            hcp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            hcp_table.setItem(row, 0, hcp_item)

            prob_item = QTableWidgetItem(f"{prob:.4f}%" if prob < 0.01 else f"{prob:.2f}%")
            prob_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            hcp_table.setItem(row, 1, prob_item)

        header = hcp_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hcp_layout.addWidget(hcp_table)

        tabs.addTab(hcp_tab, "HCP Distribution")

        # Finesses tab
        finesse_tab = QWidget()
        finesse_layout = QVBoxLayout(finesse_tab)

        finesse_title = QLabel("Common Finesse Probabilities")
        finesse_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        finesse_title.setFont(font)
        finesse_layout.addWidget(finesse_title)

        finesses = [
            ("Simple finesse (50%)", "50%"),
            ("Either of two finesses", "75%"),
            ("Both of two finesses", "25%"),
            ("AQ10 - play for K or J", "76%"),
            ("AJ10 - play for K or Q", "76%"),
            ("KJ9 - double finesse", "24%"),
            ("AQ9 - double finesse for J", "26%"),
            ("8 card fit - drop vs finesse", "52% finesse"),
            ("9 card fit - drop vs finesse", "52% drop"),
        ]

        finesse_table = QTableWidget(len(finesses), 2)
        finesse_table.setHorizontalHeaderLabels(["Situation", "Success Rate"])
        finesse_table.verticalHeader().setVisible(False)

        for row, (situation, rate) in enumerate(finesses):
            sit_item = QTableWidgetItem(situation)
            finesse_table.setItem(row, 0, sit_item)

            rate_item = QTableWidgetItem(rate)
            rate_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            finesse_table.setItem(row, 1, rate_item)

        header = finesse_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        finesse_layout.addWidget(finesse_table)

        tabs.addTab(finesse_tab, "Finesses")

        layout.addWidget(tabs)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class ScoringReferenceDialog(QDialog):
    """Display scoring reference tables"""

    # Trick scores
    TRICK_SCORES = [
        ("1♣/1♦", "20", "40", "80"),
        ("1♥/1♠", "30", "60", "120"),
        ("1NT", "40", "80", "160"),
        ("2♣/2♦", "40", "80", "160"),
        ("2♥/2♠", "60", "120", "240"),
        ("2NT", "70", "140", "280"),
        ("3♣/3♦", "60", "120", "240"),
        ("3♥/3♠", "90", "180", "360"),
        ("3NT", "100", "200", "400"),
    ]

    # Undertrick penalties
    UNDERTRICKS = [
        ("1 down", "50", "100", "100", "200", "200", "400"),
        ("2 down", "100", "200", "300", "500", "600", "1000"),
        ("3 down", "150", "300", "500", "800", "1000", "1600"),
        ("4 down", "200", "400", "800", "1100", "1400", "2200"),
        ("5 down", "250", "500", "1100", "1400", "1800", "2800"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scoring Reference")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # Trick scores tab
        trick_tab = QWidget()
        trick_layout = QVBoxLayout(trick_tab)

        trick_table = QTableWidget(len(self.TRICK_SCORES), 4)
        trick_table.setHorizontalHeaderLabels(["Contract", "Undoubled", "Doubled", "Redoubled"])
        trick_table.verticalHeader().setVisible(False)

        for row, (contract, undbl, dbl, rdbl) in enumerate(self.TRICK_SCORES):
            trick_table.setItem(row, 0, QTableWidgetItem(contract))
            trick_table.setItem(row, 1, QTableWidgetItem(undbl))
            trick_table.setItem(row, 2, QTableWidgetItem(dbl))
            trick_table.setItem(row, 3, QTableWidgetItem(rdbl))

        trick_layout.addWidget(trick_table)
        tabs.addTab(trick_tab, "Trick Scores")

        # Undertricks tab
        under_tab = QWidget()
        under_layout = QVBoxLayout(under_tab)

        under_table = QTableWidget(len(self.UNDERTRICKS), 7)
        under_table.setHorizontalHeaderLabels([
            "Result", "NV Undbl", "Vul Undbl",
            "NV Dbl", "Vul Dbl", "NV Rdbl", "Vul Rdbl"
        ])
        under_table.verticalHeader().setVisible(False)

        for row, data in enumerate(self.UNDERTRICKS):
            for col, val in enumerate(data):
                under_table.setItem(row, col, QTableWidgetItem(val))

        under_layout.addWidget(under_table)
        tabs.addTab(under_tab, "Undertricks")

        # Bonuses tab
        bonus_tab = QWidget()
        bonus_layout = QVBoxLayout(bonus_tab)

        bonuses = [
            ("Partscore bonus", "50", "50"),
            ("Game bonus", "300", "500"),
            ("Small slam bonus", "500", "750"),
            ("Grand slam bonus", "1000", "1500"),
            ("Making doubled contract", "+50", "+50"),
            ("Making redoubled contract", "+100", "+100"),
            ("Overtrick (undoubled, minor)", "20", "20"),
            ("Overtrick (undoubled, major/NT)", "30", "30"),
            ("Overtrick (doubled)", "100", "200"),
            ("Overtrick (redoubled)", "200", "400"),
        ]

        bonus_table = QTableWidget(len(bonuses), 3)
        bonus_table.setHorizontalHeaderLabels(["Bonus Type", "Non-Vul", "Vulnerable"])
        bonus_table.verticalHeader().setVisible(False)

        for row, (bonus_type, nv, vul) in enumerate(bonuses):
            bonus_table.setItem(row, 0, QTableWidgetItem(bonus_type))
            bonus_table.setItem(row, 1, QTableWidgetItem(nv))
            bonus_table.setItem(row, 2, QTableWidgetItem(vul))

        bonus_layout.addWidget(bonus_table)
        tabs.addTab(bonus_tab, "Bonuses")

        layout.addWidget(tabs)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
