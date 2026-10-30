#!/usr/bin/env python3
"""
bridgeIQ - A PyQt6 bridge application (native rule-based engine).
Classic desktop Bridge interface.

Usage:
    python main.py              # the window
    python main.py --textmode   # play in the terminal (text_mode.py)
    python main.py --check      # build integrity check only
"""

import sys
import os

# Suppress TensorFlow warnings before importing anything else
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

# Ensure we can find our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QSplashScreen, QMessageBox
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

# Set up logging
from logging_config import setup_logging, get_logger

def exception_hook(exc_type, exc_value, exc_tb):
    """Global exception handler to log uncaught exceptions."""
    import traceback
    logger = get_logger()
    logger.critical("Uncaught exception:", exc_info=(exc_type, exc_value, exc_tb))
    # Still show to user
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    QMessageBox.critical(None, "Unexpected Error",
                        f"An unexpected error occurred:\n\n{exc_value}\n\n"
                        "See the log file for details.")

def run_check_only() -> int:
    """`python3 main.py --check`: run the build integrity check and print it."""
    from backend import build_check
    problems, notes = build_check.run_all()
    for n in notes:
        print(f"note: {n}")
    for p in problems:
        print(f"PROBLEM: {p}")
    print("build OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def main():
    # Set up logging (file + console)
    from logging_config import get_default_log_path
    logger = setup_logging(log_file=get_default_log_path(), console=False)
    logger.info("Starting BridgeIQ application")

    # Install global exception handler
    sys.excepthook = exception_hook

    # Create application — reuse an existing instance if one was already
    # created earlier (e.g. by a screen-resolution probe), otherwise
    # constructing a second QApplication causes a hard crash.
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BridgeIQ")
    app.setOrganizationName("Bridge")
    app.setApplicationVersion("1.0.0")

    # Set application style
    app.setStyle("Fusion")

    # Single-instance guard. Launching biq again (e.g. clicking the launcher a
    # second time) should RAISE the running window, not start another copy that
    # piles up as a windowless dock entry. We probe a local socket; if an
    # instance answers, we tell it to come forward and exit immediately.
    _SINGLE_KEY = "bridgeIQ-single-instance"
    single_server = None
    try:
        from PyQt6.QtNetwork import QLocalServer, QLocalSocket
        single_server = QLocalServer()
        single_server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not single_server.listen(_SINGLE_KEY):
            # The name is taken. Distinguish a LIVE instance from a stale socket
            # left by a crashed/killed one: only a live instance accepts a
            # connection. (Connecting first, as a probe, is unreliable — a stale
            # socket file could linger; listening first lets us self-heal.)
            _probe = QLocalSocket()
            _probe.connectToServer(_SINGLE_KEY)
            if _probe.waitForConnected(300):
                _probe.write(b"activate"); _probe.flush()
                _probe.waitForBytesWritten(300); _probe.disconnectFromServer()
                logger.info("bridgeIQ already running — raising the existing window.")
                os._exit(0)
            # stale socket from a dead instance — clear it and take over
            _probe.abort()
            QLocalServer.removeServer(_SINGLE_KEY)
            single_server.listen(_SINGLE_KEY)
            logger.info("cleared a stale single-instance socket; starting normally.")
    except Exception as e:
        logger.warning(f"single-instance guard unavailable: {e}")

    # Set application-wide styling for proper contrast
    app.setStyleSheet("""
        QMenu {
            background-color: #f0f0f0;
            color: #000000;
            border: 1px solid #606060;
            padding: 4px;
        }
        QMenu::item {
            background-color: #f0f0f0;
            padding: 6px 30px 6px 20px;
            color: #000000;
        }
        QMenu::item:selected {
            background-color: #3070b0;
            color: #ffffff;
        }
        QMenu::item:disabled {
            color: #808080;
        }
        QMenu::separator {
            height: 1px;
            background-color: #a0a0a0;
            margin: 4px 10px;
        }
        QDialog {
            background-color: #f0f0f0;
            color: #000000;
        }
        QDialog QGroupBox {
            background-color: #f8f8f8;
            border: 1px solid #c0c0c0;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 8px;
            color: #000000;
        }
        QDialog QGroupBox::title {
            color: #000000;
        }
        QDialog QLabel {
            color: #000000;
        }
        QDialog QComboBox {
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #a0a0a0;
            padding: 4px;
        }
        QDialog QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #000000;
            selection-background-color: #3070b0;
            selection-color: #ffffff;
        }
        QDialog QListWidget {
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #a0a0a0;
        }
        QDialog QTextEdit {
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #a0a0a0;
        }
        QDialog QCheckBox {
            color: #000000;
            spacing: 8px;
        }
        /* A visible box: the default indicator is blank on these dialogs,
           so users couldn't see there was anything to click. */
        QDialog QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border: 2px solid #505050;
            border-radius: 3px;
            background-color: #ffffff;
        }
        QDialog QCheckBox::indicator:hover {
            border-color: #3070b0;
        }
        QDialog QCheckBox::indicator:checked {
            background-color: #3070b0;
            border-color: #3070b0;
            image: url(@CHECK@);
        }
        QDialog QCheckBox::indicator:disabled {
            border-color: #b0b0b0;
            background-color: #eeeeee;
        }
        QDialog QPushButton {
            background-color: #e0e0e0;
            color: #000000;
            border: 1px solid #a0a0a0;
            padding: 6px 12px;
            border-radius: 3px;
        }
        QDialog QPushButton:hover {
            background-color: #d0d0d0;
        }
        QDialog QPushButton:pressed {
            background-color: #c0c0c0;
        }
        QDialog QTabWidget::pane {
            background-color: #f0f0f0;
            border: 1px solid #c0c0c0;
        }
        QDialog QTabBar::tab {
            background-color: #e0e0e0;
            color: #000000;
            padding: 6px 12px;
            border: 1px solid #c0c0c0;
            border-bottom: none;
        }
        QDialog QTabBar::tab:selected {
            background-color: #f0f0f0;
        }
        QDialog QScrollArea {
            background-color: #f0f0f0;
            border: 1px solid #c0c0c0;
        }
        QComboBox QAbstractItemView {
            background-color: #f0f0f0;
            color: #000000;
            selection-background-color: #3070b0;
            selection-color: #ffffff;
        }
    """.replace("@CHECK@", os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "ui", "assets", "icons", "check.svg").replace(os.sep, "/")))

    # Show splash screen
    splash_pix = QPixmap(400, 250)
    splash_pix.fill(Qt.GlobalColor.white)

    splash = QSplashScreen(splash_pix)
    splash.setFont(QFont("Arial", 14))
    splash.showMessage(
        "bridgeIQ\n\nLoading…",
        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom,
        Qt.GlobalColor.darkBlue
    )
    splash.show()
    app.processEvents()

    # Build integrity check: a broken build must not play (missing or stale
    # files, a second copy of biq on the path, an engine on a fallback).
    splash.showMessage("bridgeIQ\n\nChecking the build…",
                       Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom,
                       Qt.GlobalColor.darkBlue)
    app.processEvents()
    try:
        from backend import build_check
        problems, notes = build_check.run_all()
    except Exception as e:
        problems, notes = [f"the build check itself failed: {e!r}"], []
    for n in notes:
        logger.info(f"build check: {n}")
    if problems:
        for p in problems:
            logger.critical(f"build check: {p}")
        splash.close()
        QMessageBox.critical(
            None, "bridgeIQ can't start: build problem",
            "bridgeIQ found a problem with this installation and won't play "
            "until it's fixed (it would bid and play wrongly):\n\n• "
            + "\n• ".join(problems)
            + "\n\nUpdate or reinstall bridgeIQ (git pull, or a fresh copy), "
            "then start it again. Details are in the log file.")
        os._exit(1)

    try:
        # Import main window (this triggers TensorFlow loading)
        logger.info("Loading bridgeIQ...")
        from ui.main_window import MainWindow

        # Create and show main window
        window = MainWindow()
        logger.info("Main window created successfully")

        # When a second launch pings the single-instance socket, bring this
        # window to the front (and onto the current workspace) instead.
        def _bring_to_front():
            try:
                window.showNormal()
                window.setWindowState(
                    (window.windowState() & ~Qt.WindowState.WindowMinimized)
                    | Qt.WindowState.WindowActive)
                window.raise_(); window.activateWindow()
            except Exception:
                pass
        if single_server is not None:
            def _on_second_launch():
                conn = single_server.nextPendingConnection()
                if conn is not None:
                    conn.disconnectFromServer()
                _bring_to_front()
            single_server.newConnection.connect(_on_second_launch)

        # Close splash and show window. Force the window to the FRONT — when
        # biq is launched from a full-screen terminal launcher, the window
        # manager's focus-stealing prevention otherwise opens it BEHIND the
        # terminal, so the user only sees a dock entry ("gear") and the main
        # window never appears. raise_/activateWindow + an active window state
        # bring it forward on the current workspace.
        def _active_screen():
            # Use the PRIMARY screen — that's the main monitor where the
            # launcher/terminal lives, i.e. the screen the user is looking at.
            # (Earlier we used the screen under the mouse cursor, but on a
            # multi-monitor setup the idle cursor can sit on the OTHER monitor,
            # so biq maximized on a screen the user wasn't watching → "gear".)
            return app.primaryScreen()

        def _show_on_active_screen():
            # The WM was placing the window OFF-SCREEN (cascade / multi-head),
            # so the user only saw a dock "gear". Pin it to the screen the user
            # is actually on (cursor's monitor) by giving it that screen's
            # geometry, then MAXIMIZE there — a maximized window fills the
            # monitor, so it can't be off-screen or hidden behind the terminal.
            try:
                scr_obj = _active_screen()
                scr = scr_obj.availableGeometry()
                logger.info(
                    f"placement: active screen '{scr_obj.name()}' avail={scr}; "
                    f"all screens={[(s.name(), s.geometry()) for s in app.screens()]}")
                window.setGeometry(scr)        # assign to that monitor
            except Exception as _e:
                logger.warning(f"placement geometry failed: {_e}")
            window.showMaximized()
            try:
                window.setWindowState(
                    (window.windowState() & ~Qt.WindowState.WindowMinimized)
                    | Qt.WindowState.WindowMaximized | Qt.WindowState.WindowActive)
            except Exception:
                pass
            window.raise_()
            window.activateWindow()
            try:
                logger.info(
                    f"window after show: geom={window.geometry()} "
                    f"frame={window.frameGeometry()} visible={window.isVisible()} "
                    f"state={int(window.windowState())}")
            except Exception:
                pass

        def show_main():
            splash.finish(window)
            _show_on_active_screen()
            # Re-assert shortly after, in case the WM relocated it during the
            # initial map.
            QTimer.singleShot(400, _show_on_active_screen)
            logger.info("Application ready")

        QTimer.singleShot(1000, show_main)

        # Run application
        exit_code = app.exec()
        logger.info(f"Application exiting with code {exit_code}")
        # Force immediate exit — TensorFlow cleanup hangs for minutes otherwise
        os._exit(exit_code)

    except Exception as e:
        logger.exception("Failed to start application")
        splash.close()
        QMessageBox.critical(None, "Startup Error",
                            f"Failed to start BridgeIQ:\n\n{e}\n\n"
                            "See the log file for details.")
        sys.exit(1)


def run_text_mode() -> int:
    """`python3 main.py --textmode`: play in the terminal (text_mode.py).
    The build check runs first, as for the GUI."""
    from logging_config import get_default_log_path
    setup_logging(log_file=get_default_log_path(), console=False)
    from backend import build_check
    problems, _notes = build_check.run_all()
    if problems:
        print("biq can't start: the build check found problems.")
        for p in problems:
            print(f"PROBLEM: {p}")
        return 1
    import text_mode
    return text_mode.main(sys.argv[1:])


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        sys.exit(run_check_only())
    if "--textmode" in sys.argv[1:]:
        sys.exit(run_text_mode())
    if (sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")
            and not os.environ.get("QT_QPA_PLATFORM")):
        print("No graphical display found: starting in text mode.")
        sys.exit(run_text_mode())
    main()
