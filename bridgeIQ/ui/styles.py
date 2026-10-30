"""
Global stylesheet for BridgeIQ application.
Provides consistent light-background styling across all widgets.
"""

from enum import IntEnum


class SuitColorMode(IntEnum):
    """Color mode for suit symbols."""
    TRADITIONAL = 0  # Black (spades/clubs) and Red (hearts/diamonds)
    FOUR_COLOR = 1   # Black spades, Red hearts, Violet diamonds, Green clubs


# Traditional colors (2-color: black and red)
TRADITIONAL_SUIT_COLORS = {
    'spades': '#000000',
    'hearts': '#d23b3b',
    'diamonds': '#d23b3b',
    'clubs': '#000000',
}

# Four-color mode (each suit has distinct color)
FOUR_COLOR_SUIT_COLORS = {
    'spades': '#000000',    # Black
    'hearts': '#d23b3b',    # Red
    'diamonds': '#0000cc',  # Blue
    'clubs': '#006400',     # Dark Green
}

# Background colors for bid buttons (light versions)
TRADITIONAL_SUIT_BG_COLORS = {
    'spades': '#e8e8f8',    # Light blue-gray
    'hearts': '#ffe8e8',    # Light pink
    'diamonds': '#ffe8e8',  # Light pink
    'clubs': '#e8e8e8',     # Light gray
}

FOUR_COLOR_SUIT_BG_COLORS = {
    'spades': '#e8e8f8',    # Light blue-gray
    'hearts': '#ffe8e8',    # Light pink
    'diamonds': '#e0e8f8',  # Light blue
    'clubs': '#e8f8e8',     # Light green
}

# Global setting for color mode (can be changed at runtime)
_current_color_mode = SuitColorMode.FOUR_COLOR


def set_suit_color_mode(mode: SuitColorMode):
    """Set the global suit color mode."""
    global _current_color_mode
    _current_color_mode = mode


def get_suit_color_mode() -> SuitColorMode:
    """Get the current suit color mode."""
    return _current_color_mode


def get_suit_color(suit_name: str) -> str:
    """Get the color for a suit based on current color mode.

    Args:
        suit_name: One of 'spades', 'hearts', 'diamonds', 'clubs'

    Returns:
        Hex color string
    """
    suit_name = suit_name.lower()
    if _current_color_mode == SuitColorMode.FOUR_COLOR:
        return FOUR_COLOR_SUIT_COLORS.get(suit_name, '#000000')
    else:
        return TRADITIONAL_SUIT_COLORS.get(suit_name, '#000000')


def get_suit_bg_color(suit_name: str) -> str:
    """Get the background color for a suit based on current color mode.

    Args:
        suit_name: One of 'spades', 'hearts', 'diamonds', 'clubs'

    Returns:
        Hex color string
    """
    suit_name = suit_name.lower()
    if _current_color_mode == SuitColorMode.FOUR_COLOR:
        return FOUR_COLOR_SUIT_BG_COLORS.get(suit_name, '#e8e8e8')
    else:
        return TRADITIONAL_SUIT_BG_COLORS.get(suit_name, '#e8e8e8')


def get_suit_colors_dict():
    """Get a dictionary mapping Suit enum values to colors.

    Returns:
        Dict mapping suit names to color strings
    """
    if _current_color_mode == SuitColorMode.FOUR_COLOR:
        return FOUR_COLOR_SUIT_COLORS.copy()
    else:
        return TRADITIONAL_SUIT_COLORS.copy()


GLOBAL_STYLESHEET = """
/* Base application style */
QWidget {
    background-color: #f5f5f5;
    color: #000000;
}

QMainWindow {
    background-color: #ffffff;
}

/* Dialogs */
QDialog {
    background-color: #ffffff;
    color: #000000;
}

QLabel {
    color: #000000;
    background-color: transparent;
}

/* Buttons */
QPushButton {
    background-color: #e0e0e0;
    color: #000000;
    border: 1px solid #999999;
    padding: 5px 15px;
    border-radius: 3px;
}

QPushButton:hover {
    background-color: #d0d0d0;
}

QPushButton:pressed {
    background-color: #c0c0c0;
}

QPushButton:disabled {
    background-color: #f0f0f0;
    color: #888888;
}

/* Input fields */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
    padding: 3px;
}

QLineEdit:focus, QTextEdit:focus {
    border: 2px solid #4a90e2;
}

/* Spin boxes */
QSpinBox, QDoubleSpinBox {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
    padding: 3px;
}

/* Combo boxes */
QComboBox {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
    padding: 3px;
}

QComboBox::drop-down {
    border: none;
    background-color: #e0e0e0;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #000000;
    selection-background-color: #4a90e2;
    selection-color: #ffffff;
}

/* List widgets */
QListWidget, QListView {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
}

QListWidget::item, QListView::item {
    padding: 4px;
}

QListWidget::item:selected, QListView::item:selected {
    background-color: #4a90e2;
    color: #ffffff;
}

/* Tables */
QTableWidget, QTableView {
    background-color: #ffffff;
    color: #000000;
    gridline-color: #d0d0d0;
    border: 1px solid #cccccc;
}

QTableWidget::item, QTableView::item {
    padding: 4px;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: #4a90e2;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #e8e8e8;
    color: #000000;
    padding: 5px;
    border: 1px solid #cccccc;
    font-weight: bold;
}

/* Group boxes */
QGroupBox {
    background-color: transparent;
    color: #000000;
    border: 2px solid #cccccc;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: bold;
}

QGroupBox::title {
    color: #000000;
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    background-color: #ffffff;
}

/* Radio buttons and checkboxes */
QRadioButton, QCheckBox {
    color: #000000;
    background-color: transparent;
}

QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #999999;
    background-color: #ffffff;
}

QRadioButton::indicator:checked, QCheckBox::indicator:checked {
    background-color: #4a90e2;
}

/* Dock widgets */
QDockWidget {
    color: #000000;
    background-color: #f5f5f5;
}

QDockWidget::title {
    background-color: #4a90e2;
    color: #ffffff;
    padding: 5px;
    font-weight: bold;
}

/* Tab widgets */
QTabWidget::pane {
    border: 1px solid #cccccc;
    background-color: #ffffff;
}

QTabBar::tab {
    background-color: #e8e8e8;
    color: #000000;
    border: 1px solid #cccccc;
    padding: 8px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: #ffffff;
    border-bottom-color: #ffffff;
    font-weight: bold;
}

QTabBar::tab:hover {
    background-color: #f0f0f0;
}

/* Scroll bars */
QScrollBar:vertical {
    border: 1px solid #cccccc;
    background-color: #f5f5f5;
    width: 16px;
}

QScrollBar::handle:vertical {
    background-color: #a0a0a0;
    min-height: 20px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover {
    background-color: #808080;
}

QScrollBar:horizontal {
    border: 1px solid #cccccc;
    background-color: #f5f5f5;
    height: 16px;
}

QScrollBar::handle:horizontal {
    background-color: #a0a0a0;
    min-width: 20px;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #808080;
}

/* Scroll area */
QScrollArea {
    background-color: #ffffff;
    border: 1px solid #cccccc;
}

/* Message boxes */
QMessageBox {
    background-color: #f0f0f0;
    color: #000000;
}

QMessageBox QLabel {
    color: #000000;
    background-color: transparent;
    min-width: 250px;
    padding: 10px;
}

QMessageBox QPushButton {
    background-color: #e0e0e0;
    color: #000000;
    border: 1px solid #999999;
    padding: 6px 20px;
    border-radius: 3px;
    min-width: 80px;
}

QMessageBox QPushButton:hover {
    background-color: #d0d0d0;
}

QMessageBox QPushButton:pressed {
    background-color: #c0c0c0;
}

/* Input dialogs */
QInputDialog {
    background-color: #ffffff;
}

QInputDialog QLabel {
    color: #000000;
}

QInputDialog QLineEdit {
    background-color: #ffffff;
    color: #000000;
}

/* Menu bar */
QMenuBar {
    background-color: #f5f5f5;
    color: #000000;
}

QMenuBar::item:selected {
    background-color: #4a90e2;
    color: #ffffff;
}

QMenu {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
}

QMenu::item {
    padding: 6px 30px 6px 20px;
}

QMenu::item:selected {
    background-color: #4a90e2;
    color: #ffffff;
}

QMenu::item:disabled {
    color: #888888;
}

QMenu::separator {
    height: 1px;
    background-color: #cccccc;
    margin: 4px 10px;
}

/* Status bar */
QStatusBar {
    background-color: #f5f5f5;
    color: #000000;
}

/* Tool bar */
QToolBar {
    background-color: #f0f0f0;
    border: none;
    spacing: 3px;
}

QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    padding: 5px;
}

QToolButton:hover {
    background-color: #e0e0e0;
    border: 1px solid #cccccc;
}

QToolButton:pressed {
    background-color: #d0d0d0;
}

/* Progress bar */
QProgressBar {
    background-color: #e0e0e0;
    border: 1px solid #cccccc;
    border-radius: 3px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #4a90e2;
}

/* Slider */
QSlider::groove:horizontal {
    background-color: #e0e0e0;
    border: 1px solid #cccccc;
    height: 8px;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background-color: #4a90e2;
    border: 1px solid #3a80d2;
    width: 18px;
    margin: -5px 0;
    border-radius: 9px;
}

/* Tree widget */
QTreeWidget, QTreeView {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
}

QTreeWidget::item, QTreeView::item {
    padding: 4px;
}

QTreeWidget::item:selected, QTreeView::item:selected {
    background-color: #4a90e2;
    color: #ffffff;
}

/* Splitter */
QSplitter::handle {
    background-color: #cccccc;
}

QSplitter::handle:horizontal {
    width: 4px;
}

QSplitter::handle:vertical {
    height: 4px;
}

/* Frame */
QFrame {
    background-color: transparent;
}

/* Tool tip */
QToolTip {
    background-color: #ffffcc;
    color: #000000;
    border: 1px solid #999999;
    padding: 4px;
}
"""


def apply_global_style(app):
    """Apply global stylesheet to application"""
    app.setStyleSheet(GLOBAL_STYLESHEET)
