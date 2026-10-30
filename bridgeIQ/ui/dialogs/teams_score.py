"""
Teams Score Dialog - Score window showing Open and Closed room results side by side.
"""

from typing import Optional, List, Dict
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from .dialog_style import apply_dialog_style

from backend.models import BenTable, BenBoardRun, BenTeamsMatch


class TeamsScoreDialog(QDialog):
    """Score window showing Open and Closed room results side by side.

    Displays a table with columns:
    Contract 1 | N/S 1 | IMP | Deal | Contract 2 | N/S 2 | IMP

    Where 1 = Open Room, 2 = Closed Room
    Clicking on a contract cell emits a signal to view the replay.
    """

    contract_clicked = pyqtSignal(int, object)  # board_num, BenTable

    # Larger font size for better visibility
    FONT_SIZE = 18

    def __init__(self, match: BenTeamsMatch, parent=None):
        super().__init__(parent)

        self.match = match

        self.setWindowTitle("Teams Match Score")

        # Size to fill most of screen height
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().geometry()
        self.resize(900, int(screen.height() * 0.85))

        apply_dialog_style(self)

        self._setup_ui()
        self._populate_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header with team names and totals
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #e0e8f0;
                border: 1px solid #a0a0a0;
                border-radius: 4px;
                padding: 10px;
            }
        """)
        header_layout = QHBoxLayout(header_frame)

        # N/S team
        ns_label = QLabel(f"N/S: {self.match.ns_team_name}")
        ns_label.setFont(QFont("Arial", self.FONT_SIZE, QFont.Weight.Bold))
        header_layout.addWidget(ns_label)

        header_layout.addStretch()

        # IMP totals
        ns_imps, ew_imps = self.match.get_total_imps()
        total_label = QLabel(f"IMPs: {ns_imps} - {ew_imps}")
        total_label.setFont(QFont("Arial", self.FONT_SIZE + 4, QFont.Weight.Bold))
        total_label.setStyleSheet("color: #0000aa;")
        header_layout.addWidget(total_label)

        header_layout.addStretch()

        # E/W team
        ew_label = QLabel(f"E/W: {self.match.ew_team_name}")
        ew_label.setFont(QFont("Arial", self.FONT_SIZE, QFont.Weight.Bold))
        header_layout.addWidget(ew_label)

        layout.addWidget(header_frame)

        # Results table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Open Contract", "Open N/S", "IMP",
            "Board",
            "Closed Contract", "Closed N/S", "IMP"
        ])

        # Style the table with larger fonts
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: #ffffff;
                color: #000000;
                gridline-color: #c0c0c0;
                font-size: {self.FONT_SIZE}px;
            }}
            QTableWidget::item {{
                padding: 10px;
            }}
            QTableWidget::item:selected {{
                background-color: #4080c0;
                color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: #d0d8e0;
                color: #000000;
                padding: 10px;
                border: 1px solid #a0a0a0;
                font-weight: bold;
                font-size: {self.FONT_SIZE}px;
            }}
        """)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Open Contract
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Open N/S
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # IMP
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)           # Board
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Closed Contract
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Closed N/S
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # IMP

        # Connect click handler
        self.table.cellClicked.connect(self._on_cell_clicked)

        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        # Compare — open vs closed room, side by side, for the selected board.
        compare_btn = QPushButton("Compare (open vs closed)")
        compare_btn.setToolTip("Side-by-side replay of your table and the "
                               "closed room for the selected board.")
        compare_btn.clicked.connect(self._on_compare)
        button_layout.addWidget(compare_btn)

        # Replay — step through the selected board's open room.
        replay_btn = QPushButton("Replay")
        replay_btn.setToolTip("Replay the selected board (or click a contract "
                              "cell).")
        replay_btn.clicked.connect(self._on_replay_selected)
        button_layout.addWidget(replay_btn)

        # Export button
        export_btn = QPushButton("Export...")
        export_btn.clicked.connect(self._on_export)
        button_layout.addWidget(export_btn)

        button_layout.addStretch()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _selected_board_num(self):
        """Board number of the currently selected row, or None."""
        row = self.table.currentRow()
        if row < 0:
            row = 0 if self.table.rowCount() == 1 else -1
        if row < 0:
            return None
        cell = self.table.item(row, 3)
        try:
            return int(cell.text()) if cell else None
        except Exception:
            return None

    def _on_compare(self):
        """Open the side-by-side open-vs-closed replay for the selected board."""
        from PyQt6.QtWidgets import QMessageBox
        board_num = self._selected_board_num()
        if board_num is None:
            QMessageBox.information(self, "Compare", "Select a board row first.")
            return
        runs = self.match.board_runs.get(board_num, {})
        open_r = runs.get(BenTable.OPEN)
        closed_r = runs.get(BenTable.CLOSED)
        if not (open_r and getattr(open_r, 'played', False)
                and closed_r and getattr(closed_r, 'played', False)):
            QMessageBox.information(
                self, "Compare",
                "Both the open room and the closed room must be complete "
                "to compare this board.")
            return
        try:
            from .compare_replay import CompareReplayDialog
            CompareReplayDialog(open_r, closed_r, parent=self).exec()
        except Exception as ex:
            QMessageBox.warning(self, "Compare",
                                f"Could not open the comparison:\n{ex}")

    def _on_replay_selected(self):
        """Replay the selected board's open room (same as clicking its cell)."""
        from PyQt6.QtWidgets import QMessageBox
        board_num = self._selected_board_num()
        if board_num is None:
            QMessageBox.information(self, "Replay", "Select a board row first.")
            return
        self.contract_clicked.emit(board_num, BenTable.OPEN)

    def _populate_table(self):
        """Fill the table with match results."""
        results = self._get_results()
        self.table.setRowCount(len(results))

        for row, result in enumerate(results):
            board_num = result['board_num']
            open_run = result.get('open')
            closed_run = result.get('closed')
            imp_swing = result.get('imp_swing')

            # Board number (center column)
            board_item = QTableWidgetItem(str(board_num))
            board_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            board_item.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            self.table.setItem(row, 3, board_item)

            # Open Room results
            if open_run and open_run.played:
                contract_str = self._format_contract(open_run)
                open_contract = QTableWidgetItem(contract_str)
                open_contract.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                open_contract.setForeground(QColor("#0000aa"))
                open_contract.setData(Qt.ItemDataRole.UserRole, (board_num, BenTable.OPEN))
                self.table.setItem(row, 0, open_contract)

                open_ns = QTableWidgetItem(self._format_score(open_run.ns_score))
                open_ns.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._color_score(open_ns, open_run.ns_score)
                self.table.setItem(row, 1, open_ns)
            else:
                self.table.setItem(row, 0, QTableWidgetItem("-"))
                self.table.setItem(row, 1, QTableWidgetItem("-"))

            # Closed Room results
            if closed_run and closed_run.played:
                contract_str = self._format_contract(closed_run)
                closed_contract = QTableWidgetItem(contract_str)
                closed_contract.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                closed_contract.setForeground(QColor("#0000aa"))
                closed_contract.setData(Qt.ItemDataRole.UserRole, (board_num, BenTable.CLOSED))
                self.table.setItem(row, 4, closed_contract)

                closed_ns = QTableWidgetItem(self._format_score(closed_run.ns_score))
                closed_ns.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._color_score(closed_ns, closed_run.ns_score)
                self.table.setItem(row, 5, closed_ns)
            else:
                pending = QTableWidgetItem("(pending)")
                pending.setForeground(QColor("#808080"))
                self.table.setItem(row, 4, pending)
                self.table.setItem(row, 5, QTableWidgetItem("-"))

            # IMP columns
            if imp_swing is not None:
                # Open room IMP (N/S perspective)
                if imp_swing >= 0:
                    open_imp = QTableWidgetItem(f"+{imp_swing}" if imp_swing > 0 else "0")
                    open_imp.setForeground(QColor("#00aa00") if imp_swing > 0 else QColor("#000000"))
                    closed_imp = QTableWidgetItem("")
                else:
                    open_imp = QTableWidgetItem("")
                    closed_imp = QTableWidgetItem(f"+{-imp_swing}")
                    closed_imp.setForeground(QColor("#00aa00"))

                open_imp.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                closed_imp.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 2, open_imp)
                self.table.setItem(row, 6, closed_imp)
            else:
                self.table.setItem(row, 2, QTableWidgetItem(""))
                self.table.setItem(row, 6, QTableWidgetItem(""))

    def _get_results(self) -> List[Dict]:
        """Get results sorted by board number."""
        results = []
        # Use actual board numbers from board_runs, not assumed 1 to num_boards
        board_nums = sorted(self.match.board_runs.keys())
        for board_num in board_nums:
            runs = self.match.board_runs.get(board_num, {})
            open_run = runs.get(BenTable.OPEN)
            closed_run = runs.get(BenTable.CLOSED)

            # Calculate IMP swing if both rooms complete
            imp_swing = None
            if open_run and open_run.played and closed_run and closed_run.played:
                imp_swing = self.match.get_imp_swing(board_num)

            results.append({
                'board_num': board_num,
                'open': open_run,
                'closed': closed_run,
                'imp_swing': imp_swing
            })

        return results

    def _format_contract(self, run: BenBoardRun) -> str:
        """Format contract for display."""
        if not run.contract:
            return "Passed"

        contract = run.contract
        suit_symbols = {0: '\u2660', 1: '\u2665', 2: '\u2666', 3: '\u2663', 4: 'NT'}
        suit_sym = suit_symbols.get(contract.suit.value, '?')

        result = f"{contract.level}{suit_sym}"
        if contract.redoubled:
            result += "XX"
        elif contract.doubled:
            result += "X"

        result += f" {contract.declarer.to_char()}"

        # Add result
        target = contract.target_tricks()
        diff = run.declarer_tricks - target
        if diff == 0:
            result += " ="
        elif diff > 0:
            result += f" +{diff}"
        else:
            result += f" {diff}"

        return result

    def _format_score(self, score: int) -> str:
        """Format score for display."""
        if score == 0:
            return "0"
        return f"{score:+d}"

    def _color_score(self, item: QTableWidgetItem, score: int):
        """Apply color to score based on value."""
        if score > 0:
            item.setForeground(QColor("#00aa00"))
        elif score < 0:
            item.setForeground(QColor("#cc0000"))

    def _on_cell_clicked(self, row: int, col: int):
        """Handle cell click - emit signal for contract cells."""
        if col in (0, 4):  # Contract columns
            item = self.table.item(row, col)
            if item:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    board_num, table = data
                    self.contract_clicked.emit(board_num, table)

    def _on_export(self):
        """Export results to file."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "",
            "Text Files (*.txt);;CSV Files (*.csv);;All Files (*)"
        )

        if not filename:
            return

        try:
            with open(filename, 'w') as f:
                f.write(f"Teams Match Results\n")
                f.write(f"N/S Team: {self.match.ns_team_name}\n")
                f.write(f"E/W Team: {self.match.ew_team_name}\n")
                f.write(f"\n")

                ns_imps, ew_imps = self.match.get_total_imps()
                f.write(f"Final Score: {ns_imps} - {ew_imps} IMPs\n")
                f.write(f"\n")

                f.write(f"{'Board':>5} | {'Open Contract':>15} | {'Open N/S':>8} | "
                       f"{'Closed Contract':>15} | {'Closed N/S':>9} | {'IMP':>5}\n")
                f.write("-" * 70 + "\n")

                for result in self._get_results():
                    board_num = result['board_num']
                    open_run = result.get('open')
                    closed_run = result.get('closed')
                    imp_swing = result.get('imp_swing')

                    open_contract = self._format_contract(open_run) if open_run and open_run.played else "-"
                    open_ns = self._format_score(open_run.ns_score) if open_run and open_run.played else "-"
                    closed_contract = self._format_contract(closed_run) if closed_run and closed_run.played else "-"
                    closed_ns = self._format_score(closed_run.ns_score) if closed_run and closed_run.played else "-"
                    imp_str = f"{imp_swing:+d}" if imp_swing is not None else "-"

                    f.write(f"{board_num:>5} | {open_contract:>15} | {open_ns:>8} | "
                           f"{closed_contract:>15} | {closed_ns:>9} | {imp_str:>5}\n")

            QMessageBox.information(self, "Export Complete",
                                   f"Results exported to {filename}")

        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Could not export: {e}")

    def refresh(self):
        """Refresh the table with current data."""
        self._populate_table()

        # Update header totals
        ns_imps, ew_imps = self.match.get_total_imps()
        # Would need to store reference to total_label to update it
