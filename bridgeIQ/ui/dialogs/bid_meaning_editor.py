"""Bid-meaning editor — Q-Plus .bid-eval-in.

A modal dialog for entering / editing the meaning of one bid in a
bidding system. Layout mirrors the spec, top to bottom:

  • Artificial / Forcing checkboxes + bid name combo.
  • HP / LP / TP min..max ranges (TP is trump-aware; the trump
    combo's selection is recorded with the meaning).
  • # aces and # kings expression entries.
  • Per-suit rows: length range, control (--/2./1.), stopper.
  • Dependencies multi-line editor with live parser feedback.

Bid meanings are saved to JSON under
``CONFIG/BIDRULE/<system>.json`` so each system carries its own
catalogue independent of the in-process rule engine. Callers
that already speak the existing ``BidMeaning`` dataclass can
convert via :func:`bid_meaning_from_form` and back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton, QSpinBox,
    QTextEdit, QMessageBox, QFileDialog,
)

from .dialog_style import apply_dialog_style


@dataclass
class BidMeaningForm:
    """Serialisable form-fill matching the spec's input shape.

    Distinct from the engine-side ``BidMeaning`` dataclass: this
    one carries the raw entry data (including the dependencies
    text block, which the engine doesn't need at runtime).
    """
    bid: str = ""           # e.g. "1NT", "2C", "X"
    name: str = ""          # e.g. "Stayman", "Strong 2C"
    is_artificial: bool = False
    is_forcing: bool = False

    hp_min: Optional[int] = None
    hp_max: Optional[int] = None
    lp_min: Optional[int] = None
    lp_max: Optional[int] = None
    tp_min: Optional[int] = None
    tp_max: Optional[int] = None
    tp_trump: str = "NT"    # 'NT' / 'S' / 'H' / 'D' / 'C'

    aces_expr: str = ""     # e.g. "# aces + HK - CA = 2 | 5"
    kings_expr: str = ""

    suit_S: dict = field(default_factory=lambda: {
        "min": 0, "max": 13, "control": "--", "stopper": "--"})
    suit_H: dict = field(default_factory=lambda: {
        "min": 0, "max": 13, "control": "--", "stopper": "--"})
    suit_D: dict = field(default_factory=lambda: {
        "min": 0, "max": 13, "control": "--", "stopper": "--"})
    suit_C: dict = field(default_factory=lambda: {
        "min": 0, "max": 13, "control": "--", "stopper": "--"})

    dependencies: str = ""  # raw multi-line text


class BidMeaningEditorDialog(QDialog):
    """Editor for a single bid's meaning. Returns a ``BidMeaningForm``
    via ``get_form()`` after accept; ``None`` on cancel.

    The dialog persists the meaning to
    ``CONFIG/BIDRULE/<system>.json`` when the user clicks Save;
    Load reads a previously-saved meaning back in.
    """

    SUITS = (('S', '♠'), ('H', '♥'), ('D', '♦'), ('C', '♣'))
    NAMES = [
        "", "Stayman", "Jacoby Transfer", "Texas Transfer",
        "Blackwood", "RKCB 1430", "RKCB 3014", "Gerber",
        "Negative Double", "Support Double",
        "Splinter", "Jacoby 2NT",
        "Strong 2C", "Weak Two", "Multi 2D",
        "Lebensohl", "Michaels", "Unusual 2NT",
        "New Minor Forcing", "Fourth Suit Forcing",
        "Drury",
    ]

    def __init__(self, system_key: str = "", bid: str = "",
                 existing: Optional[BidMeaningForm] = None,
                 parent=None):
        super().__init__(parent)
        self._system_key = system_key or "SAYC"
        self._form = existing or BidMeaningForm(bid=bid)
        self.setWindowTitle(
            f"Edit bid meaning — {self._system_key}: "
            f"{self._form.bid or '(new bid)'}"
        )
        self.setMinimumWidth(740)
        self.setMinimumHeight(640)
        apply_dialog_style(self)
        self._setup_ui()
        self._load_form()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Bid + name + flags.
        top_group = QGroupBox("Bid")
        top = QGridLayout(top_group)
        top.addWidget(QLabel("Bid:"), 0, 0)
        self.bid_edit = QLineEdit()
        self.bid_edit.setMaximumWidth(80)
        self.bid_edit.setPlaceholderText("1NT, 2C, X, …")
        top.addWidget(self.bid_edit, 0, 1)

        self.artificial_cb = QCheckBox("artificial")
        top.addWidget(self.artificial_cb, 0, 2)
        self.forcing_cb = QCheckBox("forcing")
        top.addWidget(self.forcing_cb, 0, 3)

        top.addWidget(QLabel("name:"), 0, 4)
        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        self.name_combo.addItems(self.NAMES)
        top.addWidget(self.name_combo, 0, 5, 1, 2)
        root.addWidget(top_group)

        # Points.
        pts_group = QGroupBox("Points")
        pts = QGridLayout(pts_group)
        pts.addWidget(QLabel(""), 0, 0)
        pts.addWidget(QLabel("min"), 0, 1)
        pts.addWidget(QLabel("max"), 0, 2)
        self.hp_min, self.hp_max = self._mk_range_pair(pts, 1, "HP [0-40]:")
        self.lp_min, self.lp_max = self._mk_range_pair(pts, 2, "LP [0-40]:")
        self.tp_min, self.tp_max = self._mk_range_pair(pts, 3, "TP [0-40]:")
        pts.addWidget(QLabel("trump:"), 3, 3)
        self.trump_combo = QComboBox()
        self.trump_combo.addItems(['NT', 'S', 'H', 'D', 'C'])
        pts.addWidget(self.trump_combo, 3, 4)
        root.addWidget(pts_group)

        # Aces / Kings expressions.
        ak_group = QGroupBox("Aces / Kings")
        ak = QGridLayout(ak_group)
        ak.addWidget(QLabel("# aces expr:"), 0, 0)
        self.aces_edit = QLineEdit()
        self.aces_edit.setPlaceholderText(
            "e.g. '# aces + HK - CA = 2 | 5'")
        ak.addWidget(self.aces_edit, 0, 1)
        ak.addWidget(QLabel("# kings expr:"), 1, 0)
        self.kings_edit = QLineEdit()
        ak.addWidget(self.kings_edit, 1, 1)
        ak_hint = QLabel(
            '<span style="color:#666">Q-Plus normally derives ace / '
            'king meanings from Blackwood &amp; Gerber automatically. '
            'Use these fields only when this bid has a different '
            'asking role.</span>'
        )
        ak_hint.setWordWrap(True)
        ak.addWidget(ak_hint, 2, 0, 1, 2)
        root.addWidget(ak_group)

        # Per-suit rows.
        suits_group = QGroupBox("Suits")
        suits = QGridLayout(suits_group)
        suits.addWidget(QLabel("suit"), 0, 0)
        suits.addWidget(QLabel("min len"), 0, 1)
        suits.addWidget(QLabel("max len"), 0, 2)
        suits.addWidget(QLabel("control"), 0, 3)
        suits.addWidget(QLabel("stopper"), 0, 4)

        self.suit_widgets: Dict[str, Dict[str, object]] = {}
        for row, (key, sym) in enumerate(self.SUITS, start=1):
            suits.addWidget(QLabel(f"{sym} {key}"), row, 0)
            min_spin = QSpinBox()
            min_spin.setRange(0, 13)
            suits.addWidget(min_spin, row, 1)
            max_spin = QSpinBox()
            max_spin.setRange(0, 13)
            max_spin.setValue(13)
            suits.addWidget(max_spin, row, 2)
            control = QComboBox()
            control.addItems(['--', '2.', '1.'])
            control.setToolTip(
                "-- = no control;\n"
                "2. = second-round (or better) control;\n"
                "1. = first-round control."
            )
            suits.addWidget(control, row, 3)
            stopper = QComboBox()
            stopper.addItems(['--', '70% +', '70% -'])
            stopper.setToolTip(
                "-- = no requirement;\n"
                "70% + = likely stopper;\n"
                "70% - = likely no stopper."
            )
            suits.addWidget(stopper, row, 4)
            self.suit_widgets[key] = {
                'min': min_spin, 'max': max_spin,
                'control': control, 'stopper': stopper,
            }
        root.addWidget(suits_group)

        # Dependencies editor + live parse status.
        dep_group = QGroupBox("Dependencies (Q-Plus DSL)")
        dep_layout = QVBoxLayout(dep_group)
        legend = QLabel(
            '<span style="color:#444">One per line. Variables: '
            'C, D, H, S (length), mC..mS (modified length), HP, LP, '
            'TP, TP(C/D/H/S), S1..S4, mS1..mS4, sC..sS (stopper '
            'score), HP~C..HP~S. Example: '
            '<tt>H &lt;= 2 &amp; S &gt;= 3 -&gt; HP &gt;= 18</tt></span>'
        )
        legend.setWordWrap(True)
        dep_layout.addWidget(legend)
        self.deps_edit = QTextEdit()
        self.deps_edit.setFont(QFont("Monospace", 11))
        self.deps_edit.textChanged.connect(self._validate_deps)
        dep_layout.addWidget(self.deps_edit)
        self.deps_status = QLabel("")
        self.deps_status.setWordWrap(True)
        dep_layout.addWidget(self.deps_status)
        root.addWidget(dep_group)

        # Button row.
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save to system…")
        save_btn.setToolTip(
            "Write this bid's meaning into "
            "CONFIG/BIDRULE/<system>.json so it can be reloaded "
            "later.")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        load_btn = QPushButton("Load from system…")
        load_btn.clicked.connect(self._on_load)
        btn_row.addWidget(load_btn)

        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    @staticmethod
    def _mk_range_pair(grid: QGridLayout, row: int, label: str):
        grid.addWidget(QLabel(label), row, 0)
        min_edit = QSpinBox()
        min_edit.setRange(0, 40)
        min_edit.setSpecialValueText("—")
        grid.addWidget(min_edit, row, 1)
        max_edit = QSpinBox()
        max_edit.setRange(0, 40)
        max_edit.setValue(40)
        max_edit.setSpecialValueText("—")
        grid.addWidget(max_edit, row, 2)
        return min_edit, max_edit

    # ------------------------------------------------------------------
    # Live dependency parser
    # ------------------------------------------------------------------

    def _validate_deps(self):
        text = self.deps_edit.toPlainText()
        if not text.strip():
            self.deps_status.setText("")
            return
        try:
            from backend.bid_dependency import parse_dependencies
            deps = parse_dependencies(text)
        except Exception as ex:
            self.deps_status.setText(
                f"<span style='color:#c00'>Parse error: {ex}</span>")
            return
        self.deps_status.setText(
            f"<span style='color:#080'>Parsed OK — "
            f"{len(deps)} clause{'s' if len(deps) != 1 else ''}.</span>"
        )

    # ------------------------------------------------------------------
    # Form ↔ data binding
    # ------------------------------------------------------------------

    def _load_form(self):
        f = self._form
        self.bid_edit.setText(f.bid)
        self.artificial_cb.setChecked(f.is_artificial)
        self.forcing_cb.setChecked(f.is_forcing)
        idx = self.name_combo.findText(f.name)
        if idx >= 0:
            self.name_combo.setCurrentIndex(idx)
        else:
            self.name_combo.setEditText(f.name)
        self.hp_min.setValue(f.hp_min if f.hp_min is not None else 0)
        self.hp_max.setValue(f.hp_max if f.hp_max is not None else 40)
        self.lp_min.setValue(f.lp_min if f.lp_min is not None else 0)
        self.lp_max.setValue(f.lp_max if f.lp_max is not None else 40)
        self.tp_min.setValue(f.tp_min if f.tp_min is not None else 0)
        self.tp_max.setValue(f.tp_max if f.tp_max is not None else 40)
        idx = self.trump_combo.findText(f.tp_trump or 'NT')
        if idx >= 0:
            self.trump_combo.setCurrentIndex(idx)
        self.aces_edit.setText(f.aces_expr)
        self.kings_edit.setText(f.kings_expr)
        for key in ('S', 'H', 'D', 'C'):
            data = getattr(f, f'suit_{key}')
            w = self.suit_widgets[key]
            w['min'].setValue(int(data.get('min', 0)))
            w['max'].setValue(int(data.get('max', 13)))
            idx = w['control'].findText(data.get('control', '--'))
            if idx >= 0:
                w['control'].setCurrentIndex(idx)
            idx = w['stopper'].findText(data.get('stopper', '--'))
            if idx >= 0:
                w['stopper'].setCurrentIndex(idx)
        self.deps_edit.setPlainText(f.dependencies)
        self._validate_deps()

    def _snapshot_form(self) -> BidMeaningForm:
        def opt(spin):
            return spin.value()
        out = BidMeaningForm(
            bid=self.bid_edit.text().strip(),
            name=self.name_combo.currentText().strip(),
            is_artificial=self.artificial_cb.isChecked(),
            is_forcing=self.forcing_cb.isChecked(),
            hp_min=opt(self.hp_min), hp_max=opt(self.hp_max),
            lp_min=opt(self.lp_min), lp_max=opt(self.lp_max),
            tp_min=opt(self.tp_min), tp_max=opt(self.tp_max),
            tp_trump=self.trump_combo.currentText(),
            aces_expr=self.aces_edit.text().strip(),
            kings_expr=self.kings_edit.text().strip(),
            dependencies=self.deps_edit.toPlainText(),
        )
        for key in ('S', 'H', 'D', 'C'):
            w = self.suit_widgets[key]
            setattr(out, f'suit_{key}', {
                'min': w['min'].value(),
                'max': w['max'].value(),
                'control': w['control'].currentText(),
                'stopper': w['stopper'].currentText(),
            })
        return out

    def get_form(self) -> BidMeaningForm:
        return self._form

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _on_ok(self):
        # Verify dependencies parse before accepting so the caller
        # doesn't end up with a corrupt entry.
        text = self.deps_edit.toPlainText().strip()
        if text:
            try:
                from backend.bid_dependency import parse_dependencies
                parse_dependencies(text)
            except Exception as ex:
                QMessageBox.warning(
                    self, "Dependency parse error",
                    f"The dependencies block has a syntax error:\n{ex}"
                )
                return
        self._form = self._snapshot_form()
        self.accept()

    @staticmethod
    def _system_file(system_key: str) -> Path:
        bidrule_dir = (Path(__file__).parent.parent.parent
                       / "CONFIG" / "BIDRULE")
        bidrule_dir.mkdir(parents=True, exist_ok=True)
        return bidrule_dir / f"{system_key}.json"

    def _on_save(self):
        form = self._snapshot_form()
        if not form.bid:
            QMessageBox.warning(
                self, "Save",
                "Enter a bid (e.g. 1NT) before saving.")
            return
        target = self._system_file(self._system_key)
        try:
            if target.exists():
                with open(target, 'r') as f:
                    catalogue = json.load(f) or {}
            else:
                catalogue = {}
        except Exception as ex:
            catalogue = {}
        catalogue[form.bid] = asdict(form)
        try:
            with open(target, 'w') as f:
                json.dump(catalogue, f, indent=2)
        except Exception as ex:
            QMessageBox.warning(self, "Save failed", str(ex))
            return
        QMessageBox.information(
            self, "Saved",
            f"{form.bid} saved to:\n{target}"
        )

    def _on_load(self):
        target = self._system_file(self._system_key)
        if not target.exists():
            QMessageBox.information(
                self, "Load",
                f"No saved meanings for {self._system_key} yet "
                f"(file would be {target}).")
            return
        try:
            with open(target, 'r') as f:
                catalogue = json.load(f) or {}
        except Exception as ex:
            QMessageBox.warning(self, "Load failed", str(ex))
            return
        if not catalogue:
            QMessageBox.information(
                self, "Load",
                f"{target.name} has no entries yet.")
            return
        # Quick prompt for which bid to load.
        from PyQt6.QtWidgets import QInputDialog
        keys = sorted(catalogue.keys())
        bid, ok = QInputDialog.getItem(
            self, "Load bid", "Pick a bid:",
            keys, 0, editable=False,
        )
        if not ok:
            return
        data = catalogue.get(bid, {})
        if not data:
            return
        # Reconstruct form, falling back to defaults for missing
        # fields so older catalogues still load.
        f = BidMeaningForm()
        for k, v in data.items():
            if hasattr(f, k):
                setattr(f, k, v)
        self._form = f
        self._load_form()
