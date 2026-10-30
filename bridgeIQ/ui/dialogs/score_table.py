"""
Score Table Dialog
Display and manage session scores.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QAbstractItemView, QComboBox,
    QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .dialog_style import apply_dialog_style


class ScoreTableDialog(QDialog):
    """
    Dialog showing score table for the session.
    Supports Rubber, IMP, and Matchpoint scoring.
    """

    def __init__(self, parent=None, scoring_type='imp'):
        super().__init__(parent)
        self.setWindowTitle("Score Table")
        self.setMinimumSize(600, 400)
        apply_dialog_style(self)

        self.scoring_type = scoring_type
        self.scores = []

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Header with scoring type selection
        header_layout = QHBoxLayout()

        header_layout.addWidget(QLabel("Scoring:"))
        self.scoring_combo = QComboBox()
        self.scoring_combo.addItems(["IMP", "Matchpoints", "Rubber"])
        self.scoring_combo.currentTextChanged.connect(self._on_scoring_changed)
        header_layout.addWidget(self.scoring_combo)

        header_layout.addStretch()

        self.total_label = QLabel("Total: N-S: 0 | E-W: 0")
        self.total_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        header_layout.addWidget(self.total_label)

        layout.addLayout(header_layout)

        # Score table
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self._setup_table_columns()

        layout.addWidget(self.table)

        # Summary section
        summary_layout = QHBoxLayout()

        self.ns_total = QLabel("N-S Total: 0")
        self.ns_total.setStyleSheet("color: blue; font-weight: bold;")
        summary_layout.addWidget(self.ns_total)

        summary_layout.addStretch()

        self.ew_total = QLabel("E-W Total: 0")
        self.ew_total.setStyleSheet("color: red; font-weight: bold;")
        summary_layout.addWidget(self.ew_total)

        layout.addLayout(summary_layout)

        # Buttons
        button_layout = QHBoxLayout()

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._on_clear)
        button_layout.addWidget(clear_btn)

        export_btn = QPushButton("Export...")
        export_btn.clicked.connect(self._on_export)
        button_layout.addWidget(export_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        # Add sample data for demonstration
        self._add_sample_data()

    def _setup_table_columns(self):
        """Setup table columns based on scoring type"""
        if self.scoring_type == 'imp':
            self.table.setColumnCount(8)
            self.table.setHorizontalHeaderLabels([
                "Board", "Contract", "By", "Result",
                "N-S Score", "E-W Score", "IMP N-S", "IMP E-W"
            ])
        elif self.scoring_type == 'mp':
            self.table.setColumnCount(7)
            self.table.setHorizontalHeaderLabels([
                "Board", "Contract", "By", "Result",
                "N-S Score", "MP N-S", "MP E-W"
            ])
        else:  # rubber
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels([
                "Board", "Contract", "By", "Result",
                "Above Line", "Below Line"
            ])

    def _on_scoring_changed(self, text: str):
        self.scoring_type = text.lower()
        self._setup_table_columns()
        self._refresh_table()

    def _add_sample_data(self):
        """Add sample score data for demonstration"""
        self.scores = [
            {'board': 1, 'contract': '4H', 'by': 'S', 'result': '=',
             'ns_score': 420, 'ew_score': 0, 'imp_ns': 10, 'imp_ew': 0},
            {'board': 2, 'contract': '3NT', 'by': 'W', 'result': '-1',
             'ns_score': 100, 'ew_score': 0, 'imp_ns': 3, 'imp_ew': 0},
            {'board': 3, 'contract': '2S', 'by': 'N', 'result': '+1',
             'ns_score': 140, 'ew_score': 0, 'imp_ns': 0, 'imp_ew': 0},
        ]
        self._refresh_table()

    def _refresh_table(self):
        """Refresh table with current scores"""
        self.table.setRowCount(len(self.scores))

        ns_total = 0
        ew_total = 0

        for row, score in enumerate(self.scores):
            self.table.setItem(row, 0, QTableWidgetItem(str(score['board'])))
            self.table.setItem(row, 1, QTableWidgetItem(score['contract']))
            self.table.setItem(row, 2, QTableWidgetItem(score['by']))
            self.table.setItem(row, 3, QTableWidgetItem(score['result']))

            if self.scoring_type == 'imp':
                self.table.setItem(row, 4, QTableWidgetItem(str(score['ns_score'])))
                self.table.setItem(row, 5, QTableWidgetItem(str(score['ew_score'])))
                self.table.setItem(row, 6, QTableWidgetItem(str(score['imp_ns'])))
                self.table.setItem(row, 7, QTableWidgetItem(str(score['imp_ew'])))

                ns_total += score['imp_ns']
                ew_total += score['imp_ew']

        self.ns_total.setText(f"N-S Total: {ns_total}")
        self.ew_total.setText(f"E-W Total: {ew_total}")
        self.total_label.setText(f"Total: N-S: {ns_total} | E-W: {ew_total}")

    def add_score(self, score: dict):
        """Add a new score entry"""
        self.scores.append(score)
        self._refresh_table()

    def _on_clear(self):
        """Clear all scores"""
        self.scores.clear()
        self._refresh_table()

    def _on_export(self):
        """Export scores to HTML file"""
        if not self.scores:
            QMessageBox.information(self, "No Data", "No scores to export.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Scores", "scores.html",
            "HTML Files (*.html);;All Files (*)"
        )
        if not filename:
            return

        if not filename.endswith('.html'):
            filename += '.html'

        try:
            html = self._generate_html()
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(html)
            QMessageBox.information(self, "Export Complete",
                                   f"Scores exported to:\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error",
                                f"Failed to export scores:\n{e}")

    def _generate_html(self) -> str:
        """Generate HTML content for the score table"""
        # Determine columns and totals based on scoring type
        if self.scoring_type == 'imp':
            headers = ["Board", "Contract", "By", "Result",
                       "N-S Score", "E-W Score", "IMP N-S", "IMP E-W"]
        elif self.scoring_type in ('mp', 'matchpoints'):
            headers = ["Board", "Contract", "By", "Result",
                       "N-S Score", "MP N-S", "MP E-W"]
        else:  # rubber
            headers = ["Board", "Contract", "By", "Result",
                       "Above Line", "Below Line"]

        ns_total = 0
        ew_total = 0

        rows_html = ""
        for score in self.scores:
            row = "<tr>"
            row += f"<td>{score.get('board', '')}</td>"
            row += f"<td>{score.get('contract', '')}</td>"
            row += f"<td>{score.get('by', '')}</td>"
            row += f"<td>{score.get('result', '')}</td>"

            if self.scoring_type == 'imp':
                row += f"<td>{score.get('ns_score', 0)}</td>"
                row += f"<td>{score.get('ew_score', 0)}</td>"
                row += f"<td>{score.get('imp_ns', 0)}</td>"
                row += f"<td>{score.get('imp_ew', 0)}</td>"
                ns_total += score.get('imp_ns', 0)
                ew_total += score.get('imp_ew', 0)
            elif self.scoring_type in ('mp', 'matchpoints'):
                row += f"<td>{score.get('ns_score', 0)}</td>"
                row += f"<td>{score.get('mp_ns', 0)}</td>"
                row += f"<td>{score.get('mp_ew', 0)}</td>"
                ns_total += score.get('mp_ns', 0)
                ew_total += score.get('mp_ew', 0)
            else:
                row += f"<td>{score.get('above_line', 0)}</td>"
                row += f"<td>{score.get('below_line', 0)}</td>"
                ns_total += score.get('above_line', 0)
                ew_total += score.get('below_line', 0)

            row += "</tr>"
            rows_html += row + "\n"

        header_cells = "".join(f"<th>{h}</th>" for h in headers)
        scoring_names = {
            'imp': 'IMP', 'mp': 'Matchpoints',
            'matchpoints': 'Matchpoints', 'rubber': 'Rubber'
        }
        scoring_name = scoring_names.get(self.scoring_type, self.scoring_type.upper())

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>BridgeIQ - Score Table</title>
<style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
    h1 {{ color: #1a3a5c; }}
    h2 {{ color: #4a7c8a; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 900px;
            margin: 15px 0; background: white; }}
    th {{ background-color: #4a7c8a; color: white; padding: 10px 12px;
         text-align: center; }}
    td {{ padding: 8px 12px; text-align: center; border-bottom: 1px solid #ddd; }}
    tr:nth-child(even) {{ background-color: #f0f5f8; }}
    tr:hover {{ background-color: #e0e8f0; }}
    .summary {{ margin-top: 15px; font-size: 1.1em; }}
    .ns {{ color: #0066cc; font-weight: bold; }}
    .ew {{ color: #cc0000; font-weight: bold; }}
    .footer {{ margin-top: 30px; color: #888; font-size: 0.9em; }}
</style>
</head><body>
<h1>BridgeIQ - Score Table</h1>
<h2>Scoring: {scoring_name}</h2>
<table>
<thead><tr>{header_cells}</tr></thead>
<tbody>
{rows_html}</tbody>
</table>
<div class="summary">
    <span class="ns">N-S Total: {ns_total}</span> &nbsp;&nbsp;|&nbsp;&nbsp;
    <span class="ew">E-W Total: {ew_total}</span>
</div>
<div class="footer">
    Generated by BridgeIQ &bull; {len(self.scores)} board(s)
</div>
</body></html>"""
