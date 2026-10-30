"""
Bid Simulation Dialog
Run simulations to evaluate candidate bids.
Similar to BridgeIQ simulation feature.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QSpinBox, QPushButton, QTableWidget, QTableWidgetItem,
    QProgressBar, QCheckBox, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from backend.models import BoardState, Bid, Seat, Card, Hand, Contract, Suit
from typing import List, Dict, Optional
import numpy as np

from .dialog_style import apply_dialog_style


class SimulationWorker(QThread):
    """Worker thread for running bid simulations"""

    progress = pyqtSignal(int)
    result_ready = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, engine, board: BoardState, seat: Seat,
                 candidate_bids: List[Bid], num_samples: int, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.board = board
        self.seat = seat
        self.candidate_bids = candidate_bids
        self.num_samples = num_samples
        self._cancelled = False

    # Standard WBF IMP conversion table: (threshold, imps)
    IMP_TABLE = [
        (10, 0), (40, 1), (80, 2), (120, 3), (160, 4),
        (210, 5), (260, 6), (310, 7), (360, 8), (420, 9),
        (490, 10), (590, 11), (740, 12), (890, 13), (1090, 14),
        (1290, 15), (1490, 16), (1740, 17), (1990, 18), (2240, 19),
        (2490, 20), (2990, 21), (3490, 22), (3990, 23),
    ]

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def _score_to_imp(diff: float) -> float:
        """Convert a score difference to IMPs using the standard WBF table."""
        sign = 1 if diff >= 0 else -1
        abs_diff = abs(diff)
        for threshold, imps in SimulationWorker.IMP_TABLE:
            if abs_diff <= threshold:
                return sign * imps
        return sign * 24

    def _find_last_real_bid(self) -> Optional[Bid]:
        """Find the last non-pass, non-double bid in the auction."""
        for b in reversed(self.board.auction):
            if not b.is_pass and not b.is_double and not b.is_redouble:
                return b
        return None

    def _generate_sample_board(self, seed: int) -> Optional[BoardState]:
        """Generate a random deal preserving the known hand."""
        known_hand = self.board.hands.get(self.seat)
        if not known_hand:
            return None

        known_codes = {c.code52() for c in known_hand.cards}
        remaining = [i for i in range(52) if i not in known_codes]

        rng = np.random.RandomState(seed)
        rng.shuffle(remaining)

        sample = BoardState(board_number=self.board.board_number)
        sample.dealer = self.board.dealer
        sample.vulnerability = self.board.vulnerability
        sample.hands[self.seat] = Hand(cards=list(known_hand.cards))

        other_seats = [s for s in Seat if s != self.seat]
        for i, other_seat in enumerate(other_seats):
            cards = [Card.from_code52(c) for c in remaining[i*13:(i+1)*13]]
            sample.hands[other_seat] = Hand(cards=cards)

        return sample

    def run(self):
        """Run bid simulations using the BEN engine's DDS solver."""
        bid_data = {bid.to_str(): {'scores': [], 'tricks': []} for bid in self.candidate_bids}
        total = len(self.candidate_bids) * self.num_samples
        done = 0

        for sample_idx in range(self.num_samples):
            if self._cancelled:
                break

            sample_board = self._generate_sample_board(sample_idx)
            if not sample_board:
                done += len(self.candidate_bids)
                self.progress.emit(int(done * 100 / total))
                continue

            # Cache DD results per suit for this sample
            dd_cache = {}

            for bid in self.candidate_bids:
                if self._cancelled:
                    break

                bid_key = bid.to_str()

                try:
                    # Determine contract parameters from the bid
                    if bid.is_pass:
                        last_real = self._find_last_real_bid()
                        if last_real is None:
                            # Passed out - no score
                            bid_data[bid_key]['scores'].append(0)
                            bid_data[bid_key]['tricks'].append(0)
                            done += 1
                            self.progress.emit(int(done * 100 / total))
                            continue
                        c_level, c_suit = last_real.level, last_real.suit
                        doubled, redoubled = False, False
                    elif bid.is_double or bid.is_redouble:
                        last_real = self._find_last_real_bid()
                        if last_real is None:
                            bid_data[bid_key]['scores'].append(0)
                            bid_data[bid_key]['tricks'].append(0)
                            done += 1
                            self.progress.emit(int(done * 100 / total))
                            continue
                        c_level, c_suit = last_real.level, last_real.suit
                        doubled = bid.is_double
                        redoubled = bid.is_redouble
                    else:
                        c_level, c_suit = bid.level, bid.suit
                        doubled, redoubled = False, False

                    if c_suit is None:
                        c_suit = Suit.NOTRUMP

                    # Get DD tricks with caching per suit
                    if c_suit not in dd_cache:
                        dd_tricks = self.engine.get_dd_tricks(
                            sample_board, c_suit, self.seat
                        )
                        dd_cache[c_suit] = dd_tricks
                    dd_tricks = dd_cache[c_suit]

                    if dd_tricks is not None:
                        contract = Contract(
                            level=c_level, suit=c_suit,
                            doubled=doubled, redoubled=redoubled,
                            declarer=self.seat
                        )
                        vuln = self.board.vulnerability.is_vulnerable(self.seat)
                        score = self.engine.calculate_score(contract, dd_tricks, vuln)
                        bid_data[bid_key]['scores'].append(score)
                        bid_data[bid_key]['tricks'].append(dd_tricks)
                    else:
                        bid_data[bid_key]['scores'].append(0)
                        bid_data[bid_key]['tricks'].append(0)
                except Exception:
                    bid_data[bid_key]['scores'].append(0)
                    bid_data[bid_key]['tricks'].append(0)

                done += 1
                self.progress.emit(int(done * 100 / total))

        # Calculate results
        results = {}
        for bid_key, data in bid_data.items():
            if data['scores']:
                results[bid_key] = {
                    'avg_score': sum(data['scores']) / len(data['scores']),
                    'avg_imp': 0.0,
                    'avg_mp': 0.0,
                    'avg_tricks': sum(data['tricks']) / len(data['tricks']),
                    'samples': len(data['scores'])
                }

        # Compute IMPs relative to the average score across all candidates
        if len(results) > 1:
            avg_scores = [r['avg_score'] for r in results.values()]
            baseline = sum(avg_scores) / len(avg_scores)
            for data in results.values():
                data['avg_imp'] = self._score_to_imp(data['avg_score'] - baseline)

        # Compute MP% as percentage ranking among candidates
        if results:
            sorted_bids = sorted(results.items(), key=lambda x: x[1]['avg_score'])
            n = len(sorted_bids)
            for rank, (bid_key, data) in enumerate(sorted_bids):
                data['avg_mp'] = (rank / max(1, n - 1)) * 100 if n > 1 else 50.0

        self.result_ready.emit(results)
        self.finished.emit()


class SimulationDialog(QDialog):
    """
    Dialog for simulating bid outcomes.
    Similar to BridgeIQ's simulation window.
    """

    def __init__(self, engine, board: BoardState, seat: Seat, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.board = board
        self.seat = seat
        self.worker = None

        self.setWindowTitle("Bid Simulation")
        self.setMinimumSize(600, 500)
        apply_dialog_style(self)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Info section
        info_group = QGroupBox("Current Position")
        info_layout = QGridLayout(info_group)

        info_layout.addWidget(QLabel("Board:"), 0, 0)
        info_layout.addWidget(QLabel(str(self.board.board_number)), 0, 1)

        info_layout.addWidget(QLabel("Bidder:"), 0, 2)
        info_layout.addWidget(QLabel(self.seat.to_char()), 0, 3)

        if self.seat in self.board.hands:
            hand = self.board.hands[self.seat]
            info_layout.addWidget(QLabel("Hand:"), 1, 0)
            info_layout.addWidget(QLabel(hand.to_pbn()), 1, 1, 1, 3)
            info_layout.addWidget(QLabel(f"HCP: {hand.hcp()}"), 2, 0, 1, 4)

        layout.addWidget(info_group)

        # Candidate bids selection
        bids_group = QGroupBox("Candidate Bids to Simulate")
        bids_layout = QVBoxLayout(bids_group)

        self.bid_checkboxes = {}
        bids_grid = QGridLayout()

        # Common bids to simulate
        common_bids = [
            "PASS", "1C", "1D", "1H", "1S", "1NT",
            "2C", "2D", "2H", "2S", "2NT",
            "3C", "3D", "3H", "3S", "3NT"
        ]

        for i, bid_str in enumerate(common_bids):
            check = QCheckBox(bid_str)
            if bid_str == "PASS":
                check.setChecked(True)
            bids_grid.addWidget(check, i // 6, i % 6)
            self.bid_checkboxes[bid_str] = check

        bids_layout.addLayout(bids_grid)
        layout.addWidget(bids_group)

        # Simulation settings
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Number of samples:"))
        self.num_samples = QSpinBox()
        self.num_samples.setRange(10, 1000)
        self.num_samples.setValue(100)
        self.num_samples.setSingleStep(10)
        settings_layout.addWidget(self.num_samples)

        settings_layout.addStretch()

        layout.addWidget(settings_group)

        # Results table
        results_group = QGroupBox("Simulation Results")
        results_layout = QVBoxLayout(results_group)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels([
            "Bid", "Avg Score", "Avg IMP", "Avg MP%", "Avg Tricks"
        ])
        self.results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        results_layout.addWidget(self.results_table)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        results_layout.addWidget(self.progress)

        layout.addWidget(results_group)

        # Buttons
        button_layout = QHBoxLayout()

        self.run_btn = QPushButton("Run Simulation")
        self.run_btn.clicked.connect(self._on_run)
        button_layout.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _on_run(self):
        """Start simulation"""
        # Get selected bids
        selected_bids = []
        for bid_str, check in self.bid_checkboxes.items():
            if check.isChecked():
                selected_bids.append(Bid.from_str(bid_str))

        if not selected_bids:
            return

        # Setup UI for running
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)

        # Clear results
        self.results_table.setRowCount(0)

        # Start worker
        self.worker = SimulationWorker(
            self.engine, self.board, self.seat,
            selected_bids, self.num_samples.value()
        )
        self.worker.progress.connect(self.progress.setValue)
        self.worker.result_ready.connect(self._on_results)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_cancel(self):
        """Cancel simulation"""
        if self.worker:
            self.worker.cancel()

    def _on_results(self, results: Dict):
        """Handle simulation results"""
        self.results_table.setRowCount(len(results))

        # Sort by average IMP
        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]['avg_imp'],
            reverse=True
        )

        for row, (bid_str, data) in enumerate(sorted_results):
            self.results_table.setItem(row, 0, QTableWidgetItem(bid_str))
            self.results_table.setItem(row, 1,
                QTableWidgetItem(f"{data['avg_score']:.1f}"))
            self.results_table.setItem(row, 2,
                QTableWidgetItem(f"{data['avg_imp']:.2f}"))
            self.results_table.setItem(row, 3,
                QTableWidgetItem(f"{data['avg_mp']:.1f}%"))
            self.results_table.setItem(row, 4,
                QTableWidgetItem(f"{data['avg_tricks']:.2f}"))

            # Highlight best result
            if row == 0:
                for col in range(5):
                    item = self.results_table.item(row, col)
                    item.setBackground(Qt.GlobalColor.lightGray)

    def _on_finished(self):
        """Cleanup after simulation"""
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setVisible(False)
        self.worker = None

    def closeEvent(self, event):
        """Clean up worker on close"""
        if self.worker:
            self.worker.cancel()
            self.worker.wait()
        super().closeEvent(event)
