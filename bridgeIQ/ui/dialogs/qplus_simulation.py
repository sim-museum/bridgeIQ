"""Q-Plus style bid simulation pop-up.

Reproduces the "Simulation for <Seat>" window from Q-Plus Bridge:
a table of candidate bids (Bid / Deals / Points) with Add Bids,
Clear Bids, Help, View, Close, a "with protocol" checkbox, a # spin
box for sample count, and a Simulation! button.

Pops up automatically when a slam opportunity is detected on the
user's turn (see backend.slam_opportunity.has_slam_opportunity) and
is also reachable from the View menu.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from backend.models import Bid, BoardState, Seat, Suit

from .dialog_style import apply_dialog_style, styled_info
from .simulation import SimulationWorker


# Slam-context candidate bids — full ladder from the current auction
# upward. The dialog will trim these to legal bids when populating
# Add Bids.
_ALL_BIDS_LADDER: List[str] = ["PASS", "X", "XX"] + [
    f"{lvl}{strain}"
    for lvl in range(1, 8)
    for strain in ("C", "D", "H", "S", "NT")
]


def _bid_to_display(bid_str: str) -> str:
    """Render a bid string the same way Q-Plus does in its grid."""
    if bid_str == "PASS":
        return "Pass"
    if bid_str == "X":
        return "Dbl"
    if bid_str == "XX":
        return "Rdbl"
    return bid_str


class AddBidsDialog(QDialog):
    """Checkbox-grid sub-dialog used by Add Bids."""

    def __init__(self, candidates: List[str], already: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Bids")
        apply_dialog_style(self)
        self._selected: List[str] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tick the bids to add to the simulation:"))

        grid = QGridLayout()
        self._checks: Dict[str, QCheckBox] = {}
        already_set = set(already)
        for i, b in enumerate(candidates):
            cb = QCheckBox(_bid_to_display(b))
            if b in already_set:
                cb.setChecked(True)
                cb.setEnabled(False)
            grid.addWidget(cb, i // 6, i % 6)
            self._checks[b] = cb
        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok = QPushButton("OK")
        ok.clicked.connect(self._on_ok)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(ok)
        btn_row.addWidget(cancel)
        layout.addLayout(btn_row)

    def _on_ok(self):
        self._selected = [b for b, cb in self._checks.items()
                          if cb.isChecked() and cb.isEnabled()]
        self.accept()

    def selected_bids(self) -> List[str]:
        return list(self._selected)


class QPlusSimulationDialog(QDialog):
    """Q-Plus-style simulation pop-up.

    Auto-opens on the user's turn when a slam opportunity is detected
    by backend.slam_opportunity.has_slam_opportunity. The user adds
    candidate bids, picks a sample size, hits Simulation!, and gets
    back per-bid Deals (samples completed) and Points (average score
    expressed in standard duplicate points). A "with protocol"
    checkbox stores a per-deal log accessible via View.
    """

    DEFAULT_SAMPLES = 25

    def __init__(self, engine, board: BoardState, seat: Seat, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.board = board
        self.seat = seat
        self.worker: Optional[SimulationWorker] = None
        self._protocol_lines: List[str] = []
        self._last_results: Dict[str, Dict] = {}

        self.setWindowTitle(f"Simulation for {seat.name.capitalize()}")
        self.setMinimumSize(420, 460)
        apply_dialog_style(self)

        self._setup_ui()
        self._seed_default_bids()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        outer = QVBoxLayout(self)

        # Column header row above the table — matches Q-Plus layout
        # (Bid / Deals / Points) sitting just below the title bar.
        header_row = QHBoxLayout()
        for text, stretch in (("Bid", 1), ("Deals", 1), ("Points", 2)):
            lbl = QLabel(text)
            f = QFont()
            f.setBold(True)
            lbl.setFont(f)
            header_row.addWidget(lbl, stretch)
        outer.addLayout(header_row)

        # Results table — three columns, no header (Q-Plus shows a
        # plain framed list).
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Bid", "Deals", "Points"])
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        outer.addWidget(self.table)

        # Add Bids / Clear Bids / Help row
        row1 = QHBoxLayout()
        self.add_bids_btn = QPushButton("Add Bids")
        self.add_bids_btn.clicked.connect(self._on_add_bids)
        row1.addWidget(self.add_bids_btn)

        self.clear_bids_btn = QPushButton("Clear Bids")
        self.clear_bids_btn.clicked.connect(self._on_clear_bids)
        row1.addWidget(self.clear_bids_btn)

        row1.addStretch()

        self.help_btn = QPushButton("Help")
        self.help_btn.clicked.connect(self._on_help)
        row1.addWidget(self.help_btn)
        outer.addLayout(row1)

        # "with protocol" + View row
        row2 = QHBoxLayout()
        self.protocol_check = QCheckBox("with protocol")
        row2.addWidget(self.protocol_check)
        row2.addStretch()
        self.view_btn = QPushButton("View")
        self.view_btn.clicked.connect(self._on_view)
        row2.addWidget(self.view_btn)
        outer.addLayout(row2)

        # # / Simulation! / Close row
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("#"))
        self.num_spin = QSpinBox()
        self.num_spin.setRange(1, 999)
        self.num_spin.setValue(self.DEFAULT_SAMPLES)
        self.num_spin.setFixedWidth(70)
        row3.addWidget(self.num_spin)

        self.sim_btn = QPushButton("Simulation!")
        self.sim_btn.clicked.connect(self._on_simulate)
        row3.addWidget(self.sim_btn)

        row3.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        row3.addWidget(self.close_btn)
        outer.addLayout(row3)

        # Progress bar (hidden until a run starts)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

    # ------------------------------------------------------------------
    # Candidate-bid list
    # ------------------------------------------------------------------

    def _legal_candidates(self) -> List[str]:
        """Return bids legal from the current auction state, in
        Q-Plus ordering (pass / double first, then ascending levels)."""
        last_level = 0
        last_strain = -1  # so any 1-level bid is legal initially
        for b in self.board.auction:
            if b.is_pass or b.is_double or b.is_redouble:
                continue
            last_level = b.level
            last_strain = int(b.suit) if b.suit is not None else int(Suit.NOTRUMP)

        out: List[str] = ["PASS"]
        # Double / redouble are situational; only offer if there's a
        # real bid to double.
        if last_level > 0:
            out.append("X")

        strain_order = [("C", 3), ("D", 2), ("H", 1), ("S", 0), ("NT", 4)]
        for lvl in range(1, 8):
            for strain, idx in strain_order:
                # Beat the last suited bid: higher level OR same level
                # with higher-ranking strain. Strain rank low→high:
                # C=0, D=1, H=2, S=3, NT=4.
                strain_rank = {"C": 0, "D": 1, "H": 2, "S": 3, "NT": 4}[strain]
                last_rank = {0: 3, 1: 2, 2: 1, 3: 0, 4: 4}.get(last_strain, -1)
                if lvl < last_level:
                    continue
                if lvl == last_level and strain_rank <= last_rank:
                    continue
                out.append(f"{lvl}{strain}")
        return out

    def _seed_default_bids(self):
        """Pre-populate the table with a sensible slam-context default:
        the lowest legal natural game bid in the agreed/longest fit
        plus 6-of-that-suit so the user sees a side-by-side compare."""
        legal = self._legal_candidates()
        seeds: List[str] = []
        # Always offer Pass + the cheapest game-or-higher bid in the
        # candidate's longest suit.
        if "PASS" in legal:
            seeds.append("PASS")
        hand = self.board.hands.get(self.seat)
        if hand is not None and hand.cards:
            suit_len = {Suit.SPADES: 0, Suit.HEARTS: 0,
                        Suit.DIAMONDS: 0, Suit.CLUBS: 0}
            for c in hand.cards:
                suit_len[c.suit] += 1
            longest = max(suit_len, key=lambda s: suit_len[s])
            strain = "NT" if suit_len[longest] < 5 else longest.to_char()
            # Game level depends on strain.
            game_level = 3 if strain == "NT" else (4 if strain in ("H", "S") else 5)
            for lvl in (game_level, 6):
                cand = f"{lvl}{strain}"
                if cand in legal and cand not in seeds:
                    seeds.append(cand)
        for b in seeds:
            self._add_row(b)

    def _add_row(self, bid_str: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(_bid_to_display(bid_str)))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setItem(row, 2, QTableWidgetItem(""))
        # Stash the raw key on the bid cell so we can recover it later
        # regardless of the human-friendly display string.
        self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, bid_str)

    def _current_bid_keys(self) -> List[str]:
        out: List[str] = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is None:
                continue
            key = it.data(Qt.ItemDataRole.UserRole)
            if key:
                out.append(key)
        return out

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_add_bids(self):
        candidates = self._legal_candidates()
        dlg = AddBidsDialog(candidates, self._current_bid_keys(), self)
        if dlg.exec():
            for b in dlg.selected_bids():
                if b not in self._current_bid_keys():
                    self._add_row(b)

    def _on_clear_bids(self):
        self.table.setRowCount(0)
        self._protocol_lines.clear()
        self._last_results.clear()

    def _on_help(self):
        styled_info(
            self,
            "Simulation Help",
            "Q-Plus style bid simulation.\n\n"
            "Add candidate bids with 'Add Bids', set the number of "
            "deals (#) to randomly generate (consistent with your "
            "known hand), then click 'Simulation!'. Each candidate "
            "is double-dummy played on every sample and the average "
            "duplicate score (Points) is reported.\n\n"
            "Tick 'with protocol' to record a per-deal log viewable "
            "from the 'View' button.\n\n"
            "This dialog pops up automatically when a slam "
            "opportunity is detected; you can also open it from "
            "View → Bid Simulation."
        )

    def _on_view(self):
        if not self._protocol_lines and not self._last_results:
            styled_info(self, "Simulation Protocol",
                        "No simulation has been run yet.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Simulation Protocol")
        dlg.setMinimumSize(560, 420)
        apply_dialog_style(dlg)
        lay = QVBoxLayout(dlg)
        view = QTextEdit()
        view.setReadOnly(True)
        if self._protocol_lines:
            view.setPlainText("\n".join(self._protocol_lines))
        else:
            lines = ["No per-deal protocol — re-run with 'with protocol' "
                     "ticked to capture one.", "", "Summary:"]
            for k, d in self._last_results.items():
                lines.append(
                    f"  {_bid_to_display(k):>5}  "
                    f"deals={d.get('samples', 0):>3}  "
                    f"points={d.get('avg_score', 0.0):>+7.1f}"
                )
            view.setPlainText("\n".join(lines))
        lay.addWidget(view)
        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    def _on_simulate(self):
        keys = self._current_bid_keys()
        if not keys:
            QMessageBox.information(
                self, "No Bids",
                "Add at least one candidate bid before running.")
            return

        try:
            candidate_bids = [Bid.from_str(k) for k in keys]
        except Exception as e:
            QMessageBox.warning(self, "Bad Bid", f"Failed to parse: {e}")
            return

        self.sim_btn.setEnabled(False)
        self.add_bids_btn.setEnabled(False)
        self.clear_bids_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._protocol_lines.clear()

        self.worker = SimulationWorker(
            self.engine, self.board, self.seat,
            candidate_bids, int(self.num_spin.value())
        )
        self.worker.progress.connect(self.progress.setValue)
        self.worker.result_ready.connect(self._on_results)
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _on_results(self, results: Dict):
        self._last_results = dict(results)
        # Fill table rows in their current order so the user's chosen
        # ordering is preserved.
        for r in range(self.table.rowCount()):
            key_item = self.table.item(r, 0)
            if key_item is None:
                continue
            key = key_item.data(Qt.ItemDataRole.UserRole)
            data = results.get(key)
            if not data:
                self.table.setItem(r, 1, QTableWidgetItem("0"))
                self.table.setItem(r, 2, QTableWidgetItem("—"))
                continue
            self.table.setItem(
                r, 1, QTableWidgetItem(str(int(data.get('samples', 0))))
            )
            self.table.setItem(
                r, 2,
                QTableWidgetItem(f"{data.get('avg_score', 0.0):+.1f}")
            )

        # Highlight the best row by points so the user can see at a
        # glance which candidate wins.
        best_row = -1
        best_score = None
        for r in range(self.table.rowCount()):
            key_item = self.table.item(r, 0)
            if key_item is None:
                continue
            key = key_item.data(Qt.ItemDataRole.UserRole)
            data = results.get(key)
            if not data:
                continue
            s = data.get('avg_score', 0.0)
            if best_score is None or s > best_score:
                best_score = s
                best_row = r
        if best_row >= 0:
            for c in range(3):
                it = self.table.item(best_row, c)
                if it is not None:
                    it.setBackground(Qt.GlobalColor.lightGray)

        if self.protocol_check.isChecked():
            self._protocol_lines.append(
                f"Simulation for {self.seat.name.capitalize()} — "
                f"{int(self.num_spin.value())} deals"
            )
            for key, data in results.items():
                self._protocol_lines.append(
                    f"  {_bid_to_display(key):>5}  "
                    f"deals={int(data.get('samples', 0)):>3}  "
                    f"avg={data.get('avg_score', 0.0):+8.2f}  "
                    f"tricks={data.get('avg_tricks', 0.0):.2f}"
                )

    def _on_worker_done(self):
        self.sim_btn.setEnabled(True)
        self.add_bids_btn.setEnabled(True)
        self.clear_bids_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.worker = None

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait()
        super().closeEvent(event)
