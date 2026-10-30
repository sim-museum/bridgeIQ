"""
Network protocol definitions for LAN bridge play.

Messages are JSON objects with newline delimiter:
{"type": "bid_made", "payload": {...}, "sequence": 42}
"""

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional


class MessageType(Enum):
    """Network message types for bridge protocol."""
    # Connection management
    CONNECT_REQUEST = "connect_request"
    CONNECT_ACCEPT = "connect_accept"
    CONNECT_REJECT = "connect_reject"
    DISCONNECT = "disconnect"

    # Keep-alive
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"

    # Lobby
    SEAT_LIST = "seat_list"
    SEAT_REQUEST = "seat_request"
    GAME_START = "game_start"

    # Game state
    DEAL_START = "deal_start"

    # Bidding
    BID_MADE = "bid_made"

    # Card play
    CARD_PLAYED = "card_played"
    TRICK_COMPLETE = "trick_complete"
    TRICK_CLEAR = "trick_clear"  # Signal to clear trick area (sync "next card")
    BOARD_COMPLETE = "board_complete"

    # Closed-room ingest: host has manually replayed a deal in Q-Plus and
    # is pushing the resulting BenBoardRun to all connected guests so they
    # can see the comparison row in their own score sheet.
    CLOSED_ROOM_INGESTED = "closed_room_ingested"

    # Sync/recovery
    STATE_REQUEST = "state_request"
    STATE_RESPONSE = "state_response"

    # Chat/misc
    CHAT_MESSAGE = "chat_message"


class NetworkRole(Enum):
    """Role in network game."""
    SERVER = auto()
    CLIENT = auto()


@dataclass
class NetworkMessage:
    """A network message with type, payload, and sequence number."""
    type: MessageType
    payload: Dict[str, Any] = field(default_factory=dict)
    sequence: int = 0

    def to_json(self) -> str:
        """Serialize to JSON string with newline delimiter."""
        data = {
            "type": self.type.value,
            "payload": self.payload,
            "sequence": self.sequence,
        }
        return json.dumps(data) + "\n"

    def to_bytes(self) -> bytes:
        """Serialize to bytes for network transmission."""
        return self.to_json().encode('utf-8')

    @classmethod
    def from_json(cls, json_str: str) -> 'NetworkMessage':
        """Deserialize from JSON string."""
        data = json.loads(json_str.strip())
        return cls(
            type=MessageType(data["type"]),
            payload=data.get("payload", {}),
            sequence=data.get("sequence", 0),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> 'NetworkMessage':
        """Deserialize from bytes."""
        return cls.from_json(data.decode('utf-8'))


# Connection protocol messages

def password_hash(password: str) -> str:
    """Backlog 55: the table password travels as a SHA-256 hash, "" for none. A host with a password refuses a guest
    whose hash differs; a host without one is an open table -- the same rule as the other Serious Games Week games."""
    import hashlib
    p = (password or "").strip()
    return hashlib.sha256(p.encode()).hexdigest() if p else ""


def make_connect_request(player_name: str, requested_seat: str = "",
                          role: str = "partner",
                          app_version: str = "", pw: str = "") -> NetworkMessage:
    """Create a connection request message.

    Args:
        player_name: Name of the connecting player.
        requested_seat: Seat character the guest wants to play (N/E/S/W).
            Empty string falls back to legacy role-based assignment.
        role: Legacy. Only used if requested_seat is empty.
        app_version: Build identifier (typically a short git commit hash)
            so the host can reject mismatched clients with code
            "version_mismatch" in the rejection payload.
    """
    return NetworkMessage(
        type=MessageType.CONNECT_REQUEST,
        payload={
            "player_name": player_name,
            "requested_seat": requested_seat,
            "role": role,
            "app_version": app_version,
            "pw": pw,
        }
    )


def make_connect_accept(
    server_name: str,
    server_seat: str,
    client_seat: str,
    client_partner_seat: str,
    role: str = "partner"
) -> NetworkMessage:
    """Create a connection acceptance message with seat assignments.

    Args:
        server_name: Host player's name
        server_seat: Host player's primary seat (e.g., "S")
        client_seat: Client player's primary seat (e.g., "N" for partner, "E" for opponent)
        client_partner_seat: Client player's partner seat
        role: "partner" or "opponent" - echoed back for confirmation
    """
    return NetworkMessage(
        type=MessageType.CONNECT_ACCEPT,
        payload={
            "server_name": server_name,
            "server_seat": server_seat,
            "client_seat": client_seat,
            "client_partner_seat": client_partner_seat,
            "role": role,
        }
    )


def make_connect_reject(reason: str, free_seats: Optional[list] = None,
                         code: str = "") -> NetworkMessage:
    """Create a connection rejection message.

    Args:
        reason: Human-readable reason string.
        free_seats: Optional list of seat chars (N/E/S/W) still available,
            so the client can re-prompt with just the free seats.
        code: Optional machine-readable tag, e.g. "version_mismatch",
            "seat_taken", "table_full". Empty string for unspecified.
    """
    payload = {"reason": reason}
    if free_seats is not None:
        payload["free_seats"] = list(free_seats)
    if code:
        payload["code"] = code
    return NetworkMessage(
        type=MessageType.CONNECT_REJECT,
        payload=payload,
    )


def make_seat_list(seats: Dict[str, Optional[str]],
                    host_seat: str = "") -> NetworkMessage:
    """Broadcast the live seat occupancy to all connected clients.

    Args:
        seats: Dict mapping seat char (N/E/S/W) to player name or None if free.
        host_seat: Seat char of the host (so clients can flag who is host).
    """
    return NetworkMessage(
        type=MessageType.SEAT_LIST,
        payload={
            "seats": dict(seats),
            "host_seat": host_seat,
        },
    )


def make_seat_request(seat: str) -> NetworkMessage:
    """Guest claims a specific seat after seeing the live SEAT_LIST.

    Used by the post-connect seat picker: the guest first connects with
    an empty seat (lobby observer), receives the seat board, and then
    sends SEAT_REQUEST when they click an available seat.
    """
    return NetworkMessage(
        type=MessageType.SEAT_REQUEST,
        payload={"requested_seat": (seat or "").upper()},
    )


def make_game_start() -> NetworkMessage:
    """Tell all clients the lobby is closing and the deal is about to begin.

    Sent by the host once the lobby fills or the host clicks Start Game.
    """
    return NetworkMessage(type=MessageType.GAME_START)


def make_disconnect(reason: str = "") -> NetworkMessage:
    """Create a disconnect message."""
    return NetworkMessage(
        type=MessageType.DISCONNECT,
        payload={"reason": reason}
    )


def make_heartbeat() -> NetworkMessage:
    """Create a heartbeat message."""
    return NetworkMessage(type=MessageType.HEARTBEAT)


def make_heartbeat_ack() -> NetworkMessage:
    """Create a heartbeat acknowledgment."""
    return NetworkMessage(type=MessageType.HEARTBEAT_ACK)


# Game protocol messages

def make_deal_start(
    board_number: int,
    dealer: str,
    vulnerability: str,
    hands: Dict[str, Any],
    sequence: int = 0
) -> NetworkMessage:
    """Create a deal start message.

    Args:
        board_number: Board number
        dealer: Dealer seat character (N/E/S/W)
        vulnerability: Vulnerability string
        hands: Dict mapping seat chars to hand data (hidden hands have {"hidden": true, "count": 13})
        sequence: Message sequence number
    """
    return NetworkMessage(
        type=MessageType.DEAL_START,
        payload={
            "board_number": board_number,
            "dealer": dealer,
            "vulnerability": vulnerability,
            "hands": hands,
        },
        sequence=sequence
    )


def make_bid_made(
    bidder: str,
    bid: Dict[str, Any],
    sequence: int = 0
) -> NetworkMessage:
    """Create a bid made message.

    Args:
        bidder: Bidder seat character (N/E/S/W)
        bid: Serialized bid dict
        sequence: Message sequence number
    """
    return NetworkMessage(
        type=MessageType.BID_MADE,
        payload={
            "bidder": bidder,
            "bid": bid,
        },
        sequence=sequence
    )


def make_card_played(
    player: str,
    card: Dict[str, Any],
    sequence: int = 0
) -> NetworkMessage:
    """Create a card played message.

    Args:
        player: Player seat character (N/E/S/W)
        card: Serialized card dict
        sequence: Message sequence number
    """
    return NetworkMessage(
        type=MessageType.CARD_PLAYED,
        payload={
            "player": player,
            "card": card,
        },
        sequence=sequence
    )


def make_trick_complete(
    winner: str,
    trick_cards: list,
    declarer_tricks: int,
    defense_tricks: int,
    sequence: int = 0
) -> NetworkMessage:
    """Create a trick complete message."""
    return NetworkMessage(
        type=MessageType.TRICK_COMPLETE,
        payload={
            "winner": winner,
            "trick_cards": trick_cards,
            "declarer_tricks": declarer_tricks,
            "defense_tricks": defense_tricks,
        },
        sequence=sequence
    )


def make_trick_clear(sequence: int = 0) -> NetworkMessage:
    """Create a trick clear message (sync 'next card' between players)."""
    return NetworkMessage(
        type=MessageType.TRICK_CLEAR,
        payload={},
        sequence=sequence
    )


def make_board_complete(
    contract: Dict[str, Any],
    declarer_tricks: int,
    ns_score: int,
    ew_score: int,
    sequence: int = 0
) -> NetworkMessage:
    """Create a board complete message."""
    return NetworkMessage(
        type=MessageType.BOARD_COMPLETE,
        payload={
            "contract": contract,
            "declarer_tricks": declarer_tricks,
            "ns_score": ns_score,
            "ew_score": ew_score,
        },
        sequence=sequence
    )


def make_closed_room_ingested(board_run: Dict[str, Any], sequence: int = 0) -> NetworkMessage:
    """Host → guest: a Q-Plus closed room has been ingested.

    Payload is `BenBoardRun.to_dict()` for the closed-room run.
    """
    return NetworkMessage(
        type=MessageType.CLOSED_ROOM_INGESTED,
        payload={"board_run": board_run},
        sequence=sequence,
    )


def make_state_request() -> NetworkMessage:
    """Create a state sync request."""
    return NetworkMessage(type=MessageType.STATE_REQUEST)


def make_state_response(
    board_state: Dict[str, Any],
    sequence: int = 0
) -> NetworkMessage:
    """Create a state sync response with full board state."""
    return NetworkMessage(
        type=MessageType.STATE_RESPONSE,
        payload={"board_state": board_state},
        sequence=sequence
    )


def make_chat_message(sender: str, message: str) -> NetworkMessage:
    """Create a chat message."""
    return NetworkMessage(
        type=MessageType.CHAT_MESSAGE,
        payload={"sender": sender, "message": message}
    )


# Protocol constants
DEFAULT_PORT = 7777
HEARTBEAT_INTERVAL_MS = 5000  # 5 seconds
# 60s rather than 15s: BEN's first opening-lead inference can hold the
# GIL long enough on a fresh deal that a 15s window misclassifies a busy
# host as a dead one. 60s still flags genuinely dropped connections in a
# reasonable time.
HEARTBEAT_TIMEOUT_MS = 60000
CONNECTION_TIMEOUT_MS = 10000  # 10 seconds
