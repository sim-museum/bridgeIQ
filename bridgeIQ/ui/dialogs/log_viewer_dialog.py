"""
Log Viewer Dialog - displays log files in a text viewer.
"""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog, QComboBox,
    QGroupBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from .dialog_style import apply_dialog_style


class LogViewerDialog(QDialog):
    """Dialog for viewing log files."""

    def __init__(self, parent=None, log_path=None, title="Log Viewer"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        apply_dialog_style(self)

        self.log_path = log_path
        self.log_dir = None

        self._setup_ui()
        if log_path:
            self._load_file(log_path)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # File info
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("File:"))
        self.file_label = QLabel("<i>No file loaded</i>")
        self.file_label.setStyleSheet("font-weight: bold;")
        file_layout.addWidget(self.file_label)
        file_layout.addStretch()

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._on_browse)
        file_layout.addWidget(self.browse_btn)

        layout.addLayout(file_layout)

        # Text display
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setFont(QFont("Courier New", 10))
        self.text_view.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #a0a0a0;
            }
        """)
        self.text_view.setPlaceholderText("Log file contents will appear here...")
        layout.addWidget(self.text_view)

        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #606060;")
        layout.addWidget(self.status_label)

        # Buttons
        button_layout = QHBoxLayout()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.refresh_btn.setMinimumWidth(80)
        button_layout.addWidget(self.refresh_btn)

        self.copy_btn = QPushButton("Copy All")
        self.copy_btn.clicked.connect(self._on_copy)
        self.copy_btn.setMinimumWidth(80)
        button_layout.addWidget(self.copy_btn)

        button_layout.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setMinimumWidth(80)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

    def _load_file(self, path):
        """Load and display a log file."""
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                self.text_view.setText(content)
                self.file_label.setText(os.path.basename(path))
                self.log_path = path
                self.log_dir = os.path.dirname(path)

                # Update status
                file_size = os.path.getsize(path)
                line_count = content.count('\n') + 1
                self.status_label.setText(
                    f"Size: {file_size:,} bytes | Lines: {line_count:,}"
                )
            else:
                self.text_view.setText(f"File not found: {path}")
                self.status_label.setText("Error: File not found")
        except Exception as e:
            self.text_view.setText(f"Error loading file:\n{str(e)}")
            self.status_label.setText(f"Error: {str(e)}")

    def _on_browse(self):
        """Browse for a log file."""
        start_dir = self.log_dir or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Log File",
            start_dir,
            "Log Files (*.log *.txt *.bdl *.pbn);;All Files (*)"
        )
        if path:
            self._load_file(path)

    def _on_refresh(self):
        """Reload the current file."""
        if self.log_path:
            self._load_file(self.log_path)

    def _on_copy(self):
        """Copy all text to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_view.toPlainText())
        self.status_label.setText("Copied to clipboard")


class PreviousLogsDialog(QDialog):
    """Dialog for selecting and viewing previous log files."""

    def __init__(self, parent=None, log_dir=None):
        super().__init__(parent)
        self.setWindowTitle("Previous Log Files")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        apply_dialog_style(self)

        self.log_dir = log_dir or self._find_log_dir()
        self.log_files = []

        self._setup_ui()
        self._scan_logs()

    def _find_log_dir(self):
        """Find the default log directory."""
        # Try common locations
        possible_dirs = [
            os.path.join(os.path.expanduser("~"), ".bridgeIQ", "logs"),
            os.path.join(os.getcwd(), "data", "log"),
            os.path.join(os.getcwd(), "logs"),
        ]
        for d in possible_dirs:
            if os.path.isdir(d):
                return d
        return os.getcwd()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Directory info
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Log directory:"))
        self.dir_label = QLabel(self.log_dir)
        self.dir_label.setStyleSheet("font-weight: bold;")
        dir_layout.addWidget(self.dir_label)
        dir_layout.addStretch()

        self.change_dir_btn = QPushButton("Change...")
        self.change_dir_btn.clicked.connect(self._on_change_dir)
        dir_layout.addWidget(self.change_dir_btn)

        layout.addLayout(dir_layout)

        # File list
        list_group = QGroupBox("Available log files:")
        list_layout = QVBoxLayout(list_group)

        self.file_combo = QComboBox()
        self.file_combo.setMinimumHeight(30)
        list_layout.addWidget(self.file_combo)

        layout.addWidget(list_group)

        # Buttons
        button_layout = QHBoxLayout()

        self.view_btn = QPushButton("View")
        self.view_btn.clicked.connect(self._on_view)
        self.view_btn.setMinimumWidth(80)
        button_layout.addWidget(self.view_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._on_delete)
        self.delete_btn.setMinimumWidth(80)
        button_layout.addWidget(self.delete_btn)

        button_layout.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setMinimumWidth(80)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

    def _scan_logs(self):
        """Scan for log files in the directory."""
        self.file_combo.clear()
        self.log_files = []

        if os.path.isdir(self.log_dir):
            try:
                files = os.listdir(self.log_dir)
                # Sort by modification time, newest first
                files_with_time = []
                for f in files:
                    if f.endswith(('.log', '.txt', '.bdl', '.pbn')):
                        path = os.path.join(self.log_dir, f)
                        mtime = os.path.getmtime(path)
                        files_with_time.append((f, mtime))

                files_with_time.sort(key=lambda x: x[1], reverse=True)

                for f, mtime in files_with_time:
                    self.log_files.append(f)
                    self.file_combo.addItem(f)

            except Exception as e:
                self.file_combo.addItem(f"Error scanning: {e}")

        if not self.log_files:
            self.file_combo.addItem("(No log files found)")
            self.view_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
        else:
            self.view_btn.setEnabled(True)
            self.delete_btn.setEnabled(True)

    def _on_change_dir(self):
        """Change the log directory."""
        new_dir = QFileDialog.getExistingDirectory(
            self, "Select Log Directory", self.log_dir
        )
        if new_dir:
            self.log_dir = new_dir
            self.dir_label.setText(new_dir)
            self._scan_logs()

    def _on_view(self):
        """View the selected log file."""
        if self.log_files:
            idx = self.file_combo.currentIndex()
            if 0 <= idx < len(self.log_files):
                path = os.path.join(self.log_dir, self.log_files[idx])
                viewer = LogViewerDialog(self, path)
                viewer.exec()

    def _on_delete(self):
        """Delete the selected log file."""
        from PyQt6.QtWidgets import QMessageBox

        if self.log_files:
            idx = self.file_combo.currentIndex()
            if 0 <= idx < len(self.log_files):
                filename = self.log_files[idx]
                reply = QMessageBox.question(
                    self,
                    "Delete Log File",
                    f"Are you sure you want to delete '{filename}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        path = os.path.join(self.log_dir, filename)
                        os.remove(path)
                        self._scan_logs()
                    except Exception as e:
                        QMessageBox.warning(self, "Error", f"Could not delete file:\n{e}")
