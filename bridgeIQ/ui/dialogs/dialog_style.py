"""
Common dialog styling for consistent light-background dialogs.
"""

DIALOG_STYLESHEET = """
    QDialog, QDialog * {
        background-color: #f0f0f0;
        color: #000000;
    }
    QDialog {
        background-color: #f0f0f0;
    }
    QWidget {
        background-color: #f0f0f0;
        color: #000000;
    }
    QGroupBox {
        background-color: #f8f8f8;
        border: 1px solid #c0c0c0;
        border-radius: 4px;
        margin-top: 16px;
        padding: 10px;
        padding-top: 20px;
        font-weight: bold;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 5px;
        background-color: #f8f8f8;
        color: #000000;
    }
    QLabel {
        background-color: transparent;
        color: #000000;
        padding: 2px;
    }
    QComboBox {
        background-color: #ffffff;
        color: #000000;
        border: 1px solid #a0a0a0;
        padding: 4px 8px;
        min-height: 20px;
    }
    QComboBox::drop-down {
        background-color: #e0e0e0;
        border-left: 1px solid #a0a0a0;
    }
    QComboBox QAbstractItemView {
        background-color: #ffffff;
        color: #000000;
        selection-background-color: #3070b0;
        selection-color: #ffffff;
    }
    QListWidget {
        background-color: #ffffff;
        color: #000000;
        border: 1px solid #a0a0a0;
    }
    QListWidget::item {
        padding: 4px;
        color: #000000;
    }
    QTextEdit, QPlainTextEdit {
        background-color: #ffffff;
        color: #000000;
        border: 1px solid #a0a0a0;
    }
    QLineEdit {
        background-color: #ffffff;
        color: #000000;
        border: 1px solid #a0a0a0;
        padding: 4px;
    }
    QSpinBox {
        background-color: #ffffff;
        color: #000000;
        border: 1px solid #a0a0a0;
        padding: 4px;
    }
    QCheckBox, QRadioButton {
        background-color: transparent;
        color: #000000;
        spacing: 8px;
        padding: 4px;
    }
    QPushButton {
        background-color: #e0e0e0;
        color: #000000;
        border: 1px solid #a0a0a0;
        padding: 6px 12px;
        border-radius: 3px;
        min-width: 60px;
    }
    QPushButton:hover {
        background-color: #d0d0d0;
    }
    QPushButton:pressed {
        background-color: #c0c0c0;
    }
    QTabWidget::pane {
        background-color: #f0f0f0;
        border: 1px solid #c0c0c0;
        padding: 5px;
    }
    QTabBar::tab {
        background-color: #e0e0e0;
        color: #000000;
        padding: 8px 16px;
        border: 1px solid #c0c0c0;
        border-bottom: none;
        margin-right: 2px;
    }
    QTabBar::tab:selected {
        background-color: #f0f0f0;
    }
    QScrollArea {
        background-color: #f0f0f0;
        border: 1px solid #c0c0c0;
    }
    QTableWidget, QTableView, QTreeWidget, QTreeView {
        background-color: #ffffff;
        color: #000000;
        border: 1px solid #a0a0a0;
        gridline-color: #c0c0c0;
    }
    QTableWidget::item, QTableView::item {
        color: #000000;
        padding: 4px;
    }
    QHeaderView::section {
        background-color: #e0e0e0;
        color: #000000;
        padding: 4px;
        border: 1px solid #c0c0c0;
    }
    QSlider::groove:horizontal {
        background-color: #c0c0c0;
        height: 6px;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background-color: #3070b0;
        width: 16px;
        margin: -5px 0;
        border-radius: 8px;
    }
    QFrame {
        background-color: #f0f0f0;
    }
"""


def apply_dialog_style(dialog):
    """Apply the common dialog stylesheet to a dialog."""
    dialog.setStyleSheet(DIALOG_STYLESHEET)


def make_detachable(dialog):
    """Promote a QDialog to a real top-level non-modal window.

    Qt's default Qt.WindowType.Dialog is treated as a parent-bound
    "tool" window by some window managers (Mutter / KWin), which keeps
    it pinned to the parent's monitor and prevents the user from
    dragging it onto a second screen. Switching to Qt.WindowType.Window
    with the standard min/max/close buttons gives a fully-detached,
    multi-monitor-friendly window. Caller still has to use show()
    instead of exec() so the parent stays interactive.
    """
    from PyQt6.QtCore import Qt
    flags = (
        Qt.WindowType.Window
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowMinMaxButtonsHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    dialog.setWindowFlags(flags)
    try:
        dialog.setModal(False)
    except AttributeError:
        # Plain QWidget — no setModal, but the flag change is enough.
        pass
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)


MESSAGEBOX_STYLESHEET = """
    QMessageBox {
        background-color: #f0f0f0;
    }
    QMessageBox QLabel {
        color: #000000;
        background-color: transparent;
        min-width: 200px;
    }
    QMessageBox QPushButton {
        background-color: #e0e0e0;
        color: #000000;
        border: 1px solid #a0a0a0;
        padding: 6px 20px;
        border-radius: 3px;
        min-width: 70px;
    }
    QMessageBox QPushButton:hover {
        background-color: #d0d0d0;
    }
"""


def styled_info(parent, title: str, message: str):
    """Show an information message box with proper styling."""
    from PyQt6.QtWidgets import QMessageBox, QDialog, QLayout, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
    from PyQt6.QtCore import Qt

    # Create a custom dialog instead of QMessageBox for reliable styling
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(400)
    dialog.setStyleSheet("""
        QDialog {
            background-color: #f0f0f0;
        }
        QLabel {
            color: #000000;
            background-color: transparent;
        }
        QPushButton {
            background-color: #e0e0e0;
            color: #000000;
            border: 1px solid #a0a0a0;
            padding: 6px 20px;
            border-radius: 3px;
            min-width: 70px;
        }
        QPushButton:hover {
            background-color: #d0d0d0;
        }
    """)

    layout = QVBoxLayout(dialog)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    # Message
    msg_label = QLabel(message)
    msg_label.setWordWrap(True)
    # Without an explicit width, QLabel's heightForWidth never gets
    # called before the dialog computes its size, so wrapped text
    # gets clipped top + bottom. Pin the wrap width to match the
    # dialog's minimum so the label can resolve its own height.
    msg_label.setMinimumWidth(380)
    msg_label.setMaximumWidth(560)
    layout.addWidget(msg_label)

    # OK button
    btn_layout = QHBoxLayout()
    btn_layout.addStretch()
    ok_btn = QPushButton("OK")
    ok_btn.clicked.connect(dialog.accept)
    btn_layout.addWidget(ok_btn)
    layout.addLayout(btn_layout)

    dialog.exec()


def styled_warning(parent, title: str, message: str):
    """Show a warning message box with proper styling."""
    from PyQt6.QtWidgets import QDialog, QLayout, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
    from PyQt6.QtCore import Qt

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(400)
    dialog.setStyleSheet("""
        QDialog {
            background-color: #f0f0f0;
        }
        QLabel {
            color: #000000;
            background-color: transparent;
        }
        QPushButton {
            background-color: #e0e0e0;
            color: #000000;
            border: 1px solid #a0a0a0;
            padding: 6px 20px;
            border-radius: 3px;
            min-width: 70px;
        }
        QPushButton:hover {
            background-color: #d0d0d0;
        }
    """)

    layout = QVBoxLayout(dialog)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    msg_label = QLabel(message)
    msg_label.setWordWrap(True)
    # Without an explicit width, QLabel's heightForWidth never gets
    # called before the dialog computes its size, so wrapped text
    # gets clipped top + bottom. Pin the wrap width to match the
    # dialog's minimum so the label can resolve its own height.
    msg_label.setMinimumWidth(380)
    msg_label.setMaximumWidth(560)
    layout.addWidget(msg_label)

    btn_layout = QHBoxLayout()
    btn_layout.addStretch()
    ok_btn = QPushButton("OK")
    ok_btn.clicked.connect(dialog.accept)
    btn_layout.addWidget(ok_btn)
    layout.addLayout(btn_layout)

    dialog.exec()


def styled_error(parent, title: str, message: str):
    """Show an error message box with proper styling."""
    from PyQt6.QtWidgets import QDialog, QLayout, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
    from PyQt6.QtCore import Qt

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(400)
    dialog.setStyleSheet("""
        QDialog {
            background-color: #f0f0f0;
        }
        QLabel {
            color: #000000;
            background-color: transparent;
        }
        QPushButton {
            background-color: #e0e0e0;
            color: #000000;
            border: 1px solid #a0a0a0;
            padding: 6px 20px;
            border-radius: 3px;
            min-width: 70px;
        }
        QPushButton:hover {
            background-color: #d0d0d0;
        }
    """)

    layout = QVBoxLayout(dialog)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    msg_label = QLabel(message)
    msg_label.setWordWrap(True)
    # Without an explicit width, QLabel's heightForWidth never gets
    # called before the dialog computes its size, so wrapped text
    # gets clipped top + bottom. Pin the wrap width to match the
    # dialog's minimum so the label can resolve its own height.
    msg_label.setMinimumWidth(380)
    msg_label.setMaximumWidth(560)
    layout.addWidget(msg_label)

    btn_layout = QHBoxLayout()
    btn_layout.addStretch()
    ok_btn = QPushButton("OK")
    ok_btn.clicked.connect(dialog.accept)
    btn_layout.addWidget(ok_btn)
    layout.addLayout(btn_layout)

    dialog.exec()
