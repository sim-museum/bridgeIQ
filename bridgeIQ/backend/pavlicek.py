"""
Pavlicek Deal Number Algorithm

Implements Richard Pavlicek's method for uniquely numbering bridge deals.
Each of the ~5.36×10^28 possible bridge deals maps to a unique number.

The algorithm uses the combinatorial number system to encode/decode deals.
Total deals = C(52,13) × C(39,13) × C(26,13) × C(13,13) = 52!/(13!)^4

Reference: http://www.rpbridge.net/7z68.htm
"""

from math import comb
from typing import Dict, List, Tuple
from .models import Card, Hand, Seat, Suit, Rank


# Total number of possible bridge deals
TOTAL_DEALS = comb(52, 13) * comb(39, 13) * comb(26, 13) * comb(13, 13)


def card_to_index(card: Card) -> int:
    """Convert a card to its index (0-51).

    Card ordering: Spades A-2, Hearts A-2, Diamonds A-2, Clubs A-2
    where A=0, K=1, Q=2, ..., 2=12 within each suit.
    """
    return card.suit * 13 + card.rank


def index_to_card(idx: int) -> Card:
    """Convert an index (0-51) to a card."""
    return Card(Suit(idx // 13), Rank(idx % 13))


def _encode_hand(card_indices: List[int], available: List[int]) -> int:
    """Encode a hand using the combinatorial number system.

    Args:
        card_indices: Sorted list of card indices (0-51) in this hand
        available: Sorted list of available card indices

    Returns:
        A unique index for this hand given the available cards
    """
    # Map card indices to positions within available cards
    positions = []
    for card_idx in sorted(card_indices):
        pos = available.index(card_idx)
        positions.append(pos)

    # Compute combinatorial index
    # index = C(p[0], 1) + C(p[1], 2) + ... + C(p[12], 13)
    index = 0
    for i, pos in enumerate(sorted(positions)):
        index += comb(pos, i + 1)

    return index


def _decode_hand(index: int, available: List[int], num_cards: int = 13) -> List[int]:
    """Decode a hand index to card indices using combinatorial number system.

    Args:
        index: The combinatorial index
        available: Sorted list of available card indices
        num_cards: Number of cards to decode (default 13)

    Returns:
        List of card indices for this hand
    """
    n = len(available)
    positions = []

    # Work backwards from position k-1 down to 0
    remaining = index
    for k in range(num_cards, 0, -1):
        # Find the largest position p such that C(p, k) <= remaining
        for p in range(n - 1, k - 2, -1):
            c = comb(p, k)
            if c <= remaining:
                positions.append(p)
                remaining -= c
                break

    positions.reverse()

    # Map positions back to card indices
    return [available[p] for p in positions]


def deal_to_number(hands: Dict[Seat, Hand]) -> int:
    """Convert a bridge deal to its Pavlicek deal number.

    Args:
        hands: Dictionary mapping Seat to Hand

    Returns:
        The unique deal number (0 to TOTAL_DEALS-1)
    """
    # Get card indices for each hand
    n_cards = sorted([card_to_index(c) for c in hands[Seat.NORTH].cards])
    e_cards = sorted([card_to_index(c) for c in hands[Seat.EAST].cards])
    s_cards = sorted([card_to_index(c) for c in hands[Seat.SOUTH].cards])
    # West gets remaining cards (not needed for encoding, just verification)

    # Start with all 52 cards available
    available = list(range(52))

    # Encode North's hand
    n_index = _encode_hand(n_cards, available)

    # Remove North's cards from available
    for card in n_cards:
        available.remove(card)

    # Encode East's hand from remaining 39 cards
    e_index = _encode_hand(e_cards, available)

    # Remove East's cards from available
    for card in e_cards:
        available.remove(card)

    # Encode South's hand from remaining 26 cards
    s_index = _encode_hand(s_cards, available)

    # West gets the last 13 cards (only one way, so index = 0)

    # Combine indices into final deal number
    # deal = n_index * C(39,13) * C(26,13) + e_index * C(26,13) + s_index
    deal_number = (n_index * comb(39, 13) * comb(26, 13) +
                   e_index * comb(26, 13) +
                   s_index)

    return deal_number


def number_to_deal(deal_number: int) -> Dict[Seat, Hand]:
    """Convert a Pavlicek deal number to a bridge deal.

    Args:
        deal_number: The deal number (0 to TOTAL_DEALS-1)

    Returns:
        Dictionary mapping Seat to Hand
    """
    if deal_number < 0 or deal_number >= TOTAL_DEALS:
        raise ValueError(f"Deal number must be between 0 and {TOTAL_DEALS-1}")

    # Extract indices for each hand
    c26_13 = comb(26, 13)
    c39_13 = comb(39, 13)

    # South's index is deal_number % C(26,13)
    s_index = deal_number % c26_13
    remainder = deal_number // c26_13

    # East's index is remainder % C(39,13)
    e_index = remainder % c39_13

    # North's index is the rest
    n_index = remainder // c39_13

    # Decode hands
    available = list(range(52))

    # Decode North's hand
    n_cards = _decode_hand(n_index, available)
    for card in n_cards:
        available.remove(card)

    # Decode East's hand
    e_cards = _decode_hand(e_index, available)
    for card in e_cards:
        available.remove(card)

    # Decode South's hand
    s_cards = _decode_hand(s_index, available)
    for card in s_cards:
        available.remove(card)

    # West gets remaining cards
    w_cards = available

    # Convert to Hand objects
    hands = {
        Seat.NORTH: Hand([index_to_card(i) for i in n_cards]),
        Seat.EAST: Hand([index_to_card(i) for i in e_cards]),
        Seat.SOUTH: Hand([index_to_card(i) for i in s_cards]),
        Seat.WEST: Hand([index_to_card(i) for i in w_cards]),
    }

    return hands


def deal_number_to_hex(deal_number: int) -> str:
    """Convert deal number to a hex string for compact display.

    Returns a 24-character hex string (96 bits).
    """
    return f"{deal_number:024x}"


def hex_to_deal_number(hex_str: str) -> int:
    """Convert hex string back to deal number."""
    return int(hex_str, 16)


def format_deal_number(deal_number: int, separator: str = "-") -> str:
    """Format deal number as a readable hex string with separators.

    Example: "A1B2-C3D4-E5F6-7890-ABCD-EF12"
    """
    hex_str = deal_number_to_hex(deal_number)
    # Group into 4-character chunks
    chunks = [hex_str[i:i+4] for i in range(0, len(hex_str), 4)]
    return separator.join(chunks).upper()


def parse_deal_number(formatted: str) -> int:
    """Parse a formatted deal number string back to integer.

    Accepts formats like:
    - "A1B2-C3D4-E5F6-7890-ABCD-EF12" (hex)
    - "A1B2C3D4E5F67890ABCDEF12" (hex)
    - Base-72 encoded strings  (current canonical format)
    - Base-62 encoded strings  (legacy — older BDL logs)
    """
    # Remove separators
    cleaned = formatted.replace("-", "").replace(" ", "")

    # If the string contains any of the base-72-only specials, it must
    # be base-72 (those characters don't appear in hex or base-62).
    if any(ch in "!@#$%^&*()" for ch in cleaned):
        return base72_to_int(cleaned)

    # Try base-72 first.  If the result is a valid Pavlicek deal number,
    # that's almost certainly what the input encoded; otherwise fall
    # back to legacy base-62, then hex.
    try:
        n72 = base72_to_int(cleaned)
        if 0 <= n72 < TOTAL_DEALS:
            return n72
    except ValueError:
        pass

    # Legacy base-62 (older BDLs, deal IDs we wrote before the migration).
    try:
        n62 = base62_to_int(cleaned)
        if 0 <= n62 < TOTAL_DEALS:
            return n62
    except ValueError:
        pass

    # Hex fallback.
    try:
        return int(cleaned.lower(), 16)
    except ValueError:
        pass

    # Last resort: base-62 even if out of range, so the caller surfaces a
    # meaningful "out of range" error.
    return base62_to_int(cleaned)


# Base-62 encoding/decoding
# Character mapping: 0-25=A-Z, 26-51=a-z, 52-61=0-9

BASE62_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
BASE62_CHAR_TO_VAL = {c: i for i, c in enumerate(BASE62_CHARS)}


def int_to_base62(number: int) -> str:
    """Convert an integer to base-62 string.

    Character mapping:
    - 0-25: A-Z (uppercase)
    - 26-51: a-z (lowercase)
    - 52-61: 0-9 (digits)
    """
    if number == 0:
        return "A"

    result = []
    while number > 0:
        result.append(BASE62_CHARS[number % 62])
        number //= 62

    return "".join(reversed(result))


def base62_to_int(s: str) -> int:
    """Convert a base-62 string back to integer."""
    result = 0
    for char in s:
        if char not in BASE62_CHAR_TO_VAL:
            raise ValueError(f"Invalid base-62 character: {char}")
        result = result * 62 + BASE62_CHAR_TO_VAL[char]
    return result


def format_deal_base62(deal_number: int) -> str:
    """Legacy: format deal number as base-62 string.

    Prefer ``format_deal_base72`` / ``int_to_base72`` for new code —
    the project standardised on base-72 deal IDs.
    """
    return int_to_base62(deal_number)


def format_deal_base72(deal_number: int) -> str:
    """Format deal number as base-72 string (current canonical form).

    The base-72 alphabet is denser than base-62 and is what BDL "Deal"
    lines, the bridge harness, and every other deal-ID consumer in the
    project read and write.
    """
    return int_to_base72(deal_number)


# Base-72 encoding/decoding (10 more characters than base-62)
# Used for compact hand codes that include special characters
BASE72_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!@#$%^&*()"
BASE72_CHAR_TO_VAL = {c: i for i, c in enumerate(BASE72_CHARS)}


def int_to_base72(number: int) -> str:
    """Convert an integer to base-72 string.

    Uses a 72-character alphabet for more compact encoding than base-62.
    """
    if number == 0:
        return "0"

    result = []
    while number > 0:
        result.append(BASE72_CHARS[number % 72])
        number //= 72

    return "".join(reversed(result))


def base72_to_int(s: str) -> int:
    """Convert a base-72 string back to integer."""
    result = 0
    for char in s:
        if char not in BASE72_CHAR_TO_VAL:
            raise ValueError(f"Invalid base-72 character: {char}")
        result = result * 72 + BASE72_CHAR_TO_VAL[char]
    return result


def deal_to_hand_code(hands: Dict[Seat, Hand]) -> str:
    """Convert a deal to a compact hand code string.

    Encodes the Pavlicek deal number in base-72 for compact representation.

    Args:
        hands: Dictionary mapping Seat to Hand

    Returns:
        A compact base-72 encoded string representing the deal
    """
    deal_number = deal_to_number(hands)
    return int_to_base72(deal_number)


def hand_code_to_deal(code: str) -> Dict[Seat, Hand]:
    """Convert a compact hand code back to a deal.

    Args:
        code: Base-72 encoded deal string

    Returns:
        Dictionary mapping Seat to Hand
    """
    deal_number = base72_to_int(code)
    return number_to_deal(deal_number)


# Convenience functions for direct PBN conversion

def pbn_to_deal_number(pbn: str) -> int:
    """Convert a PBN deal string to its Pavlicek number.

    Args:
        pbn: Deal in PBN format, e.g., "N:AKQ.xxx.xxx.xxxx xxx.xxx.xxx.xxxx ..."

    Returns:
        The deal number
    """
    from .models import BoardState
    board = BoardState.from_pbn_deal(pbn)
    return deal_to_number(board.hands)


def deal_number_to_pbn(deal_number: int) -> str:
    """Convert a Pavlicek deal number to PBN format.

    Args:
        deal_number: The deal number

    Returns:
        Deal in PBN format starting with North
    """
    hands = number_to_deal(deal_number)
    hand_strs = []
    for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
        hand_strs.append(hands[seat].to_pbn())
    return f"N:{' '.join(hand_strs)}"


if __name__ == "__main__":
    # Test the implementation
    import random

    print(f"Total possible deals: {TOTAL_DEALS:,}")
    print(f"That's approximately {TOTAL_DEALS:.2e}")
    print()

    # Create a random deal
    cards = list(range(52))
    random.shuffle(cards)

    test_hands = {
        Seat.NORTH: Hand([index_to_card(i) for i in sorted(cards[0:13])]),
        Seat.EAST: Hand([index_to_card(i) for i in sorted(cards[13:26])]),
        Seat.SOUTH: Hand([index_to_card(i) for i in sorted(cards[26:39])]),
        Seat.WEST: Hand([index_to_card(i) for i in sorted(cards[39:52])]),
    }

    print("Original deal:")
    for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
        print(f"  {seat.name}: {test_hands[seat].to_pbn()}")

    # Encode
    deal_num = deal_to_number(test_hands)
    print(f"\nDeal number: {deal_num}")
    print(f"Formatted: {format_deal_number(deal_num)}")

    # Decode
    decoded_hands = number_to_deal(deal_num)
    print("\nDecoded deal:")
    for seat in [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]:
        print(f"  {seat.name}: {decoded_hands[seat].to_pbn()}")

    # Verify round-trip
    match = all(
        set(card_to_index(c) for c in test_hands[seat].cards) ==
        set(card_to_index(c) for c in decoded_hands[seat].cards)
        for seat in Seat if seat != Seat.WEST or True
    )
    print(f"\nRound-trip verification: {'PASSED' if match else 'FAILED'}")
