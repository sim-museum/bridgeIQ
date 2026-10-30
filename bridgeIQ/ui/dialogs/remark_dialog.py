"""
Remark Dialog - add and view comments/remarks about deals.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QGroupBox, QCheckBox,
    QComboBox
)
from PyQt6.QtCore import Qt
from .dialog_style import apply_dialog_style


class RemarkDialog(QDialog):
    """Dialog for editing remarks/comments on a deal."""

    def __init__(self, parent=None, board=None, remark=None, readonly=False):
        super().__init__(parent)
        self.setWindowTitle("Edit Remark" if not readonly else "View Remark")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        apply_dialog_style(self)

        self.board = board
        self.remark = remark or {}
        self.readonly = readonly

        self._setup_ui()
        self._load_remark()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Deal info
        if self.board:
            board_num = getattr(self.board, 'board_number', '?')
            info_label = QLabel(f"<b>Deal: {board_num}</b>")
            info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(info_label)

        # Display timing options
        timing_group = QGroupBox("Show remark at:")
        timing_layout = QHBoxLayout(timing_group)

        self.show_start = QCheckBox("Start of deal")
        self.show_start.setEnabled(not self.readonly)
        timing_layout.addWidget(self.show_start)

        self.show_bidding = QCheckBox("After bidding")
        self.show_bidding.setEnabled(not self.readonly)
        timing_layout.addWidget(self.show_bidding)

        self.show_lead = QCheckBox("After lead")
        self.show_lead.setEnabled(not self.readonly)
        timing_layout.addWidget(self.show_lead)

        self.show_end = QCheckBox("End of play")
        self.show_end.setEnabled(not self.readonly)
        timing_layout.addWidget(self.show_end)

        timing_layout.addStretch()
        layout.addWidget(timing_group)

        # Remark text
        text_group = QGroupBox("Remark text:")
        text_layout = QVBoxLayout(text_group)

        self.remark_text = QTextEdit()
        self.remark_text.setReadOnly(self.readonly)
        self.remark_text.setPlaceholderText(
            "Enter your comments about this deal here...\n\n"
            "You can note observations, explain the correct bidding or play, "
            "or provide hints for students."
        )
        self.remark_text.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #a0a0a0;
                font-size: 12px;
            }
        """)
        text_layout.addWidget(self.remark_text)

        layout.addWidget(text_group)

        # Buttons
        button_layout = QHBoxLayout()

        if not self.readonly:
            self.ok_btn = QPushButton("OK")
            self.ok_btn.clicked.connect(self._on_ok)
            self.ok_btn.setMinimumWidth(80)
            button_layout.addWidget(self.ok_btn)

            self.clear_btn = QPushButton("Clear")
            self.clear_btn.clicked.connect(self._on_clear)
            self.clear_btn.setMinimumWidth(80)
            button_layout.addWidget(self.clear_btn)

        self.cancel_btn = QPushButton("Close" if self.readonly else "Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setMinimumWidth(80)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumWidth(80)
        button_layout.addWidget(self.help_btn)

        layout.addLayout(button_layout)

    def _load_remark(self):
        """Load existing remark data."""
        self.remark_text.setText(self.remark.get('text', ''))
        self.show_start.setChecked(self.remark.get('show_start', False))
        self.show_bidding.setChecked(self.remark.get('show_bidding', False))
        self.show_lead.setChecked(self.remark.get('show_lead', False))
        self.show_end.setChecked(self.remark.get('show_end', True))

    def _on_ok(self):
        """Save the remark."""
        self.remark = {
            'text': self.remark_text.toPlainText(),
            'show_start': self.show_start.isChecked(),
            'show_bidding': self.show_bidding.isChecked(),
            'show_lead': self.show_lead.isChecked(),
            'show_end': self.show_end.isChecked(),
        }
        self.accept()

    def _on_clear(self):
        """Clear all fields."""
        self.remark_text.clear()
        self.show_start.setChecked(False)
        self.show_bidding.setChecked(False)
        self.show_lead.setChecked(False)
        self.show_end.setChecked(True)

    def get_remark(self):
        """Return the remark data."""
        return self.remark


class ViewRemarkDialog(RemarkDialog):
    """Read-only version of the remark dialog."""

    def __init__(self, parent=None, board=None, remark=None):
        super().__init__(parent, board, remark, readonly=True)
