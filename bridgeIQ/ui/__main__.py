"""
Entry point for running the UI directly: python -m ui
"""

import sys
from PyQt6.QtWidgets import QApplication
from .main_window import MainWindow
from .styles import apply_global_style


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BridgeIQ")
    app.setStyle("Fusion")

    # Apply global stylesheet for consistent light backgrounds
    apply_global_style(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
