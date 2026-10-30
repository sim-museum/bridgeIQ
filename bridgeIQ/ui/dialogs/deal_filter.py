"""
Deal Filter Dialog
Configure filters for generating specific types of deals.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QSpinBox, QCheckBox, QPushButton, QComboBox,
    QTabWidget, QWidget, QMessageBox
)
from PyQt6.QtCore import Qt

from .dialog_style import apply_dialog_style


class DealFilterDialog(QDialog):
    """
    Dialog for configuring deal generation filters.
    Similar to BridgeIQ deal filter.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deal Filter")
        self.setMinimumWidth(500)
        apply_dialog_style(self)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Enable filter
        self.enable_filter = QCheckBox("Enable Deal Filter")
        layout.addWidget(self.enable_filter)

        # Tab widget for different filter types
        tabs = QTabWidget()

        # HCP Tab
        hcp_tab = QWidget()
        hcp_layout = QVBoxLayout(hcp_tab)

        hcp_group = QGroupBox("High Card Points")
        hcp_grid = QGridLayout(hcp_group)

        # Headers
        hcp_grid.addWidget(QLabel(""), 0, 0)
        hcp_grid.addWidget(QLabel("Min"), 0, 1)
        hcp_grid.addWidget(QLabel("Max"), 0, 2)

        self.hcp_ranges = {}
        for row, (name, default_min, default_max) in enumerate([
            ("North", 0, 37),
            ("East", 0, 37),
            ("South", 0, 37),
            ("West", 0, 37),
            ("N+S", 0, 40),
            ("E+W", 0, 40),
        ], 1):
            hcp_grid.addWidget(QLabel(name), row, 0)

            min_spin = QSpinBox()
            min_spin.setRange(0, 40)
            min_spin.setValue(default_min)
            hcp_grid.addWidget(min_spin, row, 1)

            max_spin = QSpinBox()
            max_spin.setRange(0, 40)
            max_spin.setValue(default_max)
            hcp_grid.addWidget(max_spin, row, 2)

            self.hcp_ranges[name] = (min_spin, max_spin)

        hcp_layout.addWidget(hcp_group)
        hcp_layout.addStretch()

        tabs.addTab(hcp_tab, "HCP")

        # Shape Tab
        shape_tab = QWidget()
        shape_layout = QVBoxLayout(shape_tab)

        # South shape
        south_group = QGroupBox("South Hand Shape")
        south_grid = QGridLayout(south_group)

        south_grid.addWidget(QLabel(""), 0, 0)
        for col, suit in enumerate(["Spades", "Hearts", "Diamonds", "Clubs"], 1):
            south_grid.addWidget(QLabel(suit), 0, col)

        self.south_lengths = {}
        for row, label in enumerate(["Min", "Max"], 1):
            south_grid.addWidget(QLabel(label), row, 0)
            for col, suit in enumerate(['S', 'H', 'D', 'C'], 1):
                spin = QSpinBox()
                spin.setRange(0, 13)
                spin.setValue(0 if label == "Min" else 13)
                south_grid.addWidget(spin, row, col)

                key = f"South_{suit}_{label}"
                self.south_lengths[key] = spin

        shape_layout.addWidget(south_group)

        # North shape
        north_group = QGroupBox("North Hand Shape")
        north_grid = QGridLayout(north_group)

        north_grid.addWidget(QLabel(""), 0, 0)
        for col, suit in enumerate(["Spades", "Hearts", "Diamonds", "Clubs"], 1):
            north_grid.addWidget(QLabel(suit), 0, col)

        self.north_lengths = {}
        for row, label in enumerate(["Min", "Max"], 1):
            north_grid.addWidget(QLabel(label), row, 0)
            for col, suit in enumerate(['S', 'H', 'D', 'C'], 1):
                spin = QSpinBox()
                spin.setRange(0, 13)
                spin.setValue(0 if label == "Min" else 13)
                north_grid.addWidget(spin, row, col)

                key = f"North_{suit}_{label}"
                self.north_lengths[key] = spin

        shape_layout.addWidget(north_group)
        shape_layout.addStretch()

        tabs.addTab(shape_tab, "Shape")

        # Features Tab
        features_tab = QWidget()
        features_layout = QVBoxLayout(features_tab)

        features_group = QGroupBox("Special Features")
        features_grid = QGridLayout(features_group)

        self.features = {}

        features_list = [
            ("Balanced South", "south_balanced"),
            ("South has 5+ card major", "south_5_major"),
            ("South has stopper in every suit", "south_stoppers"),
            ("Strong 2C opening for South", "south_2c_opener"),
            ("Slam zone (33+ combined)", "slam_zone"),
            ("Game zone (25+ combined)", "game_zone"),
        ]

        for row, (label, key) in enumerate(features_list):
            check = QCheckBox(label)
            features_grid.addWidget(check, row, 0)
            self.features[key] = check

        features_layout.addWidget(features_group)
        features_layout.addStretch()

        tabs.addTab(features_tab, "Features")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._on_clear)
        button_layout.addWidget(clear_btn)

        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._on_accept)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _on_accept(self):
        """Validate inputs before accepting."""
        if not self.enable_filter.isChecked():
            self.accept()
            return

        errors = []

        # Validate HCP ranges
        for name, (min_spin, max_spin) in self.hcp_ranges.items():
            if min_spin.value() > max_spin.value():
                errors.append(f"HCP {name}: minimum ({min_spin.value()}) > maximum ({max_spin.value()})")

        # Validate shape constraints
        for prefix, lengths_dict in [("South", self.south_lengths), ("North", self.north_lengths)]:
            for suit in ['S', 'H', 'D', 'C']:
                min_key = f"{prefix}_{suit}_Min"
                max_key = f"{prefix}_{suit}_Max"
                if min_key in lengths_dict and max_key in lengths_dict:
                    min_val = lengths_dict[min_key].value()
                    max_val = lengths_dict[max_key].value()
                    if min_val > max_val:
                        suit_name = {'S': 'Spades', 'H': 'Hearts', 'D': 'Diamonds', 'C': 'Clubs'}[suit]
                        errors.append(f"{prefix} {suit_name}: min ({min_val}) > max ({max_val})")

        if errors:
            QMessageBox.warning(
                self, "Invalid Filter",
                "Please fix the following errors:\n\n" + "\n".join(f"- {e}" for e in errors)
            )
            return

        self.accept()

    def _on_clear(self):
        """Reset all filters to default"""
        self.enable_filter.setChecked(False)

        for min_spin, max_spin in self.hcp_ranges.values():
            min_spin.setValue(0)
            max_spin.setValue(40)

        for key, spin in self.south_lengths.items():
            spin.setValue(0 if "Min" in key else 13)

        for key, spin in self.north_lengths.items():
            spin.setValue(0 if "Min" in key else 13)

        for check in self.features.values():
            check.setChecked(False)

    def get_filter(self) -> dict:
        """Return the configured filter"""
        if not self.enable_filter.isChecked():
            return None

        return {
            'hcp_ranges': {
                name: (min_spin.value(), max_spin.value())
                for name, (min_spin, max_spin) in self.hcp_ranges.items()
            },
            'south_lengths': {
                key: spin.value() for key, spin in self.south_lengths.items()
            },
            'north_lengths': {
                key: spin.value() for key, spin in self.north_lengths.items()
            },
            'features': {
                key: check.isChecked() for key, check in self.features.items()
            }
        }
