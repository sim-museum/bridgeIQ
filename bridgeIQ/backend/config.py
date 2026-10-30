"""
Configuration Manager for BridgeIQ.

Handles loading/saving configuration files.
Configuration files use a simple key=value format with sections.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum, IntEnum
import re
from datetime import datetime


class PlayerType(IntEnum):
    HUMAN = 0
    COMPUTER = 1
    REMOTE = 2
    ADVANCED = 3
    EXTENDED = 4


class ScoringMethod(Enum):
    RUBBER = "Rubber"
    CHICAGO = "Chicago"
    PAIRS = "Pairs"
    TEAMS = "Team (IMP)"


class ComparisonMode(Enum):
    NONE = "None"
    PAIR_TOURNAMENT = "Pair"
    CLOSED_ROOM = "Closed"


class CheatingMode(Enum):
    NEVER = "Never"
    ALWAYS = "Always"
    CONDITIONAL = "Conditional"


class SuitLayout(IntEnum):
    SHDC = 0  # Spades, Hearts, Diamonds, Clubs
    SHCD = 1  # Spades, Hearts, Clubs, Diamonds


@dataclass
class PlayerConfig:
    """Configuration for a single player position."""
    player_type: PlayerType = PlayerType.COMPUTER
    visible: bool = False
    bidding_system: str = "BEN-NN"
    name: str = ""


@dataclass
class MatchConfig:
    """Match control configuration."""
    deal_source: str = "Random"
    deal_number: float = 1.0
    scoring_method: ScoringMethod = ScoringMethod.TEAMS
    comparison: ComparisonMode = ComparisonMode.NONE
    deal_file_pair: str = ""
    deal_file_team: str = ""
    deal_file_own: str = ""


@dataclass
class PreferencesConfig:
    """User preferences configuration."""
    single_click: bool = True
    suit_layout: SuitLayout = SuitLayout.SHDC
    swap_ns_declarer: bool = True
    show_alert_marks: bool = True
    log_enabled: bool = True
    log_as_pbn: bool = False
    moved_cards_speed: float = 0.5
    language: str = "eng"
    use_double_dummy_play: bool = False
    use_monte_carlo_play: bool = True  # Monte Carlo simulation (MC+DDS fallback)
    use_nopeek_play: bool = True  # no-peek alpha-mu engine (default - strongest;
    # never peeks at hidden cards, signals + reads partner signals)
    sim_bidding_enabled: bool = True  # hybrid bidding: simulation checks the
    # rules at judgment points (backend.bid_sim). Slower, stronger.
    sim_leads_enabled: bool = True   # simulated opening leads (backend.lead_sim)
    signalling_enabled: bool = True  # play defensive signals at all. False =
    # never spend a card on a signal (plain lowest card / pure trick value);
    # partner-signal reading is off too. See backend.signals.set_enabled().
    signalling_convention: str = "standard"  # defensive carding biq plays AND
    # expects from partner: "standard" (hi=encourage, hi-lo=even) or "udca"
    # (upside-down count & attitude). Emitter + reader both use it.
    legacy_colors: bool = False  # Use legacy 2-color mode (red and black only)
    show_ben_bid_analysis: bool = False  # Show BEN bid analysis panel (disabled by default)
    # Which engine bots consult for bidding. "native" → the rule-based
    # bidder in backend.native_bidder (Q-Plus-style — the default,
    # since it covers seven systems with full spec-driven conventions);
    # "BEN" → the older neural-net engine, kept for users who prefer it.
    # Card play always stays with BEN regardless of this flag.
    bidding_engine: str = "native"
    # System used by the native bidder. Any name in
    # backend.bidding_systems.list_systems() (SAYC / TwoOverOne /
    # StandardAcol / StandardFrench / Precision90M / Precision90P /
    # Precision70) or an alias like "Precision", "2/1", "Wbridge5", etc.
    native_bidding_system: str = "Precision90M"
    # Per-pair systems — when set, override native_bidding_system on
    # the matching side. None / empty means "use native_bidding_system
    # for both pairs". Lets a teaching session run a different system
    # at NS vs EW (e.g. Precision90M vs SAYC) without forcing the
    # whole table onto one spec.
    ns_bidding_system: str = ""
    ew_bidding_system: str = ""
    # Blunder check: pop a confirmation dialog when the human's bid
    # differs from what the native bidder (current Q-Plus engine,
    # NOT the legacy BEN TensorFlow model) would have chosen for
    # the same seat. Defaults on; disable in preferences for a
    # silent table. Card-play blunder check is queued for a later
    # build that wires through DDS.
    blunder_check_enabled: bool = True
    # Set once the "biq's signalling conventions" explainer has been shown
    # (the first time the player's carding doesn't fit them).
    signal_help_shown: bool = False
    # One Player + MiniBridge — Q-Plus .one-player. "off" plays
    # standard auction bridge; "minibridge" replaces the auction
    # with two simplified rounds (points then contract);
    # "one_player" gives the engine only the named seat's hand
    # so the user can prove the program isn't cheating. Sub-
    # options below only matter when the matching mode is active.
    play_mode: str = "off"  # 'off' / 'minibridge' / 'one_player'
    # One-Player options.
    one_player_seat: str = "S"  # 'N' / 'E' / 'S' / 'W'
    one_player_use_for_sim: bool = True
    # MiniBridge options.
    mini_show_all_hcp: bool = True
    mini_auto_declarer: bool = True
    mini_suggest_contract: bool = True
    # Show the "Information about the bids" panel docked at the upper-left of
    # the bidding screen (embedded, not a floating window). Off hides it.
    show_bid_info_panel: bool = True
    # Claude Code integration (post-hand AI analysis, annotated transcripts,
    # AI hints). OFF by default — it shells out to the `claude` CLI, costs
    # tokens, and isn't needed for ordinary play. Turn on in Preferences.
    claude_code_enabled: bool = False
    # Whether a Q-Plus install is available for closed-room / Q-NET play.
    # "none"  → no Q-Plus (default): hide the closed-room / Q-NET features.
    # "demo"  → Q-Plus demo build available (limited boards).
    # "full"  → full licensed Q-Plus available.
    qplus_availability: str = "none"
    # Per-pair carding agreement shown/decoded in the instrumented view. Preset
    # names from teaching_view.CARDING_PRESETS ("Standard", "Upside-down",
    # "Std + Lavinthal", "UDCA + Lavinthal"). Empty = derive from
    # signalling_convention. smith_echo ∈ Off / Standard / Reverse.
    carding_ns: str = ""
    carding_ew: str = ""
    smith_echo: str = "Off"
    # Explain biq's actions: when ON, clicking a bid in the auction or a card
    # on the table pops up a window explaining why biq made that call/play
    # (how it fits the bidding system / what card-play technique applies).
    # ON by default — clicking a biq action to see why is a headline feature;
    # toggle it off in Preferences if the popups get in the way.
    explain_actions_enabled: bool = True


@dataclass
class LeadConventions:
    """Lead conventions configuration."""
    suit_lead: str = "-hiA-lo2-ln4-lwN-3cH-"
    notrump_lead: str = "-hiA-lo2-ln4-lwN-3cH-"


@dataclass
class SignalConventions:
    """Signalling conventions configuration."""
    signal_string: str = "-ldA-lwN-paA-pwN-dcC-maH-mcH-pcY-dsN-dnN-"


@dataclass
class GameConfig:
    """Complete game configuration."""
    version: str = "17.1"

    # Players
    north: PlayerConfig = field(default_factory=lambda: PlayerConfig(name="N: BridgeIQ"))
    east: PlayerConfig = field(default_factory=lambda: PlayerConfig(name="E: BridgeIQ"))
    south: PlayerConfig = field(default_factory=lambda: PlayerConfig(
        player_type=PlayerType.HUMAN, visible=True, name="S: HUMAN"))
    west: PlayerConfig = field(default_factory=lambda: PlayerConfig(name="W: BridgeIQ"))

    # Strength settings (0-100)
    strength_ns: int = 72
    strength_ew: int = 72

    # Conventions
    bidding_system_ns: str = "BEN-NN"
    bidding_system_ew: str = "BEN-NN"
    lead_conventions_ns: LeadConventions = field(default_factory=LeadConventions)
    lead_conventions_ew: LeadConventions = field(default_factory=LeadConventions)
    signal_conventions_ns: SignalConventions = field(default_factory=SignalConventions)
    signal_conventions_ew: SignalConventions = field(default_factory=SignalConventions)

    # Match settings
    match: MatchConfig = field(default_factory=MatchConfig)

    # Preferences
    preferences: PreferencesConfig = field(default_factory=PreferencesConfig)

    # Cheating mode
    cheating: CheatingMode = CheatingMode.NEVER

    # Memory model
    memory_model: int = 5


class ConfigManager:
    """Manages configuration files for BridgeIQ."""

    CONFIG_DIR = Path(__file__).parent.parent / "CONFIG"

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize the configuration manager.

        Args:
            config_dir: Override the default configuration directory.
        """
        self.config_dir = Path(config_dir) if config_dir else self.CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        for subdir in ["BIDRULE", "COLOR", "FILTER", "USER", "LICENSE"]:
            (self.config_dir / subdir).mkdir(exist_ok=True)

        self.config = GameConfig()
        self._load_or_create_defaults()

    def _load_or_create_defaults(self):
        """Load existing config or create defaults."""
        init_file = self.config_dir / "B-INIT.CFG"
        if init_file.exists():
            self.load_init_config()
        else:
            self.save_init_config()

    def _parse_config_file(self, filepath: Path) -> Dict[str, str]:
        """Parse a BridgeIQ configuration file.

        Format:
            # Comment line
            .version = 17.1
            key.subkey = value
            key = value
        """
        data = {}
        if not filepath.exists():
            return data

        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()
                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    data[key] = value

        return data

    def _write_config_file(self, filepath: Path, data: Dict[str, str],
                          description: str = "", header_comment: str = ""):
        """Write a BridgeIQ configuration file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# BridgeIQ configuration file\n")
            if header_comment:
                f.write(f"# {header_comment}\n")
            f.write(f".version = {self.config.version}\n")
            if description:
                f.write(f".description.eng = \"{description}\"\n")
            f.write("\n")

            for key, value in data.items():
                if key.startswith('.'):
                    continue  # Skip version/description, already written
                if isinstance(value, str) and ' ' in value:
                    f.write(f"{key} = \"{value}\"\n")
                else:
                    f.write(f"{key} = {value}\n")

    def load_init_config(self):
        """Load initialization configuration."""
        filepath = self.config_dir / "B-INIT.CFG"
        data = self._parse_config_file(filepath)

        if "strength.N/S" in data:
            self.config.strength_ns = int(data["strength.N/S"])
        if "strength.E/W" in data:
            self.config.strength_ew = int(data["strength.E/W"])
        if "memory.model" in data:
            self.config.memory_model = int(data["memory.model"])
        if "general.cheating" in data:
            try:
                self.config.cheating = CheatingMode(data["general.cheating"])
            except ValueError:
                pass

    def save_init_config(self):
        """Save initialization configuration."""
        filepath = self.config_dir / "B-INIT.CFG"
        data = {
            "is_init_config": "1",
            "strength.N/S": str(self.config.strength_ns),
            "strength.E/W": str(self.config.strength_ew),
            "memory.model": str(self.config.memory_model),
            "general.cheating": self.config.cheating.value,
        }
        self._write_config_file(filepath, data, description="BridgeIQ initialization")

    def load_match_config(self):
        """Load match control configuration."""
        filepath = self.config_dir / "B-MATCH.CFG"
        data = self._parse_config_file(filepath)

        if "general.deal_number" in data:
            self.config.match.deal_number = float(data["general.deal_number"])
        if "general.deal_source" in data:
            self.config.match.deal_source = data["general.deal_source"]
        if "general.scoring" in data:
            try:
                self.config.match.scoring_method = ScoringMethod(data["general.scoring"])
            except ValueError:
                pass

    def save_match_config(self):
        """Save match control configuration."""
        filepath = self.config_dir / "B-MATCH.CFG"
        date_str = datetime.now().strftime("%b/%d/%Y")
        data = {
            ".date": date_str,
            "general.deal_number": str(self.config.match.deal_number),
            "general.deal_source": self.config.match.deal_source,
            "general.scoring": self.config.match.scoring_method.value,
            "general.comparison": self.config.match.comparison.value,
        }
        self._write_config_file(filepath, data, description="BridgeIQ match control")

    def load_preferences(self):
        """Load user preferences."""
        filepath = self.config_dir / "B-PREFER.CFG"
        data = self._parse_config_file(filepath)

        if "preference.single_click" in data:
            self.config.preferences.single_click = data["preference.single_click"] == "1"
        if "preference.suit_layout" in data:
            self.config.preferences.suit_layout = SuitLayout(int(data["preference.suit_layout"]))
        if "preference.swap_ns_declarer" in data:
            self.config.preferences.swap_ns_declarer = data["preference.swap_ns_declarer"] == "1"
        # show_alert_marks was being written into self.prefs by the
        # Preferences dialog but never saved OR loaded — that left the
        # checkbox effectively non-persistent. Wire both directions
        # here and in save_preferences below.
        if "preference.show_alert_marks" in data:
            self.config.preferences.show_alert_marks = (
                data["preference.show_alert_marks"] == "1")
        if "preference.log_enabled" in data:
            self.config.preferences.log_enabled = data["preference.log_enabled"] == "1"
        # log_as_pbn / language were being saved but never read on the
        # next launch, so the user's choice was silently reverted to
        # the default. Add the missing load arms.
        if "preference.log_as_pbn" in data:
            self.config.preferences.log_as_pbn = (
                data["preference.log_as_pbn"] == "1")
        if "preference.language" in data:
            v = data["preference.language"].strip()
            if v:
                self.config.preferences.language = v
        if "preference.moved_cards" in data:
            self.config.preferences.moved_cards_speed = float(data["preference.moved_cards"])
        if "preference.use_dd_play" in data:
            self.config.preferences.use_double_dummy_play = data["preference.use_dd_play"] == "1"
        if "preference.use_mc_play" in data:
            self.config.preferences.use_monte_carlo_play = data["preference.use_mc_play"] == "1"
        if "preference.use_nopeek_play" in data:
            self.config.preferences.use_nopeek_play = data["preference.use_nopeek_play"] == "1"
        if "preference.signalling" in data:
            self.config.preferences.signalling_convention = data["preference.signalling"]
        if "preference.sim_bidding_enabled" in data:
            self.config.preferences.sim_bidding_enabled = (
                data["preference.sim_bidding_enabled"] == "1")
        if "preference.sim_leads_enabled" in data:
            self.config.preferences.sim_leads_enabled = (
                data["preference.sim_leads_enabled"] == "1")
        try:
            from backend import bid_sim as _bs, lead_sim as _ls
            _bs.set_enabled(self.config.preferences.sim_bidding_enabled)
            _ls.set_enabled(self.config.preferences.sim_leads_enabled)
        except Exception:
            pass
        if "preference.signalling_enabled" in data:
            self.config.preferences.signalling_enabled = (
                data["preference.signalling_enabled"] == "1")
        # Apply the signalling switch + convention to the live engine
        # (emitter + reader).
        try:
            from backend import signals as _sig
            _sig.set_enabled(self.config.preferences.signalling_enabled)
            _sig.set_convention(
                self.config.preferences.signalling_convention == "udca")
        except Exception:
            pass
        if "preference.legacy_colors" in data:
            self.config.preferences.legacy_colors = data["preference.legacy_colors"] == "1"
        if "preference.show_ben_bid_analysis" in data:
            self.config.preferences.show_ben_bid_analysis = data["preference.show_ben_bid_analysis"] == "1"
        if "preference.bidding_engine" in data:
            v = data["preference.bidding_engine"].strip()
            if v in ("BEN", "native"):
                self.config.preferences.bidding_engine = v
        if "preference.native_bidding_system" in data:
            v = data["preference.native_bidding_system"].strip()
            # Accept any non-empty value — the bidding-systems catalog
            # now lists 7+ Q-Plus specs and will keep growing, so a
            # hard whitelist would silently drop the user's setting
            # for every newly-added system. Unknown names fall back
            # to SAYC at lookup time in get_system().
            if v:
                self.config.preferences.native_bidding_system = v
        if "preference.ns_bidding_system" in data:
            v = data["preference.ns_bidding_system"].strip()
            self.config.preferences.ns_bidding_system = v
        if "preference.ew_bidding_system" in data:
            v = data["preference.ew_bidding_system"].strip()
            self.config.preferences.ew_bidding_system = v
        if "preference.blunder_check_enabled" in data:
            self.config.preferences.blunder_check_enabled = (
                data["preference.blunder_check_enabled"] == "1")
        if "preference.signal_help_shown" in data:
            self.config.preferences.signal_help_shown = (
                data["preference.signal_help_shown"] == "1")
        if "preference.play_mode" in data:
            v = data["preference.play_mode"].strip()
            if v in ("off", "minibridge", "one_player"):
                self.config.preferences.play_mode = v
        if "preference.one_player_seat" in data:
            v = data["preference.one_player_seat"].strip().upper()
            if v in ("N", "E", "S", "W"):
                self.config.preferences.one_player_seat = v
        if "preference.one_player_use_for_sim" in data:
            self.config.preferences.one_player_use_for_sim = (
                data["preference.one_player_use_for_sim"] == "1")
        if "preference.mini_show_all_hcp" in data:
            self.config.preferences.mini_show_all_hcp = (
                data["preference.mini_show_all_hcp"] == "1")
        if "preference.mini_auto_declarer" in data:
            self.config.preferences.mini_auto_declarer = (
                data["preference.mini_auto_declarer"] == "1")
        if "preference.mini_suggest_contract" in data:
            self.config.preferences.mini_suggest_contract = (
                data["preference.mini_suggest_contract"] == "1")
        if "preference.show_bid_info_panel" in data:
            self.config.preferences.show_bid_info_panel = (
                data["preference.show_bid_info_panel"] == "1")
        if "preference.claude_code_enabled" in data:
            self.config.preferences.claude_code_enabled = (
                data["preference.claude_code_enabled"] == "1")
        if "preference.qplus_availability" in data:
            v = data["preference.qplus_availability"].strip().lower()
            if v in ("none", "demo", "full"):
                self.config.preferences.qplus_availability = v
        if "preference.carding_ns" in data:
            self.config.preferences.carding_ns = data["preference.carding_ns"].strip()
        if "preference.carding_ew" in data:
            self.config.preferences.carding_ew = data["preference.carding_ew"].strip()
        if "preference.smith_echo" in data:
            v = data["preference.smith_echo"].strip()
            if v in ("Off", "Standard", "Reverse"):
                self.config.preferences.smith_echo = v
        if "preference.explain_actions_enabled" in data:
            self.config.preferences.explain_actions_enabled = (
                data["preference.explain_actions_enabled"] == "1")

    def save_preferences(self):
        """Save user preferences."""
        filepath = self.config_dir / "B-PREFER.CFG"
        data = {
            "preference.single_click": "1" if self.config.preferences.single_click else "0",
            "preference.suit_layout": str(self.config.preferences.suit_layout.value),
            "preference.swap_ns_declarer": "1" if self.config.preferences.swap_ns_declarer else "0",
            # show_alert_marks now persists in both directions; without
            # this write the alert-marks checkbox lost its setting on
            # every launch.
            "preference.show_alert_marks": (
                "1" if self.config.preferences.show_alert_marks else "0"),
            "preference.log_enabled": "1" if self.config.preferences.log_enabled else "0",
            "preference.log_as_pbn": "1" if self.config.preferences.log_as_pbn else "0",
            "preference.moved_cards": str(self.config.preferences.moved_cards_speed),
            "preference.language": self.config.preferences.language,
            "preference.use_dd_play": "1" if self.config.preferences.use_double_dummy_play else "0",
            "preference.use_mc_play": "1" if self.config.preferences.use_monte_carlo_play else "0",
            "preference.use_nopeek_play": "1" if self.config.preferences.use_nopeek_play else "0",
            "preference.signalling": self.config.preferences.signalling_convention,
            "preference.sim_bidding_enabled": (
                "1" if self.config.preferences.sim_bidding_enabled else "0"),
            "preference.sim_leads_enabled": (
                "1" if self.config.preferences.sim_leads_enabled else "0"),
            "preference.signalling_enabled": (
                "1" if self.config.preferences.signalling_enabled else "0"),
            "preference.legacy_colors": "1" if self.config.preferences.legacy_colors else "0",
            "preference.show_ben_bid_analysis": "1" if self.config.preferences.show_ben_bid_analysis else "0",
            "preference.bidding_engine": self.config.preferences.bidding_engine,
            "preference.native_bidding_system": self.config.preferences.native_bidding_system,
            "preference.ns_bidding_system": self.config.preferences.ns_bidding_system,
            "preference.ew_bidding_system": self.config.preferences.ew_bidding_system,
            "preference.blunder_check_enabled": (
                "1" if self.config.preferences.blunder_check_enabled else "0"),
            "preference.signal_help_shown": (
                "1" if self.config.preferences.signal_help_shown else "0"),
            "preference.play_mode": self.config.preferences.play_mode,
            "preference.one_player_seat":
                self.config.preferences.one_player_seat,
            "preference.one_player_use_for_sim": (
                "1" if self.config.preferences.one_player_use_for_sim
                else "0"),
            "preference.mini_show_all_hcp": (
                "1" if self.config.preferences.mini_show_all_hcp else "0"),
            "preference.mini_auto_declarer": (
                "1" if self.config.preferences.mini_auto_declarer else "0"),
            "preference.mini_suggest_contract": (
                "1" if self.config.preferences.mini_suggest_contract else "0"),
            "preference.show_bid_info_panel": (
                "1" if self.config.preferences.show_bid_info_panel else "0"),
            "preference.claude_code_enabled": (
                "1" if self.config.preferences.claude_code_enabled else "0"),
            "preference.qplus_availability":
                self.config.preferences.qplus_availability,
            "preference.carding_ns": self.config.preferences.carding_ns,
            "preference.carding_ew": self.config.preferences.carding_ew,
            "preference.smith_echo": self.config.preferences.smith_echo,
            "preference.explain_actions_enabled": (
                "1" if self.config.preferences.explain_actions_enabled
                else "0"),
        }
        self._write_config_file(filepath, data, description="BridgeIQ preferences")

    def save_all(self):
        """Save all configuration files."""
        self.save_init_config()
        self.save_match_config()
        self.save_preferences()

    def load_all(self):
        """Load all configuration files."""
        self.load_init_config()
        self.load_match_config()
        self.load_preferences()

    def get_config_summary(self) -> str:
        """Get a summary of current configuration (Check! feature)."""
        lines = [
            "BridgeIQ Configuration Summary",
            "=" * 40,
            "",
            f"Version: {self.config.version}",
            "",
            "Players:",
            f"  North: {self.config.north.player_type.name} - {self.config.north.bidding_system}",
            f"  East:  {self.config.east.player_type.name} - {self.config.east.bidding_system}",
            f"  South: {self.config.south.player_type.name} - {self.config.south.bidding_system}",
            f"  West:  {self.config.west.player_type.name} - {self.config.west.bidding_system}",
            "",
            "Strength:",
            f"  N/S: {self.config.strength_ns}",
            f"  E/W: {self.config.strength_ew}",
            "",
            "Bidding Systems:",
            f"  N/S: {self.config.bidding_system_ns}",
            f"  E/W: {self.config.bidding_system_ew}",
            "",
            "Match Settings:",
            f"  Scoring: {self.config.match.scoring_method.value}",
            f"  Deal Source: {self.config.match.deal_source}",
            f"  Comparison: {self.config.match.comparison.value}",
            "",
            "Preferences:",
            f"  Single Click: {self.config.preferences.single_click}",
            f"  Suit Layout: {'SHDC' if self.config.preferences.suit_layout == SuitLayout.SHDC else 'SHCD'}",
            f"  Swap N/S when N declarer: {self.config.preferences.swap_ns_declarer}",
            f"  Logging: {self.config.preferences.log_enabled}",
            "",
            f"Cheating: {self.config.cheating.value}",
        ]
        return "\n".join(lines)


@dataclass
class DealFilter:
    """Deal filter configuration for constrained deal generation."""

    @dataclass
    class HandConstraints:
        hcp_min: int = 0
        hcp_max: int = 40
        spade_min: int = 0
        spade_max: int = 13
        heart_min: int = 0
        heart_max: int = 13
        diamond_min: int = 0
        diamond_max: int = 13
        club_min: int = 0
        club_max: int = 13
        join_and: bool = True  # True for AND, False for OR

    description: str = ""
    search_limit: int = 100
    join_and: bool = True

    north: HandConstraints = field(default_factory=HandConstraints)
    south: HandConstraints = field(default_factory=HandConstraints)
    east: HandConstraints = field(default_factory=HandConstraints)
    west: HandConstraints = field(default_factory=HandConstraints)

    # Partnership constraints
    ns_hcp_min: int = 0
    ns_hcp_max: int = 40
    ew_hcp_min: int = 0
    ew_hcp_max: int = 40


class FilterManager:
    """Manages deal filter files."""

    FILTER_DIR = Path(__file__).parent.parent / "CONFIG" / "FILTER"

    def __init__(self, filter_dir: Optional[Path] = None):
        self.filter_dir = Path(filter_dir) if filter_dir else self.FILTER_DIR
        self.filter_dir.mkdir(parents=True, exist_ok=True)

    def load_filter(self, name: str) -> Optional[DealFilter]:
        """Load a deal filter by name."""
        # Try both lowercase and uppercase extensions
        filepath = self.filter_dir / f"{name}.flt"
        if not filepath.exists():
            filepath = self.filter_dir / f"{name}.FLT"
        if not filepath.exists():
            return None

        filter_obj = DealFilter()

        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()

                    self._apply_filter_value(filter_obj, key, value)

        return filter_obj

    def _apply_filter_value(self, filter_obj: DealFilter, key: str, value: str):
        """Apply a parsed filter value to the filter object."""
        parts = key.split('.')

        if key == "join":
            filter_obj.join_and = (value.upper() == "A")
        elif key == "search":
            filter_obj.search_limit = int(value)
        elif key.startswith(".description"):
            filter_obj.description = value.strip('"')
        elif len(parts) == 2:
            position, attr = parts

            # Get the hand constraints object
            hand = None
            if position == "North":
                hand = filter_obj.north
            elif position == "South":
                hand = filter_obj.south
            elif position == "East":
                hand = filter_obj.east
            elif position == "West":
                hand = filter_obj.west
            elif position == "N/S":
                # Partnership constraint
                if attr == "total":
                    min_val, max_val = self._parse_range(value)
                    filter_obj.ns_hcp_min = min_val
                    filter_obj.ns_hcp_max = max_val
                return
            elif position == "E/W":
                if attr == "total":
                    min_val, max_val = self._parse_range(value)
                    filter_obj.ew_hcp_min = min_val
                    filter_obj.ew_hcp_max = max_val
                return

            if hand is None:
                return

            if attr == "join":
                hand.join_and = (value.upper() == "A")
            elif attr == "hcp":
                min_val, max_val = self._parse_range(value)
                hand.hcp_min = min_val
                hand.hcp_max = max_val
            elif attr == "Spade":
                min_val, max_val = self._parse_range(value)
                hand.spade_min = min_val
                hand.spade_max = max_val
            elif attr == "Heart":
                min_val, max_val = self._parse_range(value)
                hand.heart_min = min_val
                hand.heart_max = max_val
            elif attr == "Diamond":
                min_val, max_val = self._parse_range(value)
                hand.diamond_min = min_val
                hand.diamond_max = max_val
            elif attr == "Club":
                min_val, max_val = self._parse_range(value)
                hand.club_min = min_val
                hand.club_max = max_val

    def _parse_range(self, value: str) -> tuple:
        """Parse a range value like '12.14' or '*.7' or '8.*'."""
        parts = value.split('.')
        if len(parts) != 2:
            return (0, 40)

        min_str, max_str = parts
        min_val = 0 if min_str == '*' else int(min_str)
        max_val = 40 if max_str == '*' else int(max_str)

        return (min_val, max_val)

    def save_filter(self, name: str, filter_obj: DealFilter):
        """Save a deal filter to file."""
        filepath = self.filter_dir / f"{name}.flt"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("# BridgeIQ deal filter\n")
            f.write(".version = 17.1\n")
            if filter_obj.description:
                f.write(f".description.eng = \"{filter_obj.description}\"\n")
            f.write("\n")
            f.write(f"join = {'A' if filter_obj.join_and else 'O'}\n")
            f.write(f"search = {filter_obj.search_limit}\n")

            # Write hand constraints
            for position, hand in [("South", filter_obj.south), ("North", filter_obj.north),
                                   ("East", filter_obj.east), ("West", filter_obj.west)]:
                if self._has_constraints(hand):
                    f.write(f"{position}.join = {'A' if hand.join_and else 'O'}\n")
                    if hand.hcp_min > 0 or hand.hcp_max < 40:
                        f.write(f"{position}.hcp = {hand.hcp_min}.{hand.hcp_max}\n")
                    if hand.spade_min > 0 or hand.spade_max < 13:
                        f.write(f"{position}.Spade = {hand.spade_min}.{hand.spade_max}\n")
                    if hand.heart_min > 0 or hand.heart_max < 13:
                        f.write(f"{position}.Heart = {hand.heart_min}.{hand.heart_max}\n")
                    if hand.diamond_min > 0 or hand.diamond_max < 13:
                        f.write(f"{position}.Diamond = {hand.diamond_min}.{hand.diamond_max}\n")
                    if hand.club_min > 0 or hand.club_max < 13:
                        f.write(f"{position}.Club = {hand.club_min}.{hand.club_max}\n")

    def _has_constraints(self, hand: DealFilter.HandConstraints) -> bool:
        """Check if a hand has any non-default constraints."""
        return (hand.hcp_min > 0 or hand.hcp_max < 40 or
                hand.spade_min > 0 or hand.spade_max < 13 or
                hand.heart_min > 0 or hand.heart_max < 13 or
                hand.diamond_min > 0 or hand.diamond_max < 13 or
                hand.club_min > 0 or hand.club_max < 13)

    def list_filters(self) -> List[str]:
        """List all available filter files."""
        filters = []
        for f in self.filter_dir.iterdir():
            if f.suffix.lower() == '.flt':
                filters.append(f.stem)
        return sorted(filters)


# Global instance for convenience
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get the global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
