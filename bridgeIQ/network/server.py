"""
Bridge network server using Qt TCP.
"""

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

from .protocol import (
    NetworkMessage, MessageType,
    make_connect_accept, make_connect_reject, make_disconnect,
    make_heartbeat, make_heartbeat_ack, make_seat_list, make_game_start,
    make_seat_request,
    DEFAULT_PORT, HEARTBEAT_INTERVAL_MS, HEARTBEAT_TIMEOUT_MS,
)
from backend.models import Seat

from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BridgeServer(QObject):
    """
    TCP server for hosting a LAN bridge game.

    Host picks their own seat. Up to 3 guests may join, each choosing any
    seat still free.  When a guest requests an occupied seat the server
    replies with CONNECT_REJECT containing the list of seats that are
    still free, so the guest dialog can re-prompt.
    """

    # Signals
    client_connected = pyqtSignal(str, str)  # client_name, role (legacy)
    client_disconnected = pyqtSignal()
    seat_swap = pyqtSignal(str, str, str)    # seat_char, old_label, new_label
    seat_map_changed = pyqtSignal(dict)      # {seat_char: name_or_None}
    message_received = pyqtSignal(object)  # NetworkMessage
    error_occurred = pyqtSignal(str)  # error message
    server_started = pyqtSignal(int)  # port number
    server_stopped = pyqtSignal()

    def __init__(self, parent=None, app_version: str = "", password: str = ""):
        super().__init__(parent)

        # Build identifier (typically a short git commit hash). When set,
        # the server rejects clients whose CONNECT_REQUEST carries a
        # different (or empty) app_version with code "version_mismatch".
        self.app_version: str = (app_version or "").strip()
        from .protocol import password_hash
        self.pw: str = password_hash(password)   # backlog 55: "" = an open table

        self._server: Optional[QTcpServer] = None
        self._buffer = b""

        # Map of seat -> socket for all connected guests (host is not here).
        self._clients: dict = {}  # Seat -> QTcpSocket
        self._client_names: dict = {}  # Seat -> str
        # Per-socket receive buffers
        self._socket_buffers: dict = {}  # id(socket) -> bytes
        # Sockets that haven't completed handshake yet
        self._pending_sockets: list = []
        # Lobby-observer sockets: have sent a CONNECT_REQUEST with no seat
        # and are waiting on the seat board to send SEAT_REQUEST. Maps
        # id(sock) -> player name so SEAT_LIST can label them as "Joining".
        self._lobby_pending: dict = {}

        # Server configuration
        self._server_name = "Host"
        self._server_seat: Optional[Seat] = None
        # Legacy single-client fields kept for backward compatibility with
        # callers that still read them. They always point to the *first*
        # connected guest.
        self._client_name = ""
        self._client_seat: Optional[Seat] = None
        self._client_partner_seat: Optional[Seat] = None
        self._client_role = "partner"

        # Heartbeat
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._send_heartbeat)
        self._last_heartbeat_received = 0
        self._heartbeat_check_timer = QTimer(self)
        self._heartbeat_check_timer.timeout.connect(self._check_heartbeat_timeout)

        # Message sequence number
        self._sequence = 0

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._server is not None and self._server.isListening()

    @property
    def is_client_connected(self) -> bool:
        """Check if at least one client is connected."""
        return any(
            s is not None and s.state() == QTcpSocket.SocketState.ConnectedState
            for s in self._clients.values()
        )

    @property
    def num_clients(self) -> int:
        """Number of currently connected guests (does not include host)."""
        return len(self._clients)

    def occupied_seats(self) -> list:
        """Return list of Seat objects that are currently occupied (host + guests)."""
        occ = []
        if self._server_seat is not None:
            occ.append(self._server_seat)
        occ.extend(self._clients.keys())
        return occ

    def free_seats(self) -> list:
        """Return list of Seat objects that are currently free."""
        occupied = set(self.occupied_seats())
        return [s for s in Seat if s not in occupied]

    def seat_map(self) -> dict:
        """Return the current seat occupancy as {seat_char: name_or_None}.

        Used to broadcast lobby state to every connected client and to the
        host's own lobby UI. Free seats map to None; occupied seats map to
        the player name (host name for the host's own seat).
        """
        out = {s.to_char(): None for s in Seat}
        if self._server_seat is not None:
            out[self._server_seat.to_char()] = self._server_name
        for seat, name in self._client_names.items():
            out[seat.to_char()] = name
        return out

    def broadcast_seat_list(self):
        """Send the current seat map to every connected guest."""
        if not self._clients:
            return
        host_char = self._server_seat.to_char() if self._server_seat else ""
        msg = make_seat_list(self.seat_map(), host_seat=host_char)
        self.send_message(msg)

    def broadcast_game_start(self):
        """Tell every guest the host is starting the game (lobby is closing)."""
        self.send_message(make_game_start())

    @property
    def server_seat(self) -> Optional[Seat]:
        """Get the server player's seat."""
        return self._server_seat

    @property
    def client_seat(self) -> Optional[Seat]:
        """Get the client player's primary seat."""
        return self._client_seat

    @property
    def client_seats(self) -> list:
        """All seats currently held by connected guests (host not included)."""
        return list(self._clients.keys())

    @property
    def client_partner_seat(self) -> Optional[Seat]:
        """Get the client player's partner seat."""
        return self._client_partner_seat

    @property
    def client_role(self) -> str:
        """Get the client's role ('partner' or 'opponent')."""
        return self._client_role

    @property
    def server_name(self) -> str:
        """Get server player name."""
        return self._server_name

    @property
    def client_name(self) -> str:
        """Get client player name."""
        return self._client_name

    def start(self, port: int = DEFAULT_PORT, name: str = "Host", seat: Seat = Seat.SOUTH) -> bool:
        """
        Start the server listening on the specified port.

        Args:
            port: Port to listen on
            name: Server player name
            seat: Server player's seat

        Returns:
            True if server started successfully
        """
        if self._server is not None:
            self.stop()

        self._server_name = name
        self._server_seat = seat
        # Client seat will be assigned when they connect and choose their role

        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)

        if not self._server.listen(QHostAddress.SpecialAddress.Any, port):
            error = self._server.errorString()
            logger.error(f"Failed to start server: {error}")
            self.error_occurred.emit(f"Failed to start server: {error}")
            self._server = None
            return False

        logger.info(f"Server started on port {port}, seat: {seat.to_char()}")
        self.server_started.emit(port)
        return True

    def stop(self):
        """Stop the server and disconnect all clients."""
        # Stop heartbeat
        self._heartbeat_timer.stop()
        self._heartbeat_check_timer.stop()

        # Disconnect each guest
        for seat, sock in list(self._clients.items()):
            try:
                if sock.state() == QTcpSocket.SocketState.ConnectedState:
                    sock.write(make_disconnect("Server shutting down").to_bytes())
                    sock.disconnectFromHost()
                sock.deleteLater()
            except Exception:
                pass
        self._clients.clear()
        self._client_names.clear()
        self._socket_buffers.clear()

        # Reject any still-pending sockets
        for sock in list(self._pending_sockets):
            try:
                sock.disconnectFromHost()
                sock.deleteLater()
            except Exception:
                pass
        self._pending_sockets.clear()

        # Stop server
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None

        self._buffer = b""
        self._sequence = 0
        # Reset legacy fields
        self._client_name = ""
        self._client_seat = None
        self._client_partner_seat = None
        logger.info("Server stopped")
        self.server_stopped.emit()

    def send_message(self, message: NetworkMessage, target_seat: Optional[Seat] = None) -> bool:
        """
        Send a message to connected clients.

        Args:
            message: Message to send
            target_seat: If given, send only to that guest. Otherwise broadcast
                to every connected guest.

        Returns:
            True if at least one client successfully received the message.
        """
        if not self.is_client_connected:
            return False

        # Update sequence number for game messages (heartbeats keep their own flow)
        if message.type not in (MessageType.HEARTBEAT, MessageType.HEARTBEAT_ACK):
            self._sequence += 1
            message.sequence = self._sequence

        if target_seat is not None and target_seat in self._clients:
            target_pairs = [(target_seat, self._clients[target_seat])]
        else:
            target_pairs = list(self._clients.items())
        ok = False
        for seat, sock in target_pairs:
            try:
                if sock.state() != QTcpSocket.SocketState.ConnectedState:
                    continue
                # _personalize is currently a passthrough — it gives us a
                # seam to rewrite per-recipient fields later (e.g. mask
                # opponents' card data) without touching every send site.
                out = self._personalize(message, seat)
                if sock.write(out.to_bytes()) != -1:
                    ok = True
            except Exception as ex:
                logger.error(f"Failed to send to {sock}: {ex}")
        return ok

    def _personalize(self, message: NetworkMessage,
                     recipient_seat: Seat) -> NetworkMessage:
        """Per-recipient message rewriting. Used to hide opponent hands so
        a guest at East cannot see North/South/West cards in the deal
        broadcast.

        DEAL_START is the only message that carries private state today.
        Each recipient's own seat (and dummy after the opening lead, which
        is handled by a separate broadcast_dummy_reveal) is unmasked; every
        other seat in the payload is replaced with a hidden stub.
        """
        if message.type != MessageType.DEAL_START:
            return message
        # The dummy reveal message is also DEAL_START with reveal_dummy set.
        # Don't touch those — the dummy hand should be visible to everyone.
        if message.payload.get("reveal_dummy"):
            return message
        hands = message.payload.get("hands") or {}
        # Seat is an IntEnum where NORTH == 0, so `if recipient_seat` is False
        # for North and the recipient's own hand gets masked along with the
        # opponents'. Use an explicit None check.
        my_char = recipient_seat.to_char() if recipient_seat is not None else ""
        masked = {}
        for seat_char, hand_data in hands.items():
            if seat_char == my_char:
                masked[seat_char] = hand_data
            else:
                # Strip card contents but preserve count so the UI can lay
                # out face-down cards.
                count = 13
                if isinstance(hand_data, dict) and "cards" in hand_data:
                    try:
                        count = len(hand_data["cards"])
                    except Exception:
                        count = 13
                masked[seat_char] = {"hidden": True, "count": count}
        new_payload = dict(message.payload)
        new_payload["hands"] = masked
        return NetworkMessage(
            type=message.type, payload=new_payload, sequence=message.sequence
        )

    def get_next_sequence(self) -> int:
        """Get the next sequence number."""
        return self._sequence + 1

    def _on_new_connection(self):
        """Handle a new pending socket. Seat is assigned on CONNECT_REQUEST."""
        sock = self._server.nextPendingConnection()

        # Already have 3 guests? Reject immediately. Do NOT deleteLater here —
        # that would race the pending write. The disconnected signal will
        # schedule cleanup once the socket closes gracefully.
        if len(self._clients) >= 3:
            try:
                sock.write(make_connect_reject(
                    "Table is full (host + 3 guests)",
                    free_seats=[s.to_char() for s in self.free_seats()],
                ).to_bytes())
                sock.flush()
                sock.disconnectFromHost()
            except Exception:
                pass
            logger.info("Rejected connection - table full")
            return

        self._pending_sockets.append(sock)
        self._socket_buffers[id(sock)] = b""
        sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
        sock.disconnected.connect(lambda s=sock: self._on_client_disconnected(s))
        sock.errorOccurred.connect(lambda _err, s=sock: self._on_socket_error(s))

        try:
            logger.info(f"Incoming connection from {sock.peerAddress().toString()}")
        except Exception:
            pass

    def _on_ready_read(self, sock: QTcpSocket):
        """Handle incoming data from a specific client socket."""
        if sock is None:
            return
        buf = self._socket_buffers.get(id(sock), b"") + bytes(sock.readAll())
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            if line:
                try:
                    message = NetworkMessage.from_bytes(line)
                    self._handle_message(message, sock)
                except Exception as e:
                    logger.error(f"Failed to parse message: {e}")
        self._socket_buffers[id(sock)] = buf

    def _handle_message(self, message: NetworkMessage, sock: QTcpSocket):
        """Handle a received message from a specific socket."""
        logger.debug(f"Server received: {message.type.value}")

        if message.type == MessageType.CONNECT_REQUEST:
            self._handle_connect_request(message, sock)
        elif message.type == MessageType.SEAT_REQUEST:
            self._handle_seat_request(message, sock)
        elif message.type == MessageType.DISCONNECT:
            self._handle_disconnect(message, sock)
        elif message.type == MessageType.HEARTBEAT:
            # Reply ONLY to the socket that asked
            try:
                sock.write(make_heartbeat_ack().to_bytes())
            except Exception:
                pass
        elif message.type == MessageType.HEARTBEAT_ACK:
            self._last_heartbeat_received = self._get_timestamp()
        else:
            # Relay this guest's game message to every other guest, then let
            # the host's app handle it locally. Without this relay a 2nd or
            # 3rd guest would never see the 1st guest's bids or card plays.
            self._relay_to_others(message, sock)
            # Forward other messages to the application
            self.message_received.emit(message)

    def _relay_to_others(self, message: NetworkMessage, source_sock: QTcpSocket):
        """Forward a game message from one guest to every other connected guest."""
        for seat, s in list(self._clients.items()):
            if s is source_sock:
                continue
            try:
                if s.state() == QTcpSocket.SocketState.ConnectedState:
                    s.write(message.to_bytes())
            except Exception as ex:
                logger.error(f"Failed to relay to {seat}: {ex}")

    def _seat_of_socket(self, sock: QTcpSocket) -> Optional[Seat]:
        for seat, s in self._clients.items():
            if s is sock:
                return seat
        return None

    def _handle_connect_request(self, message: NetworkMessage, sock: QTcpSocket):
        """Handle a client connection request. Assign the requested seat if free."""
        payload = message.payload
        player_name = payload.get("player_name", "Guest")
        requested = (payload.get("requested_seat") or "").upper()
        legacy_role = payload.get("role", "partner")
        client_version = (payload.get("app_version") or "").strip()

        # Reject mismatched builds early so guests don't desync over wire-
        # format changes. Empty host version disables the check.
        if self.app_version and client_version != self.app_version:
            shown_client = client_version or "unknown"
            try:
                sock.write(make_connect_reject(
                    f"Version mismatch: host is on '{self.app_version}', "
                    f"you are on '{shown_client}'. Update both sides to "
                    "the same bridgeIQ revision and reconnect.",
                    code="version_mismatch",
                ).to_bytes())
                sock.flush()
                sock.disconnectFromHost()
            except Exception:
                pass
            logger.info(
                f"Rejected '{player_name}' — version mismatch "
                f"(host {self.app_version!r} vs client {client_version!r})")
            return

        # Backlog 55: a table password (a host without one is an open table).
        client_pw = str(payload.get("pw") or "")
        if self.pw and client_pw != self.pw:
            try:
                sock.write(make_connect_reject(
                    "Wrong password for this table." if client_pw else "This table needs a password.",
                    code="wrong_password",
                ).to_bytes())
                sock.flush()
                sock.disconnectFromHost()
            except Exception:
                pass
            logger.info(f"Rejected '{player_name}' -- wrong password")
            return

        # Empty `requested_seat` is the post-connect-lobby flow: the guest
        # connects first, the server pushes them the live SEAT_LIST, and
        # the guest then picks an available seat (SEAT_REQUEST). Hold them
        # in the lobby-pending pool until that arrives.
        if requested not in ("N", "E", "S", "W"):
            self._lobby_pending[id(sock)] = player_name
            try:
                host_char = self._server_seat.to_char() if self._server_seat else ""
                sock.write(make_seat_list(self.seat_map(), host_seat=host_char).to_bytes())
            except Exception as ex:
                logger.error(f"Failed to send lobby SEAT_LIST: {ex}")
            logger.info(f"'{player_name}' joined lobby, waiting for SEAT_REQUEST")
            return

        # Explicit seat path: validate and seat the guest immediately.
        try:
            target_seat = Seat.from_char(requested)
        except Exception:
            target_seat = None

        self._seat_client(sock, player_name, target_seat)

    def _seat_client(self, sock: QTcpSocket, player_name: str,
                     target_seat: Optional[Seat]) -> bool:
        """Move `sock` from pending/lobby state into _clients[target_seat].

        Sends CONNECT_ACCEPT on success, or CONNECT_REJECT (with code
        "seat_taken" so the client knows to stay connected and let the
        user pick a different seat). Used by both the legacy explicit-
        seat path and the new SEAT_REQUEST flow.
        """
        occupied = set(self.occupied_seats())
        if target_seat is None or target_seat in occupied:
            free_chars = [s.to_char() for s in self.free_seats()]
            seat_label = target_seat.to_char() if target_seat else "?"
            try:
                sock.write(make_connect_reject(
                    f"Seat {seat_label} is already taken",
                    free_seats=free_chars,
                    code="seat_taken",
                ).to_bytes())
                sock.flush()
            except Exception:
                pass
            logger.info(f"Rejected '{player_name}' -> seat {seat_label} already taken")
            return False

        if sock in self._pending_sockets:
            self._pending_sockets.remove(sock)
        self._lobby_pending.pop(id(sock), None)
        self._clients[target_seat] = sock
        self._client_names[target_seat] = player_name

        # Update legacy single-client fields
        self._client_name = player_name
        self._client_seat = target_seat
        self._client_partner_seat = target_seat.partner()
        if target_seat == self._server_seat.partner():
            self._client_role = "partner"
        else:
            self._client_role = "opponent"

        try:
            sock.write(make_connect_accept(
                server_name=self._server_name,
                server_seat=self._server_seat.to_char(),
                client_seat=target_seat.to_char(),
                client_partner_seat=target_seat.partner().to_char(),
                role=self._client_role,
            ).to_bytes())
        except Exception as ex:
            logger.error(f"Failed to send accept to new client: {ex}")
            return False

        self._last_heartbeat_received = self._get_timestamp()
        if not self._heartbeat_timer.isActive():
            self._heartbeat_timer.start(HEARTBEAT_INTERVAL_MS)
            self._heartbeat_check_timer.start(HEARTBEAT_INTERVAL_MS)

        logger.info(f"Client '{player_name}' seated at {target_seat.to_char()} ({self._client_role})")
        try:
            self.seat_swap.emit(target_seat.to_char(), "AI", player_name)
        except Exception:
            pass
        self.client_connected.emit(player_name, self._client_role)
        self.broadcast_seat_list()
        self.seat_map_changed.emit(self.seat_map())
        return True

    def _handle_seat_request(self, message: NetworkMessage, sock: QTcpSocket):
        """Lobby-pending guest is claiming a seat from the live SEAT_LIST."""
        player_name = self._lobby_pending.get(id(sock))
        if player_name is None:
            # Unknown socket — treat as protocol violation, ignore.
            logger.warning("SEAT_REQUEST from unknown socket, ignoring")
            return
        requested = (message.payload.get("requested_seat") or "").upper()
        if requested not in ("N", "E", "S", "W"):
            try:
                free_chars = [s.to_char() for s in self.free_seats()]
                sock.write(make_connect_reject(
                    "Invalid seat", free_seats=free_chars,
                ).to_bytes())
            except Exception:
                pass
            return
        try:
            target_seat = Seat.from_char(requested)
        except Exception:
            target_seat = None
        # If rejected the socket stays in lobby_pending so the user can
        # click another seat. Don't disconnect on a soft seat-taken error.
        self._seat_client(sock, player_name, target_seat)

    def _handle_disconnect(self, message: NetworkMessage, sock: QTcpSocket):
        """Handle client disconnect message."""
        reason = message.payload.get("reason", "")
        seat = self._seat_of_socket(sock)
        logger.info(f"Client ({seat}) disconnected: {reason}")
        self._cleanup_client(sock)

    def _on_client_disconnected(self, sock: QTcpSocket):
        """Handle client socket disconnection."""
        logger.info("Client socket disconnected")
        self._cleanup_client(sock)

    def _on_socket_error(self, sock: QTcpSocket):
        """Handle socket error on a specific client socket."""
        try:
            error_str = sock.errorString()
        except Exception:
            error_str = "unknown"
        print(f"[server sock_err] t={self._get_timestamp()} err={error_str!r}",
              flush=True)
        logger.error(f"Socket error: {error_str}")
        self.error_occurred.emit(f"Connection error: {error_str}")

    def _cleanup_client(self, sock: Optional[QTcpSocket] = None):
        """Clean up state for a specific client socket (or for all on None)."""
        if sock is None:
            targets = list(self._clients.values()) + list(self._pending_sockets)
        else:
            targets = [sock]

        for s in targets:
            seat = self._seat_of_socket(s)
            if seat is not None:
                old_name = self._client_names.get(seat, "Guest")
                self._clients.pop(seat, None)
                self._client_names.pop(seat, None)
                # Emit the swap so the controller can log the AI takeover.
                try:
                    self.seat_swap.emit(seat.to_char(), old_name, "AI")
                except Exception:
                    pass
            if s in self._pending_sockets:
                self._pending_sockets.remove(s)
            self._lobby_pending.pop(id(s), None)
            self._socket_buffers.pop(id(s), None)
            try:
                s.deleteLater()
            except Exception:
                pass

        if not self._clients:
            self._heartbeat_timer.stop()
            self._heartbeat_check_timer.stop()
            self._client_name = ""
            self._client_seat = None
            self._client_partner_seat = None

        self.client_disconnected.emit()
        # Refresh lobby state for any guests still connected, so a guest's
        # seat-picker reflects the seat that just freed up.
        try:
            self.broadcast_seat_list()
            self.seat_map_changed.emit(self.seat_map())
        except Exception:
            pass

    def _send_heartbeat(self):
        """Send heartbeat to every connected client."""
        if self.is_client_connected:
            print(f"[server hb] send t={self._get_timestamp()}", flush=True)
            self.send_message(make_heartbeat())

    def _check_heartbeat_timeout(self):
        """Check if heartbeat has timed out."""
        if not self.is_client_connected:
            return

        elapsed = self._get_timestamp() - self._last_heartbeat_received
        print(f"[server hb] check elapsed={elapsed}ms timeout={HEARTBEAT_TIMEOUT_MS}",
              flush=True)
        if elapsed > HEARTBEAT_TIMEOUT_MS:
            logger.warning("Client heartbeat timeout")
            self.error_occurred.emit("Connection lost: heartbeat timeout")
            self._cleanup_client()

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        from PyQt6.QtCore import QDateTime
        return QDateTime.currentMSecsSinceEpoch()
