"""
End of Hand Dialog - Modal dialog shown when a hand is complete.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from .dialog_style import apply_dialog_style


class EndOfHandDialog(QDialog):
    """Modal dialog shown when a hand is complete — Q-Plus style.

    Centre panel mirrors the Q-Plus 'playing finished' banner ("East
    was set one trick in 2♠" etc.); the button row at the bottom is
    the same 5-button line Q-Plus offers (Next deal / Review / Score
    / Repeat / Help). Each button emits a signal so MainWindow can
    wire it to the matching existing handler.
    """

    view_other_table = pyqtSignal()   # teams: view other table
    next_deal_requested = pyqtSignal()  # Next Deal button
    review_requested = pyqtSignal()   # open auction + played tricks
    score_requested = pyqtSignal()    # open the score table
    repeat_requested = pyqtSignal()   # re-deal the same board
    help_requested = pyqtSignal()     # open help
    # Single-player extras — generated closed room comparison and
    # post-hand Claude commentary. Both buttons stay enabled until
    # used; Claude can be re-clicked after Generate Closed Room so
    # the second invocation gets to compare both rooms.
    generate_closed_room_requested = pyqtSignal()
    claude_analysis_requested = pyqtSignal()

    def __init__(self, contract_str: str, declarer: str, result_str: str,
                 score: int, imp_swing: int = None, is_teams_match: bool = False,
                 banner_text: str = "", parent=None):
        """Initialize the end of hand dialog.

        Args:
            contract_str: Contract string (e.g., "4S", "3NT X")
            declarer: Declarer seat character (e.g., "S", "N")
            result_str: Result string (e.g., "Made", "+1", "-2")
            score: Score for declarer's side
            imp_swing: IMP swing (positive = N/S), None if not teams
            is_teams_match: Whether this is a teams match (shows IMP info)
            banner_text: Optional Q-Plus-style sentence ("East was
                set one trick in 2♠"). Falls back to a sentence
                generated from the result/contract if not provided.
            parent: Parent widget
        """
        super().__init__(parent)

        self.contract_str = contract_str
        self.declarer = declarer
        self.result_str = result_str
        self.score = score
        self.imp_swing = imp_swing
        self.is_teams_match = is_teams_match
        self.banner_text = banner_text

        self.setWindowTitle("Playing finished")
        self.setMinimumWidth(560)
        # Non-modal — the user can leave this open while the harness
        # spawns / Q-Plus runs / they consult the score sheet. The
        # main window holds a reference so it isn't GC'd.
        self.setModal(False)
        apply_dialog_style(self)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Header — Q-Plus banner. "playing finished: East was set
        # one trick in 2♠" is the canonical Q-Plus phrasing; we
        # generate that from the contract + result when no caller
        # passed an explicit banner_text in.
        header = QLabel(self._compose_banner_text())
        header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setWordWrap(True)
        layout.addWidget(header)

        # Contract and result frame
        result_frame = QFrame()
        result_frame.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 2px solid #4080c0;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        result_layout = QGridLayout(result_frame)
        result_layout.setSpacing(10)

        # Contract
        contract_label = QLabel("Contract:")
        contract_label.setFont(QFont("Arial", 12))
        result_layout.addWidget(contract_label, 0, 0)

        contract_value = QLabel(f"{self.contract_str} by {self.declarer}")
        contract_value.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        # Color code the contract
        if 'H' in self.contract_str or 'D' in self.contract_str:
            # Could add color for red suits if desired
            pass
        result_layout.addWidget(contract_value, 0, 1)

        # Result
        result_label = QLabel("Result:")
        result_label.setFont(QFont("Arial", 12))
        result_layout.addWidget(result_label, 1, 0)

        result_value = QLabel(self.result_str)
        result_value.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        # Color code result
        if self.result_str.startswith("-"):
            result_value.setStyleSheet("color: #cc0000;")  # Red for down
        elif self.result_str.startswith("+") or self.result_str == "Made":
            result_value.setStyleSheet("color: #00aa00;")  # Green for made
        result_layout.addWidget(result_value, 1, 1)

        # Score
        score_label = QLabel("Score:")
        score_label.setFont(QFont("Arial", 12))
        result_layout.addWidget(score_label, 2, 0)

        score_text = f"{self.score:+d}" if self.score != 0 else "0"
        score_value = QLabel(score_text)
        score_value.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        if self.score > 0:
            score_value.setStyleSheet("color: #00aa00;")
        elif self.score < 0:
            score_value.setStyleSheet("color: #cc0000;")
        result_layout.addWidget(score_value, 2, 1)

        # IMP swing (if teams match and available)
        if self.is_teams_match and self.imp_swing is not None:
            imp_label = QLabel("IMP Swing:")
            imp_label.setFont(QFont("Arial", 12))
            result_layout.addWidget(imp_label, 3, 0)

            imp_text = f"{self.imp_swing:+d}" if self.imp_swing != 0 else "0"
            imp_value = QLabel(imp_text)
            imp_value.setFont(QFont("Arial", 14, QFont.Weight.Bold))
            if self.imp_swing > 0:
                imp_value.setStyleSheet("color: #00aa00;")  # N/S won IMPs
                imp_value.setText(f"{imp_text} (N/S)")
            elif self.imp_swing < 0:
                imp_value.setStyleSheet("color: #cc0000;")  # E/W won IMPs
                imp_value.setText(f"{-self.imp_swing:+d} (E/W)")
            result_layout.addWidget(imp_value, 3, 1)

        layout.addWidget(result_frame)

        # Primary choices row — what the user wants to do next.
        # In single-player mode this is the three-way fork: Generate
        # closed room (launches harness + Q-Plus), Claude analysis
        # (post-hand commentary, includes both rooms if a closed room
        # was generated), or Next deal (move on). Teams mode keeps
        # "View other table" instead of "Generate closed room".
        def _make(label: str, signal_emit, default: bool = False,
                  min_width: int = 140):
            btn = QPushButton(label)
            btn.setFont(QFont("Arial", 11, QFont.Weight.Bold if default
                              else QFont.Weight.Normal))
            btn.setMinimumWidth(min_width)
            if default:
                btn.setDefault(True)
            btn.clicked.connect(signal_emit)
            return btn

        primary_row = QHBoxLayout()
        primary_row.setSpacing(10)
        primary_row.addStretch()
        if self.is_teams_match:
            primary_row.addWidget(_make(
                "View other table", self._on_view_other_table))
        else:
            # Generate closed room — kept enabled even after click so
            # the user can re-launch the harness if Q-Plus crashed.
            self.gen_closed_btn = _make(
                "Generate closed room",
                self._on_generate_closed_room)
            primary_row.addWidget(self.gen_closed_btn)
        # Claude analysis — stays enabled so a second click after the
        # closed room is ingested upgrades the prompt to compare both
        # rooms.
        self.claude_btn = _make(
            "Claude analysis", self._on_claude_analysis)
        primary_row.addWidget(self.claude_btn)
        primary_row.addWidget(_make(
            "Next deal", self._on_next_deal, default=True))
        primary_row.addStretch()
        layout.addLayout(primary_row)

        # Secondary row — the existing Q-Plus 5-button line, demoted
        # so it doesn't compete with the primary fork.
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()
        button_layout.addWidget(_make(
            "Review", self._on_review, min_width=100))
        button_layout.addWidget(_make(
            "Score", self._on_score, min_width=100))
        button_layout.addWidget(_make(
            "Repeat", self._on_repeat, min_width=100))
        button_layout.addWidget(_make(
            "Help", self._on_help, min_width=100))
        button_layout.addStretch()
        layout.addLayout(button_layout)

    # ------------------------------------------------------------------
    # Q-Plus banner text — "<seat> made <contract>" / "<seat> was set
    # <N> tricks in <contract>". Uses the seat name not the seat char
    # for natural-language readability.
    # ------------------------------------------------------------------

    _SEAT_NAMES = {
        'N': 'North', 'E': 'East', 'S': 'South', 'W': 'West',
    }

    def _compose_banner_text(self) -> str:
        if self.banner_text:
            return self.banner_text
        seat_name = self._SEAT_NAMES.get(self.declarer, self.declarer)
        # Parse the result_str the caller already built. "Made" /
        # "+1" / "+2" → made. "-1" / "-2" → went down.
        rs = (self.result_str or "").strip()
        contract_pretty = self.contract_str or "?"
        if rs.startswith("-"):
            try:
                down = int(rs)  # e.g. "-2"
                n = -down  # positive
            except ValueError:
                n = 1
            trick_word = "trick" if n == 1 else "tricks"
            phrase = (f"was set {self._spell(n)} {trick_word} "
                      f"in {contract_pretty}")
        elif rs.startswith("+"):
            phrase = f"made {rs} overtricks in {contract_pretty}"
        else:
            phrase = f"made {contract_pretty}"
        return f"playing finished: {seat_name} {phrase}"

    @staticmethod
    def _spell(n: int) -> str:
        spelled = {1: 'one', 2: 'two', 3: 'three', 4: 'four',
                   5: 'five', 6: 'six', 7: 'seven'}
        return spelled.get(n, str(n))

    # ------------------------------------------------------------------
    # Button-handler shims — each closes the dialog and emits its
    # signal so MainWindow can dispatch.
    # ------------------------------------------------------------------

    def _on_view_other_table(self):
        self.view_other_table.emit()
        self.accept()

    def _on_next_deal(self):
        # Emit the dedicated Next-Deal signal so MainWindow's
        # accept-handler isn't shared with Review / Score / Repeat
        # (each of which also calls self.accept()). Without this,
        # clicking Review fired Next-Deal on the same accept signal
        # and the next hand auto-dealt under the review dialog.
        self.next_deal_requested.emit()
        self.accept()

    def _on_review(self):
        self.review_requested.emit()
        self.accept()

    def _on_score(self):
        self.score_requested.emit()
        self.accept()

    def _on_repeat(self):
        self.repeat_requested.emit()
        self.accept()

    def _on_help(self):
        self.help_requested.emit()
        # Don't close — help is a side-show, the user may want to
        # keep the result banner up while reading.

    def _on_generate_closed_room(self):
        """Launch the GUI harness + Q-Plus, don't close the dialog so
        the user can come back for Claude analysis or Next deal once
        the closed-room game is over and ingested.
        """
        self.generate_closed_room_requested.emit()

    def _on_claude_analysis(self):
        """Kick off Claude. Dialog stays open so the user can also
        click Next deal afterwards. The handler upstream decides
        whether the closed room is already attached and picks the
        comparison vs single-room prompt accordingly.
        """
        self.claude_analysis_requested.emit()


class PassedOutDialog(QDialog):
    """Dialog shown when a hand is passed out."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Passed Out")
        self.setMinimumWidth(300)
        self.setModal(True)
        apply_dialog_style(self)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Header
        header = QLabel("Passed Out")
        header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Message
        msg = QLabel("All four players passed.\nNo score for this board.")
        msg.setFont(QFont("Arial", 12))
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg)

        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_btn = QPushButton("Next Deal")
        ok_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
