"""
Lead and Signalling Conventions Dialog - Configure defensive carding preferences.
Based on Q-plus Bridge lead and signalling conventions.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QRadioButton, QCheckBox, QComboBox, QPushButton,
    QButtonGroup, QGridLayout, QTabWidget, QWidget, QFrame
)
from PyQt6.QtCore import Qt

from backend.config import get_config_manager, LeadConventions, SignalConventions
from .dialog_style import apply_dialog_style


class LeadSignalDialog(QDialog):
    """Dialog for configuring lead and signalling conventions."""

    def __init__(self, parent=None, pair="NS"):
        """
        Args:
            parent: Parent widget
            pair: "NS" or "EW" to indicate which pair to configure
        """
        super().__init__(parent)
        self.pair = pair
        self.config_manager = get_config_manager()

        if pair == "NS":
            self.lead_conv = self.config_manager.config.lead_conventions_ns
            self.signal_conv = self.config_manager.config.signal_conventions_ns
        else:
            self.lead_conv = self.config_manager.config.lead_conventions_ew
            self.signal_conv = self.config_manager.config.signal_conventions_ew

        self.setWindowTitle(f"Lead & Signalling Conventions ({pair})")
        self.setMinimumWidth(500)
        apply_dialog_style(self)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()

        # Opening Leads tab
        lead_tab = self._create_lead_tab()
        tabs.addTab(lead_tab, "Opening Leads")

        # Signals tab
        signal_tab = self._create_signal_tab()
        tabs.addTab(signal_tab, "Signals")

        # Discards tab
        discard_tab = self._create_discard_tab()
        tabs.addTab(discard_tab, "Discards")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._on_ok)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        defaults_btn = QPushButton("Defaults")
        defaults_btn.clicked.connect(self._reset_defaults)
        button_layout.addWidget(defaults_btn)

        layout.addLayout(button_layout)

    def _create_lead_tab(self) -> QWidget:
        """Create opening leads configuration tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Suit contracts group
        suit_group = QGroupBox("Suit Contracts")
        suit_layout = QGridLayout()

        # Lead from honors
        suit_layout.addWidget(QLabel("Lead from AK:"), 0, 0)
        self.suit_ak_combo = QComboBox()
        self.suit_ak_combo.addItems(["Ace (A from AK)", "King (K from AK)"])
        suit_layout.addWidget(self.suit_ak_combo, 0, 1)

        suit_layout.addWidget(QLabel("Lead from KQ:"), 1, 0)
        self.suit_kq_combo = QComboBox()
        self.suit_kq_combo.addItems(["King", "Queen"])
        suit_layout.addWidget(self.suit_kq_combo, 1, 1)

        # Spot card leads
        suit_layout.addWidget(QLabel("From small cards:"), 2, 0)
        self.suit_small_combo = QComboBox()
        self.suit_small_combo.addItems([
            "4th best",
            "3rd/5th (3rd from odd, 5th from even)",
            "2nd/4th (2nd from bad, 4th from good)",
            "Top of nothing"
        ])
        suit_layout.addWidget(self.suit_small_combo, 2, 1)

        # MUD
        self.suit_mud_check = QCheckBox("MUD (Middle-Up-Down from 3 small)")
        suit_layout.addWidget(self.suit_mud_check, 3, 0, 1, 2)

        suit_group.setLayout(suit_layout)
        layout.addWidget(suit_group)

        # NT contracts group
        nt_group = QGroupBox("Notrump Contracts")
        nt_layout = QGridLayout()

        nt_layout.addWidget(QLabel("Lead from AKx:"), 0, 0)
        self.nt_ak_combo = QComboBox()
        self.nt_ak_combo.addItems(["Ace", "King"])
        nt_layout.addWidget(self.nt_ak_combo, 0, 1)

        nt_layout.addWidget(QLabel("From small cards:"), 1, 0)
        self.nt_small_combo = QComboBox()
        self.nt_small_combo.addItems([
            "4th best",
            "3rd/5th",
            "2nd from bad, 4th from good",
            "Attitude (low = encouraging)"
        ])
        nt_layout.addWidget(self.nt_small_combo, 1, 1)

        nt_group.setLayout(nt_layout)
        layout.addWidget(nt_group)

        layout.addStretch()
        return widget

    def _create_signal_tab(self) -> QWidget:
        """Create defensive signalling configuration tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Attitude signals
        att_group = QGroupBox("Attitude Signals")
        att_layout = QVBoxLayout()

        self.att_button_group = QButtonGroup(self)
        self.att_standard = QRadioButton("Standard (high = encouraging)")
        self.att_button_group.addButton(self.att_standard, 0)
        att_layout.addWidget(self.att_standard)

        self.att_upside = QRadioButton("Upside-down (low = encouraging)")
        self.att_button_group.addButton(self.att_upside, 1)
        att_layout.addWidget(self.att_upside)

        att_group.setLayout(att_layout)
        layout.addWidget(att_group)

        # Count signals
        count_group = QGroupBox("Count Signals")
        count_layout = QVBoxLayout()

        self.count_button_group = QButtonGroup(self)
        self.count_standard = QRadioButton("Standard (high-low = even)")
        self.count_button_group.addButton(self.count_standard, 0)
        count_layout.addWidget(self.count_standard)

        self.count_upside = QRadioButton("Upside-down (low-high = even)")
        self.count_button_group.addButton(self.count_upside, 1)
        count_layout.addWidget(self.count_upside)

        count_group.setLayout(count_layout)
        layout.addWidget(count_group)

        # Suit preference
        pref_group = QGroupBox("Suit Preference Signals")
        pref_layout = QVBoxLayout()

        self.pref_button_group = QButtonGroup(self)
        self.pref_standard = QRadioButton("Standard (high = higher suit)")
        self.pref_button_group.addButton(self.pref_standard, 0)
        pref_layout.addWidget(self.pref_standard)

        self.pref_reverse = QRadioButton("Reverse (low = higher suit)")
        self.pref_button_group.addButton(self.pref_reverse, 1)
        pref_layout.addWidget(self.pref_reverse)

        pref_group.setLayout(pref_layout)
        layout.addWidget(pref_group)

        layout.addStretch()
        return widget

    def _create_discard_tab(self) -> QWidget:
        """Create discard configuration tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Discard method
        discard_group = QGroupBox("First Discard")
        discard_layout = QVBoxLayout()

        self.discard_button_group = QButtonGroup(self)

        self.discard_attitude = QRadioButton("Attitude (high = wants suit)")
        self.discard_button_group.addButton(self.discard_attitude, 0)
        discard_layout.addWidget(self.discard_attitude)

        self.discard_lavinthal = QRadioButton("Lavinthal (suit preference)")
        self.discard_button_group.addButton(self.discard_lavinthal, 1)
        discard_layout.addWidget(self.discard_lavinthal)

        self.discard_odd_even = QRadioButton("Odd-Even (odd = wants, even = suit pref)")
        self.discard_button_group.addButton(self.discard_odd_even, 2)
        discard_layout.addWidget(self.discard_odd_even)

        self.discard_revolving = QRadioButton("Revolving (asks for touching suit)")
        self.discard_button_group.addButton(self.discard_revolving, 3)
        discard_layout.addWidget(self.discard_revolving)

        discard_group.setLayout(discard_layout)
        layout.addWidget(discard_group)

        # Second discard
        second_group = QGroupBox("Subsequent Discards")
        second_layout = QVBoxLayout()

        self.second_same = QCheckBox("Same as first discard")
        self.second_same.setChecked(True)
        second_layout.addWidget(self.second_same)

        second_group.setLayout(second_layout)
        layout.addWidget(second_group)

        layout.addStretch()
        return widget

    def _load_settings(self):
        """Load current settings into dialog."""
        # Parse the lead string to set UI
        # Format: -hiA-lo2-ln4-lwN-3cH-
        lead_str = self.lead_conv.suit_lead

        # Set defaults
        self.suit_ak_combo.setCurrentIndex(0)  # Ace from AK
        self.suit_kq_combo.setCurrentIndex(0)  # King from KQ
        self.suit_small_combo.setCurrentIndex(0)  # 4th best
        self.suit_mud_check.setChecked(False)
        self.nt_ak_combo.setCurrentIndex(0)
        self.nt_small_combo.setCurrentIndex(0)

        # Parse signal string
        sig_str = self.signal_conv.signal_string

        # Set defaults
        self.att_standard.setChecked(True)
        self.count_standard.setChecked(True)
        self.pref_standard.setChecked(True)
        self.discard_attitude.setChecked(True)

    def _save_settings(self):
        """Save dialog settings to configuration."""
        # Build lead convention string
        # This is a simplified version - would need proper encoding
        lead_str = "-hiA-lo2-ln4-lwN-3cH-"

        self.lead_conv.suit_lead = lead_str
        self.lead_conv.notrump_lead = lead_str

        # Build signal convention string
        sig_str = "-ldA-lwN-paA-pwN-dcC-maH-mcH-pcY-dsN-dnN-"
        self.signal_conv.signal_string = sig_str

        # Update config manager
        if self.pair == "NS":
            self.config_manager.config.lead_conventions_ns = self.lead_conv
            self.config_manager.config.signal_conventions_ns = self.signal_conv
        else:
            self.config_manager.config.lead_conventions_ew = self.lead_conv
            self.config_manager.config.signal_conventions_ew = self.signal_conv

    def _reset_defaults(self):
        """Reset to default conventions."""
        self.suit_ak_combo.setCurrentIndex(0)
        self.suit_kq_combo.setCurrentIndex(0)
        self.suit_small_combo.setCurrentIndex(0)
        self.suit_mud_check.setChecked(False)
        self.nt_ak_combo.setCurrentIndex(0)
        self.nt_small_combo.setCurrentIndex(0)
        self.att_standard.setChecked(True)
        self.count_standard.setChecked(True)
        self.pref_standard.setChecked(True)
        self.discard_attitude.setChecked(True)

    def _on_ok(self):
        """Handle OK button."""
        self._save_settings()
        self.accept()


class StrengthDialog(QDialog):
    """Dialog for configuring computer playing strength."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = get_config_manager()

        self.setWindowTitle("Playing Strength")
        self.setMinimumWidth(400)
        apply_dialog_style(self)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Info label
        info = QLabel(
            "Adjust the playing strength of the computer opponents.\n"
            "Higher values = stronger play (0-100)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # N/S strength
        ns_group = QGroupBox("North/South Strength")
        ns_layout = QHBoxLayout()

        self.ns_slider = self._create_strength_slider()
        ns_layout.addWidget(self.ns_slider)

        self.ns_label = QLabel("72")
        self.ns_label.setMinimumWidth(30)
        ns_layout.addWidget(self.ns_label)

        self.ns_slider.valueChanged.connect(
            lambda v: self.ns_label.setText(str(v))
        )

        ns_group.setLayout(ns_layout)
        layout.addWidget(ns_group)

        # E/W strength
        ew_group = QGroupBox("East/West Strength")
        ew_layout = QHBoxLayout()

        self.ew_slider = self._create_strength_slider()
        ew_layout.addWidget(self.ew_slider)

        self.ew_label = QLabel("72")
        self.ew_label.setMinimumWidth(30)
        ew_layout.addWidget(self.ew_label)

        self.ew_slider.valueChanged.connect(
            lambda v: self.ew_label.setText(str(v))
        )

        ew_group.setLayout(ew_layout)
        layout.addWidget(ew_group)

        # Presets
        preset_group = QGroupBox("Presets")
        preset_layout = QHBoxLayout()

        for name, value in [("Beginner", 30), ("Intermediate", 55),
                           ("Advanced", 72), ("Expert", 90)]:
            btn = QPushButton(name)
            btn.clicked.connect(lambda _, v=value: self._set_preset(v))
            preset_layout.addWidget(btn)

        preset_group.setLayout(preset_layout)
        layout.addWidget(preset_group)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._on_ok)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _create_strength_slider(self):
        """Create a strength slider."""
        from PyQt6.QtWidgets import QSlider
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(72)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setTickInterval(10)
        return slider

    def _load_settings(self):
        """Load current settings."""
        self.ns_slider.setValue(self.config_manager.config.strength_ns)
        self.ew_slider.setValue(self.config_manager.config.strength_ew)
        self.ns_label.setText(str(self.config_manager.config.strength_ns))
        self.ew_label.setText(str(self.config_manager.config.strength_ew))

    def _set_preset(self, value: int):
        """Set both sliders to a preset value."""
        self.ns_slider.setValue(value)
        self.ew_slider.setValue(value)

    def _on_ok(self):
        """Handle OK button."""
        self.config_manager.config.strength_ns = self.ns_slider.value()
        self.config_manager.config.strength_ew = self.ew_slider.value()
        self.config_manager.save_init_config()
        self.accept()


class ConfigCheckDialog(QDialog):
    """Dialog showing current configuration summary (Check! feature)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = get_config_manager()

        self.setWindowTitle("Configuration Check")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Text display
        from PyQt6.QtWidgets import QTextEdit
        from PyQt6.QtGui import QFont

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

        # Get configuration summary
        summary = self.config_manager.get_config_summary()
        self.text_view.setText(summary)

        layout.addWidget(self.text_view)

        # Buttons
        button_layout = QHBoxLayout()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        button_layout.addWidget(refresh_btn)

        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy)
        button_layout.addWidget(copy_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _refresh(self):
        """Refresh the configuration display."""
        summary = self.config_manager.get_config_summary()
        self.text_view.setText(summary)

    def _copy(self):
        """Copy configuration to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_view.toPlainText())
