"""
Match Control Dialog — laid out to mirror Q-Plus Bridge's "Match Control"
window (Deal menu ▸ Match Control), adapted to biq.

Q-Plus reference: MANUAL/ENG/BRIDGE.HLQ `.match-control-w`. The window has,
top to bottom (the help tells the user to set them in that order because of
the dependencies between them):

  Deal Source   : Deal number | Deal file | Score table | User entered
  Deal number   : major / minor  (in biq this is a single number — biq deals
                  its own way, so we keep one biq "Deal number" field)
  File          : Pair tournament file | Team tournament file |
                  Score table | Deal file ; selected (N): <path> [idx] [X] ;
                  Deal filter <name> [on/off]
  Scoring Method: Rubber | Team (IMP) | Pair (MP) ; Keep scoring table
  Comparison    : Closed room | Results of file | None or later ; # boards
  [ Ok ]  [ Cancel ]  [ Help ]

`get_settings()` stays backward-compatible with the existing callers in
main_window (`source` in {'random','file','user'}, `scoring` in
{'rubber','imp','mp'}, `comparison` in {'closed_room','file','none'},
plus `start_number`, `file_path`, `first_deal`, `deal_count`, `read_mode`,
`num_boards`, `auto_advance`) and adds the new Q-Plus axes
(`source_kind`, `file_kind`, `keep_scoring_table`, `deal_filter`,
`filter_on`).
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QComboBox, QSpinBox, QLineEdit, QPushButton,
    QRadioButton, QButtonGroup, QFileDialog, QCheckBox, QMessageBox,
    QToolButton,
)
from PyQt6.QtCore import Qt

from .dialog_style import apply_dialog_style


class MatchControlDialog(QDialog):
    """Q-Plus-style match/tournament control."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match Control")
        self.setMinimumWidth(520)
        apply_dialog_style(self)

        # Filled in by the tournament/deal-file pickers so the match
        # controller can start at deal N rather than always 1.
        self._first_deal: int = 1
        self._deal_count: int = 0
        self._read_mode: str = 'auto'
        self._file_kind: str = ''          # 'pair' | 'team' | 'score' | 'deal'
        self._deal_filter = None           # filter dict, when one is set

        self._setup_ui()
        self._refresh_enabled()

    # ----------------------------------------------------------------- UI
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_dealnum_group())
        layout.addWidget(self._build_file_group())
        layout.addWidget(self._build_scoring_group())
        layout.addWidget(self._build_comparison_group())
        layout.addLayout(self._build_buttons())

    def _build_source_group(self) -> QGroupBox:
        g = QGroupBox("Deal Source")
        row = QHBoxLayout(g)
        self.source_buttons = QButtonGroup(self)
        # biq does "Deal number" its own way (single number, biq-dealt) —
        # see the class docstring; everything else mirrors Q-Plus.
        self.src_dealnum = QRadioButton("Deal number")
        self.src_dealfile = QRadioButton("Deal file")
        self.src_scoretable = QRadioButton("Score table")
        self.src_userentered = QRadioButton("User entered")
        self.src_dealnum.setChecked(True)
        for i, b in enumerate((self.src_dealnum, self.src_dealfile,
                               self.src_scoretable, self.src_userentered)):
            self.source_buttons.addButton(b, i)
            row.addWidget(b)
        row.addStretch()
        self.source_buttons.idToggled.connect(
            lambda _id, on: on and self._refresh_enabled())
        return g

    def _build_dealnum_group(self) -> QGroupBox:
        g = QGroupBox("Deal number")
        row = QHBoxLayout(g)
        row.addWidget(QLabel("Deal number:"))
        self.start_number = QSpinBox()
        self.start_number.setRange(1, 999999)
        self.start_number.setValue(1)
        self.start_number.setToolTip(
            "biq deals its own way — this is the starting board number for "
            "biq's deals (Q-Plus uses a deterministic major/minor pair here).")
        row.addWidget(self.start_number)
        note = QLabel("(biq uses a single deal number)")
        note.setStyleSheet("color:#666;")
        row.addWidget(note)
        row.addStretch()
        return g

    def _build_file_group(self) -> QGroupBox:
        g = QGroupBox("File")
        grid = QGridLayout(g)

        self.btn_pair = QPushButton("Pair tournament file")
        self.btn_team = QPushButton("Team tournament file")
        self.btn_score = QPushButton("Score table")
        self.btn_deal = QPushButton("Deal file")
        self.btn_pair.clicked.connect(lambda: self._pick_tournament('pair'))
        self.btn_team.clicked.connect(lambda: self._pick_tournament('team'))
        self.btn_score.clicked.connect(self._pick_score_table)
        self.btn_deal.clicked.connect(self._pick_deal_file)
        grid.addWidget(self.btn_pair, 0, 0)
        grid.addWidget(self.btn_team, 0, 1)
        grid.addWidget(self.btn_score, 1, 0)
        grid.addWidget(self.btn_deal, 1, 1)

        sel_row = QHBoxLayout()
        self.selected_label = QLabel("selected (0):")
        sel_row.addWidget(self.selected_label)
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        sel_row.addWidget(self.file_path, 1)
        self.file_index = QSpinBox()
        self.file_index.setRange(0, 999999)
        self.file_index.setToolTip("Start at this deal number within the file.")
        self.file_index.valueChanged.connect(
            lambda v: setattr(self, '_first_deal', max(1, v) if v else 1))
        sel_row.addWidget(self.file_index)
        clear_btn = QToolButton()
        clear_btn.setText("✕")
        clear_btn.setToolTip("Clear the selected file.")
        clear_btn.clicked.connect(self._clear_file)
        sel_row.addWidget(clear_btn)
        grid.addLayout(sel_row, 2, 0, 1, 2)

        filt_row = QHBoxLayout()
        self.btn_filter = QPushButton("Deal filter")
        self.btn_filter.clicked.connect(self._edit_filter)
        filt_row.addWidget(self.btn_filter)
        self.filter_label = QLineEdit("none")
        self.filter_label.setReadOnly(True)
        filt_row.addWidget(self.filter_label, 1)
        self.filter_toggle = QPushButton("on/off")
        self.filter_toggle.setCheckable(True)
        self.filter_toggle.clicked.connect(self._toggle_filter)
        filt_row.addWidget(self.filter_toggle)
        grid.addLayout(filt_row, 3, 0, 1, 2)
        return g

    def _build_scoring_group(self) -> QGroupBox:
        g = QGroupBox("Scoring Method")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.scoring_buttons = QButtonGroup(self)
        self.rubber_radio = QRadioButton("Rubber")
        self.imp_radio = QRadioButton("Team (IMP)")
        self.mp_radio = QRadioButton("Pair (MP)")
        self.imp_radio.setChecked(True)
        for i, b in enumerate((self.rubber_radio, self.imp_radio, self.mp_radio)):
            self.scoring_buttons.addButton(b, i)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.keep_scoring = QCheckBox("Keep scoring table")
        v.addWidget(self.keep_scoring)
        self.scoring_buttons.idToggled.connect(
            lambda _id, on: on and self._refresh_enabled())
        return g

    def _build_comparison_group(self) -> QGroupBox:
        g = QGroupBox("Comparison")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.comparison_buttons = QButtonGroup(self)
        self.closed_room_radio = QRadioButton("Closed room")
        self.file_results_radio = QRadioButton("Results of file")
        self.no_comparison_radio = QRadioButton("None or later")
        self.closed_room_radio.setChecked(True)
        for i, b in enumerate((self.closed_room_radio, self.file_results_radio,
                               self.no_comparison_radio)):
            self.comparison_buttons.addButton(b, i)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        boards_row = QHBoxLayout()
        boards_row.addWidget(QLabel("# boards:"))
        self.num_boards = QComboBox()
        self.num_boards.setEditable(True)
        for n in ("1", "2", "4", "8", "16", "24", "32", "64"):
            self.num_boards.addItem(n)
        self.num_boards.setCurrentText("16")
        boards_row.addWidget(self.num_boards)
        boards_row.addStretch()
        v.addLayout(boards_row)
        return g

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        ok = QPushButton("Ok")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        help_btn = QPushButton("Help")
        help_btn.clicked.connect(self._show_help)
        row.addWidget(ok)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(help_btn)
        return row

    # ----------------------------------------------------- dependencies
    def _refresh_enabled(self):
        """Mirror Q-Plus's top-to-bottom dependencies."""
        is_dealnum = self.src_dealnum.isChecked()
        is_file = self.src_dealfile.isChecked() or self.src_scoretable.isChecked()
        is_user = self.src_userentered.isChecked()
        is_rubber = self.rubber_radio.isChecked()

        self.start_number.setEnabled(is_dealnum)
        for b in (self.btn_pair, self.btn_team, self.btn_score, self.btn_deal):
            b.setEnabled(not is_user)

        # 'Results of file' only with Deal file / Score table source.
        self.file_results_radio.setEnabled(is_file)
        # 'None or later' is forced for User entered or Rubber scoring.
        force_none = is_user or is_rubber
        if force_none:
            self.no_comparison_radio.setChecked(True)
        self.closed_room_radio.setEnabled(not force_none)
        if self.file_results_radio.isChecked() and not is_file:
            self.no_comparison_radio.setChecked(True)
        # # boards matters mainly for the closed room.
        self.num_boards.setEnabled(self.closed_room_radio.isChecked())

    # --------------------------------------------------------- file pick
    def _pick_tournament(self, kind: str):
        """Pair/Team tournament file via the Q-Plus-style picker; sets the
        matching scoring method (Pair→MP, Team→IMP)."""
        from .tournament_file_dialog import TournamentFileDialog
        dlg = TournamentFileDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.get_result() or {}
        path = result.get('path', '')
        if not path:
            return
        self._set_selected_file(path, kind,
                                first=int(result.get('first_deal', 1)),
                                count=int(result.get('deal_count', 0)),
                                read_mode=result.get('read_mode', 'auto'))
        (self.mp_radio if kind == 'pair' else self.imp_radio).setChecked(True)

    def _pick_deal_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a deal file", "",
            "Deal files (*.bdl *.pbn);;BDL Files (*.bdl);;"
            "PBN Files (*.pbn);;All Files (*)")
        if path:
            self._set_selected_file(path, 'deal')

    def _pick_score_table(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a score table", "",
            "Score tables (*.qss *.bdl);;All Files (*)")
        if path:
            self._set_selected_file(path, 'score')
            self.src_scoretable.setChecked(True)

    def _set_selected_file(self, path, kind, first=1, count=0, read_mode='auto'):
        self.file_path.setText(path)
        self._file_kind = kind
        self._first_deal = max(1, int(first or 1))
        self._deal_count = int(count or 0)
        self._read_mode = read_mode
        self.file_index.setValue(self._first_deal)
        self.selected_label.setText(f"selected ({self._deal_count}):")
        # Score table is its own source; everything else is a deal file.
        (self.src_scoretable if kind == 'score'
         else self.src_dealfile).setChecked(True)
        self._refresh_enabled()

    def _clear_file(self):
        self.file_path.clear()
        self._file_kind = ''
        self._deal_count = 0
        self._first_deal = 1
        self.file_index.setValue(0)
        self.selected_label.setText("selected (0):")

    # ------------------------------------------------------- deal filter
    def _edit_filter(self):
        """Open biq's deal-filter dialog (Q-Plus: a filter is meaningful for
        the deal-number source, allowed for deal files too)."""
        try:
            from .deal_filter import DealFilterDialog
        except Exception:
            QMessageBox.information(self, "Deal filter",
                                    "Deal-filter editor is unavailable.")
            return
        dlg = DealFilterDialog(self)
        if dlg.exec():
            self._deal_filter = dlg.get_filter()
            self.filter_label.setText("active" if self._deal_filter else "none")
            self.filter_toggle.setChecked(bool(self._deal_filter))

    def _toggle_filter(self):
        if not self._deal_filter and self.filter_toggle.isChecked():
            # Nothing configured yet — open the editor.
            self._edit_filter()
        self.filter_label.setText(
            "active" if (self._deal_filter and self.filter_toggle.isChecked())
            else "none")

    # --------------------------------------------------------------- help
    def _show_help(self):
        QMessageBox.information(
            self, "Match Control — help",
            "Set the options from top to bottom (they depend on each other), "
            "as in Q-Plus Bridge.\n\n"
            "Deal Source:\n"
            "  • Deal number — biq deals from the given starting number "
            "(biq's own dealing; not Q-Plus's deterministic major/minor).\n"
            "  • Deal file — play the deals from a deal file. Use the "
            "Pair/Team tournament or Deal file buttons to choose one. If the "
            "file already carries a contract + vulnerability (e.g. the "
            "practice decks), biq skips the auction and plays from that "
            "contract.\n"
            "  • Score table — replay the deals of a saved score table.\n"
            "  • User entered — you enter the deals yourself.\n\n"
            "Scoring: Rubber, Team (IMP) or Pair (MP). 'Keep scoring table' "
            "keeps the running table across the match.\n\n"
            "Comparison:\n"
            "  • Closed room — four biq bots play the same boards in parallel; "
            "their result appears in the scoring table. '# boards' sets how "
            "many they play.\n"
            "  • Results of file — compare against the results stored in the "
            "deal/score-table file (deal-file or score-table source only).\n"
            "  • None or later — no comparison now (forced for User entered / "
            "Rubber).")

    # ------------------------------------------------------------ result
    def get_settings(self) -> dict:
        kind = ['deal_number', 'deal_file', 'score_table',
                'user_entered'][self.source_buttons.checkedId()]
        # Back-compat 'source': callers branch on 'file' vs not.
        source = {'deal_number': 'random', 'deal_file': 'file',
                  'score_table': 'file', 'user_entered': 'user'}[kind]
        try:
            num_boards = int(self.num_boards.currentText())
        except (ValueError, AttributeError):
            num_boards = 16
        filter_on = bool(self._deal_filter) and self.filter_toggle.isChecked()
        return {
            # back-compatible keys
            'source': source,
            'start_number': self.start_number.value(),
            'file_path': self.file_path.text(),
            'first_deal': self._first_deal,
            'deal_count': self._deal_count,
            'read_mode': self._read_mode,
            'scoring': ['rubber', 'imp', 'mp'][self.scoring_buttons.checkedId()],
            'comparison': ['closed_room', 'file',
                           'none'][self.comparison_buttons.checkedId()],
            'auto_advance': True,
            'num_boards': num_boards,
            # new Q-Plus axes
            'source_kind': kind,
            'file_kind': self._file_kind,
            'keep_scoring_table': self.keep_scoring.isChecked(),
            'deal_filter': self._deal_filter if filter_on else None,
            'filter_on': filter_on,
        }
