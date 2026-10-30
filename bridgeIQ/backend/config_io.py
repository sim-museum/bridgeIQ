"""
Configuration File I/O - Read and write BridgeIQ .CFG files.

CFG files use INI-style format with sections and key-value pairs.
This module provides loading and saving of bridge configuration.
"""

from pathlib import Path
from typing import Dict, Any, Optional
import configparser
from dataclasses import dataclass, field, asdict
from enum import Enum
import json


@dataclass
class MouseConfig:
    """Mouse and input configuration"""
    single_click_play: bool = True
    double_click_speed_ms: int = 500
    drag_cards: bool = False
    highlight_valid_cards: bool = True


@dataclass
class DisplayConfig:
    """Display and visual configuration"""
    suit_layout: str = "SHDC"  # Order of suits: SHDC, SHCD, etc.
    card_style: str = "modern"  # modern, classic, simple
    swap_ns_for_north_declarer: bool = False
    show_card_count: bool = True
    animate_card_play: bool = True
    animation_speed_ms: int = 300


@dataclass
class BiddingConfig:
    """Bidding display configuration"""
    show_bid_meanings: bool = True
    show_hcp_ranges: bool = True
    show_suit_lengths: bool = True
    alert_own_bids: bool = True
    auto_explain_opponent_bids: bool = True
    bidding_box_position: str = "right"  # right, bottom, floating


@dataclass
class LoggingConfig:
    """Game logging configuration"""
    auto_log: bool = True
    log_format: str = "bdl"  # bdl, pbn, both
    log_directory: str = "DATA/LOGFILES/"
    include_pavlicek_id: bool = True
    include_timestamps: bool = True
    rotate_log_files: bool = True
    max_log_files: int = 100


@dataclass
class ScoringConfig:
    """Scoring configuration"""
    scoring_type: str = "IMP"  # IMP, MP, RUBBER, TOTAL
    comparison_mode: bool = False
    compute_par: bool = True


@dataclass
class MatchConfig:
    """Match/session configuration"""
    deal_source: str = "random"  # random, file, manual, pavlicek
    deal_file: str = ""
    boards_per_session: int = 16
    auto_advance: bool = False
    show_results_after_hand: bool = True


@dataclass
class PlayerConfig:
    """Player configuration"""
    north_type: str = "computer"  # human, computer, external
    east_type: str = "computer"
    south_type: str = "human"
    west_type: str = "computer"
    north_visible: bool = False
    east_visible: bool = False
    south_visible: bool = True
    west_visible: bool = False


@dataclass
class BridgePreferences:
    """Complete bridge preferences"""
    mouse: MouseConfig = field(default_factory=MouseConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    bidding: BiddingConfig = field(default_factory=BiddingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    player: PlayerConfig = field(default_factory=PlayerConfig)

    # Bidding system
    bidding_system: str = "SAYC"
    ns_bidding_system: str = "SAYC"
    ew_bidding_system: str = "SAYC"
    enabled_conventions: list = field(default_factory=list)

    # Card play options
    use_double_dummy_play: bool = False
    use_monte_carlo_play: bool = True  # Monte Carlo simulation (default)


class ConfigIO:
    """Read and write BridgeIQ .CFG files"""

    @staticmethod
    def read_cfg(filepath: Path) -> Dict[str, Dict[str, str]]:
        """Read CFG file into nested dictionary"""
        config = configparser.ConfigParser()
        config.optionxform = str  # Preserve case
        config.read(filepath, encoding='utf-8')

        result = {}
        for section in config.sections():
            result[section] = dict(config[section])

        return result

    @staticmethod
    def write_cfg(filepath: Path, data: Dict[str, Dict[str, Any]]):
        """Write nested dictionary to CFG file"""
        config = configparser.ConfigParser()
        config.optionxform = str  # Preserve case

        for section, values in data.items():
            config[section] = {k: str(v) for k, v in values.items()}

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            config.write(f)

    @staticmethod
    def save_preferences(prefs: BridgePreferences, filepath: Path):
        """Save BridgePreferences to CFG file"""
        data = {
            'Mouse': {
                'single_click_play': str(prefs.mouse.single_click_play),
                'double_click_speed_ms': str(prefs.mouse.double_click_speed_ms),
                'drag_cards': str(prefs.mouse.drag_cards),
                'highlight_valid_cards': str(prefs.mouse.highlight_valid_cards),
            },
            'Display': {
                'suit_layout': prefs.display.suit_layout,
                'card_style': prefs.display.card_style,
                'swap_ns_for_north_declarer': str(prefs.display.swap_ns_for_north_declarer),
                'show_card_count': str(prefs.display.show_card_count),
                'animate_card_play': str(prefs.display.animate_card_play),
                'animation_speed_ms': str(prefs.display.animation_speed_ms),
            },
            'Bidding': {
                'show_bid_meanings': str(prefs.bidding.show_bid_meanings),
                'show_hcp_ranges': str(prefs.bidding.show_hcp_ranges),
                'show_suit_lengths': str(prefs.bidding.show_suit_lengths),
                'alert_own_bids': str(prefs.bidding.alert_own_bids),
                'auto_explain_opponent_bids': str(prefs.bidding.auto_explain_opponent_bids),
                'bidding_box_position': prefs.bidding.bidding_box_position,
            },
            'Logging': {
                'auto_log': str(prefs.logging.auto_log),
                'log_format': prefs.logging.log_format,
                'log_directory': prefs.logging.log_directory,
                'include_pavlicek_id': str(prefs.logging.include_pavlicek_id),
                'include_timestamps': str(prefs.logging.include_timestamps),
                'rotate_log_files': str(prefs.logging.rotate_log_files),
                'max_log_files': str(prefs.logging.max_log_files),
            },
            'Scoring': {
                'scoring_type': prefs.scoring.scoring_type,
                'comparison_mode': str(prefs.scoring.comparison_mode),
                'compute_par': str(prefs.scoring.compute_par),
            },
            'Match': {
                'deal_source': prefs.match.deal_source,
                'deal_file': prefs.match.deal_file,
                'boards_per_session': str(prefs.match.boards_per_session),
                'auto_advance': str(prefs.match.auto_advance),
                'show_results_after_hand': str(prefs.match.show_results_after_hand),
            },
            'Players': {
                'north_type': prefs.player.north_type,
                'east_type': prefs.player.east_type,
                'south_type': prefs.player.south_type,
                'west_type': prefs.player.west_type,
                'north_visible': str(prefs.player.north_visible),
                'east_visible': str(prefs.player.east_visible),
                'south_visible': str(prefs.player.south_visible),
                'west_visible': str(prefs.player.west_visible),
            },
            'BiddingSystems': {
                'bidding_system': prefs.bidding_system,
                'ns_bidding_system': prefs.ns_bidding_system,
                'ew_bidding_system': prefs.ew_bidding_system,
                'enabled_conventions': ','.join(prefs.enabled_conventions),
            },
        }

        ConfigIO.write_cfg(filepath, data)

    @staticmethod
    def load_preferences(filepath: Path) -> Optional[BridgePreferences]:
        """Load BridgePreferences from CFG file"""
        if not filepath.exists():
            return None

        data = ConfigIO.read_cfg(filepath)
        prefs = BridgePreferences()

        def get_bool(section: str, key: str, default: bool = False) -> bool:
            if section in data and key in data[section]:
                val = data[section][key].lower()
                return val in ('true', '1', 'yes', 'on')
            return default

        def get_str(section: str, key: str, default: str = "") -> str:
            if section in data and key in data[section]:
                return data[section][key]
            return default

        def get_int(section: str, key: str, default: int = 0) -> int:
            if section in data and key in data[section]:
                try:
                    return int(data[section][key])
                except ValueError:
                    pass
            return default

        # Mouse config
        prefs.mouse.single_click_play = get_bool('Mouse', 'single_click_play', True)
        prefs.mouse.double_click_speed_ms = get_int('Mouse', 'double_click_speed_ms', 500)
        prefs.mouse.drag_cards = get_bool('Mouse', 'drag_cards', False)
        prefs.mouse.highlight_valid_cards = get_bool('Mouse', 'highlight_valid_cards', True)

        # Display config
        prefs.display.suit_layout = get_str('Display', 'suit_layout', 'SHDC')
        prefs.display.card_style = get_str('Display', 'card_style', 'modern')
        prefs.display.swap_ns_for_north_declarer = get_bool('Display', 'swap_ns_for_north_declarer', False)
        prefs.display.show_card_count = get_bool('Display', 'show_card_count', True)
        prefs.display.animate_card_play = get_bool('Display', 'animate_card_play', True)
        prefs.display.animation_speed_ms = get_int('Display', 'animation_speed_ms', 300)

        # Bidding config
        prefs.bidding.show_bid_meanings = get_bool('Bidding', 'show_bid_meanings', True)
        prefs.bidding.show_hcp_ranges = get_bool('Bidding', 'show_hcp_ranges', True)
        prefs.bidding.show_suit_lengths = get_bool('Bidding', 'show_suit_lengths', True)
        prefs.bidding.alert_own_bids = get_bool('Bidding', 'alert_own_bids', True)
        prefs.bidding.auto_explain_opponent_bids = get_bool('Bidding', 'auto_explain_opponent_bids', True)
        prefs.bidding.bidding_box_position = get_str('Bidding', 'bidding_box_position', 'right')

        # Logging config
        prefs.logging.auto_log = get_bool('Logging', 'auto_log', True)
        prefs.logging.log_format = get_str('Logging', 'log_format', 'bdl')
        prefs.logging.log_directory = get_str('Logging', 'log_directory', 'DATA/LOGFILES/')
        prefs.logging.include_pavlicek_id = get_bool('Logging', 'include_pavlicek_id', True)
        prefs.logging.include_timestamps = get_bool('Logging', 'include_timestamps', True)
        prefs.logging.rotate_log_files = get_bool('Logging', 'rotate_log_files', True)
        prefs.logging.max_log_files = get_int('Logging', 'max_log_files', 100)

        # Scoring config
        prefs.scoring.scoring_type = get_str('Scoring', 'scoring_type', 'IMP')
        prefs.scoring.comparison_mode = get_bool('Scoring', 'comparison_mode', False)
        prefs.scoring.compute_par = get_bool('Scoring', 'compute_par', True)

        # Match config
        prefs.match.deal_source = get_str('Match', 'deal_source', 'random')
        prefs.match.deal_file = get_str('Match', 'deal_file', '')
        prefs.match.boards_per_session = get_int('Match', 'boards_per_session', 16)
        prefs.match.auto_advance = get_bool('Match', 'auto_advance', False)
        prefs.match.show_results_after_hand = get_bool('Match', 'show_results_after_hand', True)

        # Player config
        prefs.player.north_type = get_str('Players', 'north_type', 'computer')
        prefs.player.east_type = get_str('Players', 'east_type', 'computer')
        prefs.player.south_type = get_str('Players', 'south_type', 'human')
        prefs.player.west_type = get_str('Players', 'west_type', 'computer')
        prefs.player.north_visible = get_bool('Players', 'north_visible', False)
        prefs.player.east_visible = get_bool('Players', 'east_visible', False)
        prefs.player.south_visible = get_bool('Players', 'south_visible', True)
        prefs.player.west_visible = get_bool('Players', 'west_visible', False)

        # Bidding systems
        prefs.bidding_system = get_str('BiddingSystems', 'bidding_system', 'SAYC')
        prefs.ns_bidding_system = get_str('BiddingSystems', 'ns_bidding_system', 'SAYC')
        prefs.ew_bidding_system = get_str('BiddingSystems', 'ew_bidding_system', 'SAYC')

        conventions_str = get_str('BiddingSystems', 'enabled_conventions', '')
        if conventions_str:
            prefs.enabled_conventions = [c.strip() for c in conventions_str.split(',') if c.strip()]

        return prefs

    @staticmethod
    def save_preferences_json(prefs: BridgePreferences, filepath: Path):
        """Save preferences to JSON format"""

        def serialize(obj):
            if hasattr(obj, '__dict__'):
                return {k: serialize(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, list):
                return [serialize(v) for v in obj]
            elif isinstance(obj, Enum):
                return obj.value
            else:
                return obj

        data = serialize(prefs)

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def load_preferences_json(filepath: Path) -> Optional[BridgePreferences]:
        """Load preferences from JSON format"""
        if not filepath.exists():
            return None

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        prefs = BridgePreferences()

        # Load each config section
        if 'mouse' in data:
            for k, v in data['mouse'].items():
                if hasattr(prefs.mouse, k):
                    setattr(prefs.mouse, k, v)

        if 'display' in data:
            for k, v in data['display'].items():
                if hasattr(prefs.display, k):
                    setattr(prefs.display, k, v)

        if 'bidding' in data:
            for k, v in data['bidding'].items():
                if hasattr(prefs.bidding, k):
                    setattr(prefs.bidding, k, v)

        if 'logging' in data:
            for k, v in data['logging'].items():
                if hasattr(prefs.logging, k):
                    setattr(prefs.logging, k, v)

        if 'scoring' in data:
            for k, v in data['scoring'].items():
                if hasattr(prefs.scoring, k):
                    setattr(prefs.scoring, k, v)

        if 'match' in data:
            for k, v in data['match'].items():
                if hasattr(prefs.match, k):
                    setattr(prefs.match, k, v)

        if 'player' in data:
            for k, v in data['player'].items():
                if hasattr(prefs.player, k):
                    setattr(prefs.player, k, v)

        if 'bidding_system' in data:
            prefs.bidding_system = data['bidding_system']
        if 'ns_bidding_system' in data:
            prefs.ns_bidding_system = data['ns_bidding_system']
        if 'ew_bidding_system' in data:
            prefs.ew_bidding_system = data['ew_bidding_system']
        if 'enabled_conventions' in data:
            prefs.enabled_conventions = data['enabled_conventions']

        return prefs


# Default config file paths
DEFAULT_CONFIG_DIR = Path("CONFIG")
DEFAULT_PREFS_FILE = DEFAULT_CONFIG_DIR / "B-PREFER.CFG"
DEFAULT_PREFS_JSON = DEFAULT_CONFIG_DIR / "preferences.json"


def get_default_preferences() -> BridgePreferences:
    """Get default preferences, loading from file if available"""
    # Try JSON first, then CFG
    if DEFAULT_PREFS_JSON.exists():
        prefs = ConfigIO.load_preferences_json(DEFAULT_PREFS_JSON)
        if prefs:
            return prefs

    if DEFAULT_PREFS_FILE.exists():
        prefs = ConfigIO.load_preferences(DEFAULT_PREFS_FILE)
        if prefs:
            return prefs

    return BridgePreferences()


def save_default_preferences(prefs: BridgePreferences):
    """Save preferences to default location"""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Save both formats
    ConfigIO.save_preferences(prefs, DEFAULT_PREFS_FILE)
    ConfigIO.save_preferences_json(prefs, DEFAULT_PREFS_JSON)


if __name__ == "__main__":
    # Test config I/O
    prefs = BridgePreferences()
    prefs.bidding_system = "2/1"
    prefs.enabled_conventions = ["STAYMAN", "JACOBY_TRANSFERS", "RKCB_1430"]
    prefs.player.south_type = "human"
    prefs.player.north_type = "computer"

    test_file = Path("test_config.cfg")
    ConfigIO.save_preferences(prefs, test_file)
    print(f"Saved config to {test_file}")

    loaded = ConfigIO.load_preferences(test_file)
    print(f"Loaded config:")
    print(f"  Bidding system: {loaded.bidding_system}")
    print(f"  Conventions: {loaded.enabled_conventions}")
    print(f"  South type: {loaded.player.south_type}")

    test_file.unlink()  # Clean up
