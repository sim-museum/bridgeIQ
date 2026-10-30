"""
High-level network game controller.

Manages network game state and coordinates between the server/client
and the game engine.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from typing import Optional, List

from .server import BridgeServer
from .client import BridgeClient
from .serious_games_week import Announcer
from .protocol import (
    NetworkMessage, MessageType, NetworkRole,
    make_deal_start, make_bid_made, make_card_played,
    make_trick_complete, make_trick_clear, make_board_complete,
    make_closed_room_ingested,
)
from backend.models import Seat, Bid, Card, BoardState, Hand, PlayerType, BenBoardRun

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def get_app_version() -> str:
    """Build identifier for the current bridgeIQ revision so the host
    and joining guests can verify they're on the same code. Uses git
    rev-parse first; falls back to the modification time of this file."""
    # Backlog 28: the same string the Serious Games Week matchmaker matches on (set by the launcher or AppImage),
    # so a join typed in by address is held to the same rule as one found through the matchmaker.
    if os.environ.get("SGW_BUILD"):
        return os.environ["SGW_BUILD"].strip()
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ['git', '-C', here, 'rev-parse', '--short=12', 'HEAD'],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            v = result.stdout.strip()
            if v:
                return v
    except Exception:
        pass
    try:
        return f"mtime:{int(os.path.getmtime(os.path.abspath(__file__)))}"
    except Exception:
        return "unknown"


APP_VERSION = get_app_version()


class NetworkGameController(QObject):
    """
    High-level controller for network bridge games.

    Handles both server and client modes, providing a unified interface
    for the main window to interact with network play.

    Supports two client roles:
    - partner: Client plays on the same team as host (cooperative)
    - opponent: Client plays against the host (competitive)
    """

    # Connection signals
    connection_established = pyqtSignal(str, str, str, str)  # mode, my_seat, partner_seat, client_role
    connection_lost = pyqtSignal(str)  # reason
    client_joined = pyqtSignal(str, str)  # client_name, client_role
    server_started = pyqtSignal(int)  # port

    # Lobby signals (used by host lobby + guest seat-picker dialogs)
    seat_map_changed = pyqtSignal(dict)  # {seat_char: name_or_None}
    game_starting = pyqtSignal()  # Host has closed the lobby
    seat_request_rejected = pyqtSignal(str)  # Soft seat-taken rejection

    # Game signals
    deal_received = pyqtSignal(object)  # BoardState
    dummy_revealed = pyqtSignal(object, object)  # dummy_seat (Seat), dummy_hand (Hand)
    remote_bid_received = pyqtSignal(str, object)  # seat_char, Bid
    remote_card_received = pyqtSignal(str, object)  # seat_char, Card
    trick_completed = pyqtSignal(str, int, int)  # winner_seat, declarer_tricks, defense_tricks
    trick_clear_received = pyqtSignal()  # Remote player clicked "next card"
    board_completed = pyqtSignal(dict)  # result data
    closed_room_ingested = pyqtSignal(object)  # BenBoardRun (closed-room run pushed by host)

    # Error signal
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._server: Optional[BridgeServer] = None
        self._client: Optional[BridgeClient] = None
        self._role: Optional[NetworkRole] = None

        # Seat assignments
        self._my_seat: Optional[Seat] = None
        self._partner_seat: Optional[Seat] = None

        # Client role: "partner" (same team as host) or "opponent" (vs host)
        self._client_role = "partner"

        # Remote player's seats (the seats they control)
        self._remote_seats: List[Seat] = []

        # My name
        self._my_name = ""

        # Current board state (for server to track)
        self._current_board: Optional[BoardState] = None

    @property
    def is_active(self) -> bool:
        """Check if network game is active (server or client connected)."""
        if self._role == NetworkRole.SERVER:
            return self._server is not None and self._server.is_running
        elif self._role == NetworkRole.CLIENT:
            return self._client is not None and self._client.is_connected
        return False

    @property
    def is_server(self) -> bool:
        """Check if we are the server."""
        return self._role == NetworkRole.SERVER

    @property
    def is_client(self) -> bool:
        """Check if we are a client."""
        return self._role == NetworkRole.CLIENT

    @property
    def my_seat(self) -> Optional[Seat]:
        """Get my seat."""
        return self._my_seat

    @property
    def partner_seat(self) -> Optional[Seat]:
        """Get my partner's seat."""
        return self._partner_seat

    @property
    def client_role(self) -> str:
        """Get the client role ('partner' or 'opponent')."""
        return self._client_role

    @property
    def my_seats(self) -> List[Seat]:
        """Get list of seats I control.

        Always just my own seat. The legacy "opponent mode" had the host
        play a whole partnership when only one guest was at the table —
        that produced the host-bids-for-North bug when a guest sat West.
        Empty seats are now AI on every connected client; if N also has
        a guest, that guest's controller will list N as my_seats[0] on
        their side, and the host's remote_seats will include N.
        """
        if self._my_seat is None:
            return []
        return [self._my_seat]

    @property
    def remote_seats(self) -> List[Seat]:
        """Get list of seats controlled by the remote player."""
        return self._remote_seats

    @property
    def ai_seats(self) -> List[Seat]:
        """Get list of seats controlled by AI (neither me nor remote)."""
        all_seats = set(Seat)
        controlled = set(self.my_seats) | set(self._remote_seats)
        return list(all_seats - controlled)

    def is_my_seat(self, seat: Seat) -> bool:
        """Check if a seat is controlled by me (local player)."""
        result = seat in self.my_seats
        logger.debug(f"is_my_seat({seat}): _my_seat={self._my_seat}, _client_role={self._client_role}, my_seats={self.my_seats}, result={result}")
        return result

    def is_remote_seat(self, seat: Seat) -> bool:
        """Check if a seat is controlled by the remote player."""
        if not self.is_active:
            return False
        result = seat in self._remote_seats
        logger.debug(f"is_remote_seat({seat}): remote_seats={self._remote_seats}, result={result}")
        return result

    def is_ai_seat(self, seat: Seat) -> bool:
        """Check if a seat is controlled by AI."""
        if not self.is_active:
            return False
        return seat in self.ai_seats

    # Server mode

    def start_server(self, port: int, name: str, seat: Seat, password: str = "") -> bool:
        """
        Start a server to host a game.

        Args:
            port: Port to listen on
            name: Host player name
            seat: Host player's seat

        Returns:
            True if server started successfully
        """
        if self._server is not None or self._client is not None:
            self.disconnect()

        self._server = BridgeServer(self, app_version=APP_VERSION, password=password)
        self._server.client_connected.connect(self._on_client_connected)
        self._server.client_disconnected.connect(self._on_client_disconnected)
        self._server.seat_swap.connect(self._on_seat_swap)
        self._server.seat_map_changed.connect(self.seat_map_changed)
        self._server.message_received.connect(self._on_message_received)
        self._server.error_occurred.connect(self._on_error)
        self._server.server_started.connect(self._on_server_started)

        if self._server.start(port, name, seat):
            self._role = NetworkRole.SERVER
            self._my_seat = seat
            self._partner_seat = seat.partner()
            self._my_name = name
            # Serious Games Week: list the table on the player's matchmaker while it is open (withdrawn in disconnect)
            self._matchmaker = Announcer()
            self._matchmaker.start(port, "%s's bridge table" % name, name=name, max_players=4, version=APP_VERSION)
            return True

        self._server = None
        return False

    def _on_server_started(self, port: int):
        """Handle server start."""
        logger.info(f"Server started on port {port}")
        self.server_started.emit(port)

    def _on_client_connected(self, client_name: str, client_role: str):
        """Handle client connection.

        Accumulates remote_seats from every connected guest — the server
        accepts up to 3 guests, each picking any free seat, so this signal
        fires once per guest and remote_seats must reflect all of them.
        Without this, the host would re-run the AI for earlier guests'
        seats and double-control them.
        """
        logger.info(f"Client connected: {client_name} as {client_role}")
        self._client_role = client_role

        # Pull every connected guest's seat directly from the server so we
        # don't lose earlier guests when later ones join.
        self._remote_seats = list(self._server.client_seats)

        self.client_joined.emit(client_name, client_role)
        self.connection_established.emit(
            "server",
            self._my_seat.to_char(),
            self._partner_seat.to_char(),
            client_role
        )

    def _on_client_disconnected(self):
        """Handle client disconnection. Refresh remote_seats from the server's
        live roster so the host stops treating the departed guest's seat as
        remote (and resumes AI for it). connection_lost is emitted to keep
        the legacy single-guest UI flow alive."""
        logger.info("Client disconnected")
        if self._server is not None:
            self._remote_seats = list(self._server.client_seats)
        self.connection_lost.emit("Client disconnected")

    def _on_seat_swap(self, seat_char: str, old_label: str, new_label: str):
        """Log a bot↔human seat swap so post-game review can see who was
        controlling each seat at any point in the game."""
        logger.info(f"[seat swap] {seat_char}: {old_label} → {new_label}")

    # Client mode

    def connect_to_server(self, host: str, port: int, name: str,
                          requested_seat: str = "",
                          role: str = "partner", password: str = "") -> bool:
        """
        Connect to a server.

        Args:
            host: Server hostname or IP
            port: Server port
            name: Client player name
            requested_seat: Seat char (N/E/S/W) the guest wants. Overrides
                `role` when present. If the seat is taken the server replies
                with CONNECT_REJECT including a list of free seats.
            role: Legacy fallback used only if requested_seat is empty.

        Returns:
            True if connection attempt started
        """
        if self._server is not None or self._client is not None:
            self.disconnect()

        self._client = BridgeClient(self, app_version=APP_VERSION, password=password)
        self._client.connected.connect(self._on_connected_to_server)
        self._client.disconnected.connect(self._on_disconnected_from_server)
        self._client.connection_failed.connect(self._on_connection_failed)
        self._client.message_received.connect(self._on_message_received)
        self._client.seat_map_changed.connect(self.seat_map_changed)
        self._client.game_starting.connect(self.game_starting)
        self._client.seat_request_rejected.connect(self.seat_request_rejected)

        self._my_name = name
        self._client_role = role
        return self._client.connect_to_server(host, port, name,
                                              requested_seat=requested_seat,
                                              role=role)

    def _on_connected_to_server(self, server_name: str, my_seat: str, partner_seat: str, role: str):
        """Handle successful connection to server."""
        logger.info(f"_on_connected_to_server: received my_seat={my_seat}, partner_seat={partner_seat}, role={role}")
        self._role = NetworkRole.CLIENT
        self._my_seat = Seat.from_char(my_seat)
        self._partner_seat = Seat.from_char(partner_seat)
        self._client_role = role
        logger.info(f"_on_connected_to_server: set _my_seat={self._my_seat}, _partner_seat={self._partner_seat}, _client_role={self._client_role}")

        # Each player controls only their own seat. Remote seats start
        # with just the host's seat; SEAT_LIST broadcasts later teach us
        # about additional guests at other seats. Empty seats are local
        # AI on the guest side too.
        host_char = (self._client.host_seat_char or "").upper() if self._client else ""
        try:
            host_seat = Seat.from_char(host_char) if host_char else None
        except Exception:
            host_seat = None
        self._remote_seats = [host_seat] if host_seat is not None else [self._partner_seat]
        logger.info(f"_on_connected_to_server: remote_seats={self._remote_seats}")

        logger.info(f"Connected to '{server_name}' as {my_seat}, partner {partner_seat}, role {role}")
        self.connection_established.emit("client", my_seat, partner_seat, role)

    def _on_disconnected_from_server(self):
        """Handle disconnection from server."""
        logger.info("Disconnected from server")
        self.connection_lost.emit("Disconnected from server")
        self._cleanup()

    def _on_connection_failed(self, reason: str):
        """Handle connection failure."""
        logger.warning(f"Connection failed: {reason}")
        self.error_occurred.emit(reason)
        self._cleanup()

    # Lobby helpers

    def current_seat_map(self) -> dict:
        """Return the current seat occupancy if hosting, else an empty dict.

        Used by the host's lobby dialog to render the initial state before
        any guest has joined.
        """
        if self._server is not None:
            return self._server.seat_map()
        return {}

    def host_seat_char(self) -> str:
        """Return the host's seat char.

        On the host this is just self._my_seat. On guests we read whatever
        the server most recently broadcast via SEAT_LIST.
        """
        if self._role == NetworkRole.SERVER and self._my_seat is not None:
            return self._my_seat.to_char()
        if self._client is not None:
            return self._client.host_seat_char
        return ""

    def start_game(self):
        """Host fires this to close the lobby and begin the deal flow.

        Broadcasts GAME_START to every guest so their seat-picker dialogs
        can dismiss themselves and switch to game UI.
        """
        if self._role == NetworkRole.SERVER and self._server is not None:
            self._server.broadcast_game_start()

    def request_seat(self, seat_char: str) -> bool:
        """Guest-side: claim a seat from the live SEAT_LIST. Used by the
        post-connect lobby seat picker."""
        if self._client is not None:
            return self._client.request_seat(seat_char)
        return False

    # Common methods

    def disconnect(self):
        """Disconnect from network game."""
        if self._server is not None:
            if getattr(self, "_matchmaker", None):
                self._matchmaker.stop()          # withdraw the table from the Serious Games Week matchmaker
                self._matchmaker = None
            self._server.stop()
            self._server = None

        if self._client is not None:
            self._client.disconnect_from_server()
            self._client = None

        self._cleanup()

    def _cleanup(self):
        """Clean up state."""
        self._role = None
        self._my_seat = None
        self._partner_seat = None
        self._current_board = None
        self._client_role = "partner"
        self._remote_seats = []

    def _on_error(self, error: str):
        """Handle error."""
        logger.error(f"Network error: {error}")
        self.error_occurred.emit(error)

    def _on_message_received(self, message: NetworkMessage):
        """Handle received message."""
        logger.debug(f"Message received: {message.type.value}")

        if message.type == MessageType.DEAL_START:
            self._handle_deal_start(message)
        elif message.type == MessageType.BID_MADE:
            self._handle_bid_made(message)
        elif message.type == MessageType.CARD_PLAYED:
            self._handle_card_played(message)
        elif message.type == MessageType.TRICK_COMPLETE:
            self._handle_trick_complete(message)
        elif message.type == MessageType.TRICK_CLEAR:
            self._handle_trick_clear(message)
        elif message.type == MessageType.BOARD_COMPLETE:
            self._handle_board_complete(message)
        elif message.type == MessageType.CLOSED_ROOM_INGESTED:
            self._handle_closed_room_ingested(message)

    def _handle_deal_start(self, message: NetworkMessage):
        """Handle deal start message or dummy reveal."""
        payload = message.payload

        # Check if this is a dummy reveal message
        if payload.get("reveal_dummy"):
            dummy_seat = Seat.from_char(payload["dummy_seat"])
            dummy_hand = Hand.from_dict(payload["dummy_hand"])
            self.dummy_revealed.emit(dummy_seat, dummy_hand)
            return

        board = BoardState.from_dict(payload)
        self._current_board = board
        self.deal_received.emit(board)

    def _handle_bid_made(self, message: NetworkMessage):
        """Handle bid made message."""
        payload = message.payload
        bidder = payload.get("bidder")
        bid = Bid.from_dict(payload.get("bid", {}))
        self.remote_bid_received.emit(bidder, bid)

    def _handle_card_played(self, message: NetworkMessage):
        """Handle card played message."""
        payload = message.payload
        player = payload.get("player")
        card = Card.from_dict(payload.get("card", {}))
        self.remote_card_received.emit(player, card)

    def _handle_trick_complete(self, message: NetworkMessage):
        """Handle trick complete message."""
        payload = message.payload
        winner = payload.get("winner")
        declarer_tricks = payload.get("declarer_tricks", 0)
        defense_tricks = payload.get("defense_tricks", 0)
        self.trick_completed.emit(winner, declarer_tricks, defense_tricks)

    def _handle_board_complete(self, message: NetworkMessage):
        """Handle board complete message."""
        self.board_completed.emit(message.payload)

    def _handle_trick_clear(self, message: NetworkMessage):
        """Handle trick clear message (remote player clicked 'next card')."""
        logger.debug("Received trick clear from remote player")
        self.trick_clear_received.emit()

    def _handle_closed_room_ingested(self, message: NetworkMessage):
        """Host has manually replayed a deal in Q-Plus. Hand the deserialized
        BenBoardRun off to the UI so guests can attach it to their score
        sheet alongside the open-room run."""
        try:
            run_data = message.payload.get("board_run") or {}
            run = BenBoardRun.from_dict(run_data)
            self.closed_room_ingested.emit(run)
        except Exception as ex:
            logger.warning(f"Failed to deserialize closed-room run: {ex}")

    # Broadcasting methods (for server/client to send state)

    def _send_message(self, message: NetworkMessage) -> bool:
        """Send a message to the peer."""
        if self._role == NetworkRole.SERVER and self._server:
            return self._server.send_message(message)
        elif self._role == NetworkRole.CLIENT and self._client:
            return self._client.send_message(message)
        return False

    def broadcast_deal(self, board: BoardState):
        """
        Broadcast a new deal to every connected guest.

        The hands dict contains every seat's full hand; the server's
        _personalize hook strips it down to just the recipient's own seat
        before each socket write, so a guest at East never receives the
        N/S/W cards.
        """
        if self._role != NetworkRole.SERVER:
            logger.warning("Only server can broadcast deals")
            return

        self._current_board = board

        hands_dict = {
            seat.to_char(): board.hands[seat].to_dict(hidden=False)
            for seat in board.hands
        }
        # DIAGNOSTIC: log which guests are connected and how many cards
        # are about to be packed into the broadcast (pre-personalization).
        try:
            seats = list(self._server.client_seats) if self._server else []
            print(f"[host broadcast_deal] connected guest seats: "
                  f"{[s.to_char() for s in seats]}", flush=True)
            for k, v in hands_dict.items():
                n = len(v.get('cards') or [])
                print(f"  pre-mask hand[{k}]: {n} cards", flush=True)
        except Exception as ex:
            print(f"[host broadcast_deal] diagnostic failed: {ex}", flush=True)

        message = make_deal_start(
            board_number=board.board_number,
            dealer=board.dealer.to_char(),
            vulnerability=board.vulnerability.value,
            hands=hands_dict,
            sequence=self._server.get_next_sequence()
        )
        self._send_message(message)

    def broadcast_dummy_reveal(self, dummy_seat: Seat, dummy_hand: 'Hand'):
        """Broadcast dummy's hand when play starts."""
        from .protocol import NetworkMessage, MessageType
        message = NetworkMessage(
            type=MessageType.DEAL_START,  # Reuse deal message to send hand
            payload={
                "reveal_dummy": True,
                "dummy_seat": dummy_seat.to_char(),
                "dummy_hand": dummy_hand.to_dict(hidden=False),
            }
        )
        self._send_message(message)

    def broadcast_bid(self, seat: Seat, bid: Bid):
        """Broadcast a bid to the remote player."""
        message = make_bid_made(
            bidder=seat.to_char(),
            bid=bid.to_dict(),
        )
        self._send_message(message)

    def broadcast_card(self, seat: Seat, card: Card):
        """Broadcast a card play to the remote player."""
        message = make_card_played(
            player=seat.to_char(),
            card=card.to_dict(),
        )
        self._send_message(message)

    def broadcast_trick_complete(self, winner: Seat, declarer_tricks: int, defense_tricks: int):
        """Broadcast trick completion to the remote player."""
        message = make_trick_complete(
            winner=winner.to_char(),
            trick_cards=[],  # Could include trick cards if needed
            declarer_tricks=declarer_tricks,
            defense_tricks=defense_tricks,
        )
        self._send_message(message)

    def broadcast_board_complete(self, contract_dict: dict, declarer_tricks: int,
                                  ns_score: int, ew_score: int):
        """Broadcast board completion to the remote player."""
        message = make_board_complete(
            contract=contract_dict,
            declarer_tricks=declarer_tricks,
            ns_score=ns_score,
            ew_score=ew_score,
        )
        self._send_message(message)

    def broadcast_trick_clear(self):
        """Broadcast trick clear to the remote player (sync 'next card')."""
        message = make_trick_clear()
        self._send_message(message)

    def broadcast_closed_room_ingested(self, run: BenBoardRun):
        """Host → guests: a Q-Plus closed room has been ingested. Guests
        attach the run to their score sheet so they can compare too."""
        if self._role != NetworkRole.SERVER:
            logger.warning("Only server can broadcast closed_room_ingested")
            return
        message = make_closed_room_ingested(
            run.to_dict(),
            sequence=self._server.get_next_sequence(),
        )
        self._send_message(message)

    def get_player_type_for_seat(self, seat: Seat) -> PlayerType:
        """
        Get the appropriate player type for a seat in network mode.

        Returns:
            HUMAN for local seats, EXTERNAL for remote player seats, COMPUTER for AI
        """
        if not self.is_active:
            return PlayerType.COMPUTER

        if self.is_my_seat(seat):
            return PlayerType.HUMAN
        elif self.is_remote_seat(seat):
            return PlayerType.EXTERNAL
        else:
            return PlayerType.COMPUTER

    def configure_players_for_network(self, players: dict) -> dict:
        """
        Configure player types for network play.

        Args:
            players: Dict[Seat, Player] - existing player configuration

        Returns:
            Modified player configuration with network-appropriate types
        """
        if not self.is_active:
            return players

        for seat, player in players.items():
            player.player_type = self.get_player_type_for_seat(seat)

        return players
