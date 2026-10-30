"""
Scoring Table Dialog - Display and manage match/session scores.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QFileDialog, QMessageBox,
    QGroupBox, QGridLayout, QComboBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

from backend.scoring import ScoringTable, ScoringType, BoardResult
from typing import Optional

from .dialog_style import apply_dialog_style


class ScoringTableDialog(QDialog):
    """Dialog for viewing and managing scoring table."""

    # View modes (Q-Plus .score-match-{t,p,t3,tc}).
    VIEW_IMP = "Teams IMP (open vs closed)"
    VIEW_MP = "Pairs MP"
    VIEW_CROSS_IMP = "Cross-IMP"
    VIEW_3WAY = "3-way Teams"

    def __init__(self, scoring_table: Optional[ScoringTable] = None, parent=None):
        super().__init__(parent)
        self.scoring_table = scoring_table or ScoringTable()
        self._view_mode = self.VIEW_IMP

        self.setWindowTitle("Scoring Table")
        self.setMinimumWidth(950)
        self.setMinimumHeight(550)
        self.resize(1050, 600)
        apply_dialog_style(self)
        self.setStyleSheet(self.styleSheet() + """
            QLabel { font-size: 14px; }
            QTableWidget { font-size: 13px; }
            QPushButton { font-size: 13px; padding: 6px 12px; }
            QGroupBox { font-size: 14px; }
        """)
        self._setup_ui()
        self._populate_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Summary group
        summary_group = QGroupBox("Session Summary")
        summary_layout = QGridLayout()

        self.scoring_type_label = QLabel(f"Scoring: {self.scoring_table.scoring_type.value}")
        summary_layout.addWidget(self.scoring_type_label, 0, 0)

        self.boards_label = QLabel(f"Boards: {len(self.scoring_table.results)}")
        summary_layout.addWidget(self.boards_label, 0, 1)

        # Running IMP total — the cumulative IMP swing across every
        # paired board in the session, signed from N/S's perspective.
        # This is the "how is the human doing" number; the per-board
        # IMP columns above split it by row.
        self.ns_total_label = QLabel("Running IMPs (N/S): 0")
        self.ns_total_label.setToolTip(
            "Cumulative IMP swing across every board with both an open- "
            "and closed-room result, signed from N/S's perspective."
        )
        summary_layout.addWidget(self.ns_total_label, 0, 2)

        self.ew_total_label = QLabel("Running IMPs (E/W): 0")
        self.ew_total_label.setToolTip(
            "Same running total, viewed from E/W (the negative of the N/S figure)."
        )
        summary_layout.addWidget(self.ew_total_label, 0, 3)

        # View-mode combo (Q-Plus .score-match-{t,p,t3,tc}). Switches
        # the right-hand pair of columns between IMPs, matchpoints,
        # cross-IMPs, and 3-way teams without dropping the underlying
        # rows.
        summary_layout.addWidget(QLabel("View:"), 1, 0)
        self.view_combo = QComboBox()
        self.view_combo.addItems([
            self.VIEW_IMP, self.VIEW_MP,
            self.VIEW_CROSS_IMP, self.VIEW_3WAY,
        ])
        self.view_combo.currentTextChanged.connect(self._on_view_changed)
        summary_layout.addWidget(self.view_combo, 1, 1, 1, 3)

        summary_group.setLayout(summary_layout)
        layout.addWidget(summary_group)

        # Results table (click a row to review that hand)
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._setup_table_columns()
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        # Side-by-side comparison of open + closed runs for the selected row.
        # Disabled until the selection has both an open-room and a closed-room
        # BenBoardRun; selection-change handler updates state.
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setToolTip(
            "Side-by-side replay of open vs closed room (needs both runs)."
        )
        self.compare_btn.setEnabled(False)
        self.compare_btn.clicked.connect(self._on_compare)
        button_layout.addWidget(self.compare_btn)

        # Single-row replay (kept alongside Compare so users can still
        # walk through a single run independently).
        self.replay_btn = QPushButton("Replay")
        self.replay_btn.setToolTip("Replay the selected open or closed run on its own.")
        self.replay_btn.setEnabled(False)
        self.replay_btn.clicked.connect(self._on_replay_selected)
        button_layout.addWidget(self.replay_btn)

        self.table.itemSelectionChanged.connect(self._refresh_action_buttons)

        save_btn = QPushButton("Save...")
        save_btn.clicked.connect(self._on_save)
        button_layout.addWidget(save_btn)

        load_btn = QPushButton("Load...")
        load_btn.clicked.connect(self._on_load)
        button_layout.addWidget(load_btn)

        retrieve_btn = QPushButton("Retrieve...")
        retrieve_btn.setToolTip(
            "Pick from previously saved scoring tables under "
            "DATA/LOCAL-MATCHES.")
        retrieve_btn.clicked.connect(self._on_retrieve)
        button_layout.addWidget(retrieve_btn)

        names_btn = QPushButton("Edit Names...")
        names_btn.setToolTip(
            "Set player names for the four seats and team labels — "
            "appears in saved tables and HTML export.")
        names_btn.clicked.connect(self._on_edit_names)
        button_layout.addWidget(names_btn)

        export_html_btn = QPushButton("Export HTML...")
        export_html_btn.clicked.connect(self._on_export_html)
        button_layout.addWidget(export_html_btn)

        print_btn = QPushButton("Print...")
        print_btn.clicked.connect(self._on_print)
        button_layout.addWidget(print_btn)

        button_layout.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._on_clear)
        button_layout.addWidget(clear_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _on_view_changed(self, mode: str):
        self._view_mode = mode
        self._setup_table_columns()
        self._populate_table()

    def _setup_table_columns(self):
        """Setup table columns — Q-Plus style with open room (left) and closed room (right).

        Column meanings — kept here in one place so the UI labels and the
        tooltips stay in sync. "Open" = the human's table, "Closed" = the
        same deal replayed by the engine or ingested from a database.
        The third-column-from-right label flexes by view mode: IMPs,
        matchpoints, cross-IMPs or 3-way IMPs.
        """
        # The "Board" column intentionally carries dealer + vulnerability
        # alongside the bare number, so the user doesn't have to mentally
        # apply the duplicate-bridge 16-board cycle to know who dealt
        # and who was vulnerable. Format: "816  Dlr W  Vul E-W".
        if self._view_mode == self.VIEW_MP:
            metric_open = "Open MP"
            metric_closed = "Closed MP"
            metric_tip_open = (
                "Matchpoints earned at the open table — half-credit "
                "for ties.")
            metric_tip_closed = (
                "Matchpoints earned at the closed table.")
        elif self._view_mode == self.VIEW_CROSS_IMP:
            metric_open = "Open xIMP"
            metric_closed = "Closed xIMP"
            metric_tip_open = (
                "Cross-IMPs: this NS score against the average NS "
                "score from every other row on the same board.")
            metric_tip_closed = (
                "Closed-room cross-IMPs (mirror of the open xIMP "
                "from N/S's perspective).")
        elif self._view_mode == self.VIEW_3WAY:
            metric_open = "Open IMP-3w"
            metric_closed = "Closed IMP-3w"
            metric_tip_open = (
                "3-way Teams: pair the open NS score against each "
                "other table on the same board, IMP-convert the "
                "differences, and average them.")
            metric_tip_closed = (
                "Mirror of the 3-way open figure from N/S's view.")
        else:
            metric_open = "Open IMP"
            metric_closed = "Closed IMP"
            metric_tip_open = (
                "IMPs scored at the open table, signed from N/S's "
                "perspective.")
            metric_tip_closed = (
                "IMPs scored at the closed table, signed from N/S's "
                "perspective.")
        columns = [
            "Open: Contract", "Open N/S", metric_open,
            "Board / Dlr / Vul",
            "Closed: Contract", "Closed N/S", metric_closed,
        ]
        tooltips = [
            "Contract bid at the open (human) table, with overtricks/undertricks.",
            "N/S score from the open table (positive = N/S earned points).",
            metric_tip_open,
            "Board number, with dealer and vulnerability spelled out so you "
            "don't have to apply the 16-board cycle by hand.",
            "Contract bid at the closed (AI / database) table.",
            "N/S score from the closed table.",
            metric_tip_closed,
        ]

        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        for i, tip in enumerate(tooltips):
            item = self.table.horizontalHeaderItem(i)
            if item is not None:
                item.setToolTip(tip)

        header = self.table.horizontalHeader()
        for i in range(len(columns)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

    def _populate_table(self):
        """Populate table — one row per board, open room left, closed room right."""
        self.table.setRowCount(0)
        self._board_rows = {}  # Map row -> (open_result, closed_result)

        ns_imps_total = 0

        # Group results by board, separating open and closed room. Rows keep
        # the order the boards were played (random deals carry random board
        # numbers, so sorting by number dropped a new board mid-table), and a
        # board number that comes round again starts a new row.
        groups = []
        latest = {}
        for result in self.scoring_table.results:
            bn = result.board_number
            closed = bool(result.notes and "Closed room" in result.notes)
            g = latest.get(bn)
            if g is None or (not closed and g['open'] is not None
                             and g['closed'] is not None):
                g = {'open': None, 'closed': None}
                groups.append(g)
                latest[bn] = g
            if closed:
                g['closed'] = result
            elif g['open'] is None:
                g['open'] = result
            else:
                g['closed'] = result

        num_boards = 0
        for g in groups:
            open_r = g['open']
            closed_r = g['closed']
            bn = (open_r or closed_r).board_number
            if not open_r and not closed_r:
                continue

            num_boards += 1
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._board_rows[row] = (open_r, closed_r)

            # --- Left: Open room (human) ---
            if open_r is not None:
                contract_str = self._format_contract(open_r)
                diff = self._format_diff(open_r)
                self.table.setItem(row, 0, QTableWidgetItem(f"{contract_str} {diff}"))
                ns_item = self._colored_item(open_r.ns_score)
                self.table.setItem(row, 1, ns_item)

                metric_val = self._row_metric(open_r)
                if isinstance(metric_val, int):
                    ns_imps_total += metric_val
                self.table.setItem(
                    row, 2, self._metric_item(metric_val))
            else:
                # Closed-room-only row (e.g. ingested Q-Plus result with
                # no matching bridgeIQ open-room hand). Leave the open
                # cells blank rather than dropping the row entirely.
                for col in (0, 1, 2):
                    self.table.setItem(row, col, QTableWidgetItem(""))

            # --- Center: Board number + dealer + vulnerability ---
            # Pull dealer / vul from the result if it carries them
            # (open-room rows do); otherwise derive from the board
            # number using the duplicate cycle.
            ref = open_r if open_r is not None else closed_r
            dealer_char = ""
            vul_str = ""
            if ref is not None and getattr(ref, 'dealer', None):
                try:
                    dealer_char = ref.dealer.to_char()
                except Exception:
                    dealer_char = ""
            if ref is not None and getattr(ref, 'vulnerability', None):
                vul_map = {
                    "NONE": "None", "NS": "N-S", "EW": "E-W", "BOTH": "Both"
                }
                try:
                    vul_str = vul_map.get(ref.vulnerability.name, "None")
                except Exception:
                    vul_str = ""
            if not dealer_char or not vul_str:
                # Fallback: derive from board number's standard cycle.
                try:
                    from backend.models import BoardState
                    d, v = BoardState._board_dealer_vuln(bn)
                    if not dealer_char:
                        dealer_char = d.to_char()
                    if not vul_str:
                        vul_str = {"NONE": "None", "NS": "N-S",
                                   "EW": "E-W", "BOTH": "Both"
                                   }.get(v.name, "None")
                except Exception:
                    pass
            board_text = f"{bn}  Dlr {dealer_char}  Vul {vul_str}".strip()
            board_item = QTableWidgetItem(board_text)
            board_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            board_item.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            board_item.setToolTip(
                f"Board {bn} — dealer {dealer_char or '?'}, "
                f"vulnerability {vul_str or '?'}"
            )
            self.table.setItem(row, 3, board_item)

            # --- Right: Closed room (AI) ---
            if closed_r:
                contract_str2 = self._format_contract(closed_r)
                diff2 = self._format_diff(closed_r)
                self.table.setItem(row, 4, QTableWidgetItem(f"{contract_str2} {diff2}"))
                ns_item2 = self._colored_item(closed_r.ns_score)
                self.table.setItem(row, 5, ns_item2)
                closed_metric = self._row_metric(closed_r)
                self.table.setItem(
                    row, 6, self._metric_item(closed_metric))
            else:
                for c in (4, 5, 6):
                    self.table.setItem(row, c, QTableWidgetItem(""))

        # Update summary — running totals across the whole session.
        self.boards_label.setText(f"Boards: {num_boards}")
        self.ns_total_label.setText(f"Running IMPs (N/S): {ns_imps_total:+d}")
        self.ew_total_label.setText(f"Running IMPs (E/W): {-ns_imps_total:+d}")

        # Compare / Replay buttons start in "no selection" state; the
        # selection-changed handler will turn them on if the user picks
        # a row with the right runs attached.
        if hasattr(self, 'compare_btn'):
            self._refresh_action_buttons()

    def _row_metric(self, r):
        """Return the metric value to display for ``r`` under the
        current view mode. May be None (no metric available) or a
        numeric int/float."""
        if self._view_mode == self.VIEW_MP:
            return r.matchpoints
        if self._view_mode == self.VIEW_CROSS_IMP:
            return self.scoring_table.cross_imps_for(r)
        if self._view_mode == self.VIEW_3WAY:
            # 3-way = average of the pairwise IMP-converted diffs
            # against every other table on the same board.
            from backend.scoring import diff_to_imps
            same = [x for x in self.scoring_table.results
                    if x.board_number == r.board_number and x is not r]
            if not same:
                return None
            imps = [diff_to_imps(r.ns_score - x.ns_score) for x in same]
            return round(sum(imps) / len(imps), 1)
        return r.imps

    def _metric_item(self, value):
        if value is None or value == "":
            return QTableWidgetItem("")
        if isinstance(value, int):
            return self._colored_item(value, fmt="+d")
        return self._colored_item(value, fmt="+.1f")

    def _format_contract(self, result):
        if not result or not result.contract:
            return "Pass"
        decl = result.declarer.to_char() if result.declarer else ""
        return f"{decl} {result.contract.to_str()}"

    def _format_diff(self, result):
        if not result or not result.contract:
            return ""
        target = result.contract.target_tricks()
        diff = result.tricks_made - target
        if diff > 0: return f"+{diff}"
        elif diff == 0: return "="
        return str(diff)

    def _colored_item(self, value, fmt="d"):
        if value is None:
            return QTableWidgetItem("")
        if fmt in ("+d", "+.1f", "+.2f"):
            try:
                text = f"{value:{fmt}}"
            except (ValueError, TypeError):
                text = str(value)
        else:
            text = str(value)
        item = QTableWidgetItem(text)
        if value > 0:
            item.setForeground(QColor(0, 128, 0))
        elif value < 0:
            item.setForeground(QColor(192, 0, 0))
        return item

    def add_result(self, result: BoardResult):
        """Add a new result to the table."""
        self.scoring_table.add_result(result)
        self._populate_table()

    def _on_save(self):
        """Save scoring table to file."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Scoring Table", "",
            "BridgeIQ Score Sheet (*.qss);;All Files (*)"
        )
        if filename:
            if not filename.endswith('.qss'):
                filename += '.qss'
            try:
                self.scoring_table.save(filename)
                QMessageBox.information(self, "Saved", f"Scoring table saved to {filename}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not save: {e}")

    def _on_load(self):
        """Load scoring table from file."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load Scoring Table", "",
            "BridgeIQ Score Sheet (*.qss);;All Files (*)"
        )
        if filename:
            try:
                self.scoring_table = ScoringTable.load(filename)
                self._setup_table_columns()
                self._populate_table()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not load: {e}")

    def _on_retrieve(self):
        """Pick from previously saved scoring tables. Q-Plus
        .scoretab-retrieve / .scoretab-select — replaces the
        file-by-file dialog with a list of every .qss in
        DATA/LOCAL-MATCHES, showing date + player names + result
        count so the user can pick the right session quickly."""
        from pathlib import Path
        from PyQt6.QtWidgets import QInputDialog
        base = Path(__file__).resolve().parent.parent.parent / 'DATA' / 'LOCAL-MATCHES'
        files = sorted(base.glob('*.qss')) if base.exists() else []
        if not files:
            QMessageBox.information(
                self, "Retrieve Scoring Table",
                "No saved tables under DATA/LOCAL-MATCHES yet. "
                "Use Save... to create one.")
            return
        labels = []
        index = []
        for f in files:
            try:
                t = ScoringTable.load(str(f))
                ns = t.player_names.get('NS', '') if t.player_names else ''
                ew = t.player_names.get('EW', '') if t.player_names else ''
                tag = ''
                if ns or ew:
                    tag = f" — {ns or '?'} vs {ew or '?'}"
                date = t.date or ''
                labels.append(
                    f"{f.name}{tag}  ({len(t.results)} rows"
                    + (f", {date}" if date else "") + ")")
                index.append(f)
            except Exception:
                labels.append(f"{f.name}  (unreadable)")
                index.append(f)
        choice, ok = QInputDialog.getItem(
            self, "Retrieve Scoring Table",
            "Pick a saved table:", labels, 0, False)
        if not ok or not choice:
            return
        chosen = index[labels.index(choice)]
        try:
            self.scoring_table = ScoringTable.load(str(chosen))
            self._setup_table_columns()
            self._populate_table()
        except Exception as e:
            QMessageBox.warning(
                self, "Retrieve failed",
                f"Could not load {chosen.name}: {e}")

    def _on_edit_names(self):
        """Pop a small dialog letting the user edit the four seat
        names and the NS / EW team labels (Q-Plus
        .score-edit-names). The names live on the ScoringTable so
        they round-trip through save / load and are visible in
        HTML export."""
        from PyQt6.QtWidgets import (
            QDialog as _QDialog, QFormLayout, QLineEdit, QDialogButtonBox)
        dlg = _QDialog(self)
        dlg.setWindowTitle("Player Names")
        form = QFormLayout(dlg)
        edits = {}
        labels = [
            ('N', 'North'), ('E', 'East'), ('S', 'South'), ('W', 'West'),
            ('NS', 'N/S team'), ('EW', 'E/W team'),
        ]
        current = (self.scoring_table.player_names or {})
        for key, label in labels:
            e = QLineEdit()
            e.setText(current.get(key, ''))
            edits[key] = e
            form.addRow(label + ":", e)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != _QDialog.DialogCode.Accepted:
            return
        if not self.scoring_table.player_names:
            self.scoring_table.player_names = {
                'N': '', 'E': '', 'S': '', 'W': '',
                'NS': '', 'EW': '',
            }
        for key in edits:
            self.scoring_table.player_names[key] = edits[key].text().strip()
        self._populate_table()

    def _on_export_html(self):
        """Export scoring table to HTML."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export to HTML", "",
            "HTML Files (*.html);;All Files (*)"
        )
        if filename:
            if not filename.endswith('.html'):
                filename += '.html'
            try:
                self._export_html(filename)
                QMessageBox.information(self, "Exported", f"Exported to {filename}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not export: {e}")

    def _export_html(self, filename: str):
        """Generate HTML export."""
        html = [
            "<!DOCTYPE html>",
            "<html><head>",
            "<title>BridgeIQ Scoring Table</title>",
            "<style>",
            "body { font-family: Arial, sans-serif; margin: 20px; }",
            "table { border-collapse: collapse; width: 100%; }",
            "th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }",
            "th { background-color: #4CAF50; color: white; }",
            "tr:nth-child(even) { background-color: #f2f2f2; }",
            ".positive { color: green; }",
            ".negative { color: red; }",
            "</style>",
            "</head><body>",
            f"<h1>BridgeIQ Scoring Table</h1>",
            f"<p>Scoring: {self.scoring_table.scoring_type.value}</p>",
            f"<p>Boards: {len(self.scoring_table.results)}</p>",
            "<table>",
            "<tr><th>Board</th><th>Contract</th><th>By</th><th>Result</th><th>N/S</th><th>E/W</th>",
        ]

        if self.scoring_table.scoring_type == ScoringType.TEAMS:
            html.append("<th>IMPs</th>")
        elif self.scoring_table.scoring_type == ScoringType.PAIRS:
            html.append("<th>MP</th>")

        html.append("</tr>")

        for r in self.scoring_table.results:
            contract = r.contract.to_str() if r.contract else "Passed"
            declarer = r.declarer.to_char() if r.declarer else ""
            result = str(r.tricks_made) if r.contract else ""

            ns_class = "positive" if r.ns_score > 0 else ("negative" if r.ns_score < 0 else "")
            ew_class = "positive" if r.ew_score > 0 else ("negative" if r.ew_score < 0 else "")

            html.append(f"<tr>")
            html.append(f"<td>{r.board_number}</td>")
            html.append(f"<td>{contract}</td>")
            html.append(f"<td>{declarer}</td>")
            html.append(f"<td>{result}</td>")
            html.append(f"<td class='{ns_class}'>{r.ns_score}</td>")
            html.append(f"<td class='{ew_class}'>{r.ew_score}</td>")

            if self.scoring_table.scoring_type == ScoringType.TEAMS and r.imps is not None:
                imp_class = "positive" if r.imps > 0 else ("negative" if r.imps < 0 else "")
                html.append(f"<td class='{imp_class}'>{r.imps:+d}</td>")
            elif self.scoring_table.scoring_type == ScoringType.PAIRS and r.matchpoints is not None:
                html.append(f"<td>{r.matchpoints:.1f}</td>")

            html.append("</tr>")

        html.extend([
            "</table>",
            f"<p><em>Generated by BridgeIQ</em></p>",
            "</body></html>"
        ])

        with open(filename, 'w') as f:
            f.write('\n'.join(html))

    def _on_print(self):
        """Print scoring table."""
        QMessageBox.information(self, "Print", "Print functionality not yet implemented.")

    def _on_clear(self):
        """Clear all results."""
        reply = QMessageBox.question(
            self, "Clear Results",
            "Are you sure you want to clear all results?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.scoring_table.results.clear()
            self._populate_table()

    def _refresh_action_buttons(self):
        """Update Compare/Replay enable state based on the current selection."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            self.compare_btn.setEnabled(False)
            self.replay_btn.setEnabled(False)
            return
        row = rows[0].row()
        open_r, closed_r = self._board_rows.get(row, (None, None))

        def has_run(r):
            return (r is not None
                    and r.board_run is not None
                    and getattr(r.board_run, 'played', False))

        self.replay_btn.setEnabled(has_run(open_r) or has_run(closed_r))
        self.compare_btn.setEnabled(has_run(open_r) and has_run(closed_r))

    def _on_compare(self):
        """Open side-by-side replay for the selected row."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        open_r, closed_r = self._board_rows.get(row, (None, None))
        if not (open_r and closed_r and open_r.board_run and closed_r.board_run):
            return
        try:
            from .compare_replay import CompareReplayDialog
            CompareReplayDialog(open_r.board_run, closed_r.board_run, parent=self).exec()
        except Exception as e:
            QMessageBox.warning(self, "Compare error", f"Could not open compare view: {e}")

    def _on_replay_selected(self):
        """Replay whichever side has a run on the selected row.

        If both sides have runs, prefers the open-room (consistent with
        the existing double-click behaviour on the left columns).
        """
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        open_r, closed_r = self._board_rows.get(row, (None, None))
        target = (open_r if (open_r and open_r.board_run
                             and getattr(open_r.board_run, 'played', False))
                  else closed_r)
        if not target or not target.board_run:
            return
        try:
            from .replay_view import ReplayViewDialog
            ReplayViewDialog(target.board_run, self).exec()
        except Exception as e:
            QMessageBox.warning(self, "Replay error", f"Could not open replay: {e}")

    def _on_row_double_clicked(self, row, col):
        """Double-click a row to review that hand with full replay.
        Left columns (0-2) → open room, right columns (4-6) → closed room."""
        if row not in self._board_rows:
            return

        open_r, closed_r = self._board_rows[row]
        # Pick open or closed based on which column was clicked
        if col >= 4 and closed_r is not None:
            result = closed_r
        else:
            result = open_r
        if result is None:
            return

        # If we have a BenBoardRun, show the full replay dialog
        if result.board_run is not None and hasattr(result.board_run, 'played') and result.board_run.played:
            try:
                from .replay_view import ReplayViewDialog
                dialog = ReplayViewDialog(result.board_run, self)
                dialog.exec()
                return
            except Exception as e:
                print(f"Replay error: {e}")

        # Fallback: show summary dialog with hand reconstruction from pavlicek_id
        contract_str = result.contract.to_str() if result.contract else "Passed out"
        declarer_str = result.declarer.to_char() if result.declarer else "—"
        target = result.contract.target_tricks() if result.contract else 0
        diff = result.tricks_made - target

        if diff > 0:
            result_text = f"made with {diff} overtrick{'s' if diff > 1 else ''}"
        elif diff == 0:
            result_text = "made exactly"
        else:
            result_text = f"down {abs(diff)}"

        review = QDialog(self)
        review.setWindowTitle(f"Board {result.board_number} — Review")
        review.setMinimumSize(550, 450)
        review.setStyleSheet("QDialog { background-color: #e8e8f0; color: #000; }")
        layout = QVBoxLayout(review)

        # Result summary (yellow highlight like Q-Plus)
        summary = QLabel(
            f"<div style='background-color: #ffffcc; padding: 15px; border: 1px solid #ccc; "
            f"border-radius: 5px; text-align: center;'>"
            f"<p style='font-size: 20px; font-weight: bold;'>Board {result.board_number}</p>"
            f"<p style='font-size: 18px;'>{declarer_str} {contract_str} — {result_text}</p>"
            f"<p style='font-size: 16px;'>N/S: {result.ns_score:+d}    E/W: {result.ew_score:+d}</p>"
            f"</div>"
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(summary)

        # Reconstruct and show hands from pavlicek_id
        try:
            from backend.pavlicek import parse_deal_number
            from backend.models import Seat, Suit
            hands = parse_deal_number(result.pavlicek_id)
            if hands:
                suit_symbols = {'SPADES': '♠', 'HEARTS': '♥', 'DIAMONDS': '♦', 'CLUBS': '♣'}
                hand_text = "<pre style='font-size: 14px; font-family: monospace;'>\n"
                for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
                    hand = hands.get(seat)
                    if hand:
                        suits = []
                        for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
                            cards = sorted([c for c in hand.cards if c.suit == suit],
                                           key=lambda c: c.rank, reverse=True)
                            sym = suit_symbols.get(suit.name, '?')
                            suits.append(f"{sym} {''.join(c.rank.to_char() for c in cards)}")
                        winner = " ★" if seat == result.declarer else ""
                        hand_text += f"  {seat.to_char()}: {' '.join(suits)}{winner}\n"
                hand_text += "</pre>"
                hands_label = QLabel(hand_text)
                hands_label.setTextFormat(Qt.TextFormat.RichText)
                hands_label.setStyleSheet("background-color: #fff; padding: 10px; "
                                          "border: 1px solid #aaa; border-radius: 3px;")
                layout.addWidget(hands_label)
        except Exception:
            pass

        # Details
        details = QLabel(
            f"<p>Deal ID: {result.pavlicek_id}    "
            f"Dealer: {result.dealer.to_char()}    "
            f"Vuln: {result.vulnerability.name}</p>"
        )
        details.setFont(QFont("Arial", 11))
        details.setStyleSheet("padding: 5px;")
        layout.addWidget(details)

        # Claude commentary intentionally omitted from this summary
        # view — the per-board hand log (View → Hand Log) shows Claude's
        # annotated BDL, which is the authoritative version. Earlier
        # versions duplicated a short blurb here from result.notes, but
        # that copy got out of sync with the BDL annotation and the user
        # was reading the less-accurate one. Drop it.

        close_btn = QPushButton("Close")
        close_btn.setFixedSize(100, 35)
        close_btn.clicked.connect(review.accept)
        layout.addWidget(close_btn)

        review.exec()
