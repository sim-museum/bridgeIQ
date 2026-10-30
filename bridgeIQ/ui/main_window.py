"""
MainWindow - Central window for the BridgeIQ application.
Classic Bridge interface with declarer play support.
"""

import os
import re
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QMenuBar, QMenu, QStatusBar, QToolBar, QLabel,
    QProgressBar, QMessageBox, QFileDialog, QApplication,
    QDockWidget, QPushButton, QFrame, QSizePolicy, QInputDialog,
    QDialog, QStackedWidget, QTextEdit
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, pyqtSlot, QPoint
from PyQt6.QtGui import QAction, QKeySequence, QFont, QIcon

from .table_view import TableView, COLORS
from .bidding_box import BiddingBox
from .game_logger import GameLogger
from .dialogs.player_config import PlayerConfigDialog
from .dialogs.match_control import MatchControlDialog
from .dialogs.deal_filter import DealFilterDialog
from .dialogs.score_table import ScoreTableDialog
from .dialogs.simulation import SimulationDialog
from .dialogs.qplus_simulation import QPlusSimulationDialog
from .dialogs.preferences import PreferencesDialog
from .dialogs.deal_entry import DealEntryDialog
from .dialogs.scoring_table import ScoringTableDialog
from .dialogs.info_tables import IMPTableDialog, ProbabilitiesDialog, ScoringReferenceDialog
from .dialogs.bidding_system import BiddingSystemDialog
from .dialogs.end_of_hand import EndOfHandDialog, PassedOutDialog
from .dialogs.teams_score import TeamsScoreDialog
from .dialogs.replay_view import ReplayViewDialog
from .dialogs.deal_converter import DealConverterDialog
from .dialogs.start_server_dialog import StartServerDialog
from .dialogs.connect_server_dialog import ConnectServerDialog
from .dialogs.network_lobby_dialog import NetworkLobbyDialog
from .dialogs.programming_help import ProgrammingHelpDialog

from backend import BridgeEngine, BoardState
from network import NetworkGameController
from backend.models import (
    Seat, Suit, Rank, Vulnerability, Bid, Card, Contract, Hand, PlayerType, Player, Trick,
    BenTable, BenBoardRun, BenTeamsMatch
)
from backend.pavlicek import number_to_deal, parse_deal_number, deal_to_number, format_deal_base72, int_to_base72
from backend.config import get_config_manager, ConfigManager
from backend.scoring import ScoringTable, ScoringType, BoardResult, calculate_contract_score
from backend.match_controller import TeamsMatchController

from typing import Optional, Dict, List


class EngineWorker(QThread):
    """Worker thread for engine operations"""

    bid_ready = pyqtSignal(object)  # Emits EngineResponse
    card_ready = pyqtSignal(object)  # Emits EngineResponse
    error = pyqtSignal(str)

    def __init__(self, engine: BridgeEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.task = None
        self.board = None
        self.seat = None
        self.trick_cards = None

    def request_bid(self, board: BoardState, seat: Seat):
        if self.isRunning():
            return  # Don't start if already running
        self.task = 'bid'
        self.board = board
        self.seat = seat
        self.start()

    def request_card(self, board: BoardState, seat: Seat, trick_cards=None):
        if self.isRunning():
            return False  # Indicate request was not started
        self.task = 'card'
        self.board = board
        self.seat = seat
        self.trick_cards = trick_cards or []
        self.start()
        return True  # Indicate request was started

    def run(self):
        try:
            if self.task == 'bid':
                # Route bidding to the native engine when the preference is
                # set, falling back to BEN for any error or for "BEN".
                bidder = self.engine
                try:
                    from backend.config import get_config_manager
                    prefs = get_config_manager().config.preferences
                    if getattr(prefs, 'bidding_engine', 'BEN') == 'native':
                        from backend.native_bidder import NativeBiddingEngine
                        bidder = NativeBiddingEngine(
                            system=getattr(prefs, 'native_bidding_system', 'Precision90M')
                        )
                except Exception:
                    bidder = self.engine
                response = bidder.get_bid(self.board, self.seat)
                self.bid_ready.emit(response)
            elif self.task == 'card':
                from backend.config import get_config_manager
                prefs = get_config_manager().config.preferences
                # NO-PEEK engine (default, strongest): alpha-mu single-dummy that
                # never sees hidden cards, emits + reads standard signals. It
                # handles the opening lead itself (native_lead).
                if getattr(prefs, 'use_nopeek_play', True):
                    from backend import nopeek
                    from backend.engine import EngineResponse
                    # Pass an explain sink so nopeek records WHY it chose this
                    # card (technique + one-line reason) for the click-to-explain
                    # popup. This is the single real decision, not a rollout, so
                    # building the reason string costs nothing measurable.
                    explain = {}
                    card = nopeek.decide(self.board, self.seat, self.trick_cards,
                                         explain=explain)
                    self.card_ready.emit(EngineResponse(
                        action=card, who="no-peek",
                        reason=explain.get("reason", ""),
                        reason_tag=explain.get("tag", "")))
                    return
                # Check if this is the opening lead (first card of play phase)
                # Opening lead: no tricks completed yet and current trick is empty
                is_opening_lead = (
                    len(self.board.tricks) == 0 and
                    (not self.board.current_trick or len(self.board.current_trick.cards) == 0)
                )
                if is_opening_lead:
                    response = self.engine.get_opening_lead(self.board)
                else:
                    # Route to the selected play engine
                    if prefs.use_monte_carlo_play:
                        response = self.engine.get_mc_card_play(
                            self.board, self.seat, self.trick_cards
                        )
                    elif prefs.use_double_dummy_play:
                        response = self.engine.get_dd_card_play(
                            self.board, self.seat, self.trick_cards
                        )
                    else:
                        response = self.engine.get_card_play(
                            self.board, self.seat, self.trick_cards
                        )
                self.card_ready.emit(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class GameController:
    """
    Controls the game flow: deals, bidding, play.
    Manages interaction between human players and BEN.
    Supports human declarer control of both declarer and dummy hands.
    """

    def __init__(self, engine: BridgeEngine):
        self.engine = engine
        self.board: Optional[BoardState] = None
        self.players: Dict[Seat, Player] = {}
        self.current_phase = 'idle'  # idle, bidding, play, waiting_next, finished
        self.current_seat: Optional[Seat] = None
        self.declarer: Optional[Seat] = None
        self.dummy: Optional[Seat] = None
        self.opening_leader: Optional[Seat] = None
        self.human_controls_declarer = False  # True if human plays declarer side

        # Initialize default players
        for seat in Seat:
            self.players[seat] = Player(
                seat=seat,
                player_type=PlayerType.COMPUTER if seat != Seat.SOUTH else PlayerType.HUMAN
            )

    def new_deal(self, board_number: int = None) -> BoardState:
        """Generate a new random deal"""
        self.board = self.engine.random_deal(board_number)
        self.current_phase = 'bidding'
        self.current_seat = self.board.dealer
        self.declarer = None
        self.dummy = None
        self.opening_leader = None
        self.human_controls_declarer = False
        return self.board

    def load_deal(self, pbn: str, board_number: int = 1) -> BoardState:
        """Load a deal from PBN string"""
        self.board = BoardState.from_pbn_deal(pbn, board_number)
        self.current_phase = 'bidding'
        self.current_seat = self.board.dealer
        self.declarer = None
        self.dummy = None
        return self.board

    def is_human_turn(self) -> bool:
        """Check if it's a human player's turn"""
        if self.current_seat is None:
            return False

        # During play, check if human controls this seat
        if self.current_phase == 'play':
            result = self._human_controls_seat(self.current_seat)
            return result
        else:
            pass

        return self.players[self.current_seat].player_type == PlayerType.HUMAN

    def _human_controls_seat(self, seat: Seat) -> bool:
        """Check if human controls this seat (either as player or declarer controlling dummy)"""
        if self.players[seat].player_type == PlayerType.HUMAN:
            return True

        # If human is declarer/dummy, they control both hands
        if self.human_controls_declarer:
            result = seat in (self.declarer, self.dummy)
            return result

        return False

    def make_bid(self, bid: Bid) -> bool:
        """Process a bid"""
        if self.current_phase != 'bidding':
            return False

        self.board.auction.append(bid)

        # Check if bidding is complete
        if self.board.is_auction_complete():
            if self.board.is_passed_out():
                self.current_phase = 'finished'
            else:
                self._setup_play()
            return True

        # Advance to next bidder
        self.current_seat = self.current_seat.next()
        return True

    def set_contract_direct(self, contract: Contract):
        """Skip the auction entirely and start card play on the given
        contract. Used by the MiniBridge runtime — Q-Plus
        .bidbox-m-s ends with a chosen contract that the program
        then plays out without an actual auction.

        Mirrors what _setup_play does at the end of a real auction,
        but consumes the contract directly instead of deriving it
        from board.auction. We DO write the contract into
        board.auction as a single regular bid so the rest of the
        codebase (logging, replay, score-sheet) keeps working
        without a special "no auction" case.
        """
        self.board.contract = contract
        self.board.auction = [Bid(level=contract.level,
                                  suit=contract.suit,
                                  is_double=contract.doubled,
                                  is_redouble=contract.redoubled)]
        self.declarer = contract.declarer
        self.dummy = contract.declarer.partner()
        self.opening_leader = contract.declarer.next()
        self.current_seat = self.opening_leader
        self.current_phase = 'play'

        # A human who is declarer (or dummy) plays BOTH hands of the
        # declaring side — same rule _setup_play applies after a real
        # auction. Without this the engine would auto-play dummy.
        human_seat = next((s for s, p in self.players.items()
                           if p.player_type == PlayerType.HUMAN), None)
        if human_seat is not None:
            self.human_controls_declarer = human_seat in (self.declarer,
                                                          self.dummy)

    def _setup_play(self):
        """Setup for card play after bidding"""
        auction = self.board.auction

        # Find contract
        level = 0
        suit = None
        declarer = None
        doubled = False
        redoubled = False

        for i, bid in enumerate(auction):
            if not bid.is_pass and not bid.is_double and not bid.is_redouble:
                level = bid.level
                suit = bid.suit
                # A new suit/level call clears any prior X/XX — doubles only
                # apply to the contract that becomes final, not to bids that
                # were later overcalled.
                doubled = False
                redoubled = False
                bidder_seat = Seat((self.board.dealer.value + i) % 4)

                # Find first bid of this suit by this side
                side_ns = bidder_seat.is_ns()
                for j, b in enumerate(auction[:i+1]):
                    if not b.is_pass and not b.is_double and not b.is_redouble:
                        if b.suit == suit:
                            b_seat = Seat((self.board.dealer.value + j) % 4)
                            if b_seat.is_ns() == side_ns:
                                declarer = b_seat
                                break
            elif bid.is_double:
                doubled = True
                redoubled = False
            elif bid.is_redouble:
                redoubled = True

        self.board.contract = Contract(
            level=level,
            suit=suit,
            doubled=doubled,
            redoubled=redoubled,
            declarer=declarer
        )

        self.declarer = declarer
        self.dummy = declarer.partner()
        self.opening_leader = declarer.next()
        # DIAGNOSTIC: capture board number, auction, and computed
        # declarer/opening_leader so we can see why host vs guest disagree.
        try:
            auc = " ".join(b.symbol() for b in auction)
            print(f"[_setup_play] board={self.board.board_number} "
                  f"dealer={self.board.dealer.to_char()} "
                  f"auction=[{auc}] "
                  f"contract={self.board.contract.to_str()} "
                  f"declarer={declarer.to_char()} "
                  f"opening_leader={self.opening_leader.to_char()}",
                  flush=True)
        except Exception:
            pass

        # Check if human controls the declarer side
        human_seat = None
        for seat, player in self.players.items():
            if player.player_type == PlayerType.HUMAN:
                human_seat = seat
                break

        if human_seat is not None:
            # Human controls declarer if they are declarer OR dummy
            self.human_controls_declarer = (human_seat == self.declarer or human_seat == self.dummy)
        else:
            pass

        self.current_phase = 'play'
        self.current_seat = self.opening_leader

    def play_card(self, card: Card) -> bool:
        """Process a card play"""
        if self.current_phase != 'play':
            return False

        # Remove card from hand
        if self.current_seat in self.board.hands:
            self.board.hands[self.current_seat].remove_card(card)

        # Add to current trick
        if not self.board.current_trick:
            self.board.current_trick = Trick(leader=self.current_seat)

        trump = self.board.contract.suit if self.board.contract.suit != Suit.NOTRUMP else None
        self.board.current_trick.add_card(card, trump)

        # Check if trick is complete
        if self.board.current_trick.is_complete():
            winner = self.board.current_trick.winner
            self.board.tricks.append(self.board.current_trick)

            # Update tricks won
            if winner.is_ns() == self.board.contract.declarer.is_ns():
                self.board.declarer_tricks += 1
            else:
                self.board.defense_tricks += 1

            # Check if play is complete
            if len(self.board.tricks) == 13:
                self.current_phase = 'finished'
                self._review_human_signals()
                return True

            # Enter waiting state for "Next Card" button
            self.current_phase = 'waiting_next'
            self.current_seat = winner
            return True
        else:
            self.current_seat = self.current_seat.next()

        return True

    def _review_human_signals(self):
        """At hand-end, review each HUMAN defender's signals (biq reads them to
        sharpen its defence). signal_trust escalates complain/warn/auto-disable
        if the human mis-signals; messages + the on-screen flag flow via the
        on_message callback the main window registered at reset()."""
        try:
            from backend import signal_read, signal_trust
            c = self.board.contract
            if c is None:
                return
            decl = c.declarer
            trump = c.suit if c.suit != Suit.NOTRUMP else None
            humans = [s for s, p in self.players.items()
                      if p.player_type == PlayerType.HUMAN
                      and s.is_ns() != decl.is_ns()]   # human DEFENDERS only
            for h in humans:
                recs = signal_read.read(self.board, h.partner(), decl, trump)
                signal_trust.review_hand(recs, set())
        except Exception:
            pass

    def advance_to_next_trick(self):
        """Called when user clicks Next Card after a trick completes"""
        if self.current_phase == 'waiting_next':
            self.board.current_trick = None
            self.current_phase = 'play'

    def get_lead_suit(self) -> Optional[Suit]:
        """Get the suit led in the current trick"""
        if self.board.current_trick and self.board.current_trick.cards:
            return self.board.current_trick.cards[0].suit
        return None

    def get_trick_winner(self) -> Optional[Seat]:
        """Get the winner of the last completed trick"""
        if self.board.tricks:
            return self.board.tricks[-1].winner
        return None


class BiddingFlowchartDialog(QDialog):
    """View menu → Bidding flowchart… (Ctrl+Shift+I).

    Renders a decision-tree flowchart customised to the current
    hand + auction position, with the path your hand actually
    took highlighted in green. Tree content from
    backend.bidding_flowchart; PNG produced by piping plantuml
    text through the system `plantuml` binary.
    """

    def __init__(self, parent, board, seat, system):
        super().__init__(parent)
        self.setWindowTitle("Bidding flowchart")
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        # Header
        header = QLabel(
            f"<b>Seat:</b> {seat.name} &nbsp;&nbsp;&nbsp; "
            f"<b>System:</b> "
            f"{getattr(system, 'name', '—') if system else '—'}")
        header.setStyleSheet("padding: 4px;")
        layout.addWidget(header)
        # Render the flowchart.
        from backend import bidding_flowchart
        import subprocess
        try:
            puml, commentary = bidding_flowchart.flowchart_for(
                board, seat, system)
        except Exception as ex:
            puml, commentary = "", f"Couldn't build flowchart: {ex!r}"
        png_bytes = b""
        if puml:
            try:
                out = subprocess.run(
                    ["plantuml", "-pipe", "-tpng"],
                    input=puml.encode("utf-8"),
                    capture_output=True, timeout=15)
                png_bytes = out.stdout if out.returncode == 0 else b""
            except Exception as ex:
                commentary = (commentary + "\n\n(plantuml render "
                              f"failed: {ex!r})")
        # Image area — scrollable QLabel.
        from PyQt6.QtWidgets import QScrollArea
        from PyQt6.QtGui import QPixmap
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image_label = QLabel()
        if png_bytes:
            pix = QPixmap()
            pix.loadFromData(png_bytes)
            image_label.setPixmap(pix)
        else:
            image_label.setText(
                "(no flowchart available for this auction position)")
            image_label.setStyleSheet("padding: 24px; color: #888;")
        scroll.setWidget(image_label)
        layout.addWidget(scroll, stretch=1)
        # Commentary text below.
        if commentary:
            from PyQt6.QtWidgets import QPlainTextEdit
            com = QPlainTextEdit(commentary)
            com.setReadOnly(True)
            com.setMaximumHeight(120)
            layout.addWidget(com)
        # Close button.
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bot = QHBoxLayout()
        bot.addStretch()
        bot.addWidget(close_btn)
        layout.addLayout(bot)


class AuctionInterpretationDialog(QDialog):
    """View menu → Interpret current auction.

    Walks through the auction on the current board bid-by-bid,
    showing for each call:
      • Bidder (N/E/S/W)
      • The bid itself (1NT, 2♣, X, …)
      • Natural-language meaning — the inference rules that fired
        for this specific bid, joined with semicolons.

    Powered by backend.auction_inference.explain_each_bid, which
    replays the auction and captures per-bid reason deltas.
    """

    def __init__(self, parent, board, system):
        super().__init__(parent)
        self.setWindowTitle("Auction interpretation")
        self.resize(960, 560)
        # Force a light background and dark text so the dialog
        # doesn't inherit the parent's dark-blue table-felt colour
        # — black text on dark blue was unreadable.
        self.setStyleSheet("""
            QDialog { background-color: #fafafa; color: #111; }
            QLabel { color: #111; }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f0f4f8;
                color: #111;
                font-size: 14px;
                gridline-color: #c0c0c0;
            }
            QHeaderView::section {
                background-color: #d0d8e0;
                color: #111;
                font-weight: bold;
                font-size: 14px;
                padding: 4px;
                border: 1px solid #a0a0a0;
            }
            QPushButton {
                background-color: #e0e0e0;
                color: #111;
                padding: 6px 14px;
                font-size: 14px;
            }
        """)
        layout = QVBoxLayout(self)
        # Header: dealer, vulnerability, system pair.
        from backend.models import Seat as _Seat, Suit as _Suit
        dealer_name = {
            _Seat.NORTH: "North", _Seat.EAST: "East",
            _Seat.SOUTH: "South", _Seat.WEST: "West",
        }.get(board.dealer, str(board.dealer))
        vul_name = str(board.vulnerability).replace(
            "Vulnerability.", "")
        system_name = getattr(system, "name", "—") if system else "—"
        header = QLabel(
            f"<b>Dealer:</b> {dealer_name} &nbsp;&nbsp; "
            f"<b>Vul:</b> {vul_name} &nbsp;&nbsp; "
            f"<b>System:</b> {system_name}")
        header.setStyleSheet(
            "padding: 6px; font-size: 15px;")
        layout.addWidget(header)
        # Table of per-bid explanations.
        from PyQt6.QtWidgets import (
            QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(
            ["Bidder", "Bid", "Meaning"])
        # Larger row height for readability.
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, stretch=1)
        # Populate.
        try:
            from backend import auction_inference
            rows = auction_inference.explain_each_bid(board, system)
        except Exception:
            rows = []
        self.table.setRowCount(len(rows))
        for r, step in enumerate(rows):
            seat_c = step["bidder"].name[0]
            self.table.setItem(r, 0, QTableWidgetItem(seat_c))
            self.table.setItem(r, 1, QTableWidgetItem(
                self._bid_str(step["bid"])))
            meaning = "; ".join(step["reasons"])
            if not meaning:
                # Generic fallback so the row isn't blank.
                if step["bid"].is_pass:
                    meaning = "Pass"
                elif step["bid"].is_double:
                    meaning = "Double"
                elif step["bid"].is_redouble:
                    meaning = "Redouble"
                else:
                    meaning = "(no specific rule fired — bid stands "
                    "on its natural meaning)"
            item = QTableWidgetItem(meaning)
            item.setToolTip(meaning)
            self.table.setItem(r, 2, item)
        self.table.resizeRowsToContents()
        # Close button.
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bot = QHBoxLayout()
        bot.addStretch()
        bot.addWidget(close_btn)
        layout.addLayout(bot)

    @staticmethod
    def _bid_str(b):
        """One-token rendering of a Bid for the table."""
        from backend.models import Suit
        if b.is_pass:
            return "Pass"
        if b.is_double:
            return "Dbl"
        if b.is_redouble:
            return "Rdbl"
        # NB: `if b.suit` short-circuits on Suit.SPADES (value 0)
        # because IntEnum-0 is falsy. Use explicit None check.
        if b.suit is None:
            suit_c = "?"
        elif b.suit == Suit.NOTRUMP:
            suit_c = "NT"
        else:
            suit_c = b.suit.to_char()
        return f"{b.level}{suit_c}"


class ToolbarButton(QPushButton):
    """Styled toolbar button"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.setMinimumWidth(85)
        self.setMinimumHeight(30)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['button_bg']};
                color: {COLORS['button_text']};
                border: 1px solid #2f4256;
                border-radius: 4px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: #34465c;
                border: 1px solid {COLORS['gold']};
            }}
            QPushButton:pressed {{
                background-color: #22303f;
            }}
            QPushButton:disabled {{
                background-color: #1a2330;
                color: #5a6675;
            }}
        """)


class MainWindow(QMainWindow):
    """
    Main application window for BridgeIQ.
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("BridgeIQ")
        self.setMinimumSize(1200, 920)

        # Set window background and ensure menus have proper contrast. The big
        # content area gets a warm pokerIQ glow via #centralArea. NB the bare
        # `background-color` cascades into child message boxes, so those are
        # styled with their OWN readable stylesheet at the call site
        # (MESSAGEBOX_STYLESHEET) — a widget's own sheet beats the inherited bg,
        # which a rule here cannot reliably do for the box's sub-labels.
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {COLORS['background']}; }}
            QWidget#centralArea {{
                background: qradialgradient(cx:0.5, cy:0.30, radius:1.15,
                    fx:0.5, fy:0.30, stop:0 #18262f, stop:0.8 {COLORS['background']});
            }}
            QMenuBar {{ background-color: {COLORS['background']}; color: #eef3f7; }}
            QMenuBar::item {{ background: transparent; padding: 4px 11px; }}
            QMenuBar::item:selected {{ background-color: #34465c; }}
            QMenu {{
                background-color: #f0f0f0;
                color: #000000;
                border: 1px solid #606060;
                padding: 4px;
            }}
            QMenu::item {{
                background-color: #f0f0f0;
                padding: 6px 30px 6px 20px;
                color: #000000;
            }}
            QMenu::item:selected {{
                background-color: #3070b0;
                color: #ffffff;
            }}
            QMenu::item:disabled {{
                color: #808080;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: #a0a0a0;
                margin: 4px 10px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #f0f0f0;
                color: #000000;
                selection-background-color: #3070b0;
                selection-color: #ffffff;
            }}
        """)

        # Initialize engine and game controller
        self.engine = BridgeEngine(verbose=False)
        self.controller = GameController(self.engine)

        # Engine worker for async operations
        self.engine_worker = None

        # Game logger for saving hands in BDL format
        self.game_logger = GameLogger()
        self.original_hands: Optional[Dict[Seat, Hand]] = None  # Store hands before play
        self.card_history: List[tuple] = []  # List of (seat, card) for undo during play

        # Scoring table for session results
        self.scoring_table = ScoringTable(ScoringType.TEAMS)

        # Teams match support
        self.teams_match: Optional[BenTeamsMatch] = None
        self.match_controller: Optional[TeamsMatchController] = None

        # Parallel closed room worker (started at deal time, joined at hand end)
        self._pending_closed_room: Optional[dict] = None
        # Board number whose teams-match closed room has been kicked off in
        # parallel (Q-Plus semantics: the closed room plays AT THE SAME TIME as
        # the human's open room). The end-of-hand handler joins it instead of
        # starting a fresh, slow run. None = not yet started for the current board.
        self._closed_room_async_started: Optional[int] = None
        # Snapshot of the just-finished hand awaiting Claude analysis. Set
        # by _show_result, consumed by _maybe_run_pending_claude after a
        # Q-Plus ingest or before the next deal.
        self._claude_pending: Optional[dict] = None
        # Cache of Claude-annotated hand logs, keyed by (board_number,
        # pavlicek_id). _show_claude_hand_analysis populates it on a
        # successful run; subsequent calls for the same board (e.g. the
        # user reopening "Hand Log" after returning from the summary
        # view) replay the cached annotated BDL instead of paying for
        # another Claude round-trip.
        self._claude_hand_logs: Dict[tuple, dict] = {}

        # The seat the local user actually wants to play. This is the
        # canonical record across network connect/disconnect transitions:
        # after the host disconnects we keep the guest at this seat instead
        # of resetting to South. Only set_local_seat() and the user
        # picking a different seat in the lobby change it.
        self._user_seat: Seat = Seat.SOUTH

        # One-Player Mode bookkeeping. ``_one_player_known_seats`` is
        # the set of seats whose hand the engine is allowed to see —
        # initially just the user's; dummy is added once revealed.
        # ``_one_player_active`` is the latched flag for the current
        # deal so the runtime keeps using the manual card-entry path
        # even after the user toggles play_mode in preferences mid-deal.
        self._one_player_active: bool = False
        self._one_player_known_seats: set = set()

        # Network game controller for LAN play
        self.network_controller = NetworkGameController(self)
        self._setup_network_signals()

        # Configuration manager
        self.config_manager = get_config_manager()

        # Initialize suit color mode from preferences
        from .styles import set_suit_color_mode, SuitColorMode
        if self.config_manager.config.preferences.legacy_colors:
            set_suit_color_mode(SuitColorMode.TRADITIONAL)
        else:
            set_suit_color_mode(SuitColorMode.FOUR_COLOR)

        # Card size for this screen: full size from 1920x1080 up, smaller on
        # a laptop so the 13-card fan and the table fit.
        try:
            from . import table_view as _tv
            _scr = QApplication.primaryScreen().availableGeometry()
            _tv.set_card_scale(min(_scr.width() / 1920, (_scr.height() + 40) / 1080))
        except Exception:
            pass

        # Setup UI
        self._setup_ui()
        self._setup_menus()
        self._setup_bottom_toolbar()
        self._setup_statusbar()
        self._setup_bid_info_window()
        # Embed the bid-info panel at the upper-left of the bidding screen and
        # apply the AI/Network/bid-info visibility toggles (all widgets now
        # exist: table view, menu actions, toolbar buttons).
        self._embed_bid_info_panel()
        self._apply_bid_info_visibility()
        self._apply_qplus_visibility()

        # Connect signals
        self._connect_signals()

        # Resume a match saved with File / Save Match + Exit (Q-Plus parity).
        self._maybe_restore_saved_match()

        # Initialize engine in background
        QTimer.singleShot(100, self._initialize_engine)

    def _setup_ui(self):
        """Setup the main UI layout"""
        central = QWidget()
        central.setObjectName("centralArea")   # warm pokerIQ backdrop glow
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Main content area — transparent so the #centralArea gradient shows
        # through (the bg is no longer cascaded universally, so set it here).
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(5, 5, 5, 5)

        # Left side: Table view, with an instrumented (teaching) view as an
        # alternate page selected by the "Analysis view" toolbar button.
        self.table_view = TableView()
        from .teaching_view import TeachingView
        self.teaching_view = TeachingView()
        self.table_stack = QStackedWidget()
        self.table_stack.addWidget(self.table_view)      # page 0 = normal
        # The instrumented view needs ~920 px; in a scroll area it no longer
        # forces that minimum height on the window (a stacked widget takes
        # the largest page minimum, which pushed the window past the screen).
        from PyQt6.QtWidgets import QScrollArea as _QScrollArea, QFrame as _QFrame
        self._teaching_scroll = _QScrollArea()
        self._teaching_scroll.setWidgetResizable(True)
        self._teaching_scroll.setFrameShape(_QFrame.Shape.NoFrame)
        self._teaching_scroll.setWidget(self.teaching_view)
        self.table_stack.addWidget(self._teaching_scroll)  # page 1 = instrumented
        content_layout.addWidget(self.table_stack, stretch=3)
        # While the instrumented view is up, refresh it on a light timer so
        # it tracks play without threading a call through every play site.
        self._teaching_timer = QTimer(self)
        self._teaching_timer.setInterval(500)
        self._teaching_timer.timeout.connect(self._refresh_teaching_view)
        # App-wide key filter so keyboard card-play works from ANY view —
        # including the instrumented view, whose combo boxes would otherwise
        # eat the suit/rank letters before they reach keyPressEvent. The
        # filter is inert outside the human's play turn (see _handle_play_key).
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        # Right side: Bidding box and analysis (hidden during card play)
        self.right_panel = QWidget()
        self.right_panel.setStyleSheet("background: transparent;")
        self.right_panel.setMaximumWidth(280)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)

        # The bidding box is an overlay on the table, right beside the felt
        # (and the auction on it); placed by _place_bidding_overlays.
        self.bidding_box = BiddingBox(self.table_view)
        self.bidding_box.setObjectName("biddingBoxPanel")
        self.bidding_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bidding_box.setStyleSheet(
            "#biddingBoxPanel { background-color: #f4f6f8;"
            " border: 1px solid #d9b25b; border-radius: 6px; }"
            " #biddingBoxPanel QLabel { color: #1a1f29; }")

        # Analysis info area
        self.analysis_label = QLabel("")
        self.analysis_label.setWordWrap(True)
        self.analysis_label.setFont(QFont("Monospace", 14))
        self.analysis_label.setStyleSheet(f"""
            QLabel {{
                background-color: #121922;
                border: 1px solid #243447;
                padding: 8px;
                font-family: monospace;
                font-size: 22px;
                color: {COLORS['text_white']};
            }}
        """)
        self.analysis_label.setMinimumHeight(100)
        right_layout.addWidget(self.analysis_label)

        # Set initial visibility based on preference
        self.analysis_label.setVisible(self.config_manager.config.preferences.show_ben_bid_analysis)

        # Book note — the deal file's Commentary (the textbook's Lead /
        # Correct play / Wrong play notes for a practice hand). Shown on
        # the right during play of a deal that carries commentary; hidden
        # otherwise. Read-only and scrollable so long notes fit.
        self.book_note = QTextEdit()
        self.book_note.setReadOnly(True)
        self.book_note.setVisible(False)
        self.book_note.setMaximumHeight(360)
        # Keep the panel a readable width (it shares the 280-px right
        # column with the bidding box); without a floor it collapses to a
        # few pixels during play and wraps one letter per line.
        self.book_note.setMinimumWidth(252)
        self.book_note.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1b2330;
                border: 1px solid #c8a24a;
                border-radius: 4px;
                padding: 8px;
                font-family: 'DejaVu Sans', Arial, sans-serif;
                font-size: 14px;
                color: {COLORS['text_white']};
            }}
        """)
        right_layout.addWidget(self.book_note)

        right_layout.addStretch()
        content_layout.addWidget(self.right_panel)

        # Teaching-mode inference boxes live INSIDE the TableView, in
        # the W/E columns directly under the W:BEN / E:BEN labels.
        # We only show E and W (the hidden hands) — N and S are visible
        # to the player (own hand + declarer's dummy), so an inference
        # box there would just duplicate what's already on screen.
        from backend.models import Seat as _Seat
        self.inference_boxes = {
            _Seat.WEST: self.table_view.west_inference_box,
            _Seat.EAST: self.table_view.east_inference_box,
        }

        main_layout.addWidget(content_widget, stretch=1)

        # Q-Plus-style status strip: deal number + source on the left, both
        # pairs' bidding systems on the right. (Per-seat network/Computer
        # icons live on the table itself, next to each seat label.)
        self._setup_status_strip(main_layout)

        # Bottom toolbar container
        self.toolbar_container = QFrame()
        self.toolbar_container.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['panel_teal']};
                border-top: 2px solid #243447;
            }}
        """)
        self.toolbar_container.setFixedHeight(45)

        main_layout.addWidget(self.toolbar_container)

    def _setup_status_strip(self, main_layout):
        """A thin Q-Plus-style status strip under the table: deal number +
        source on the left, both pairs' bidding systems on the right. The
        per-seat network/Computer icons live on the table itself."""
        self.status_strip = QFrame()
        self.status_strip.setStyleSheet(
            "QFrame { background-color: #0d141c; "
            "border-top: 1px solid #243447; } QLabel { color: #9aa7b4; }")
        self.status_strip.setFixedHeight(24)
        row = QHBoxLayout(self.status_strip)
        row.setContentsMargins(8, 0, 8, 0)
        self.deal_info_label = QLabel("Deal: —")
        self.deal_info_label.setFont(QFont("Arial", 10))
        self.systems_strip_label = QLabel("")
        self.systems_strip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        row.addWidget(self.deal_info_label)
        row.addStretch(1)
        row.addWidget(self.systems_strip_label)
        main_layout.addWidget(self.status_strip)
        self._refresh_status_strip()

    def _deal_source_str(self):
        """(board_number, pavlicek-id-or-None) for the current deal. The
        Pavlicek id (biq's analog of Q-Plus's deal source) is computed once
        from the full 52-card deal and cached on the board, so it survives
        the hands depleting during play."""
        board = getattr(getattr(self, 'controller', None), 'board', None)
        if board is None:
            return None, None
        num = getattr(board, 'board_number', None)
        src = getattr(board, '_pavlicek_src', None)
        if src is None:
            try:
                hands = board.hands
                if hands and sum(len(hands[s].cards) for s in Seat) == 52:
                    src = format_deal_base72(deal_to_number(hands))
                    board._pavlicek_src = src
            except Exception:
                src = None
        return num, src

    def _refresh_status_strip(self):
        """Repaint the deal/source + both-pair-systems strip. Cheap; safe to
        call from deal load, network (re)config, and system switches."""
        if not hasattr(self, 'deal_info_label'):
            return
        num, src = self._deal_source_str()
        net = (getattr(self, 'network_controller', None) is not None
               and getattr(self.network_controller, 'is_active', False))
        origin = "network" if net else "local"
        parts = []
        if num is not None:
            parts.append(f"Deal #{num}")
        parts.append(f"src {origin}:{src}" if src else f"src {origin}")
        self.deal_info_label.setText("   ·   ".join(parts))
        try:
            ns = self._active_bidding_system('NS')
            ew = self._active_bidding_system('EW')
            self.systems_strip_label.setText(
                f"NS: {ns.name}      EW: {ew.name}")
        except Exception:
            self.systems_strip_label.setText("")

    def _push_seat_types_to_table(self):
        """Mirror the controller's per-seat player types onto the table so
        each seat label shows the right icon (👤/🖥/🖧)."""
        try:
            mapping = {s: self.controller.players[s].player_type for s in Seat}
            self.table_view.set_seat_types(mapping)
        except Exception:
            pass

    def _setup_menus(self):
        """Setup the menu bar"""
        menubar = self.menuBar()
        # Use more specific styling with higher contrast
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #d0d0d0;
                color: #000000;
                border-bottom: 1px solid #808080;
                padding: 2px;
                font-size: 14px;
            }
            QMenuBar::item {
                background-color: transparent;
                padding: 4px 8px;
                color: #000000;
            }
            QMenuBar::item:selected {
                background-color: #4080c0;
                color: #ffffff;
            }
            QMenuBar::item:pressed {
                background-color: #3060a0;
                color: #ffffff;
            }
        """)

        # Define menu style to apply to each menu
        self._menu_style = """
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
        """

        # File menu
        file_menu = menubar.addMenu("&File")
        file_menu.setStyleSheet(self._menu_style)

        new_action = QAction("&New Deal", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._on_menu_new_deal)
        file_menu.addAction(new_action)

        open_action = QAction("&Open Deal File...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._on_open_file)
        file_menu.addAction(open_action)

        save_action = QAction("&Save Deals...", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save_file)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        export_action = QAction("&Export HTML...", self)
        export_action.triggered.connect(self._on_export_html)
        file_menu.addAction(export_action)

        print_action = QAction("&Print Current Hand...", self)
        print_action.setShortcut(QKeySequence("Ctrl+P"))
        print_action.triggered.connect(self._on_print_current_hand)
        file_menu.addAction(print_action)

        file_menu.addSeparator()

        restore_action = QAction("&Restore Score Table...", self)
        restore_action.setToolTip(
            "Load a saved rubber scorecard (or other score table) "
            "to continue a match where you left off."
        )
        restore_action.triggered.connect(self._on_restore_score_table)
        file_menu.addAction(restore_action)

        file_menu.addSeparator()

        # Q-Plus: Save Match + Exit resumes the match next start; plain Exit
        # starts fresh.
        save_exit_action = QAction("Save &Match + Exit", self)
        save_exit_action.setToolTip(
            "Save the current scoring table so the next start resumes it, "
            "then close (Q-Plus 'Save Match + Exit').")
        save_exit_action.triggered.connect(self._on_save_match_and_exit)
        file_menu.addAction(save_exit_action)

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Configuration menu
        config_menu = menubar.addMenu("&Configuration")
        config_menu.setStyleSheet(self._menu_style)

        players_action = QAction("&Players...", self)
        players_action.triggered.connect(self._on_configure_players)
        config_menu.addAction(players_action)

        systems_action = QAction("Bidding &Systems...", self)
        systems_action.triggered.connect(self._on_configure_systems)
        config_menu.addAction(systems_action)

        config_menu.addSeparator()

        lead_ns_action = QAction("&Lead/Signals N/S...", self)
        lead_ns_action.triggered.connect(lambda: self._on_lead_signal("NS"))
        config_menu.addAction(lead_ns_action)

        lead_ew_action = QAction("Lead/Signals &E/W...", self)
        lead_ew_action.triggered.connect(lambda: self._on_lead_signal("EW"))
        config_menu.addAction(lead_ew_action)

        config_menu.addSeparator()

        strength_action = QAction("Playing &Strength...", self)
        strength_action.triggered.connect(self._on_strength)
        config_menu.addAction(strength_action)

        config_menu.addSeparator()

        check_action = QAction("&Check Configuration!", self)
        check_action.triggered.connect(self._on_config_check)
        config_menu.addAction(check_action)

        config_menu.addSeparator()

        # Q-Plus: save / retrieve named configuration sets (a folder holding
        # the B-*.CFG files).
        save_cfg_action = QAction("Sa&ve Configuration...", self)
        save_cfg_action.triggered.connect(self._on_save_configuration)
        config_menu.addAction(save_cfg_action)

        retrieve_cfg_action = QAction("&Retrieve Configuration...", self)
        retrieve_cfg_action.triggered.connect(self._on_retrieve_configuration)
        config_menu.addAction(retrieve_cfg_action)

        config_menu.addSeparator()

        prefs_action = QAction("&Preferences...", self)
        prefs_action.triggered.connect(self._on_preferences)
        config_menu.addAction(prefs_action)

        # Deal menu
        deal_menu = menubar.addMenu("&Deal")
        deal_menu.setStyleSheet(self._menu_style)

        match_control_action = QAction("&Match Control...", self)
        match_control_action.triggered.connect(self._on_match_control)
        deal_menu.addAction(match_control_action)

        repeat_action = QAction("&Repeat Deal", self)
        repeat_action.setShortcut(Qt.Key.Key_F5)
        repeat_action.triggered.connect(self._on_repeat_deal)
        deal_menu.addAction(repeat_action)

        variations_action = QAction("&Variations…", self)
        variations_action.triggered.connect(self._on_deal_variations)
        deal_menu.addAction(variations_action)

        # Q-Plus lists Next Deal / Previous Deal under the Deal menu (biq also
        # has Next on the toolbar — keep both, like Q-Plus).
        next_deal_action = QAction("&Next Deal", self)
        next_deal_action.triggered.connect(self._on_next_deal)
        deal_menu.addAction(next_deal_action)

        prev_deal_action = QAction("&Previous Deal", self)
        prev_deal_action.setShortcut(Qt.Key.Key_F4)
        prev_deal_action.triggered.connect(self._on_previous_deal)
        deal_menu.addAction(prev_deal_action)

        # Computer Multiplay belongs under Deal in Q-Plus.
        multiplay_action = QAction("Computer &Multiplay...", self)
        multiplay_action.triggered.connect(self._on_multiplay)
        deal_menu.addAction(multiplay_action)

        deal_menu.addSeparator()

        filter_action = QAction("Deal &Filter...", self)
        filter_action.triggered.connect(self._on_deal_filter)
        deal_menu.addAction(filter_action)

        deal_menu.addSeparator()

        load_pavlicek_action = QAction("Load &Pavlicek Deal...", self)
        load_pavlicek_action.triggered.connect(self._on_load_pavlicek)
        deal_menu.addAction(load_pavlicek_action)

        convert_action = QAction("&Convert hand code / PBN...", self)
        convert_action.triggered.connect(self._on_convert_deal)
        deal_menu.addAction(convert_action)

        deal_menu.addSeparator()

        dealer_vul_action = QAction("&Dealer and Vulnerability...", self)
        dealer_vul_action.triggered.connect(self._on_set_dealer_vul)
        deal_menu.addAction(dealer_vul_action)

        board_num_action = QAction("Set &Board Number...", self)
        board_num_action.triggered.connect(self._on_set_board_number)
        deal_menu.addAction(board_num_action)

        # Own Deals menu
        own_deals_menu = menubar.addMenu("&Own Deals")
        own_deals_menu.setStyleSheet(self._menu_style)

        enter_deal_action = QAction("&Enter Deal...", self)
        enter_deal_action.triggered.connect(self._on_enter_deal)
        own_deals_menu.addAction(enter_deal_action)

        save_deals_action = QAction("&Save Entered Deals...", self)
        save_deals_action.triggered.connect(self._on_save_entered_deals)
        own_deals_menu.addAction(save_deals_action)

        own_deals_menu.addSeparator()

        use_own_action = QAction("&Use Own Deals File...", self)
        use_own_action.triggered.connect(self._on_use_own_deals)
        own_deals_menu.addAction(use_own_action)

        # View menu
        view_menu = menubar.addMenu("&View")
        view_menu.setStyleSheet(self._menu_style)

        # Q-Plus calls this "Open all hands" — match the wording.
        show_all_action = QAction("&Open All Hands", self)
        show_all_action.setShortcut(Qt.Key.Key_F2)
        show_all_action.triggered.connect(self._on_show_all_hands)
        view_menu.addAction(show_all_action)

        self.bid_info_action = QAction("&Bidding Information", self)
        self.bid_info_action.setShortcut(Qt.Key.Key_F3)
        self.bid_info_action.setCheckable(True)
        self.bid_info_action.setChecked(True)
        self.bid_info_action.triggered.connect(self._on_toggle_bid_info)
        view_menu.addAction(self.bid_info_action)

        # Teaching-mode toggle (off by default, like wbridge5's
        # "Info" button): per-seat inference boxes during cardplay
        # showing HCP + suit-length ranges that tighten as cards
        # are played. Tooltips on each box reveal the reasoning.
        self.teaching_panel_action = QAction(
            "Show &auction inferences (teaching)", self)
        self.teaching_panel_action.setCheckable(True)
        self.teaching_panel_action.setChecked(False)
        self.teaching_panel_action.triggered.connect(
            self._on_toggle_teaching_panel)
        view_menu.addAction(self.teaching_panel_action)

        # Auto-claim toggle (off by default — claims are usually
        # cosmetic and a learner may want to play out the tricks
        # to see why). When on, after each completed trick DDS is
        # asked whether the winning side will take every remaining
        # trick; if so, a "make the rest" dialog offers to skip
        # the cardplay to the end of the hand.
        self.auto_claim_action = QAction(
            "Auto-&claim when DDS proves the rest", self)
        self.auto_claim_action.setCheckable(True)
        self.auto_claim_action.setChecked(False)
        view_menu.addAction(self.auto_claim_action)

        # Auction interpretation dialog: walks through the current
        # auction bid-by-bid with a natural-language explanation
        # of each call (Bridge Baron 12-style teaching aid).
        self.auction_interp_action = QAction(
            "&Interpret current auction…", self)
        self.auction_interp_action.setShortcut("Ctrl+I")
        self.auction_interp_action.triggered.connect(
            self._on_show_auction_interpretation)
        view_menu.addAction(self.auction_interp_action)

        # Bidding flowchart dialog: plantuml-rendered decision tree
        # for the current auction position, with the actual path
        # taken by my hand highlighted.
        self.flowchart_action = QAction(
            "Show bidding &flowchart…", self)
        self.flowchart_action.setShortcut("Ctrl+Shift+I")
        self.flowchart_action.triggered.connect(
            self._on_show_bidding_flowchart)
        view_menu.addAction(self.flowchart_action)

        # Defensive carding: explain the meaning of the most
        # recently played defensive card under the configured
        # signaling conventions (attitude / count / suit-
        # preference / UDCA polarity). Q-Plus's "Display meaning
        # of signals and conventional treatments in defense" —
        # backed by backend.signal_explainer.
        self.signal_meaning_action = QAction(
            "Explain last &signal…", self)
        self.signal_meaning_action.setShortcut("Ctrl+Shift+S")
        self.signal_meaning_action.triggered.connect(
            self._on_explain_last_signal)
        view_menu.addAction(self.signal_meaning_action)

        view_menu.addSeparator()

        scores_action = QAction("&Score Table...", self)
        scores_action.triggered.connect(self._on_show_scores)
        view_menu.addAction(scores_action)

        teams_score_action = QAction("&Teams Match Score...", self)
        teams_score_action.triggered.connect(self._on_view_teams_score)
        view_menu.addAction(teams_score_action)

        rubber_action = QAction("&Rubber Scorecard...", self)
        rubber_action.setToolTip(
            "View the running rubber bridge scorecard "
            "(per Q-Plus .score-rubber)."
        )
        rubber_action.triggered.connect(self._on_view_rubber)
        view_menu.addAction(rubber_action)

        dd_action = QAction("&Double Dummy Analysis", self)
        dd_action.triggered.connect(self._on_dd_analysis)
        view_menu.addAction(dd_action)

        simulation_action = QAction("Bid &Simulation...", self)
        simulation_action.triggered.connect(self._on_simulation)
        view_menu.addAction(simulation_action)

        self.qplus_sim_action = QAction("Q-Plus Simulation &Pop-up...", self)
        self.qplus_sim_action.setToolTip(
            "Open the Q-Plus-style slam simulation pop-up "
            "(auto-opens on the user's turn when slam is in scope)."
        )
        self.qplus_sim_action.triggered.connect(self._on_qplus_simulation)
        view_menu.addAction(self.qplus_sim_action)

        auction_tricks_action = QAction("&Auction and Played Tricks...", self)
        auction_tricks_action.triggered.connect(self._on_view_auction_tricks)
        view_menu.addAction(auction_tricks_action)

        view_menu.addSeparator()

        # Hand Log — re-opens the most recent Claude annotated BDL for
        # the current/last finished hand. Cached, so reselecting it
        # doesn't pay for another Claude round-trip.
        hand_log_action = QAction("&Hand Log (Claude Analysis)", self)
        hand_log_action.setToolTip(
            "Reopen the Claude-annotated BDL log for the current hand. "
            "Cached after the first run so reselecting doesn't trigger "
            "another Claude call."
        )
        hand_log_action.triggered.connect(self._on_show_hand_log)
        view_menu.addAction(hand_log_action)

        edit_remark_action = QAction("&Edit Remark...", self)
        edit_remark_action.triggered.connect(self._on_edit_remark)
        view_menu.addAction(edit_remark_action)

        view_remark_action = QAction("View &Remark", self)
        view_remark_action.triggered.connect(self._on_view_remark)
        view_menu.addAction(view_remark_action)

        view_menu.addSeparator()

        show_log_action = QAction("Show &Current Log File", self)
        show_log_action.triggered.connect(self._on_show_current_log)
        view_menu.addAction(show_log_action)

        show_prev_logs_action = QAction("Show &Previous Log Files...", self)
        show_prev_logs_action.triggered.connect(self._on_show_previous_logs)
        view_menu.addAction(show_prev_logs_action)

        view_menu.addSeparator()

        imp_table_action = QAction("&IMP Conversion Table...", self)
        imp_table_action.triggered.connect(self._on_show_imp_table)
        view_menu.addAction(imp_table_action)

        prob_action = QAction("&Probabilities Table...", self)
        prob_action.triggered.connect(self._on_show_probabilities)
        view_menu.addAction(prob_action)

        scoring_ref_action = QAction("Scoring &Reference...", self)
        scoring_ref_action.triggered.connect(self._on_show_scoring_reference)
        view_menu.addAction(scoring_ref_action)

        # Extras menu
        extras_menu = menubar.addMenu("&Extras")
        extras_menu.setStyleSheet(self._menu_style)

        # Q-Plus mounts MiniBridge AND One-Player on the same dialog
        # (.one-player spec: "Here the program can be set to
        # MiniBridge mode and the one-player mode"). The menu label
        # now follows the spec title.
        self.minibridge_action = QAction(
            "&One Player and MiniBridge Mode...", self)
        self.minibridge_action.setCheckable(True)
        self.minibridge_action.triggered.connect(self._on_minibridge)
        extras_menu.addAction(self.minibridge_action)

        # Q-Plus: Check user actions — report weak bids/cards as you make them.
        # biq's equivalent is the live blunder check (a single toggle); surface
        # it here as a checkable item bound to the preference.
        self.check_actions_action = QAction("&Check User Actions", self)
        self.check_actions_action.setCheckable(True)
        try:
            self.check_actions_action.setChecked(bool(
                self.config_manager.config.preferences.blunder_check_enabled))
        except Exception:
            pass
        self.check_actions_action.toggled.connect(self._on_check_user_actions)
        extras_menu.addAction(self.check_actions_action)

        extras_menu.addSeparator()

        rubber_action = QAction("&Rubber Scoring...", self)
        rubber_action.triggered.connect(self._on_rubber_scoring)
        extras_menu.addAction(rubber_action)

        extras_menu.addSeparator()

        # Send the current deal (the one on the table right now) into
        # the GUI harness — same launch path as the end-of-hand
        # "Generate closed room" button, but accessible mid-game so
        # the user can spin up the harness whenever they want.
        self.send_to_harness_action = QAction(
            "Send Current Deal to GUI &Harness", self)
        self.send_to_harness_action.setToolTip(
            "Launch the GUI harness preloaded with the current deal's "
            "hands, dealer, and vulnerability so it can be played in "
            "Q-Plus for closed-room comparison.")
        self.send_to_harness_action.triggered.connect(
            self._on_send_current_to_harness)
        extras_menu.addAction(self.send_to_harness_action)

        # Q-Plus closed-room ingest. Greyed out when Q-Plus isn't
        # installed; guests can't ingest (the host pushes the run to them
        # via CLOSED_ROOM_INGESTED) so the action stays disabled for them
        # too. Updated dynamically by _refresh_qplus_ingest_action_state.
        self.ingest_qplus_action = QAction("&Ingest Q-Plus Closed Room...", self)
        self.ingest_qplus_action.triggered.connect(self._on_ingest_qplus_closed_room)
        extras_menu.addAction(self.ingest_qplus_action)
        self._refresh_qplus_ingest_action_state()

        # LIN-format closed-room database loader. Loads played-out hands
        # from a BBO vugraph LIN file and ingests them as closed-room
        # results so the score sheet can compute IMPs against them.
        self.ingest_lin_action = QAction("Load &Closed-Room Database (LIN)...", self)
        self.ingest_lin_action.setToolTip(
            "Load a BBO vugraph LIN file. Played-out deals are ingested as "
            "closed-room results; IMPs vs the human's open room are then "
            "shown in the score table.")
        self.ingest_lin_action.triggered.connect(self._on_load_lin_database)
        extras_menu.addAction(self.ingest_lin_action)

        extras_menu.addSeparator()
        # Recovery: force-quit every wine/Q-Plus process so a stale Q-NET
        # socket or a hung Q-Plus can't block the next closed-room run.
        self.kill_wine_action = QAction("&Kill all wine processes", self)
        self.kill_wine_action.setToolTip(
            "Force-quit Q-Plus and ALL wine processes (wineserver -k + pkill).\n"
            "Use when biq E/W can't attach to the Q-Plus network because a "
            "previous session left a stale Q-NET socket open.")
        self.kill_wine_action.triggered.connect(self._on_kill_all_wine)
        extras_menu.addAction(self.kill_wine_action)

        # Network menu
        network_menu = menubar.addMenu("&Network")
        network_menu.setStyleSheet(self._menu_style)

        start_server_action = QAction("&Start Server...", self)
        start_server_action.triggered.connect(self._on_start_server)
        network_menu.addAction(start_server_action)

        connect_server_action = QAction("&Connect to Server...", self)
        connect_server_action.triggered.connect(self._on_connect_server)
        network_menu.addAction(connect_server_action)

        network_menu.addSeparator()

        self.disconnect_action = QAction("&Disconnect", self)
        self.disconnect_action.triggered.connect(self._on_network_disconnect)
        self.disconnect_action.setEnabled(False)
        network_menu.addAction(self.disconnect_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")
        help_menu.setStyleSheet(self._menu_style)

        about_action = QAction("&About...", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

        help_action = QAction("&Help Contents", self)
        help_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        help_action.triggered.connect(self._on_help)
        help_menu.addAction(help_action)

        # Long-form analogy that lets a reader who knows programming
        # but not bridge translate one to the other — the names of
        # bidding systems, conventions and defensive carding
        # methods are gobbledygook on first contact, but the
        # programming-language analogy is a Rosetta Stone.
        rosetta_action = QAction(
            "&Bridge for Programmers (Rosetta Stone)…", self)
        rosetta_action.triggered.connect(
            self._on_programming_rosetta)
        help_menu.addAction(rosetta_action)

    def _setup_bottom_toolbar(self):
        """Setup the bottom toolbar with two modes:
        - Opening mode: First deal, Pair tourn., Closed Room, etc.
        - In-game mode: Next deal, Next card, Hint, etc.
        """
        layout = QHBoxLayout(self.toolbar_container)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        toolbar_button_style = """
            QPushButton {
                background-color: #2b3a4d;
                color: #eef3f7;
                border: 1px solid #2f4256;
                border-radius: 3px;
                padding: 5px 15px;
                font-size: 12px;
                min-width: 90px;
            }
            QPushButton:hover {
                background-color: #34465c;
                border: 1px solid #d9b25b;
            }
            QPushButton:pressed {
                background-color: #22303f;
            }
        """

        # Primary action buttons get pokerIQ's green "go" accent so the bottom
        # row isn't all flat slate.
        primary_button_style = """
            QPushButton {
                background-color: #2a7d4f;
                color: #06121f;
                border: 1px solid #3fb950;
                border-radius: 3px;
                padding: 5px 15px;
                font-size: 12px;
                font-weight: 600;
                min-width: 90px;
            }
            QPushButton:hover { background-color: #34995f; border: 1px solid #d9b25b; }
            QPushButton:pressed { background-color: #226340; }
        """

        # === Opening screen buttons ===
        self.opening_buttons = []

        self.first_deal_btn = QPushButton("First deal")
        self.first_deal_btn.setStyleSheet(primary_button_style)
        self.first_deal_btn.clicked.connect(self._on_first_deal)
        layout.addWidget(self.first_deal_btn)
        self.opening_buttons.append(self.first_deal_btn)

        self.pair_tourn_btn = QPushButton("Pair tourn.")
        self.pair_tourn_btn.setStyleSheet(toolbar_button_style)
        self.pair_tourn_btn.clicked.connect(self._on_pair_tournament)
        layout.addWidget(self.pair_tourn_btn)
        self.opening_buttons.append(self.pair_tourn_btn)

        self.team_tourn_btn = QPushButton("Team tourn.")
        self.team_tourn_btn.setStyleSheet(toolbar_button_style)
        self.team_tourn_btn.clicked.connect(self._on_team_tournament)
        layout.addWidget(self.team_tourn_btn)
        self.opening_buttons.append(self.team_tourn_btn)

        self.match_control_btn = QPushButton("Match control")
        self.match_control_btn.setStyleSheet(toolbar_button_style)
        self.match_control_btn.clicked.connect(self._on_match_control)
        layout.addWidget(self.match_control_btn)
        self.opening_buttons.append(self.match_control_btn)

        self.opening_help_btn = QPushButton("Help")
        self.opening_help_btn.setStyleSheet(toolbar_button_style)
        self.opening_help_btn.clicked.connect(self._on_help)
        layout.addWidget(self.opening_help_btn)
        self.opening_buttons.append(self.opening_help_btn)

        # Closed Room is a biq extra (not part of Q-Plus's First deal / Pair /
        # Team / Match control / Help row), so keep that Q-Plus order intact and
        # place Closed Room at the far right — never on the lower left. Shown
        # only when a Q-Plus build is configured (see _apply_qplus_visibility).
        self.closed_room_toolbar_btn = QPushButton("Closed Room")
        self.closed_room_toolbar_btn.setStyleSheet(toolbar_button_style)
        self.closed_room_toolbar_btn.clicked.connect(self._on_closed_room_start)
        layout.addWidget(self.closed_room_toolbar_btn)
        self.opening_buttons.append(self.closed_room_toolbar_btn)

        # === In-game buttons ===
        self.ingame_buttons = []

        self.next_deal_btn = ToolbarButton("Next deal")
        self.next_deal_btn.setStyleSheet(primary_button_style)   # green "go"
        self.next_deal_btn.clicked.connect(self._on_next_deal)
        layout.addWidget(self.next_deal_btn)
        self.ingame_buttons.append(self.next_deal_btn)

        self.next_card_btn = ToolbarButton("Next card")
        self.next_card_btn.clicked.connect(self._on_next_card)
        self.next_card_btn.setEnabled(False)
        layout.addWidget(self.next_card_btn)
        self.ingame_buttons.append(self.next_card_btn)

        # Spacebar shortcut for Next card. Use a window-scoped shortcut
        # so the press fires regardless of which child widget has focus,
        # and gate it on the button's enabled state inside the handler
        # so we don't double-advance when auto-advance is in flight.
        from PyQt6.QtGui import QShortcut
        from PyQt6.QtCore import Qt as _Qt
        self._next_card_shortcut = QShortcut(QKeySequence(_Qt.Key.Key_Space), self)
        self._next_card_shortcut.setContext(_Qt.ShortcutContext.WindowShortcut)
        self._next_card_shortcut.activated.connect(self._on_next_card_shortcut)

        self.hint_btn = ToolbarButton("Hint")
        self.hint_btn.clicked.connect(self._on_hint)
        # Off by default — _advance_game / _refresh_hint_button_state
        # turn it on only when the local user is on lead.
        self.hint_btn.setEnabled(False)
        layout.addWidget(self.hint_btn)
        self.ingame_buttons.append(self.hint_btn)

        self.undo_btn = ToolbarButton("Undo")
        self.undo_btn.clicked.connect(self._on_undo)
        self.undo_btn.setEnabled(False)
        layout.addWidget(self.undo_btn)
        self.ingame_buttons.append(self.undo_btn)

        self.claim_btn = ToolbarButton("Claim")
        self.claim_btn.clicked.connect(self._on_claim)
        self.claim_btn.setEnabled(False)
        layout.addWidget(self.claim_btn)
        self.ingame_buttons.append(self.claim_btn)

        self.evaluate_btn = ToolbarButton("Evaluate")
        self.evaluate_btn.clicked.connect(self._on_evaluate)
        layout.addWidget(self.evaluate_btn)
        self.ingame_buttons.append(self.evaluate_btn)

        self.autoplay_btn = ToolbarButton("Autoplay")
        self.autoplay_btn.setCheckable(True)
        self.autoplay_btn.toggled.connect(self._on_auto_play_toggle)
        layout.addWidget(self.autoplay_btn)
        self.ingame_buttons.append(self.autoplay_btn)

        self.review_btn = ToolbarButton("Review")
        self.review_btn.clicked.connect(self._on_review)
        self.review_btn.setEnabled(False)
        layout.addWidget(self.review_btn)
        self.ingame_buttons.append(self.review_btn)

        # Closed room — reachable on EVERY hand, not just the opening screen.
        # Starts the 4-biq-bot closed room for the current deal, or (when one
        # is already running in parallel) opens the open-vs-closed comparison.
        self.closed_room_btn = ToolbarButton("Closed room")
        self.closed_room_btn.setToolTip(
            "Closed room: four biq bots play this same deal in parallel.\n"
            "Start it for the current deal, or — once it's running — view\n"
            "the open-vs-closed (IMP) comparison.")
        self.closed_room_btn.clicked.connect(self._on_closed_room_button)
        layout.addWidget(self.closed_room_btn)
        self.ingame_buttons.append(self.closed_room_btn)

        # Toggle between the normal table and the analysis (teaching) view.
        # The label names the view a click switches TO.
        self.teaching_btn = ToolbarButton("Analysis view")
        self.teaching_btn.setCheckable(True)
        self.teaching_btn.setToolTip(
            "Toggle the analysis view: per-hand Known/Other suit grid, "
            "count, signals, entries, plan and coaching.")
        self.teaching_btn.toggled.connect(self._on_toggle_teaching_view)
        layout.addWidget(self.teaching_btn)
        self.ingame_buttons.append(self.teaching_btn)

        self.help_btn = ToolbarButton("Help")
        self.help_btn.clicked.connect(self._on_about)
        layout.addWidget(self.help_btn)
        self.ingame_buttons.append(self.help_btn)

        layout.addStretch()

        # Start in opening mode
        self._set_toolbar_mode('opening')

    def _set_toolbar_mode(self, mode: str):
        """Switch toolbar between 'opening' and 'ingame' modes."""
        if mode == 'opening':
            for btn in self.opening_buttons:
                btn.setVisible(True)
            for btn in self.ingame_buttons:
                btn.setVisible(False)
        else:  # ingame
            for btn in self.opening_buttons:
                btn.setVisible(False)
            for btn in self.ingame_buttons:
                btn.setVisible(True)

    def _on_first_deal(self):
        """Handle First deal button - start new deal and switch to in-game mode."""
        # Reset teams match mode for regular first deal
        self.teams_match = None
        self.match_controller = None
        self._set_toolbar_mode('ingame')
        self._on_new_deal()

    def _on_closed_room_button(self):
        """Play-toolbar 'Closed room' button — available on every hand.

        With a closed-room match already running (four biq bots playing each
        deal in parallel):
          • once the current deal is finished, clicking Closed room deals the
            NEXT comparison board — the human plays the open room while the
            four bots play the SAME deal in the closed room. (The score table
            stays reachable from the end-of-hand dialog's "View other table"
            button and Deal ▸ Teams score.)
          • mid-deal, it opens the open-vs-closed comparison for the boards
            played so far.
        Otherwise it starts closed-room play for the current deal — the same
        entry point as the opening-screen button.
        """
        if self.teams_match is not None and self.match_controller is not None:
            if self.controller.current_phase == 'finished':
                # Deal is over — advance to a fresh comparison board (this
                # blanks the finished deal's cards and starts the closed room
                # on the new deal), rather than popping the score table.
                self._on_next_deal()
            else:
                self._on_view_teams_score()
        else:
            self._on_closed_room_start()

    def _on_closed_room_start(self):
        """Closed Room button — let the user pick how to run it.

        • Internal all-biq — biq plays all four seats double-dummy,
          instantly, in-app (the original behaviour).
        • Real Q-Plus (harness) — launch the test harness and play a
          genuine closed room live: biq E/W vs Q-Plus N/S, card by card
          over Q-NET.
        """
        # The "Real Q-Plus (harness)" path only makes sense with a Q-Plus
        # build configured. With no Q-Plus available there's just one way to
        # play a closed room — four biq bots in-app — so skip the chooser and
        # run it directly.
        if not self._qplus_available():
            self._set_toolbar_mode('ingame')
            self._on_closed_room()
            return
        box = QMessageBox(self)
        from .dialogs.dialog_style import MESSAGEBOX_STYLESHEET
        box.setStyleSheet(MESSAGEBOX_STYLESHEET)   # readable on the dark window
        box.setWindowTitle("Closed Room")
        box.setText("How should the closed room be played?")
        box.setInformativeText(
            "Internal all-biq — biq plays all four seats double-dummy, "
            "instantly, without leaving bridgeIQ.\n\n"
            "Real Q-Plus (harness) — launch the test harness to play it "
            "out live: biq sits E/W, Q-Plus sits N/S.")
        internal_btn = box.addButton(
            "Internal all-biq", QMessageBox.ButtonRole.AcceptRole)
        real_btn = box.addButton(
            "Real Q-Plus (harness)…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(internal_btn)
        # QMessageBox sizes every button to one shared width; the long custom
        # labels ("Internal all-biq", "Real Q-Plus (harness)…") overflow it and
        # get clipped. Widen each button to its own text's hint so nothing cuts.
        for b in box.buttons():
            b.setMinimumWidth(b.sizeHint().width() + 16)
        box.exec()
        clicked = box.clickedButton()
        if clicked is internal_btn:
            self._set_toolbar_mode('ingame')
            self._on_closed_room()
        elif clicked is real_btn:
            self._launch_real_closed_room()

    def _launch_real_closed_room(self):
        """Launch the test harness in Closed Room mode for the current
        deal: biq E/W vs Q-Plus N/S, played out live over Q-NET.

        Needs a fully-dealt board (four 13-card hands). Deals one first
        if the table is empty so the button works straight from the
        opening screen.
        """
        board = self.controller.board
        if board is None:
            self._on_new_deal()
            board = self.controller.board
            if board is None:
                return
        self._launch_qplus_harness(board, closed_room=True)
        self.status_label.setText(
            "Launched test harness — Closed Room (biq E/W vs Q-Plus N/S). "
            "Follow the numbered steps in the harness window."
        )

    def _on_menu_new_deal(self):
        """Handle File > New Deal menu - return to opening screen."""
        # Reset any teams match
        if self.match_controller:
            self.match_controller.stop_closed_room()
        self.teams_match = None
        self.match_controller = None
        self._return_to_opening_screen()
        self.status_label.setText("Choose an option to start a new deal")

    def _on_next_deal(self):
        """Handle Next deal button - return to opening screen or advance teams match."""
        # Close the end-of-hand summary overlay so it doesn't linger over the
        # next deal.
        self._dismiss_end_of_hand_dialogs()
        # Strip the Q-Plus end-of-hand winner outlines from the
        # previous deal so the next hand starts visually clean.
        try:
            self.table_view.clear_end_of_hand_view()
        except Exception:
            pass

        # Check if we're still in bidding phase (hand not completed)
        still_in_bidding = (self.controller.current_phase == 'bidding' or
                           (self.controller.board and not self.controller.board.contract))

        # Claude analysis is MANUAL only — it does NOT run automatically when
        # a deal finishes or on Next deal. The user runs it from the
        # end-of-hand dialog's "Claude analysis" button or Extras ▸ Hand Log
        # (Claude Analysis) when they want it. (_show_result still stages the
        # pending-hand context so those buttons have something to analyse.)

        # Practice-deck / tournament-file queue is the authoritative deal
        # source: advance through the book even if a teams match was started
        # (e.g. the user pressed Closed room on a practice deal). Without this
        # the teams branch below would deal a fresh random board instead.
        queue = getattr(self, '_deal_queue', None)
        if queue and getattr(self, '_deal_queue_pos', 0) < len(queue):
            self._on_new_deal()
            return

        # If in teams match mode during bidding, user wants to discard this hand
        if self.teams_match is not None and self.match_controller is not None:
            if still_in_bidding:
                # User wants to discard this hand and start fresh
                # Don't show score dialog, just reset and go back to opening screen
                self.match_controller.stop_closed_room()  # Stop any running closed room
                self.teams_match = None
                self.match_controller = None
                self._return_to_opening_screen()
                self.status_label.setText("Hand discarded. Choose an option to start a new deal")
                return

            # Hand was completed - advance to next board in the match
            next_board = self.teams_match.current_board + 1
            if next_board <= self.teams_match.num_boards:
                self.teams_match.current_board = next_board
                self._start_teams_board(next_board)
            else:
                # Match complete - show final scores and return to opening
                self._on_view_teams_score()
                self.status_label.setText("Teams match complete!")
                self.teams_match = None
                self.match_controller = None
                self._return_to_opening_screen()
            return

        # For normal play, deal the next hand immediately
        self._on_new_deal()
        self.status_label.setText("Choose an option to start a new deal")

    def _return_to_opening_screen(self):
        """Return to the opening screen, clearing any current game state."""
        # Hide game UI elements
        self.bidding_box.setVisible(False)
        self.right_panel.setVisible(False)
        self.bid_info_dock.hide()

        # Clear the table - hide all hands
        for seat in Seat:
            self.table_view.set_hand_visible(seat, False)
        self.table_view.clear_trick()

        # Reset controller
        self.controller.board = None
        self.controller.current_phase = None

        # Switch toolbar to opening mode
        self._set_toolbar_mode('opening')

    def _setup_bid_info_window(self):
        """Setup the bidding-information panel. It is built here as a plain
        QWidget and later EMBEDDED at the upper-left of the bidding screen by
        _embed_bid_info_panel() (no free-floating window). Visibility follows
        the F3 toggle and the Preferences 'show bid-info panel' option.
        """
        self.bid_info_dock = QWidget()
        self.bid_info_dock.setObjectName("bidInfoPanel")
        # A self-contained light panel that reads on the dark table backdrop.
        self.bid_info_dock.setStyleSheet(
            "#bidInfoPanel { background-color: #f4f6f8;"
            " border: 1px solid #d9b25b; border-radius: 6px; }")

        # Content widget — laid out directly inside the top-level window.
        bid_info_widget = self.bid_info_dock
        bid_info_layout = QVBoxLayout(bid_info_widget)
        bid_info_layout.setContentsMargins(5, 5, 5, 5)
        bid_info_layout.setSpacing(2)

        # System banner — shows which bidding system the labels describe.
        self.bid_info_system_label = QLabel()
        self.bid_info_system_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.bid_info_system_label.setStyleSheet(
            "background-color: #d8e8f8; border: 1px solid #889; padding: 3px;"
        )
        self.bid_info_system_label.setWordWrap(True)
        bid_info_layout.addWidget(self.bid_info_system_label)
        self._refresh_bid_info_system_label()

        # Header row: Bid | Points | Help
        header_frame = QFrame()
        header_frame.setStyleSheet("background-color: #e0e0e0; border: 1px solid #999;")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(5, 3, 5, 3)
        header_layout.setSpacing(0)

        for col_name, width in [("Bid", 100), ("Points", 100), ("Help", 220)]:
            lbl = QLabel(col_name)
            lbl.setFont(QFont("Arial", 16, QFont.Weight.Bold))
            lbl.setFixedWidth(width)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(lbl)

        bid_info_layout.addWidget(header_frame)

        # Scrollable bid history area
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(90)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ccc; }")

        self.bid_history_widget = QWidget()
        self.bid_history_layout = QVBoxLayout(self.bid_history_widget)
        self.bid_history_layout.setContentsMargins(0, 0, 0, 0)
        self.bid_history_layout.setSpacing(1)
        self.bid_history_layout.addStretch()

        scroll.setWidget(self.bid_history_widget)
        bid_info_layout.addWidget(scroll, stretch=2)

        # Available bids section with meanings
        self.avail_label = QLabel("Available bids:")
        self.avail_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.avail_label.setStyleSheet("margin-top: 5px;")
        bid_info_layout.addWidget(self.avail_label)

        # Scrollable area for available bids with meanings
        avail_scroll = QScrollArea()
        avail_scroll.setWidgetResizable(True)
        avail_scroll.setMinimumHeight(120)
        # Selector-scoped: a bare "border: ..." cascaded onto every label and
        # boxed each cell, doubling the row height.
        avail_scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #ccc; background-color: #fff; }")

        self.available_bids_widget = QWidget()
        self.available_bids_widget.setStyleSheet("background-color: #fff;")
        self.available_bids_layout = QVBoxLayout(self.available_bids_widget)
        self.available_bids_layout.setContentsMargins(5, 3, 5, 3)
        self.available_bids_layout.setSpacing(0)

        avail_scroll.setWidget(self.available_bids_widget)
        # The available bids get most of the panel's height (the tester had
        # to scroll a 180-px list).
        bid_info_layout.addWidget(avail_scroll, stretch=5)

        # Size and position come from _place_bidding_overlays (top-left, as
        # tall as the room above South's hand).

    def _setup_statusbar(self):
        """Setup the status bar"""
        statusbar = self.statusBar()
        statusbar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {COLORS['panel_teal']};
                color: {COLORS['text_white']};
                font-size: 14px;
            }}
        """)

        self.status_label = QLabel("Ready")
        self.status_label.setFont(QFont("Arial", 14))
        self.status_label.setStyleSheet(f"color: {COLORS['text_white']};")
        statusbar.addWidget(self.status_label, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(150)
        self.progress_bar.setVisible(False)
        statusbar.addPermanentWidget(self.progress_bar)

        self.engine_status = QLabel("Starting…")
        self.engine_status.setFont(QFont("Arial", 14))
        self.engine_status.setStyleSheet(f"color: {COLORS['text_white']};")
        statusbar.addPermanentWidget(self.engine_status)

        # Flag shown when biq has stopped reading partner's (your) signals
        # because they were unreliable. Hidden until that happens.
        self.signal_flag = QLabel("")
        self.signal_flag.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.signal_flag.setStyleSheet("color: #ffcc00;")
        statusbar.addPermanentWidget(self.signal_flag)

    def _on_signal_note(self, msg: str):
        """After-hand signalling feedback from signal_trust (complain/warn/
        disable/recover). Shown in the status bar; the persistent ⛔ flag tracks
        whether biq is currently reading the human's signals."""
        try:
            from backend import signal_trust
            self.status_label.setText(msg)
            self.signal_flag.setText(signal_trust.status_flag() or "")
            mis = signal_trust.last_mis_signals()
        except Exception:
            return
        prefs = self.config_manager.config.preferences
        if mis and not getattr(prefs, 'signal_help_shown', False):
            prefs.signal_help_shown = True
            try:
                self.config_manager.save_preferences()
            except Exception:
                pass
            # After the end-of-hand summary has opened, so it sits on top.
            QTimer.singleShot(700, lambda m=mis: self._show_signal_help(m))

    def _show_signal_help(self, mis):
        """Shown once, the first time the player's defensive carding doesn't
        fit biq's conventions: what biq expected on this hand, and the
        conventions biq plays and reads (so its partner can follow them)."""
        from backend import signals
        ranks = "AKQJT98765432"
        syms = {0: "♠", 1: "♥", 2: "♦", 3: "♣"}

        def card(c52):
            r = ranks[c52 % 13]
            return syms[c52 // 13] + ("10" if r == "T" else r)

        this_hand = "".join(
            f"<li>Trick {m['trick'] + 1}: you played <b>{card(m['played52'])}</b>; "
            f"biq's conventions call for <b>{card(m['expected52'])}</b>.</li>"
            for m in mis[:3])
        if signals.is_udca():
            att = "a <b>low</b> spot encourages, a <b>high</b> spot discourages"
            cnt = "<b>low-high</b> shows an even number, high-low an odd number"
        else:
            att = "a <b>high</b> spot encourages, a <b>low</b> spot discourages"
            cnt = "<b>high-low</b> shows an even number, low-high an odd number"
        text = (
            "<p>biq reads its partner's defensive signals to defend better, so it "
            "helps to know what it expects. On this hand:</p>"
            f"<ul>{this_hand}</ul>"
            "<p><b>biq's signalling conventions</b> (first round of a suit only):</p>"
            "<ul>"
            f"<li><b>Attitude</b>, when partner leads a suit: {att}. biq encourages "
            "only with the honour that matters: after the ace, the king; after "
            "the king, the ace or queen (or the jack from J-x / J-x-x); after a "
            "spot lead, the A, K or Q.</li>"
            f"<li><b>Count</b>, when declarer leads a suit: {cnt}. Given only while "
            "the suit's ace or king is out of sight (not in dummy, not yet played). "
            "A high count card keeps the 9 back, except in fourth seat.</li>"
            "<li><b>Discards</b>: Lavinthal suit preference (a high discard asks "
            "for the higher of the other two suits).</li>"
            "<li>No trump echo, no Smith echo. Honours, the ten, and cards that "
            "could win the trick are never signals. With nothing to show, play "
            "your lowest spot.</li>"
            "</ul>"
            "<p>If several of your signals in a row don't fit, biq stops reading "
            "them (the ⛔ note at the bottom right) until your carding fits again. "
            "Standard or upside-down carding is set in "
            "Configuration ▸ Preferences ▸ Carding.</p>"
            "<p style='color:#555'>This explanation is shown once.</p>")
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("biq's signalling conventions")
        dlg.setStyleSheet("QDialog { background-color: #f4f6f8; }"
                          " QLabel { color: #1a1f29; }")
        lay = QVBoxLayout(dlg)
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setFont(QFont("Arial", 12))
        lbl.setFixedWidth(640)
        lay.addWidget(lbl)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        dlg.setModal(False)
        dlg.show()
        dlg.raise_()
        self._signal_help_box = dlg

    def _connect_signals(self):
        """Connect UI signals"""
        self.table_view.card_played.connect(self._on_card_played)
        self.bidding_box.bid_selected.connect(self._on_bid_made)
        # Click-to-explain (gated by the explain_actions_enabled preference).
        self.table_view.explain_bid_requested.connect(self._on_explain_bid)
        self.table_view.explain_card_requested.connect(self._on_explain_card)
        self._sync_explain_enabled()

    def _sync_explain_enabled(self):
        """Push the explain-actions preference to the table view."""
        try:
            on = bool(self.config_manager.config.preferences
                      .explain_actions_enabled)
        except Exception:
            on = False
        self.table_view.set_explain_enabled(on)

    def _seat_is_biq(self, seat: Seat) -> bool:
        """True when `seat` is played by biq (a bot), not a human."""
        try:
            return (self.controller.players[seat].player_type
                    != PlayerType.HUMAN)
        except Exception:
            return False

    def _show_explanation(self, explanation):
        from .dialogs import ExplanationDialog
        dlg = ExplanationDialog(explanation, self)
        dlg.exec()

    def _on_explain_bid(self, seat: Seat, bid):
        """Pop the why-popup for a clicked bid (gated by the preference)."""
        try:
            from backend import explain
            from backend.bid_descriptions import system_for_prefs
            board = self.controller.board
            auction = list(getattr(board, "auction", []) or [])
            # Locate this exact call to slice the auction before it.
            idx = next((i for i, b in enumerate(auction) if b is bid), None)
            if idx is None:
                idx = len(auction)
            before = auction[:idx]
            prefs = self.config_manager.config.preferences
            side = 'NS' if seat.is_ns() else 'EW'
            system = system_for_prefs(prefs, side)
            who = "native" if self._seat_is_biq(seat) else ""
            ex = explain.explain_bid(bid, before, seat, board.dealer,
                                     system, engine_who=who)
            self._show_explanation(ex)
        except Exception as e:
            self.status_label.setText(f"Could not explain bid: {e}")

    def _biq_reason_store(self):
        """The per-deal card→reason map, auto-reset when the board changes.

        Keyed off board_number so a stale reason from a previous deal can never
        surface — no matter which deal-setup path installed the new board."""
        bn = getattr(getattr(self.controller, "board", None), "board_number", None)
        if (getattr(self, "_biq_reasons_board", object()) != bn
                or not hasattr(self, "_biq_card_reasons")):
            self._biq_card_reasons = {}
            self._biq_reasons_board = bn
        return self._biq_card_reasons

    def _record_biq_card_reason(self, card, response):
        """Stash biq's stored reason for a card it just played, keyed by the
        card's unique code52 (each card is played once per deal)."""
        if card is None:
            return
        reason = getattr(response, "reason", "") or ""
        if not reason:
            return
        try:
            self._biq_reason_store()[card.code52()] = (
                reason, getattr(response, "reason_tag", "") or "")
        except Exception:
            pass

    def _on_explain_card(self, seat: Seat, card):
        """Pop the why-popup for a clicked played card."""
        if not self._seat_is_biq(seat):
            self.status_label.setText(
                "Explanations describe biq's actions; this was your own play.")
            return
        try:
            from backend import explain
            board = self.controller.board
            # Find the trick + position holding this card.
            tricks = list(getattr(board, "tricks", []) or [])
            cur = getattr(board, "current_trick", None)
            if cur is not None and getattr(cur, "cards", None):
                tricks = tricks + [cur]
            found_trick = found_pos = None
            for t_i, trick in enumerate(tricks):
                for c_i, c in enumerate(trick.cards):
                    if c.suit == card.suit and c.rank == card.rank:
                        found_trick, found_pos = (t_i, trick), c_i
                        break
                if found_trick is not None:
                    break
            if found_trick is None:
                return
            t_i, trick = found_trick
            before = list(trick.cards[:found_pos])
            is_opening_lead = (t_i == 0 and found_pos == 0)
            who = self._current_play_engine_label()
            # Prefer biq's ACTUAL stored reason for this exact card (captured
            # when biq played it); fall back to the public-info reconstruction.
            stored = self._biq_reason_store().get(card.code52())
            engine_reason, engine_tag = stored if stored else ("", "")
            ex = explain.explain_card(
                card, seat, before, trick.leader,
                getattr(board, "contract", None), is_opening_lead,
                engine_who=who, engine_reason=engine_reason,
                engine_tag=engine_tag)
            self._show_explanation(ex)
        except Exception as e:
            self.status_label.setText(f"Could not explain card: {e}")

    def _current_play_engine_label(self) -> str:
        """Name biq's active card-play engine, from preferences."""
        try:
            p = self.config_manager.config.preferences
            if getattr(p, "use_nopeek_play", False):
                return "no-peek"
            if getattr(p, "use_monte_carlo_play", False):
                return "MC"
            if getattr(p, "use_double_dummy_play", False):
                return "DDS"
        except Exception:
            pass
        return ""

    def _setup_network_signals(self):
        """Setup network controller signals"""
        self.network_controller.connection_established.connect(self._on_network_connected)
        self.network_controller.connection_lost.connect(self._on_network_disconnected)
        self.network_controller.client_joined.connect(self._on_network_client_joined)
        self.network_controller.server_started.connect(self._on_network_server_started)
        self.network_controller.deal_received.connect(self._on_network_deal_received)
        self.network_controller.dummy_revealed.connect(self._on_network_dummy_revealed)
        self.network_controller.remote_bid_received.connect(self._on_network_remote_bid)
        self.network_controller.remote_card_received.connect(self._on_network_remote_card)
        self.network_controller.trick_clear_received.connect(self._on_network_trick_clear)
        self.network_controller.error_occurred.connect(self._on_network_error)
        self.network_controller.closed_room_ingested.connect(self._on_network_closed_room_ingested)
        # Lobby — drive both the host's NetworkLobbyDialog and the guest's
        # ConnectServerDialog from the same SEAT_LIST stream.
        self.network_controller.seat_map_changed.connect(self._on_network_seat_map)
        self.network_controller.game_starting.connect(self._on_network_game_starting)
        self.network_controller.seat_request_rejected.connect(
            self._on_network_seat_rejected)

    def _initialize_engine(self):
        """Initialize the BEN engine"""
        self.status_label.setText("Initializing engine...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate

        QApplication.processEvents()

        success = self.engine.initialize()

        self.progress_bar.setVisible(False)

        if success:
            self.engine_status.setText("Ready")
            self.engine_status.setStyleSheet("color: #3fb950;")
            self.status_label.setText("Press Ctrl+N or “First deal” to start a hand.")

            # Create worker thread
            self.engine_worker = EngineWorker(self.engine)
            self.engine_worker.bid_ready.connect(self._on_engine_bid)
            self.engine_worker.card_ready.connect(self._on_engine_card)
            self.engine_worker.error.connect(self._on_engine_error)
        else:
            self.engine_status.setText("Not ready")
            self.engine_status.setStyleSheet("color: #f85149;")
            self.status_label.setText("Could not start — see the log file.")

    def _update_window_title(self):
        """Update window title with board number"""
        if self.controller.board:
            self.setWindowTitle(f"BridgeIQ - Board #{self.controller.board.board_number}")
        else:
            self.setWindowTitle("BridgeIQ")

    # Menu action handlers

    def _on_new_deal(self):
        """Start a new random deal"""
        if not self.engine.is_ready:
            QMessageBox.warning(self, "Engine Not Ready",
                              "The bridge engine is not yet initialized.")
            return

        # Register signal-trust ONCE per app session (not per deal) so the
        # partner-signal pressure accumulates ACROSS hands — that's what lets
        # "if it keeps happening" escalate complain -> warn -> auto-disable.
        try:
            from backend import signal_trust
            if not getattr(self, '_signal_trust_init', False):
                signal_trust.reset(on_message=self._on_signal_note)
                self._signal_trust_init = True
                self.signal_flag.setText("")
        except Exception:
            pass

        # Practice-deck / tournament-file queue takes priority over the
        # teams-match path: once a deal file is loaded it stays the deal
        # source for "Next deal" (advancing through the book), even if the
        # user pressed Closed room in between. This also routes every deal
        # through _present_fresh_board, which refreshes the book note. The
        # queue branch lives further down; jump to it when one is active.
        queue = getattr(self, '_deal_queue', None)
        if queue and getattr(self, '_deal_queue_pos', 0) < len(queue):
            self._play_next_queued_deal()
            return

        # If in teams match mode, use the teams board method
        if self.teams_match is not None and self.match_controller is not None:
            # Advance to next board in teams match
            next_board = self.teams_match.current_board + 1
            if next_board <= self.teams_match.num_boards:
                self.teams_match.current_board = next_board
                self._start_teams_board(next_board)
            else:
                # Match complete - show final scores
                self._on_view_teams_score()
                self.status_label.setText("Teams match complete!")
            return

        board = self.controller.new_deal()

        # Deal filter (random deals only): reject-sample until the deal matches
        # the configured filter, capped so an impossible filter can't hang.
        flt = getattr(self, '_active_deal_filter', None)
        if flt:
            attempts = 0
            while attempts < 5000 and not self._deal_passes_filter(board, flt):
                board = self.controller.new_deal()
                attempts += 1
            if not self._deal_passes_filter(board, flt):
                self.status_label.setText(
                    "Deal filter: no matching deal in 5000 tries — "
                    "using the last deal (loosen the filter?).")

        self._present_fresh_board(board)

    def _play_next_queued_deal(self):
        """Present the next deal from the practice-deck / tournament-file
        queue (Q-Plus deal source = Deal file). Plays the file's deals in
        order; _present_fresh_board then skips the auction when the deal
        carries a contract and shows its book note."""
        import copy
        queue = self._deal_queue
        pos = getattr(self, '_deal_queue_pos', 0)
        if pos >= len(queue):
            self.status_label.setText(
                "Tournament file complete — all deals played.")
            try:
                self._on_show_scores()
            except Exception:
                pass
            return
        board = copy.deepcopy(queue[pos])   # keep the queue pristine
        self._deal_queue_pos = pos + 1
        if not getattr(board, 'board_number', 0):
            board.board_number = pos + 1
        self.controller.board = board
        self.controller.current_phase = 'bidding'
        self.controller.current_seat = board.dealer
        self.controller.declarer = None
        self.controller.dummy = None
        self.controller.opening_leader = None
        self.controller.human_controls_declarer = False
        self._present_fresh_board(board)
        self.status_label.setText(
            f"Tournament deal {pos + 1}/{len(queue)}: "
            f"{board.dealer.to_char()} deals")

    def _present_fresh_board(self, board):
        """Set up the table/bidding UI for a freshly-dealt board and start the
        auction. Shared by New Deal and Previous Deal so both go through the
        same full reset (original hands, undo history, visibility, bots)."""
        # A board is now in play — show the in-game toolbar (Next card, …)
        # rather than the opening lobby (First deal, …). The First-deal /
        # Closed-room buttons already do this; deals started from Match
        # Control / a tournament file must too.
        try:
            self._set_toolbar_mode('ingame')
        except Exception:
            pass

        # Autoplay is a per-deal choice — never carry it into the next hand.
        # (Unchecking emits the toggle, which is a no-op when turning off.)
        try:
            if self.autoplay_btn.isChecked():
                self.autoplay_btn.setChecked(False)
        except Exception:
            pass
        # Book note — if this deal carries the textbook's play commentary
        # (a practice-deck hand), load it so it can be shown during play.
        note = (getattr(board, '_commentary', '') or '').strip()
        try:
            if note:
                self.book_note.setPlainText(note)
                self.book_note.setVisible(True)
            else:
                self.book_note.clear()
                self.book_note.setVisible(False)
        except Exception:
            pass

        # Closed room is meaningless on a practice-deck hand (the contract is
        # fixed by the book, so re-bidding it 4-bot just confuses the score
        # sheet) — disable it for book deals (identified by their commentary),
        # but keep it for ordinary deals and tournament files.
        is_practice = bool(note)
        try:
            self.closed_room_btn.setEnabled(not is_practice)
            self.closed_room_btn.setToolTip(
                "Not available for practice-deck hands"
                if is_practice else "")
        except Exception:
            pass

        # Store a deep copy of original hands for logging
        self.original_hands = {}
        for seat, hand in board.hands.items():
            self.original_hands[seat] = Hand(cards=list(hand.cards))

        # Clear card history for undo
        self.card_history = []
        self.undo_btn.setEnabled(False)

        # Reset per-deal flags — the Q-Plus slam-sim pop-up fires at
        # most once per board, so a fresh deal must re-arm it.
        self._slam_popup_shown_for_board = None

        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction([], board.dealer)
        self.bidding_box.setVisible(True)  # Show bidding box for new deal
        self.right_panel.setVisible(True)  # Ensure right panel is visible
        self.next_deal_btn.setVisible(True)  # Ensure next deal button is visible
        self.analysis_label.setText("")
        # Hide teaching inferences — fresh deal has no auction yet
        if hasattr(self, "inference_boxes"):
            self._update_inferences_panel(None)
        self._clear_bid_info()  # Clear bid info window
        self._update_available_bids()
        self._update_window_title()

        # Show bid info window for bidding phase
        if self.bid_info_action.isChecked():
            self._apply_bid_info_visibility()

        # Set visibility based on player types
        for seat in Seat:
            player = self.controller.players[seat]
            visible = player.player_type == PlayerType.HUMAN
            self.table_view.set_hand_visible(seat, visible)

        # Always show human's hand (South by default)
        self.table_view.set_hand_visible(self._user_seat, True)

        # Enable buttons
        self.next_card_btn.setEnabled(False)

        self.status_label.setText(f"Board {board.board_number}: {board.dealer.to_char()} deals")
        self._refresh_status_strip()

        # One-Player runtime — give the user the manual hand-entry
        # dialog so the seat they're playing carries the hand they
        # actually typed in, not the random deal. The "engine knows
        # ONLY this seat" half of the .one-player promise still
        # requires deeper engine work; a banner in the status bar
        # makes that limitation visible to the user.
        try:
            self._maybe_run_one_player_entry(board)
        except Exception as ex:
            print(f"[one-player] runtime failed: {ex!r}", flush=True)

        # MiniBridge runtime — when the user has selected play_mode
        # 'minibridge' in the One Player / MiniBridge dialog, skip
        # the auction and run the two Q-Plus rounds
        # (.bidbox-m-p / .bidbox-m-s) instead. Card play resumes
        # against the chosen contract.
        try:
            self._maybe_run_minibridge_rounds(board)
        except Exception as ex:
            print(f"[minibridge] runtime failed: {ex!r}", flush=True)

        # Preset-contract deals — when the deal file already carries a
        # contract + declarer (e.g. the practice decks), skip the auction
        # and play straight from that contract. No-op unless MiniBridge
        # left us in bidding and the board has a usable contract.
        try:
            self._maybe_play_from_preset_contract(board)
        except Exception as ex:
            print(f"[preset-contract] runtime failed: {ex!r}", flush=True)

        # Closed room (AI vs AI) is started after the human finishes the
        # hand — running it in parallel with human play made both sides
        # contend for the BEN engine and the game appeared to hang.

        # Push the deal to all connected guests so a host-side New Deal
        # click also resets every guest's table (otherwise multi-guest
        # sessions desync the moment the host starts a fresh hand).
        if self.network_controller.is_active and self.network_controller.is_server:
            self.network_controller.broadcast_deal(board)

        # Start bidding
        self._advance_game()

    def _on_open_file(self):
        """Open a deal file (PBN or BDL)"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open Deal File", "",
            "Bridge files (*.pbn *.bdl);;PBN Files (*.pbn);;BDL Files (*.bdl);;All Files (*)"
        )
        if filename:
            self._on_open_file_path(filename)

    def _on_save_file(self):
        """Save the current deal to PBN. Uses PBNExporter, which already
        round-trips deal+auction+play through Pavlicek format."""
        if self.controller.board is None:
            QMessageBox.information(
                self, "Save", "There's no deal to save yet — "
                "deal a hand or enter one first.")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Deal", "",
            "PBN Files (*.pbn);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.pbn'):
            filename = filename + '.pbn'
        try:
            from backend.pbn_exporter import PBNExporter
            from pathlib import Path
            PBNExporter().export_deal(
                self.controller.board, Path(filename))
            self.status_label.setText(
                f"Saved: {os.path.basename(filename)}")
        except Exception as ex:
            QMessageBox.warning(
                self, "Save failed",
                f"Could not write PBN file:\n{ex!r}")

    def _on_export_html(self):
        """Render the current deal as a standalone HTML page —
        compass layout for hands, table for auction, list for play,
        plus contract/result line. The page is self-contained: open
        it in any browser to inspect or print."""
        if self.controller.board is None:
            QMessageBox.information(
                self, "Export HTML",
                "There's no deal to export yet.")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export HTML", "",
            "HTML Files (*.html);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(('.html', '.htm')):
            filename = filename + '.html'
        try:
            html = self._render_board_html(self.controller.board)
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(html)
            self.status_label.setText(
                f"Exported: {os.path.basename(filename)}")
        except Exception as ex:
            QMessageBox.warning(
                self, "Export failed",
                f"Could not write HTML file:\n{ex!r}")

    def _render_board_html(self, board) -> str:
        """Build a self-contained HTML page for ``board``. Static
        CSS only; no external dependencies. Suit symbols use the
        Unicode glyphs ♠ ♥ ♦ ♣ for portability."""
        from html import escape

        suit_glyph = {0: '♠', 1: '♥', 2: '♦', 3: '♣'}
        rank_glyph = {0: 'A', 1: 'K', 2: 'Q', 3: 'J', 4: 'T',
                      5: '9', 6: '8', 7: '7', 8: '6', 9: '5',
                      10: '4', 11: '3', 12: '2'}

        def hand_block(seat):
            hand = board.hands.get(seat)
            if hand is None:
                return '<em>(hidden)</em>'
            by_suit = {0: [], 1: [], 2: [], 3: []}
            for c in hand.cards:
                by_suit[c.suit.value].append(c.rank.value)
            rows = []
            for s_val in (0, 1, 2, 3):
                ranks = sorted(by_suit[s_val])
                rstr = ''.join(rank_glyph[r] for r in ranks) or '—'
                color = '#c00' if s_val in (1, 2) else '#000'
                rows.append(
                    f'<div><span style="color:{color}">'
                    f'{suit_glyph[s_val]}</span> {escape(rstr)}</div>')
            return '\n'.join(rows)

        # Compass layout: North on top, W and E on the sides, S on bottom.
        from backend.models import Seat as _Seat
        n_block = hand_block(_Seat.NORTH)
        e_block = hand_block(_Seat.EAST)
        s_block = hand_block(_Seat.SOUTH)
        w_block = hand_block(_Seat.WEST)

        # Auction rows of four, dealer-aligned.
        auction_rows_html = ''
        if board.auction:
            cells_per_row = ['', '', '', '']
            order = [_Seat.NORTH, _Seat.EAST,
                     _Seat.SOUTH, _Seat.WEST]
            dealer_idx = order.index(board.dealer)
            cells = ['—'] * dealer_idx + [
                b.symbol() for b in board.auction]
            rows = []
            for i in range(0, len(cells), 4):
                row_cells = cells[i:i + 4]
                while len(row_cells) < 4:
                    row_cells.append('')
                rows.append(
                    '<tr>' + ''.join(
                        f'<td>{escape(c)}</td>'
                        for c in row_cells) + '</tr>')
            auction_rows_html = (
                '<table class="auction">'
                '<thead><tr><th>N</th><th>E</th><th>S</th><th>W</th>'
                '</tr></thead><tbody>'
                + ''.join(rows) + '</tbody></table>')

        # Play tricks.
        play_html = ''
        if board.tricks:
            play_rows = []
            for i, trick in enumerate(board.tricks, start=1):
                cards_in_order = []
                for j, c in enumerate(trick.cards):
                    seat = _Seat((trick.leader + j) % 4)
                    glyph = (
                        f'{suit_glyph[c.suit.value]}'
                        f'{rank_glyph[c.rank.value]}')
                    cards_in_order.append(
                        f'<span title="{seat.to_char()}">'
                        f'{escape(glyph)}</span>')
                play_rows.append(
                    f'<tr><td>{i}</td>'
                    f'<td>{trick.leader.to_char()}</td>'
                    f'<td>{" ".join(cards_in_order)}</td>'
                    f'<td>{trick.winner.to_char() if trick.winner else ""}</td>'
                    f'</tr>')
            play_html = (
                '<h2>Play</h2><table class="play">'
                '<thead><tr><th>#</th><th>Lead</th>'
                '<th>Cards (N E S W order from leader)</th>'
                '<th>Won</th></tr></thead><tbody>'
                + ''.join(play_rows) + '</tbody></table>')

        contract_line = '—'
        if board.contract:
            contract_line = (
                f'{escape(board.contract.to_str())} by '
                f'{board.contract.declarer.to_char()}')
            if len(board.tricks) == 13:
                contract_line += (
                    f' — {board.declarer_tricks} tricks '
                    f'({board.declarer_tricks - 6 - board.contract.level:+d})')

        vul_map = {'None': 'None', 'NS': 'N-S', 'EW': 'E-W',
                   'Both': 'Both'}
        vul = vul_map.get(board.vulnerability.value,
                          str(board.vulnerability.value))

        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Board {board.board_number}</title>
<style>
  body {{ font: 14px sans-serif; max-width: 720px; margin: 1em auto;
          color: #111; }}
  .deal {{ display: grid;
           grid-template-columns: 1fr 1fr 1fr;
           gap: 4px; text-align: center;
           border: 1px solid #888; padding: 12px;
           background: #fafafa; }}
  .deal > div {{ font-family: monospace; line-height: 1.45;
                 white-space: pre; min-width: 8em; }}
  .deal .north {{ grid-column: 2; }}
  .deal .west  {{ grid-column: 1; text-align: right; }}
  .deal .east  {{ grid-column: 3; text-align: left; }}
  .deal .south {{ grid-column: 2; }}
  table {{ border-collapse: collapse; margin-top: 12px; }}
  th, td {{ border: 1px solid #ccc; padding: 3px 8px;
            text-align: center; }}
  .auction td {{ font-family: monospace; }}
  .play td:nth-child(3) {{ font-family: monospace; }}
  h1, h2 {{ margin-bottom: 4px; }}
</style></head>
<body>
<h1>Board {board.board_number}</h1>
<p><strong>Dealer:</strong> {board.dealer.to_char()} &nbsp;
   <strong>Vul:</strong> {escape(vul)} &nbsp;
   <strong>Contract:</strong> {contract_line}</p>
<div class="deal">
  <div class="north">N<br>{n_block}</div>
  <div class="west">W<br>{w_block}</div>
  <div class="east">E<br>{e_block}</div>
  <div class="south">S<br>{s_block}</div>
</div>
{('<h2>Auction</h2>' + auction_rows_html) if auction_rows_html else ''}
{play_html}
</body></html>
"""

    def _maybe_run_one_player_entry(self, board):
        """If the user is in One-Player Mode, pop the manual hand-
        entry dialog and swap the random hand for the named seat
        with whatever the user typed in. No-op when
        play_mode != 'one_player'.

        Spec: in One-Player mode the program "knows only the hand of
        the specified player. The cards of this hand (and later of
        dummy) must be entered manually for each deal." We honour
        that literally — the three non-named seats get EMPTY hands
        and become PlayerType.HUMAN, so every bid and every card from
        them is entered manually by the user (typically while reading
        them off a real-world table). The engine is never asked to
        bid or play for a seat whose hand it doesn't have.
        """
        try:
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
        except Exception:
            self._one_player_active = False
            self._one_player_known_seats = set()
            return
        if getattr(prefs, 'play_mode', 'off') != 'one_player':
            self._one_player_active = False
            self._one_player_known_seats = set()
            return
        seat_char = getattr(prefs, 'one_player_seat', 'S')
        try:
            target = Seat.from_char(seat_char)
        except Exception:
            target = Seat.SOUTH

        from .dialogs.one_player_hand_entry import OnePlayerHandEntryDialog
        dlg = OnePlayerHandEntryDialog(
            seat_char=target.to_char(), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._one_player_active = False
            self._one_player_known_seats = set()
            return
        new_hand = dlg.get_hand()
        if new_hand is None or not new_hand.cards:
            self._one_player_active = False
            self._one_player_known_seats = set()
            return

        # Install the user's hand at the named seat; clear the other
        # three so the engine has literally nothing to peek at. Flip
        # every seat to HUMAN so the bot decision path is never
        # entered — every bid and card is human-entered.
        try:
            from backend.models import Hand
            board.hands[target] = new_hand
            for s in Seat:
                if s != target:
                    board.hands[s] = Hand(cards=[])
            for s in Seat:
                self.controller.players[s].player_type = PlayerType.HUMAN
            self._user_seat = target
            self.controller.human_controls_declarer = True
            self._one_player_active = True
            self._one_player_known_seats = {target}
            # The original_hands snapshot is used by replay and the
            # post-mortem comparison. Empty placeholders prevent
            # spurious peeks via that channel.
            self.original_hands = {
                s: Hand(cards=list(board.hands[s].cards))
                for s in Seat
            }
            self.table_view.set_board(board)
            # The visibility loop in _on_new_deal ran with the old
            # COMPUTER/HUMAN split. Fix it now: only the user's seat
            # shows its cards; the three unknown seats stay hidden
            # until cards from them are entered (or dummy is revealed).
            for s in Seat:
                self.table_view.set_hand_visible(s, s == target)
        except Exception as ex:
            print(f"[one-player] hand swap failed: {ex!r}", flush=True)
            self._one_player_active = False
            self._one_player_known_seats = set()
            return

        self.status_label.setText(
            f"One-Player Mode active — engine sees only "
            f"{target.to_char()}'s hand (+ dummy after the lead). "
            "You enter bids and cards for the other seats manually."
        )

    def _one_player_seat_known(self, seat) -> bool:
        """Returns True iff the engine is allowed to see ``seat``'s
        hand in the current deal. Only meaningful when
        ``_one_player_active`` is set; outside one-player mode every
        seat is known."""
        if not getattr(self, '_one_player_active', False):
            return True
        return seat in getattr(self, '_one_player_known_seats', set())

    def _one_player_played_pairs(self) -> set:
        """Return the set of (suit_char, rank_char) tuples already
        played in the current deal — used to disable those cells in
        the per-card entry grid so the user can't re-pick them."""
        board = self.controller.board
        out = set()
        if board is None:
            return out
        suit_char = {0: 'S', 1: 'H', 2: 'D', 3: 'C'}
        for trick in board.tricks:
            for c in trick.cards:
                try:
                    out.add((suit_char[c.suit.value], c.rank.to_char()))
                except Exception:
                    pass
        ct = board.current_trick
        if ct is not None:
            for c in ct.cards:
                try:
                    out.add((suit_char[c.suit.value], c.rank.to_char()))
                except Exception:
                    pass
        return out

    def _one_player_maybe_enter_dummy(self) -> bool:
        """If we're in one-player mode and dummy's hand hasn't been
        entered yet, pop the hand-entry dialog for dummy. Returns
        True when dummy is now known (either it was already or the
        user just entered it), False on cancel."""
        if not getattr(self, '_one_player_active', False):
            return True
        dummy_seat = self.controller.dummy
        if dummy_seat is None:
            return True
        if dummy_seat in self._one_player_known_seats:
            return True
        from .dialogs.one_player_hand_entry import OnePlayerHandEntryDialog
        dlg = OnePlayerHandEntryDialog(
            seat_char=dummy_seat.to_char(), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        hand = dlg.get_hand()
        if hand is None or not hand.cards:
            return False
        # Subtract any cards dummy has already played (rare — dummy
        # plays after the opening lead, but if the user delayed the
        # entry we still want the remaining-cards state to be right).
        played_by_dummy = set()
        suit_char = {0: 'S', 1: 'H', 2: 'D', 3: 'C'}
        board = self.controller.board
        for trick in board.tricks:
            for i, c in enumerate(trick.cards):
                from backend.models import Seat as _Seat
                seat = _Seat((trick.leader + i) % 4)
                if seat == dummy_seat:
                    played_by_dummy.add((c.suit, c.rank))
        ct = board.current_trick
        if ct is not None:
            from backend.models import Seat as _Seat
            for i, c in enumerate(ct.cards):
                seat = _Seat((ct.leader + i) % 4)
                if seat == dummy_seat:
                    played_by_dummy.add((c.suit, c.rank))
        remaining = [c for c in hand.cards
                     if (c.suit, c.rank) not in played_by_dummy]
        from backend.models import Hand
        board.hands[dummy_seat] = Hand(cards=remaining)
        self._one_player_known_seats.add(dummy_seat)
        # Snapshot the full hand for replay / comparison.
        try:
            self.original_hands[dummy_seat] = Hand(cards=list(hand.cards))
        except Exception:
            pass
        try:
            self.table_view.set_board(board)
        except Exception:
            pass
        return True

    def _one_player_run_card_entry(self, seat) -> bool:
        """Pop the per-card entry dialog for ``seat`` and commit
        whichever card the user picked into the trick. Returns True
        when a card was registered, False when the user cancelled
        (in which case the table sits and waits for another action).
        """
        from .dialogs.one_player_card_entry import OnePlayerCardEntryDialog
        lead_suit = None
        ct = self.controller.board.current_trick
        if ct is not None and ct.cards:
            lead_suit = ct.cards[0].suit.to_char()
        dlg = OnePlayerCardEntryDialog(
            seat_char=seat.to_char(),
            played=self._one_player_played_pairs(),
            lead_suit=lead_suit,
            parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        card = dlg.get_card()
        if card is None:
            return False
        # Make sure the seat's hand contains the card so the
        # controller's standard remove-card path works unchanged.
        from backend.models import Hand
        hand = self.controller.board.hands.get(seat)
        if hand is None:
            hand = Hand(cards=[])
            self.controller.board.hands[seat] = hand
        if not any(c.suit == card.suit and c.rank == card.rank
                   for c in hand.cards):
            hand.cards.append(card)
        try:
            self._on_card_played(seat, card)
        except Exception as ex:
            print(f"[one-player] card entry commit failed: {ex!r}",
                  flush=True)
            return False
        return True

    def _minibridge_auto_contract(self, board, declarer, dummy):
        """Pick a sensible MiniBridge contract for the declaring side from its
        combined HCP and longest combined fit. Used as the bot declarer's
        choice and as the hint for the human declarer."""
        from backend.models import Suit, Contract
        hcp = board.hands[declarer].hcp() + board.hands[dummy].hcp()

        def lengths(seat):
            d = {Suit.SPADES: 0, Suit.HEARTS: 0,
                 Suit.DIAMONDS: 0, Suit.CLUBS: 0}
            for c in board.hands[seat].cards:
                d[c.suit] += 1
            return d
        a, b = lengths(declarer), lengths(dummy)
        comb = {su: a[su] + b[su] for su in a}

        # Prefer an 8+ major fit, else an 8+ minor fit, else no-trumps.
        strain = Suit.NOTRUMP
        for su in (Suit.SPADES, Suit.HEARTS):
            if comb[su] >= 8:
                strain = su
                break
        else:
            for su in (Suit.DIAMONDS, Suit.CLUBS):
                if comb[su] >= 9:        # minors need length to be worth it
                    strain = su
                    break

        is_major = strain in (Suit.SPADES, Suit.HEARTS)
        is_minor = strain in (Suit.DIAMONDS, Suit.CLUBS)
        if hcp >= 37:
            level = 7
        elif hcp >= 33:
            level = 6
        elif hcp >= 25:
            level = 4 if is_major else (5 if is_minor else 3)   # 4M / 5m / 3NT
        elif hcp >= 23:
            level = 3 if is_major else 2                          # invitational
        elif hcp >= 19:
            level = 2 if (is_major or is_minor) else 1
        else:
            level = 1
        level = max(1, min(7, level))
        return Contract(level=level, suit=strain, declarer=declarer)

    def _maybe_run_minibridge_rounds(self, board):
        """If the user is in MiniBridge mode, run the two simplified
        rounds (announce points → pick contract) and start card play
        on the chosen contract. No-op when play_mode != 'minibridge'.

        Per Q-Plus spec the side with the higher combined HP is the
        declaring side; within that side, the seat with the higher
        individual HP is declarer. We compute everything from the
        actual hands — the user just announces THEIR side's HP and
        picks the contract.
        """
        try:
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
        except Exception:
            return
        if getattr(prefs, 'play_mode', 'off') != 'minibridge':
            return

        # Hide the bidding box — we replace it with the two dialogs.
        try:
            self.bidding_box.setVisible(False)
        except Exception:
            pass

        from .dialogs.minibridge_rounds import (
            MiniBridgePointsDialog, MiniBridgeContractDialog)

        user_seat = self._user_seat
        partner_seat = user_seat.partner()
        try:
            user_hp = board.hands[user_seat].hcp()
            partner_hp = board.hands[partner_seat].hcp()
        except Exception:
            user_hp = partner_hp = None

        from backend.models import Suit, Contract

        # Step 1 — determine the declaring side and declarer FROM THE POINTS,
        # BEFORE any contract is chosen (real MiniBridge order). Declaring side
        # = more combined HP (N/S wins ties); declarer = the higher-HP seat of
        # that side; partner is dummy.
        try:
            hps = {s: board.hands[s].hcp() for s in Seat}
        except Exception:
            return
        ns = hps[Seat.NORTH] + hps[Seat.SOUTH]
        ew = hps[Seat.EAST] + hps[Seat.WEST]
        if ns >= ew:
            declarer = (Seat.NORTH if hps[Seat.NORTH] >= hps[Seat.SOUTH]
                        else Seat.SOUTH)
        else:
            declarer = (Seat.EAST if hps[Seat.EAST] >= hps[Seat.WEST]
                        else Seat.WEST)
        dummy = declarer.partner()
        suit_map = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                    'D': Suit.DIAMONDS, 'C': Suit.CLUBS,
                    'NT': Suit.NOTRUMP}

        if declarer == user_seat:
            # The human declares: expose dummy (declarer sees both hands when
            # choosing in MiniBridge), then announce points and pick a contract.
            try:
                self.table_view.set_hand_visible(dummy, True)
            except Exception:
                pass
            round1 = MiniBridgePointsDialog(
                seat_char=user_seat.to_char(),
                hint_hp=hps[declarer] + hps[dummy], parent=self)
            if round1.exec() != QDialog.DialogCode.Accepted:
                return
            auto = self._minibridge_auto_contract(board, declarer, dummy)
            round2 = MiniBridgeContractDialog(
                hint_level=auto.level,
                hint_suit=('NT' if auto.suit == Suit.NOTRUMP
                           else auto.suit.to_char()),
                parent=self)
            if round2.exec() != QDialog.DialogCode.Accepted:
                return
            cd = round2.get_contract()
            if cd is None:
                return
            level, suit_char, doubled, redoubled = cd
            contract = Contract(
                level=level, suit=suit_map.get(suit_char, Suit.NOTRUMP),
                doubled=bool(doubled), redoubled=bool(redoubled),
                declarer=declarer)
        else:
            # A bot declares (the opponents, or the user's partner): the engine
            # chooses the contract from the two declaring hands; the user
            # defends (or sits as dummy).
            contract = self._minibridge_auto_contract(board, declarer, dummy)
            QMessageBox.information(
                self, "MiniBridge",
                f"{declarer.to_char()} has the stronger side "
                f"({max(ns, ew)} HCP) and declares {contract.to_str()}.")

        self.controller.set_contract_direct(contract)
        # Refresh UI to reflect the fresh play phase.
        try:
            self.table_view.update_auction(
                self.controller.board.auction,
                self.controller.board.dealer)
        except Exception:
            pass
        try:
            self.table_view.set_contract(
                contract.to_str(), declarer.to_char())
        except Exception:
            pass
        self.status_label.setText(
            f"MiniBridge: {contract.to_str()} by "
            f"{declarer.to_char()} — opening lead")
        # Kick the play loop (handle bot opening lead if declarer's
        # LHO is a bot).
        try:
            self._advance_game()
        except Exception:
            pass

    def _maybe_play_from_preset_contract(self, board):
        """If ``board`` arrived with a contract + declarer already set (a deal
        file that carries the final contract, e.g. the practice decks), skip
        the auction and play straight from that contract — Q-Plus plays such
        'own deals' from their stored contract rather than re-bidding them.

        No-op when: there is no usable contract; MiniBridge or a real auction
        has already moved us out of the bidding phase; or the board carries a
        real multi-call auction we should replay instead.
        """
        # Only fire while still set up for a fresh auction.
        if self.controller.current_phase != 'bidding':
            return
        contract = getattr(board, 'contract', None)
        declarer = getattr(contract, 'declarer', None) if contract else None
        if contract is None or declarer is None:
            return
        # A board that already holds a genuine auction (more than the single
        # synthesised contract-bid) should be replayed through bidding, not
        # short-circuited. Practice decks have an empty auction.
        auction = getattr(board, 'auction', None) or []
        if len(auction) > 1:
            return

        self.controller.set_contract_direct(contract)

        # Replace the bidding box with the contract banner, exactly as the
        # MiniBridge path does after it picks a contract.
        try:
            self.bidding_box.setVisible(False)
        except Exception:
            pass
        try:
            self.table_view.update_auction(
                self.controller.board.auction, self.controller.board.dealer)
        except Exception:
            pass
        try:
            self.table_view.set_contract(
                contract.to_str(), declarer.to_char())
        except Exception:
            pass

        leader = declarer.next()
        self.status_label.setText(
            f"Board {board.board_number}: {contract.to_str()} by "
            f"{declarer.to_char()} (from deal file) — {leader.to_char()} "
            f"leads. Auction skipped.")

    def _on_print_current_hand(self):
        """File → Print Current Hand. Q-Plus spec at BRIDGE.HLQ
        .printing (lines 1930-1940): pick a card layout on the left
        and which extras to include on the right, then send to the
        printer. Only the current deal is printable."""
        board = self.controller.board
        if board is None:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Print",
                "No deal is open to print. Start or open a deal first."
            )
            return
        # Pull together the result banner / contract / result so the
        # printout matches what the end-of-hand dialog shows.
        contract = getattr(board, 'contract', None)
        result_str = ""
        banner_text = ""
        try:
            if contract is not None and getattr(
                    board, 'tricks', None):
                tricks = board.declarer_tricks
                target = contract.target_tricks()
                if tricks >= target:
                    overtricks = tricks - target
                    result_str = "Made" if overtricks == 0 else f"+{overtricks}"
                else:
                    result_str = f"-{target - tricks}"
                # Q-Plus-style banner sentence — same logic as
                # EndOfHandDialog._compose_banner_text.
                seat_full = {'N': 'North', 'E': 'East',
                             'S': 'South', 'W': 'West'}
                seat_char = contract.declarer.to_char()
                seat_name = seat_full.get(seat_char, seat_char)
                if result_str.startswith('-'):
                    n = -int(result_str)
                    tw = 'trick' if n == 1 else 'tricks'
                    spelled = {1: 'one', 2: 'two', 3: 'three',
                               4: 'four', 5: 'five', 6: 'six',
                               7: 'seven'}.get(n, str(n))
                    banner_text = (
                        f"playing finished: {seat_name} was set "
                        f"{spelled} {tw} in {contract.to_str()}"
                    )
                elif result_str.startswith('+'):
                    banner_text = (
                        f"playing finished: {seat_name} made "
                        f"{contract.to_str()} {result_str} overtricks"
                    )
                else:
                    banner_text = (
                        f"playing finished: {seat_name} made "
                        f"{contract.to_str()}"
                    )
        except Exception:
            pass
        try:
            remark = (self._remark_for_current_board()
                      if hasattr(self, '_remark_for_current_board')
                      else "")
        except Exception:
            remark = ""

        from .dialogs.print_dialog import PrintCurrentHandDialog
        dlg = PrintCurrentHandDialog(
            board=board,
            original_hands=self.original_hands,
            contract=contract,
            result_str=result_str,
            banner_text=banner_text,
            remark=remark,
            parent=self,
        )
        dlg.exec()

    def _on_pair_tournament(self):
        """Start a pairs tournament — Pairs (Matchpoints) scoring; plays a
        tournament-file deal source in order when one is selected."""
        dialog = MatchControlDialog(self)
        # Default a pairs tournament to MP scoring for convenience.
        try:
            dialog.mp_radio.setChecked(True)
        except Exception:
            pass
        if dialog.exec():
            settings = dialog.get_settings()
            self.teams_match = None
            self.match_controller = None
            self._apply_match_settings(settings)
            self.status_label.setText("Pairs tournament started")
            self._on_new_deal()

    def _on_closed_room(self):
        """Run the current board in closed room (computer vs computer).

        Creates a single-board teams match, then runs the same deal
        with 4 bots in the closed room while user plays the open room.
        """
        # Closed room requires four hands the engine can see — by
        # definition impossible in One-Player Mode where the engine
        # only has the user's seat (+ dummy after the lead). Stop
        # here and tell the user why.
        if getattr(self, '_one_player_active', False):
            QMessageBox.information(
                self, "Closed room unavailable",
                "Closed-room play needs all four hands, but One-Player "
                "Mode hides three of them from the engine. Switch "
                "play mode off in Preferences to enable closed room.")
            return

        if not self.controller.board:
            # Start a new deal first
            self._on_new_deal()
            if not self.controller.board:
                return

        board = self.controller.board
        board_num = board.board_number

        # Create the closed-room teams match if not already in one. Open-ended
        # (num_boards huge) so each "Next deal" deals a FRESH closed-room board
        # and runs the 4-bot room again, accumulating IMPs — instead of the
        # match "completing" after a single board with no way to play another.
        if self.teams_match is None or self.match_controller is None:
            import uuid
            self.teams_match = BenTeamsMatch(
                match_id=str(uuid.uuid4()),
                num_boards=board_num + 100000,
                current_board=board_num,
                ns_bidding_system=self._current_pair_systems()[0],
                ew_bidding_system=self._current_pair_systems()[1],
            )
            self.match_controller = TeamsMatchController(self.engine, self.teams_match)

            # Store original hands for comparison
            if not self.original_hands:
                self.original_hands = {}
                for seat, hand in board.hands.items():
                    self.original_hands[seat] = Hand(cards=list(hand.cards))

            # Register the current board with the match controller
            self.match_controller.start_board(board_num, board)

        # Q-Plus semantics: the closed room (four biq bots) plays the SAME deal
        # AT THE SAME TIME as you play the open room. Kick it off now so it runs
        # in parallel; the end-of-hand handler then joins the already-running
        # (or finished) worker. (The old "play your hand first" sequencing was a
        # BEN-subprocess-era workaround against broken pipes; the engine is now
        # in-process and serialises DDS access through its own re-entrant lock,
        # and the closed room plays an INDEPENDENT game off a pristine copy of
        # the dealt hands — so concurrent play is safe and self-contained.)
        self._closed_room_async_started = None     # fresh match → fresh start
        self._begin_parallel_closed_room(board_num)

    def _begin_parallel_closed_room(self, board_num: int):
        """Start the four-biq-bot closed room in the background so it plays the
        same deal at the same time as the human's open room (Q-Plus 'Closed
        Room' behaviour). Idempotent per board. The closed room is independent
        — it bids and plays off a pristine copy of the dealt hands, so starting
        it early changes nothing except that it runs concurrently."""
        if self.teams_match is None or self.match_controller is None:
            return
        if self._closed_room_async_started == board_num:
            return
        try:
            self.match_controller.start_closed_room_async(
                board_num, callback=self._on_closed_room_complete)
            self._closed_room_async_started = board_num
            self.status_label.setText(
                f"Board {board_num} — closed room (4 biq bots) is playing "
                f"alongside you.")
        except Exception as e:
            self._closed_room_async_started = None
            print(f"parallel closed room failed to start: {e!r}", flush=True)
            self.status_label.setText(
                f"Board {board_num} — play your hand; closed room runs after.")

    def _on_team_tournament(self):
        """Start a teams tournament — IMP scoring. Closed-room comparison runs
        the 4-bot teams match; a tournament-file source plays the file's deals."""
        dialog = MatchControlDialog(self)
        if dialog.exec():
            settings = dialog.get_settings()
            # A selected deal file plays its deals (skipping the auction when
            # the file carries the contract); otherwise the closed-room teams
            # path runs.
            if settings.get('source') == 'file' and settings.get('file_path'):
                self.teams_match = None
                self.match_controller = None
                if self._apply_match_settings(settings):
                    self._on_new_deal()
            elif settings.get('comparison') == 'closed_room':
                self._start_teams_match(settings)
            else:
                self.teams_match = None
                self.match_controller = None
                if self._apply_match_settings(settings):
                    self._on_new_deal()
            self.status_label.setText("Teams tournament started")

    def _on_help(self):
        """Show help dialog"""
        help_text = """BridgeIQ - Quick Help

Keyboard Shortcuts:
  Ctrl+N - New deal
  F2 - Show all hands
  P - Pass
  X - Double
  1C-7N - Make a bid

Toolbar Buttons:
  First deal - Start a new random deal
  Pair tourn. - Start pairs tournament
  Closed Room - Run current board with computer
  Team tourn. - Start teams tournament
  Match control - Configure match settings
  Help - Show this help

For more information, see the README file."""
        QMessageBox.information(self, "Help", help_text)

    def _on_programming_rosetta(self):
        """Open the bridge-as-programming-language Rosetta Stone.

        Three sections — bidding systems → languages, conventions →
        libraries / type-system features, defensive carding → IPC.
        Aimed at users who find the bridge terminology unfamiliar
        but who already think in programming terms.
        """
        dialog = ProgrammingHelpDialog(self)
        dialog.exec()

    def _apply_match_settings(self, settings: dict):
        """Honor the Match Control axes that aren't the closed-room teams path:
        the scoring method (Rubber/IMP/MP) and a 'From file' deal source (load
        the tournament file's deals into a playable queue). Returns True if a
        tournament-file deal queue was loaded."""
        from backend.scoring import ScoringType
        smap = {'rubber': ScoringType.RUBBER, 'imp': ScoringType.TEAMS,
                'mp': ScoringType.PAIRS}
        st = smap.get(settings.get('scoring'))
        if st is not None and not self.scoring_table.results:
            self.scoring_table.scoring_type = st     # don't change mid-match
        self._deal_queue = []
        self._deal_queue_pos = 0
        self._field_results = {}
        self._comparison_mode = settings.get('comparison')
        # Deal filter from Match Control (applies to biq's random deals).
        if 'deal_filter' in settings:
            self._active_deal_filter = settings.get('deal_filter')
        if settings.get('source') == 'file' and settings.get('file_path'):
            boards = self._load_deal_queue_from_file(settings['file_path'])
            if boards:
                self._deal_queue = boards
                self._deal_queue_pos = max(
                    0, int(settings.get('first_deal', 1)) - 1)
                self.status_label.setText(
                    f"Tournament file: {len(boards)} deals loaded "
                    f"(starting at #{self._deal_queue_pos + 1}).")
                return True
            self.status_label.setText(
                "Tournament file: no playable deals found.")
        return False

    def _load_deal_queue_from_file(self, path: str):
        """Read a deal file (.pbn / .bdl) into a list of fully-dealt
        BoardStates for tournament play, and record any per-board results the
        file carries into self._field_results (keyed by board number) for the
        Results-of-file comparison. Best-effort; returns []."""
        from pathlib import Path
        p = Path(path)
        out = []
        recs = []        # parallel list of (ns_score, contract) or None
        try:
            if p.suffix.lower() == '.bdl':
                from backend.bdl_reader import BDLReader
                deals = BDLReader().read_file(p)
                for d in deals:
                    bs = d.to_board_state()
                    # to_board_state() drops the Commentary; carry it so the
                    # book's play notes can be shown during play.
                    bs._commentary = getattr(d, 'commentary', '') or ''
                    out.append(bs)
                    recs.append(d if d.contract is not None else None)
            elif p.suffix.lower() == '.pbn':
                from backend.pbn_exporter import PBNParser
                out = list(PBNParser().parse_file(p))
                recs = [None] * len(out)
        except Exception as e:
            print(f"deal-queue load failed: {e!r}", flush=True)
        # Keep only boards with all four 13-card hands; number them
        # sequentially by position in the file so "Next deal" visibly
        # advances (1, 2, 3, …). We override any board number the file
        # carried because some books restart their own numbering per
        # chapter (e.g. "Practice Hand #1" appears in several chapters),
        # which would make the deal counter regress and look stuck. The
        # Results-of-file lookup keys on this same sequential number.
        good = []
        if not hasattr(self, '_field_results'):
            self._field_results = {}
        seq = 0
        for b, rec in zip(out, recs + [None] * (len(out) - len(recs))):
            hands = getattr(b, 'hands', None) or {}
            if not (len(hands) >= 4 and all(
                    len(getattr(hands[s], 'cards', [])) == 13 for s in hands)):
                continue
            seq += 1
            b.board_number = seq
            good.append(b)
            if rec is not None:
                self._field_results.setdefault(b.board_number, []).append({
                    'ns_score': rec.ns_score, 'ew_score': rec.ew_score,
                    'contract': rec.contract, 'declarer': rec.declarer,
                    'tricks_made': rec.tricks_made})
        return good

    def _on_match_control(self):
        """Show match control dialog"""
        dialog = MatchControlDialog(self)
        if dialog.exec():
            settings = dialog.get_settings()

            # A selected deal file plays its own deals (and skips the auction
            # when the file carries the contract). Otherwise a closed-room
            # comparison starts the teams match.
            if settings.get('source') == 'file' and settings.get('file_path'):
                self.teams_match = None
                self.match_controller = None
                if self._apply_match_settings(settings):
                    self._on_new_deal()
            elif settings.get('comparison') == 'closed_room':
                self._start_teams_match(settings)
            else:
                # Clear teams match state for non-teams modes
                self.teams_match = None
                self.match_controller = None
                # Honor scoring method + a tournament-file deal source.
                if self._apply_match_settings(settings):
                    self._on_new_deal()      # start playing the file's deals

    def _start_teams_match(self, settings: dict):
        """Start a new teams match with closed room comparison."""
        import uuid

        start_num = settings.get('start_number', 1)

        # Create teams match
        ns_sys_name, ew_sys_name = self._current_pair_systems()
        self.teams_match = BenTeamsMatch(
            match_id=str(uuid.uuid4()),
            num_boards=settings.get('num_boards', 16),
            current_board=start_num,
            ns_bidding_system=ns_sys_name,
            ew_bidding_system=ew_sys_name,
        )

        # Create match controller
        self.match_controller = TeamsMatchController(self.engine, self.teams_match)

        print(f"Starting teams match: {self.teams_match.num_boards} boards, starting at {start_num}", flush=True)

        # Start the first board
        self._start_teams_board(start_num)

    def _start_teams_board(self, board_num: int):
        """Start a new board in the teams match."""
        if self.teams_match is None or self.match_controller is None:
            print(f"ERROR: _start_teams_board called but teams_match or match_controller is None", flush=True)
            return

        print(f"Starting teams board {board_num}", flush=True)

        # A teams board is a fresh random deal, not a book deal — clear any
        # book note left over from a practice-deck deal, re-enable Closed
        # room, and reset autoplay (a per-deal choice).
        try:
            self.book_note.clear()
            self.book_note.setVisible(False)
            self.closed_room_btn.setEnabled(True)
            self.closed_room_btn.setToolTip("")
            if self.autoplay_btn.isChecked():
                self.autoplay_btn.setChecked(False)
        except Exception:
            pass

        # Generate a new deal
        board = self.controller.new_deal(board_num)

        # Store original hands for logging
        self.original_hands = {}
        for seat, hand in board.hands.items():
            self.original_hands[seat] = Hand(cards=list(hand.cards))

        # Register with match controller
        self.match_controller.start_board(board_num, board)

        # Q-Plus semantics: start the four-bot closed room now so it plays this
        # deal at the same time as the human (joined at hand end).
        self._closed_room_async_started = None
        self._begin_parallel_closed_room(board_num)

        # Setup the UI for play
        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction([], board.dealer)
        self.bidding_box.setVisible(True)
        self.right_panel.setVisible(True)
        self._clear_bid_info()
        self._update_available_bids()
        self._update_window_title()

        # Show bid info window
        if self.bid_info_action.isChecked():
            self._apply_bid_info_visibility()

        # Set visibility for players
        for seat in Seat:
            player = self.controller.players[seat]
            visible = player.player_type == PlayerType.HUMAN
            self.table_view.set_hand_visible(seat, visible)

        self.table_view.set_hand_visible(self._user_seat, True)
        self.next_card_btn.setEnabled(False)
        self.next_deal_btn.setVisible(True)

        self.status_label.setText(
            f"Teams Match - Board {board_num}/{self.teams_match.num_boards}: "
            f"{board.dealer.to_char()} deals"
        )

        self._advance_game()

    def _on_closed_room_complete(self, result: BenBoardRun):
        """Handle completion of closed room play."""
        if result:
            print(f"Closed room complete: Board {result.board_number}, "
                  f"Contract: {result.contract.to_str() if result.contract else 'Passed'}, "
                  f"NS Score: {result.ns_score}", flush=True)

            # Log the closed room result
            try:
                self.game_logger.log_board_run(result)
            except Exception as e:
                print(f"Error logging closed room hand: {e}", flush=True)

            # Update status to show closed room is done
            if self.teams_match:
                imp_swing = self.teams_match.get_imp_swing(result.board_number)
                if imp_swing is not None:
                    self.status_label.setText(
                        f"Closed room complete. IMP swing: {imp_swing:+d}"
                    )
        else:
            print("Closed room complete: No result returned", flush=True)

    def _on_view_teams_score(self):
        """Show the teams score dialog."""
        if self.teams_match is None:
            QMessageBox.information(
                self, "No Teams Match",
                "No teams match is active.\n\n"
                "To start a teams match, go to Deal > Match Control\n"
                "and select 'Against Closed Room (BridgeIQ vs BridgeIQ)' under Comparison."
            )
            return

        dialog = TeamsScoreDialog(self.teams_match, self)
        dialog.contract_clicked.connect(self._on_replay_board)
        dialog.exec()

    def _on_replay_board(self, board_num: int, table: BenTable):
        """Show replay viewer for a specific board and table."""
        if self.match_controller is None:
            return

        if table == BenTable.OPEN:
            run = self.match_controller.get_open_room_result(board_num)
        else:
            run = self.match_controller.get_closed_room_result(board_num)

        if run and run.played:
            dialog = ReplayViewDialog(run, self)
            dialog.exec()
        else:
            QMessageBox.information(self, "Not Available",
                                   f"The {table.value} room result for board {board_num} "
                                   "is not yet available.")

    def _on_explain_last_signal(self):
        """View → Explain last signal… — interpret the most recent
        defensive card under the configured signal conventions and
        pop the explanation. Q-Plus's "Display of meaning of
        signals + conventional treatments in defense"."""
        board = self.controller.board
        if (board is None or board.contract is None
                or not (board.tricks or board.current_trick)):
            QMessageBox.information(
                self, "No defensive card yet",
                "Play has to be under way for a signal to interpret.")
            return
        # Find the most recent defensive card. Walk backwards
        # through the current trick, then the completed tricks.
        from backend.signal_explainer import (
            SignalConfig, explain_defensive_card,
        )
        declarer = board.contract.declarer
        defensive_side_ns = not declarer.is_ns()
        last_def_card = None
        last_def_seat = None
        last_def_trick_idx = None
        last_def_position = None
        candidate_tricks = []
        if board.current_trick and board.current_trick.cards:
            candidate_tricks.append(
                (len(board.tricks), board.current_trick))
        for ti, tr in reversed(list(enumerate(board.tricks))):
            candidate_tricks.append((ti, tr))
        for trick_idx, trick in candidate_tricks:
            seat = trick.leader
            for pos, c in enumerate(trick.cards):
                if seat.is_ns() == defensive_side_ns:
                    last_def_card = c
                    last_def_seat = seat
                    last_def_trick_idx = trick_idx
                    last_def_position = pos
                seat = seat.next()
            if last_def_card is not None:
                break
        if last_def_card is None:
            QMessageBox.information(
                self, "No defensive card",
                "No defensive card has been played yet.")
            return
        # Figure out the rest of the context for the explainer.
        trick = (board.tricks[last_def_trick_idx]
                 if last_def_trick_idx < len(board.tricks)
                 else board.current_trick)
        led_suit = trick.cards[0].suit if trick.cards else None
        partner_seat = last_def_seat.partner()
        led_by_partner = (trick.leader == partner_seat)
        trump = (board.contract.suit
                 if board.contract.suit != Suit.NOTRUMP else None)
        # Prior cards by this defender in this suit, this hand.
        prior_in_suit: list = []
        for t in board.tricks[:last_def_trick_idx] + (
                [board.current_trick]
                if (last_def_trick_idx >= len(board.tricks)
                    and board.current_trick is not None) else []):
            if t is None or not t.cards:
                continue
            seat = t.leader
            for c in t.cards:
                if (seat == last_def_seat
                        and c.suit == last_def_card.suit
                        and c is not last_def_card):
                    prior_in_suit.append(c)
                seat = seat.next()
        is_discard = (led_suit is not None
                      and last_def_card.suit != led_suit
                      and last_def_card.suit != trump)
        cfg_mgr = get_config_manager()
        cfg = cfg_mgr.config
        sig_str = (cfg.signal_conventions_ns.signal_string
                   if last_def_seat.is_ns()
                   else cfg.signal_conventions_ew.signal_string)
        sig_cfg = SignalConfig.from_signal_string(sig_str)
        explanation = explain_defensive_card(
            card=last_def_card,
            defender=last_def_seat,
            trick_index=last_def_trick_idx,
            position_in_trick=last_def_position,
            led_by_partner=led_by_partner,
            led_suit=led_suit,
            trump_suit=trump,
            prior_cards_in_suit_by_defender=prior_in_suit,
            is_discard=is_discard,
            signal_config=sig_cfg,
        )
        if explanation is None:
            explanation = ("No clean signal applies to this card "
                           "(forced play or third-and-later card).")
        QMessageBox.information(
            self, f"Signal — {last_def_seat.to_char()}",
            f"Trick {last_def_trick_idx + 1}, position "
            f"{last_def_position + 1}: {last_def_seat.to_char()} "
            f"played\n\n  {explanation}")

    def _on_deal_variations(self):
        """Deal → Variations… — replay the current deal with a
        structural transform applied (swap two hands, exchange two
        suits, rotate the table). Q-Plus's `Deal → Variations`
        feature. The dealer + vulnerability are preserved so the
        auction context is the same — only the cards move.

        Mutates the current board's hands in place and resets the
        auction / play state, matching what `_on_repeat_deal` does
        for an auction-restart replay.
        """
        if not self.controller.board:
            QMessageBox.information(
                self, "No deal", "Start a deal first.")
            return
        from .dialogs.deal_variations import (
            DealVariationsDialog, apply_variation,
        )
        dlg = DealVariationsDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        key = dlg.chosen_key()
        if key is None:
            return
        try:
            new_board = apply_variation(self.controller.board, key)
        except ValueError as ex:
            QMessageBox.warning(
                self, "Variation failed", str(ex))
            return
        # In-place mutation, matching the `_on_repeat_deal` pattern.
        board = self.controller.board
        for s in Seat:
            if s in new_board.hands:
                board.hands[s] = Hand(
                    cards=list(new_board.hands[s].cards))
        board.tricks = []
        board.current_trick = None
        board.declarer_tricks = 0
        board.defense_tricks = 0
        board.auction = []
        board.contract = None
        self.controller.current_phase = 'bidding'
        self.controller.current_seat = board.dealer
        self.controller.declarer = None
        self.controller.dummy = None
        self.original_hands = {
            s: Hand(cards=list(board.hands[s].cards))
            for s in Seat if s in board.hands
        }
        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction([], board.dealer)
        self.bidding_box.setVisible(True)
        self.status_label.setText(
            f"Variation applied ({key}) — replay this hand and "
            "compare to the original")
        self._advance_game()

    def _on_repeat_deal(self):
        """Replay the just-finished deal — Q-Plus `.repeat-f`. Pops
        the Repeat Deal modal to let the user choose whether to
        restart from the auction or skip to play with the same
        contract, who drives the repeat, and how the result lands in
        the scoring table."""
        if not self.controller.board:
            return
        from .dialogs.repeat_deal import RepeatDealDialog
        rows = (list(self.scoring_table.results)
                if getattr(self, 'scoring_table', None) is not None
                else [])
        dlg = RepeatDealDialog(
            parent=self,
            have_first_row=len(rows) >= 1,
            have_second_row=len(rows) >= 2,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # Close the end-of-hand summary so it doesn't sit over the table while
        # the computer replays the deal.
        self._dismiss_end_of_hand_dialogs()
        choice = dlg.get_choice()

        board = self.controller.board
        original = (self.original_hands if self.original_hands
                    else {s: Hand(cards=list(board.hands[s].cards))
                          for s in Seat
                          if s in board.hands})
        # Drop any scoring-table row the user asked us to replace
        # BEFORE we reset state — once the controller wipes the
        # current board the lookup keys change. Equivalent to
        # Q-Plus's "replaces 1./2. played deal".
        if choice.entry_mode == 'replace1' and rows:
            try:
                self.scoring_table.results.remove(rows[0])
            except Exception:
                pass
        elif choice.entry_mode == 'replace2' and len(rows) >= 2:
            try:
                self.scoring_table.results.remove(rows[1])
            except Exception:
                pass

        # Re-install the same hands.
        for s in Seat:
            if s in original:
                board.hands[s] = Hand(cards=list(original[s].cards))
        board.tricks = []
        board.current_trick = None
        board.declarer_tricks = 0
        board.defense_tricks = 0
        self.original_hands = {
            s: Hand(cards=list(board.hands[s].cards))
            for s in Seat if s in board.hands
        }

        if choice.start_with_play and board.contract is not None:
            # Skip auction — keep the contract from the previous run.
            # set_contract_direct rebuilds board.auction from the contract
            # (a single synthesised contract-bid) and sets declarer / dummy /
            # opening leader / play phase. (It builds the Bid with the right
            # is_double/is_redouble kwargs; the old manual build used the
            # non-existent doubled=/redoubled= and crashed.)
            self.controller.set_contract_direct(board.contract)
        else:
            # Restart from the auction.
            board.auction = []
            board.contract = None
            self.controller.current_phase = 'bidding'
            self.controller.current_seat = board.dealer
            self.controller.declarer = None
            self.controller.dummy = None

        # Computer-automatic = autoplay on; stepwise still needs the
        # user to click each card but bots fill in their seats.
        if not choice.by_user:
            try:
                self.autoplay_btn.setChecked(choice.computer_stepwise is False)
            except Exception:
                pass

        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction(
            list(board.auction), board.dealer)
        self.bidding_box.setVisible(
            self.controller.current_phase == 'bidding')
        self.status_label.setText(
            f"Repeat of board {board.board_number} — "
            + ("play phase" if choice.start_with_play
               else "auction restart"))
        self._advance_game()

    def _on_deal_filter(self):
        """Show the deal filter dialog and remember the configured filter so
        subsequent RANDOM deals are reject-sampled to match it (Q-Plus: a deal
        filter is only useful for randomly dealt deals, not tournament deals)."""
        dialog = DealFilterDialog(self)
        if dialog.exec():
            self._active_deal_filter = dialog.get_filter()
            if self._active_deal_filter:
                self.status_label.setText(
                    "Deal filter active — new random deals will match it "
                    "(open the dialog and disable it to clear).")
            else:
                self.status_label.setText("Deal filter cleared.")

    def _deal_passes_filter(self, board, flt) -> bool:
        """True if `board` satisfies the deal-filter dict from DealFilterDialog
        (HCP ranges per seat / N+S / E+W, South & North suit-length min/max, and
        feature flags). Missing constraints pass. Never raises."""
        if not flt:
            return True
        try:
            from backend.models import Seat, Suit, Rank
            hands = board.hands
            seatmap = {'North': Seat.NORTH, 'East': Seat.EAST,
                       'South': Seat.SOUTH, 'West': Seat.WEST}

            def hcp(seat):
                return hands[seat].hcp()

            def lengths(seat):
                d = {Suit.SPADES: 0, Suit.HEARTS: 0,
                     Suit.DIAMONDS: 0, Suit.CLUBS: 0}
                for c in hands[seat].cards:
                    d[c.suit] += 1
                return d

            for name, rng in (flt.get('hcp_ranges') or {}).items():
                lo, hi = rng
                if name in seatmap:
                    v = hcp(seatmap[name])
                elif name == 'N+S':
                    v = hcp(Seat.NORTH) + hcp(Seat.SOUTH)
                elif name == 'E+W':
                    v = hcp(Seat.EAST) + hcp(Seat.WEST)
                else:
                    continue
                if not (lo <= v <= hi):
                    return False

            suitkey = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                       'D': Suit.DIAMONDS, 'C': Suit.CLUBS}
            for who, store in (('South', flt.get('south_lengths')),
                               ('North', flt.get('north_lengths'))):
                if not store:
                    continue
                L = lengths(seatmap[who])
                for sl, suit in suitkey.items():
                    mn = store.get(f"{who}_{sl}_Min", 0)
                    mx = store.get(f"{who}_{sl}_Max", 13)
                    if not (mn <= L[suit] <= mx):
                        return False

            feats = flt.get('features') or {}
            sl = lengths(Seat.SOUTH)
            shape = tuple(sorted(sl.values(), reverse=True))
            ns = hcp(Seat.NORTH) + hcp(Seat.SOUTH)
            if feats.get('south_balanced') and shape not in (
                    (4, 3, 3, 3), (4, 4, 3, 2), (5, 3, 3, 2)):
                return False
            if feats.get('south_5_major') and not (
                    sl[Suit.SPADES] >= 5 or sl[Suit.HEARTS] >= 5):
                return False
            if feats.get('south_2c_opener') and hcp(Seat.SOUTH) < 22:
                return False
            if feats.get('slam_zone') and ns < 33:
                return False
            if feats.get('game_zone') and ns < 25:
                return False
            if feats.get('south_stoppers'):
                for suit in suitkey.values():
                    ranks = {c.rank for c in hands[Seat.SOUTH].cards
                             if c.suit == suit}
                    n = sl[suit]
                    has = (Rank.ACE in ranks
                           or (Rank.KING in ranks and n >= 2)
                           or (Rank.QUEEN in ranks and n >= 3)
                           or (Rank.JACK in ranks and n >= 4))
                    if not has:
                        return False
            return True
        except Exception:
            return True       # never block dealing on a filter bug

    def _on_set_dealer_vul(self):
        """Q-Plus .set-dealer — pick a new dealer and vulnerability
        for the current deal. Resets the auction (changing dealer
        mid-auction would misattribute every bid)."""
        if self.controller.board is None:
            QMessageBox.information(
                self, "Dealer & Vulnerability",
                "Deal a hand first.")
            return
        from .dialogs.dealer_vul import DealerVulnerabilityDialog
        board = self.controller.board
        dlg = DealerVulnerabilityDialog(
            initial_dealer=board.dealer,
            initial_vul=board.vulnerability,
            parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_dealer, new_vul = dlg.get_choice()
        board.dealer = new_dealer
        board.vulnerability = new_vul
        board.auction = []
        board.contract = None
        board.tricks = []
        board.current_trick = None
        board.declarer_tricks = 0
        board.defense_tricks = 0
        self.controller.current_phase = 'bidding'
        self.controller.current_seat = new_dealer
        self.controller.declarer = None
        self.controller.dummy = None
        self.controller.opening_leader = None
        self.bidding_box.clear()
        self.bidding_box.set_auction([], new_dealer)
        self.bidding_box.setVisible(True)
        self.table_view.set_board(board)
        self.status_label.setText(
            f"Dealer {new_dealer.to_char()}, vul {new_vul.value} — "
            "auction reset.")
        self._advance_game()

    def _on_set_board_number(self):
        """Q-Plus .seednum-d/-f — change the board number on the
        current deal. The default 16-board rotation table also
        re-derives dealer + vulnerability from the new number
        (matching Q-Plus's behaviour); the user can opt out via
        the toggle in the dialog if they just want to relabel."""
        if self.controller.board is None:
            QMessageBox.information(
                self, "Set Board Number",
                "Deal a hand first.")
            return
        from .dialogs.dealer_vul import BoardNumberDialog
        board = self.controller.board
        dlg = BoardNumberDialog(
            initial=board.board_number, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_num = dlg.get_board_number()
        board.board_number = new_num
        if dlg.apply_rotation():
            new_dealer, new_vul = BoardState._board_dealer_vuln(new_num)
            board.dealer = new_dealer
            board.vulnerability = new_vul
            board.auction = []
            board.contract = None
            board.tricks = []
            board.current_trick = None
            board.declarer_tricks = 0
            board.defense_tricks = 0
            self.controller.current_phase = 'bidding'
            self.controller.current_seat = new_dealer
            self.controller.declarer = None
            self.controller.dummy = None
            self.bidding_box.clear()
            self.bidding_box.set_auction([], new_dealer)
            self.bidding_box.setVisible(True)
            self._advance_game()
        self.table_view.set_board(board)
        self.status_label.setText(
            f"Board {new_num} — "
            f"dealer {board.dealer.to_char()}, "
            f"vul {board.vulnerability.value}.")
        self._update_window_title()

    def _on_load_pavlicek(self):
        """Load a deal by Pavlicek number"""
        if not self.engine.is_ready:
            QMessageBox.warning(self, "Engine Not Ready",
                              "The bridge engine is not yet initialized.")
            return

        text, ok = QInputDialog.getText(
            self, "Load Pavlicek Deal",
            "Enter Pavlicek deal number (base-72 format, e.g., 6agBeN!sWCn#IdjB):"
        )
        if not ok or not text.strip():
            return

        try:
            deal_number = parse_deal_number(text.strip())
            hands = number_to_deal(deal_number)

            # Create a new board with these hands
            board = BoardState(
                board_number=self.controller.board.board_number + 1 if self.controller.board else 1,
                dealer=Seat.NORTH,  # Default dealer
                vulnerability=Vulnerability.NONE,  # Default vulnerability
                hands=hands
            )

            # Update dealer/vulnerability based on board number
            dealer, vuln = BoardState._board_dealer_vuln(board.board_number)
            board.dealer = dealer
            board.vulnerability = vuln

            self.controller.board = board
            self.controller.current_seat = board.dealer

            # Store original hands for logging
            self.original_hands = {}
            for seat, hand in board.hands.items():
                self.original_hands[seat] = Hand(cards=list(hand.cards))

            self.table_view.set_board(board)
            self.bidding_box.clear()
            self.bidding_box.set_auction([], board.dealer)
            self.bidding_box.setVisible(True)
            self.right_panel.setVisible(True)
            self.next_deal_btn.setVisible(True)
            self.analysis_label.setText("")
            self._clear_bid_info()
            self._update_available_bids()
            self._update_window_title()

            # Show bid info window for bidding phase
            if self.bid_info_action.isChecked():
                self._apply_bid_info_visibility()

            for seat in Seat:
                player = self.controller.players[seat]
                visible = player.player_type == PlayerType.HUMAN
                self.table_view.set_hand_visible(seat, visible)

            self.table_view.set_hand_visible(self._user_seat, True)
            self.next_card_btn.setEnabled(False)

            self.status_label.setText(f"Board {board.board_number}: {board.dealer.to_char()} deals")
            self._refresh_status_strip()
            self._advance_game()

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Deal Number",
                              f"Could not parse deal number: {e}")
        except Exception as e:
            QMessageBox.warning(self, "Error",
                              f"Error loading deal: {e}")

    def _on_convert_deal(self):
        """Show deal converter dialog for hand code / PBN conversion."""
        dialog = DealConverterDialog(self)

        # If we have a current deal, populate it
        if self.controller.board and self.original_hands:
            dialog.set_deal(self.original_hands)

        # Connect load signal
        dialog.load_deal.connect(self._load_converted_deal)
        dialog.exec()

    def _load_converted_deal(self, board: BoardState):
        """Load a deal from the converter dialog."""
        self._load_entered_board(board)

    def _on_enter_deal(self):
        """Show deal entry dialog"""
        dialog = DealEntryDialog(self)
        if dialog.exec():
            board = dialog.get_board()
            if board:
                if not hasattr(self, '_entered_deals'):
                    self._entered_deals = []
                self._entered_deals.append(board)
                self._load_entered_board(board)

    def _load_entered_board(self, board: BoardState):
        """Load a manually entered board"""
        if not self.engine.is_ready:
            QMessageBox.warning(self, "Engine Not Ready",
                              "The bridge engine is not yet initialized.")
            return

        self.controller.board = board
        self.controller.current_phase = 'bidding'
        self.controller.current_seat = board.dealer

        # Store original hands for logging
        self.original_hands = {}
        for seat, hand in board.hands.items():
            self.original_hands[seat] = Hand(cards=list(hand.cards))

        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction([], board.dealer)
        self.bidding_box.setVisible(True)
        self.right_panel.setVisible(True)
        self.next_deal_btn.setVisible(True)
        self.analysis_label.setText("")
        self._clear_bid_info()
        self._update_available_bids()
        self._update_window_title()

        # Show bid info window for bidding phase
        if self.bid_info_action.isChecked():
            self._apply_bid_info_visibility()

        for seat in Seat:
            player = self.controller.players[seat]
            visible = player.player_type == PlayerType.HUMAN
            self.table_view.set_hand_visible(seat, visible)

        self.table_view.set_hand_visible(self._user_seat, True)
        self.next_card_btn.setEnabled(False)

        self.status_label.setText(f"Board {board.board_number}: {board.dealer.to_char()} deals")
        self._refresh_status_strip()
        self._advance_game()

    def _on_save_entered_deals(self):
        """Save the running list of manually-entered deals to a PBN
        file. Deals enter the list each time the user accepts the
        Deal Entry dialog; this dump preserves them all in one file
        (Q-Plus `.save-deal-a` semantics)."""
        deals = list(getattr(self, '_entered_deals', []))
        if not deals:
            QMessageBox.information(
                self, "Save Deals",
                "No manually-entered deals yet. Use "
                "Own Deals → Enter Deal first.")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Entered Deals", "DATA/",
            "PBN Files (*.pbn);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.pbn'):
            filename = filename + '.pbn'
        try:
            from backend.pbn_exporter import PBNExporter
            from pathlib import Path
            PBNExporter().export_session(deals, Path(filename))
            self.status_label.setText(
                f"Saved {len(deals)} deal(s) to "
                f"{os.path.basename(filename)}")
        except Exception as ex:
            QMessageBox.warning(
                self, "Save failed",
                f"Could not write PBN file:\n{ex!r}")

    def _on_use_own_deals(self):
        """Load a file of own deals"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open Deals File", "DATA/",
            "PBN Files (*.pbn);;BDL Files (*.bdl);;All Files (*)"
        )
        if filename:
            try:
                self._on_open_file_path(filename)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not load file: {e}")

    def _on_open_file_path(self, filepath: str):
        """Open a deal file by path — supports PBN and BDL with replay and commentary."""
        from pathlib import Path
        p = Path(filepath)

        if p.suffix.lower() == '.bdl':
            try:
                from backend.bdl_reader import BDLReader
                reader = BDLReader()
                deals = reader.read_file(p)
                if not deals:
                    QMessageBox.warning(self, "BDL Error", "No deals found in file.")
                    return

                # Show a BDL replay dialog with all deals
                self._show_bdl_replay(deals, p.name)
                self.status_label.setText(f"Loaded {len(deals)} deal(s) from {p.name}")
            except Exception as exc:
                QMessageBox.warning(self, "BDL Error", str(exc))

        elif p.suffix.lower() == '.pbn':
            try:
                from backend.pbn_exporter import PBNParser
                boards = PBNParser.parse_file(str(p))
                if boards:
                    self.status_label.setText(f"Loaded {len(boards)} board(s) from {p.name}")
                else:
                    QMessageBox.warning(self, "PBN Error", "No boards found in file.")
            except Exception as exc:
                QMessageBox.warning(self, "PBN Error", str(exc))

    def _show_bdl_replay(self, deals, filename):
        """Show a dialog to browse and replay deals from a BDL file."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QTextEdit, QPushButton, QLabel, QSplitter
        from PyQt6.QtCore import Qt

        dialog = QDialog(self)
        dialog.setWindowTitle(f"BDL Replay — {filename}")
        dialog.resize(900, 650)

        layout = QVBoxLayout(dialog)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: deal list
        list_widget = QListWidget()
        for i, deal in enumerate(deals):
            label = f"Deal {i+1}"
            if deal.contract:
                label += f" — {deal.contract}"
            if deal.commentary:
                label += " [+commentary]"
            list_widget.addItem(label)
        splitter.addWidget(list_widget)

        # Right: detail view
        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setFontFamily("monospace")
        splitter.addWidget(detail)

        splitter.setSizes([250, 650])
        layout.addWidget(splitter)

        def show_deal(idx):
            if idx < 0 or idx >= len(deals):
                return
            deal = deals[idx]
            text = []
            text.append(f"{'='*50}")
            text.append(f"Deal {idx+1}")
            if deal.pavlicek_id:
                text.append(f"Pavlicek ID: {deal.pavlicek_id}")
            text.append(f"Dealer: {deal.dealer.name}    Vulnerability: {deal.vulnerability.name}")
            text.append("")

            # Show hands
            for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
                hand = deal.hands.get(seat)
                if hand:
                    suits = []
                    for suit in [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]:
                        suit_cards = sorted([c for c in hand.cards if c.suit == suit],
                                            key=lambda c: c.rank, reverse=True)
                        symbol = {Suit.SPADES: '♠', Suit.HEARTS: '♥',
                                  Suit.DIAMONDS: '♦', Suit.CLUBS: '♣'}[suit]
                        suits.append(f"{symbol} {''.join(c.rank.to_char() for c in suit_cards)}")
                    text.append(f"  {seat.name:5s}: {' '.join(suits)}")

            text.append("")
            if deal.contract:
                text.append(f"Contract: {deal.contract}")
            if deal.tricks_made:
                text.append(f"Tricks made: {deal.tricks_made}")
            if deal.ns_score or deal.ew_score:
                text.append(f"Score: NS={deal.ns_score}  EW={deal.ew_score}")

            if deal.commentary:
                text.append("")
                text.append(f"{'─'*50}")
                text.append("CLAUDE ANALYSIS:")
                text.append(deal.commentary)

            detail.setPlainText("\n".join(text))

        list_widget.currentRowChanged.connect(show_deal)
        if deals:
            list_widget.setCurrentRow(0)

        # Close button
        btn_layout = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.exec()

    def _on_configure_players(self):
        """Show player configuration dialog"""
        dialog = PlayerConfigDialog(self.controller.players, self)
        if dialog.exec():
            self.controller.players = dialog.get_players()

    def _on_configure_systems(self):
        """Show bidding systems dialog.

        Seeds the dialog with the user's current preferred system,
        which lives on ``preferences.native_bidding_system`` (and is
        also what the rule-based bidder reads on every bid). Falls
        back to whatever was last picked in this session, or SAYC.
        """
        try:
            prefs = get_config_manager().config.preferences
            current_system = getattr(
                self, '_current_bidding_system',
                prefs.native_bidding_system or 'SAYC')
        except Exception:
            current_system = getattr(self, '_current_bidding_system', 'SAYC')
        dialog = BiddingSystemDialog(self, current_system)
        if dialog.exec():
            new_system = dialog.get_system()
            self._current_bidding_system = new_system
            self._ns_bidding_system = dialog.get_ns_system()
            self._ew_bidding_system = dialog.get_ew_system()
            self._active_conventions = dialog.get_conventions()

            # Persist to preferences so the rule-based bidder picks
            # up the new choice on its next call, and so the bid-info
            # window's system banner is refreshed. Per-pair overrides
            # are stored alongside so a teaching session can run e.g.
            # Precision90M at NS and SAYC at EW — when both pairs
            # share a system we clear the overrides to keep the
            # stored config tidy.
            try:
                cm = get_config_manager()
                cm.config.preferences.native_bidding_system = new_system
                ns_sys = self._ns_bidding_system or new_system
                ew_sys = self._ew_bidding_system or new_system
                if ns_sys == ew_sys:
                    cm.config.preferences.ns_bidding_system = ""
                    cm.config.preferences.ew_bidding_system = ""
                else:
                    cm.config.preferences.ns_bidding_system = ns_sys
                    cm.config.preferences.ew_bidding_system = ew_sys
                cm.save_preferences()
            except Exception:
                pass
            # Drop the cached BiddingSystem so the bid-info window
            # repaints with the new system on its next refresh.
            self._refresh_active_system()

            # Apply the bidding system to the engine
            self.status_label.setText(f"Setting bidding system: {new_system}...")
            QApplication.processEvents()

            if self.engine.set_bidding_system(new_system):
                # Show N/S and E/W systems in status bar
                ns_sys = self._ns_bidding_system
                ew_sys = self._ew_bidding_system
                if ns_sys == ew_sys:
                    self.status_label.setText(f"Bidding system: {new_system}")
                else:
                    self.status_label.setText(f"Systems: N/S={ns_sys}, E/W={ew_sys}")
            else:
                self.status_label.setText(f"Failed to set bidding system")

    def _on_preferences(self):
        """Show preferences dialog"""
        dialog = PreferencesDialog(self)
        # Connect signal for Apply button (refreshes colors while dialog is open)
        dialog.settings_applied.connect(self._refresh_suit_colors)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Also refresh on OK
            self._refresh_suit_colors()

    def _refresh_suit_colors(self):
        """Refresh UI elements after preference change."""
        # Refresh bidding box colors for 4-color suit mode changes
        self.bidding_box.refresh_colors()
        # Also refresh table view if it has the method
        if hasattr(self.table_view, 'refresh_colors'):
            self.table_view.refresh_colors()
        # Update analysis panel visibility based on preference
        self.analysis_label.setVisible(self.config_manager.config.preferences.show_ben_bid_analysis)
        # Keep the click-to-explain affordance in sync with its preference.
        self._sync_explain_enabled()
        # Bidding-system selection may have changed — re-tag the
        # information-about-bids window with the new system's labels.
        self._refresh_active_system()
        # Apply the AI & Network toggles live.
        self._apply_qplus_visibility()
        self._apply_bid_info_visibility()
        # Carding defaults may have changed — refresh the instrumented view's
        # header selectors from the shared Preferences config.
        tv = getattr(self, "teaching_view", None)
        if tv is not None and hasattr(tv, "reload_carding_defaults"):
            tv.reload_carding_defaults()

    def _on_show_all_hands(self):
        """Toggle showing all hands"""
        for seat in Seat:
            self.table_view.set_hand_visible(seat, True)
        # If the instrumented view is up, more face-up hands means more
        # exact cards to show — refresh straight away.
        self._refresh_teaching_view()

    def _on_toggle_teaching_view(self, checked: bool):
        """Switch the table area between the normal table (page 0) and the
        instrumented teaching view (page 1)."""
        stack = getattr(self, 'table_stack', None)
        if stack is None:
            return
        self.teaching_btn.setText("Default view" if checked else "Analysis view")
        stack.setCurrentIndex(1 if checked else 0)
        if checked:
            self._refresh_teaching_view()
            self._teaching_timer.start()
        else:
            self._teaching_timer.stop()

    def _refresh_teaching_view(self):
        """Repaint the instrumented view from the current play state. Cheap
        no-op unless that view is the one currently showing."""
        view = getattr(self, 'teaching_view', None)
        stack = getattr(self, 'table_stack', None)
        if view is None or stack is None or stack.currentIndex() != 1:
            return
        board = self.controller.board if getattr(self, 'controller', None) else None
        contract = getattr(board, 'contract', None) if board else None
        declarer = getattr(self.controller, 'declarer', None)
        dummy = getattr(self.controller, 'dummy', None)
        if declarer is None and contract is not None:
            declarer = contract.declarer
        if dummy is None and declarer is not None:
            dummy = declarer.partner()
        try:
            visible = self.table_view.face_up_seats()
        except Exception:
            visible = set()
        try:
            ns_sys = self._active_bidding_system('NS')
            ew_sys = self._active_bidding_system('EW')
        except Exception:
            ns_sys = ew_sys = None
        try:
            view.refresh(board=board, contract=contract, declarer=declarer,
                         dummy=dummy, visible_seats=visible,
                         ns_system=ns_sys, ew_system=ew_sys)
        except Exception as e:
            print(f"[teaching view] refresh failed: {e}", flush=True)

    def _embed_bid_info_panel(self):
        """Reparent the bid-info panel onto the bidding screen (table view) and
        pin it to the upper-left corner as an overlay."""
        dock = getattr(self, "bid_info_dock", None)
        host = getattr(self, "table_view", None)
        if dock is None or host is None:
            return
        try:
            dock.setParent(host)
            dock.move(10, 10)
            dock.raise_()
        except Exception:
            pass
        self._place_bidding_overlays()

    def _place_bidding_overlays(self):
        """Lay the two bidding overlays around the felt: the bid-info panel
        top-left, as tall as the room above South's hand (most of it for the
        available bids), and the bidding box just right of the felt, level
        with the auction, so the buttons sit next to the bidding history."""
        host = getattr(self, 'table_view', None)
        if host is None:
            return
        try:
            ta = host.trick_area
            green = ta._green_rect.translated(ta.mapTo(host, QPoint(0, 0)))
            bottom = host.south_label.mapTo(host, QPoint(0, 0)).y() - 8
        except Exception:
            return
        if bottom < 200:
            bottom = host.height() - 10
        dock = getattr(self, 'bid_info_dock', None)
        if dock is not None and dock.parent() is host:
            width = max(320, min(440, green.left() - 24))
            dock.setGeometry(10, 10, width, max(260, bottom - 10))
            dock.raise_()
        bb = getattr(self, 'bidding_box', None)
        if bb is not None and bb.parent() is host:
            hint = bb.sizeHint()
            x = min(green.right() + 14, host.width() - hint.width() - 10)
            y = green.center().y() - hint.height() // 2
            y = max(10, min(y, bottom - hint.height()))
            bb.setGeometry(x, y, hint.width(), hint.height())
            bb.raise_()

    def _bid_info_pref(self) -> bool:
        try:
            from backend.config import get_config_manager
            return bool(getattr(get_config_manager().config.preferences,
                                "show_bid_info_panel", True))
        except Exception:
            return True

    def _apply_bid_info_visibility(self):
        """The panel shows only DURING BIDDING, and only when BOTH the
        Preferences toggle is on AND the F3 'Bidding Information' action is
        checked. It is always hidden during card play / finished."""
        dock = getattr(self, "bid_info_dock", None)
        if dock is None:
            return
        want = self._bid_info_pref()
        act = getattr(self, "bid_info_action", None)
        if act is not None:
            act.setEnabled(want)
            want = want and act.isChecked()
        phase = getattr(getattr(self, "controller", None), "current_phase", None)
        if phase != 'bidding':
            want = False                 # never overlay the cards during play
        dock.setVisible(want)
        if want:
            dock.raise_()
        # Shift the table right while the panel overlays the top-left, so the
        # West column / label isn't clipped behind it; restore when hidden.
        if want:
            self._place_bidding_overlays()

    def _on_toggle_bid_info(self, checked: bool):
        """Toggle the embedded bidding-information panel (respects the
        Preferences 'show bid-info panel' option)."""
        self._apply_bid_info_visibility()

    def _active_bidding_system(self, side: str = None):
        """Resolve the configured `BiddingSystem` spec.

        When ``side`` is 'NS' or 'EW', return the per-pair override
        (or the shared default if no override is set). Cached
        independently per side; `_refresh_active_system` clears
        every cache so a user-driven switch lands the next time the
        bid-info window is redrawn.
        """
        if not hasattr(self, '_cached_bidding_systems'):
            self._cached_bidding_systems = {}
        key = side or 'default'
        cached = self._cached_bidding_systems.get(key)
        if cached is not None:
            return cached
        try:
            from backend.bid_descriptions import system_for_prefs
            prefs = get_config_manager().config.preferences
            spec = system_for_prefs(prefs, side=side)
        except Exception:
            from backend.bidding_systems import get_system
            spec = get_system('SAYC')
        self._cached_bidding_systems[key] = spec
        return spec

    def _refresh_active_system(self):
        """Drop the cached system specs; next access reloads from prefs."""
        self._cached_bidding_systems = {}
        # If the bid info window is up, refresh the header + table.
        if getattr(self, 'bid_info_dock', None) is not None:
            self._refresh_bid_info_system_label()
            try:
                self._refresh_bid_history_descriptions()
            except Exception:
                pass
            self._update_available_bids()
        # Keep the bottom status strip's NS/EW systems in sync.
        self._refresh_status_strip()

    @staticmethod
    def _format_system_summary(spec) -> str:
        """One-paragraph description of a BiddingSystem spec for the
        Claude hint prompt — pulls numeric ranges straight from the
        dataclass fields so Precision-70's 13-15 1NT comes through
        when the user is on that variant, not the SAYC 15-17.
        """
        if spec.strong_open_call == "1C":
            return (
                f"strong-club: 1♣ = {spec.strong_open_min_hcp}+ any shape; "
                f"1♦ = {spec.one_diamond_min_hcp}-"
                f"{spec.one_diamond_max_hcp} (catch-all); 1♥/1♠ = "
                f"{spec.precision_two_clubs_min_hcp}-"
                f"{spec.one_nt_max_hcp} with 5+ in the suit; "
                f"1NT = {spec.one_nt_min_hcp}-"
                f"{spec.one_nt_max_hcp} balanced; "
                f"2♣ = {spec.precision_two_clubs_min_hcp}-"
                f"{spec.precision_two_clubs_max_hcp} with 6+ clubs; "
                f"2♦ = Precision three-suiter "
                f"({spec.precision_two_diamonds_shape}); "
                f"2♥/2♠ = weak two; 1♣-1♦ = 0-7 negative; positive "
                f"responses 8+ HCP; RKC = {spec.rkc_variant}"
            )
        return (
            f"5-card majors; strong 1NT {spec.one_nt_min_hcp}-"
            f"{spec.one_nt_max_hcp} balanced; "
            f"2NT {spec.two_nt_min_hcp}-{spec.two_nt_max_hcp} balanced; "
            f"2♣ Stayman; Jacoby transfers; weak 2♦/2♥/2♠ "
            f"({spec.weak_two_min_hcp}-{spec.weak_two_max_hcp}); "
            f"strong 2♣ = {spec.strong_open_min_hcp}+ HCP; "
            f"2/1 ≥ {spec.two_over_one_min_hcp}; "
            f"Blackwood 4NT for aces; negative doubles through 2♠; "
            f"standard takeout doubles; RKC = {spec.rkc_variant}"
        )

    def _refresh_bid_info_system_label(self):
        """Update the bid-info window's system header to match prefs.

        Shows ONE line when both pairs share the system, two lines
        ("NS: …" / "EW: …") otherwise — matches the dialog's "N/S vs
        E/W" tab so users can verify both sides at a glance.
        """
        lbl = getattr(self, 'bid_info_system_label', None)
        if lbl is None:
            return
        ns = self._active_bidding_system('NS')
        ew = self._active_bidding_system('EW')
        if ns.name == ew.name:
            desc = ns.description or ns.name
            lbl.setText(f"System: {ns.name} — {desc}")
        else:
            ns_desc = ns.description or ns.name
            ew_desc = ew.description or ew.name
            lbl.setText(
                f"NS: {ns.name} — {ns_desc}\n"
                f"EW: {ew.name} — {ew_desc}"
            )

    def _get_bid_interpretation(self, bid: Bid, auction: List[Bid], bidder: Seat) -> tuple:
        """Get point range and meaning for a bid based on context.
        Returns (points_str, suit_length_str, help_text, is_artificial).

        Delegates to `backend.bid_descriptions.describe_bid`, which
        reads the active `BiddingSystem` spec so labels reflect the
        user's chosen system (SAYC / Precision / Acol / 2-over-1 / …).
        """
        try:
            from backend.bid_descriptions import describe_bid
            sys_ = self._active_bidding_system()
            dealer = (self.controller.board.dealer
                      if self.controller.board is not None
                      else Seat.NORTH)
            return describe_bid(bid, list(auction), bidder, dealer, sys_)
        except Exception:
            # Fall through to the legacy SAYC-hardcoded labels if the
            # spec-driven describer chokes for any reason.
            pass

        if bid.is_pass:
            # Pass after partner opened might be different from initial pass
            if len(auction) == 0:
                return ("0-11", "", "No opening values", False)
            # Check if it's a forcing pass situation or just weak
            has_opening = any(not b.is_pass and not b.is_double and not b.is_redouble for b in auction)
            if has_opening:
                return ("0-5", "", "Weak, no bid", False)
            return ("0-11", "", "Pass", False)

        if bid.is_double:
            # Check context - takeout or penalty
            last_suit_bid = None
            for b in reversed(auction):
                if not b.is_pass and not b.is_double and not b.is_redouble:
                    last_suit_bid = b
                    break
            if last_suit_bid and len(auction) <= 3:
                return ("12+", "3+ other suits", "Takeout double", False)
            return ("10+", "", "Penalty/Cooperative", False)

        if bid.is_redouble:
            return ("10+", "", "Strength, redouble", False)

        # Regular suit/NT bids
        level = bid.level
        suit = bid.suit

        # Opening bids
        if len(auction) == 0 or all(b.is_pass for b in auction):
            if level == 1:
                if suit == Suit.CLUBS:
                    return ("12-21", "♣ 3+", "1♣ opening, 3+ clubs", False)
                elif suit == Suit.DIAMONDS:
                    return ("12-21", "♦ 3+", "1♦ opening, 3+ diamonds", False)
                elif suit == Suit.HEARTS:
                    return ("12-21", "♥ 5+", "1♥ opening, 5+ hearts", False)
                elif suit == Suit.SPADES:
                    return ("12-21", "♠ 5+", "1♠ opening, 5+ spades", False)
                elif suit == Suit.NOTRUMP:
                    return ("15-17", "Balanced", "1NT opening, balanced", False)
            elif level == 2:
                if suit == Suit.CLUBS:
                    return ("22+", "Any", "Strong 2♣, game force", True)
                elif suit == Suit.NOTRUMP:
                    return ("20-21", "Balanced", "2NT opening", False)
                else:
                    sym = suit.symbol()
                    return ("5-10", f"{sym} 6+", f"Weak 2, 6+ {sym}", False)
            elif level == 3:
                sym = suit.symbol()
                return ("5-10", f"{sym} 7+", f"Preempt, 7+ {sym}", False)

        # Responses to 1-level openings
        opening_bid = None
        for b in auction:
            if not b.is_pass and not b.is_double and not b.is_redouble:
                opening_bid = b
                break

        if opening_bid and opening_bid.level == 1:
            # Responding to 1 of a suit
            if level == 1 and suit != Suit.NOTRUMP:
                sym = suit.symbol()
                return ("6+", f"{sym} 4+", f"New suit, 4+ {sym}", False)
            elif level == 1 and suit == Suit.NOTRUMP:
                return ("6-10", "Balanced", "1NT response", False)
            elif level == 2:
                if suit == opening_bid.suit:
                    return ("6-10", f"{suit.symbol()} 3+", "Simple raise", False)
                elif suit == Suit.NOTRUMP:
                    return ("11-12", "Balanced", "Invitational", False)
                else:
                    sym = suit.symbol()
                    return ("10+", f"{sym} 4+", f"New suit, game force", False)
            elif level == 3:
                if suit == opening_bid.suit:
                    return ("10-12", f"{suit.symbol()} 4+", "Limit raise", False)

        # Check for Stayman (2♣ over 1NT)
        if bid.level == 2 and bid.suit == Suit.CLUBS:
            for i, b in enumerate(auction):
                if b.level == 1 and b.suit == Suit.NOTRUMP:
                    return ("8+", "4+ major", "Stayman, asks for 4-card major", True)

        # Check for Jacoby transfers (2♦/2♥ over 1NT)
        if bid.level == 2 and bid.suit in (Suit.DIAMONDS, Suit.HEARTS):
            for b in auction:
                if b.level == 1 and b.suit == Suit.NOTRUMP:
                    if bid.suit == Suit.DIAMONDS:
                        return ("0+", "♥ 5+", "Transfer to hearts", True)
                    elif bid.suit == Suit.HEARTS:
                        return ("0+", "♠ 5+", "Transfer to spades", True)

        # Default interpretation
        sym = suit.symbol() if suit != Suit.NOTRUMP else "NT"
        if suit == Suit.NOTRUMP:
            pts = f"{10 + level * 3}-{12 + level * 3}"
            return (pts, "Balanced", f"{level}NT bid", False)
        else:
            pts = f"{8 + level * 2}+"
            length = "4+" if level <= 2 else "5+"
            return (pts, f"{sym} {length}", f"{level}{sym} bid", False)

    def _add_bid_to_info(self, seat: Seat, bid: Bid, points_est: str = "", help_text: str = ""):
        """Add a bid to the info window history"""
        # Get interpretation if not provided
        if not points_est and not help_text:
            auction_before = self.controller.board.auction[:-1] if self.controller.board else []
            points_est, suit_len, help_text, is_artificial = self._get_bid_interpretation(bid, auction_before, seat)
            if suit_len:
                points_est = f"{points_est}  {suit_len}"

        row_frame = QFrame()
        row_frame.setStyleSheet("background-color: #fff; border-bottom: 1px solid #ddd;")
        row_layout = QHBoxLayout(row_frame)
        row_layout.setContentsMargins(5, 3, 5, 3)
        row_layout.setSpacing(0)

        # Bid column: "E: 1♥" style
        bid_text = f"{seat.to_char()}: {bid.symbol()}"
        bid_lbl = QLabel(bid_text)
        bid_lbl.setFont(QFont("Arial", 14))
        bid_lbl.setFixedWidth(100)
        if hasattr(bid, 'suit') and bid.suit is not None:
            from .styles import get_suit_color
            suit_names = {Suit.SPADES: 'spades', Suit.HEARTS: 'hearts',
                         Suit.DIAMONDS: 'diamonds', Suit.CLUBS: 'clubs'}
            color = get_suit_color(suit_names.get(bid.suit, 'spades'))
            bid_lbl.setStyleSheet(f"color: {color};")
        row_layout.addWidget(bid_lbl)

        # Points column
        pts_lbl = QLabel(points_est if points_est else "-")
        pts_lbl.setFont(QFont("Arial", 14))
        pts_lbl.setFixedWidth(100)
        row_layout.addWidget(pts_lbl)

        # Help column
        help_lbl = QLabel(help_text if help_text else "-")
        help_lbl.setFont(QFont("Arial", 14))
        help_lbl.setWordWrap(True)
        row_layout.addWidget(help_lbl, stretch=1)

        # Insert before the stretch
        self.bid_history_layout.insertWidget(self.bid_history_layout.count() - 1, row_frame)

    def _update_available_bids(self):
        """Update the list of available bids with meanings for the next bidder.

        The label and the interpretation queries both use the actual
        next-to-bid seat (computed from `board.get_current_bidder()`),
        so the meanings reflect what each bid would mean *for the seat
        that is about to bid* — that matters because partner-side
        conventional responses differ from overcalls of the same call.
        """
        # Clear existing
        while self.available_bids_layout.count():
            item = self.available_bids_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.controller.board:
            lbl = QLabel("-")
            lbl.setFont(QFont("Arial", 10))
            self.available_bids_layout.addWidget(lbl)
            return

        if self.controller.current_phase != 'bidding':
            lbl = QLabel("Bidding complete")
            lbl.setFont(QFont("Arial", 10))
            self.available_bids_layout.addWidget(lbl)
            return

        # Get current auction state
        auction = self.controller.board.auction
        try:
            next_seat = self.controller.board.get_current_bidder()
        except Exception:
            next_seat = Seat.SOUTH
        if getattr(self, 'avail_label', None) is not None:
            self.avail_label.setText(f"Available bids for {next_seat.name.title()}:")
        min_level = 0
        min_suit_idx = -1
        suit_order = [Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES, Suit.NOTRUMP]

        # Check for doubles available
        can_double = False
        can_redouble = False
        last_non_pass = None
        passes_after_last = 0

        for bid in reversed(auction):
            if bid.is_pass:
                passes_after_last += 1
            elif bid.is_double:
                # 0 or 2 passes since: an opponent doubled (1 = partner).
                can_redouble = passes_after_last in (0, 2)
                break
            elif bid.is_redouble:
                break
            else:
                last_non_pass = bid
                can_double = passes_after_last in (0, 2)
                break

        # Find minimum valid bid
        for bid in auction:
            if not bid.is_pass and not bid.is_double and not bid.is_redouble:
                min_level = bid.level
                min_suit_idx = suit_order.index(bid.suit)

        # Build list of available bids with meanings
        available_bids = []

        # Pass is always available
        pts, suit_len, help_txt, _ = self._get_bid_interpretation(Bid.make_pass(), auction, next_seat)
        available_bids.append(("Pass", pts, help_txt))

        # Double if available
        if can_double:
            pts, suit_len, help_txt, _ = self._get_bid_interpretation(Bid.make_double(), auction, next_seat)
            available_bids.append(("X", pts, help_txt))

        # Redouble if available
        if can_redouble:
            pts, suit_len, help_txt, _ = self._get_bid_interpretation(Bid.make_redouble(), auction, next_seat)
            available_bids.append(("XX", pts, help_txt))

        # Suit bids - show the next few levels
        count = 0
        for level in range(1, 8):
            for suit in suit_order:
                suit_idx = suit_order.index(suit)
                if level > min_level or (level == min_level and suit_idx > min_suit_idx):
                    bid = Bid(level=level, suit=suit)
                    pts, suit_len, help_txt, is_art = self._get_bid_interpretation(bid, auction, next_seat)
                    bid_str = bid.symbol()
                    if suit_len == "Balanced":
                        suit_len = "bal."
                    display_pts = f"{pts}  {suit_len}" if suit_len else pts
                    if is_art:
                        help_txt = f"[Art] {help_txt}"
                    available_bids.append((bid_str, display_pts, help_txt))
                    count += 1
                    if count >= 12:  # Limit display
                        break
            if count >= 12:
                break

        # Display bids
        for bid_str, pts, help_txt in available_bids:
            row = QHBoxLayout()
            row.setSpacing(5)
            row.setContentsMargins(2, 1, 2, 1)

            bid_lbl = QLabel(bid_str)
            bid_lbl.setFont(QFont("Arial", 14, QFont.Weight.Bold))
            bid_lbl.setFixedWidth(48)
            # Color suits using centralized colors
            from .styles import get_suit_color
            if '♠' in bid_str:
                bid_lbl.setStyleSheet(f"color: {get_suit_color('spades')};")
            elif '♥' in bid_str:
                bid_lbl.setStyleSheet(f"color: {get_suit_color('hearts')};")
            elif '♦' in bid_str:
                bid_lbl.setStyleSheet(f"color: {get_suit_color('diamonds')};")
            elif '♣' in bid_str:
                bid_lbl.setStyleSheet(f"color: {get_suit_color('clubs')};")
            row.addWidget(bid_lbl)

            pts_lbl = QLabel(pts)
            pts_lbl.setFont(QFont("Arial", 13))
            pts_lbl.setFixedWidth(110)
            row.addWidget(pts_lbl)

            help_lbl = QLabel(help_txt)
            help_lbl.setFont(QFont("Arial", 13))
            help_lbl.setWordWrap(True)
            row.addWidget(help_lbl, stretch=1)

            row_widget = QWidget()
            row_widget.setLayout(row)
            self.available_bids_layout.addWidget(row_widget)
        self.available_bids_layout.addStretch()

    def _clear_bid_info(self):
        """Clear the bid info window"""
        while self.bid_history_layout.count() > 1:
            item = self.bid_history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Clear available bids - don't call _update_available_bids here to avoid issues
        while self.available_bids_layout.count():
            item = self.available_bids_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_bid_history_descriptions(self):
        """Rebuild the bid-history rows using the active bidding system.

        Used when the user switches bidding system mid-auction: the
        existing rows were rendered with the old labels and need to be
        retagged with what each bid means under the new system.
        """
        if self.controller.board is None:
            return
        # Wipe the history layout but keep the trailing stretch.
        while self.bid_history_layout.count() > 1:
            item = self.bid_history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        auction = list(self.controller.board.auction)
        dealer = self.controller.board.dealer
        bidder = dealer
        for b in auction:
            self._add_bid_to_info(bidder, b, "", "")
            bidder = bidder.next()

    def _on_view_rubber(self):
        """View → Rubber Scorecard. Opens the non-modal Q-Plus
        .score-rubber scorecard dialog backed by self.rubber_score
        (lazily created on first access)."""
        if not hasattr(self, 'rubber_score') or self.rubber_score is None:
            from backend.scoring import RubberScore
            self.rubber_score = RubberScore()
        # Cache the dialog so re-opening keeps the same view rather
        # than spawning a stack of widgets behind the table.
        existing = getattr(self, '_rubber_dialog', None)
        try:
            still_visible = (existing is not None
                             and existing.isVisible())
        except RuntimeError:
            still_visible = False
            existing = None
        if not still_visible:
            from .dialogs.minibridge_dialog import RubberScoringDialog
            existing = RubberScoringDialog(
                self.rubber_score, parent=self)
            existing.setModal(False)
            self._rubber_dialog = existing
        else:
            existing.set_rubber(self.rubber_score)
        existing.show()
        existing.raise_()
        existing.activateWindow()

    def _on_restore_score_table(self):
        """File → Restore Score Table. Currently routes to the rubber
        loader (rubber is the only score-table format that needs a
        cross-session resume — IMP/MP tables resume via Match
        Control)."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path
        default_dir = (Path(__file__).parent.parent
                       / "DATA" / "RUBBERS")
        if not default_dir.exists():
            default_dir = Path.home()
        filename, _ = QFileDialog.getOpenFileName(
            self, "Restore score table",
            str(default_dir),
            "Rubber scorecard (*.json);;All files (*)",
        )
        if not filename:
            return
        try:
            from backend.scoring import RubberScore
            self.rubber_score = RubberScore.load(filename)
        except Exception as ex:
            QMessageBox.warning(
                self, "Restore failed",
                f"Could not load score table:\n{ex}")
            return
        # Pop the rubber dialog so the user can see the loaded state.
        self._on_view_rubber()

    def _on_show_scores(self):
        """Show score table.

        Shown non-modally (`show()` not `exec()`): a modal dialog blocks
        the main thread's QTcpSocket readyRead processing on both host
        and guest, so heartbeats stop arriving and the connection times
        out. Keep a reference on `self` so the dialog isn't garbage-
        collected as soon as we return.

        If a teams match is active, shows the TeamsScoreDialog with
        clickable contracts to view replays. Otherwise shows the
        regular ScoringTableDialog.
        """
        if self.teams_match is not None:
            self._scores_dialog = TeamsScoreDialog(self.teams_match, self)
            self._scores_dialog.contract_clicked.connect(self._on_replay_board)
        else:
            self._scores_dialog = ScoringTableDialog(self.scoring_table, self)
        self._scores_dialog.show()
        self._scores_dialog.raise_()

    def _on_dd_analysis(self):
        """Run double dummy analysis"""
        if not self.controller.board:
            self.status_label.setText("No deal loaded - press Ctrl+N for new deal")
            return

        # Check if all hands are available
        board = self.controller.board
        missing_hands = []
        for seat_char in ['N', 'E', 'S', 'W']:
            from backend.models import Seat
            seat = Seat.from_char(seat_char)
            if seat not in board.hands or not board.hands[seat].cards:
                missing_hands.append(seat_char)

        if missing_hands:
            self.status_label.setText(f"DD analysis requires all hands (missing: {', '.join(missing_hands)})")
            return

        self.status_label.setText("Running double dummy analysis...")
        QApplication.processEvents()

        try:
            results = self.engine.analyze_double_dummy(board)

            if results:
                # Format for display panel (right side)
                panel_text = "Double Dummy\nAnalysis:\n\n"
                panel_text += "    NT  S  H  D  C\n"
                for seat in ['N', 'E', 'S', 'W']:
                    panel_text += f"{seat}:  "
                    for strain in ['NT', 'S', 'H', 'D', 'C']:
                        tricks = results.get(seat, {}).get(strain, '-')
                        panel_text += f"{tricks:>2} "
                    panel_text += "\n"

                self.analysis_label.setText(panel_text)
                self.status_label.setText("Double dummy analysis complete")

                # Create a proper table dialog
                from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QDialogButtonBox
                from PyQt6.QtCore import Qt
                from .dialogs.dialog_style import apply_dialog_style

                dialog = QDialog(self)
                dialog.setWindowTitle("Double Dummy Analysis")
                dialog.setMinimumWidth(350)
                apply_dialog_style(dialog)

                layout = QVBoxLayout(dialog)

                # Create table widget
                table = QTableWidget(4, 5)  # 4 rows (N,E,S,W), 5 columns (NT,S,H,D,C)
                table.setHorizontalHeaderLabels(['NT', '♠', '♥', '♦', '♣'])
                table.setVerticalHeaderLabels(['North', 'East', 'South', 'West'])
                table.setStyleSheet("""
                    QTableWidget {
                        background-color: #ffffff;
                        color: #000000;
                        gridline-color: #c0c0c0;
                        font-size: 14px;
                    }
                    QTableWidget::item {
                        padding: 8px;
                        text-align: center;
                    }
                    QHeaderView::section {
                        background-color: #e0e0e0;
                        color: #000000;
                        padding: 6px;
                        font-weight: bold;
                        border: 1px solid #c0c0c0;
                    }
                """)

                # Populate table
                seat_list = ['N', 'E', 'S', 'W']
                strain_list = ['NT', 'S', 'H', 'D', 'C']
                for row, seat in enumerate(seat_list):
                    for col, strain in enumerate(strain_list):
                        tricks = results.get(seat, {}).get(strain, '-')
                        item = QTableWidgetItem(str(tricks))
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        table.setItem(row, col, item)

                table.resizeColumnsToContents()
                table.resizeRowsToContents()
                layout.addWidget(table)

                # OK button
                buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
                buttons.accepted.connect(dialog.accept)
                buttons.setStyleSheet("QPushButton { background-color: #e0e0e0; color: #000000; padding: 6px 20px; }")
                layout.addWidget(buttons)

                dialog.exec()
            else:
                self.status_label.setText("DD analysis returned no results - check engine")
        except Exception as e:
            self.status_label.setText(f"DD analysis error: {str(e)[:50]}")
            import traceback
            traceback.print_exc()

    def _on_simulation(self):
        """Show bid simulation dialog"""
        if not self.controller.board:
            QMessageBox.warning(self, "No Board",
                              "Please start a new deal first.")
            return

        if self.controller.current_phase != 'bidding':
            QMessageBox.warning(self, "Not Bidding",
                              "Simulation is only available during bidding.")
            return

        dialog = SimulationDialog(
            self.engine, self.controller.board,
            self.controller.current_seat, self
        )
        dialog.exec()

    def _on_qplus_simulation(self):
        """Show the Q-Plus style simulation pop-up.

        Manual entry point; the same dialog also auto-opens on the
        user's turn when a slam opportunity is detected (see
        _maybe_popup_slam_simulation).
        """
        if not self.controller.board:
            QMessageBox.warning(self, "No Board",
                                "Please start a new deal first.")
            return
        if self.controller.current_phase != 'bidding':
            QMessageBox.warning(
                self, "Not Bidding",
                "Simulation is only available during bidding.")
            return
        seat = self.controller.current_seat or self.controller.board.dealer
        dialog = QPlusSimulationDialog(
            self.engine, self.controller.board, seat, self)
        dialog.exec()

    def _maybe_popup_slam_simulation(self):
        """Auto-open the Q-Plus sim pop-up when slam is in scope.

        Called from _advance_bidding when it's the human's turn.
        Guarded by:
          * _slam_popup_shown_for_board — pop at most once per deal
            so the user isn't re-interrupted every round of bidding
          * a feature flag _slam_popup_enabled (default on) so the
            user can dismiss permanently
        """
        if not getattr(self, '_slam_popup_enabled', True):
            return
        board = self.controller.board
        if board is None:
            return
        # Track per-board so we only auto-pop once even if slam
        # remains in scope across multiple of the user's bidding
        # rounds.
        shown_id = getattr(self, '_slam_popup_shown_for_board', None)
        if shown_id == id(board):
            return
        try:
            from backend.slam_opportunity import has_slam_opportunity
        except Exception:
            return
        seat = self.controller.current_seat
        if seat is None:
            return
        try:
            if not has_slam_opportunity(board, seat):
                return
        except Exception:
            return
        self._slam_popup_shown_for_board = id(board)
        try:
            dialog = QPlusSimulationDialog(
                self.engine, board, seat, self)
            dialog.exec()
        except Exception as e:
            print(f"[qplus sim popup] failed: {e}", flush=True)

    def _on_show_imp_table(self):
        """Show IMP conversion table — detached so it can sit on a
        second monitor for reference while play continues."""
        self._imp_dialog = IMPTableDialog(self)
        self._show_detached(self._imp_dialog)

    def _on_show_probabilities(self):
        """Show probabilities table (detached)."""
        self._prob_dialog = ProbabilitiesDialog(self)
        self._show_detached(self._prob_dialog)

    def _on_show_scoring_reference(self):
        """Show scoring reference tables (detached)."""
        self._scoring_ref_dialog = ScoringReferenceDialog(self)
        self._show_detached(self._scoring_ref_dialog)

    def _show_detached(self, dialog):
        """Promote `dialog` to a free-floating top-level window and
        show it non-modally. Use for read-only / reference dialogs
        that benefit from sitting on a second monitor next to the
        main table view. Settings dialogs and anything that returns
        a value via exec() should *not* go through this path."""
        from .dialogs.dialog_style import make_detachable
        make_detachable(dialog)
        dialog.show()
        dialog.raise_()

    def _on_about(self):
        """Show about dialog with a light background.

        QMessageBox.about() inherits the parent window's stylesheet,
        which on the table view is dark blue — that left the body text
        unreadable against the same blue tone. Construct the message
        box explicitly and apply a light-grey palette so the text
        always reads at full contrast regardless of which window we
        descend from.
        """
        msg = QMessageBox(self)
        msg.setWindowTitle("About BridgeIQ")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "<h2 style='margin:0'>BridgeIQ</h2>"
            "<p>A bridge playing and analysis application "
            "powered by the BridgeIQ engine.</p>"
            "<p>Classic desktop Bridge interface.</p>"
            "<p><b>Engine:</b> biq (native, rule-based)<br>"
            "<b>UI:</b> PyQt6</p>"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setStyleSheet(
            "QMessageBox { background-color: #f0f0f0; }"
            "QMessageBox QLabel { color: #000; background-color: transparent;"
            " font-size: 14px; min-width: 380px; padding: 4px; }"
            "QMessageBox QPushButton { background-color: #e0e0e0;"
            " color: #000; border: 1px solid #999; padding: 6px 18px;"
            " border-radius: 3px; min-width: 80px; }"
            "QMessageBox QPushButton:hover { background-color: #d0d0d0; }"
        )
        msg.exec()

    # Network menu handlers

    def _on_start_server(self):
        """Show start server dialog and start hosting."""
        if self.network_controller.is_active:
            from .dialogs.dialog_style import styled_warning
            styled_warning(
                self, "Already Connected",
                "You are already in a network game. Disconnect first."
            )
            return

        dialog = StartServerDialog(self)
        if dialog.exec():
            settings = dialog.get_settings()
            if self.network_controller.start_server(
                settings['port'],
                settings['name'],
                settings['seat'],
                settings.get('password', '')
            ):
                self.status_label.setText(
                    f"Server started on port {settings['port']}. Waiting for players..."
                )
                # Open the persistent lobby. It stays open while guests
                # connect, auto-accepts when 4 seats are filled, and gives
                # the host a Start Game button to begin early.
                self._open_host_lobby(settings)
            else:
                from .dialogs.dialog_style import styled_warning
                styled_warning(
                    self, "Server Error",
                    "Failed to start server. Check if the port is available."
                )

    def _open_host_lobby(self, settings: dict):
        """Show the host lobby and wire its signals back to the controller."""
        # Pull the same IP list the StartServerDialog showed so the host
        # has it handy while waiting for guests.
        from PyQt6.QtNetwork import QNetworkInterface, QAbstractSocket
        ip_addresses = []
        for iface in QNetworkInterface.allInterfaces():
            flags = iface.flags()
            if not (flags & QNetworkInterface.InterfaceFlag.IsUp):
                continue
            if flags & QNetworkInterface.InterfaceFlag.IsLoopBack:
                continue
            for entry in iface.addressEntries():
                ip = entry.ip()
                if ip.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                    ip_addresses.append(ip.toString())

        self._lobby_dialog = NetworkLobbyDialog(
            host_name=settings['name'],
            host_seat=settings['seat'],
            port=settings['port'],
            ip_addresses=ip_addresses,
            parent=self,
        )
        self._lobby_dialog.start_game_requested.connect(self._on_lobby_start_clicked)
        # Tear down the server if the host cancels the lobby.
        self._lobby_dialog.rejected.connect(self._on_lobby_cancelled)
        # Seed the board with the host already seated.
        self._lobby_dialog.update_seat_map(self.network_controller.current_seat_map())
        self._lobby_dialog.show()

    def _on_lobby_start_clicked(self):
        """Host has clicked Start Game (or the lobby auto-filled)."""
        self._lobby_dialog = None
        self.network_controller.start_game()
        # Mirror the previous "first guest auto-deals" behaviour, but now
        # gated by the host explicitly closing the lobby.
        QTimer.singleShot(200, self._start_network_game)

    def _on_lobby_cancelled(self):
        """Host cancelled the lobby before starting — stop the server."""
        self._lobby_dialog = None
        if self.network_controller.is_active:
            self.network_controller.disconnect()
        self.status_label.setText("Lobby cancelled. Server stopped.")

    def _on_connect_server(self):
        """Show connect dialog and connect to a server."""
        if self.network_controller.is_active:
            from .dialogs.dialog_style import styled_warning
            styled_warning(
                self, "Already Connected",
                "You are already in a network game. Disconnect first."
            )
            return

        self._connect_dialog = ConnectServerDialog(self)
        self._connect_dialog.connect_requested.connect(self._do_connect_to_server)
        self._connect_dialog.seat_clicked.connect(self._on_lobby_seat_clicked)
        self._connect_dialog.show()

    def _do_connect_to_server(self, host: str, port: int, name: str):
        """Open the socket. The seat is claimed later from the lobby
        seat board via _on_lobby_seat_clicked → request_seat."""
        pw = self._connect_dialog.password_edit.text() if getattr(self, "_connect_dialog", None) else ""
        self.network_controller.connect_to_server(host, port, name,
                                                   requested_seat="", password=pw)

    def _on_lobby_seat_clicked(self, seat: Seat):
        """Forward the guest's seat click to the server as SEAT_REQUEST."""
        self.network_controller.request_seat(seat.to_char())

    def _on_network_disconnect(self):
        """Disconnect from network game."""
        if self.network_controller.is_active:
            self.network_controller.disconnect()
            self.disconnect_action.setEnabled(False)
            self.status_label.setText("Disconnected from network game")
            # Revert to single-player mode
            self._configure_single_player()
            self._refresh_qplus_ingest_action_state()

    def _on_network_connected(self, mode: str, my_seat: str, partner_seat: str, client_role: str):
        """Handle successful network connection."""
        self.disconnect_action.setEnabled(True)
        self._refresh_qplus_ingest_action_state()

        seat = Seat.from_char(my_seat)
        partner = Seat.from_char(partner_seat)

        # Remember the seat the user actually claimed so disconnect
        # recovery doesn't drop them back to South.
        self._user_seat = seat

        # Guest mode: keep the connect dialog open in lobby mode so the
        # guest can see the live seat board until the host starts the
        # game. The dialog dismisses itself on GAME_START.
        connect = getattr(self, '_connect_dialog', None)
        if mode == "client" and connect is not None:
            connect.enter_lobby(seat)

        # Rotate the table view so the local user's seat is at the bottom.
        # Single-player keeps the default (South); a guest at E/N/W now sees
        # their own hand at the bottom of the screen.
        self.table_view.set_local_seat(seat)

        # Configure players for network mode
        self._configure_network_players()

        # Build status message based on role
        role_desc = "partner" if client_role == "partner" else "opponent"
        if mode == "server":
            status = f"Hosting game. You play {my_seat}/{partner_seat}. Waiting for {role_desc} to connect..."
        else:
            status = f"Connected as {role_desc}! You play {my_seat}/{partner_seat}. Ready to play!"

        self.status_label.setText(status)

        # Update window title
        mode_str = "Host" if mode == "server" else f"Guest ({role_desc})"
        self.setWindowTitle(f"BridgeIQ - LAN Game ({mode_str}: {my_seat}/{partner_seat})")

    def _on_network_client_joined(self, client_name: str, client_role: str):
        """Handle client joining (server mode).

        While the lobby is on screen, do nothing destructive — the host will
        click Start Game (or auto-accept fires when 4 seats fill) which then
        triggers _start_network_game. After the game has begun, late joiners
        receive the current deal state instead of a fresh deal.
        """
        role_desc = "as your partner" if client_role == "partner" else "as your opponent"
        self.status_label.setText(
            f"'{client_name}' has joined {role_desc}!"
        )
        self._configure_network_players()

        # Lobby still open → wait for host's Start Game click.
        if getattr(self, '_lobby_dialog', None) is not None:
            return

        already_in_progress = (
            self.controller.board is not None
            and self.controller.current_phase in ('bidding', 'play', 'waiting_next')
        )
        if already_in_progress:
            # Late joiner — push the current deal state so they can follow along.
            self.network_controller.broadcast_deal(self.controller.board)
            # Seat is an IntEnum (NORTH == 0); use explicit None check or
            # a North dummy would be skipped on every late-joiner push.
            if (self.controller.dummy is not None
                    and self.controller.current_phase != 'bidding'):
                dummy_hand = self.controller.board.hands.get(self.controller.dummy)
                if dummy_hand is not None and dummy_hand.cards:
                    self.network_controller.broadcast_dummy_reveal(
                        self.controller.dummy, dummy_hand)
        else:
            QTimer.singleShot(500, self._start_network_game)

    def _on_network_seat_map(self, seats: dict):
        """Live seat-occupancy update from the server. Routes to whichever
        lobby/seat-picker dialog is currently visible."""
        lobby = getattr(self, '_lobby_dialog', None)
        if lobby is not None:
            lobby.update_seat_map(seats)
        connect = getattr(self, '_connect_dialog', None)
        if connect is not None:
            my_seat = self.network_controller.my_seat
            connect.update_seat_map(
                seats,
                host_seat=self.network_controller.host_seat_char(),
                my_seat=my_seat,
            )
            # First SEAT_LIST after a successful socket connect signals
            # "lobby join confirmed" — switch from the form page to the
            # live seat board so the user can pick a seat.
            if connect._stack.currentIndex() == 0:
                connect.set_connecting(False)
                connect.enter_lobby(my_seat)

    def _on_network_seat_rejected(self, reason: str):
        """Server rejected the guest's SEAT_REQUEST as taken. Surface the
        error inline on the lobby seat board without closing the socket."""
        connect = getattr(self, '_connect_dialog', None)
        if connect is not None:
            connect.show_seat_taken_error(reason)
            connect.show_lobby_status("Pick another seat.")

    def _on_network_game_starting(self):
        """Host has broadcast GAME_START (or our own host-side equivalent).
        Close the guest's lobby view and let the deal flow take over."""
        connect = getattr(self, '_connect_dialog', None)
        if connect is not None:
            try:
                connect.accept()
            except Exception:
                pass
            self._connect_dialog = None

    def _on_network_server_started(self, port: int):
        """Handle server start confirmation."""
        self.disconnect_action.setEnabled(True)
        self._refresh_qplus_ingest_action_state()

    def _on_network_disconnected(self, reason: str):
        """Handle network disconnection.

        Two very different cases share this signal:

        * Server mode — a guest dropped. The host stays hosting, BEN
          takes over the freed seat(s) automatically because
          _configure_network_players reads from the live remote_seats
          list. Don't tear down the rotation or revert to South.
        * Client mode — we lost the host. Keep the user at their current
          seat, fill every other seat with BEN locally so the user can
          finish the hand offline, and clean up the controller state so
          the disconnect button reflects "no network game".
        """
        from .dialogs.dialog_style import styled_info

        if self.network_controller.is_server:
            # A guest left. Refresh player ownership; AI now controls the
            # freed seat. Do not exit network mode.
            self._configure_network_players()
            self.status_label.setText(
                f"Guest disconnected ({reason}); BridgeIQ now controls the freed seat."
            )
            # If we were mid-hand waiting on the (now-departed) guest's
            # turn, kick the engine so BEN takes over and play resumes
            # instead of stalling on "Waiting for partner…".
            if self.controller.current_phase in ('bidding', 'play', 'waiting_next'):
                self._advance_game()
            return

        # Client mode: keep playing locally with BEN as every other seat.
        # Prefer the canonical _user_seat (set when the guest claimed
        # their seat) over the controller's possibly-cleared field.
        # NOTE: Seat is an IntEnum where NORTH == 0; bare truthiness chains
        # would silently skip a North-seated guest, so test for None.
        my_seat = self._user_seat
        if my_seat is None:
            my_seat = self.network_controller.my_seat
        if my_seat is None:
            my_seat = Seat.SOUTH
        self.disconnect_action.setEnabled(False)
        styled_info(
            self, "Host Disconnected",
            f"Lost connection to the host ({reason}). "
            f"biq will take over the other three seats so you can finish the hand."
        )
        # Drop the network controller back to idle so other code paths
        # (next-deal, hint, save) treat us as offline again.
        try:
            self.network_controller.disconnect()
        except Exception:
            pass
        # Local takeover: only the user's seat is human; the rest are AI.
        for seat in Seat:
            if seat == my_seat:
                self.controller.players[seat].player_type = PlayerType.HUMAN
            else:
                self.controller.players[seat].player_type = PlayerType.COMPUTER
        # Keep the rotation so the user's hand stays at the bottom.
        self.table_view.set_local_seat(my_seat)
        self.setWindowTitle(f"BridgeIQ (offline — {my_seat.to_char()})")
        self.status_label.setText(
            f"Host disconnected; you are now playing offline as {my_seat.to_char()}."
        )
        # Re-enable Next-card / Hint / engine flow now that we're in
        # local single-player mode. Without this the toolbar buttons
        # stay disabled in whatever state they had at the moment the
        # host dropped, and the user has no way to finish the hand.
        self.next_card_btn.setEnabled(False)  # cleared; _advance_play will set as needed
        self.bidding_box.set_enabled(False)
        if self.controller.current_phase in ('bidding', 'play', 'waiting_next'):
            self._advance_game()
        # Hint refresh follows the regular path inside _advance_game; if
        # nothing advanced (e.g. board not started), make sure Hint is at
        # least correctly disabled.
        self._refresh_hint_button_state()

    def _on_network_deal_received(self, board: BoardState):
        """Handle receiving a deal from the server (client mode)."""
        self.controller.board = board
        self.controller.current_phase = 'bidding'
        self.controller.current_seat = board.dealer
        self._refresh_status_strip()

        # DIAGNOSTIC — surface enough to debug "guest sees no cards":
        # which seat the local user is at, what cards arrived per seat,
        # and which physical widget will end up showing the local hand.
        try:
            my_seat = self.network_controller.my_seat
            print(f"[net deal] my_seat={my_seat.to_char() if my_seat is not None else None}, "
                  f"dealer={board.dealer.to_char()}, "
                  f"table_view._local_seat={self.table_view._local_seat.to_char()}, "
                  f"rotation_quarters={self.table_view._rotation_quarters}",
                  flush=True)
            for s in Seat:
                hand = board.hands.get(s)
                n = len(hand.cards) if hand else 0
                print(f"  hand[{s.to_char()}]: {n} cards", flush=True)
        except Exception as ex:
            print(f"[net deal] diagnostic failed: {ex}", flush=True)

        # If set_local_seat hasn't been applied (e.g. signal-ordering quirk),
        # apply it now so the bottom widget is the right one before set_board.
        try:
            ms = self.network_controller.my_seat
            if ms is not None and self.table_view._local_seat != ms:
                print(f"[net deal] late set_local_seat({ms.to_char()})", flush=True)
                self.table_view.set_local_seat(ms)
        except Exception:
            pass

        # Store original hands
        self.original_hands = {}
        for seat, hand in board.hands.items():
            if hand.cards:  # Only copy non-hidden hands
                self.original_hands[seat] = Hand(cards=list(hand.cards))

        # Setup UI
        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction([], board.dealer)
        self.bidding_box.setVisible(True)
        self.right_panel.setVisible(True)
        self._clear_bid_info()
        self._update_available_bids()
        self._update_window_title()

        # Set visibility - show my hand and partner's hand
        for seat in Seat:
            visible = self.network_controller.is_my_seat(seat)
            self.table_view.set_hand_visible(seat, visible)

        if self.bid_info_action.isChecked():
            self._apply_bid_info_visibility()

        self._set_toolbar_mode('ingame')
        self._advance_game()

    def _on_network_remote_bid(self, seat_char: str, bid: Bid):
        """Handle receiving a bid from the remote player."""
        seat = Seat.from_char(seat_char)

        # Apply the bid
        self.controller.board.auction.append(bid)
        self.bidding_box.add_bid(bid)
        self.table_view.update_auction(self.controller.board.auction, self.controller.board.dealer)
        self._add_bid_to_info(seat, bid, "", "")

        # Check if auction complete
        if self.controller.board.is_auction_complete():
            if self.controller.board.is_passed_out():
                self.controller.current_phase = 'finished'
            else:
                self.controller._setup_play()
        else:
            self.controller.current_seat = self.controller.current_seat.next()

        self._advance_game()

    def _on_network_remote_card(self, seat_char: str, card: Card):
        """Handle receiving a card play from the remote player."""
        seat = Seat.from_char(seat_char)

        # Force current_seat to match the broadcasting player before
        # play_card removes the card from the right hand. Without this
        # the local controller would happily strip a card from whichever
        # seat it last advanced to, which drifts host vs guest after a
        # trick boundary or after an AI play arrived in unexpected order.
        if self.controller.board is not None:
            self.controller.current_seat = seat

        # Apply the card play
        self.table_view.play_card_to_trick(seat, card)
        self.controller.play_card(card)
        self._advance_game()

    def _on_network_dummy_revealed(self, dummy_seat: Seat, dummy_hand: Hand):
        """Handle receiving dummy's hand when play starts (client mode)."""
        # DIAGNOSTIC — track exactly when the guest's UI gets the dummy.
        try:
            n_cards = len(dummy_hand.cards) if dummy_hand else 0
            tv_decl = self.table_view.declarer
            print(
                f"[guest dummy_reveal] dummy_seat={dummy_seat.to_char()} "
                f"cards={n_cards} controller.dummy={self.controller.dummy} "
                f"table_view.declarer={tv_decl} "
                f"phase={self.controller.current_phase}",
                flush=True,
            )
        except Exception:
            pass
        # Update the board with dummy's actual hand
        self.controller.board.hands[dummy_seat] = dummy_hand
        # Show dummy's hand
        self.table_view.set_hand_visible(dummy_seat, True)
        # DIAGNOSTIC — confirm the widget got the cards.
        try:
            ds = self.table_view._display_seat(dummy_seat)
            w = self.table_view.hand_widgets[ds]
            print(
                f"[guest dummy_reveal] widget at physical {ds}: "
                f"visible={w.isVisible()} "
                f"hand_cards={len(w.hand.cards) if getattr(w, 'hand', None) else 0} "
                f"size={w.size().width()}x{w.size().height()}",
                flush=True,
            )
        except Exception as ex:
            print(f"[guest dummy_reveal] post-set diag failed: {ex}", flush=True)

    def _on_network_closed_room_ingested(self, run):
        """Guest received a manually-ingested closed-room run from the host.

        Attach it to the matching BoardResult (by pavlicek_id, falling back
        to board number) so the score sheet's Compare button lights up for
        the guest too.
        """
        try:
            self._attach_closed_room_run(run)
            self.status_label.setText(
                f"Closed room received from host (board {run.board_number})."
            )
        except Exception as e:
            print(f"[net closed_room_ingested] error: {e}", flush=True)

    def _on_network_error(self, error: str):
        """Handle network error."""
        from .dialogs.dialog_style import styled_warning
        styled_warning(self, "Network Error", error)
        if hasattr(self, '_connect_dialog') and self._connect_dialog:
            self._connect_dialog.show_error(error)

    def _on_network_trick_clear(self):
        """Handle receiving trick clear from remote player (they clicked 'next card').

        Clears the green-felt trick widgets unconditionally — leaving the
        previous trick on the felt while the next trick is laid on top is
        much worse than skipping a redundant controller advance when our
        phase has already moved on. Server fan-out to other guests is
        handled by `BridgeServer._relay_to_others`.
        """
        self.table_view.clear_trick()
        self.next_card_btn.setEnabled(False)

        if self.controller.current_phase == 'waiting_next':
            self.controller.advance_to_next_trick()
            self._advance_game()

    def _configure_network_players(self):
        """Configure player types for network play based on network controller state."""
        for seat in Seat:
            player_type = self.network_controller.get_player_type_for_seat(seat)
            self.controller.players[seat].player_type = player_type
        # Surface who's network/Computer on the table + strip.
        self._push_seat_types_to_table()
        self._refresh_status_strip()

    def _configure_single_player(self):
        """Revert to single-player mode (South-as-human, no rotation)."""
        for seat in Seat:
            if seat == Seat.SOUTH:
                self.controller.players[seat].player_type = PlayerType.HUMAN
            else:
                self.controller.players[seat].player_type = PlayerType.COMPUTER
        # Make sure the canonical user seat and the table rotation also
        # reset to the single-player default; otherwise a previous
        # network claim (e.g. North) lingers and the bottom widget keeps
        # showing the wrong hand on the next deal.
        self._user_seat = Seat.SOUTH
        self.table_view.set_local_seat(Seat.SOUTH)
        self._push_seat_types_to_table()
        self._refresh_status_strip()

    def _start_network_game(self):
        """Start a network game (server generates deal)."""
        if not self.network_controller.is_server:
            return

        if not self.engine.is_ready:
            QMessageBox.warning(self, "Engine Not Ready",
                              "The bridge engine is not yet initialized.")
            return

        # Generate a new deal
        board = self.controller.new_deal()

        # Store original hands
        self.original_hands = {}
        for seat, hand in board.hands.items():
            self.original_hands[seat] = Hand(cards=list(hand.cards))

        # Broadcast deal to client
        self.network_controller.broadcast_deal(board)

        # Setup UI
        self.table_view.set_board(board)
        self.bidding_box.clear()
        self.bidding_box.set_auction([], board.dealer)
        self.bidding_box.setVisible(True)
        self.right_panel.setVisible(True)
        self._clear_bid_info()
        self._update_available_bids()
        self._update_window_title()

        # Set visibility - show my hand and partner's hand
        for seat in Seat:
            visible = self.network_controller.is_my_seat(seat)
            self.table_view.set_hand_visible(seat, visible)

        if self.bid_info_action.isChecked():
            self._apply_bid_info_visibility()

        self._set_toolbar_mode('ingame')
        self.status_label.setText(f"Board {board.board_number}: {board.dealer.to_char()} deals")
        self._refresh_status_strip()
        self._advance_game()

    def _is_waiting_for_remote(self) -> bool:
        """Check if we're waiting for the remote player to act."""
        if not self.network_controller.is_active:
            return False

        current_seat = self.controller.current_seat
        if current_seat is None:
            return False

        # We're waiting for remote if it's an opponent's turn
        return not self.network_controller.is_my_seat(current_seat)

    def _on_auto_play_toggle(self, checked: bool):
        """Toggle auto play mode.

        ON: kick the play loop so it starts straight away — including from
        the between-tricks pause (phase 'waiting_next'), where it must clear
        the trick via Next-card rather than _advance_game.

        OFF: nothing to do here. _advance_play and _handle_trick_complete
        re-read the button state every card, so play hands control back to
        the human at the next declarer/dummy card — i.e. clicking Autoplay
        again stops it.
        """
        if not checked:
            return
        phase = self.controller.current_phase
        if phase == 'waiting_next':
            self._on_next_card()
        elif phase not in ('idle', 'finished', None):
            self._advance_game()

    def _is_local_user_turn(self) -> bool:
        """True if the locally-controlled human is on lead right now.

        Network mode: maps `current_seat` through dummy → declarer (the
        declarer controls dummy's plays) and checks ownership against
        `is_my_seat`. Single-player: trusts the controller's flag, which
        already accounts for human-controls-declarer.
        """
        seat = self.controller.current_seat
        if seat is None:
            return False
        if self.network_controller.is_active:
            phase = self.controller.current_phase
            if phase == 'play':
                dummy = self.controller.dummy
                declarer = self.controller.declarer
                actual = declarer if (dummy is not None and seat == dummy
                                      and declarer is not None) else seat
                return self.network_controller.is_my_seat(actual)
            return self.network_controller.is_my_seat(seat)
        return self.controller.is_human_turn()

    def _on_hint(self):
        """Ask Claude what to do next, with a progress bar and result dialog.

        BEN's own engine suggestion is included in the prompt as context so
        Claude can agree, disagree, or explain it.
        """
        board = self.controller.board
        seat = self.controller.current_seat
        phase = self.controller.current_phase
        # Seat is an IntEnum (NORTH == 0); use explicit None checks.
        if board is None or seat is None or phase not in ('bidding', 'play'):
            return
        # Hint is only allowed when it's the local user's turn — otherwise
        # the prompt would expose the active seat's hand to whoever clicked
        # the button. Defense in depth: the toolbar button is also greyed
        # out off-turn (see _refresh_hint_button_state), but that can be
        # bypassed if the click lands while the state is stale.
        if not self._is_local_user_turn():
            self.status_label.setText(
                "Hint is only available when it's your turn."
            )
            return

        # BEN's TensorFlow bidder was trained on SAYC + 2-over-1 only —
        # it has no idea what Precision / Acol / French strong-club
        # responses look like, so its bid suggestion (and softmax
        # confidence score) is misleading at best when the table is
        # on any non-SAYC system. We DROP BEN entirely from the
        # bidding-phase hint and let Claude reason from the active
        # bidding system clauses in the prompt instead.
        #
        # Card play has no such bias — winners, finesses, ducks etc.
        # don't depend on the bidding system — so BEN's card-play
        # suggestion still shows up there. Score is its NN softmax
        # confidence (0–1).
        engine_text = ""
        try:
            if phase == 'play':
                # Skip BEN suggestion when we don't have the seat's hand
                # (guest in network mode only has its own + visible hands).
                hand = board.hands.get(seat)
                if hand and hand.cards:
                    trick_cards = board.current_trick.cards if board.current_trick else []
                    # The card the robots would play (no-peek engine), not the
                    # lowest-card fallback, so the hint agrees with the
                    # blunder check (which measures against this card).
                    card, reason = self._biq_card_choice(board, seat, trick_cards)
                    if card is not None:
                        engine_text = f"biq suggests: play {card.to_str()}"
                        if reason:
                            engine_text += f"\nWhy: {reason}"
        except Exception as e:
            engine_text = f"(BEN engine error: {e!r})"

        # Surface the active bidding systems at the top of the hint
        # dialog so the user knows which spec Claude is being told to
        # interpret each side's bids by. Two lines when NS / EW differ,
        # one when they share a system.
        system_preamble = ""
        try:
            ns_spec = self._active_bidding_system('NS')
            ew_spec = self._active_bidding_system('EW')
            if ns_spec.name == ew_spec.name:
                desc = ns_spec.description or ns_spec.name
                system_preamble = (
                    f"Bidding system: {ns_spec.name} — {desc}"
                )
            else:
                ns_d = ns_spec.description or ns_spec.name
                ew_d = ew_spec.description or ew_spec.name
                system_preamble = (
                    f"Bidding systems — NS: {ns_spec.name} ({ns_d}); "
                    f"EW: {ew_spec.name} ({ew_d})"
                )
        except Exception:
            pass

        state_text = self._build_hint_state_text(board, seat, phase)
        prompt = self._build_hint_prompt(phase, seat, state_text, engine_text)

        # Route the result through the BDL-with-annotations renderer so
        # Claude's reasoning lands inline next to the section it's
        # commenting on instead of in a free-floating text block. The
        # preamble shows the bidding system(s) in use AND BEN's
        # second-opinion line, so the user sees both before reading
        # Claude's analysis.
        preamble_parts = [p for p in (system_preamble, engine_text) if p]
        preamble_text = ("\n\n".join(preamble_parts) + "\n\n"
                         if preamble_parts else "")
        # Cache key derived from the prompt (deterministic in the game
        # state) so dismissing and re-clicking Hint before play
        # advances re-shows the same dialog without re-calling Claude.
        import hashlib as _hashlib
        hint_cache_key = (
            "hint:"
            + _hashlib.sha1(prompt.encode('utf-8')).hexdigest()
        )

        # --- Offline, book-grounded coaching note + whole-deal plan ---------
        # The note (and, in play, the 13-trick plan) is computed locally from
        # the live board state — system params, vulnerability, the visible /
        # inferable cards — and shown immediately, with no Claude dependency.
        # NO PEEKING: everything flows through the legitimately face-up seats.
        note, plan_text, topics = "", "", []
        try:
            from . import coach_notes
            from . import whole_deal_plan as wdp_mod
            ns_spec = self._active_bidding_system('NS')
            ew_spec = self._active_bidding_system('EW')
            visible = set(self.table_view.face_up_seats())
            note, topics = coach_notes.coaching_context(
                board, seat, phase, ns_spec, ew_spec, visible)
            # The whole-deal 13-trick plan is a START-of-play artifact: show it
            # only while still on the first trick (the planning moment). Once
            # trick one is over, the note above is already next-card advice and
            # the plan would just repeat stale beginning-of-hand guidance.
            if phase == 'play' and coach_notes.is_play_start(board):
                declarer = board.contract.declarer if board.contract else None
                dummy = declarer.partner() if declarer is not None else None
                plan = wdp_mod.whole_deal_plan(
                    board, seat, declarer, dummy, board.contract, visible)
                plan_text = wdp_mod.render_whole_deal_plan(plan)
        except Exception as e:
            note = note or f"(coach note unavailable: {e!r})"

        # biq's own engine names the concrete next card to play — surface it in
        # the dialog (especially mid-play, where the note is technique, not a
        # full plan). `engine_text` was computed above for the play phase.
        engine_line = ""
        if phase == 'play' and engine_text:
            engine_line = engine_text.splitlines()[0]

        # Record the offline advice as comments in the .BDL (live) and queue it
        # for the .qss (written at close / export).
        self._record_advice(note, plan_text)

        # Claude is the deeper, optional second layer — gated by Preferences.
        def _ask_claude():
            grounded = prompt
            try:
                from . import coach_notes
                passages = coach_notes.book_passages(topics, limit=3)
                if passages:
                    blocks = "\n\n".join(
                        f"[{p['book']}]\n{p['text']}" for p in passages)
                    grounded = (
                        prompt
                        + "\n\nBOOK CONTEXT (excerpts from the player's bridge "
                        "library — use as grounding, do not quote verbatim):\n"
                        + blocks)
            except Exception:
                grounded = prompt
            self._run_claude_with_dialog(
                prompt=grounded,
                title=f"Claude hint — {'bidding' if phase == 'bidding' else 'card play'}",
                wait_label=f"Claude is thinking about your {'bid' if phase == 'bidding' else 'card'}...",
                timeout_seconds=900,
                preamble=preamble_text,
                bdl_text=state_text,
                cache_key=hint_cache_key,
            )

        self._show_coach_dialog(note, plan_text, phase, _ask_claude,
                                next_card=engine_line)

    def _record_advice(self, note: str, plan_text: str = ""):
        """Write coaching advice as comments into the active .BDL (immediately)
        and queue it for the .qss (written at close/export). Idempotent within
        a session so re-opening the same hint doesn't duplicate blocks."""
        parts = [p for p in (note, plan_text) if p and not p.startswith("(")]
        if not parts:
            return
        text = "\n".join(parts)
        if not hasattr(self, '_recorded_advice'):
            self._recorded_advice = set()
        key = hash(text)
        if key in self._recorded_advice:
            return
        self._recorded_advice.add(key)
        try:
            self.game_logger._append_commentary_to_bdl("Coach — " + text)
        except Exception:
            pass
        try:
            self.scoring_table.record_comment("Coach — " + text)
        except Exception:
            pass

    def _show_coach_dialog(self, note: str, plan_text: str, phase: str,
                           ask_claude_cb, next_card: str = ""):
        """Show the offline coaching note — plus, in play, biq's suggested next
        card and (only at the start of play) the 13-trick plan. The 'Ask Claude'
        button offers deeper advice (gated by Preferences)."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                                      QScrollArea, QWidget, QHBoxLayout)
        from PyQt6.QtGui import QFont

        dlg = QDialog(self)
        dlg.setWindowTitle(
            f"Hint — {'bidding' if phase == 'bidding' else 'card play'}")
        dlg.setMinimumWidth(560)
        dlg.setStyleSheet("background-color: #e8e8f0; color: #000;")
        lay = QVBoxLayout(dlg)

        hdr = QLabel("Coaching note")
        hdr.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        lay.addWidget(hdr)
        note_lbl = QLabel(note or "No specific note for this situation.")
        note_lbl.setWordWrap(True)
        note_lbl.setFont(QFont("Arial", 12))
        lay.addWidget(note_lbl)

        # Concrete next card (play phase) — the actionable "what to play now".
        if next_card:
            card_lbl = QLabel("▶ " + next_card)
            card_lbl.setWordWrap(True)
            card_lbl.setFont(QFont("Arial", 12, QFont.Weight.Bold))
            card_lbl.setStyleSheet("color:#1a5e1a;")
            lay.addWidget(card_lbl)

        if plan_text:
            ph = QLabel("Whole-deal plan")
            ph.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            lay.addWidget(ph)
            plan_lbl = QLabel(plan_text)
            plan_lbl.setWordWrap(True)
            plan_lbl.setFont(QFont("Arial", 11))
            area = QScrollArea()
            area.setWidgetResizable(True)
            inner = QWidget()
            il = QVBoxLayout(inner)
            il.addWidget(plan_lbl)
            area.setWidget(inner)
            area.setMaximumHeight(220)
            lay.addWidget(area)

        btn_row = QHBoxLayout()
        ask_btn = QPushButton("Ask Claude for deeper advice")

        def _on_ask():
            # Always functional: if Claude is on, run it; if not, offer to
            # turn it on (it's an opt-in feature) rather than sit there dead.
            if self._claude_enabled():
                dlg.accept()
                ask_claude_cb()
                return
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox(dlg)
            box.setWindowTitle("Claude is turned off")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("Deeper AI advice uses the Claude CLI — an opt-in "
                        "feature (it costs tokens and isn't needed for "
                        "ordinary play).")
            box.setInformativeText("Enable it in Preferences → AI & Network?")
            open_btn = box.addButton("Open Preferences…",
                                     QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                dlg.accept()
                self._on_preferences()
                # If the user just enabled it, go straight to the advice.
                if self._claude_enabled():
                    ask_claude_cb()

        ask_btn.clicked.connect(_on_ask)
        ask_btn.setToolTip("Deeper AI advice via the Claude CLI "
                           "(opt-in; enable in Preferences → AI & Network).")
        btn_row.addWidget(ask_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()

    def _build_hint_state_text(self, board, seat, phase) -> str:
        """Serialize the current board state for a Claude hint prompt as
        a partial BDL file. The BDL form keeps every bid, every card and
        every trick attached to a fixed N/E/S/W column or leader marker,
        which is the format Claude has the easiest time annotating
        without confusing who did what.
        """
        from .game_logger import build_bdl_snapshot

        # Dummy is visible to defenders only after the opening lead.
        # During bidding it never is. Use the table-view's reveal flag
        # (already accounts for human-side declarer).
        dummy_visible = (phase == 'play'
                         and getattr(self.table_view, 'dummy_revealed', True))

        # Mark the pavlicek deal id when we know the full hand layout —
        # gives Claude one more anchor when deciding which deal it is.
        deal_id = ""
        try:
            hands_for_pavlicek = self.original_hands or board.hands
            if all(s in hands_for_pavlicek for s in Seat):
                deal_id = format_deal_base72(deal_to_number(hands_for_pavlicek))
        except Exception:
            deal_id = ""

        # Hint dialog shows BDL + Claude annotations only — no footer
        # (Viewer / HCP / Phase) and no "Remaining cards" appendix. The
        # BDL block already encodes everything Claude needs (the seat
        # whose hand is shown is implicit, and remaining cards are
        # derivable from the Cards block + Tricks block).
        bdl = build_bdl_snapshot(
            board,
            original_hands=self.original_hands,
            viewer_seat=seat,
            phase=phase,
            dummy_visible_to_viewer=dummy_visible,
            deal_id=deal_id,
            current_seat=seat,
            include_footer=False,
        )
        return bdl

    def _build_hint_prompt(self, phase: str, seat, state_text: str, engine_text: str) -> str:
        """Assemble the Claude prompt for a hint query."""
        seat_names = {Seat.NORTH: 'N', Seat.EAST: 'E', Seat.SOUTH: 'S', Seat.WEST: 'W'}
        action = "bid" if phase == 'bidding' else "card to play"
        engine_block = f"\n\n{engine_text}" if engine_text else ""

        # During card play we want a full plan for the rest of the hand,
        # not just a one-card recommendation. The clause below makes
        # that explicit so the annotator lays out the line, key entries,
        # threat suits and counting before naming the next card.
        play_clause = ""
        if phase == 'play':
            play_clause = (
                "\n\nCARD-PLAY HINT — produce a FULL PLAN for the rest of "
                "the hand, not only the next card. The annotation block "
                "must include:\n"
                "  • A 'Plan:' line stating the line of play (e.g. "
                "'establish hearts after losing one', 'cross-ruff', "
                "'duck twice and run diamonds').\n"
                "  • A 'Tricks needed:' line that counts top tricks and "
                "names where the missing tricks come from.\n"
                "  • An 'Entries:' line listing every entry to each hand "
                "in order, since establishing length tricks usually hinges "
                "on them.\n"
                "  • A 'Threat / counting:' line covering the danger hand, "
                "what cards opponents have shown / signalled, and what to "
                "watch for as the hand unfolds.\n"
                "  • A short trick-by-trick sketch (Trick T+1, T+2, …) "
                "for the next 3-5 tricks of the plan.\n"
                "  • Then the usual '>> Recommendation: <card>' line as "
                "the very last annotation.\n\n"
                "DIAGRAMS — visual learners profit from a picture. "
                "Include 1–3 PlantUML diagrams in your reply, EACH "
                "wrapped in a ```plantuml ... ``` fenced block (the "
                "renderer will replace each block with the rendered "
                "PNG inline). Pick the diagram types that actually "
                "clarify THIS hand, e.g.:\n"
                "  – A mindmap (@startmindmap … @endmindmap) of the "
                "plan: root = 'Plan', children = each suit, "
                "grandchildren = top tricks / length tricks / losers.\n"
                "  – A simple block diagram (@startuml component … "
                "@enduml) of entries between hands, with arrows "
                "labelled by the card or sequence used.\n"
                "  – An activity diagram (@startuml :step; :step; "
                "@enduml) walking through the trick-by-trick sketch "
                "with decision diamonds where the next play depends "
                "on a card you don't yet know (e.g. \"if heart "
                "honour drops singleton → claim\").\n"
                "Keep each diagram small (≤ 12 nodes) so it renders "
                "fast and reads at a glance. Don't include diagrams "
                "for trivial spots."
            )


        # Tell Claude which bidding system the table is actually using so a
        # Precision-configured user gets Precision-style recommendations
        # ("1♣ = 16+", strong-club responses, etc.) and an SAYC-configured
        # user gets strict SAYC. Card-play hints don't depend on the
        # system, so this only kicks in during bidding.
        #
        # The clause is deliberately strict: Claude must apply the chosen
        # system's natural meanings only. No "SAYC-flavoured" hedging, no
        # crossing systems mid-recommendation, no falling back on a
        # different convention if the chosen system has nothing to say.
        system_clause = ""
        if phase == 'bidding':
            try:
                ns_spec = self._active_bidding_system('NS')
                ew_spec = self._active_bidding_system('EW')
                ns_text = self._format_system_summary(ns_spec)
                ew_text = self._format_system_summary(ew_spec)
                ns_pretty = ns_spec.description or ns_spec.name
                ew_pretty = ew_spec.description or ew_spec.name
                if ns_spec.name == ew_spec.name:
                    system_clause = (
                        f" The table is playing strict **{ns_pretty}** at "
                        f"BOTH pairs. Apply only this system's bid "
                        f"meanings — {ns_text}. Do NOT mix in conventions "
                        "from any other system."
                    )
                else:
                    # Different systems per pair — instruct Claude to
                    # interpret each side's calls using the matching
                    # spec, never blending the two.
                    system_clause = (
                        f"\n\nSYSTEMS IN USE (different per pair — "
                        "interpret each side's bids ONLY through the "
                        "matching system):\n"
                        f"  • NS plays **{ns_pretty}** — {ns_text}\n"
                        f"  • EW plays **{ew_pretty}** — {ew_text}\n"
                        "When commenting on an NS call, apply ONLY NS's "
                        "system. When commenting on an EW call, apply "
                        "ONLY EW's system. Never describe a bid using "
                        "the other pair's conventions."
                    )
            except Exception:
                # Config / spec lookup failed — fall back to a
                # system-agnostic prompt rather than crashing the hint.
                pass
        # Card play: tell Claude the carding agreements so it reads the
        # defenders' spot cards (and places their unseen cards) the way the
        # analysis view does, not by some default convention of its own.
        signal_clause = ""
        if phase == 'play':
            try:
                from .teaching_view import signalling_conventions_text
                signal_clause = "\n\n" + signalling_conventions_text()
            except Exception:
                signal_clause = ""
        return (
            f"You are a bridge teacher advising {seat_names[seat]} on the next {action}."
            f"{system_clause}{play_clause}{signal_clause}\n\n"
            "OUTPUT FORMAT — strictly required:\n"
            "  • Echo the BDL block below VERBATIM, line by line, "
            "preserving spacing.\n"
            "  • After every BDL section that you have something to say "
            "about, insert one or more annotation lines that BEGIN WITH "
            "'>>' (two right angle brackets followed by a space). Use "
            "annotations after the Bids block to comment on the auction, "
            "after the Tricks block to comment on play, and again at the "
            "very end of the file with your final recommendation.\n"
            "  • Do NOT modify any non-annotation line. Do NOT add a "
            "preamble or a trailing summary outside the BDL frame.\n"
            "  • Keep most annotations under ~25 words; multiple short "
            "ones beat one long one. The card-play 'Plan:' / 'Tricks "
            "needed:' / 'Entries:' / 'Threat:' / 'Trick-sketch' lines may "
            "each run to ~40 words to give a full picture.\n"
            "  • End with a single '>> Recommendation: <bid-or-card>' "
            "line that names exactly one action.\n\n"
            "Read seat letters straight off the N/E/S/W column headers in "
            "the Bids block and the leader/winner markers in the Tricks "
            "block — do not infer seats from order alone. If a BEN "
            "card-play suggestion is shown, say whether you agree (BEN "
            "card-play is system-agnostic). biq bidding suggestions are "
            "NEVER shown because BEN was trained only on SAYC / 2-over-1 "
            "and can't reason about Precision / Acol / French calls.\n\n"
            f"BDL:\n{state_text}{engine_block}"
        )

    def _claude_enabled(self) -> bool:
        """Claude Code integration is opt-in (Preferences → AI & Network),
        default OFF."""
        try:
            from backend.config import get_config_manager
            return bool(get_config_manager().config.preferences.claude_code_enabled)
        except Exception:
            return False

    def _claude_disabled_notice(self):
        """Tell the user Claude is off — and offer a button straight to
        Preferences to switch it on."""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        try:
            from .dialogs.dialog_style import MESSAGEBOX_STYLESHEET
            box.setStyleSheet(MESSAGEBOX_STYLESHEET)
        except Exception:
            pass
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Claude is off")
        box.setText("Claude analysis is disabled — it's an opt-in feature.")
        box.setInformativeText(
            "Enable it in Preferences → AI & Network to use post-hand "
            "analysis, annotated transcripts and AI hints.")
        open_btn = box.addButton("Open Preferences…",
                                 QMessageBox.ButtonRole.AcceptRole)
        box.addButton("OK", QMessageBox.ButtonRole.RejectRole)
        for b in box.buttons():
            b.setMinimumWidth(b.sizeHint().width() + 16)
        # Give the text room and keep it flush-left. The shared stylesheet
        # pins QLabel min-width to 200px, so the main line otherwise clips
        # ("…disabled — i") and the informative text wraps raggedly. Per-label
        # min-width doesn't reliably widen a QMessageBox, so we force the
        # overall width with a zero-height spacer spanning the grid's columns;
        # the labels then wrap cleanly at the wider box.
        from PyQt6.QtWidgets import QLabel, QSpacerItem, QSizePolicy
        for lbl in box.findChildren(QLabel):
            if lbl.objectName() in ("qt_msgbox_label",
                                    "qt_msgbox_informativelabel"):
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter)
        grid = box.layout()
        try:
            # Force the TEXT column wide so the labels fill it and wrap
            # cleanly. The spacer has to land in the same column as the
            # message labels (column 2 in current Qt; detected here so a
            # layout change can't silently re-narrow the box).
            text_col = grid.columnCount() - 1
            for i in range(grid.count()):
                w = grid.itemAt(i).widget()
                if w is not None and w.objectName() == "qt_msgbox_label":
                    text_col = grid.getItemPosition(i)[1]
                    break
            grid.addItem(
                QSpacerItem(460, 0, QSizePolicy.Policy.Minimum,
                            QSizePolicy.Policy.Minimum),
                grid.rowCount(), text_col)
        except Exception:
            pass
        box.exec()
        if box.clickedButton() is open_btn:
            try:
                self._on_preferences()
            except Exception:
                pass

    def _qplus_availability(self) -> str:
        """'none' (default) / 'demo' / 'full' — gates closed-room / Q-NET."""
        try:
            from backend.config import get_config_manager
            return getattr(get_config_manager().config.preferences,
                           "qplus_availability", "none")
        except Exception:
            return "none"

    def _qplus_available(self) -> bool:
        return self._qplus_availability() != "none"

    def _apply_qplus_visibility(self):
        """Show closed-room / Q-NET / Q-Plus features only when a Q-Plus
        build is configured (Preferences → AI & Network). Default: hidden."""
        avail = self._qplus_available()
        # NOTE: the Closed Room button is intentionally NOT gated on Q-Plus.
        # Its "Internal all-biq" mode plays all four seats with biq bots and
        # needs no Q-Plus build, so the button stays available always (its
        # visibility is governed by the normal opening/toolbar logic). Only
        # the genuinely Q-Plus-dependent menu actions are hidden below.
        for attr in ("ingest_qplus_action", "ingest_lin_action",
                     "qplus_sim_action", "kill_wine_action"):
            a = getattr(self, attr, None)
            if a is not None:
                a.setVisible(avail)

    def _on_kill_all_wine(self):
        """Force-quit Q-Plus + all wine so a stale Q-NET socket / hung Q-Plus
        can't block the next closed-room run."""
        import subprocess
        from .dialogs.dialog_style import MESSAGEBOX_STYLESHEET
        box = QMessageBox(self)
        box.setStyleSheet(MESSAGEBOX_STYLESHEET)
        box.setWindowTitle("Kill all wine processes?")
        box.setText(
            "Force-quit Q-Plus and ALL wine processes?\n\n"
            "This clears a stale Q-NET listen socket and any hung Q-Plus so "
            "biq E/W can attach cleanly next time. Any unsaved Q-Plus score "
            "table is lost.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        # Scope the wine teardown to the Q-Plus WINEPREFIX (wineserver -k tears
        # down exactly that prefix's session) and only SIGKILL the named Windows
        # binaries. A broad `pkill -f wine` is NOT used — it matched and killed
        # bridgeIQ itself. The remaining patterns name the .EXE/.py explicitly so
        # they can never hit a python process.
        prefix = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "WP"))
        script = (f'WINEPREFIX="{prefix}" wineserver -k 2>/dev/null; sleep 0.3; '
                  "pkill -9 -f 'QBRIDGE\\.EXE'; pkill -9 -f 'Q-NET\\.EXE'; "
                  "pkill -9 -f 'biq_qnet_client\\.py'")
        try:
            subprocess.run(["bash", "-c", script], timeout=15)
            self.status_label.setText(
                "Exited Q-Plus and killed its wine session — Q-NET socket cleared.")
        except Exception as e:
            self.status_label.setText(f"Kill wine failed: {e}")

    def _run_claude_with_dialog(self, prompt: str, title: str, wait_label: str,
                                 timeout_seconds: int = 900, preamble: str = "",
                                 bdl_text: str = "",
                                 cache_key: str | None = None,
                                 extended_thinking: bool = True):
        """Run claude -p with a progress dialog, then show the result.

        Shared between the end-of-hand analysis and the Hint button. Shows an
        error dialog if claude fails so the user never sees silent failure.

        When `bdl_text` is provided, the result is displayed via the
        BDL-with-annotations renderer: Claude's reply (which the prompt
        instructs it to format as the BDL with `>>` annotation lines
        inserted) is shown verbatim with annotations highlighted. If
        Claude failed to follow the format, the renderer falls back to
        appending its free-form text after the BDL.

        When `cache_key` is provided, the most-recent result for that
        key is cached on the MainWindow. A repeat call with the same
        key short-circuits the Claude invocation entirely and re-shows
        the cached dialog — used by the Hint button so dismissing then
        re-clicking before play has advanced doesn't burn another
        Claude turn.
        """
        # Claude Code must be enabled in Preferences (default off).
        if not self._claude_enabled():
            self._claude_disabled_notice()
            return
        # Cache short-circuit — check BEFORE we open the progress
        # dialog or spawn the subprocess.
        if cache_key is not None:
            cached = getattr(self, '_claude_cache', {}).get(cache_key)
            if cached is not None:
                if bdl_text:
                    preface = preamble.rstrip()
                    framed = bdl_text
                    if preface:
                        framed = preface + "\n\n" + bdl_text
                    self._show_annotated_bdl_dialog(
                        title=title,
                        bdl_text=framed,
                        claude_text=cached,
                        error=False,
                    )
                else:
                    self._show_plain_dialog(
                        title, f"{preamble}{cached}", error=False)
                return
        import shutil
        import subprocess
        import threading
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                                      QScrollArea, QProgressBar)
        from PyQt6.QtGui import QFont

        if not shutil.which('claude'):
            self._show_plain_dialog(title,
                                    "Claude CLI is not installed on this machine.\n"
                                    "Install it to enable this feature.",
                                    error=True)
            return

        # Progress dialog
        from PyQt6.QtWidgets import QApplication as _QApp
        progress = QDialog(_QApp.activeModalWidget() or self)   # Cancel usable beside a modal Compare
        progress.setWindowTitle(title)
        progress.setFixedSize(420, 110)
        progress.setStyleSheet("background-color: #e8e8f0; color: #000;")
        p_layout = QVBoxLayout(progress)
        p_label = QLabel(wait_label)
        p_label.setFont(QFont("Arial", 12))
        p_label.setStyleSheet("color: #000;")
        p_layout.addWidget(p_label)
        p_bar = QProgressBar()
        p_bar.setRange(0, 0)  # Indeterminate
        p_bar.setStyleSheet("QProgressBar { border: 1px solid #aaa; border-radius: 3px; }"
                            "QProgressBar::chunk { background-color: #4a9; }")
        p_layout.addWidget(p_bar)
        # Cancel — Claude can take a long time; let the user bail out. Cancelling
        # kills the subprocess and skips rendering.
        cancel_holder = {'cancelled': False}
        # Set once the run completes normally, BEFORE we close the progress
        # dialog. Closing a QDialog emits `rejected` (Qt calls reject() from
        # closeEvent), which is wired to _cancel_claude — without this guard a
        # successful request would mark itself cancelled at teardown and the
        # result would be thrown away ("Claude request cancelled" every time).
        done_holder = {'done': False}
        proc_holder = {'proc': None}
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("QPushButton { padding: 6px 18px; }")

        def _cancel_claude():
            if done_holder['done']:
                return
            cancel_holder['cancelled'] = True
            cancel_btn.setEnabled(False)
            p_label.setText("Cancelling…")
            proc = proc_holder.get('proc')
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        cancel_btn.clicked.connect(_cancel_claude)
        progress.rejected.connect(_cancel_claude)   # the window's X also cancels
        p_layout.addWidget(cancel_btn)
        progress.show()
        QApplication.processEvents()

        # Pidfile so run.sh waits for us (belt + suspenders)
        pidfile = None
        pid_dir = os.environ.get('CRITIQUE_PID_DIR')
        if pid_dir:
            try:
                os.makedirs(pid_dir, exist_ok=True)
                pidfile = os.path.join(pid_dir, f"{os.getpid()}-hint-{id(prompt)}.pid")
                with open(pidfile, 'w') as f:
                    f.write(str(os.getpid()))
            except Exception:
                pidfile = None

        result_holder = {'text': None, 'error': None}

        def _run_claude():
            # Opus 4.8. Extended thinking is the main latency driver, so the
            # caller can turn it OFF (e.g. the "Quick verdict (fast)" scope) to
            # get a concise answer back well inside the timeout; deeper scopes
            # keep it on. Popen (not subprocess.run) so Cancel can kill it.
            cmd = ['claude', '-p', '--model', 'claude-opus-4-8']
            if extended_thinking:
                cmd += ['--thinking', 'enabled']
            cmd += ['--max-turns', '1', prompt]
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                proc_holder['proc'] = proc
                try:
                    stdout, stderr = proc.communicate(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.communicate()
                    except Exception:
                        pass
                    result_holder['error'] = (
                        f"claude timed out after {timeout_seconds} seconds.")
                    return
                if cancel_holder['cancelled']:
                    return
                stdout = (stdout or '').strip()
                stderr = (stderr or '').strip()
                if proc.returncode == 0 and len(stdout) > 5:
                    result_holder['text'] = stdout
                else:
                    parts = [f"claude exited {proc.returncode}"]
                    if stderr:
                        parts.append(f"stderr: {stderr[:500]}")
                    if stdout:
                        parts.append(f"stdout: {stdout[:500]}")
                    result_holder['error'] = "\n".join(parts)
            except Exception as e:
                result_holder['error'] = f"claude call failed: {e!r}"

        thread = threading.Thread(target=_run_claude, daemon=True)
        thread.start()
        while thread.is_alive():
            QApplication.processEvents()
            thread.join(timeout=0.1)
            if cancel_holder['cancelled']:
                break
        # Make sure a cancelled subprocess is really gone.
        if cancel_holder['cancelled']:
            proc = proc_holder.get('proc')
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Mark done so the close()-triggered `rejected` below is a no-op rather
        # than a spurious cancel.
        if not cancel_holder['cancelled']:
            done_holder['done'] = True
        try:
            progress.close()
        except Exception:
            pass
        if cancel_holder['cancelled']:
            try:
                self.status_label.setText("Claude request cancelled.")
            except Exception:
                pass
            return

        if pidfile:
            try:
                os.unlink(pidfile)
            except OSError:
                pass

        text = result_holder['text']
        error = result_holder['error']

        # Cache the fresh result so a repeat hint click with the same
        # cache_key skips Claude entirely.
        if cache_key is not None and text:
            if not hasattr(self, '_claude_cache'):
                self._claude_cache = {}
            self._claude_cache[cache_key] = text

        # Render inside a guard: an exception escaping this Qt slot would abort
        # the whole app (PyQt aborts on unhandled slot exceptions) — exactly the
        # "Claude error, then the window vanished" failure. Never let that out.
        try:
            if bdl_text:
                # BDL-annotated rendering. Even on failure we still show the
                # BDL so the user sees the same context Claude was given.
                preface = preamble.rstrip()
                framed = bdl_text
                if preface:
                    framed = preface + "\n\n" + bdl_text
                self._show_annotated_bdl_dialog(
                    title=title,
                    bdl_text=framed,
                    claude_text=text or "",
                    error=not text,
                )
                if not text:
                    # Surface the error inline at the top, since the
                    # annotated dialog itself doesn't show errors.
                    self._show_plain_dialog(
                        title + " (claude error)",
                        f"Claude did not return a response.\n\n"
                        f"{error or 'No output received.'}",
                        error=True,
                    )
                return

            if text:
                self._show_plain_dialog(title, f"{preamble}{text}", error=False)
            else:
                self._show_plain_dialog(
                    title,
                    f"{preamble}Claude did not return a response.\n\n"
                    f"{error or 'No output received.'}",
                    error=True,
                )
        except Exception as ex:
            import traceback
            traceback.print_exc()
            try:
                self._show_plain_dialog(
                    title + " (display error)",
                    f"Claude returned, but rendering the result failed:\n{ex!r}",
                    error=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # PlantUML rendering — for the card-play hint plan diagrams. Tries
    # a locally installed `plantuml` binary first (fast, offline), then
    # falls back to the public plantuml.com PNG endpoint (no key, no
    # account, works any time you have internet). Each block is cached
    # by source text so re-opening the same hint doesn't re-render.
    # ------------------------------------------------------------------

    _PLANTUML_FENCE_RE = re.compile(
        r'```\s*(?:plantuml|uml)\s*\n(.*?)\n```',
        re.DOTALL | re.IGNORECASE,
    )

    @staticmethod
    def _plantuml_encode(text: str) -> str:
        """PlantUML's URL encoding: zlib-deflate the source, then map
        bytes through PlantUML's custom 6-bit alphabet
        (0-9, A-Z, a-z, '-', '_'). See PlantUML's text encoding spec.
        """
        import zlib
        deflated = zlib.compress(text.encode('utf-8'))
        raw = deflated[2:-4]  # strip zlib header + Adler-32 footer

        def enc6(b: int) -> str:
            if b < 10:
                return chr(48 + b)        # '0'-'9'
            b -= 10
            if b < 26:
                return chr(65 + b)        # 'A'-'Z'
            b -= 26
            if b < 26:
                return chr(97 + b)        # 'a'-'z'
            b -= 26
            if b == 0:
                return '-'
            if b == 1:
                return '_'
            return '?'

        out = []
        for i in range(0, len(raw), 3):
            b1 = raw[i]
            b2 = raw[i + 1] if i + 1 < len(raw) else 0
            b3 = raw[i + 2] if i + 2 < len(raw) else 0
            out.append(enc6(b1 >> 2))
            out.append(enc6(((b1 & 0x3) << 4) | (b2 >> 4)))
            out.append(enc6(((b2 & 0xF) << 2) | (b3 >> 6)))
            out.append(enc6(b3 & 0x3F))
        return ''.join(out)

    def _render_plantuml(self, uml_text: str):
        """Return PNG bytes for the given PlantUML source, or None if
        every render path failed. Tries local `plantuml` binary first
        then falls back to plantuml.com's PNG endpoint."""
        if not hasattr(self, '_plantuml_cache'):
            self._plantuml_cache = {}
        cached = self._plantuml_cache.get(uml_text)
        if cached is not None:
            return cached or None
        import shutil
        import subprocess
        png = None
        if shutil.which('plantuml'):
            try:
                r = subprocess.run(
                    ['plantuml', '-tpng', '-pipe'],
                    input=uml_text.encode('utf-8'),
                    capture_output=True, timeout=30,
                )
                if r.returncode == 0 and r.stdout:
                    png = r.stdout
            except Exception:
                png = None
        if png is None:
            try:
                import urllib.request
                encoded = self._plantuml_encode(uml_text)
                url = (
                    'https://www.plantuml.com/plantuml/png/' + encoded
                )
                with urllib.request.urlopen(url, timeout=30) as resp:
                    png = resp.read()
            except Exception:
                png = None
        self._plantuml_cache[uml_text] = png or b''
        return png

    def _replace_plantuml_with_images(self, text: str):
        """Pre-process Claude's reply: replace each ```plantuml block
        with a placeholder, render the blocks to PNG, return the
        modified text plus a list of (placeholder, png_bytes) pairs.
        Failed renders fall back to fenced code in the modified text so
        the user at least sees the source they can paste into a
        PlantUML editor by hand."""
        import base64

        chunks = []
        images = []
        last = 0
        for i, m in enumerate(self._PLANTUML_FENCE_RE.finditer(text)):
            chunks.append(text[last:m.start()])
            uml = m.group(1)
            png = self._render_plantuml(uml)
            if png:
                placeholder = f"\n<<<PLANTUML_IMG_{i}>>>\n"
                images.append((placeholder, png))
                chunks.append(placeholder)
            else:
                # Keep the source visible if we can't render.
                chunks.append(
                    "\n[PlantUML diagram — render failed; raw source below]\n"
                    f"```plantuml\n{uml}\n```\n"
                )
            last = m.end()
        chunks.append(text[last:])
        return ''.join(chunks), images

    def _show_annotated_bdl_dialog(self, title: str, bdl_text: str,
                                    claude_text: str, error: bool = False):
        """Render the BDL printout with Claude's annotations interspersed.

        Claude is asked to embed lines starting with ``>>`` at logical
        breakpoints in the BDL (after the auction, after the tricks
        block, at the end). The renderer below highlights those lines
        in italic green so the file's structure stays visible while the
        commentary sits inline next to whatever it's commenting on.

        If Claude's reply doesn't contain any ``>>`` markers (it
        ignored the format instruction), we fall back to showing the
        BDL on top and Claude's text below, separated by a banner.
        """
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QPushButton,
                                      QTextEdit)
        from PyQt6.QtGui import QFont, QTextCharFormat, QColor, QTextCursor

        # Extract any ```plantuml fenced blocks BEFORE the >> /
        # BDL split logic — we want the placeholders to flow through
        # as ordinary lines so they pick up the right surrounding
        # layout (after the BDL, between annotation lines, etc.).
        plantuml_images = []
        if claude_text:
            claude_text, plantuml_images = self._replace_plantuml_with_images(
                claude_text)

        # Pick the body text: if Claude returned a properly-annotated
        # BDL with `>>` markers in it, use that verbatim; otherwise
        # synthesize one by appending Claude's free-form text after the
        # BDL with a `>>` header so the layout stays consistent.
        if claude_text and ">>" in claude_text:
            body_text = claude_text.rstrip() + "\n"
        else:
            extra = (claude_text or "").strip()
            if not extra:
                extra = "(Claude returned no response.)"
            body_text = (
                bdl_text.rstrip() + "\n\n"
                ">> Claude (free-form, not interleaved):\n"
                + "\n".join(
                    ">> " + line if line.strip() else ">>"
                    for line in extra.splitlines()
                )
                + "\n"
            )

        from PyQt6.QtWidgets import QApplication as _QApp
        dialog = QDialog(_QApp.activeModalWidget() or self)   # usable beside a modal Compare
        dialog.setWindowTitle(title)
        # Maximizable + large default so the analysis can fill a 1920×1080 screen.
        dialog.setWindowFlags(dialog.windowFlags()
                              | Qt.WindowType.WindowMaximizeButtonHint)
        dialog.setMinimumSize(960, 640)
        dialog.resize(1280, 860)
        dialog.setStyleSheet("QDialog { background-color: #e8e8f0; color: #000; }")
        layout = QVBoxLayout(dialog)

        # Build an HTML document so annotation lines can wrap inside the
        # dialog width while BDL lines (which are column-aligned) stay
        # untouched in <pre>. Annotation continuations hang-indent under
        # the first character after `>> ` so the text reads like a margin
        # comment rather than a free-form paragraph.
        from html import escape
        BDL_FONT_PT = 22       # was 18 — bumped per request
        ANN_FONT_PT = 22
        BDL_COLOR   = "#202020"
        ANN_COLOR   = "#1a6020"
        BG_COLOR    = "#f8f8f8"

        # Group consecutive non-annotation lines into a single <pre> so
        # multi-line BDL blocks render as one whitespace-preserved chunk.
        html_parts = []

        def flush_bdl(buf):
            if not buf:
                return
            text = "\n".join(buf)
            html_parts.append(
                f'<pre style="font-family: Monospace; font-size: {BDL_FONT_PT}pt; '
                f'color: {BDL_COLOR}; margin: 0; line-height: 1.3; '
                f'white-space: pre-wrap; word-wrap: break-word;">'
                f'{escape(text)}</pre>'
            )
            buf.clear()

        # Build a lookup of placeholder → base64 image so we can swap
        # them out inline as soon as we hit one in the body stream.
        import base64
        placeholder_to_img = {}
        for placeholder, png_bytes in plantuml_images:
            try:
                b64 = base64.b64encode(png_bytes).decode('ascii')
                placeholder_to_img[placeholder.strip()] = (
                    f'<div style="margin: 12px 0; text-align: center;">'
                    f'<img src="data:image/png;base64,{b64}" '
                    f'style="max-width: 100%; border: 1px solid #aaa;'
                    f' border-radius: 4px; background: #fff;"/>'
                    f'</div>'
                )
            except Exception:
                pass

        bdl_buf: list[str] = []
        for line in body_text.splitlines():
            stripped_line = line.strip()
            if stripped_line in placeholder_to_img:
                flush_bdl(bdl_buf)
                html_parts.append(placeholder_to_img[stripped_line])
                continue
            if line.lstrip().startswith('>>'):
                flush_bdl(bdl_buf)
                # Strip the leading >> and any single space, then render the
                # comment with the >> prefix inline AND a hanging indent so
                # wrapped continuations align under the first character of
                # the comment text (matching the BDL's left margin).
                rest = line.lstrip()[2:]
                if rest.startswith(' '):
                    rest = rest[1:]
                html_parts.append(
                    f'<div style="font-family: Monospace; '
                    f'font-size: {ANN_FONT_PT}pt; color: {ANN_COLOR}; '
                    f'font-style: italic; font-weight: bold; '
                    f'margin: 2px 0; padding-left: 3ch; '
                    f'text-indent: -3ch; white-space: normal; '
                    f'word-wrap: break-word; line-height: 1.3;">'
                    f'&gt;&gt; {escape(rest)}</div>'
                )
            else:
                bdl_buf.append(line)
        flush_bdl(bdl_buf)

        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setStyleSheet(
            f"QTextEdit {{ background-color: {BG_COLOR}; color: #000;"
            f" border: 1px solid #aaa; padding: 14px; }}"
        )
        # Wrap everything to the window width — never make the user scroll
        # left/right. WrapAtWordBoundaryOrAnywhere + horizontal scrollbar OFF
        # forces even the monospace BDL blocks to reflow into the window, so a
        # maximized dialog on 1920×1080 shows all output justified to fit.
        from PyQt6.QtGui import QTextOption
        editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHtml("".join(html_parts))
        editor.moveCursor(QTextCursor.MoveOperation.Start)

        layout.addWidget(editor, stretch=1)

        ok_btn = QPushButton("OK")
        ok_btn.setFixedSize(140, 48)
        ok_btn.setStyleSheet(
            "QPushButton { background-color: #d0d0d0; color: #000;"
            " border: 1px solid #999; border-radius: 3px; font-size: 18px; }"
        )
        ok_btn.clicked.connect(dialog.accept)
        layout.addWidget(ok_btn)

        # Detached: the user wants to leave it open on a second monitor
        # next to the play view.
        from .dialogs.dialog_style import make_detachable
        make_detachable(dialog)
        dialog.show()
        dialog.raise_()
        # Hold a reference so it isn't GC'd as soon as we return.
        # Dialogs were promoted with WA_DeleteOnClose by make_detachable,
        # so previous entries may have had their underlying C++ object
        # destroyed already; calling isVisible() on a dead wrapper
        # raises RuntimeError ("wrapped C/C++ object … has been
        # deleted"), so guard the survivor sweep.
        if not hasattr(self, '_annotated_bdl_dialogs'):
            self._annotated_bdl_dialogs = []

        survivors = []
        for d in self._annotated_bdl_dialogs:
            try:
                if d.isVisible():
                    survivors.append(d)
            except RuntimeError:
                # Wrapper outlived the C++ widget — drop it.
                pass
        survivors.append(dialog)
        self._annotated_bdl_dialogs = survivors

    def _show_plain_dialog(self, title: str, body: str, error: bool = False):
        """Show a scrollable result dialog with OK button (shared helper).

        Non-modal so the user can interact with the main window — open
        the Compare dialog, the score sheet, replay a board — while the
        Claude analysis is still visible. We hold a reference on
        `self._plain_dialogs` so the widget isn't garbage-collected as
        soon as we return.
        """
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                                      QScrollArea)
        from PyQt6.QtGui import QFont

        # Parent to whatever modal window is up (the Compare dialog when it
        # was opened with exec() from the score table): a child of the main
        # window gets no input while a modal dialog is open, so the scrollbar
        # "did not work" until Compare was closed.
        from PyQt6.QtWidgets import QApplication as _QApp
        dialog = QDialog(_QApp.activeModalWidget() or self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(700, 350)
        dialog.resize(800, 450)
        dialog.setStyleSheet("QDialog { background-color: #e8e8f0; color: #000; }")
        layout = QVBoxLayout(dialog)

        # A read-only QTextBrowser scrolls on its own (wheel, scrollbar drag,
        # keys). The old word-wrapped QLabel inside a QScrollArea got its
        # height wrong, so the scrollbar did not scroll the text. Claude's
        # replies are Markdown, so render them as such (errors stay plain).
        from PyQt6.QtWidgets import QTextBrowser
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setFont(QFont("Arial", 14 if error else 16))
        view.setStyleSheet(
            f"QTextBrowser {{ color: {'#803030' if error else '#000'};"
            " background-color: #f8f8f8; border: 1px solid #aaa; padding: 10px; }"
        )
        if error:
            view.setPlainText(body)
        else:
            view.setMarkdown(body)
        layout.addWidget(view, stretch=1)

        ok_btn = QPushButton("OK")
        ok_btn.setFixedSize(120, 40)
        ok_btn.setStyleSheet("QPushButton { background-color: #d0d0d0; color: #000; "
                             "border: 1px solid #999; border-radius: 3px; font-size: 16px; }")
        ok_btn.clicked.connect(dialog.close)
        layout.addWidget(ok_btn)

        # Promote to a free-floating top-level window so the user can
        # drag it to a second monitor and keep working in main_window.
        from .dialogs.dialog_style import make_detachable
        make_detachable(dialog)
        dialog.show()
        dialog.raise_()

        # Keep references to surviving dialogs; sweep dead wrappers
        # (WA_DeleteOnClose set by make_detachable destroys the C++
        # widget on close, after which isVisible() raises RuntimeError).
        if not hasattr(self, '_plain_dialogs'):
            self._plain_dialogs = []
        survivors = []
        for d in self._plain_dialogs:
            try:
                if d.isVisible():
                    survivors.append(d)
            except RuntimeError:
                pass
        survivors.append(dialog)
        self._plain_dialogs = survivors

    def _on_undo(self):
        """Take back to your last decision point (Q-Plus 'back up').

        Card play: removes cards from the end — your own AND the computer's
        responses, across a completed trick if need be — until your most recent
        card is taken back, then it's your turn to replay. Bidding: removes the
        computer's calls and your last call so you can bid again. Repeatable.
        """
        if self.network_controller.is_active:
            self.status_label.setText("Undo not available during network play")
            return
        board = self.controller.board
        if board is None:
            self.status_label.setText("Nothing to undo")
            return
        phase = self.controller.current_phase
        if phase == 'bidding':
            self._undo_last_user_bid(board)
        elif phase in ('play', 'waiting_next', 'finished'):
            self._undo_last_user_card(board)
        else:
            self.status_label.setText(
                "Undo is available during bidding and card play")

    def _seat_at(self, base, offset):
        from backend.models import Seat as _Seat
        return _Seat((int(base) + int(offset)) % 4)

    def _has_user_play(self, board) -> bool:
        """True if any card already played belongs to a seat the human controls
        (their own seat, or dummy when the human declares)."""
        seen = set()
        seqs = list(getattr(board, 'tricks', None) or [])
        ct = getattr(board, 'current_trick', None)
        if ct is not None:
            seqs.append(ct)
        for tr in seqs:
            if id(tr) in seen:
                continue
            seen.add(id(tr))
            for i in range(len(tr.cards)):
                if self.controller._human_controls_seat(
                        self._seat_at(tr.leader, i)):
                    return True
        return False

    def _undo_one_card(self, board):
        """Remove the single most-recently-played card, restore it to its
        owner's hand, and reverse any trick credit. Returns the seat whose card
        was removed, or None when there are no cards left to undo."""
        ct = getattr(board, 'current_trick', None)
        if ct is not None and ct.cards:
            # In 'waiting_next' the completed trick is BOTH current_trick and
            # board.tricks[-1] (same object) and was already credited — detach
            # and reverse the credit before pulling a card.
            if board.tricks and board.tricks[-1] is ct:
                board.tricks.pop()
                self._reverse_trick_credit(board, ct.winner)
            ct.winner = None
            idx = len(ct.cards) - 1
            card = ct.cards.pop(idx)
            seat = self._seat_at(ct.leader, idx)
            board.hands[seat].cards.append(card)
            if not ct.cards:
                board.current_trick = None
            return seat
        # Between tricks: reopen the last completed trick.
        if getattr(board, 'tricks', None):
            tr = board.tricks.pop()
            self._reverse_trick_credit(board, tr.winner)
            tr.winner = None
            idx = len(tr.cards) - 1
            card = tr.cards.pop(idx)
            seat = self._seat_at(tr.leader, idx)
            board.hands[seat].cards.append(card)
            board.current_trick = tr if tr.cards else None
            return seat
        return None

    def _reverse_trick_credit(self, board, winner):
        if winner is None or getattr(board, 'contract', None) is None:
            return
        if winner.is_ns() == board.contract.declarer.is_ns():
            board.declarer_tricks = max(0, board.declarer_tricks - 1)
        else:
            board.defense_tricks = max(0, board.defense_tricks - 1)

    def _undo_last_user_card(self, board):
        if not self._has_user_play(board):
            self.status_label.setText("Nothing of yours to take back.")
            return
        # Autoplay + undo don't mix — the engine would instantly replay.
        if self.autoplay_btn.isChecked():
            self.autoplay_btn.setChecked(False)
        last_seat = None
        count = 0
        for _ in range(64):
            seat = self._undo_one_card(board)
            if seat is None:
                break
            last_seat = seat
            count += 1
            if self.controller._human_controls_seat(seat):
                break
        self.controller.current_phase = 'play'
        if last_seat is not None:
            self.controller.current_seat = last_seat
        self._refresh_after_undo(board)
        try:
            self._advance_game()
        except Exception:
            pass
        who = last_seat.to_char() if last_seat is not None else ''
        self.status_label.setText(
            f"Took back {count} card{'s' if count != 1 else ''} — "
            f"your play ({who})")

    def _undo_last_user_bid(self, board):
        auction = getattr(board, 'auction', None) or []
        if not any(self.controller._human_controls_seat(self._seat_at(board.dealer, i))
                   for i in range(len(auction))):
            self.status_label.setText("Nothing of yours to take back.")
            return
        last_seat = None
        while auction:
            i = len(auction) - 1
            seat = self._seat_at(board.dealer, i)
            auction.pop()
            last_seat = seat
            if self.controller._human_controls_seat(seat):
                break
        self.controller.current_phase = 'bidding'
        if last_seat is not None:
            self.controller.current_seat = last_seat
        try:
            self.bidding_box.set_auction(list(auction), board.dealer)
        except Exception:
            pass
        try:
            self._update_available_bids()
        except Exception:
            pass
        try:
            self._advance_game()
        except Exception:
            pass
        who = last_seat.to_char() if last_seat is not None else ''
        self.status_label.setText(f"Took back your last bid — your bid ({who})")

    def _refresh_after_undo(self, board):
        """Re-render hands + the current trick after a take-back."""
        for s in Seat:
            try:
                self.table_view.hand_widgets[s].set_hand(board.hands[s])
            except Exception:
                pass
        try:
            self.table_view.clear_trick()
            ct = getattr(board, 'current_trick', None)
            if ct:
                for i, c in enumerate(ct.cards):
                    self.table_view.play_card_to_trick(
                        self._seat_at(ct.leader, i), c)
        except Exception:
            pass
        try:
            self._update_button_states()
        except Exception:
            pass

    def _on_next_card(self):
        """Advance to next trick after a trick completes"""

        # Debug: verify current_seat matches the last trick winner
        last_winner = self.controller.get_trick_winner()
        if last_winner and self.controller.current_seat != last_winner:
            pass

        if self.controller.current_phase == 'waiting_next':
            # In network mode, broadcast to sync the other player
            if self.network_controller.is_active:
                self.network_controller.broadcast_trick_clear()

            self.controller.advance_to_next_trick()
            self.table_view.clear_trick()
            self.next_card_btn.setEnabled(False)
            self._advance_game()
        else:
            pass

    def _on_next_card_shortcut(self):
        """Spacebar shortcut for Next card.

        We honour the button's enabled state so the spacebar can't fire
        when auto-advance has already queued up a callback or when the
        opposing side hasn't finished yet (network).
        """
        if self.next_card_btn.isVisible() and self.next_card_btn.isEnabled():
            self._on_next_card()

    # Game flow

    def _on_bid_made(self, bid: Bid):
        """Handle human bid"""
        if self.controller.current_phase != 'bidding':
            return

        if not self.controller.is_human_turn():
            return

        bidder = self.controller.current_seat

        # Blunder check — run the native bidder for this seat with
        # the recorded side's system, compare to the user's call,
        # and pop a Hint/Cancel dialog when they diverge. Mirror of
        # the cardplay blunder check; same per-turn warned-set so
        # re-clicking the same bid after a warning commits it
        # without re-prompting.
        if not self._maybe_warn_bid_blunder(bidder, bid):
            return

        self.controller.make_bid(bid)
        self.bidding_box.add_bid(bid)
        self.table_view.update_auction(self.controller.board.auction, self.controller.board.dealer)

        # Successful commit — clear per-call warned-bids set so the
        # next decision starts fresh.
        self._blunder_warned_bids = set()

        # Broadcast to network if active
        if self.network_controller.is_active:
            self.network_controller.broadcast_bid(bidder, bid)

        # Add to bid info window
        self._add_bid_to_info(bidder, bid, "", "")
        self._update_available_bids()

        self._advance_game()

    def keyPressEvent(self, event):
        """Full keyboard play: during the human's turn in the play phase,
        a suit key (S/H/D/C) then a rank key (A K Q J T 9-2; 0 = ten) plays
        that card — and when you must follow suit, the rank alone is enough.
        Mirrors the mouse path exactly (routes through _on_card_played), so
        every cardplay command works with EITHER input. Bidding already has
        both (the bidding box's number/letter keys + its buttons)."""
        if self._handle_play_key(event):
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Route play-keys to the card handler no matter which widget has
        focus (e.g. a combo box on the instrumented view). Inert unless it's
        the human's turn in the play phase, so it never steals keys from the
        bidding box or normal navigation."""
        try:
            from PyQt6.QtCore import QEvent
            if event.type() == QEvent.Type.KeyPress and self._handle_play_key(event):
                return True
            if (event.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                                 QEvent.Type.Show)
                    and obj in (getattr(self, 'table_view', None),
                                getattr(getattr(self, 'table_view', None),
                                        'trick_area', None),
                                getattr(self, 'bidding_box', None))):
                QTimer.singleShot(0, self._place_bidding_overlays)
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _handle_play_key(self, event) -> bool:
        c = getattr(self, 'controller', None)
        if c is None or getattr(c, 'current_phase', None) != 'play':
            return False
        if not c.is_human_turn():
            return False
        seat = c.current_seat
        if seat is None or not c.board:
            return False
        text = (event.text() or "").upper()
        suit_map = {'S': Suit.SPADES, 'H': Suit.HEARTS,
                    'D': Suit.DIAMONDS, 'C': Suit.CLUBS}
        if text in suit_map:
            self._kbd_play_suit = suit_map[text]
            self.status_label.setText(
                f"{text} — now press a rank (A K Q J T 9–2).")
            return True
        rank_chars = "AKQJT0987654321"
        if text and text in rank_chars and len(text) == 1:
            try:
                rank = Rank.from_char('T' if text in ('0', '1') else text)
            except Exception:
                return False
            suit = getattr(self, '_kbd_play_suit', None)
            hand = c.board.hands.get(seat)
            if suit is None:
                # No suit armed — if we must follow, the suit is forced.
                led = c.get_lead_suit()
                if led and hand and any(cd.suit == led for cd in hand.cards):
                    suit = led
            if suit is None:
                self.status_label.setText(
                    "Press a suit first (S/H/D/C), then the rank.")
                return True
            self._kbd_play_suit = None
            actual = next((cd for cd in (hand.cards if hand else [])
                           if cd.suit == suit and cd.rank == rank), None)
            if actual is None:
                self.status_label.setText(
                    f"No {text} of {suit.to_char()} in your hand.")
                return True
            self._on_card_played(seat, actual)   # same validated path as a click
            return True
        return False

    def _on_card_played(self, seat: Seat, card: Card):
        """Handle human card play"""

        if self.controller.current_phase != 'play':
            return

        if seat != self.controller.current_seat:
            return

        if not self.controller.is_human_turn():
            return


        # Validate the card is legal
        lead_suit = self.controller.get_lead_suit()
        hand = self.controller.board.hands.get(seat)
        if hand and lead_suit:
            has_suit = any(c.suit == lead_suit for c in hand.cards)
            if has_suit and card.suit != lead_suit:
                self.status_label.setText("Must follow suit!")
                return

        # Blunder check — run a Monte-Carlo + DDS scoring of every
        # legal card and warn the user if their pick loses ≥1 trick
        # against the best play. Two-button dialog [Hint] / [Cancel];
        # neither commits the play, so the user can either re-pick or
        # click the same card a second time to override the warning
        # (the warning is suppressed once-per-turn per card).
        if not self._maybe_warn_card_blunder(seat, card):
            return

        # Record for undo (only human cards can be undone, marked with is_human=True)
        self.card_history.append((seat, card, True))
        self.undo_btn.setEnabled(True)

        # Check if this is the opening lead (first card of play)
        is_opening_lead = (len(self.controller.board.tricks) == 0 and
                          (not self.controller.board.current_trick or
                           len(self.controller.board.current_trick.cards) == 0))

        self.table_view.play_card_to_trick(seat, card)
        self.controller.play_card(card)
        # Successful commit — clear the per-turn warned-cards set so
        # the next decision starts fresh (no carry-over warnings
        # about a card the user already discarded).
        self._blunder_warned_cards = set()

        # Broadcast to network if active
        if self.network_controller.is_active:
            self.network_controller.broadcast_card(seat, card)

            # Dummy reveal is now broadcast at the start of the play
            # phase (in _advance_play right after setup_declarer_play)
            # so the guest sees dummy at the same moment as the host.
            # No second post-opening-lead broadcast needed here.

        self._advance_game()

    def _maybe_reveal_dummy_after_lead(self):
        """Bridge rule: dummy is laid down right after the opening lead.

        Two independent state machines run here:
          1. TableView.dummy_revealed — the host's *local* visibility.
             setup_declarer_play() flips it on immediately when the
             host's side declared (declarer needs the dummy to plan)
             and off when the opposing side declared (defender mustn't
             peek before leading).
          2. self._dummy_broadcasted_for_board — whether the host has
             yet pushed the dummy reveal to guests. Always deferred to
             the opening lead, even when the host already sees dummy
             locally, because a guest on defense should never see dummy
             before the human-side opening lead lands.
        """
        board = self.controller.board
        if board is None:
            return
        ct = board.current_trick
        # Opening lead just landed when there are 0 completed tricks
        # and exactly one card sits in the current trick.
        if len(board.tricks) != 0 or ct is None or len(ct.cards) < 1:
            return

        # Local reveal — only matters when setup_declarer_play hid dummy.
        if not getattr(self.table_view, 'dummy_revealed', True):
            self.table_view.reveal_dummy()

        # One-Player Mode: dummy's hand isn't in the engine yet
        # unless the user controls declarer's side. Now that dummy is
        # exposed, prompt the user to type in dummy's 13 cards.
        try:
            self._one_player_maybe_enter_dummy()
        except Exception as ex:
            print(f"[one-player] dummy entry failed: {ex!r}",
                  flush=True)

        # Network broadcast — always after opening lead, gated by a
        # per-board flag so we don't spam the wire on every later card.
        if (self.network_controller.is_active
                and self.network_controller.is_server
                and self.controller.dummy is not None
                and not getattr(self, '_dummy_broadcasted_for_board', False)):
            dummy_seat = self.controller.dummy
            dummy_hand = board.hands.get(dummy_seat)
            if dummy_hand is not None and dummy_hand.cards:
                try:
                    self.network_controller.broadcast_dummy_reveal(
                        dummy_seat, dummy_hand
                    )
                    self._dummy_broadcasted_for_board = True
                except Exception as e:
                    print(f"[dummy reveal] broadcast failed: {e}", flush=True)

    BLUNDER_TRICK_THRESHOLD = 1.0   # ≥1 trick loss = blunder
    BLUNDER_MC_SAMPLES = 15         # MC samples per legal card

    def _maybe_warn_bid_blunder(self, seat: 'Seat', bid: 'Bid') -> bool:
        """Bidding blunder check — runs the native bidder with the
        seat's recorded side system and pops a Hint/Cancel dialog if
        the user's call differs from the engine's recommendation.

        Returns True if the call should be committed, False if the
        user dismissed via Hint or Cancel. The per-call warned-set
        means clicking the SAME bid a second time bypasses the
        warning, so the user can override after seeing the
        explanation.

        Unlike the cardplay version we don't have a continuous
        score axis — the native bidder returns a single
        recommendation. We treat ANY mismatch as a candidate
        blunder. The warning is gated by the same
        blunder_check_enabled preference.
        """
        try:
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
            if not getattr(prefs, 'blunder_check_enabled', True):
                return True
        except Exception:
            pass

        if not getattr(self, '_blunder_warned_bids', None):
            self._blunder_warned_bids = set()
        key = (seat, bid.symbol())
        if key in self._blunder_warned_bids:
            return True

        # Resolve the system spec for the bidder's side (NS or EW).
        try:
            from backend.bidding_systems import get_system
            from backend.native_bidder import NativeBidder
            from backend.models import Seat as _Seat
            ns_name, ew_name = self._current_pair_systems()
            sys_name = (ns_name
                        if seat in (_Seat.NORTH, _Seat.SOUTH)
                        else ew_name)
            spec = get_system(sys_name or 'SAYC')
        except Exception:
            return True

        # Run the native bidder against the auction-in-progress.
        try:
            resp = NativeBidder(spec).get_bid(
                self.controller.board, seat)
            engine_bid = resp.action if resp else None
        except Exception:
            return True

        if engine_bid is None:
            return True
        if self._bids_match(bid, engine_bid):
            return True

        # Stash and prompt.
        self._blunder_warned_bids.add(key)
        return self._show_bid_blunder_dialog(
            seat, bid, engine_bid,
            getattr(engine_bid, 'explanation', '') or
            getattr(resp, 'who', '') or '',
            sys_name,
        )

    @staticmethod
    def _bids_match(a, b) -> bool:
        """True iff two bids are semantically equivalent (ignoring
        metadata like .explanation). Mirrors compare_replay.py."""
        if a is None or b is None:
            return False
        if a.is_pass != b.is_pass:
            return False
        if a.is_double != b.is_double:
            return False
        if a.is_redouble != b.is_redouble:
            return False
        if a.is_pass or a.is_double or a.is_redouble:
            return True
        return (a.level == b.level and a.suit == b.suit)

    def _show_bid_blunder_dialog(self, seat, user_bid, engine_bid,
                                  explanation: str,
                                  sys_name: str) -> bool:
        """Two-button Hint/Cancel dialog for a bid that diverges from
        the native bidder's recommendation. Always returns False so
        the user's pick doesn't commit on this click."""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.setWindowTitle("Possible bidding blunder")
        msg.setTextFormat(Qt.TextFormat.RichText)
        why = (f"<div style='font-size:15px; color:#555;"
               f" margin-top:8px;'>"
               f"Why ({sys_name or 'system'}): {explanation}"
               f"</div>") if explanation else ""
        msg.setText(
            f"<div style='font-size:22px; font-weight:bold;"
            f" color:#000;'>"
            f"From {seat.to_char()} the native bidder would call "
            f"<b>{engine_bid.symbol()}</b>, not "
            f"<b>{user_bid.symbol()}</b>.</div>"
            f"<div style='font-size:18px; color:#333; margin-top:10px;'>"
            f"Get a hint (full Claude explanation) or cancel and "
            f"re-bid? Re-clicking the same call after dismissing "
            f"this dialog commits your original bid.</div>"
            f"{why}"
        )
        msg.setStyleSheet(
            "QMessageBox { background-color: #f0f0f0; }"
            "QMessageBox QLabel { color: #000;"
            " background-color: transparent; min-width: 720px;"
            " padding: 14px 10px; }"
            "QMessageBox QPushButton { font-size: 16px;"
            " padding: 8px 22px; min-width: 110px;"
            " background-color: #e0e0e0; color: #000;"
            " border: 1px solid #a0a0a0; border-radius: 3px; }"
            "QMessageBox QPushButton:hover { background-color: #d0d0d0; }"
            "QMessageBox QPushButton:pressed { background-color: #c0c0c0; }"
            "QMessageBox QPushButton:default { border: 2px solid #3070b0; }"
        )
        hint_btn = msg.addButton(
            "Hint", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = msg.addButton(
            "Cancel (re-bid)", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(hint_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is hint_btn:
            try:
                self._on_hint()
            except Exception:
                pass
        return False

    @staticmethod
    def _current_pair_systems() -> tuple:
        """Return ``(ns_system_name, ew_system_name)`` for the currently
        configured bidder. Per-pair override wins; falls back to the
        shared native_bidding_system, then to SAYC.

        Used by the BenBoardRun / BenTeamsMatch construction sites so
        a freshly-recorded deal is stamped with the real system the
        bots played, not the legacy "BEN-NN" placeholder default.
        """
        try:
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
            default = (getattr(prefs, 'native_bidding_system', '')
                       or 'SAYC') or 'SAYC'
            ns = (getattr(prefs, 'ns_bidding_system', '')
                  or default)
            ew = (getattr(prefs, 'ew_bidding_system', '')
                  or default)
            return ns, ew
        except Exception:
            return 'SAYC', 'SAYC'

    def _maybe_warn_card_blunder(self, seat: 'Seat', card: 'Card') -> bool:
        """Cardplay blunder check. Runs DDS over Monte-Carlo samples
        of the hidden hands, scores every legal card by average
        trick count from this point, and pops a Hint/Cancel dialog
        if the user's pick loses ≥ BLUNDER_TRICK_THRESHOLD tricks
        against the best card.

        Returns True if the play should be committed, False if the
        user dismissed via Hint or Cancel. The per-turn warned-set
        means clicking the SAME card a second time bypasses the
        warning — the user can override after seeing the explanation.
        """
        try:
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
            if not getattr(prefs, 'blunder_check_enabled', True):
                return True
        except Exception:
            pass

        if not getattr(self, '_blunder_warned_cards', None):
            self._blunder_warned_cards = set()
        key = (seat, card.to_str())
        if key in self._blunder_warned_cards:
            return True  # already warned about this card this turn

        try:
            board = self.controller.board
            trick_cards = []
            if board.current_trick and board.current_trick.cards:
                trick_cards = list(board.current_trick.cards)
        except Exception:
            return True

        # Trivial spots — only one legal card means there's nothing
        # to blunder on; skip the DDS run entirely.
        try:
            hand = board.hands.get(seat)
            lead_suit = (trick_cards[0].suit if trick_cards else None)
            if hand is None or not hand.cards:
                return True
            legal = [c for c in hand.cards
                     if lead_suit is None or c.suit == lead_suit]
            if not legal:
                legal = list(hand.cards)
            if len(legal) <= 1:
                return True
        except Exception:
            return True

        # Run the MC+DDS score. This can take 2–8 s depending on
        # the position; surface a status message so the user knows
        # why the click is pausing.
        try:
            self.status_label.setText(
                "Checking for blunders (Monte-Carlo + DDS)…")
            QApplication.processEvents()
            resp = self.engine.get_mc_card_play(
                board, seat, trick_cards,
                num_samples=self.BLUNDER_MC_SAMPLES,
            )
        except Exception:
            return True
        finally:
            try:
                self.status_label.setText("")
            except Exception:
                pass

        if not resp or not getattr(resp, 'candidates', None):
            return True

        # Find user's card and the best card in the candidate list.
        best = None
        user = None
        for cand in resp.candidates:
            c = getattr(cand, 'card', None)
            if c is None:
                continue
            if best is None or cand.score > best.score:
                best = cand
            if user is None and c == card:
                user = cand
        if best is None or user is None:
            return True

        loss = best.score - user.score
        if loss < self.BLUNDER_TRICK_THRESHOLD:
            return True  # not substantially worse

        # Measure against the card biq itself would play (what Hint shows),
        # so a warned card is never the hint's suggestion.
        biq_card, _ = self._biq_card_choice(board, seat, trick_cards)
        if biq_card is None or biq_card == card:
            return True
        biq = next((c for c in resp.candidates
                    if getattr(c, 'card', None) == biq_card), None)
        if biq is None:
            return True
        loss = biq.score - user.score
        if loss < self.BLUNDER_TRICK_THRESHOLD:
            return True
        best = biq

        # Mark warned BEFORE showing the dialog so a re-click after
        # Cancel/Hint goes straight through.
        self._blunder_warned_cards.add(key)
        return self._show_card_blunder_dialog(seat, card, best.card, loss)

    def _biq_card_choice(self, board, seat, trick_cards):
        """(card, reason) from the no-peek engine the robots play with,
        cached per position so Hint and the blunder check see one answer."""
        key = (seat, sum(len(t.cards) for t in board.tricks),
               tuple(c.to_str() for c in trick_cards))
        cache = getattr(self, '_biq_card_cache', None)
        if cache is None or cache[0] != id(board):
            cache = self._biq_card_cache = (id(board), {})
        if key not in cache[1]:
            try:
                from backend import nopeek
                explain = {}
                card = nopeek.decide(board, seat, list(trick_cards),
                                     explain=explain)
                cache[1][key] = (card, explain.get("reason", ""))
            except Exception:
                return None, ""
        return cache[1][key]

    def _show_card_blunder_dialog(self, seat, user_card, best_card,
                                   trick_loss) -> bool:
        """Two-button dialog: Hint / Cancel. Always returns False so
        the user's blunder doesn't commit on this click. Re-clicking
        the same card after dismissal bypasses the check (see the
        warned-set in _maybe_warn_card_blunder)."""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.setWindowTitle("Possible blunder")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            f"<div style='font-size:22px; font-weight:bold; color:#000;'>"
            f"Playing {user_card.to_str()} from "
            f"{seat.to_char()} looks like a blunder.</div>"
            f"<div style='font-size:18px; color:#333; margin-top:10px;'>"
            f"Monte-Carlo + double-dummy says it loses about "
            f"<b>{trick_loss:.1f} trick"
            f"{'s' if trick_loss >= 1.5 else ''}</b> on average "
            f"compared with the best play.</div>"
            f"<div style='font-size:18px; color:#333; margin-top:8px;'>"
            f"Get a hint (BridgeIQ's recommendation + Claude's "
            f"explanation), or cancel and pick again? "
            f"Re-clicking the same card after dismissing this "
            f"dialog will commit your original play.</div>"
        )
        msg.setStyleSheet(
            "QMessageBox { background-color: #f0f0f0; }"
            "QMessageBox QLabel { color: #000;"
            " background-color: transparent; min-width: 720px;"
            " padding: 14px 10px; }"
            "QMessageBox QPushButton { font-size: 16px;"
            " padding: 8px 22px; min-width: 110px;"
            " background-color: #e0e0e0; color: #000;"
            " border: 1px solid #a0a0a0; border-radius: 3px; }"
            "QMessageBox QPushButton:hover { background-color: #d0d0d0; }"
            "QMessageBox QPushButton:pressed { background-color: #c0c0c0; }"
            "QMessageBox QPushButton:default { border: 2px solid #3070b0; }"
        )
        hint_btn = msg.addButton(
            "Hint", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = msg.addButton(
            "Cancel (pick again)", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(hint_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is hint_btn:
            try:
                self._on_hint()
            except Exception:
                pass
        return False  # neither Hint nor Cancel commits the play

    def _advance_game(self):
        """Advance the game state"""
        # If we just played the opening lead, dummy is now visible —
        # check before per-phase dispatch so the next status update can
        # already see the revealed dummy.
        self._maybe_reveal_dummy_after_lead()
        if self.controller.current_phase == 'bidding':
            self._advance_bidding()
        elif self.controller.current_phase == 'play':
            self._advance_play()
        elif self.controller.current_phase == 'waiting_next':
            self._handle_trick_complete()
        elif self.controller.current_phase == 'finished':
            self._show_result()
        # Refresh Hint enable state — only on the local user's turn so
        # players can't peek at another seat's hand by clicking Hint
        # while it isn't their turn.
        self._refresh_hint_button_state()
        # Refresh Claim / Undo enable state every turn so Claim is available
        # throughout card play (it was created disabled and only refreshed in
        # one place, leaving it greyed out during the hand).
        try:
            self._update_button_states()
        except Exception:
            pass

    def _refresh_hint_button_state(self):
        """Grey the Hint toolbar button when it isn't the user's turn."""
        if not hasattr(self, 'hint_btn'):
            return
        try:
            self.hint_btn.setEnabled(self._is_local_user_turn())
        except Exception:
            # Button enable state is cosmetic — don't let a transient
            # state error block the game flow.
            pass

    def _advance_bidding(self):
        """Advance bidding phase"""
        self.bidding_box.set_current_bidder(self.controller.current_seat)

        current_seat = self.controller.current_seat

        # Check if we're in network mode
        if self.network_controller.is_active:
            is_mine = self.network_controller.is_my_seat(current_seat)
            is_remote = self.network_controller.is_remote_seat(current_seat)
            if is_mine:
                # My turn - enable bidding
                self.bidding_box.set_enabled(True)
                self.status_label.setText(
                    f"Your bid ({current_seat.to_char()})"
                )
                return
            elif is_remote:
                # Remote player's turn - wait for them
                self.bidding_box.set_enabled(False)
                who = "partner" if self.network_controller.client_role == "partner" else "opponent"
                self.status_label.setText(
                    f"Waiting for {who} ({current_seat.to_char()})..."
                )
                return
            else:
                # AI seat
                if self.network_controller.is_client:
                    # Client waits for server to broadcast AI bid
                    self.bidding_box.set_enabled(False)
                    self.status_label.setText(f"Waiting for AI ({current_seat.to_char()})...")
                    return
                # Server runs the AI - fall through to engine handling

        # Standard single-player mode — when autoplay is on, run the
        # engine for every seat including the human's so the whole
        # auction unfolds without clicking. Mirrors what _advance_play
        # does for card play.
        autoplay_active = self.autoplay_btn.isChecked()
        if self.controller.is_human_turn() and not autoplay_active:
            self.bidding_box.set_enabled(True)
            self.status_label.setText(
                f"Your bid ({current_seat.to_char()})"
            )
            # Slam-opportunity check — fires the Q-Plus style
            # simulation pop-up exactly once per deal when the
            # auction looks slammish on the user's turn. Deferred
            # via QTimer so the bidding box paints first.
            QTimer.singleShot(0, self._maybe_popup_slam_simulation)
        else:
            # One-Player non-peek: the engine must NEVER be asked to bid a seat
            # whose hand it isn't allowed to know (even under autoplay). Fall
            # back to manual entry via the bidding box, mirroring the per-card
            # entry guard in _advance_play.
            if (getattr(self, '_one_player_active', False)
                    and not self._one_player_seat_known(current_seat)):
                self.bidding_box.set_enabled(True)
                self.status_label.setText(
                    f"One-Player: enter the bid {current_seat.to_char()} "
                    "made at the table.")
                return

            self.bidding_box.set_enabled(False)
            self.status_label.setText(
                f"Thinking ({current_seat.to_char()})..."
            )

            # Request bid from engine
            if self.engine_worker:
                self.engine_worker.request_bid(
                    self.controller.board, current_seat
                )

    def _advance_play(self):
        """Advance play phase"""
        trick = self.controller.board.current_trick
        trick_info = f"cards={[c.to_str() for c in trick.cards]}, leader={trick.leader}" if trick else "None"
        self.bidding_box.set_enabled(False)

        # Setup declarer play if just starting
        # `self.table_view.declarer` is a Seat (IntEnum where NORTH == 0),
        # so `not …declarer` would re-fire setup_declarer_play (and the
        # dummy-reveal broadcast inside it) on every _advance_play tick
        # whenever declarer is North. Use an explicit None check.
        if (self.controller.board.contract
                and self.table_view.declarer is None):
            # Mirror the controller's "does the local user control the
            # declarer partnership" flag onto the table view so it can
            # decide whether to reveal declarer face up.
            self.table_view.human_controls_declarer = bool(
                getattr(self.controller, 'human_controls_declarer', False)
            )
            # Reset the per-board "dummy already broadcast" flag the
            # first time we enter play on this hand. Cheaper than
            # resetting in every _on_new_deal / new-deal path.
            self._dummy_broadcasted_for_board = False
            self.table_view.setup_declarer_play(self.controller.board.contract)
            self.table_view.set_auction_complete("bidding finished")
            # Hide right panel (bidding box + analysis) during card play —
            # UNLESS this deal carries a book note, which stays visible on
            # the right so the player can read the textbook's line while
            # playing. Keep only the note in that case (bidding box hidden).
            has_book_note = bool(
                getattr(self, 'book_note', None)
                and self.book_note.toPlainText().strip())
            self.right_panel.setVisible(has_book_note)
            # The bidding box is a table overlay now, not in the right panel.
            self.bidding_box.setVisible(False)
            if has_book_note:
                self.analysis_label.setVisible(False)
                self.book_note.setVisible(True)
            self.next_deal_btn.setVisible(False)
            # Teaching panel: populate with auction inferences now
            # that cardplay is starting. Only if the user has the
            # View → Show auction inferences toggle on (off by
            # default, just like wbridge5's "Info" button).
            if (hasattr(self, "teaching_panel_action")
                    and self.teaching_panel_action.isChecked()):
                self._refresh_inferences_from_board()
            # Hide bid info window during card play — and undo the table
            # right-shift it caused, so cardplay isn't off-centre / clipped.
            self.bid_info_dock.hide()
            try:
                self.table_view.set_left_inset(0)
            except Exception:
                pass
            # Dummy reveal is broadcast strictly after the opening lead
            # — see _maybe_reveal_dummy_after_lead. Even when the host's
            # side declared and dummy is already shown locally, guests
            # on defense must wait for the lead before they get to peek.

        # Update tricks display
        self.table_view.update_tricks(
            self.controller.board.declarer_tricks,
            self.controller.board.defense_tricks
        )

        # Get current lead suit
        lead_suit = self.controller.get_lead_suit()

        # Disable all hands first
        for seat in Seat:
            self.table_view.set_hand_selectable(seat, False)

        current_seat = self.controller.current_seat
        is_human = self.controller.is_human_turn()
        autoplay_active = self.autoplay_btn.isChecked()

        # Check if we're in network mode
        if self.network_controller.is_active:
            declarer = self.controller.declarer
            dummy = self.controller.dummy

            # Determine who should play this seat
            # If current seat is dummy, declarer plays it
            actual_player = declarer if current_seat == dummy else current_seat


            if self.network_controller.is_my_seat(actual_player):
                # My turn - enable hand selection (could be my hand or dummy if I'm declarer)
                self.table_view.set_hand_selectable(current_seat, True, lead_suit)
                if current_seat == dummy:
                    self.status_label.setText(f"Play dummy ({current_seat.to_char()})")
                else:
                    self.status_label.setText(f"Your play ({current_seat.to_char()})")
                return
            elif self.network_controller.is_remote_seat(actual_player):
                # Remote player's turn - wait
                who = "partner" if self.network_controller.client_role == "partner" else "opponent"
                self.status_label.setText(f"Waiting for {who} ({current_seat.to_char()})...")
                return
            else:
                # AI seat
                if self.network_controller.is_client:
                    # Client waits for server to broadcast AI card
                    self.status_label.setText(f"Waiting for AI ({current_seat.to_char()})...")
                    return
                # Server runs the AI - fall through to engine handling

        # One-Player Mode: if the seat to play is one whose hand the
        # engine doesn't have, pop the per-card entry dialog instead
        # of waiting on a click that would never come (the hand is
        # empty in the model). Per .one-player the program never
        # invents a card for these seats — autoplay still applies to
        # the user's own + dummy's cards, but unknown opponents must
        # be entered every time.
        if (getattr(self, '_one_player_active', False)
                and not self._one_player_seat_known(current_seat)):
            self.status_label.setText(
                f"One-Player: enter the card {current_seat.to_char()} "
                "played at the table.")
            QTimer.singleShot(
                0,
                lambda s=current_seat:
                    self._one_player_run_card_entry(s))
            return

        # Standard single-player mode
        # If autoplay is on, use engine for all seats including human
        if is_human and not autoplay_active:
            # Enable the current hand for human
            self.table_view.set_hand_selectable(
                current_seat, True, lead_suit
            )
            self.status_label.setText(
                f"Your play ({current_seat.to_char()})"
            )
        else:
            self.status_label.setText(
                f"Thinking ({current_seat.to_char()})..."
            )

            # Request card from engine
            if self.engine_worker:
                trick_cards = []
                if self.controller.board.current_trick:
                    trick_cards = self.controller.board.current_trick.cards
                started = self.engine_worker.request_card(
                    self.controller.board, current_seat, trick_cards
                )
                if not started:
                    # Worker was busy, retry after a delay
                    QTimer.singleShot(200, self._advance_play)
                else:
                    # Watchdog: if the engine doesn't produce a card
                    # within a reasonable window, force a legal-card
                    # fallback so the table doesn't sit on
                    # "Thinking (X)…" forever. Every successful
                    # request bumps the token; only the most-recent
                    # token's timer ever fires the rescue path.
                    self._engine_card_token = (
                        getattr(self, '_engine_card_token', 0) + 1
                    )
                    self._engine_card_seat = current_seat
                    token = self._engine_card_token
                    # Was 45 s — long enough that the user assumes the
                    # game has frozen. 20 s is comfortably above BEN's
                    # normal think-time on a 4-card-out endgame and
                    # still gives the engine plenty of room before we
                    # fall back to a legal card.
                    QTimer.singleShot(
                        20_000,
                        lambda t=token, s=current_seat:
                            self._engine_card_watchdog(t, s)
                    )
            else:
                pass

    def _engine_card_watchdog(self, token: int, seat: 'Seat'):
        """Rescue the table when the BEN engine never returns a card
        for `seat`. We arm one of these per `request_card` and stamp it
        with a token; if a later request supersedes us (token mismatch),
        or if the controller has moved past play, this timer is a
        no-op. Otherwise we synthesize a legal-card fallback (mirrors
        the response.action == None path in _on_engine_card).

        Before falling back we capture a freeze report so the next
        engine hang can be diagnosed without the user having to
        remember the py-spy / gdb procedure — see
        _capture_engine_freeze_report for what gets written.
        """
        if getattr(self, '_engine_card_token', 0) != token:
            return  # superseded by a newer request
        if self.controller.current_phase != 'play':
            return
        if self.controller.current_seat != seat:
            return

        # Capture diagnostics FIRST, before we mutate state with the
        # fallback play. This way the worker thread is still parked in
        # whatever frame caused the hang.
        report_path = None
        try:
            report_path = self._capture_engine_freeze_report(seat)
        except Exception as e:
            print(f"[engine watchdog] freeze report capture failed: {e!r}",
                  flush=True)

        # Still waiting on the same seat after the timeout — pick a
        # legal card and advance.
        hand = self.controller.board.hands.get(seat)
        if not hand or not hand.cards:
            return
        try:
            lead_suit = self.controller.get_lead_suit()
        except Exception:
            lead_suit = None
        if lead_suit is not None:
            same_suit = [c for c in hand.cards if c.suit == lead_suit]
            card = same_suit[0] if same_suit else hand.cards[0]
        else:
            card = hand.cards[0]
        print(f"[engine watchdog] {seat.to_char()} timed out; "
              f"playing {card.to_str()} as fallback", flush=True)
        try:
            self.table_view.play_card_to_trick(seat, card)
            self.controller.play_card(card)
            if (self.network_controller.is_active
                    and self.network_controller.is_server):
                self.network_controller.broadcast_card(seat, card)
        except Exception as e:
            print(f"[engine watchdog] fallback play failed: {e!r}", flush=True)
            return
        msg = f"Engine timeout for {seat.to_char()} — fallback played."
        if report_path:
            msg += f"  Freeze report: {report_path}"
        self.status_label.setText(msg)
        QTimer.singleShot(200, self._advance_game)

    def _capture_engine_freeze_report(self, seat: 'Seat') -> str:
        """Write a per-freeze diagnostic dump to DATA/freeze_reports/.

        Bundles everything you'd want when an engine hang reproduces:
          • Python stack of EVERY thread in this process via
            ``sys._current_frames()`` — no ptrace needed, so it works
            without sudo. The EngineWorker thread is the one whose top
            frame is inside backend (engine.py / pimc / sample.py
            / nn / ddsolver), and that frame names the exact code
            path that's stuck.
          • A native (gdb) backtrace of every thread when ``gdb`` is
            on PATH and ptrace_scope allows attaching to ourselves —
            this is best-effort; if it fails the report still has the
            Python side, which is the high-signal piece.
          • Game state at the moment of the freeze: dealer, vulnerability,
            contract, declarer, dummy, current_seat, all four hands,
            recent action history, and the relevant preferences
            (use_monte_carlo_play / use_double_dummy_play). That makes
            the deal reproducible offline.

        Returns the path to the written report (or '' on failure) so
        the watchdog can surface it in the status bar.
        """
        import os
        import sys
        import datetime
        import traceback as tb_mod
        from pathlib import Path

        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        # Sit alongside the BDL logs so the user has one obvious place
        # to look for hand-related diagnostics.
        try:
            log_dir = Path(self.game_logger.log_dir).parent / "freeze_reports"
        except Exception:
            log_dir = Path(os.path.expanduser(
                "~/.bridgeIQ/freeze_reports"))
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"freeze-{ts}-{seat.to_char()}.txt"

        lines = []
        lines.append(f"BridgeIQ engine-freeze report")
        lines.append(f"timestamp:   {datetime.datetime.now().isoformat()}")
        lines.append(f"pid:         {os.getpid()}")
        lines.append(f"frozen seat: {seat.to_char()}")

        # --- Game state ---
        try:
            board = self.controller.board
            lines.append("")
            lines.append("=== Game state ===")
            lines.append(f"board #:       {getattr(board, 'board_number', '?')}")
            lines.append(f"dealer:        {board.dealer.to_char() if board else '?'}")
            lines.append(f"vulnerability: "
                         f"{board.vulnerability.name if board else '?'}")
            lines.append(f"current phase: {self.controller.current_phase}")
            lines.append(f"current seat:  "
                         f"{self.controller.current_seat.to_char() if self.controller.current_seat else '?'}")
            if self.controller.declarer is not None:
                lines.append(f"declarer:      {self.controller.declarer.to_char()}")
            if self.controller.dummy is not None:
                lines.append(f"dummy:         {self.controller.dummy.to_char()}")
            if board and board.contract:
                lines.append(f"contract:      {board.contract.to_str()} "
                             f"by {board.contract.declarer.to_char()}")
            lines.append("")
            lines.append("Hands (current):")
            from backend.models import Suit as _Suit
            for s in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
                h = board.hands.get(s) if board else None
                if h is None:
                    lines.append(f"  {s.to_char()}: (unknown)")
                    continue
                suit_strs = []
                for suit in [_Suit.SPADES, _Suit.HEARTS,
                             _Suit.DIAMONDS, _Suit.CLUBS]:
                    cards = sorted([c for c in h.cards if c.suit == suit],
                                   key=lambda c: c.rank)
                    sym = {_Suit.SPADES: 'S', _Suit.HEARTS: 'H',
                           _Suit.DIAMONDS: 'D', _Suit.CLUBS: 'C'}[suit]
                    text = ''.join(c.rank.to_char() for c in cards) or '-'
                    suit_strs.append(f"{sym}:{text}")
                lines.append(f"  {s.to_char()}: {' '.join(suit_strs)}")
            ct = getattr(board, 'current_trick', None) if board else None
            if ct and ct.cards and ct.leader is not None:
                played = []
                for j, card in enumerate(ct.cards):
                    p_seat = Seat((ct.leader.value + j) % 4)
                    played.append(f"{p_seat.to_char()}:{card.to_str()}")
                lines.append(f"current trick: {' '.join(played)}  "
                             f"(leader {ct.leader.to_char()})")
            tricks = getattr(board, 'tricks', []) if board else []
            lines.append(f"tricks completed: {len(tricks)}")
            if board and board.auction:
                bidder = board.dealer
                auction = []
                for bid in board.auction:
                    auction.append(f"{bidder.to_char()}:{bid.to_str()}")
                    bidder = bidder.next()
                lines.append("auction: " + " ".join(auction))
        except Exception as e:
            lines.append(f"(game-state capture failed: {e!r})")

        # --- Preferences (so we know which engine path was on) ---
        try:
            prefs = self.config_manager.config.preferences
            lines.append("")
            lines.append("=== Preferences ===")
            for attr in ('use_monte_carlo_play', 'use_double_dummy_play',
                         'bidding_engine', 'native_bidding_system',
                         'nosearch'):
                if hasattr(prefs, attr):
                    lines.append(f"  {attr}: {getattr(prefs, attr)}")
        except Exception as e:
            lines.append(f"(prefs capture failed: {e!r})")

        # --- Python stacks of every live thread ---
        # sys._current_frames() works in-process: no ptrace needed, no
        # sudo. The EngineWorker thread is the one with a frame inside
        # backend; its topmost frame is the smoking gun.
        lines.append("")
        lines.append("=== Python thread stacks (sys._current_frames) ===")
        try:
            import threading
            tid_to_name = {t.ident: t.name for t in threading.enumerate()}
            for tid, frame in sys._current_frames().items():
                name = tid_to_name.get(tid, f"tid {tid}")
                lines.append("")
                lines.append(f"--- Thread: {name} (tid {tid}) ---")
                stack = tb_mod.format_stack(frame)
                lines.extend(s.rstrip() for s in stack)
        except Exception as e:
            lines.append(f"(thread-stack capture failed: {e!r})")

        # --- Native (gdb) backtrace, best-effort ---
        # Skipped quietly when gdb is missing or ptrace_scope blocks
        # us. Captures C-level frames inside DDS / native MC code,
        # which is invisible to py-spy and sys._current_frames.
        lines.append("")
        lines.append("=== Native backtrace (gdb -batch) ===")
        try:
            import shutil
            import subprocess
            if shutil.which('gdb') is None:
                lines.append("(gdb not installed — skipped)")
            else:
                gdb_cmd = [
                    'gdb', '-p', str(os.getpid()), '-batch',
                    '-ex', 'set pagination off',
                    '-ex', 'thread apply all bt',
                    '-ex', 'detach',
                ]
                r = subprocess.run(gdb_cmd, capture_output=True,
                                   text=True, timeout=10)
                if r.returncode == 0 and r.stdout:
                    lines.append(r.stdout)
                else:
                    err = (r.stderr or '').splitlines()[:5]
                    lines.append(
                        "(gdb attach failed — likely "
                        "/proc/sys/kernel/yama/ptrace_scope blocks "
                        "self-attach. Run\n"
                        "   echo 0 | sudo tee /proc/sys/kernel/yama/"
                        "ptrace_scope\n"
                        "to allow gdb here for the next freeze.)\n"
                        "stderr head:\n  " + "\n  ".join(err))
        except subprocess.TimeoutExpired:
            lines.append("(gdb attach timed out after 10s — skipped)")
        except Exception as e:
            lines.append(f"(gdb attach failed: {e!r})")

        try:
            path.write_text("\n".join(lines) + "\n")
        except Exception as e:
            print(f"[freeze report] write failed: {e!r}", flush=True)
            return ""
        print(f"[engine watchdog] freeze report saved to {path}",
              flush=True)
        return str(path)

    def _handle_trick_complete(self):
        """Handle completed trick - auto-advance when the human side won
        the trick (so the user just plays the next card without an
        intervening click); enable Next-card otherwise.

        Spacebar is wired up at construction time as a shortcut on the
        Next-card button, so even when the button is enabled the user
        doesn't have to reach for the mouse.
        """
        winner = self.controller.get_trick_winner()
        if winner is None:
            return

        self.table_view.show_trick_winner(winner)
        self.table_view.update_tricks(
            self.controller.board.declarer_tricks,
            self.controller.board.defense_tricks
        )

        human_side_won = self._winner_is_local_partnership(winner)

        # Network mode: the host/guest who actually played the LAST
        # card of the trick is responsible for emitting the trick-clear,
        # so anyone else just waits. Independent of who *won*: the
        # winning side may all be remote, in which case we still wait.
        if self.network_controller.is_active:
            last_trick = (self.controller.board.tricks[-1]
                          if self.controller.board.tricks else None)
            if last_trick and len(last_trick.cards) == 4:
                last_player_seat = Seat((last_trick.leader.value + 3) % 4)
                declarer = self.controller.declarer
                dummy = self.controller.dummy
                actual_player = (declarer if last_player_seat == dummy
                                 else last_player_seat)
                remote_played_last = self.network_controller.is_remote_seat(
                    actual_player
                )
                if remote_played_last:
                    self.next_card_btn.setEnabled(False)
                    who = ("partner"
                           if self.network_controller.client_role == "partner"
                           else "opponent")
                    self.status_label.setText(
                        f"Trick won by {winner.to_char()}. Waiting for {who}..."
                    )
                    return

                # I (or an AI seat I drive) played last. If our side won,
                # auto-advance so the next lead drops straight in. If
                # the opponents won, leave the button on so we can press
                # 'next card' / spacebar deliberately.
                if human_side_won or self.autoplay_btn.isChecked():
                    self.next_card_btn.setEnabled(False)
                    self.status_label.setText(f"Trick won by {winner.to_char()}.")
                    QTimer.singleShot(400, self._on_next_card)
                else:
                    self.next_card_btn.setEnabled(True)
                    self.status_label.setText(
                        f"Trick won by {winner.to_char()}. "
                        "Press Next card (or Space)."
                    )
                return

        # Auto-claim detection (wbridge5-style "make the rest").
        # If DDS proves the side just won can take every remaining
        # trick, ask the user to confirm and skip the rest of play.
        # Only fires when the toggle is on (default off — see
        # View → Auto-claim).
        if (hasattr(self, "auto_claim_action")
                and self.auto_claim_action.isChecked()
                and self._maybe_offer_claim(winner)):
            return  # claim path handled inline

        # Single-player.
        if human_side_won or self.autoplay_btn.isChecked():
            self.next_card_btn.setEnabled(False)
            self.status_label.setText(f"Trick won by {winner.to_char()}.")
            QTimer.singleShot(400, self._on_next_card)
        else:
            self.next_card_btn.setEnabled(True)
            self.status_label.setText(
                f"Trick won by {winner.to_char()}. Press Next card (or Space)."
            )

    def _maybe_offer_claim(self, winner: Seat) -> bool:
        """Ask DDS whether the side that just won the trick can take
        every remaining trick; if so, pop a confirmation dialog and
        (on accept) fast-forward the rest of the deal.

        Returns True iff a claim was offered AND accepted (caller
        should skip its own next-card scheduling)."""
        try:
            claim = self.engine.can_claim_remaining(
                self.controller.board)
        except Exception:
            return False
        if claim is None:
            return False
        # The leader of the next trick is `winner`. claim.claimer_side
        # is whoever's on lead, so it always matches winner's side.
        ns = "North-South" if claim.claimer_side == "NS" else "East-West"
        reply = QMessageBox.question(
            self, "Claim",
            f"{ns} make the rest ({claim.tricks_to_claim} tricks).\n\n"
            "Skip the remaining cardplay?",
            QMessageBox.StandardButton.Ok
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok)
        if reply != QMessageBox.StandardButton.Ok:
            return False
        # Apply the claim: bump trick counts and end the hand.
        board = self.controller.board
        board.declarer_tricks = claim.declarer_side_tricks
        board.defense_tricks = claim.defense_side_tricks
        # Mark the hand finished — the controller may want extra
        # bookkeeping; use the same path the user takes for the
        # final trick.
        self.controller.current_phase = 'finished'
        self.table_view.update_tricks(
            board.declarer_tricks, board.defense_tricks)
        self.status_label.setText(
            f"Claim accepted — {ns} took the rest.")
        # Show the result dialog after a moment.
        QTimer.singleShot(400, self._show_result)
        return True

    def _winner_is_local_partnership(self, winner: Seat) -> bool:
        """Return True if the just-won trick was taken by a seat the
        local human controls (themself, their partner if AI, or the
        dummy when the human is declarer)."""
        if self.network_controller.is_active:
            # Network: the local player owns one or more seats. A win on
            # any of those — or on a seat their *partner* (also a remote
            # human) owns — counts as "our side". For simplicity we
            # treat both partnership members as local if either is
            # human-controlled by anybody at this client.
            try:
                if self.network_controller.is_my_seat(winner):
                    return True
                # If my seat's partner won, treat as my side too.
                my_seats = list(self.network_controller.my_seats or [])
                if my_seats and any(s.partner() == winner for s in my_seats):
                    return True
            except Exception:
                pass
            return False
        # Single-player.
        return bool(self.controller._human_controls_seat(winner))

    def _wire_end_of_hand_dialog(self, dialog):
        """Connect the Q-Plus button signals on an EndOfHandDialog to
        the matching existing MainWindow handler. Centralised here so
        the teams-match path and the single-player path share the
        same wiring.

        Single-player buttons:
          • Generate closed room → spawn harness + Q-Plus
          • Claude analysis      → run Claude (open-room, or both if
                                   a closed-room run has been
                                   ingested before clicking)
          • Next deal            → advance to next deal
          • Review / Score / Repeat / Help — secondary row
        """
        try:
            dialog.review_requested.connect(self._on_review)
        except Exception:
            pass
        try:
            dialog.score_requested.connect(self._on_show_scores)
        except Exception:
            pass
        try:
            dialog.repeat_requested.connect(self._on_repeat_deal)
        except Exception:
            pass

        # Help: prefer a dedicated handler if one exists; otherwise
        # fall through to a do-nothing pass so the click is silently
        # absorbed.
        help_handler = getattr(self, '_on_show_help', None)
        if callable(help_handler):
            try:
                dialog.help_requested.connect(help_handler)
            except Exception:
                pass

        # Generate closed room (single-player only — the teams-match
        # path uses View other table instead, which is already wired
        # by the caller). Hands off to _launch_qplus_harness so the
        # user can play the same deal in Q-Plus; the existing
        # Extras → Ingest Q-Plus closed room flow picks up the BDL
        # afterwards.
        try:
            dialog.generate_closed_room_requested.connect(
                self._on_dialog_generate_closed_room)
        except Exception:
            pass
        # Generate-closed-room needs Q-Plus; hide it when none is available.
        if not self._qplus_available():
            for attr in ("gen_closed_btn",):
                b = getattr(dialog, attr, None)
                if b is not None:
                    b.hide()

        # Claude analysis — runs the pending hand critique. If the
        # closed room has been ingested between hand end and click,
        # _maybe_run_pending_claude switches to the comparison
        # prompt automatically.
        try:
            dialog.claude_analysis_requested.connect(
                self._on_dialog_claude_analysis)
        except Exception:
            pass
        # Hide the Claude button unless Claude Code is enabled (default off).
        if not self._claude_enabled():
            b = getattr(dialog, "claude_btn", None)
            if b is not None:
                b.hide()

        # Next deal — use the DEDICATED next_deal_requested signal,
        # not the broad accepted signal. Review / Score / Repeat all
        # call self.accept() to close the dialog, so listening on
        # accepted accidentally triggered Next Deal whenever the user
        # clicked any of those buttons — auto-dealing a fresh hand
        # in the background.
        try:
            dialog.next_deal_requested.connect(self._on_next_deal)
        except Exception:
            pass

    def _dismiss_end_of_hand_dialogs(self):
        """Close any embedded end-of-hand summary overlay so it doesn't sit on
        top of the table while the next deal / repeat plays out."""
        for d in list(getattr(self, '_end_of_hand_dialogs', []) or []):
            try:
                d.close()
            except Exception:
                pass
        self._end_of_hand_dialogs = []

    def _show_end_of_hand_dialog(self, dialog):
        """Show the end-of-hand summary as a MOVABLE top-level window — a real
        title bar the user can grab to drag it aside and see the cards behind
        it. Non-modal and owned by the main window (so it stays above the
        table and is raised with it); the main window keeps a reference.
        """
        from PyQt6.QtCore import Qt as _Qt
        try:
            # A proper window (title bar + system menu + close), not an
            # embedded child overlay — so it can be moved off the cards.
            dialog.setParent(self)
            dialog.setWindowFlags(
                _Qt.WindowType.Window
                | _Qt.WindowType.WindowTitleHint
                | _Qt.WindowType.WindowSystemMenuHint
                | _Qt.WindowType.WindowCloseButtonHint)
            dialog.setModal(False)
        except Exception:
            pass
        dialog.adjustSize()
        # Initial position: centred over the main window (global coords),
        # but the user can drag it anywhere from here.
        try:
            ds = dialog.sizeHint()
            g = self.frameGeometry()
            x = g.x() + max(0, (g.width() - ds.width()) // 2)
            y = g.y() + max(0, (g.height() - ds.height()) // 3)
            dialog.move(x, y)
        except Exception:
            pass
        dialog.show()
        dialog.raise_()
        try:
            dialog.activateWindow()
        except Exception:
            pass
        if not hasattr(self, '_end_of_hand_dialogs'):
            self._end_of_hand_dialogs = []
        survivors = []
        for d in self._end_of_hand_dialogs:
            try:
                if d is not dialog and d.isVisible():
                    # only one summary at a time — close older embedded panels
                    d.close()
            except RuntimeError:
                pass
        survivors.append(dialog)
        self._end_of_hand_dialogs = survivors

    def _on_dialog_generate_closed_room(self):
        """End-of-hand dialog button → spawn the harness on its Closed
        Room wizard for a real Q-Plus closed room.

        The open room was just played by the human + biq as N/S against
        biq E/W. The closed room replays the SAME deal with Q-Plus N/S
        and biq networked in as E/W (live over Q-NET), so the comparison
        isolates the human/biq N/S pair against Q-Plus. After the closed
        room is played, Extras → Ingest Q-Plus closed room brings the
        result back for the IMP swing.

        The previous behaviour fired this automatically from
        _show_result, which surprised users who hadn't asked for a
        closed-room comparison. Routing it through the dialog button
        makes it explicit.
        """
        if self.controller.board is None:
            return
        self._launch_qplus_harness(self.controller.board, closed_room=True)
        self.status_label.setText(
            "Launched closed-room harness — Q-Plus N/S vs biq E/W. Follow "
            "the steps, then Extras → Ingest Q-Plus closed room for the swing."
        )

    def _on_send_current_to_harness(self):
        """Extras menu handler — write the current deal to Q-Plus's
        OWN-DEALS folder as a .BDE file, then spawn the harness.

        The .BDE file is Q-Plus's native Own-Deals format. Writing
        it directly is fast and reliable — no per-card pyautogui
        click chain. After this returns, the user opens Q-Plus's
        `Own Deals → Open` menu, picks the file, and Q-Plus
        auto-plays the deal. The GUI harness is still spawned so
        the user has its UI for follow-up actions, but the heavy
        lifting (transferring the deal to Q-Plus) is done via the
        file system.
        """
        if self.controller.board is None:
            QMessageBox.information(
                self, "No deal",
                "Start a deal first — there's nothing to send.")
            return
        bde_path = self._write_current_deal_as_bde(self.controller.board)
        self._launch_qplus_harness(self.controller.board)
        if bde_path is not None:
            self.status_label.setText(
                f"Wrote {os.path.basename(bde_path)} to OWN-DEALS/. "
                f"In Q-Plus: Own Deals → Open → pick that file."
            )
        else:
            self.status_label.setText(
                "Launched GUI harness with the current deal."
            )

    def _write_current_deal_as_bde(self, board) -> Optional[str]:
        """Write the current deal as a Q-Plus .BDE file.

        Returns the file path on success, None on failure (no Q-Plus
        install, hands not yet dealt, etc.). The format is the same
        text-table layout Q-Plus's shipped EXAMPLE.BDE uses, with
        WEST in the left column / EAST in the right column and `T`
        for the ten — round-tripped through the BDL reader to
        confirm seat assignment.
        """
        import os
        # Locate Q-Plus's DATA/OWN-DEALS folder under FRI/WP/.
        # The harness's _qplus_own_deals_dir does the same lookup;
        # we mirror it here so bridgeIQ can write directly.
        here = os.path.dirname(os.path.abspath(__file__))
        fri_root = os.path.normpath(os.path.join(here, "..", "..", ".."))
        candidates = [
            os.path.join(fri_root, "WP", "drive_c", "games",
                         "qbridge17", "DATA", "OWN-DEALS"),
            os.path.join(fri_root, "WP", "drive_c", "games",
                         "qbridge15", "DATA", "OWN-DEALS"),
        ]
        own_dir = next((d for d in candidates if os.path.isdir(d)), None)
        if own_dir is None:
            return None
        hands_src = self.original_hands or board.hands
        if not all(s in hands_src and len(hands_src[s].cards) == 13 for s in Seat):
            return None

        # Render each seat's cards into the [spades, hearts, diamonds, clubs]
        # rank-token shape the BDE writer wants.
        rank_chars = {0: 'A', 1: 'K', 2: 'Q', 3: 'J', 4: 'T',
                      5: '9', 6: '8', 7: '7', 8: '6',
                      9: '5', 10: '4', 11: '3', 12: '2'}
        suit_chars = {Suit.SPADES: 'S', Suit.HEARTS: 'H',
                      Suit.DIAMONDS: 'D', Suit.CLUBS: 'C'}
        VULN_TO_BDE = {'None': '---', 'N-S': 'N/S',
                       'E-W': 'E/W', 'Both': 'All'}
        DEALER_TO_BDE = {'N': 'North', 'E': 'East',
                         'S': 'South', 'W': 'West'}

        def _suit_line(cards_for_suit):
            ranks = sorted((c.rank.value for c in cards_for_suit))
            return ' '.join(rank_chars[r] for r in ranks) or '-'

        def _seat_suits(seat):
            cards = hands_src[seat].cards
            return {
                Suit.SPADES:   [c for c in cards if c.suit == Suit.SPADES],
                Suit.HEARTS:   [c for c in cards if c.suit == Suit.HEARTS],
                Suit.DIAMONDS: [c for c in cards if c.suit == Suit.DIAMONDS],
                Suit.CLUBS:    [c for c in cards if c.suit == Suit.CLUBS],
            }

        n = _seat_suits(Seat.NORTH)
        e = _seat_suits(Seat.EAST)
        s = _seat_suits(Seat.SOUTH)
        w = _seat_suits(Seat.WEST)
        suit_order = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]

        label = f"BB-board-{board.board_number}"
        dealer = DEALER_TO_BDE.get(board.dealer.to_char(), 'North')
        vuln = VULN_TO_BDE.get(board.vulnerability.value, '---')

        out = []
        out.append("DOCTYPE: BDL 7.1")
        out.append('.description.eng = "Relayed from bridgeIQ"')
        out.append("")
        out.append(f"Deal         :   {label}")
        out.append(f"Dealer       :   {dealer}")
        out.append(f"Vuln         :   {vuln}")
        out.append(f"Cards        :                  {_suit_line(n[suit_order[0]])}")
        for su in suit_order[1:]:
            out.append(f"             :                  {_suit_line(n[su])}")
        for su in suit_order:
            wcol = _suit_line(w[su])
            ecol = _suit_line(e[su])
            out.append(f"             :   {wcol:<30}{ecol}")
        for su in suit_order:
            out.append(f"             :                  {_suit_line(s[su])}")
        out.append("")
        out.append("*********************************************************")

        path = os.path.join(own_dir, "BRIDGEIQ_RELAY.BDE")
        try:
            with open(path, "w", encoding="latin-1") as f:
                f.write("\n".join(out) + "\n")
        except Exception as ex:
            print(f"[bde write] failed: {ex!r}", flush=True)
            return None
        return path

    def _on_dialog_claude_analysis(self):
        """End-of-hand dialog button → run Claude on the last hand.

        Reuses the existing pending-claude pipeline. If the closed
        room has been ingested before this click, the prompt builder
        automatically switches to the comparison flavour.
        """
        self._maybe_run_pending_claude()

    def _show_result(self):
        """Show deal result"""
        board = self.controller.board

        self.next_card_btn.setEnabled(False)
        self.next_deal_btn.setVisible(True)  # Show next deal button when finished
        self.right_panel.setVisible(True)  # Show right panel again

        # Refresh tricks display so it reflects all 13 tricks
        self.table_view.update_tricks(
            board.declarer_tricks,
            board.defense_tricks
        )

        # Q-Plus-style post-play table: re-spread every original
        # 13-card hand face-up and outline the cards that won a
        # trick. The modal EndOfHandDialog still pops on top, but it
        # sits over a table that looks like the Q-Plus reference
        # screen rather than a play-area with empty hand widgets.
        #
        # Unconditional clear first: if `original_hands` is missing
        # (e.g. loaded-deal paths that never populated it) we still
        # need to wipe the last trick from the centre, otherwise the
        # green felt keeps showing the final 4 cards beneath the
        # end-of-hand dialog.
        try:
            self.table_view.clear_trick()
        except Exception:
            pass
        try:
            if self.original_hands:
                # Populate every hand with its original 13 cards (and paint
                # the trick-winner outlines) so View ▸ Open All Hands / F2
                # and the Review dialog can reveal them on demand.
                self.table_view.show_end_of_hand_view(
                    self.original_hands,
                    list(board.tricks) if board.tricks else [],
                )
        except Exception as ex:
            print(f"end-of-hand view failed: {ex!r}", flush=True)
        # Play is over — whether by the 13th trick or a claim. Four full
        # 13-card hands overflow the table and get cut off, and a claim's
        # unplayed cards were never actually played, so clear ALL cards from
        # the screen once the deal completes. The hands stay populated above;
        # F2 / Open All Hands reveals them on demand. Blank UNCONDITIONALLY —
        # in its own try — so a failure while populating the post-mortem
        # snapshot above can never strand the last cards on the felt.
        try:
            self.table_view.hide_all_hands()
        except Exception as ex:
            print(f"hide_all_hands failed: {ex!r}", flush=True)

        if board.is_passed_out():
            self.status_label.setText("Passed out")
            self.analysis_label.setText("All four players passed.\nNo score.")

            # Show passed out dialog for teams match
            if self.teams_match is not None:
                dialog = PassedOutDialog(self)
                dialog.exec()
            return

        # Calculate score
        contract = board.contract
        tricks = board.declarer_tricks
        vulnerable = board.vulnerability.is_vulnerable(contract.declarer)

        score = self.engine.calculate_score(contract, tricks, vulnerable)

        result_str = ""
        target = contract.target_tricks()
        if tricks >= target:
            overtricks = tricks - target
            if overtricks == 0:
                result_str = "Made"
            else:
                result_str = f"+{overtricks}"
        else:
            undertricks = target - tricks
            result_str = f"-{undertricks}"

        text = f"Contract: {contract.to_str()} by {contract.declarer.to_char()}\n"
        text += f"Result: {tricks} tricks ({result_str})\n"
        text += f"Score: {score:+d}\n"

        self.analysis_label.setText(text)
        self.status_label.setText(f"Deal complete: {contract.to_str()} {result_str}")

        # Rubber scoring auto-feed: when a rubber is active, push this
        # hand's result onto the scorecard so the Rubber Scorecard
        # dialog reflects it. Q-Plus .score-rubber routes trick score
        # below the line and bonuses above; RubberScore.add_hand_result
        # encapsulates the split.
        try:
            rubber = getattr(self, 'rubber_score', None)
            if rubber is not None and not rubber.rubber_complete:
                rubber.add_hand_result(
                    contract=contract,
                    tricks_made=tricks,
                    declarer_is_ns=contract.declarer.is_ns(),
                )
                # Refresh the dialog if it's currently open.
                rd = getattr(self, '_rubber_dialog', None)
                if rd is not None:
                    try:
                        if rd.isVisible():
                            rd.refresh()
                    except RuntimeError:
                        self._rubber_dialog = None
        except Exception as ex:
            print(f"[rubber] auto-add failed: {ex!r}", flush=True)

        # If this is a teams match, show the end-of-hand dialog
        if self.teams_match is not None and self.match_controller is not None:
            # Complete the open room result
            self.match_controller.complete_open_room(board.board_number, board)

            # The closed room is normally already running in parallel (started
            # at deal time — Q-Plus semantics). Only start it here if it wasn't
            # pre-started for this board; otherwise just join the worker.
            self._closed_room_result = None
            if self._closed_room_async_started != board.board_number:
                self.status_label.setText("Running closed room...")
                QApplication.processEvents()
                self.match_controller.start_closed_room_async(
                    board.board_number,
                    callback=self._on_closed_room_complete
                )
            else:
                self.status_label.setText("Joining closed room (4 biq bots)...")
                QApplication.processEvents()
            self._closed_room_async_started = None

            # Wait for closed room to complete via a proper Qt event
            # loop. The previous version polled QApplication.process
            # Events + time.sleep(0.1) which raced the worker's
            # finished signal: if the signal landed BETWEEN an
            # is_board_complete() check and the next processEvents
            # tick, the loop stayed stuck until the 100 ms sleep
            # expired (visible as a stutter). The new path blocks on
            # the worker's finished signal directly so completion
            # unblocks instantly and the UI keeps pumping events.
            if not self.match_controller.wait_for_closed_room(
                    board.board_number, timeout_ms=60000):
                print("Closed room timeout", flush=True)

            # Calculate score for the dialog
            ns_score = score if contract.declarer.is_ns() else -score

            # Get IMP swing if closed room is complete
            imp_swing = None
            if self.match_controller.is_board_complete(board.board_number):
                imp_swing = self.teams_match.get_imp_swing(board.board_number)

            # Show end-of-hand dialog (teams). Wire every Q-Plus
            # button to its existing handler.
            dialog = EndOfHandDialog(
                contract_str=contract.to_str(),
                declarer=contract.declarer.to_char(),
                result_str=result_str,
                score=ns_score,
                imp_swing=imp_swing,
                is_teams_match=True,
                parent=self
            )
            dialog.view_other_table.connect(self._on_view_teams_score)
            self._wire_end_of_hand_dialog(dialog)
            self._show_end_of_hand_dialog(dialog)
        else:
            # Single-player end-of-hand dialog — same Q-Plus banner +
            # the three-way fork (Generate closed room / Claude
            # analysis / Next deal) plus the legacy Review / Score /
            # Repeat / Help row.
            ns_score_sp = score if contract.declarer.is_ns() else -score
            sp_dialog = EndOfHandDialog(
                contract_str=contract.to_str(),
                declarer=contract.declarer.to_char(),
                result_str=result_str,
                score=ns_score_sp,
                imp_swing=None,
                is_teams_match=False,
                parent=self
            )
            self._wire_end_of_hand_dialog(sp_dialog)
            self._show_end_of_hand_dialog(sp_dialog)

        # Log the completed hand. The logger needs all four original hands
        # to compute the Pavlicek deal id; a guest only ever has its own
        # seat, so the call would KeyError. Skip on guests.
        if not (self.network_controller.is_active
                and not self.network_controller.is_server):
            try:
                self.game_logger.log_hand(board, self.original_hands)
            except Exception as e:
                print(f"Error logging hand: {e}", flush=True)

        # Add result to scoring table
        try:
            ns_score = score if contract.declarer.is_ns() else -score
            ew_score = -ns_score

            # pavlicek_id needs all four hands. A guest only has its own seat
            # (and dummy after the reveal), so the encoding will KeyError —
            # don't let that drop the whole result from the score table.
            hands_for_pavlicek = self.original_hands or board.hands
            try:
                pavlicek_id = format_deal_base72(deal_to_number(hands_for_pavlicek))
            except Exception:
                pavlicek_id = ""

            # Create a BenBoardRun for replay from the score table.
            # Stamp it with the system names that were actually in
            # use at the time — pulled from preferences (per-pair
            # override, or native_bidding_system, or last-resort
            # SAYC). Without this the run defaulted to "BEN-NN",
            # mislabelling every deal as having been played by the
            # legacy neural net even when the native rule-based
            # bidder produced the auction.
            ns_sys_name, ew_sys_name = self._current_pair_systems()
            from backend.models import BenBoardRun, BenTable
            board_run = BenBoardRun(
                table=BenTable.OPEN,
                board_number=board.board_number,
                pavlicek_id=pavlicek_id,
                original_hands=self.original_hands or board.hands,
                auction=list(board.auction) if board.auction else [],
                tricks=list(board.tricks) if board.tricks else [],
                contract=contract,
                declarer_tricks=tricks,
                ns_score=ns_score,
                ew_score=ew_score,
                played=True,
                ns_bidding_system=ns_sys_name,
                ew_bidding_system=ew_sys_name,
            )

            result = BoardResult(
                board_number=board.board_number,
                pavlicek_id=pavlicek_id,
                dealer=board.dealer,
                vulnerability=board.vulnerability,
                contract=contract,
                declarer=contract.declarer,
                tricks_made=tricks,
                ns_score=ns_score,
                ew_score=ew_score,
                board_run=board_run,
            )
            self.scoring_table.add_result(result)
            self._apply_field_comparison(result)
        except Exception as e:
            print(f"Error adding to scoring table: {e}", flush=True)

        # Hand visibility at end of play is decided earlier:
        # show_end_of_hand_view() reveals the declaring side (declarer +
        # dummy) and a claim hides everything. Don't blanket-reveal all four
        # here — the defenders' hands run off-screen and the user can use
        # View ▸ Open All Hands (F2) to see them.

        # Post-hand sequence (normal play only, not teams match):
        # The end-of-hand dialog now drives both the harness launch
        # (Generate closed room button) and the Claude critique
        # (Claude analysis button), so we no longer fire either
        # automatically. We only snapshot the deal so the dialog's
        # Claude button has the context it needs, and pop the score
        # sheet for the running total.
        is_guest = (self.network_controller.is_active
                    and not self.network_controller.is_server)
        if self.teams_match is None and self.match_controller is None:
            if not is_guest:
                self._claude_pending = {
                    'board': board,
                    'contract': contract,
                    'tricks': tricks,
                    'result_str': result_str,
                    'score': score,
                    'analyzed': False,
                }
            # Don't pop the score sheet over the end-of-hand summary (its
            # Score button opens it); just refresh a sheet that's already open.
            sd = getattr(self, '_scores_dialog', None)
            try:
                if sd is not None and sd.isVisible() and hasattr(sd, '_populate_table'):
                    sd._populate_table()
            except RuntimeError:
                self._scores_dialog = None

    def _launch_qplus_harness(self, board, closed_room: bool = False):
        """Spawn the guiHarness (bridgeHarness.sh) preloaded with this deal's
        base-72 code, dealer, and vulnerability, so the host can manually
        play the closed room in Q-Plus without leaving bridgeIQ. No-op
        if the harness script can't be located or if the deal has no full
        hand data.

        When ``closed_room`` is set the harness is opened on its Closed
        Room tab (biq E/W vs Q-Plus N/S over Q-NET) via --closed-room.

        Dealer and vulnerability are passed via `--dealer` / `--vuln` so
        the harness can show them on the entry tab — and, once Q-Plus's
        dealer / vulnerability radio buttons are calibrated, click them
        for the user as part of the entry sequence. Matching the BDL's
        dealer/vuln in Q-Plus is required for the closed-room contract
        to be scored against the same conditions as the open room.
        """
        import os
        import subprocess
        try:
            hands = self.original_hands or board.hands
            if not all(s in hands and len(hands[s].cards) == 13 for s in Seat):
                # Hands have already been played — original_hands is the
                # canonical pre-play snapshot. If it's missing seats, skip.
                return
            code = int_to_base72(deal_to_number(hands))
            # bridgeHarness.sh sits at FRI/bridgeHarness.sh — three levels
            # above this file (ui/main_window.py → bridgeIQ → bridgeIQ → FRI).
            script = os.path.normpath(os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "bridgeHarness.sh"
            ))
            if not os.path.isfile(script):
                return
            # Map bridgeIQ enums to the harness's expected CLI tokens.
            dealer_char = board.dealer.to_char()  # 'N' / 'E' / 'S' / 'W'
            vuln_map = {
                'None': 'None',
                'N-S':  'NS',
                'E-W':  'EW',
                'Both': 'Both',
            }
            vuln_token = vuln_map.get(board.vulnerability.value, 'None')
            cmd = [script, "--base72", code,
                   "--dealer", dealer_char,
                   "--vuln", vuln_token]
            if closed_room:
                cmd.append("--closed-room")
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            print(f"[qplus harness] launch failed: {e}", flush=True)

    def _show_claude_hand_analysis(self, board, contract, tricks, result_str, score):
        """Show a progress bar while Claude analyzes, then display results in a dialog.

        If we've already analysed this exact board+deal in this session,
        the result is replayed from `_claude_hand_logs` without spending
        another Claude call. The persisted version on disk lives in the
        BDL log (Commentary block, written by _append_commentary_to_bdl),
        so even across restarts the user sees the same wording when
        opening the BDL log file.
        """
        if not self._claude_enabled():
            self._claude_disabled_notice()
            return
        import shutil
        import subprocess
        import threading
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                                      QScrollArea, QProgressBar)
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import QTimer

        # Cache key: (board_number, pavlicek_id). Pavlicek differentiates
        # two hands that happen to share a board number (replays etc.);
        # if we couldn't compute it, the board number alone keys the
        # cache for this session.
        try:
            hands_for_pavlicek = self.original_hands or board.hands
            if all(s in hands_for_pavlicek for s in Seat):
                cache_pavlicek = format_deal_base72(deal_to_number(hands_for_pavlicek))
            else:
                cache_pavlicek = ""
        except Exception:
            cache_pavlicek = ""
        cache_key = (board.board_number, cache_pavlicek)
        cached = self._claude_hand_logs.get(cache_key)
        if cached is not None:
            # Re-display the saved annotated BDL — no Claude call.
            self._show_annotated_bdl_dialog(
                title="Claude Analysis (cached)",
                bdl_text=cached.get('bdl', ''),
                claude_text=cached.get('claude', ''),
                error=False,
            )
            return

        if not shutil.which('claude'):
            self._show_plain_dialog("Claude Analysis",
                                    "Claude CLI is not installed on this machine.\n"
                                    "Install it to enable this feature.",
                                    error=True)
            return

        # Build hand summary for Claude as a full BDL snapshot. Claude
        # reads seat letters off the BDL N/E/S/W column headers and the
        # leader/winner markers in the Tricks block instead of trying to
        # walk the auction order to attribute bids — that mis-attribution
        # is what caused earlier annotations to confuse who did what.
        seat_names = {Seat.NORTH: 'N', Seat.EAST: 'E', Seat.SOUTH: 'S', Seat.WEST: 'W'}
        suit_symbols = {Suit.SPADES: 'S', Suit.HEARTS: 'H', Suit.DIAMONDS: 'D',
                        Suit.CLUBS: 'C', Suit.NOTRUMP: 'NT'}

        from .game_logger import build_bdl_snapshot
        deal_id = ""
        try:
            hands_for_pavlicek = self.original_hands or board.hands
            if all(s in hands_for_pavlicek for s in Seat):
                deal_id = format_deal_base72(deal_to_number(hands_for_pavlicek))
        except Exception:
            deal_id = ""

        result_summary = (
            f"Result: {tricks} tricks ({result_str}), declarer-side score "
            f"{score:+d}."
        )
        hand_text = build_bdl_snapshot(
            board,
            original_hands=self.original_hands,
            viewer_seat=None,           # end-of-hand: every hand is on display
            phase='finished',
            dummy_visible_to_viewer=True,
            contract=contract,
            result_summary=result_summary,
            deal_id=deal_id,
        ) + "\n"

        # Pull the closed-room result (populated by Q-Plus ingest) so Claude
        # can compare the human's table against the manual closed room.
        # Look for a BoardResult tagged "Closed room" for this board and
        # carrying a played BenBoardRun.
        closed_run = None
        imp_swing = None
        for r in self.scoring_table.results:
            if r.board_number != board.board_number:
                continue
            if (r.notes and "Closed room" in r.notes
                    and r.board_run is not None
                    and getattr(r.board_run, 'played', False)):
                closed_run = r.board_run
                break

        closed_summary = ""
        if closed_run and closed_run.contract:
            c_target = closed_run.contract.target_tricks()
            c_diff = closed_run.declarer_tricks - c_target
            c_result = f"+{c_diff}" if c_diff > 0 else ("=" if c_diff == 0 else str(c_diff))
            ns_open = score if contract.declarer.is_ns() else -score
            ns_diff = ns_open - closed_run.ns_score
            from backend.scoring import diff_to_imps
            try:
                imp_swing = diff_to_imps(ns_diff)
            except Exception:
                imp_swing = None
            closed_summary = (
                f"\nClosed room (Q-Plus, manual replay by host): "
                f"{closed_run.contract.to_str()} by {closed_run.contract.declarer.to_char()}, "
                f"{closed_run.declarer_tricks} tricks ({c_result}), "
                f"NS {closed_run.ns_score:+d}"
            )
            if imp_swing is not None:
                closed_summary += (
                    f"\nIMP swing of open vs closed (NS perspective): "
                    f"{imp_swing:+d}"
                )
            closed_summary += "\n"
        hand_text += closed_summary

        # Instrumentation rundown — run EVERY instrumented-view lens over the
        # completed deal so Claude can critique it through the same tools the
        # teaching view shows the user (counting, losers/LTC, draw-trumps,
        # trump split, entries, hold-up, danger hand, finesses, squeeze, and the
        # defensive-signal log with honesty). Built for BOTH rooms when a closed
        # room is present, so the comparison is grounded the same way each side.
        from .teaching_view import instrumentation_summary
        instr_summary = ""
        closed_instr = ""
        try:
            decl = contract.declarer if contract is not None else None
            dmy = decl.partner() if decl is not None else None
            if self.original_hands and decl is not None:
                instr_summary = instrumentation_summary(
                    self.original_hands, board, contract, decl, dmy)
        except Exception as e:
            print(f"[claude] open-room instrumentation failed: {e}", flush=True)
        if closed_run and closed_run.contract and getattr(closed_run, 'original_hands', None):
            try:
                from backend.models import BoardState as _BS, Hand as _H
                cb = _BS(board_number=closed_run.board_number, dealer=board.dealer,
                         vulnerability=board.vulnerability,
                         hands={s: _H(cards=[]) for s in Seat})
                cb.tricks = list(closed_run.tricks or [])
                cb.contract = closed_run.contract
                cdecl = closed_run.contract.declarer
                cdmy = cdecl.partner() if cdecl is not None else None
                closed_instr = instrumentation_summary(
                    closed_run.original_hands, cb, closed_run.contract, cdecl, cdmy)
            except Exception as e:
                print(f"[claude] closed-room instrumentation failed: {e}", flush=True)

        # Bidding systems each side used, so Claude can critique the AUCTION
        # (not just the cardplay) in each system's terms.
        try:
            _pf = self.config_manager.config.preferences
            open_ns_sys = (_pf.ns_bidding_system or _pf.native_bidding_system or "SAYC")
            open_ew_sys = (_pf.ew_bidding_system or _pf.native_bidding_system or "SAYC")
        except Exception:
            open_ns_sys = open_ew_sys = "SAYC"
        closed_ns_sys = (getattr(closed_run, 'ns_bidding_system', '') or "?") if closed_run else ""
        closed_ew_sys = (getattr(closed_run, 'ew_bidding_system', '') or "?") if closed_run else ""
        systems_text = (f"Open room systems — N/S: {open_ns_sys}, E/W: {open_ew_sys}."
                        + (f" Closed room systems — N/S: {closed_ns_sys}, E/W: {closed_ew_sys}."
                           if closed_run else ""))

        human_seats = [f"{seat_names[s]} ({p.name})"
                       for s, p in self.controller.players.items()
                       if p.player_type == PlayerType.HUMAN]
        human_desc = ", ".join(human_seats) if human_seats else "South"

        # Clear remaining cards from hands and show last trick on table
        try:
            # Remove all cards from hand displays (all 52 cards have been played)
            for seat in Seat:
                hw = self.table_view.hand_widgets.get(seat)
                if hw:
                    hw.clear()
            # Show the last trick on the green table
            last_trick = board.tricks[-1] if board.tricks else None
            if last_trick and len(last_trick.cards) == 4:
                self.table_view.clear_trick()
                for i, card in enumerate(last_trick.cards):
                    seat = Seat((last_trick.leader.value + i) % 4)
                    is_winner = (seat == last_trick.winner)
                    self.table_view.play_card_to_trick(seat, card, is_winner)
            QApplication.processEvents()
        except Exception:
            pass

        # Show progress dialog while Claude thinks
        progress = QDialog(self)
        progress.setWindowTitle("Claude Analysis")
        progress.setFixedSize(460, 130)
        progress.setStyleSheet("background-color: #e8e8f0; color: #000;")
        p_layout = QVBoxLayout(progress)
        p_label = QLabel("Claude is analyzing the hand...")
        p_label.setFont(QFont("Arial", 12))
        p_label.setStyleSheet("color: #000;")
        p_layout.addWidget(p_label)
        p_bar = QProgressBar()
        p_bar.setRange(0, 0)  # Indeterminate
        p_bar.setStyleSheet("QProgressBar { border: 1px solid #aaa; border-radius: 3px; }"
                            "QProgressBar::chunk { background-color: #4a9; }")
        p_layout.addWidget(p_bar)
        cancel_holder = {'cancelled': False}
        done_holder = {'done': False}
        proc_holder = {'proc': None}
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("QPushButton { padding: 6px 18px; }")

        def _cancel_analysis():
            # Closing the dialog after success also emits `rejected`.
            if done_holder['done']:
                return
            cancel_holder['cancelled'] = True
            proc = proc_holder.get('proc')
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        cancel_btn.clicked.connect(_cancel_analysis)
        progress.rejected.connect(_cancel_analysis)
        p_layout.addWidget(cancel_btn)
        progress.show()
        QApplication.processEvents()

        # Write a pidfile so run.sh can wait for us before exiting (and before
        # the launcher moves the BDL out from under _append_commentary_to_bdl).
        pidfile = None
        pid_dir = os.environ.get('CRITIQUE_PID_DIR')
        if pid_dir:
            try:
                os.makedirs(pid_dir, exist_ok=True)
                pidfile = os.path.join(pid_dir, f"{os.getpid()}-{id(board)}.pid")
                with open(pidfile, 'w') as f:
                    f.write(str(os.getpid()))
            except Exception:
                pidfile = None

        # Run Claude in background thread
        result_holder = {'critique': None, 'error': None}

        comparison_note = ""
        if closed_run:
            comparison_note = (
                " Compare the open-room result (the human player's table) "
                "with the closed-room result (Q-Plus, manual replay) "
                "and comment on who did better and why."
            )

        try:
            from .teaching_view import signalling_conventions_text
            signals_text = signalling_conventions_text() + "\n"
            if closed_run:
                signals_text += ("  (These are the open room's agreements; "
                                 "a Q-Plus closed room cards by Q-Plus's own "
                                 "agreements.)\n")
        except Exception:
            signals_text = ""

        def _run_claude():
            try:
                analysis_prompt = (
                    "You are a bridge teacher. Annotate this hand by "
                    "echoing the BDL log VERBATIM and inserting "
                    "annotation lines that BEGIN with '>>' (two right "
                    "angle brackets followed by a space) at logical "
                    "section breaks.\n\n"
                    "MANDATORY OUTPUT FORMAT:\n"
                    "  • Reproduce the BDL exactly, line by line, "
                    "preserving every non-annotation line.\n"
                    "  • After the Bids block, insert one or more '>>' "
                    "lines that critique the auction IN EACH SIDE'S BIDDING "
                    "SYSTEM (see SYSTEMS below) — was each call correct for "
                    "that system, off-shape, a missed game/slam, a better "
                    "convention available?\n"
                    "  • After the Tricks block, insert '>>' lines that "
                    "critique the play and defense, naming specific "
                    "tricks where decisions mattered.\n"
                    "  • At the very end, add a '>> Verdict:' line with "
                    "your overall takeaway in one short sentence.\n"
                    "  • Read seat letters straight from the N/E/S/W "
                    "column headers in Bids and the leader/winner "
                    "markers in Tricks — never infer a seat from order.\n"
                    "  • Keep each annotation under ~25 words; multiple "
                    "short ones beat one long paragraph.\n"
                    "  • Do not output anything outside the BDL frame.\n\n"
                    "Critique the deal THROUGH THE LENS of bridgeIQ's "
                    "instrumented-view features, applied to THIS deal — work "
                    "each one into your '>>' annotations where it's relevant: "
                    "counting (HCP/shape), the declarer plan (top tricks, "
                    "losers / LTC and which suits they're in, the draw-trumps "
                    "question, the trump split, entries / transportation, the "
                    "Rule-of-7 hold-up, the danger hand, finesses), squeeze "
                    "ingredients (threats, type, rectified count), the "
                    "opening-lead Rule-of-11 read, and the defensive-signal "
                    "log (attitude / count / suit-preference / Smith / trump "
                    "echo, and any false-cards). Use the INSTRUMENTATION "
                    "block(s) below as the ground truth for those reads. When a "
                    "closed room is present, critique BOTH rooms through these "
                    "lenses — bidding AND cardplay — and explain which side did "
                    "better and why, in instrumented-view terms.\n\n"
                    f"SYSTEMS: {systems_text}\n"
                    f"{signals_text}"
                    f"The human player(s) at the open room: {human_desc}."
                    f"{comparison_note}\n\n"
                    f"INSTRUMENTATION — OPEN ROOM (biq's reads):\n"
                    f"{instr_summary or '(unavailable)'}\n\n"
                    + (f"INSTRUMENTATION — CLOSED ROOM (biq's reads):\n{closed_instr}\n\n"
                       if closed_instr else "")
                    + f"BDL:\n{hand_text}"
                )
                # Opus 4.7 with extended thinking, longer timeout to
                # accommodate think-then-write for full hand annotations.
                # stdin closed: an inherited open stdin makes `claude -p`
                # wait for piped input forever. Popen so Cancel can kill it.
                proc = subprocess.Popen(
                    ['claude', '-p',
                     '--model', 'claude-opus-4-8',
                     '--thinking', 'enabled',
                     '--max-turns', '1', analysis_prompt],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                proc_holder['proc'] = proc
                try:
                    out, err = proc.communicate(timeout=600)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise
                if cancel_holder['cancelled']:
                    return
                r = subprocess.CompletedProcess(proc.args, proc.returncode,
                                                out, err)
                stdout = (r.stdout or '').strip()
                stderr = (r.stderr or '').strip()
                if r.returncode == 0 and len(stdout) > 10:
                    result_holder['critique'] = stdout
                else:
                    parts = [f"claude exited {r.returncode}"]
                    if stderr:
                        parts.append(f"stderr: {stderr[:500]}")
                    if stdout:
                        parts.append(f"stdout: {stdout[:500]}")
                    result_holder['error'] = "\n".join(parts)
            except subprocess.TimeoutExpired:
                result_holder["error"] = "claude timed out after 600 seconds."
            except Exception as e:
                result_holder['error'] = f"claude call failed: {e!r}"

        thread = threading.Thread(target=_run_claude, daemon=True)
        thread.start()

        # Poll until thread finishes (keeps UI responsive); show the elapsed
        # time so a slow answer doesn't look like a hang.
        import time as _time
        t0 = _time.monotonic()
        while thread.is_alive():
            QApplication.processEvents()
            thread.join(timeout=0.1)
            if cancel_holder['cancelled']:
                break
            el = int(_time.monotonic() - t0)
            p_label.setText(f"Claude is analyzing the hand... {el // 60}:{el % 60:02d}"
                            f" (usually 1-3 minutes)")

        done_holder['done'] = True
        progress.close()
        if cancel_holder['cancelled']:
            proc = proc_holder.get('proc')
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            if pidfile:
                try:
                    os.unlink(pidfile)
                except OSError:
                    pass
            try:
                self.status_label.setText("Claude analysis cancelled.")
            except Exception:
                pass
            return

        critique = result_holder['critique']
        error = result_holder['error']

        # Save critique to BDL file
        if critique:
            try:
                self.game_logger._append_commentary_to_bdl(critique)
            except Exception as e:
                print(f"Failed to append commentary to BDL: {e}", flush=True)

        # Remove pidfile now that critique is written — run.sh can stop waiting.
        if pidfile:
            try:
                os.unlink(pidfile)
            except OSError:
                pass

        # Build a short open-vs-closed summary header so the user sees
        # the objective numbers even if Claude failed; the BDL view then
        # follows with Claude's annotations interspersed.
        summary_header = ""
        if closed_run and closed_run.contract:
            c_target = closed_run.contract.target_tricks()
            c_diff = closed_run.declarer_tricks - c_target
            c_result = (f"+{c_diff}" if c_diff > 0
                        else ("=" if c_diff == 0 else str(c_diff)))
            ns_open = score if contract.declarer.is_ns() else -score
            summary_header = (
                f"Open room:   {contract.to_str()} by {contract.declarer.to_char()}  "
                f"{tricks} tricks ({result_str})  NS {ns_open:+d}\n"
                f"Closed room: {closed_run.contract.to_str()} by "
                f"{closed_run.contract.declarer.to_char()}  "
                f"{closed_run.declarer_tricks} tricks ({c_result})  "
                f"NS {closed_run.ns_score:+d}"
            )
            if imp_swing is not None:
                side = 'N/S' if imp_swing >= 0 else 'E/W'
                summary_header += f"\nIMP: {imp_swing:+d} ({side})"
            summary_header += "\n\n"

        framed_bdl = (summary_header + hand_text) if summary_header else hand_text
        self._show_annotated_bdl_dialog(
            title="Claude Analysis",
            bdl_text=framed_bdl,
            claude_text=critique or "",
            error=not critique,
        )
        # Cache the successful analysis so reopening "Hand Log" later
        # for this same hand redraws the saved annotated BDL instead of
        # firing another Claude call. We deliberately don't cache
        # failures — the user can retry on the next click.
        if critique:
            self._claude_hand_logs[cache_key] = {
                'bdl': framed_bdl,
                'claude': critique,
                'board_number': board.board_number,
                'pavlicek_id': cache_pavlicek,
            }
        if not critique:
            self._show_plain_dialog(
                "Claude Analysis (claude error)",
                "Claude did not return an analysis for this hand.\n\n"
                f"{error or 'No output received.'}",
                error=True,
            )

    def _start_parallel_closed_room(self, board):
        """Kick off the all-AI closed room the moment a new deal is dealt.

        The closed room then runs in parallel with the human's play, and when
        the human finishes _auto_closed_room just joins the pending worker
        instead of starting a fresh (slow) run.
        """
        # Skip when teams/match modes manage their own closed room.
        if self.teams_match is not None or self.match_controller is not None:
            return
        # Cancel any previous pending room (e.g. user hit "Next deal" early).
        self._cancel_pending_closed_room()
        try:
            import uuid
            from backend.models import BenTeamsMatch
            from backend.match_controller import TeamsMatchController

            board_num = board.board_number
            temp_match = BenTeamsMatch(
                match_id=str(uuid.uuid4()),
                num_boards=1,
                current_board=board_num,
            )
            temp_controller = TeamsMatchController(self.engine, temp_match)
            temp_controller.start_board(board_num, board)
            temp_controller.start_closed_room_async(board_num)
            self._pending_closed_room = {
                'controller': temp_controller,
                'match': temp_match,
                'board_num': board_num,
            }
            self.status_label.setText(
                f"Board {board_num}: closed room (AI) started in background"
            )
        except Exception as e:
            print(f"Failed to start parallel closed room: {e}", flush=True)
            self._pending_closed_room = None

    def _cancel_pending_closed_room(self):
        """Stop any running closed-room worker and discard its pending record."""
        pending = getattr(self, '_pending_closed_room', None)
        if not pending:
            return
        try:
            pending['controller'].stop_closed_room()
        except Exception:
            pass
        self._pending_closed_room = None

    def _auto_closed_room(self, board, contract, tricks, result_str, score):
        """Join the parallel closed-room worker and post its result.

        Started by _start_parallel_closed_room at deal time. If the user
        finishes faster than the AI, waits for completion with a progress dialog.
        Falls back to starting the closed room here if none was pre-started.
        """
        import time
        import uuid
        from backend.models import BenTeamsMatch, BenTable
        from backend.match_controller import TeamsMatchController
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar
        from PyQt6.QtGui import QFont

        board_num = board.board_number
        ns_score = score if contract.declarer.is_ns() else -score

        # Reuse the pre-started pending worker if it's for this board.
        pending = getattr(self, '_pending_closed_room', None)
        if pending and pending.get('board_num') == board_num:
            temp_controller = pending['controller']
            temp_match = pending['match']
            # Record the open-room outcome the user just played.
            try:
                temp_controller.complete_open_room(board_num, board)
            except Exception:
                pass
        else:
            # No pre-started worker — fall back to running it now.
            temp_match = BenTeamsMatch(
                match_id=str(uuid.uuid4()),
                num_boards=1,
                current_board=board_num,
            )
            temp_controller = TeamsMatchController(self.engine, temp_match)
            temp_controller.start_board(board_num, board)
            temp_controller.complete_open_room(board_num, board)
            temp_controller.start_closed_room_async(board_num)

        # If the closed room hasn't finished yet, show a progress dialog while we wait.
        waiting_dialog = None
        if not temp_controller.is_board_complete(board_num):
            waiting_dialog = QDialog(self)
            waiting_dialog.setWindowTitle("Closed Room")
            waiting_dialog.setFixedSize(400, 110)
            waiting_dialog.setStyleSheet("background-color: #e8e8f0; color: #000;")
            w_layout = QVBoxLayout(waiting_dialog)
            w_label = QLabel("Finishing closed room (AI vs AI)...")
            w_label.setFont(QFont("Arial", 12))
            w_label.setStyleSheet("color: #000;")
            w_layout.addWidget(w_label)
            w_bar = QProgressBar()
            w_bar.setRange(0, 0)  # Indeterminate
            w_bar.setStyleSheet("QProgressBar { border: 1px solid #aaa; border-radius: 3px; }"
                                "QProgressBar::chunk { background-color: #4a9; }")
            w_layout.addWidget(w_bar)
            waiting_dialog.show()
            QApplication.processEvents()

        self.status_label.setText("Waiting for closed room (all AI)...")
        QApplication.processEvents()

        # Event-loop wait (no more tight processEvents+sleep loop).
        # Same race-free path as _show_result's teams-match wait.
        if not temp_controller.wait_for_closed_room(
                board_num, timeout_ms=120000):
            self.status_label.setText("Closed room timed out")
            if waiting_dialog is not None:
                waiting_dialog.close()
            self._pending_closed_room = None
            return

        if waiting_dialog is not None:
            waiting_dialog.close()

        # Get results
        imp_swing = temp_match.get_imp_swing(board_num)
        closed_run = temp_match.board_runs.get(board_num, {}).get(BenTable.CLOSED)
        self._pending_closed_room = None  # done with this one

        # Update the BoardResult in scoring table with closed room data
        if self.scoring_table.results:
            last_result = self.scoring_table.results[-1]
            if last_result.board_number == board_num:
                last_result.imps = imp_swing
                if closed_run and closed_run.played:
                    if closed_run.contract:
                        closed_summary_text = (
                            f"Closed: {closed_run.contract.to_str()} "
                            f"by {closed_run.contract.declarer.to_char()} "
                            f"({closed_run.declarer_tricks} tricks, "
                            f"NS {closed_run.ns_score:+d}). "
                            f"IMP: {imp_swing:+d}"
                        )
                    else:
                        closed_summary_text = (
                            f"Closed: passed out (NS {closed_run.ns_score:+d}). "
                            f"IMP: {imp_swing:+d}"
                        )
                    last_result.notes = closed_summary_text
                    last_result.closed_room_run = closed_run

        # Log the closed room result
        try:
            if closed_run:
                self.game_logger.log_board_run(closed_run)
        except Exception:
            pass

        # Show result with IMP comparison in the side panel
        if closed_run and closed_run.played:
            if closed_run.contract:
                closed_target = closed_run.contract.target_tricks()
                closed_diff = closed_run.declarer_tricks - closed_target
                if closed_diff > 0:
                    closed_result_diff = f"+{closed_diff}"
                elif closed_diff == 0:
                    closed_result_diff = "="
                else:
                    closed_result_diff = str(closed_diff)
                closed_display = (
                    f"{closed_run.contract.to_str()} by "
                    f"{closed_run.contract.declarer.to_char()} — "
                    f"{closed_run.declarer_tricks} tricks ({closed_result_diff})"
                )
            else:
                closed_display = "Passed out"

            imp_text = f"IMP: {imp_swing:+d} {'(N/S)' if imp_swing >= 0 else '(E/W)'}"

            self.analysis_label.setText(
                f"Contract: {contract.to_str()} by {contract.declarer.to_char()}\n"
                f"Result: {tricks} tricks ({result_str})\n"
                f"Score: {ns_score:+d} (N/S)\n\n"
                f"Closed room: {closed_display}\n"
                f"Score: {closed_run.ns_score:+d} (N/S)\n\n"
                f"{imp_text}"
            )

        # Add closed-room result as companion entry so it shows on the
        # scoring table (Contract 2 / N/S 2). Also added for passed-out
        # closed hands — their "Pass" entry is still informative.
        if closed_run and closed_run.played:
            pav_id = (self.scoring_table.results[-1].pavlicek_id
                      if self.scoring_table.results else "")
            closed_result_obj = BoardResult(
                board_number=board_num,
                pavlicek_id=pav_id,
                dealer=board.dealer,
                vulnerability=board.vulnerability,
                contract=closed_run.contract,  # may be None → shown as "Pass"
                declarer=closed_run.contract.declarer if closed_run.contract else None,
                tricks_made=closed_run.declarer_tricks or 0,
                ns_score=closed_run.ns_score,
                ew_score=closed_run.ew_score,
                imps=-imp_swing if imp_swing is not None else None,
                notes="Closed room",
                board_run=closed_run,
            )
            self.scoring_table.results.append(closed_result_obj)

        self.status_label.setText(
            f"Deal complete: {contract.to_str()} {result_str}"
            + (f" | IMP: {imp_swing:+d}" if imp_swing is not None else "")
        )

    @pyqtSlot(object)
    def _on_engine_bid(self, response):
        """Handle bid from engine"""
        if self.controller.current_phase != 'bidding':
            return

        bid = response.action
        bidder = self.controller.current_seat
        self.controller.make_bid(bid)
        self.bidding_box.add_bid(bid)
        self.table_view.update_auction(self.controller.board.auction, self.controller.board.dealer)

        # Broadcast to network if we're the server
        if self.network_controller.is_active and self.network_controller.is_server:
            self.network_controller.broadcast_bid(bidder, bid)

        # Show analysis
        text = f"biq bid: {bid.symbol()}\n"
        if response.candidates:
            text += "\nCandidates:\n"
            for cand in response.candidates[:5]:
                text += f"  {cand.bid.symbol()}: {cand.score:.3f}\n"
        self.analysis_label.setText(text)

        # Update bid info window - use proper bid interpretation, not engine score
        self._add_bid_to_info(bidder, bid)
        self._update_available_bids()

        # Continue
        QTimer.singleShot(300, self._advance_game)

    @pyqtSlot(object)
    def _update_inferences_panel(self, constraints_dict):
        """Push per-seat constraints into the E/W SeatInferenceBox
        widgets that live in the TableView. `constraints_dict` is
        {Seat: SeatConstraints} from
        backend.auction_inference.infer_constraints, or None to
        clear everything.

        Only E/W get boxes: N is dummy (visible to all once revealed)
        and S is the player's hand (always visible). Both boxes are
        suppressed when their seat is in fact visible (e.g. E is dummy).
        """
        from backend.models import Seat as _Seat
        wants_visible = (hasattr(self, "teaching_panel_action")
                         and self.teaching_panel_action.isChecked())
        is_play = (self.controller is not None
                   and getattr(self.controller, "current_phase", None)
                   == "play")
        if not constraints_dict or not (is_play and wants_visible):
            for box in self.inference_boxes.values():
                box.update_from(None)
                box.setVisible(False)
            return
        board = self.controller.board if self.controller else None
        # Which seats have publicly-known hands? Dummy is known to
        # everybody once revealed.
        known_seats = set()
        if (board is not None
                and getattr(board, "contract", None) is not None
                and getattr(self.table_view, "dummy_revealed", False)):
            declarer = board.contract.declarer
            known_seats.add(declarer.partner())
        # Collect cards played by each seat — used to suppress
        # already-played honors in the per-seat display. List
        # (not set) because Card isn't hashable.
        played_by_seat = {s: [] for s in _Seat}
        if board is not None:
            for trick in (getattr(board, "tricks", None) or []):
                if not trick.cards:
                    continue
                s = trick.leader
                for c in trick.cards:
                    played_by_seat[s].append(c)
                    s = _Seat((s + 1) % 4)
            cur = getattr(board, "current_trick", None)
            if cur is not None and cur.cards:
                s = cur.leader
                for c in cur.cards:
                    played_by_seat[s].append(c)
                    s = _Seat((s + 1) % 4)
        for seat, box in self.inference_boxes.items():
            # If this seat's hand is publicly visible (it's the dummy
            # after reveal), there's nothing to teach — hide the box.
            if seat in known_seats:
                box.setVisible(False)
                continue
            box.update_from(constraints_dict.get(seat), None,
                            played_cards=played_by_seat[seat])
            box.setVisible(True)

    def _on_show_bidding_flowchart(self):
        """View → Show bidding flowchart. Pops a modal with a
        plantuml-rendered decision tree for the current auction
        position, with the path our hand took highlighted."""
        board = self.controller.board if self.controller else None
        if board is None:
            QMessageBox.information(
                self, "No board",
                "No board loaded — deal first.")
            return
        # Use the local seat (the human's seat) for the flowchart.
        from backend.models import Seat as _Seat
        seat = (self.table_view._local_seat
                if hasattr(self.table_view, "_local_seat")
                else _Seat.SOUTH)
        if board.hands.get(seat) is None or not board.hands[seat].cards:
            QMessageBox.information(
                self, "No hand",
                f"Seat {seat.name} doesn't have a hand yet.")
            return
        system = None
        try:
            from backend.bidding_systems import get_system
            system = get_system(self.engine.current_system)
        except Exception:
            pass
        dlg = BiddingFlowchartDialog(self, board, seat, system)
        dlg.exec()

    def _on_show_auction_interpretation(self):
        """View → Interpret current auction. Pops a modal listing
        each bid in the current auction with a natural-language
        explanation of what it shows. Powered by the same
        auction_inference machinery the teaching panel uses."""
        board = self.controller.board if self.controller else None
        if board is None or not getattr(board, "auction", None):
            QMessageBox.information(
                self, "No auction",
                "There's no auction yet on the current board.")
            return
        system = None
        try:
            from backend.bidding_systems import get_system
            system = get_system(self.engine.current_system)
        except Exception:
            pass
        dlg = AuctionInterpretationDialog(self, board, system)
        dlg.exec()

    def _on_toggle_teaching_panel(self, checked: bool):
        """View → Show auction inferences toggle handler.

        The E/W inference boxes live in the TableView columns; we
        drive their visibility through _update_inferences_panel which
        checks the menu action's state and the phase.
        """
        if not checked:
            for box in self.inference_boxes.values():
                box.setVisible(False)
            return
        if (self.controller is not None
                and getattr(self.controller, "current_phase", None)
                == "play"):
            self._refresh_inferences_from_board()
        else:
            for box in self.inference_boxes.values():
                box.setVisible(False)

    def _refresh_inferences_from_board(self):
        """Compute auction inferences from the current board, then
        tighten them with any play info, and push them to the 4
        per-seat boxes. Cheap (no DDS) so we can call this on
        every card played to keep the boxes sharpening live."""
        try:
            from backend import auction_inference
            board = self.controller.board if self.controller else None
            if board is None or not getattr(board, "auction", None):
                self._update_inferences_panel(None)
                return
            system = None
            try:
                from backend.bidding_systems import get_system
                system = get_system(self.engine.current_system)
            except Exception:
                pass
            constraints = auction_inference.infer_constraints(
                board, system)
            # Tighten with played-cards info (show-outs, played-HCP
            # floor, played-length floor) so the boxes sharpen
            # during cardplay just like wbridge5's do.
            constraints = auction_inference.tighten_from_play(
                board, constraints)
            self._update_inferences_panel(constraints)
        except Exception:
            self._update_inferences_panel(None)

    def _on_engine_card(self, response):
        """Handle card from engine"""
        # Get the seat that was requested (stored in engine_worker)
        requested_seat = self.engine_worker.seat if self.engine_worker else None

        if self.controller.current_phase != 'play':
            return

        # Keep the per-seat teaching boxes in sync. Cheap to
        # recompute from board — no DDS calls — and handles edge
        # cases like replay / new-deal mid-trick.
        if (hasattr(self, "teaching_panel_action")
                and self.teaching_panel_action.isChecked()):
            self._refresh_inferences_from_board()

        # Verify the response is for the current seat. Seat is an IntEnum
        # (NORTH==0); use an explicit None check so a NORTH-requested card
        # doesn't fall through the truthiness gate.
        if (requested_seat is not None
                and requested_seat != self.controller.current_seat):
            # Don't play the card if it's for the wrong seat
            QTimer.singleShot(100, self._advance_game)
            return

        if not response.action:
            # Engine returned no card - try to play any legal card as fallback.
            # IMPORTANT: this branch must broadcast just like the success
            # path; otherwise a guest never sees the engine-fallback card
            # (which is exactly how W's opening lead used to disappear from
            # the guest while showing on the host).
            self.status_label.setText(f"Engine error: {response.who}. Trying fallback...")
            seat = self.controller.current_seat
            hand = self.controller.board.hands.get(seat)
            if hand and hand.cards:
                lead_suit = self.controller.get_lead_suit()
                if lead_suit is not None:
                    suit_cards = [c for c in hand.cards if c.suit == lead_suit]
                    card = suit_cards[0] if suit_cards else hand.cards[0]
                else:
                    card = hand.cards[0]
                self.table_view.play_card_to_trick(seat, card)
                self.controller.play_card(card)
                if (self.network_controller.is_active
                        and self.network_controller.is_server):
                    from PyQt6.QtCore import QDateTime
                    print(f"[host engine_card-fallback] "
                          f"t={QDateTime.currentMSecsSinceEpoch()} "
                          f"seat={seat.to_char()} card={card.to_str()}",
                          flush=True)
                    self.network_controller.broadcast_card(seat, card)
                QTimer.singleShot(300, self._advance_game)
            else:
                self.status_label.setText("Error: No cards available to play")
            return

        card = response.action
        seat = self.controller.current_seat

        # Remember biq's actual reason for THIS card so a later click on it
        # shows biq's real rationale (not a reconstruction). Keyed by the
        # card's unique 0-51 code — each card is played exactly once per deal.
        self._record_biq_card_reason(card, response)

        # Check if this is the opening lead (first card of play)
        is_opening_lead = (len(self.controller.board.tricks) == 0 and
                          (not self.controller.board.current_trick or
                           len(self.controller.board.current_trick.cards) == 0))

        self.table_view.play_card_to_trick(seat, card)
        self.controller.play_card(card)

        # Broadcast to network if we're the server
        if self.network_controller.is_active and self.network_controller.is_server:
            from PyQt6.QtCore import QDateTime
            print(f"[host engine_card] t={QDateTime.currentMSecsSinceEpoch()} "
                  f"seat={seat.to_char()} card={card.to_str()} "
                  f"is_opening_lead={is_opening_lead}",
                  flush=True)
            self.network_controller.broadcast_card(seat, card)

            # Dummy was already broadcast at start of play phase via
            # _advance_play.setup_declarer_play. Skipping the redundant
            # post-opening-lead broadcast.

        # Continue after delay
        if self.controller.current_phase == 'waiting_next':
            # Trick complete
            QTimer.singleShot(500, self._advance_game)
        else:
            QTimer.singleShot(300, self._advance_game)

    @pyqtSlot(str)
    def _on_engine_error(self, error: str):
        """Handle engine error"""
        self.status_label.setText(f"Engine error: {error}")

    def _on_evaluate(self):
        """Handle Evaluate button - show hand evaluation dialog."""
        # Show evaluate dialog during both bidding and play phases
        self._show_evaluate_dialog()

    def _show_evaluate_dialog(self):
        """Show the evaluate dialog for bidding phase."""
        from .dialogs import EvaluateDialog, HandEvaluationDialog

        my_seat = getattr(self.table_view, "_local_seat", Seat.SOUTH)
        dialog = EvaluateDialog(self, current_seat=self.controller.current_seat,
                                my_seat=my_seat)
        result = dialog.exec()

        if result == QDialog.DialogCode.Accepted:
            # Q-Plus result modes (card play): conventional meaning / expected
            # tricks (hidden vs open). When any is requested, show those instead
            # of the point-count dialog.
            if (dialog.want_conventional or dialog.want_tricks_hidden
                    or dialog.want_tricks_open):
                if dialog.want_conventional:
                    self._on_explain_last_signal()
                if dialog.want_tricks_hidden:
                    self._show_expected_tricks_hidden()
                if dialog.want_tricks_open:
                    self._on_dd_analysis()
                return
            # Q-Plus "ask <who> about <whom>": what that player can know about
            # that hand. "Own Hand" = my own hand (not the seat to play, which
            # may be a robot whose cards I must not see).
            if dialog.own_hand:
                ask_seat = about_seat = my_seat
            else:
                ask_seat, about_seat = dialog.ask_who, dialog.about_whom
            self._show_hand_view(ask_seat, about_seat)

    def _show_hand_view(self, ask_seat, about_seat):
        """Q-Plus Evaluate: `ask_seat`'s evaluation of `about_seat`'s hand —
        exact for a hand it can see, else its belief from the calls, the
        play and the cards it sees (backend.hand_evaluation)."""
        from .dialogs import HandEvaluationDialog
        from backend import hand_evaluation
        from backend.bid_descriptions import system_for_prefs
        from backend.config import get_config_manager
        board = self.controller.board
        if board is None or ask_seat is None or about_seat is None:
            return
        prefs = get_config_manager().config.preferences
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            view = hand_evaluation.evaluate(
                board, ask_seat, about_seat,
                system_for_prefs(prefs, 'NS'), system_for_prefs(prefs, 'EW'))
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            self._show_plain_dialog("Evaluate", f"Could not evaluate: {ex!r}",
                                    error=True)
            return
        QApplication.restoreOverrideCursor()
        if not view.get("samples"):
            self._show_plain_dialog(
                "Evaluate", f"{ask_seat.name.capitalize()} has no consistent "
                f"picture of {about_seat.name.capitalize()}'s hand.")
            return
        bid_round = len(board.auction) // 4 + 1
        HandEvaluationDialog(self, seat=about_seat, hand=view.get("hand"),
                             bid_round=bid_round, board=board,
                             ask_seat=ask_seat, view=view).exec()

    def _show_expected_tricks_hidden(self):
        """Q-Plus 'Expected tricks with hidden hands' — biq's MC+DDS engine
        scores each legal card for the player to act using only legal knowledge
        of the hidden hands. Shown as a per-card list."""
        board = self.controller.board
        seat = self.controller.current_seat
        if (board is None or seat is None
                or self.controller.current_phase != 'play'):
            self._show_plain_dialog(
                "Expected tricks (hidden hands)",
                "Available during card play, on the turn of the player to act.")
            return
        try:
            trick = (board.current_trick.cards
                     if board.current_trick else [])
            resp = self.engine.get_mc_card_play(board, seat, trick)
            cands = getattr(resp, 'candidates', None) or []
            lines = [f"Expected tricks (hidden hands) for {seat.to_char()} — "
                     f"MC+DDS over sampled layouts:", ""]
            for c in cands:
                lines.append(f"   {c.card.to_str()}:  {c.score:.2f}")
            if not cands and getattr(resp, 'action', None):
                lines.append(f"   suggested: {resp.action.to_str()}")
            self._show_plain_dialog("Expected tricks (hidden hands)",
                                    "\n".join(lines))
        except Exception as ex:
            self._show_plain_dialog("Expected tricks (hidden hands)",
                                    f"Could not compute: {ex!r}", error=True)

    def _apply_field_comparison(self, result):
        """Results-of-file comparison (Q-Plus): score the user's just-played
        board against the result(s) the tournament file recorded for it.
        Teams -> IMP vs the file's NS score; Pairs -> matchpoints vs the field.
        Sets result.imps / result.matchpoints so the score table shows it."""
        if getattr(self, '_comparison_mode', None) != 'file':
            return
        field = getattr(self, '_field_results', {}).get(result.board_number)
        if not field:
            return
        from backend.scoring import diff_to_imps, ScoringType
        field_ns = [f['ns_score'] for f in field]
        if self.scoring_table.scoring_type == ScoringType.TEAMS:
            imp = diff_to_imps(result.ns_score - field_ns[0])
            result.imps = imp
            self.status_label.setText(
                f"Board {result.board_number}: your N/S {result.ns_score:+d} "
                f"vs file {field_ns[0]:+d}  →  {imp:+d} IMP")
        elif self.scoring_table.scoring_type == ScoringType.PAIRS:
            # 2 matchpoints per field score beaten, 1 per tie (Q-Plus style).
            mp = 0.0
            for s in field_ns:
                if result.ns_score > s:
                    mp += 2
                elif result.ns_score == s:
                    mp += 1
            top = 2 * len(field_ns)
            result.matchpoints = mp
            pct = (100.0 * mp / top) if top else 0.0
            self.status_label.setText(
                f"Board {result.board_number}: {mp:.0f}/{top} MP "
                f"({pct:.0f}%) vs the field")

    def _on_view_auction_tricks(self):
        """Show the Record (auction + played tricks) dialog detached.

        Non-modal so the user can drag it to a second monitor and watch
        the play unfold next to the main table view. The dialog is
        marked WA_DeleteOnClose, so we just keep the latest reference
        and overwrite it on the next open instead of bookkeeping a list.
        """
        from .dialogs import AuctionTricksDialog

        if not self.controller.board:
            self.status_label.setText("No deal loaded")
            return

        # Collect trick information from board.tricks
        tricks = []
        board = self.controller.board
        if board.tricks:
            for trick in board.tricks:
                cards_dict = {}
                for i, card in enumerate(trick.cards):
                    seat = Seat((trick.leader.value + i) % 4)
                    seat_char = seat.to_char()
                    cards_dict[seat_char] = card

                trick_info = {
                    'leader': trick.leader.to_char(),
                    'cards': cards_dict,
                    'winner': trick.winner.to_char() if trick.winner else ''
                }
                tricks.append(trick_info)

        # If a Record window is already open, close it so we can spawn
        # a fresh one with the latest tricks. The dialog was promoted
        # to a top-level Qt.Window with WA_DeleteOnClose so the C++
        # widget is destroyed the moment the user dismisses it; the
        # Python `_record_dialog` reference outlives that, and any
        # method call on the dangling wrapper raises RuntimeError
        # ("wrapped C/C++ object … has been deleted"). Catch that.
        existing = getattr(self, '_record_dialog', None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.close()
            except RuntimeError:
                # C++ side already deleted — drop the stale reference.
                self._record_dialog = None
            except Exception:
                pass

        # Pass every seat whose hand is currently visible on the
        # table — local viewer's hand, dummy when revealed, and (when
        # viewer is dummy) declarer's hand which the app exposes too.
        # Reading hand_widgets.isVisible() catches all three uniformly.
        viewer_seat = None
        try:
            viewer_seat = self.table_view._local_seat
        except Exception:
            viewer_seat = None
        dummy_seat = None
        try:
            if getattr(self.table_view, 'dummy_revealed', False):
                dummy_seat = self.controller.dummy
        except Exception:
            dummy_seat = None
        visible_seats = set()
        try:
            for physical_seat, widget in self.table_view.hand_widgets.items():
                if widget.isVisible():
                    visible_seats.add(
                        self.table_view._logical_seat(physical_seat)
                    )
        except Exception:
            visible_seats = {s for s in (viewer_seat, dummy_seat)
                             if s is not None}
        self._record_dialog = AuctionTricksDialog(
            self,
            board=self.controller.board,
            tricks=tricks,
            viewer_seat=viewer_seat,
            dummy_seat=dummy_seat,
            visible_seats=visible_seats,
        )
        self._record_dialog.show()
        self._record_dialog.raise_()

    def _on_show_hand_log(self):
        """Reopen the Claude-annotated BDL log for the current hand.

        Order of preference for choosing what to display:
          1. The cached annotated BDL for the most-recently-completed
             hand (built after _show_claude_hand_analysis ran).
          2. Any cached entry for the current board number.
          3. The pending analysis from `_claude_pending` — fires
             `_maybe_run_pending_claude` so we render the same dialog
             as the post-hand path. From then on it's cached.
          4. Status-bar message if there's nothing to show yet.
        """
        # Try the most recent cached hand first — pending snapshot has
        # the right board+pavlicek even if the score sheet has moved on.
        pending = self._claude_pending
        if pending and pending.get('analyzed'):
            board = pending.get('board')
            try:
                hands = self.original_hands or board.hands
                pav = (format_deal_base72(deal_to_number(hands))
                       if board and all(s in hands for s in Seat) else "")
            except Exception:
                pav = ""
            if board is not None:
                key = (board.board_number, pav)
                cached = self._claude_hand_logs.get(key)
                if cached is None:
                    # Fallback: pavlicek may have differed slightly;
                    # match on board number alone.
                    for k, v in self._claude_hand_logs.items():
                        if k[0] == board.board_number:
                            cached = v
                            break
                if cached is not None:
                    self._show_annotated_bdl_dialog(
                        title=f"Hand Log — Board {board.board_number} (cached)",
                        bdl_text=cached.get('bdl', ''),
                        claude_text=cached.get('claude', ''),
                        error=False,
                    )
                    return

        # Nothing cached yet but there IS a pending hand — run / render
        # for the first time. _maybe_run_pending_claude is idempotent
        # via its 'analyzed' flag, so we clear that to force a render
        # if the user explicitly asked.
        if pending is not None:
            pending['analyzed'] = False
            self._maybe_run_pending_claude()
            return

        # Brand-new session, nothing to show.
        self.status_label.setText(
            "No hand analysis yet — finish a hand first."
        )

    def _on_edit_remark(self):
        """Edit remark for current deal."""
        from .dialogs import RemarkDialog

        if not self.controller.board:
            self.status_label.setText("No deal loaded")
            return

        # Get existing remark if any
        remark = getattr(self.controller.board, 'remark', None) or {}

        dialog = RemarkDialog(self, board=self.controller.board, remark=remark)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.controller.board.remark = dialog.get_remark()
            self.status_label.setText("Remark saved")

    def _on_view_remark(self):
        """View remark for current deal."""
        from .dialogs import ViewRemarkDialog

        if not self.controller.board:
            self.status_label.setText("No deal loaded")
            return

        remark = getattr(self.controller.board, 'remark', None)
        if not remark or not remark.get('text'):
            self.status_label.setText("No remark for this deal")
            return

        dialog = ViewRemarkDialog(self, board=self.controller.board, remark=remark)
        dialog.exec()

    def _on_show_current_log(self):
        """Show current session log file."""
        from .dialogs.log_viewer_dialog import LogViewerDialog
        import os

        # Use the GameLogger's current log file directly
        current_log = self.game_logger.current_log_file

        if current_log and os.path.exists(current_log):
            dialog = LogViewerDialog(self, str(current_log), "Current Session Log")
            dialog.exec()
        else:
            self.status_label.setText(f"No log file found - play a hand to create one")

    def _on_show_previous_logs(self):
        """Show previous log files dialog."""
        from .dialogs.log_viewer_dialog import PreviousLogsDialog

        # Use the same log directory as the GameLogger
        log_dir = str(self.game_logger.log_dir)
        os.makedirs(log_dir, exist_ok=True)

        dialog = PreviousLogsDialog(self, log_dir)
        dialog.exec()

    def _on_bid_list(self):
        """Show list of possible bids with meanings."""
        from .dialogs import BidListDialog

        if self.controller.current_phase != 'bidding':
            self.status_label.setText("List is only available during bidding")
            return

        dialog = BidListDialog(
            self,
            seat=self.controller.current_seat,
            auction=self.controller.board.auction if self.controller.board else []
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_bid = dialog.get_selected_bid()
            if selected_bid:
                # Could auto-select the bid here
                self.status_label.setText(f"Selected: {selected_bid}")

    def _on_claim(self):
        """Handle claim button - claim remaining tricks."""
        from .dialogs import ClaimDialog

        if self.controller.current_phase not in ('play', 'waiting_next'):
            self.status_label.setText("Claim is only available during play")
            return

        if not self.controller.board or not self.controller.board.contract:
            return

        # Tricks taken so far live on the BOARD (not the controller) — reading
        # them off the controller returned 0/0, so the claim mis-scored and,
        # worse, never finalised the hand (no score, Next disabled, stuck).
        board = self.controller.board
        declarer_tricks = getattr(board, 'declarer_tricks', 0)
        defense_tricks = getattr(board, 'defense_tricks', 0)
        remaining = 13 - declarer_tricks - defense_tricks

        if remaining <= 0:
            self.status_label.setText("No tricks remaining to claim")
            return

        declarer = board.contract.declarer
        current = self.controller.current_seat
        is_declarer_side = (current is not None
                            and current.is_ns() == declarer.is_ns())

        dialog = ClaimDialog(
            self,
            remaining_tricks=remaining,
            declarer_tricks=declarer_tricks,
            defense_tricks=defense_tricks,
            is_declarer=is_declarer_side
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # get_claimed_tricks() always returns DECLARER's share of the rest.
        claimed = max(0, min(remaining, dialog.get_claimed_tricks()))
        final_declarer = declarer_tricks + claimed
        final_defense = defense_tricks + (remaining - claimed)

        # Apply to the board and END the hand on the normal scoring path
        # (same as the natural 13th-trick finish): this scores it, adds it to
        # the score table, shows the end-of-hand dialog, and enables Next deal.
        board.declarer_tricks = final_declarer
        board.defense_tricks = final_defense
        self.controller.current_phase = 'finished'
        try:
            self.table_view.update_tricks(final_declarer, final_defense)
        except Exception:
            pass
        verb = "verified" if dialog.verify else "accepted"
        self.status_label.setText(
            f"Claim {verb}: Declarer {final_declarer}, Defense {final_defense}")
        QTimer.singleShot(400, self._show_result)

    def _update_button_states(self):
        """Update enabled state of toolbar buttons based on game phase."""
        phase = self.controller.current_phase

        # Undo / take-back — enabled whenever the human has a call or card to
        # take back (bidding OR play), like Q-Plus. Off during network play.
        if hasattr(self, 'undo_btn'):
            can_undo = False
            board = self.controller.board
            if board is not None and not self.network_controller.is_active:
                if phase == 'bidding':
                    auction = getattr(board, 'auction', None) or []
                    can_undo = any(
                        self.controller._human_controls_seat(
                            self._seat_at(board.dealer, i))
                        for i in range(len(auction)))
                elif phase in ('play', 'waiting_next', 'finished'):
                    can_undo = self._has_user_play(board)
            self.undo_btn.setEnabled(can_undo)

        # Claim button - available throughout card play, including the
        # between-tricks pause (phase 'waiting_next') after a trick completes.
        if hasattr(self, 'claim_btn'):
            self.claim_btn.setEnabled(phase in ('play', 'waiting_next'))

        # Review button - only after hand is finished
        if hasattr(self, 'review_btn'):
            self.review_btn.setEnabled(phase == 'finished')

    def _on_review(self):
        """Review the completed hand.

        Non-modal so the user can interact with the main window while
        reviewing — drag the dialog to a second monitor, open the
        bidding-system / score sheet / hint, etc. As the user navigates
        positions the dialog drives the table view (cards leave hands
        and slide into the centre trick area), so the animation plays
        out on the main screen as well as in the text review.
        """
        from .dialogs import ReviewDialog
        from .dialogs.dialog_style import make_detachable

        if not self.controller.board:
            self.status_label.setText("No deal to review")
            return

        # Collect trick information — the controller stores them on
        # board.tricks, not played_tricks. The old code read a
        # non-existent attribute, so review only ever stepped through
        # the auction.
        tricks = list(self.controller.board.tricks or [])

        contract_str = None
        if self.controller.board.contract:
            contract_str = self.controller.board.contract.to_str()

        dialog = ReviewDialog(
            self,
            board=self.controller.board,
            auction=self.controller.board.auction,
            tricks=tricks,
            contract=contract_str,
            engine=getattr(self, 'engine', None),
            original_hands=getattr(self, 'original_hands', None),
            table_view=self.table_view,
        )
        make_detachable(dialog)
        dialog.show()
        dialog.raise_()
        # Keep a reference so the dialog isn't GC'd; sweep dead
        # wrappers from prior reviews (make_detachable sets
        # WA_DeleteOnClose, after which isVisible raises RuntimeError).
        if not hasattr(self, '_review_dialogs'):
            self._review_dialogs = []
        survivors = []
        for d in self._review_dialogs:
            try:
                if d.isVisible():
                    survivors.append(d)
            except RuntimeError:
                pass
        survivors.append(dialog)
        self._review_dialogs = survivors

    def _on_previous_deal(self):
        """Go to the previous deal (Q-Plus: the deal with the minor number
        decreased). random_deal() is seeded by board number, so deal n-1 is the
        same deterministic deal Q-Plus would show. Goes through the full
        new-deal setup (state reset + auction start), not a partial redraw."""
        if not self.controller.board:
            self.status_label.setText("No current deal")
            return

        current_num = self.controller.board.board_number
        if current_num <= 1:
            self.status_label.setText("Already at first deal")
            return
        prev_num = current_num - 1

        # In a teams match, step the board pointer back and re-deal that board
        # number (the previous deal of the scoring table).
        if self.teams_match is not None and self.match_controller is not None:
            self.teams_match.current_board = prev_num
            self._start_teams_board(prev_num)
            return

        board = self.controller.new_deal(prev_num)   # full reset, deterministic
        self._present_fresh_board(board)
        self.status_label.setText(f"Deal #{prev_num} loaded")

    def _on_lead_signal(self, pair: str):
        """Show lead and signalling conventions dialog."""
        from .dialogs import LeadSignalDialog

        dialog = LeadSignalDialog(self, pair=pair)
        dialog.exec()

    def _on_strength(self):
        """Show playing strength dialog."""
        from .dialogs import StrengthDialog

        dialog = StrengthDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.status_label.setText("Strength settings saved")

    def _on_config_check(self):
        """Show configuration check dialog."""
        from .dialogs import ConfigCheckDialog

        dialog = ConfigCheckDialog(self)
        dialog.exec()

    def _on_save_configuration(self):
        """Q-Plus Configuration / Save configuration — write the current
        settings (the B-*.CFG set) into a user-chosen folder so it can be
        retrieved later."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path
        import shutil
        dest = QFileDialog.getExistingDirectory(
            self, "Save configuration to folder", str(Path.home()))
        if not dest:
            return
        try:
            self.config_manager.save_all()           # flush current → CONFIG/
            src = self.config_manager.config_dir
            n = 0
            for cfg in ("B-INIT.CFG", "B-MATCH.CFG", "B-PREFER.CFG"):
                p = src / cfg
                if p.exists():
                    shutil.copy(p, Path(dest) / cfg)
                    n += 1
            self.status_label.setText(
                f"Configuration saved to {dest} ({n} files).")
        except Exception as ex:
            QMessageBox.warning(self, "Save configuration failed", str(ex))

    def _on_retrieve_configuration(self):
        """Q-Plus Configuration / Retrieve configuration — load a previously
        saved configuration set (a folder of B-*.CFG files) into the active
        config. Some changes (players/systems) take effect on the next deal."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path
        import shutil
        src = QFileDialog.getExistingDirectory(
            self, "Retrieve configuration from folder", str(Path.home()))
        if not src:
            return
        srcp = Path(src)
        if not any((srcp / c).exists() for c in
                   ("B-INIT.CFG", "B-MATCH.CFG", "B-PREFER.CFG")):
            QMessageBox.warning(
                self, "Retrieve configuration",
                "That folder has no B-*.CFG configuration files.")
            return
        try:
            dst = self.config_manager.config_dir
            for cfg in ("B-INIT.CFG", "B-MATCH.CFG", "B-PREFER.CFG"):
                p = srcp / cfg
                if p.exists():
                    shutil.copy(p, dst / cfg)
            self.config_manager.load_all()
            try:
                self._refresh_suit_colors()
            except Exception:
                pass
            self.status_label.setText(
                "Configuration retrieved. Player/system changes apply "
                "from the next deal.")
        except Exception as ex:
            QMessageBox.warning(self, "Retrieve configuration failed", str(ex))

    def _on_check_user_actions(self, checked: bool):
        """Q-Plus Extras / Check user actions — toggle the live blunder check
        (reports weak bids/cards as you make them)."""
        try:
            self.config_manager.config.preferences.blunder_check_enabled = bool(
                checked)
            self.config_manager.save_preferences()
        except Exception:
            pass
        self.status_label.setText(
            f"Check user actions: {'ON' if checked else 'OFF'}.")

    def _on_save_match_and_exit(self):
        """Q-Plus File / Save Match + Exit — persist the scoring table so the
        next start resumes it, then close. (Plain Exit starts fresh.)"""
        try:
            self.config_manager.save_all()
            path = self.config_manager.config_dir / "SAVED-MATCH.qss"
            self.scoring_table.save(str(path))
        except Exception as ex:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save Match failed",
                                f"Could not save the match:\n{ex}")
            return
        self._match_saved_on_exit = True
        self.close()

    def _maybe_restore_saved_match(self):
        """At startup, resume a match saved via 'Save Match + Exit' (then
        consume it so a later plain Exit starts fresh, matching Q-Plus)."""
        try:
            from backend.scoring import ScoringTable
            path = self.config_manager.config_dir / "SAVED-MATCH.qss"
            if not path.exists():
                return
            self.scoring_table = ScoringTable.load(str(path))
            path.unlink()
            self.status_label.setText(
                "Resumed saved match (Save Match + Exit).")
        except Exception as e:
            print(f"saved-match restore failed: {e!r}", flush=True)

    def _on_minibridge(self):
        """Toggle or configure One Player / MiniBridge mode. The
        dialog persists the choice to PreferencesConfig; we just
        update the status bar + the menu's checked indicator (set
        when EITHER alternative mode is active)."""
        from .dialogs import MiniBridgeDialog
        from backend.config import get_config_manager
        prefs = get_config_manager().config.preferences
        dialog = MiniBridgeDialog(self, prefs=prefs)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mode = dialog.play_mode
        # Reflect the chosen mode in the status bar and the menu
        # check. The actual auction / cardplay flow change is wired
        # separately on the next deal (queued for a follow-up).
        self.minibridge_action.setChecked(mode != 'off')
        if mode == 'minibridge':
            self.status_label.setText("MiniBridge mode active")
        elif mode == 'one_player':
            seat = prefs.one_player_seat
            self.status_label.setText(
                f"One-Player mode active (seat {seat})")
        else:
            self.status_label.setText("Standard Bridge mode")

    def _on_multiplay(self):
        """Show computer multiplay dialog."""
        from .dialogs import MultiplayDialog

        dialog = MultiplayDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.get_settings()
            # Would start multiplay mode here
            self.status_label.setText(
                f"Computer multiplay: {settings['num_deals']} deals"
            )
            # Enable autoplay for all positions
            for seat in Seat:
                self.controller.players[seat].player_type = PlayerType.COMPUTER

            # Start first deal
            if settings['num_deals'] > 0:
                self._on_new_deal()
                self.autoplay_btn.setChecked(True)

    def _on_rubber_scoring(self):
        """Show rubber scoring dialog."""
        from .dialogs import RubberScoringDialog

        dialog = RubberScoringDialog(self)
        dialog.exec()

    def _maybe_run_pending_claude(self):
        """Run Claude on the most recently finished hand if it's still
        pending. Idempotent — sets `analyzed=True` so subsequent calls
        from a different trigger (ingest then next-deal, etc.) are a
        no-op. By the time Claude actually runs, the score sheet has
        whatever closed-room data the user managed to attach (or none),
        so the prompt automatically gets the comparison form when a
        Q-Plus row is present and the open-room-only form otherwise.
        """
        if not self._claude_enabled():
            return                       # silent no-op when Claude is off
        pending = self._claude_pending
        if not pending or pending.get('analyzed'):
            return
        pending['analyzed'] = True
        try:
            self._show_claude_hand_analysis(
                pending['board'],
                pending['contract'],
                pending['tricks'],
                pending['result_str'],
                pending['score'],
            )
        except Exception as e:
            print(f"[claude] deferred analysis failed: {e}", flush=True)

    # ------------------------------------------------------------------
    # Q-Plus closed-room ingest
    # ------------------------------------------------------------------

    def _refresh_qplus_ingest_action_state(self):
        """Enable/disable the ingest menu item based on Q-Plus install
        and current network role. Guests get the score-sheet Compare
        button via host broadcast; they don't ingest themselves."""
        if not hasattr(self, 'ingest_qplus_action'):
            return
        try:
            from backend.qplus import qplus_available
            installed = qplus_available()
        except Exception:
            installed = False
        is_guest = (self.network_controller.is_active
                    and not self.network_controller.is_server)
        enabled = installed and not is_guest
        self.ingest_qplus_action.setEnabled(enabled)
        if not installed:
            self.ingest_qplus_action.setToolTip(
                "Q-Plus Bridge isn't installed. Run FRI/qplus.sh once to install it."
            )
        elif is_guest:
            self.ingest_qplus_action.setToolTip(
                "Closed-room ingest happens on the host; the result will appear here automatically."
            )
        else:
            self.ingest_qplus_action.setToolTip("")

    def _on_load_lin_database(self):
        """Load a LIN file (BBO vugraph) as a closed-room database.

        Each played-out deal in the file is ingested as a closed-room
        BoardResult. If the open-room score sheet already has a row for
        the same board, the LIN result attaches there (so IMPs compute);
        otherwise the LIN deal stands alone in the score sheet, ready
        for a future open-room hand to pair with.
        """
        from backend.lin_reader import (
            load_lin_file, lin_deal_to_board_run
        )
        from backend.models import BenTable

        filename, _ = QFileDialog.getOpenFileName(
            self, "Select LIN file (BBO vugraph)", "",
            "LIN files (*.lin);;All files (*)"
        )
        if not filename:
            return

        try:
            deals = load_lin_file(filename)
        except Exception as e:
            QMessageBox.warning(self, "LIN Read Error",
                                f"Could not parse LIN file:\n{e}")
            return
        if not deals:
            QMessageBox.warning(self, "Empty LIN",
                                "No deals were found in the selected LIN file.")
            return

        ingested = 0
        skipped = 0
        for deal in deals:
            run = lin_deal_to_board_run(deal, table=BenTable.CLOSED)
            if run is None:
                skipped += 1
                continue
            try:
                self._attach_closed_room_run(run, replace_ai_run=True)
                ingested += 1
            except Exception as e:
                print(f"[lin ingest] attach failed for board "
                      f"{deal.board_number}: {e}", flush=True)
                skipped += 1

        # Push the ingested deals to guests so their score sheets line up.
        if (self.network_controller.is_active
                and self.network_controller.is_server):
            for deal in deals:
                run = lin_deal_to_board_run(deal, table=BenTable.CLOSED)
                if run is None:
                    continue
                try:
                    self.network_controller.broadcast_closed_room_ingested(run)
                except Exception as e:
                    print(f"[lin ingest] broadcast failed: {e}", flush=True)

        self.status_label.setText(
            f"Loaded {ingested} LIN deal(s) as closed room"
            + (f" ({skipped} skipped — no contract / no hands)" if skipped else "")
        )

        # Refresh an open Scoring Table dialog so the new rows show up
        # without the user having to reopen it.
        scores = getattr(self, '_scores_dialog', None)
        if scores is not None and scores.isVisible():
            try:
                scores._populate_table()
            except Exception:
                pass

    def _on_ingest_qplus_closed_room(self):
        """File picker → BDL parse → attach run to score sheet → broadcast.

        If multiple deals are present in the BDL, picks the one matching
        the most recent open-room result (by pavlicek_id, falling back
        to board number); if none match, asks the user.
        """
        from backend.qplus import qplus_bdl_log_dir, newest_bdl_path
        from backend.bdl_reader import (load_bdl_file, load_qss_file,
                                        bdl_deal_to_board_run)
        from backend.models import BenTable

        # Default the picker to the qplus log dir + newest BDL.
        log_dir = qplus_bdl_log_dir()
        default_path = ""
        newest = newest_bdl_path()
        if newest is not None:
            default_path = str(newest)
        elif log_dir is not None:
            default_path = str(log_dir)

        filename, _ = QFileDialog.getOpenFileName(
            self, "Select Q-Plus closed-room file", default_path,
            "Q-Plus files (*.bdl *.qss);;BDL log (*.bdl);;"
            "QSS score export (*.qss);;All files (*)"
        )
        if not filename:
            return

        from .dialogs.dialog_style import styled_warning, styled_info
        # Both .bdl logs and .qss teams exports carry the full deal (hands,
        # auction, contract, card-by-card play); the .qss just wraps each BDL
        # block in "D1 " lines, which load_qss_file strips. Either way we get
        # the same BDLDeal list and the same closed-room ingest.
        try:
            if filename.lower().endswith(".qss"):
                deals = load_qss_file(filename)
            else:
                deals = load_bdl_file(filename)
        except Exception as e:
            styled_warning(self, "Read error",
                           f"Could not parse the file:\n{e}")
            return
        if not deals:
            styled_warning(self, "Nothing to load",
                           "No deals were found in the selected file.")
            return

        # Pick the deal that matches an existing open-room result, if any.
        chosen = self._pick_bdl_deal_for_ingest(deals)
        if chosen is None:
            return

        run = bdl_deal_to_board_run(chosen, table=BenTable.CLOSED)
        if run is None:
            styled_warning(
                self, "Deal not played out",
                "That deal has only the hands recorded — no contract or card "
                "play — so there's nothing to compare against the open room.\n\n"
                "Pick a deal that was actually bid and played (it shows a "
                "contract in the list).")
            return

        attached = self._attach_closed_room_run(run, replace_ai_run=True)
        if not attached:
            styled_info(
                self, "Closed room loaded",
                f"Loaded board {run.board_number}, but no matching open-room "
                "result was found — added as a standalone score-sheet entry."
            )
        else:
            self.status_label.setText(
                f"Closed room ingested for board {run.board_number}."
            )
            # Open the side-by-side compare for this board immediately
            # so the user lands on the screen showing both rooms.
            self._open_compare_for_board(run.board_number)

        # Push to guests so they get the Compare button too.
        if (self.network_controller.is_active
                and self.network_controller.is_server):
            try:
                self.network_controller.broadcast_closed_room_ingested(run)
            except Exception as e:
                print(f"[ingest qplus] broadcast failed: {e}", flush=True)

    def _open_compare_for_board(self, board_number: int):
        """Open the side-by-side Compare dialog for the given board.

        Used after a fresh closed-room ingest so the user sees the
        comparison screen directly instead of having to navigate to
        the score sheet and click Compare. Non-modal; multiple
        compare windows can be left open across boards.
        """
        try:
            from .dialogs.compare_replay import CompareReplayDialog
        except Exception:
            return
        # The score sheet keeps two BoardResults per board_number when a
        # closed-room run is attached — one with table==OPEN and one
        # with table==CLOSED. We want both runs so the compare dialog
        # has two sides to draw.
        from backend.models import BenTable
        open_run = None
        closed_run = None
        for r in (self.scoring_table.results if self.scoring_table else []):
            if r.board_number != board_number:
                continue
            run = getattr(r, 'board_run', None)
            if run is None:
                continue
            if getattr(run, 'table', None) == BenTable.CLOSED:
                closed_run = run
            else:
                open_run = run
        if open_run is None or closed_run is None:
            return
        try:
            dlg = CompareReplayDialog(open_run, closed_run, parent=self)
            from .dialogs.dialog_style import make_detachable
            try:
                make_detachable(dlg)
            except Exception:
                pass
            dlg.show()
            dlg.raise_()
            if not hasattr(self, '_compare_dialogs'):
                self._compare_dialogs = []
            survivors = []
            for d in self._compare_dialogs:
                try:
                    if d.isVisible():
                        survivors.append(d)
                except RuntimeError:
                    pass
            survivors.append(dlg)
            self._compare_dialogs = survivors
        except Exception as e:
            print(f"[compare auto-open] failed: {e}", flush=True)

    def _pick_bdl_deal_for_ingest(self, deals):
        """Choose which BDL deal corresponds to the open-room hand.

        Strategy:
        1. If there's only one deal, use it.
        2. Otherwise, score each candidate by match strength against the
           score sheet's existing open-room results (pavlicek_id beats
           board_number) and pick the strongest match.
        3. If still ambiguous, fall back to the newest deal.
        """
        if len(deals) == 1:
            return deals[0]

        existing = list(self.scoring_table.results) if self.scoring_table else []
        existing_pavs = {r.pavlicek_id for r in existing if r.pavlicek_id}
        existing_boards = {r.board_number for r in existing}

        # Strongest: pavlicek match. Next: board_number match.
        for d in deals:
            if d.pavlicek_id and d.pavlicek_id in existing_pavs:
                return d
        # The deal labels in a .qss/.bdl (RANDOM-001…) aren't our base-72 ids,
        # but the CARDS are — compute the pavlicek from the hands and match
        # that, so a 64-board export auto-picks the open-room deal instead of
        # making the user scroll a long list.
        if existing_pavs:
            for d in deals:
                try:
                    if d.hands and all(s in d.hands for s in Seat):
                        if format_deal_base72(deal_to_number(d.hands)) in existing_pavs:
                            return d
                except Exception:
                    pass
        for d in deals:
            if d.board_number in existing_boards:
                return d

        # No match — let the user pick. Build distinguishable, informative
        # labels (the Q-Plus deal id + the contract), and flag the deals that
        # can't be ingested (passed out / no play). "Board 0" twice was
        # useless when the BDL used deal ids like BB-181111 rather than board
        # numbers.
        def _label(i, d):
            name = (d.pavlicek_id
                    or (f"Board {d.board_number}" if d.board_number
                        else f"Deal {i + 1}"))
            if d.contract:
                dec = d.declarer or d.contract.declarer
                tail = d.contract.to_str() + (f" by {dec.to_char()}" if dec else "")
            else:
                tail = "no contract / not played — can't ingest"
            return f"{i + 1}. {name} — {tail}"

        labels = [_label(i, d) for i, d in enumerate(deals)]
        # Default the selection to the first PLAYED deal (one with a contract),
        # so the user isn't pre-pointed at an un-ingestable passed-out board.
        default_idx = next((i for i, d in enumerate(deals) if d.contract), 0)

        from PyQt6.QtWidgets import QInputDialog, QDialog
        from .dialogs.dialog_style import apply_dialog_style
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Pick deal")
        dlg.setLabelText("Multiple deals in this file — which is the closed "
                         "room?")
        dlg.setComboBoxItems(labels)
        dlg.setComboBoxEditable(False)
        dlg.setTextValue(labels[default_idx])
        apply_dialog_style(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return deals[labels.index(dlg.textValue())]

    def _attach_closed_room_run(self, run, replace_ai_run: bool = True) -> bool:
        """Attach a closed-room BenBoardRun to the matching BoardResult.

        Match priority:
          1. pavlicek_id equality
          2. board_number equality
          3. most-recent open-room row without a closed-room sibling
             (Q-Plus and bridgeIQ use independent board numbering, so
             after the user has just played a hand and done the closed
             room manually, the latest unpaired row is almost always the
             intended target).

        If matched, the closed run's board_number is overridden to the
        open-room's number so the score-sheet groups them on the same row.

        Returns True if a matching open-room result was found, False if
        the row was added standalone.
        """
        from backend.scoring import BoardResult
        from backend.models import BenTable

        if self.scoring_table is None:
            return False

        def _is_closed(r):
            return r.notes and "Closed room" in (r.notes or "")

        results = self.scoring_table.results

        # Pavlicek match — only meaningful when both sides recorded a non-
        # empty pavlicek_id. bridgeIQ skips it on guests; Q-Plus may not
        # write it at all. So most ingest paths fall through to step 2/3.
        match_idx = None
        if run.pavlicek_id:
            for i, r in enumerate(results):
                if (not _is_closed(r)
                        and r.pavlicek_id
                        and r.pavlicek_id == run.pavlicek_id):
                    match_idx = i
                    break

        # Board-number match.
        if match_idx is None:
            for i, r in enumerate(results):
                if not _is_closed(r) and r.board_number == run.board_number:
                    match_idx = i
                    break

        # Fallback: latest open-room row that doesn't already have a
        # closed-room sibling. Walk from newest to oldest.
        if match_idx is None:
            paired_boards = {r.board_number for r in results if _is_closed(r)}
            for i in range(len(results) - 1, -1, -1):
                r = results[i]
                if not _is_closed(r) and r.board_number not in paired_boards:
                    match_idx = i
                    break

        # If matched, snap the closed-run's board_number onto the open one
        # so they group together in the score sheet. The BDL parser's
        # system tags (`.Bidding cnv : N/S: …` / `… E/W: …`) are passed
        # through verbatim — we never invent them, so when the BDL
        # genuinely doesn't carry tags the Compare dialog's "system
        # tag is blank" warning fires (which is the right signal that
        # the two rooms might have used different systems).
        if match_idx is not None:
            target_board = results[match_idx].board_number
            # Mutate the run itself too — the compare dialog reads
            # `run.board_number` to compute dealer / vulnerability,
            # and Q-Plus's auto-generated board number gives the
            # wrong rotation entries. Without this, the closed-room
            # header showed a stale dealer (e.g. W instead of S).
            try:
                run.board_number = target_board
            except Exception:
                pass
        else:
            target_board = run.board_number

        # Remove any existing closed-room row for the target board so the
        # manual qplus run replaces the AI auto-closed-room.
        if replace_ai_run:
            self.scoring_table.results = [
                r for r in self.scoring_table.results
                if not (r.board_number == target_board and _is_closed(r))
            ]

        # Build the closed-room BoardResult. Dealer/vulnerability are
        # placeholders — the score sheet only displays board number,
        # contract, and scores per row, and we don't lose anything by
        # not threading the BDL header values all the way through.
        closed_result = BoardResult(
            board_number=target_board,
            pavlicek_id=run.pavlicek_id,
            dealer=Seat.NORTH,
            vulnerability=Vulnerability.NONE,
            contract=run.contract,
            declarer=run.contract.declarer if run.contract else None,
            tricks_made=run.declarer_tricks,
            ns_score=run.ns_score,
            ew_score=run.ew_score,
            notes="Closed room (Q-Plus)",
            board_run=run,
        )
        self.scoring_table.add_result(closed_result)

        # Refresh an open Scoring Table dialog (now non-modal) so the
        # newly attached closed-room row shows up immediately and the
        # Compare button has the data to light up. Without this the user
        # still sees the pre-ingest snapshot until they re-open the dialog.
        scores = getattr(self, '_scores_dialog', None)
        if scores is not None and scores.isVisible():
            try:
                scores._populate_table()
            except Exception as e:
                print(f"[ingest qplus] score dialog refresh failed: {e}",
                      flush=True)

        return match_idx is not None

    def closeEvent(self, event):
        """Auto-save scores and clean up resources."""
        # Auto-save scoring table if any boards were played
        if self.scoring_table and self.scoring_table.results:
            try:
                path = self.scoring_table.save_to_local_matches()
                print(f"Match scores auto-saved to {path}")
            except Exception as e:
                print(f"Could not auto-save scores: {e}")

        # Stop the engine worker thread if running
        if self.engine_worker is not None:
            if self.engine_worker.isRunning():
                self.engine_worker.quit()
                # Wait up to 2 seconds for thread to finish
                if not self.engine_worker.wait(2000):
                    self.engine_worker.terminate()
                    self.engine_worker.wait()

        # Clean up the BridgeEngine (releases TensorFlow resources)
        if self.engine is not None:
            self.engine.cleanup()

        event.accept()
