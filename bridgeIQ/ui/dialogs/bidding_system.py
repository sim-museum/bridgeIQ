"""
Bidding System Selection Dialog - Choose and configure bidding systems.

Two catalogs exist:

  * BEN catalog (3 systems) — what BEN's neural net was trained on.
    Used only when ``preferences.bidding_engine == "BEN"``.
  * Native catalog (biq's five systems) — the default. Pulled live from
    ``backend.bidding_systems.list_systems()`` so any new system
    added to the catalog appears here automatically.

The dialog picks the catalog based on the current preference at open
time; switching engines in Preferences and re-opening this dialog
shows the new catalog.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QListWidget, QListWidgetItem,
    QTabWidget, QWidget, QCheckBox, QGridLayout, QMessageBox,
    QTextEdit, QSplitter, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

import os
from pathlib import Path
from typing import Optional, Dict, List

from .dialog_style import apply_dialog_style


# Legacy catalog — only the three systems BEN was trained on. Used
# when preferences.bidding_engine == "BEN".
BEN_BIDDING_SYSTEMS = {
    "SAYC": {
        "name": "SAYC (Standard American Yellow Card)",
        "description": "Standard American with 5-card majors, strong NT (15-17), and standard conventions.",
        "conventions": ["Stayman", "Jacoby Transfers", "Blackwood", "Gerber", "Negative Doubles"],
    },
    "2/1": {
        "name": "2/1 Game Force",
        "description": "Modern 2/1 system with game-forcing 2-level responses, Bergen raises, and 1NT forcing.",
        "conventions": ["Stayman", "Jacoby Transfers", "Blackwood", "RKCB 1430", "Jacoby 2NT", "Bergen Raises", "Negative Doubles", "Support Doubles"],
    },
    "GIB": {
        "name": "GIB (BBO Default)",
        "description": "GIB robot bidding system used on BridgeBase Online. Similar to SAYC with some variations.",
        "conventions": ["Stayman", "Jacoby Transfers", "Blackwood", "Negative Doubles", "Weak Two Bids"],
    },
}


def _native_catalog() -> Dict[str, dict]:
    """Build the dialog's view of biq's systems.

    Each entry mirrors the BEN-catalog shape (`name` / `description` /
    `conventions`) so the rest of the dialog code can treat them
    uniformly. Conventions are derived from the system's `conventions`
    set, mapping the system settings onto user-friendly labels.
    """
    try:
        from backend.bidding_systems import get_system, list_systems
    except Exception:
        return {}

    out: Dict[str, dict] = {}
    for name in list_systems():
        try:
            s = get_system(name)
        except Exception:
            continue
        conv_labels = _convention_labels_for(s)
        out[name] = {
            "name": f"{name} — {s.description or name}",
            "description": _native_system_summary(s),
            "conventions": conv_labels,
        }
    return out


def _native_system_summary(s) -> str:
    """One-paragraph human summary of a BiddingSystem spec.

    Pulled from the dataclass fields rather than the system file's description
    so the user sees the actual numeric ranges the bidder uses.
    """
    lines = []
    lines.append(s.description or s.name)
    lines.append("")
    lines.append(f"• 1NT: {s.one_nt_min_hcp}-{s.one_nt_max_hcp} HCP")
    lines.append(f"• 2NT: {s.two_nt_min_hcp}-{s.two_nt_max_hcp} HCP")
    lines.append(f"• Strong opening: {s.strong_open_call} ({s.strong_open_min_hcp}+ HCP)")
    lines.append(f"• Majors: {s.one_major_card_min}+ card opening")
    lines.append(f"• 2-over-1: {s.two_over_one_min_hcp}+ HCP")
    lines.append(f"• RKC: {s.rkc_variant}")
    lines.append(f"• Conventions enabled: {len(s.conventions)}")
    return "\n".join(lines)


# Map system settings to user-facing convention labels. Falls back to
# the bare flag name for anything we haven't given a friendly tag.
_FLAG_TO_LABEL = {
    "nt.stayman": "Stayman",
    "nt.transfers.always": "Jacoby Transfers",
    "nt.transfers": "Jacoby Transfers",
    "nt.texas": "Texas Transfers",
    "resp.major.splinters": "Splinter Bids",
    "resp.major.truscott_2nt": "Jacoby 2NT",
    "resp.major.2nt_limit_raise": "2NT as Limit Raise",
    "resp.bergen_raises": "Bergen Raises",
    "resp.strong_2c.2d_negative": "2C waiting / negative 2D",
    "resp.inverted_minors": "Inverted Minors",
    "resp.strong_1c.switch_1h_1nt": "Precision 1NT/1H switch",
    "comp.negative_double.through-2S": "Negative Doubles",
    "comp.negative_double": "Negative Doubles",
    "comp.support_doubles": "Support Doubles",
    "comp.responsive_doubles": "Responsive Doubles",
    "gen.new_minor_forcing": "New Minor Forcing",
    "gen.fourth_suit_forcing": "Fourth Suit Forcing",
    "gen.drury": "Drury",
    "def.michaels": "Michaels Cue Bid",
    "def.unusual_nt": "Unusual 2NT",
    "def.1nt.landy": "Landy (over 1NT)",
    "def.1nt.dont": "DONT (over 1NT)",
    "def.1nt.cappelletti": "Cappelletti (over 1NT)",
    "def.weak_jump_overcalls.all_suits": "Weak Jump Overcalls",
    "def.weak_jump_overcalls": "Weak Jump Overcalls",
    "def.strong_1c.double_majors": "Defence to Strong 1C (X=majors)",
    "slam.rkc.1430": "RKCB 1430",
    "slam.rkc.0314": "RKCB 0314",
    "slam.gerber": "Gerber",
    "open.2nt.range-20-21": "Strong 2NT (20-21)",
    "open.2major.weak": "Weak Two Majors",
    "open.2d.weak": "Weak 2D",
    "open.3nt.gambling_any": "Gambling 3NT",
    "open.strong_2c": "Strong 2C",
    "open.strong_1c.min_hcp-16": "Precision Strong 1C",
    "open.2d.three_suiter.majors-4-3": "Precision 2D (3-suiter)",
    "rebid.1nt_after_1_1.width-3": "1NT rebid (12-14)",
    "rebid.weak2.ogust": "Ogust",
    "resp.weak2.ogust_any": "Ogust",
}


def _convention_labels_for(system) -> List[str]:
    """Friendly labels for the conventions enabled in `system`.

    De-duplicates (so "Jacoby Transfers" doesn't appear twice when both
    the always-variant and the bare flag are present) and sorts the
    result for stable display.
    """
    seen = set()
    out: List[str] = []
    for flag in system.conventions:
        label = _FLAG_TO_LABEL.get(flag)
        if label is None:
            # Skip internal settings that we don't surface to users
            # (B- opening structure, parameter-only flags, etc.).
            continue
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return sorted(out)


def _resolve_catalog() -> Dict[str, dict]:
    """Return the catalog matching the user's preferences.bidding_engine."""
    try:
        from backend.config import get_config_manager
        engine = (get_config_manager().config.preferences.bidding_engine
                  or "native")
    except Exception:
        engine = "native"
    if engine == "BEN":
        return BEN_BIDDING_SYSTEMS
    cat = _native_catalog()
    return cat if cat else BEN_BIDDING_SYSTEMS

# Common conventions that can be toggled
CONVENTIONS = [
    ("Stayman", "2♣ response to 1NT asks for 4-card major"),
    ("Jacoby Transfers", "2♦/2♥ over 1NT transfers to hearts/spades"),
    ("Texas Transfers", "4♦/4♥ over 1NT transfers to hearts/spades at game level"),
    ("Blackwood", "4NT asks for aces (0-3 step responses)"),
    ("RKCB 1430", "Roman Key Card Blackwood with 1430 responses"),
    ("RKCB 3014", "Roman Key Card Blackwood with 3014 responses"),
    ("Gerber", "4♣ asks for aces after NT opening"),
    ("Jacoby 2NT", "Game-forcing raise of major with 4+ support"),
    ("Bergen Raises", "3♣/3♦ as constructive/limit raises of major"),
    ("Splinter Bids", "Double jump shift shows singleton/void with support"),
    ("Negative Doubles", "Double of overcall is takeout, not penalty"),
    ("Support Doubles", "Opener's double shows 3-card support"),
    ("Responsive Doubles", "Double by advancer after partner overcalls"),
    ("Lebensohl", "2NT as relay after opponent's interference"),
    ("New Minor Forcing", "Bid of new minor is artificial and forcing"),
    ("Fourth Suit Forcing", "Fourth suit bid is artificial and forcing"),
    ("Drury", "2♣ response to 3rd/4th seat major opening"),
    ("Weak Two Bids", "2♦/2♥/2♠ = 6-card suit, 5-10 HCP"),
    ("Strong 2♣", "2♣ opening = 22+ HCP or 8.5+ tricks"),
    ("Multi 2♦", "2♦ = weak two in either major"),
    ("Michaels Cue Bid", "Direct cue bid shows two-suiter"),
    ("Unusual 2NT", "Jump to 2NT shows minors"),
]


class BiddingSystemDialog(QDialog):
    """Dialog for selecting and configuring bidding systems."""

    def __init__(self, parent=None, current_system: str = "SAYC"):
        super().__init__(parent)
        self.current_system = current_system
        self.selected_conventions: Dict[str, bool] = {}

        # Pick the catalog matching the active engine. The native
        # catalog is the default (biq's five systems); BEN catalog is
        # used only when preferences.bidding_engine == "BEN".
        self.bidding_systems = _resolve_catalog()
        # Remember the engine label so the title and the user can tell
        # at a glance which catalog they're looking at.
        try:
            from backend.config import get_config_manager
            self._engine_label = (
                "BridgeIQ" if get_config_manager().config.preferences.bidding_engine
                == "BEN" else "Native")
        except Exception:
            self._engine_label = "Native"

        self.setWindowTitle(f"Bidding Systems — {self._engine_label}")
        self.setMinimumWidth(700)
        self.setMinimumHeight(550)
        apply_dialog_style(self)

        self._setup_ui()
        self._load_system(current_system)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Create tabs
        tabs = QTabWidget()

        # System Selection Tab
        system_tab = QWidget()
        system_layout = QVBoxLayout(system_tab)

        # System selector
        selector_group = QGroupBox("Select Bidding System")
        selector_layout = QHBoxLayout()

        selector_layout.addWidget(QLabel("System:"))
        self.system_combo = QComboBox()
        for key, info in self.bidding_systems.items():
            self.system_combo.addItem(info["name"], key)
        self.system_combo.currentIndexChanged.connect(self._on_system_changed)
        selector_layout.addWidget(self.system_combo, stretch=1)

        selector_group.setLayout(selector_layout)
        system_layout.addWidget(selector_group)

        # System description
        desc_group = QGroupBox("System Description")
        desc_layout = QVBoxLayout()

        self.desc_text = QTextEdit()
        self.desc_text.setReadOnly(True)
        self.desc_text.setMaximumHeight(100)
        desc_layout.addWidget(self.desc_text)

        desc_group.setLayout(desc_layout)
        system_layout.addWidget(desc_group)

        # Included conventions
        conv_group = QGroupBox("Included Conventions")
        conv_layout = QVBoxLayout()

        self.conventions_list = QListWidget()
        self.conventions_list.setMaximumHeight(150)
        conv_layout.addWidget(self.conventions_list)

        conv_group.setLayout(conv_layout)
        system_layout.addWidget(conv_group)

        # Bid-meaning editor. Opens the
        # full per-bid editor (HP/LP/TP ranges, per-suit fields, the
        # dependency DSL) for the currently selected system.
        bid_meaning_row = QHBoxLayout()
        self.edit_bid_meaning_btn = QPushButton("Edit bid meaning…")
        self.edit_bid_meaning_btn.setToolTip(
            "Open the per-bid editor for the currently selected "
            "system. Lets you set HP/LP/TP "
            "ranges, per-suit length / control / stopper, and the "
            "full dependency-expression DSL "
            "(H <= 2 & S >= 3 -> HP >= 18 etc.)."
        )
        self.edit_bid_meaning_btn.clicked.connect(
            self._on_edit_bid_meaning)
        bid_meaning_row.addWidget(self.edit_bid_meaning_btn)
        bid_meaning_row.addStretch()
        system_layout.addLayout(bid_meaning_row)

        system_layout.addStretch()
        tabs.addTab(system_tab, "System")

        # Conventions Tab
        conventions_tab = QWidget()
        conventions_layout = QVBoxLayout(conventions_tab)

        conventions_layout.addWidget(QLabel("Enable/disable individual conventions:"))

        # Scrollable convention checkboxes
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        conv_widget = QWidget()
        conv_widget.setStyleSheet("background-color: #ffffff;")
        conv_grid = QGridLayout(conv_widget)
        conv_grid.setColumnStretch(1, 1)

        self.convention_checks: Dict[str, QCheckBox] = {}
        for i, (name, desc) in enumerate(CONVENTIONS):
            check = QCheckBox(name)
            check.setToolTip(desc)
            self.convention_checks[name] = check
            conv_grid.addWidget(check, i, 0)

            desc_label = QLabel(desc)
            desc_label.setStyleSheet("color: #333333; background-color: transparent;")
            desc_label.setWordWrap(True)
            conv_grid.addWidget(desc_label, i, 1)

        scroll.setWidget(conv_widget)
        conventions_layout.addWidget(scroll)

        tabs.addTab(conventions_tab, "Conventions")

        # N/S vs E/W Tab
        pairs_tab = QWidget()
        pairs_layout = QVBoxLayout(pairs_tab)

        pairs_layout.addWidget(QLabel("Configure different systems for each pair:"))

        ns_group = QGroupBox("North-South System")
        ns_layout = QHBoxLayout()
        ns_layout.addWidget(QLabel("System:"))
        self.ns_system_combo = QComboBox()
        for key, info in self.bidding_systems.items():
            self.ns_system_combo.addItem(info["name"], key)
        self.ns_system_combo.currentIndexChanged.connect(self._on_ns_system_changed)
        ns_layout.addWidget(self.ns_system_combo, stretch=1)
        ns_group.setLayout(ns_layout)
        pairs_layout.addWidget(ns_group)

        ew_group = QGroupBox("East-West System")
        ew_layout = QHBoxLayout()
        ew_layout.addWidget(QLabel("System:"))
        self.ew_system_combo = QComboBox()
        for key, info in self.bidding_systems.items():
            self.ew_system_combo.addItem(info["name"], key)
        ew_layout.addWidget(self.ew_system_combo, stretch=1)
        ew_group.setLayout(ew_layout)
        pairs_layout.addWidget(ew_group)

        self.same_system_check = QCheckBox("Use same system for both pairs")
        self.same_system_check.setChecked(False)  # Default unchecked so both can be set independently
        self.same_system_check.toggled.connect(self._on_same_system_toggled)
        pairs_layout.addWidget(self.same_system_check)

        pairs_layout.addStretch()
        tabs.addTab(pairs_tab, "N/S vs E/W")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()

        load_btn = QPushButton("Load from File...")
        load_btn.clicked.connect(self._on_load_file)
        button_layout.addWidget(load_btn)

        save_btn = QPushButton("Save to File...")
        save_btn.clicked.connect(self._on_save_file)
        button_layout.addWidget(save_btn)

        button_layout.addStretch()

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply)
        button_layout.addWidget(apply_btn)

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

        # Initial state - both combos enabled by default
        self._on_same_system_toggled(False)

    def _load_system(self, system_key: str):
        """Load a bidding system configuration."""
        # Normalise legacy aliases first (BEN catalog only).
        key_map = {
            "21GF": "2/1",
            "2/1GF": "2/1",
            "2-1": "2/1",
        }
        system_key = key_map.get(system_key.upper(), system_key)

        # For the native catalog, also accept any alias the systems
        # module recognises (Precision → Precision90M, Wbridge5 →
        # TwoOverOne, etc.) so saved prefs from earlier sessions keep
        # working after the rename.
        if system_key not in self.bidding_systems:
            try:
                from backend.bidding_systems import get_system
                canonical = get_system(system_key).name
                if canonical in self.bidding_systems:
                    system_key = canonical
            except Exception:
                pass

        if system_key not in self.bidding_systems:
            system_key = "SAYC" if "SAYC" in self.bidding_systems \
                else next(iter(self.bidding_systems))

        system = self.bidding_systems[system_key]

        # Update combo box
        for i in range(self.system_combo.count()):
            if self.system_combo.itemData(i) == system_key:
                self.system_combo.setCurrentIndex(i)
                break

        # Update description
        self.desc_text.setText(system["description"])

        # Update conventions list
        self.conventions_list.clear()
        for conv in system["conventions"]:
            self.conventions_list.addItem(conv)

        # Update convention checkboxes
        for name, check in self.convention_checks.items():
            check.setChecked(name in system["conventions"])

        self.current_system = system_key

    def _on_system_changed(self, index: int):
        """Handle system selection change on System tab."""
        system_key = self.system_combo.itemData(index)
        self._load_system(system_key)
        # Sync N/S combo on the pairs tab
        self.ns_system_combo.blockSignals(True)
        self.ns_system_combo.setCurrentIndex(index)
        self.ns_system_combo.blockSignals(False)
        if self.same_system_check.isChecked():
            self.ew_system_combo.setCurrentIndex(index)

    def _on_ns_system_changed(self, index: int):
        """Handle N/S system selection change on pairs tab."""
        system_key = self.ns_system_combo.itemData(index)
        self.current_system = system_key
        # Sync the main System tab combo
        self.system_combo.blockSignals(True)
        self.system_combo.setCurrentIndex(index)
        self.system_combo.blockSignals(False)
        # Update description and conventions
        if system_key in self.bidding_systems:
            system = self.bidding_systems[system_key]
            self.desc_text.setText(system["description"])
            self.conventions_list.clear()
            for conv in system["conventions"]:
                self.conventions_list.addItem(conv)
        # Sync E/W if same system is checked
        if self.same_system_check.isChecked():
            self.ew_system_combo.setCurrentIndex(index)

    def _on_same_system_toggled(self, checked: bool):
        """Handle same system checkbox toggle."""
        self.ew_system_combo.setEnabled(not checked)
        if checked:
            self.ew_system_combo.setCurrentIndex(self.ns_system_combo.currentIndex())

    def _on_edit_bid_meaning(self):
        """Open the per-bid editor for the current system. The
        editor handles its own JSON save/load under
        CONFIG/BIDRULE/<system>.json so this caller just spawns
        it; no state to round-trip back."""
        from .bid_meaning_editor import (
            BidMeaningEditorDialog, BidMeaningForm)
        # Prompt for which bid to edit (free-text — Q-Plus accepts
        # any bid string, including auction-context ones like "1NT
        # over 1S"; we accept any value the user enters).
        from PyQt6.QtWidgets import QInputDialog
        bid, ok = QInputDialog.getText(
            self, "Bid to edit",
            "Enter the bid (e.g. 1NT, 2C, X):",
            text="",
        )
        if not ok or not bid.strip():
            return
        dlg = BidMeaningEditorDialog(
            system_key=self.current_system,
            bid=bid.strip(),
            parent=self,
        )
        dlg.exec()

    def _on_load_file(self):
        """Load a bidding system from a JSON file. The file format
        matches what ``_on_save_file`` writes — ``{name, description,
        conventions}`` — and is also compatible with Q-Plus's BSC
        text files at the line level (name=…, description=…,
        conventions=A,B,C). The loaded system is added to
        ``self.bidding_systems`` keyed by its name; the dialog
        reloads to display it."""
        from PyQt6.QtWidgets import QFileDialog
        import json
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load Bidding System", "CONFIG/BIDRULE/",
            "Bidding System Files (*.bsc *.json);;All Files (*)"
        )
        if not filename:
            return
        try:
            data = self._parse_system_file(filename)
        except Exception as ex:
            QMessageBox.warning(
                self, "Load failed",
                f"Could not read {filename}:\n{ex!r}")
            return
        name = (data.get('name') or
                os.path.splitext(os.path.basename(filename))[0])
        self.bidding_systems[name] = {
            'name': name,
            'description': data.get('description', ''),
            'conventions': list(data.get('conventions', [])),
        }
        # Re-populate the system combo to include the new entry.
        present = [self.system_combo.itemData(i)
                   for i in range(self.system_combo.count())]
        if name not in present:
            self.system_combo.addItem(name, name)
            self.ns_system_combo.addItem(name, name)
            self.ew_system_combo.addItem(name, name)
        self._load_system(name)
        QMessageBox.information(
            self, "Load",
            f"Loaded bidding system '{name}' from "
            f"{os.path.basename(filename)}.")

    def _on_save_file(self):
        """Save the currently displayed system to a JSON file under
        CONFIG/BIDRULE/. The file is symmetric with ``_on_load_file``:
        ``{name, description, conventions}``."""
        from PyQt6.QtWidgets import QFileDialog
        import json
        if not self.current_system:
            QMessageBox.information(
                self, "Save", "No system currently selected.")
            return
        system = self.bidding_systems.get(self.current_system, {})
        # Refresh from the live UI in case the user edited inline.
        description = self.desc_text.toPlainText()
        conventions = []
        for i in range(self.conventions_list.count()):
            conventions.append(self.conventions_list.item(i).text())
        suggested = self.current_system.replace('/', '-')
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Bidding System",
            f"CONFIG/BIDRULE/{suggested}.json",
            "Bidding System Files (*.json *.bsc);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(('.json', '.bsc')):
            filename = filename + '.json'
        payload = {
            'name': self.current_system,
            'description': description or system.get('description', ''),
            'conventions': conventions,
        }
        try:
            os.makedirs(os.path.dirname(filename) or '.', exist_ok=True)
            with open(filename, 'w', encoding='utf-8') as f:
                if filename.lower().endswith('.bsc'):
                    # Q-Plus BSC: line-based key=value.
                    f.write(f"name={payload['name']}\n")
                    f.write(f"description={payload['description']}\n")
                    f.write("conventions="
                            + ",".join(payload['conventions'])
                            + "\n")
                else:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as ex:
            QMessageBox.warning(
                self, "Save failed",
                f"Could not write {filename}:\n{ex!r}")
            return
        QMessageBox.information(
            self, "Save",
            f"Saved '{self.current_system}' to "
            f"{os.path.basename(filename)}.")

    @staticmethod
    def _parse_system_file(filename: str) -> dict:
        """Read a saved bidding-system file in either JSON or Q-Plus
        BSC line-key=value form. Returns a dict with name /
        description / conventions keys (missing fields become empty
        strings or empty list)."""
        import json
        with open(filename, 'r', encoding='utf-8') as f:
            text = f.read()
        try:
            return json.loads(text)
        except Exception:
            pass
        out = {'name': '', 'description': '', 'conventions': []}
        for line in text.splitlines():
            line = line.strip()
            if not line or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k = k.strip().lower()
            v = v.strip()
            if k == 'name':
                out['name'] = v
            elif k == 'description':
                out['description'] = v
            elif k == 'conventions':
                out['conventions'] = [
                    c.strip() for c in v.split(',') if c.strip()]
        return out

    def _on_apply(self):
        """Apply the current settings without closing."""
        self._apply_settings()
        # Use the N/S system (which is the primary system selection)
        system_key = self.ns_system_combo.currentData()
        self.current_system = system_key
        # Also update the main System tab combo to stay in sync
        for i in range(self.system_combo.count()):
            if self.system_combo.itemData(i) == system_key:
                self.system_combo.blockSignals(True)
                self.system_combo.setCurrentIndex(i)
                self.system_combo.blockSignals(False)
                break
        from .dialog_style import styled_info
        ns_system = self.bidding_systems[self.ns_system_combo.currentData()]['name']
        ew_system = self.bidding_systems[self.ew_system_combo.currentData()]['name']
        if ns_system == ew_system:
            styled_info(self, "Applied", f"Bidding system set to: {ns_system}")
        else:
            styled_info(self, "Applied", f"N/S system: {ns_system}\nE/W system: {ew_system}")

    def _apply_settings(self):
        """Apply bidding system settings."""
        # Collect enabled conventions
        self.selected_conventions = {}
        for name, check in self.convention_checks.items():
            self.selected_conventions[name] = check.isChecked()

    def get_system(self) -> str:
        """Get the selected system key."""
        return self.system_combo.currentData()

    def get_ns_system(self) -> str:
        """Get N/S system key."""
        return self.ns_system_combo.currentData()

    def get_ew_system(self) -> str:
        """Get E/W system key."""
        if self.same_system_check.isChecked():
            return self.ns_system_combo.currentData()
        return self.ew_system_combo.currentData()

    def get_conventions(self) -> Dict[str, bool]:
        """Get selected conventions."""
        return {name: check.isChecked() for name, check in self.convention_checks.items()}
