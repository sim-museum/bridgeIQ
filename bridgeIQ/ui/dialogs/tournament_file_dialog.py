"""Tournament-file picker mirroring Q-Plus `.bdlfile-f`.

Replaces the plain QFileDialog the old `Browse…` button used. The
user picks a folder + file mask, lists the matching deal files,
clicks one to select, and sees a deal-count preview plus a
Start-deal spinbox bounded by that count. Folders are flagged
``(Folder)`` and a double-click on one navigates into it.

Read flag (Q-Plus's Cards / Bids / Lead / Tricks / Auto) is
recorded but currently advisory only — the existing BDL / LIN
readers always load the full record. Wiring real per-mode
filtering is a future slice.

Returns:
    None on Cancel; otherwise dict with keys
      ``path``           — absolute filename
      ``first_deal``     — 1-based index of the first deal to use
      ``read_mode``      — 'auto' / 'cards' / 'bids' / 'lead' / 'tricks'
      ``deal_count``     — total deals detected in the file
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QComboBox, QSpinBox,
)

from .dialog_style import apply_dialog_style


# File extensions we know how to count deals in.
_KNOWN_EXTS = ('.bdl', '.pbn', '.lin', '.ben', '.qss')
_DEFAULT_MASK = "*.bdl;*.pbn;*.lin;*.ben"


def _count_deals(path: Path) -> int:
    """Best-effort deal count for a deal file. Returns 0 when we
    don't have a reader for the format or the file doesn't parse."""
    ext = path.suffix.lower()
    try:
        if ext == '.bdl':
            from backend.bdl_reader import load_bdl_file
            deals = load_bdl_file(str(path))
            return len(deals)
        if ext == '.lin':
            from backend.lin_reader import load_lin_file
            deals = load_lin_file(str(path))
            return len(deals)
        if ext == '.pbn':
            # PBN counts a deal per "[Board ...]" tag — cheap text scan.
            n = 0
            with open(path, 'r', encoding='utf-8',
                      errors='replace') as f:
                for line in f:
                    if line.lstrip().lower().startswith('[board'):
                        n += 1
            return n
    except Exception:
        return 0
    return 0


class TournamentFileDialog(QDialog):
    """Q-Plus-style deal-file picker (`.bdlfile-f`).

    Use ``.get_result()`` after `exec()` to retrieve the picked
    file + first-deal index + read mode.
    """

    def __init__(self, parent=None, start_dir: Optional[Path] = None):
        super().__init__(parent)
        self.setWindowTitle("Select a deal file")
        self.setMinimumWidth(640)
        self.setMinimumHeight(520)
        apply_dialog_style(self)

        self._selected_path: Optional[Path] = None
        self._deal_count: int = 0
        self._result: Optional[dict] = None

        # Pick a sensible default folder: caller-supplied → DATA/
        # under bridgeIQ → user's home.
        if start_dir is None:
            here = Path(__file__).resolve().parent.parent.parent
            for candidate in (here / 'DATA', here, Path.home()):
                if candidate.exists():
                    start_dir = candidate
                    break
            else:
                start_dir = Path.home()
        self._cwd = Path(start_dir)

        self._setup_ui()
        self._refresh_list()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Folder + file-mask form.
        folder_group = QGroupBox("Folder and file mask")
        form = QFormLayout(folder_group)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(str(self._cwd))
        folder_row.addWidget(self.folder_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(browse_btn)
        form.addRow("Folder:", folder_row)

        self.mask_edit = QLineEdit(_DEFAULT_MASK)
        form.addRow("File mask:", self.mask_edit)
        root.addWidget(folder_group)

        # List button + Up-folder shortcut.
        actions = QHBoxLayout()
        list_btn = QPushButton("List")
        list_btn.setToolTip(
            "Repopulate the file list with the current folder + mask.")
        list_btn.clicked.connect(self._refresh_list)
        actions.addWidget(list_btn)

        up_btn = QPushButton("Up ↑")
        up_btn.setToolTip("Go to the parent folder.")
        up_btn.clicked.connect(self._on_up_folder)
        actions.addWidget(up_btn)
        actions.addStretch()
        root.addLayout(actions)

        # File list with (Folder) entries.
        self.file_list = QListWidget()
        self.file_list.itemSelectionChanged.connect(self._on_selection)
        self.file_list.itemDoubleClicked.connect(
            self._on_double_clicked)
        root.addWidget(self.file_list, stretch=1)

        # Deals preview + first-deal spinbox.
        preview_group = QGroupBox("Selected file")
        preview = QFormLayout(preview_group)
        self.deal_count_label = QLabel("(no file selected)")
        preview.addRow("Deals:", self.deal_count_label)
        self.first_deal_spin = QSpinBox()
        self.first_deal_spin.setRange(1, 1)
        self.first_deal_spin.setEnabled(False)
        preview.addRow("First deal:", self.first_deal_spin)

        self.read_mode = QComboBox()
        for label, value in (
            ("Auto (full record)", "auto"),
            ("Cards only", "cards"),
            ("Cards + Bids", "bids"),
            ("Cards + Bids + Lead", "lead"),
            ("Cards + Bids + Tricks", "tricks"),
        ):
            self.read_mode.addItem(label, value)
        self.read_mode.setToolTip(
            "How much of each deal to keep. Currently advisory — "
            "the readers always load the full record; per-mode "
            "filtering is a future slice."
        )
        preview.addRow("Read flag:", self.read_mode)
        root.addWidget(preview_group)

        # OK / Cancel.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.ok_btn = QPushButton("OK")
        self.ok_btn.setDefault(True)
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # List handling
    # ------------------------------------------------------------------

    def _patterns(self) -> List[str]:
        text = (self.mask_edit.text() or "").strip()
        if not text:
            return ["*"]
        return [p.strip() for p in text.split(';') if p.strip()]

    def _refresh_list(self):
        cwd_text = (self.folder_edit.text() or "").strip()
        candidate = Path(cwd_text) if cwd_text else self._cwd
        if candidate.is_dir():
            self._cwd = candidate
        self.folder_edit.setText(str(self._cwd))

        self.file_list.clear()
        self._selected_path = None
        self._deal_count = 0
        self._update_preview()

        try:
            entries = sorted(self._cwd.iterdir(),
                             key=lambda p: (not p.is_dir(),
                                            p.name.lower()))
        except Exception as ex:
            self.file_list.addItem(f"(error listing folder: {ex})")
            return

        for path in entries:
            if path.is_dir():
                item = QListWidgetItem(f"(Folder)  {path.name}")
                item.setData(Qt.ItemDataRole.UserRole,
                             ('folder', str(path)))
                self.file_list.addItem(item)

        patterns = self._patterns()
        import fnmatch
        for path in entries:
            if path.is_dir():
                continue
            name = path.name
            if not any(fnmatch.fnmatch(name.lower(), p.lower())
                       for p in patterns):
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole,
                         ('file', str(path)))
            self.file_list.addItem(item)

    def _on_selection(self):
        item = self.file_list.currentItem()
        if item is None:
            self._selected_path = None
            self._deal_count = 0
            self._update_preview()
            return
        kind, payload = item.data(Qt.ItemDataRole.UserRole) or ('', '')
        if kind != 'file':
            self._selected_path = None
            self._deal_count = 0
            self._update_preview()
            return
        self._selected_path = Path(payload)
        self._deal_count = _count_deals(self._selected_path)
        self._update_preview()

    def _on_double_clicked(self, item: QListWidgetItem):
        kind, payload = item.data(Qt.ItemDataRole.UserRole) or ('', '')
        if kind == 'folder':
            self._cwd = Path(payload)
            self.folder_edit.setText(str(self._cwd))
            self._refresh_list()
        elif kind == 'file':
            # Double-click on a file = OK, same as Q-Plus.
            self._on_ok()

    def _on_up_folder(self):
        parent = self._cwd.parent
        if parent != self._cwd:
            self._cwd = parent
            self.folder_edit.setText(str(self._cwd))
            self._refresh_list()

    def _on_browse_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select a folder", str(self._cwd))
        if chosen:
            self._cwd = Path(chosen)
            self.folder_edit.setText(str(self._cwd))
            self._refresh_list()

    def _update_preview(self):
        if self._selected_path is None:
            self.deal_count_label.setText("(no file selected)")
            self.first_deal_spin.setRange(1, 1)
            self.first_deal_spin.setValue(1)
            self.first_deal_spin.setEnabled(False)
            self.ok_btn.setEnabled(False)
            return
        n = self._deal_count
        if n <= 0:
            self.deal_count_label.setText(
                f"{self._selected_path.name} — "
                "(unknown format / parse error)")
            self.first_deal_spin.setRange(1, 1)
            self.first_deal_spin.setValue(1)
            self.first_deal_spin.setEnabled(False)
        else:
            self.deal_count_label.setText(
                f"{self._selected_path.name} — "
                f"<b>{n}</b> deal{'s' if n != 1 else ''}")
            self.first_deal_spin.setRange(1, max(1, n))
            cur = self.first_deal_spin.value()
            self.first_deal_spin.setValue(min(cur, n))
            self.first_deal_spin.setEnabled(True)
        self.ok_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def _on_ok(self):
        if self._selected_path is None:
            return
        self._result = {
            'path': str(self._selected_path),
            'first_deal': int(self.first_deal_spin.value()),
            'read_mode': self.read_mode.currentData() or 'auto',
            'deal_count': int(self._deal_count),
        }
        self.accept()

    def get_result(self) -> Optional[dict]:
        return self._result
