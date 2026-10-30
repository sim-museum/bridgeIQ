"""
Preferences Dialog - Configure user preferences for BridgeIQ.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QRadioButton, QCheckBox, QComboBox, QPushButton, QSlider,
    QButtonGroup, QGridLayout, QTabWidget, QWidget
)
from PyQt6.QtCore import Qt, pyqtSignal

from backend.config import (
    ConfigManager, PreferencesConfig, SuitLayout,
    get_config_manager
)

from .dialog_style import apply_dialog_style


class PreferencesDialog(QDialog):
    """Dialog for configuring user preferences."""

    # Signal emitted when settings are applied (for Apply button)
    settings_applied = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = get_config_manager()
        self.prefs = self.config_manager.config.preferences

        self.setWindowTitle("Preferences")
        self.setMinimumWidth(450)
        apply_dialog_style(self)
        self._setup_ui()
        self._load_current_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget for organized preferences
        tabs = QTabWidget()

        # Mouse & Input tab
        mouse_tab = self._create_mouse_tab()
        tabs.addTab(mouse_tab, "Mouse && Input")

        # Display tab
        display_tab = self._create_display_tab()
        tabs.addTab(display_tab, "Display")

        # Bidding tab
        bidding_tab = self._create_bidding_tab()
        tabs.addTab(bidding_tab, "Bidding")

        # Logging tab
        logging_tab = self._create_logging_tab()
        tabs.addTab(logging_tab, "Logging")

        # AI & Network tab
        ai_tab = self._create_ai_tab()
        tabs.addTab(ai_tab, "AI && Network")

        # Carding tab (defensive-signalling defaults for the instrumented view)
        carding_tab = self._create_carding_tab()
        tabs.addTab(carding_tab, "Carding")

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

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply)
        button_layout.addWidget(apply_btn)

        defaults_btn = QPushButton("Defaults")
        defaults_btn.clicked.connect(self._reset_defaults)
        button_layout.addWidget(defaults_btn)

        layout.addLayout(button_layout)

    def _create_mouse_tab(self) -> QWidget:
        """Create mouse and input settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Click handling
        click_group = QGroupBox("Card Selection")
        click_layout = QVBoxLayout()

        self.click_button_group = QButtonGroup(self)

        self.single_click_radio = QRadioButton("Single click to select and play card")
        self.click_button_group.addButton(self.single_click_radio, 0)
        click_layout.addWidget(self.single_click_radio)

        self.double_click_radio = QRadioButton("Double click to play card (single click to select)")
        self.click_button_group.addButton(self.double_click_radio, 1)
        click_layout.addWidget(self.double_click_radio)

        click_group.setLayout(click_layout)
        layout.addWidget(click_group)

        # Animation speed
        anim_group = QGroupBox("Animation")
        anim_layout = QGridLayout()

        anim_layout.addWidget(QLabel("Card movement speed:"), 0, 0)
        self.anim_slider = QSlider(Qt.Orientation.Horizontal)
        self.anim_slider.setRange(0, 100)
        self.anim_slider.setValue(50)
        anim_layout.addWidget(self.anim_slider, 0, 1)

        self.anim_label = QLabel("Medium")
        anim_layout.addWidget(self.anim_label, 0, 2)
        self.anim_slider.valueChanged.connect(self._update_anim_label)

        anim_group.setLayout(anim_layout)
        layout.addWidget(anim_group)

        # Computer play options
        play_group = QGroupBox("Computer Card Play")
        play_layout = QVBoxLayout()

        self.play_mode_group = QButtonGroup(self)
        self.play_mc_radio = QRadioButton("Monte Carlo simulation (recommended)")
        self.play_mc_radio.setToolTip(
            "Generates random deals consistent with known information,\n"
            "solves each double-dummy, and picks the best card on average.\n"
            "Strong and practical — the same technique used by Bridge Baron."
        )
        self.play_engine_radio = QRadioButton("BridgeIQ neural-net engine")
        self.play_engine_radio.setToolTip(
            "Uses BridgeIQ's neural-net for card play decisions.\n"
            "Fast but often makes poor plays."
        )
        self.play_dd_radio = QRadioButton("Double-dummy optimal play")
        self.play_dd_radio.setToolTip(
            "Uses the double-dummy solver assuming all cards are visible.\n"
            "Unrealistically strong — sees through the backs of cards."
        )
        self.play_nopeek_radio = QRadioButton(
            "No-peek (alpha-mu) — strongest, signals with partner")
        self.play_nopeek_radio.setToolTip(
            "Never looks at hidden cards. Plays a single-dummy alpha-mu search,\n"
            "emits standard defensive signals AND reads partner's signals.\n"
            "The strongest honest engine — recommended."
        )
        self.play_mode_group.addButton(self.play_mc_radio, 0)
        self.play_mode_group.addButton(self.play_engine_radio, 1)
        self.play_mode_group.addButton(self.play_dd_radio, 2)
        self.play_mode_group.addButton(self.play_nopeek_radio, 3)
        play_layout.addWidget(self.play_nopeek_radio)
        play_layout.addWidget(self.play_mc_radio)
        play_layout.addWidget(self.play_engine_radio)
        play_layout.addWidget(self.play_dd_radio)

        play_group.setLayout(play_layout)
        layout.addWidget(play_group)

        sim_group = QGroupBox("Simulation (stronger, slower)")
        sim_layout = QVBoxLayout()
        self.sim_bidding_check = QCheckBox(
            "Check bids by simulation at judgment points")
        self.sim_bidding_check.setToolTip(
            "When competing, doubling, inviting or choosing a game, biq deals\n"
            "out many hands consistent with the auction, finishes the auction\n"
            "for each candidate call and keeps the call that scores best.\n"
            "Adds a few seconds to those calls.")
        self.sim_leads_check = QCheckBox("Choose opening leads by simulation")
        self.sim_leads_check.setToolTip(
            "biq tries every opening lead against layouts consistent with the\n"
            "auction and leads the one that does best. Adds a few seconds.")
        sim_layout.addWidget(self.sim_bidding_check)
        sim_layout.addWidget(self.sim_leads_check)
        sim_group.setLayout(sim_layout)
        layout.addWidget(sim_group)


        layout.addStretch()
        return widget

    def _create_display_tab(self) -> QWidget:
        """Create display settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Hand display
        hand_group = QGroupBox("Hand Display")
        hand_layout = QVBoxLayout()

        # Suit layout
        suit_layout = QHBoxLayout()
        suit_layout.addWidget(QLabel("Suit order:"))
        self.suit_layout_combo = QComboBox()
        self.suit_layout_combo.addItems([
            "Spades - Hearts - Diamonds - Clubs (SHDC)",
            "Spades - Hearts - Clubs - Diamonds (SHCD)"
        ])
        suit_layout.addWidget(self.suit_layout_combo)
        hand_layout.addLayout(suit_layout)

        # N/S swap
        self.swap_ns_check = QCheckBox("Swap North/South when North is declarer")
        hand_layout.addWidget(self.swap_ns_check)

        # Card style
        style_layout = QHBoxLayout()
        style_layout.addWidget(QLabel("Card style:"))
        self.card_style_combo = QComboBox()
        self.card_style_combo.addItems(["Graphical cards", "Symbol cards", "Text cards"])
        style_layout.addWidget(self.card_style_combo)
        hand_layout.addLayout(style_layout)

        hand_group.setLayout(hand_layout)
        layout.addWidget(hand_group)

        # Table display
        table_group = QGroupBox("Table Display")
        table_layout = QVBoxLayout()

        self.show_played_cards_check = QCheckBox("Show played cards on table")
        self.show_played_cards_check.setChecked(True)
        table_layout.addWidget(self.show_played_cards_check)

        self.highlight_playable_check = QCheckBox("Highlight playable cards")
        self.highlight_playable_check.setChecked(True)
        table_layout.addWidget(self.highlight_playable_check)

        self.legacy_colors_check = QCheckBox("Legacy colors (red and black only)")
        self.legacy_colors_check.setToolTip(
            "Use traditional two-color suits:\n"
            "Spades/Clubs = Black, Hearts/Diamonds = Red"
        )
        table_layout.addWidget(self.legacy_colors_check)

        self.show_ben_analysis_check = QCheckBox("Show bid analysis panel")
        self.show_ben_analysis_check.setToolTip(
            "Show the BridgeIQ bid analysis panel during bidding.\n"
            "Displays BridgeIQ's recommended bid and candidate scores."
        )
        table_layout.addWidget(self.show_ben_analysis_check)

        self.show_bid_info_panel_check = QCheckBox(
            "Show bid-information panel on the bidding screen")
        self.show_bid_info_panel_check.setToolTip(
            "Dock the 'Information about the bids' panel at the upper-left of\n"
            "the bidding screen (embedded, not a separate window).")
        table_layout.addWidget(self.show_bid_info_panel_check)

        self.explain_actions_check = QCheckBox(
            "Explain biq's actions (click a bid or card for a why-popup)")
        self.explain_actions_check.setToolTip(
            "When on, clicking a bid in the auction or a card biq has played\n"
            "pops up a window explaining the call/play: how a bid fits the\n"
            "chosen bidding system and why it was chosen, or which card-play\n"
            "technique applies. A teaching aid; off by default.")
        table_layout.addWidget(self.explain_actions_check)

        table_group.setLayout(table_layout)
        layout.addWidget(table_group)

        layout.addStretch()
        return widget

    def _create_bidding_tab(self) -> QWidget:
        """Create bidding display settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Bidding display
        bid_group = QGroupBox("Bidding Display")
        bid_layout = QVBoxLayout()

        # Start position
        start_layout = QHBoxLayout()
        start_layout.addWidget(QLabel("Bidding box starts with:"))
        self.bid_start_combo = QComboBox()
        self.bid_start_combo.addItems(["West (standard)", "South (your hand first)"])
        start_layout.addWidget(self.bid_start_combo)
        bid_layout.addLayout(start_layout)

        # Alert marks
        self.show_alerts_check = QCheckBox("Show alert marks on artificial/alertable bids")
        self.show_alerts_check.setChecked(True)
        bid_layout.addWidget(self.show_alerts_check)

        # Bid explanation
        self.auto_explain_check = QCheckBox("Auto-explain bids on hover")
        self.auto_explain_check.setChecked(True)
        bid_layout.addWidget(self.auto_explain_check)

        # Blunder check — pops a Hint / Cancel dialog if the user
        # plays a card that the DDS+MC engine says loses ≥ 1 trick
        # vs the best card. Bidding-side blunder check is queued
        # for a later build.
        self.blunder_check_check = QCheckBox(
            "Blunder check during cardplay "
            "(warn if a play loses ≥ 1 trick)"
        )
        self.blunder_check_check.setChecked(True)
        self.blunder_check_check.setToolTip(
            "Runs Monte-Carlo + double-dummy scoring on every "
            "human card play. Adds 2–8 s to your move when it "
            "fires. Off to play silently."
        )
        bid_layout.addWidget(self.blunder_check_check)

        bid_group.setLayout(bid_layout)
        layout.addWidget(bid_group)

        # Bidding engine
        engine_group = QGroupBox("Bidding Engine")
        engine_layout = QVBoxLayout()
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Bots use:"))
        self.bidding_engine_combo = QComboBox()
        self.bidding_engine_combo.addItem("Native (Q-Plus-style rule engine, default)", "native")
        self.bidding_engine_combo.addItem("BridgeIQ (neural net)", "BEN")
        self.bidding_engine_combo.setToolTip(
            "Native is a from-scratch rule-based bidder modeled on Q-Plus 17,\n"
            "covering SAYC, 2/1, Acol, French, and three Precision variants\n"
            "with their full Q-Plus convention sets (default).\n"
            "BridgeIQ's neural-net bidder.\n"
            "Card play always uses the BridgeIQ engine regardless of this setting."
        )
        engine_row.addWidget(self.bidding_engine_combo)
        engine_row.addStretch()
        engine_layout.addLayout(engine_row)

        sys_row = QHBoxLayout()
        sys_row.addWidget(QLabel("System:"))
        self.native_system_combo = QComboBox()
        # Populated dynamically from bidding_systems / the BEN catalog
        # whenever the engine selection changes, so the user sees the
        # right list for whichever engine they picked.
        self.native_system_combo.setMinimumWidth(280)
        sys_row.addWidget(self.native_system_combo)
        sys_row.addStretch()
        engine_layout.addLayout(sys_row)
        # Repopulate the system combo whenever the engine selection
        # changes. Each engine ships a different catalog.
        self.bidding_engine_combo.currentIndexChanged.connect(
            self._on_engine_changed)
        self._populate_native_systems("native")

        engine_group.setLayout(engine_layout)
        layout.addWidget(engine_group)

        # Convention display
        conv_group = QGroupBox("Convention Display")
        conv_layout = QVBoxLayout()

        self.show_hcp_range_check = QCheckBox("Show HCP range for bids")
        self.show_hcp_range_check.setChecked(True)
        conv_layout.addWidget(self.show_hcp_range_check)

        self.show_suit_length_check = QCheckBox("Show suit length requirements")
        self.show_suit_length_check.setChecked(True)
        conv_layout.addWidget(self.show_suit_length_check)

        conv_group.setLayout(conv_layout)
        layout.addWidget(conv_group)

        layout.addStretch()
        return widget

    def _create_logging_tab(self) -> QWidget:
        """Create logging settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Log settings
        log_group = QGroupBox("Game Logging")
        log_layout = QVBoxLayout()

        self.log_enabled_check = QCheckBox("Enable automatic logging of completed hands")
        log_layout.addWidget(self.log_enabled_check)

        # Log format
        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("Log format:"))
        self.log_format_combo = QComboBox()
        self.log_format_combo.addItems(["BDL (BridgeIQ format)", "PBN (Portable Bridge Notation)"])
        format_layout.addWidget(self.log_format_combo)
        log_layout.addLayout(format_layout)

        # Log location
        loc_layout = QHBoxLayout()
        loc_layout.addWidget(QLabel("Log directory:"))
        self.log_dir_label = QLabel("DATA/LOG/")
        loc_layout.addWidget(self.log_dir_label)
        loc_layout.addStretch()
        log_layout.addLayout(loc_layout)

        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        # Pavlicek settings
        pav_group = QGroupBox("Deal Identification")
        pav_layout = QVBoxLayout()

        self.use_pavlicek_check = QCheckBox("Use Pavlicek deal numbers in logs")
        self.use_pavlicek_check.setChecked(True)
        pav_layout.addWidget(self.use_pavlicek_check)

        pav_group.setLayout(pav_layout)
        layout.addWidget(pav_group)

        layout.addStretch()
        return widget

    def _create_ai_tab(self) -> QWidget:
        """Create the AI & Network settings tab (Claude Code + Q-Plus)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        ai_group = QGroupBox("Claude Code (AI analysis)")
        ai_layout = QVBoxLayout()
        self.claude_enabled_check = QCheckBox("Enable Claude Code integration")
        self.claude_enabled_check.setToolTip(
            "Allow post-hand AI analysis, annotated transcripts and AI hints\n"
            "via the `claude` command-line tool. OFF by default — it shells\n"
            "out to Claude, costs tokens, and isn't needed for ordinary play.")
        ai_layout.addWidget(self.claude_enabled_check)
        note = QLabel("When off, the 'Claude analysis' buttons are hidden and "
                      "no AI calls are made.")
        note.setWordWrap(True)
        ai_layout.addWidget(note)
        ai_group.setLayout(ai_layout)
        layout.addWidget(ai_group)

        qp_group = QGroupBox("Q-Plus (closed-room / network play)")
        qp_layout = QVBoxLayout()
        qp_row = QHBoxLayout()
        qp_row.addWidget(QLabel("Q-Plus available:"))
        self.qplus_combo = QComboBox()
        # value order matches ("none","demo","full")
        self.qplus_combo.addItems(["None (no Q-Plus installed)",
                                   "Demo build", "Full (licensed)"])
        qp_row.addWidget(self.qplus_combo)
        qp_row.addStretch()
        qp_layout.addLayout(qp_row)
        qnote = QLabel("Closed-room and Q-NET features (biq E/W vs Q-Plus N/S) "
                       "are hidden unless a Q-Plus build is available.")
        qnote.setWordWrap(True)
        qp_layout.addWidget(qnote)
        qp_group.setLayout(qp_layout)
        layout.addWidget(qp_group)

        layout.addStretch()
        return widget

    # Carding presets surfaced in the dialog. "" = what biq's defenders play
    # (teaching_view.AS_BIQ_PLAYS: the signalling convention above, Lavinthal
    # discards, plain count in trumps); the rest match
    # teaching_view.CARDING_PRESETS.
    _CARDING_CHOICES = [
        ("As biq plays (convention above, Lavinthal discards)", ""),
        ("Standard", "Standard"),
        ("Upside-down (UDCA)", "Upside-down"),
        ("Std + Lavinthal", "Std + Lavinthal"),
        ("UDCA + Lavinthal", "UDCA + Lavinthal"),
    ]

    def _create_carding_tab(self) -> QWidget:
        """Defensive-signalling agreements per pair + Smith echo — the defaults
        the instrumented (teaching) view decodes signals with."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Defensive signalling convention (biq plays this AND expects it from
        # partner; the partner-signal reader stays in sync).
        sig_group = QGroupBox("Defensive Signalling")
        sig_outer = QVBoxLayout()
        self.signalling_enabled_check = QCheckBox(
            "Play defensive signals (attitude / count / suit-preference)")
        self.signalling_enabled_check.setToolTip(
            "Off: biq never spends a card on a signal — it plays the plain\n"
            "lowest card when it can't win and chooses every other card on\n"
            "trick value alone. Partner-signal reading is off too.")
        sig_outer.addWidget(self.signalling_enabled_check)
        sig_layout = QHBoxLayout()
        sig_layout.addWidget(QLabel("Convention:"))
        self.signalling_combo = QComboBox()
        self.signalling_combo.addItem("Standard (high = encourage, hi-lo = even)",
                                      "standard")
        self.signalling_combo.addItem("Upside-down (UDCA: low = encourage)",
                                      "udca")
        self.signalling_combo.setToolTip(
            "Attitude/count signals biq gives and reads from partner.\n"
            "Standard or Upside-Down Count & Attitude (UDCA).")
        sig_layout.addWidget(self.signalling_combo, 1)
        sig_outer.addLayout(sig_layout)
        self.signalling_enabled_check.toggled.connect(
            self.signalling_combo.setEnabled)
        sig_group.setLayout(sig_outer)
        layout.addWidget(sig_group)

        grp = QGroupBox("Defensive carding agreements")
        gl = QVBoxLayout()

        ns_row = QHBoxLayout()
        ns_row.addWidget(QLabel("N/S carding:"))
        self.carding_ns_combo = QComboBox()
        self.carding_ns_combo.addItems([lbl for lbl, _ in self._CARDING_CHOICES])
        ns_row.addWidget(self.carding_ns_combo)
        ns_row.addStretch()
        gl.addLayout(ns_row)

        ew_row = QHBoxLayout()
        ew_row.addWidget(QLabel("E/W carding:"))
        self.carding_ew_combo = QComboBox()
        self.carding_ew_combo.addItems([lbl for lbl, _ in self._CARDING_CHOICES])
        ew_row.addWidget(self.carding_ew_combo)
        ew_row.addStretch()
        gl.addLayout(ew_row)

        sm_row = QHBoxLayout()
        sm_row.addWidget(QLabel("Smith echo (NT):"))
        self.smith_echo_combo = QComboBox()
        self.smith_echo_combo.addItems(["Off", "Standard", "Reverse"])
        sm_row.addWidget(self.smith_echo_combo)
        sm_row.addStretch()
        gl.addLayout(sm_row)

        note = QLabel(
            "These set how the analysis view reads attitude / count / "
            "suit-preference / Smith signals for each pair, and so where it "
            "places the defenders' unseen cards; Claude's hints and analyses "
            "are told the same agreements. You can also change them live from "
            "the analysis view's header; both share this setting.")
        note.setWordWrap(True)
        gl.addWidget(note)
        grp.setLayout(gl)
        layout.addWidget(grp)
        layout.addStretch()
        return widget

    def _load_current_settings(self):
        """Load current configuration into dialog."""
        # Mouse settings
        if self.prefs.single_click:
            self.single_click_radio.setChecked(True)
        else:
            self.double_click_radio.setChecked(True)

        self.anim_slider.setValue(int(self.prefs.moved_cards_speed * 100))

        # Play mode setting (no-peek takes precedence — it's the strongest)
        if getattr(self.prefs, 'use_nopeek_play', False):
            self.play_nopeek_radio.setChecked(True)
        elif self.prefs.use_monte_carlo_play:
            self.play_mc_radio.setChecked(True)
        elif self.prefs.use_double_dummy_play:
            self.play_dd_radio.setChecked(True)
        else:
            self.play_engine_radio.setChecked(True)

        self.sim_bidding_check.setChecked(
            bool(getattr(self.prefs, 'sim_bidding_enabled', True)))
        self.sim_leads_check.setChecked(
            bool(getattr(self.prefs, 'sim_leads_enabled', True)))

        # Defensive signalling switch + convention
        enabled = bool(getattr(self.prefs, 'signalling_enabled', True))
        self.signalling_enabled_check.setChecked(enabled)
        self.signalling_combo.setEnabled(enabled)
        idx = self.signalling_combo.findData(
            getattr(self.prefs, 'signalling_convention', 'standard'))
        self.signalling_combo.setCurrentIndex(max(0, idx))

        # Display settings
        if self.prefs.suit_layout == SuitLayout.SHDC:
            self.suit_layout_combo.setCurrentIndex(0)
        else:
            self.suit_layout_combo.setCurrentIndex(1)

        self.swap_ns_check.setChecked(self.prefs.swap_ns_declarer)
        self.legacy_colors_check.setChecked(self.prefs.legacy_colors)
        self.show_ben_analysis_check.setChecked(self.prefs.show_ben_bid_analysis)
        self.show_bid_info_panel_check.setChecked(
            getattr(self.prefs, "show_bid_info_panel", True))
        self.explain_actions_check.setChecked(
            getattr(self.prefs, "explain_actions_enabled", False))
        self.claude_enabled_check.setChecked(
            getattr(self.prefs, "claude_code_enabled", False))
        self.qplus_combo.setCurrentIndex(
            {"none": 0, "demo": 1, "full": 2}.get(
                getattr(self.prefs, "qplus_availability", "none"), 0))
        # Carding tab
        vals = [v for _, v in self._CARDING_CHOICES]
        def _carding_idx(v):
            return vals.index(v) if v in vals else 0
        self.carding_ns_combo.setCurrentIndex(
            _carding_idx(getattr(self.prefs, "carding_ns", "")))
        self.carding_ew_combo.setCurrentIndex(
            _carding_idx(getattr(self.prefs, "carding_ew", "")))
        sm = getattr(self.prefs, "smith_echo", "Off")
        self.smith_echo_combo.setCurrentText(
            sm if sm in ("Off", "Standard", "Reverse") else "Off")

        # Bidding settings
        self.show_alerts_check.setChecked(self.prefs.show_alert_marks)
        self.blunder_check_check.setChecked(
            getattr(self.prefs, 'blunder_check_enabled', True))

        # Bidding engine — set engine first, then refresh the system
        # combo for that engine, then select the saved system.
        engine = getattr(self.prefs, 'bidding_engine', 'native') or 'native'
        idx = self.bidding_engine_combo.findData(engine)
        if idx >= 0:
            self.bidding_engine_combo.setCurrentIndex(idx)
        self._populate_native_systems(engine)
        idx = self.native_system_combo.findData(
            getattr(self.prefs, 'native_bidding_system', 'Precision90M'))
        if idx >= 0:
            self.native_system_combo.setCurrentIndex(idx)

        # Logging settings
        self.log_enabled_check.setChecked(self.prefs.log_enabled)
        if self.prefs.log_as_pbn:
            self.log_format_combo.setCurrentIndex(1)
        else:
            self.log_format_combo.setCurrentIndex(0)

    def _on_engine_changed(self, _idx: int):
        """Refresh the system combo when the user switches engines."""
        engine = self.bidding_engine_combo.currentData() or "native"
        # Preserve the previously-chosen system if it exists in the new
        # engine's catalog, otherwise fall back to that engine's first
        # entry (SAYC in both catalogs).
        prev = self.native_system_combo.currentData()
        self._populate_native_systems(engine)
        if prev:
            idx = self.native_system_combo.findData(prev)
            if idx >= 0:
                self.native_system_combo.setCurrentIndex(idx)

    def _populate_native_systems(self, engine: str):
        """Fill the system combo with the catalog for the active engine.

        Native engine → the seven Q-Plus systems from
        ``backend.bidding_systems`` with their descriptions.
        BEN engine → the legacy three-system list BEN was trained on.
        """
        self.native_system_combo.blockSignals(True)
        self.native_system_combo.clear()
        if (engine or "native") == "native":
            try:
                from backend.bidding_systems import (
                    list_systems, get_system)
                for name in list_systems():
                    sys_ = get_system(name)
                    label = f"{name} — {sys_.description}" \
                        if sys_.description else name
                    self.native_system_combo.addItem(label, name)
            except Exception:
                # If the catalog fails to load, fall back to SAYC.
                self.native_system_combo.addItem("SAYC", "SAYC")
        else:
            # BEN's training data is the legacy three-system catalog.
            for code, label in [
                ("SAYC", "SAYC (Standard American Yellow Card)"),
                ("2/1",  "2/1 Game Force"),
                ("GIB",  "GIB (BBO Default)"),
            ]:
                self.native_system_combo.addItem(label, code)
        self.native_system_combo.blockSignals(False)

    def _save_settings(self):
        """Save dialog settings to configuration."""
        # Mouse settings
        self.prefs.single_click = self.single_click_radio.isChecked()
        self.prefs.moved_cards_speed = self.anim_slider.value() / 100.0

        # Play mode setting
        self.prefs.use_nopeek_play = self.play_nopeek_radio.isChecked()
        self.prefs.use_monte_carlo_play = self.play_mc_radio.isChecked()
        self.prefs.use_double_dummy_play = self.play_dd_radio.isChecked()

        self.prefs.sim_bidding_enabled = self.sim_bidding_check.isChecked()
        self.prefs.sim_leads_enabled = self.sim_leads_check.isChecked()
        try:
            from backend import bid_sim as _bs, lead_sim as _ls
            _bs.set_enabled(self.prefs.sim_bidding_enabled)
            _ls.set_enabled(self.prefs.sim_leads_enabled)
        except Exception:
            pass

        # Defensive signalling convention — apply to the live engine now so the
        # emitter and the partner-signal reader both switch together.
        self.prefs.signalling_enabled = self.signalling_enabled_check.isChecked()
        self.prefs.signalling_convention = \
            self.signalling_combo.currentData() or "standard"
        try:
            from backend import signals as _sig
            _sig.set_enabled(self.prefs.signalling_enabled)
            _sig.set_convention(self.prefs.signalling_convention == "udca")
        except Exception:
            pass

        # Display settings
        if self.suit_layout_combo.currentIndex() == 0:
            self.prefs.suit_layout = SuitLayout.SHDC
        else:
            self.prefs.suit_layout = SuitLayout.SHCD

        self.prefs.swap_ns_declarer = self.swap_ns_check.isChecked()
        self.prefs.legacy_colors = self.legacy_colors_check.isChecked()
        self.prefs.show_ben_bid_analysis = self.show_ben_analysis_check.isChecked()
        self.prefs.show_bid_info_panel = self.show_bid_info_panel_check.isChecked()
        self.prefs.explain_actions_enabled = self.explain_actions_check.isChecked()
        self.prefs.claude_code_enabled = self.claude_enabled_check.isChecked()
        self.prefs.qplus_availability = ("none", "demo", "full")[
            self.qplus_combo.currentIndex()]
        vals = [v for _, v in self._CARDING_CHOICES]
        self.prefs.carding_ns = vals[self.carding_ns_combo.currentIndex()]
        self.prefs.carding_ew = vals[self.carding_ew_combo.currentIndex()]
        self.prefs.smith_echo = self.smith_echo_combo.currentText()

        # Bidding engine
        self.prefs.bidding_engine = self.bidding_engine_combo.currentData() or "native"
        self.prefs.native_bidding_system = self.native_system_combo.currentData() or "SAYC"

        # Apply suit color mode immediately
        from ..styles import set_suit_color_mode, SuitColorMode
        if self.prefs.legacy_colors:
            set_suit_color_mode(SuitColorMode.TRADITIONAL)
        else:
            set_suit_color_mode(SuitColorMode.FOUR_COLOR)

        # Bidding settings
        self.prefs.show_alert_marks = self.show_alerts_check.isChecked()
        self.prefs.blunder_check_enabled = self.blunder_check_check.isChecked()

        # Logging settings
        self.prefs.log_enabled = self.log_enabled_check.isChecked()
        self.prefs.log_as_pbn = (self.log_format_combo.currentIndex() == 1)

        # Save to file
        self.config_manager.save_preferences()

    def _update_anim_label(self, value: int):
        """Update animation speed label."""
        if value < 25:
            self.anim_label.setText("Fast")
        elif value < 50:
            self.anim_label.setText("Medium-Fast")
        elif value < 75:
            self.anim_label.setText("Medium")
        else:
            self.anim_label.setText("Slow")

    def _reset_defaults(self):
        """Reset to default settings."""
        self.single_click_radio.setChecked(True)
        self.anim_slider.setValue(50)
        self.suit_layout_combo.setCurrentIndex(0)
        self.swap_ns_check.setChecked(True)
        self.show_alerts_check.setChecked(True)
        self.log_enabled_check.setChecked(True)
        self.log_format_combo.setCurrentIndex(0)

    def _on_ok(self):
        """Handle OK button."""
        self._save_settings()
        self.accept()

    def _on_apply(self):
        """Handle Apply button."""
        self._save_settings()
        self.settings_applied.emit()
