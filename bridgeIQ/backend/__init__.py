"""
BEN Backend - Thin wrapper around BEN's Bridge Engine API.
Provides bidding, play, analysis, and configuration capabilities.
"""

from .engine import BridgeEngine
from .models import (
    BoardState, Player, Contract, Bid, Card, Trick,
    Hand, Suit, Rank, Seat, Vulnerability, PlayerType
)
from .scoring import ScoringTable, ScoringType, BoardResult
from .pavlicek import (
    deal_to_number, number_to_deal,
    format_deal_base72, int_to_base72, base72_to_int,
    format_deal_base62, int_to_base62, base62_to_int,
    pbn_to_deal_number, deal_number_to_pbn
)

# File format support
from .bdl_reader import BDLReader, BDLDeal, load_bdl_file
from .pbn_exporter import PBNExporter

# Configuration
from .config_io import (
    ConfigIO, BridgePreferences,
    MouseConfig, DisplayConfig, BiddingConfig, LoggingConfig,
    ScoringConfig, MatchConfig, PlayerConfig,
    get_default_preferences, save_default_preferences
)


__all__ = [
    # Core engine
    'BridgeEngine',

    # Models
    'BoardState', 'Player', 'Contract', 'Bid', 'Card', 'Trick',
    'Hand', 'Suit', 'Rank', 'Seat', 'Vulnerability', 'PlayerType',

    # Scoring
    'ScoringTable', 'ScoringType', 'BoardResult',

    # Deal encoding (Pavlicek)
    'deal_to_number', 'number_to_deal',
    'format_deal_base72', 'int_to_base72', 'base72_to_int',
    'format_deal_base62', 'int_to_base62', 'base62_to_int',
    'pbn_to_deal_number', 'deal_number_to_pbn',

    # File formats
    'BDLReader', 'BDLDeal', 'load_bdl_file',
    'PBNExporter',

    # Configuration
    'ConfigIO', 'BridgePreferences',
    'MouseConfig', 'DisplayConfig', 'BiddingConfig', 'LoggingConfig',
    'ScoringConfig', 'MatchConfig', 'PlayerConfig',
    'get_default_preferences', 'save_default_preferences',
]
