"""
Game Logger - Saves completed hands in BDL, PBN, and PPL format.

Claude critique is handled by MainWindow._show_claude_hand_analysis, which
writes into the BDL via _append_commentary_to_bdl below.
"""

import os
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

from backend.models import (
    BoardState, Card, Seat, Suit, Vulnerability, Contract, Trick, Rank, Bid, Hand
)
from backend.pavlicek import deal_to_number, format_deal_base72


_SUIT_CHARS = {Suit.SPADES: 'S', Suit.HEARTS: 'H',
               Suit.DIAMONDS: 'D', Suit.CLUBS: 'C'}
_SEAT_CHARS = {Seat.NORTH: 'N', Seat.EAST: 'E',
               Seat.SOUTH: 'S', Seat.WEST: 'W'}
_SEAT_NAMES = {Seat.NORTH: 'North', Seat.EAST: 'East',
               Seat.SOUTH: 'South', Seat.WEST: 'West'}
_VUL_NAMES = {Vulnerability.NONE: 'None',
              Vulnerability.NS: 'N/S',
              Vulnerability.EW: 'E/W',
              Vulnerability.BOTH: 'Both'}


def _suit_str(hand: Optional[Hand], suit: Suit) -> str:
    if hand is None:
        return '?'
    cards = [c for c in hand.cards if c.suit == suit]
    if not cards:
        return '-'
    cards.sort(key=lambda c: c.rank)  # ACE=0 sorts highest first
    return ' '.join(c.rank.to_char() for c in cards)


def _bid_to_bdl(bid: Bid) -> str:
    if bid.is_pass:
        return 'p'
    if bid.is_double:
        return 'X'
    if bid.is_redouble:
        return 'XX'
    suit_char = (_SUIT_CHARS.get(bid.suit, '?').lower()
                 if bid.suit is not None and bid.suit != Suit.NOTRUMP
                 else 'nt')
    return f"{bid.level}{suit_char}"


def _format_cards_block_for(hands: Dict[Seat, Optional[Hand]]) -> List[str]:
    """BDL Cards block. Hands map may contain None for hidden seats —
    those are emitted as '?' so Claude knows the cards are concealed.
    """
    n = hands.get(Seat.NORTH)
    e = hands.get(Seat.EAST)
    s = hands.get(Seat.SOUTH)
    w = hands.get(Seat.WEST)

    lines = []
    # North block
    lines.append(f"Cards        :                  {_suit_str(n, Suit.SPADES)} ")
    lines.append(f"             :                  {_suit_str(n, Suit.HEARTS)} ")
    lines.append(f"             :                  {_suit_str(n, Suit.DIAMONDS)} ")
    lines.append(f"             :                  {_suit_str(n, Suit.CLUBS)} ")
    # West / East side-by-side
    for suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
        ws = _suit_str(w, suit)
        es = _suit_str(e, suit)
        lines.append(f"             :   {ws:<20}          {es} ")
    # South
    lines.append(f"             :                  {_suit_str(s, Suit.SPADES)} ")
    lines.append(f"             :                  {_suit_str(s, Suit.HEARTS)} ")
    lines.append(f"             :                  {_suit_str(s, Suit.DIAMONDS)} ")
    lines.append(f"             :                  {_suit_str(s, Suit.CLUBS)} ")
    return lines


def _format_bidding_block(auction: List[Bid], dealer: Seat) -> List[str]:
    """BDL-style bidding block: column headers N/E/S/W and one row per
    auction round, with '-' filling the cells before dealer's first bid."""
    if not auction:
        return []
    lines = ["Bids         :   N    E    S    W"]
    bidder = dealer
    pos = bidder.value  # 0=N, 1=E, 2=S, 3=W
    row: List[str] = ['-' for _ in range(4)]
    # Pre-fill cells before dealer's first bid with '-' (already there)
    # Walk through bids, advancing seat after each.
    pre_pads = ['-'] * pos  # not used directly, but kept for clarity
    col = pos
    for i, bid in enumerate(auction):
        row[col] = _bid_to_bdl(bid)
        col += 1
        if col >= 4:
            lines.append("             :   "
                         + "    ".join(f"{c:<4}" for c in row).rstrip())
            row = ['-' for _ in range(4)]
            col = 0
    if any(c != '-' for c in row):
        lines.append("             :   "
                     + "    ".join(f"{c:<4}" for c in row).rstrip())
    return lines


def _format_tricks_block(tricks: List[Trick],
                         contract: Optional[Contract]) -> List[str]:
    if not tricks or contract is None:
        return []
    declarer = contract.declarer
    declarer_side_ns = declarer.is_ns() if declarer is not None else False
    lines: List[str] = []
    for i, trick in enumerate(tricks):
        leader = trick.leader
        winner = trick.winner
        declarer_won = (winner.is_ns() == declarer_side_ns
                        if winner is not None else False)
        card_strs = []
        for j, card in enumerate(trick.cards):
            cstr = f"{_SUIT_CHARS[card.suit].lower()}{card.rank.to_char()}"
            player_seat = Seat((leader.value + j) % 4)
            if winner is not None and player_seat == winner:
                cstr += "+" if declarer_won else "-"
            card_strs.append(f"{cstr:<4}")
        winning_suit = trick.cards[0].suit
        if winner is not None:
            winner_idx = (winner.value - leader.value) % 4
            if winner_idx < len(trick.cards):
                winning_suit = trick.cards[winner_idx].suit
        suit_indicator = _SUIT_CHARS.get(winning_suit, '')
        spacing = "     " if declarer_won else "         "
        prefix = "Tricks       : " if i == 0 else "             : "
        lines.append(
            f"{prefix}{i + 1:2d}  {_SEAT_CHARS[leader]}    "
            f"{' '.join(card_strs)}{spacing}{suit_indicator} "
        )
    return lines


def build_bdl_snapshot(board: BoardState,
                       original_hands: Optional[Dict[Seat, Hand]] = None,
                       viewer_seat: Optional[Seat] = None,
                       phase: Optional[str] = None,
                       dummy_visible_to_viewer: bool = True,
                       contract: Optional[Contract] = None,
                       result_summary: Optional[str] = None,
                       deal_id: str = "",
                       current_seat: Optional[Seat] = None,
                       include_footer: bool = True) -> str:
    """Render the current state of `board` as BDL text.

    Designed for Claude prompts: every seat is labelled in the same way
    BDL files are labelled (N/E/S/W headers in Bids, leader + winner
    markers in Tricks, fixed N/W-E/S layout in Cards), so the model has
    no chance to misattribute who did what.

    Args:
        board: the in-progress or finished board.
        original_hands: pre-play snapshot of all four hands. When given,
            the Cards block is built from these (so cards already played
            still show in the diagram). Falls back to board.hands.
        viewer_seat: if set, only this seat's hand (and dummy when
            visible) is shown — others are masked as '?'. None means
            "show every hand we have" (use for end-of-hand summaries).
        phase: 'bidding' or 'play' or 'finished'. Drives the trailing
            "Currently…" footer.
        dummy_visible_to_viewer: during play, whether dummy is face-up.
        contract: explicit contract override (defaults to board.contract).
        result_summary: free-form text appended at the bottom (e.g. the
            tricks-made / score line for end-of-hand prompts).
        deal_id: pavlicek/base-72 deal id, written into the Deal: line.
    """
    contract = contract or board.contract
    hands_for_cards = original_hands if original_hands else board.hands

    masked: Dict[Seat, Optional[Hand]] = {}
    # When the viewer is on the declaring side and dummy is visible,
    # they see BOTH declarer and dummy at the table — so the BDL
    # snapshot must reveal both, even when the viewer IS dummy.
    # Otherwise Claude (or any other reader) speculates about cards
    # it should be able to see. The earlier version only revealed
    # declarer.partner(), so when viewer was the dummy declarer's
    # hand stayed hidden and Claude invented evidence about it.
    viewer_on_declaring_side = (
        viewer_seat is not None
        and contract is not None
        and contract.declarer is not None
        and viewer_seat in (contract.declarer, contract.declarer.partner())
    )
    for seat in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
        h = hands_for_cards.get(seat) if hands_for_cards else None
        if viewer_seat is None:
            masked[seat] = h
        else:
            show = (seat == viewer_seat)
            if (not show
                    and contract is not None
                    and dummy_visible_to_viewer
                    and seat == contract.declarer.partner()):
                show = True
            if (not show
                    and contract is not None
                    and dummy_visible_to_viewer
                    and viewer_on_declaring_side
                    and seat == contract.declarer):
                show = True
            masked[seat] = h if show else None

    out: List[str] = []
    out.append("DOCTYPE: BDL 17.1 (live snapshot)")
    out.append("")
    if deal_id:
        out.append(f"Deal         :   {deal_id}")
    out.append(f"Deal-text    :   Board #{board.board_number}")
    out.append(f"Dealer       :   {_SEAT_NAMES[board.dealer]}")
    out.append(f"Vuln         :   {_VUL_NAMES.get(board.vulnerability, 'None')}  ")
    # PBN comment line — copy/paste-ready for bcalc's Distribution
    # field. Only emitted when we have full information (viewer_seat
    # is None) so a hint dialog can't accidentally leak hands the
    # human seat shouldn't see. We still emit when ONLY some hands
    # are known (e.g. mid-hand snapshot with hidden seats) by using
    # the 'xxx...' placeholder for unknown hands, which bcalc rejects
    # gracefully and which makes it obvious to the reader that the
    # cell wasn't filled in.
    if viewer_seat is None and hands_for_cards:
        pbn_parts: List[str] = []
        for seat in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
            h = hands_for_cards.get(seat)
            try:
                pbn_parts.append(h.to_pbn() if h else "-")
            except Exception:
                pbn_parts.append("-")
        if any(p and p != "-" for p in pbn_parts):
            out.append(
                "# PBN (paste into bcalc Distribution): "
                + "N:" + " ".join(pbn_parts)
            )
    out.extend(_format_cards_block_for(masked))
    out.append("")

    if board.auction:
        out.extend(_format_bidding_block(list(board.auction), board.dealer))
        out.append("")

    if contract is not None:
        decl = contract.declarer
        decl_str = _SEAT_NAMES[decl] if decl is not None else "?"
        suit_char = (_SUIT_CHARS[contract.suit].lower()
                     if contract.suit != Suit.NOTRUMP else 'nt')
        dbl = ""
        if contract.redoubled:
            dbl = "xx"
        elif contract.doubled:
            dbl = "x"
        out.append(f"Contract     :  {contract.level}{suit_char}{dbl}   {decl_str}")
        # Opening leader is declarer.next() — Trick-prep aids the reader.
        if decl is not None:
            out.append(f"Trick-prep   : {_SEAT_CHARS[decl.next()]} ")
        out.append("")

    if board.tricks:
        out.extend(_format_tricks_block(list(board.tricks), contract))
        out.append("")

    # In-progress trick (cards already played but trick not complete).
    # Suppressed once the hand is over or all 13 tricks have already
    # been recorded — the controller doesn't always clear
    # board.current_trick when the 13th trick lands, and emitting a
    # stale "(incomplete)" line after the hand confuses Claude and
    # the user equally.
    ct = getattr(board, 'current_trick', None)
    hand_finished = (phase == 'finished'
                     or len(board.tricks) >= 13)
    if (ct is not None and ct.cards and ct.leader is not None
            and not hand_finished
            and len(ct.cards) < 4):
        partial = []
        for j, card in enumerate(ct.cards):
            player_seat = Seat((ct.leader.value + j) % 4)
            partial.append(
                f"{_SEAT_CHARS[player_seat]}:"
                f"{_SUIT_CHARS[card.suit].lower()}{card.rank.to_char()}"
            )
        out.append(f"Current trick: {' '.join(partial)} (incomplete)")
        out.append("")

    # Footer — viewer + phase context. Suppressed when callers want a
    # pure BDL block (e.g. the hint dialog, where the footer adds clutter
    # without telling Claude anything that isn't already in the BDL).
    if include_footer:
        footer_lines = []
        if viewer_seat is not None:
            partner = viewer_seat.partner()
            lho = viewer_seat.next()
            rho = partner.next()
            footer_lines.append(
                f"Viewer: {_SEAT_CHARS[viewer_seat]}. "
                f"Partner: {_SEAT_CHARS[partner]}. "
                f"LHO: {_SEAT_CHARS[lho]}. "
                f"RHO: {_SEAT_CHARS[rho]}."
            )
            if hands_for_cards and viewer_seat in hands_for_cards:
                try:
                    footer_lines.append(
                        f"Viewer HCP: {hands_for_cards[viewer_seat].hcp()}"
                    )
                except Exception:
                    pass
        if phase:
            if phase == 'finished':
                footer_lines.append("Phase: hand finished (full diagram above).")
            elif phase == 'bidding':
                current = current_seat
                if current is None:
                    current = board.dealer
                    for _ in board.auction:
                        current = current.next()
                footer_lines.append(
                    f"Phase: bidding. {_SEAT_CHARS[current]} to bid next."
                )
            elif phase == 'play':
                current = current_seat
                if current is None and ct is not None and ct.leader is not None:
                    current = Seat((ct.leader.value + len(ct.cards)) % 4)
                if current is not None:
                    footer_lines.append(
                        f"Phase: play. {_SEAT_CHARS[current]} to play next."
                    )
                else:
                    footer_lines.append("Phase: play.")
        if result_summary:
            footer_lines.append(result_summary)
        if footer_lines:
            out.append("---")
            out.extend(footer_lines)

    return "\n".join(out)


class GameLogger:
    """Logs completed bridge hands in BDL format."""

    def __init__(self, log_dir: str = None, ns_system: str = "biq",
                 ew_system: str = "biq"):
        """Initialize the logger.

        Args:
            log_dir: Directory for log files. Defaults to DATA/LOG inside the bridgeIQ package.
            ns_system: N/S bidding system name
            ew_system: E/W bidding system name
        """
        if log_dir is None:
            log_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent / "DATA" / "LOG"

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.ns_bidding_system = ns_system
        self.ew_bidding_system = ew_system

        self.current_log_file: Optional[Path] = None
        self.log_number = 1
        self._init_log_file()

    def _init_log_file(self):
        """Initialize or find the next log file."""
        # Find the next available log number
        while True:
            log_name = f"log-{self.log_number:03d}.bdl"
            log_path = self.log_dir / log_name
            if not log_path.exists():
                break
            self.log_number += 1

        self.current_log_file = self.log_dir / f"log-{self.log_number:03d}.bdl"
        self._write_header()

    def _write_header(self):
        """Write the BDL file header."""
        date_str = datetime.now().strftime("%b/%d/%Y")

        header = f'''DOCTYPE: BDL 17.1
.description.eng = "log file from {date_str}"

Scoring      :  Team (IMP)
.Bidding cnv :  N/S: {self.ns_bidding_system}
.            :  E/W: {self.ew_bidding_system}
.Lead cnv.   :  N/S.Suit   : -hiA-lo2-ln4-lwN-3cH-
.            :  N/S.Notrump: -hiA-lo2-ln4-lwN-3cH-
.            :  E/W.Suit   : -hiA-lo2-ln4-lwN-3cH-
.            :  E/W.Notrump: -hiA-lo2-ln4-lwN-3cH-
.Signal cnv. :  N/S: -ldA-lwN-paA-pwN-dcC-maH-mcH-pcY-dsN-dnN-
.            :  E/W: -ldA-lwN-paA-pwN-dcC-maH-mcH-pcY-dsN-dnN-
.Strength    :  72,c18,q7,r0,t1  Bs auto  E QPR  Mm 5
.Cheating    :  Never

'''
        with open(self.current_log_file, 'w') as f:
            f.write(header)

    def set_bidding_systems(self, ns_system: str, ew_system: str):
        """Update the bidding system names.

        Args:
            ns_system: N/S bidding system name
            ew_system: E/W bidding system name
        """
        self.ns_bidding_system = ns_system
        self.ew_bidding_system = ew_system

    def _card_to_str(self, card: Card) -> str:
        """Convert a card to BDL format (e.g., 'sA', 'hK', 'd7')."""
        suit_chars = {Suit.SPADES: 's', Suit.HEARTS: 'h',
                      Suit.DIAMONDS: 'd', Suit.CLUBS: 'c'}
        return f"{suit_chars[card.suit]}{card.rank.to_char()}"

    def _format_hand_line(self, cards: List[Card], suit: Suit) -> str:
        """Format cards of one suit for display."""
        suit_cards = sorted([c for c in cards if c.suit == suit], key=lambda c: c.rank)
        if not suit_cards:
            return "-"
        return " ".join(c.rank.to_char() for c in suit_cards)

    def _format_cards_block(self, board: BoardState) -> str:
        """Format the cards display block showing all four hands."""
        lines = []

        # Get hands
        n_hand = board.hands.get(Seat.NORTH, None)
        e_hand = board.hands.get(Seat.EAST, None)
        s_hand = board.hands.get(Seat.SOUTH, None)
        w_hand = board.hands.get(Seat.WEST, None)

        # We need to reconstruct original hands if cards have been played
        # For now, use what we have (this should be called before play or with saved original hands)

        def get_suit_str(hand, suit):
            if hand is None:
                return "-"
            cards = [c for c in hand.cards if c.suit == suit]
            if not cards:
                return "-"
            sorted_cards = sorted(cards, key=lambda c: c.rank)
            return " ".join(c.rank.to_char() for c in sorted_cards)

        # North hand (centered at column ~25)
        n_spades = get_suit_str(n_hand, Suit.SPADES) if n_hand else "-"
        n_hearts = get_suit_str(n_hand, Suit.HEARTS) if n_hand else "-"
        n_diamonds = get_suit_str(n_hand, Suit.DIAMONDS) if n_hand else "-"
        n_clubs = get_suit_str(n_hand, Suit.CLUBS) if n_hand else "-"

        lines.append(f"Cards        :                  {n_spades} ")
        lines.append(f"             :                  {n_hearts} ")
        lines.append(f"             :                  {n_diamonds} ")
        lines.append(f"             :                  {n_clubs} ")

        # West and East hands (side by side)
        w_spades = get_suit_str(w_hand, Suit.SPADES) if w_hand else "-"
        w_hearts = get_suit_str(w_hand, Suit.HEARTS) if w_hand else "-"
        w_diamonds = get_suit_str(w_hand, Suit.DIAMONDS) if w_hand else "-"
        w_clubs = get_suit_str(w_hand, Suit.CLUBS) if w_hand else "-"

        e_spades = get_suit_str(e_hand, Suit.SPADES) if e_hand else "-"
        e_hearts = get_suit_str(e_hand, Suit.HEARTS) if e_hand else "-"
        e_diamonds = get_suit_str(e_hand, Suit.DIAMONDS) if e_hand else "-"
        e_clubs = get_suit_str(e_hand, Suit.CLUBS) if e_hand else "-"

        # Format W/E lines with proper spacing
        lines.append(f"             :   {w_spades:<20}          {e_spades} ")
        lines.append(f"             :   {w_hearts:<20}          {e_hearts} ")
        lines.append(f"             :   {w_diamonds:<20}          {e_diamonds} ")
        lines.append(f"             :   {w_clubs:<20}          {e_clubs} ")

        # South hand (centered)
        s_spades = get_suit_str(s_hand, Suit.SPADES) if s_hand else "-"
        s_hearts = get_suit_str(s_hand, Suit.HEARTS) if s_hand else "-"
        s_diamonds = get_suit_str(s_hand, Suit.DIAMONDS) if s_hand else "-"
        s_clubs = get_suit_str(s_hand, Suit.CLUBS) if s_hand else "-"

        lines.append(f"             :                  {s_spades} ")
        lines.append(f"             :                  {s_hearts} ")
        lines.append(f"             :                  {s_diamonds} ")
        lines.append(f"             :                  {s_clubs} ")

        return "\n".join(lines)

    def _vuln_to_str(self, vuln: Vulnerability) -> str:
        """Convert vulnerability to BDL format."""
        return {
            Vulnerability.NONE: "None",
            Vulnerability.NS: "N/S",
            Vulnerability.EW: "E/W",
            Vulnerability.BOTH: "Both"
        }.get(vuln, "None")

    def _seat_to_str(self, seat: Seat) -> str:
        """Convert seat to full name."""
        return {
            Seat.NORTH: "North",
            Seat.EAST: "East",
            Seat.SOUTH: "South",
            Seat.WEST: "West"
        }[seat]

    def _contract_to_str(self, contract: Contract) -> str:
        """Convert contract to BDL format (e.g., '3nt', '4s', '2hx')."""
        suit_chars = {
            Suit.SPADES: 's', Suit.HEARTS: 'h',
            Suit.DIAMONDS: 'd', Suit.CLUBS: 'c',
            Suit.NOTRUMP: 'nt'
        }
        result = f"{contract.level}{suit_chars[contract.suit]}"
        if contract.redoubled:
            result += "xx"
        elif contract.doubled:
            result += "x"
        return result

    def _format_tricks(self, board: BoardState) -> str:
        """Format the tricks section."""
        lines = []
        trump = board.contract.suit if board.contract.suit != Suit.NOTRUMP else None
        declarer_side_ns = board.contract.declarer.is_ns()

        suit_chars = {Suit.SPADES: 'S', Suit.HEARTS: 'H',
                      Suit.DIAMONDS: 'D', Suit.CLUBS: 'C'}
        seat_chars = {Seat.NORTH: 'N', Seat.EAST: 'E',
                      Seat.SOUTH: 'S', Seat.WEST: 'W'}

        for i, trick in enumerate(board.tricks):
            trick_num = i + 1
            leader = trick.leader
            winner = trick.winner

            # Determine if declarer won this trick
            declarer_won = winner.is_ns() == declarer_side_ns

            # Format each card with winner marker
            card_strs = []
            for j, card in enumerate(trick.cards):
                card_str = self._card_to_str(card)
                player_seat = Seat((leader.value + j) % 4)
                if player_seat == winner:
                    # + for declarer win, - for defender win
                    card_str += "+" if declarer_won else "-"
                card_strs.append(f"{card_str:<4}")

            # Winning suit indicator
            winning_suit = trick.cards[0].suit  # Lead suit or trump
            if winner is not None:
                winner_idx = (winner.value - leader.value) % 4
                winning_suit = trick.cards[winner_idx].suit
            suit_indicator = suit_chars.get(winning_suit, '')

            # Format spacing based on whether declarer won
            spacing = "     " if declarer_won else "         "

            leader_char = seat_chars[leader]
            cards_str = " ".join(card_strs)

            if trick_num == 1:
                lines.append(f"Tricks       : {trick_num:2d}  {leader_char}    {cards_str}{spacing}{suit_indicator} ")
            else:
                lines.append(f"             : {trick_num:2d}  {leader_char}    {cards_str}{spacing}{suit_indicator} ")

        return "\n".join(lines)

    def log_hand(self, board: BoardState, original_hands: dict = None, deal_text: str = ""):
        """Log a completed hand to the BDL file.

        Args:
            board: The completed board state
            original_hands: Optional dict of original hands before play (for accurate card display)
            deal_text: Optional description text for the deal
        """
        if board.contract is None:
            return  # Can't log passed-out hands meaningfully

        # Calculate Pavlicek deal number from original hands
        hands_for_pavlicek = original_hands if original_hands else board.hands
        pavlicek_num = deal_to_number(hands_for_pavlicek)
        pavlicek_b72 = format_deal_base72(pavlicek_num)

        date_str = datetime.now().strftime("%y%b%d")
        deal_id = pavlicek_b72  # Use Pavlicek number (base-72) as deal ID

        # Calculate result
        contract = board.contract
        target = contract.target_tricks()
        made = board.declarer_tricks
        result_diff = made - target

        # Format result string
        if result_diff >= 0:
            result_str = f"+{result_diff}" if result_diff > 0 else "="
        else:
            result_str = str(result_diff)

        # Calculate score (simplified)
        vulnerable = board.vulnerability.is_vulnerable(contract.declarer)
        score = self._calculate_score(contract, made, vulnerable)

        # Build the log entry
        entry_lines = []
        entry_lines.append(f"Deal         :   {deal_id}")
        entry_lines.append(f"Deal-text    :   {deal_text if deal_text else f'Board #{board.board_number}'}")
        entry_lines.append(f"Dealer       :   {self._seat_to_str(board.dealer)}")
        entry_lines.append(f"Vuln         :   {self._vuln_to_str(board.vulnerability)}  ")

        # Use original hands if provided, otherwise use current state
        if original_hands:
            # Create a temporary board state with original hands for display
            display_board = BoardState(
                board_number=board.board_number,
                dealer=board.dealer,
                vulnerability=board.vulnerability,
                hands=original_hands
            )
            entry_lines.append(self._format_cards_block(display_board))
        else:
            entry_lines.append(self._format_cards_block(board))

        entry_lines.append("")

        # Opening leader
        opening_leader = contract.declarer.next()
        leader_char = {Seat.NORTH: 'N', Seat.EAST: 'E',
                       Seat.SOUTH: 'S', Seat.WEST: 'W'}[opening_leader]
        entry_lines.append(f"Trick-prep   : {leader_char} ")

        # Contract
        doubled_str = "+0"
        if contract.redoubled:
            doubled_str = "+2"
        elif contract.doubled:
            doubled_str = "+1"
        entry_lines.append(f"Contract     :  {self._contract_to_str(contract)}   {self._seat_to_str(contract.declarer)}     {doubled_str}")

        entry_lines.append("")

        # Tricks
        if board.tricks:
            entry_lines.append(self._format_tricks(board))

        entry_lines.append("")

        # Result
        entry_lines.append(f"Result       :  {self._contract_to_str(contract)}   {self._seat_to_str(contract.declarer)}    {result_str}      {score}      {deal_id}")

        entry_lines.append("")
        entry_lines.append("************************************************************")
        entry_lines.append("")
        entry_lines.append("")

        # Write to file
        with open(self.current_log_file, 'a') as f:
            f.write("\n".join(entry_lines))

        # Also save in PBN and PPL formats
        self._save_pbn_alongside(board, original_hands)
        self._save_ppl_alongside(board, original_hands)

        print(f"Hand logged to {self.current_log_file}")

    def _calculate_score(self, contract: Contract, tricks_made: int, vulnerable: bool) -> int:
        """Calculate the score for a contract."""
        level = contract.level
        suit = contract.suit
        target = 6 + level
        result = tricks_made - target

        if result < 0:
            # Undertricks
            undertricks = -result
            if contract.redoubled:
                if vulnerable:
                    return -(200 + 400 * (undertricks - 1)) if undertricks > 1 else -200
                else:
                    score = -100
                    for i in range(1, undertricks):
                        score -= 200 if i < 3 else 400
                    return score
            elif contract.doubled:
                if vulnerable:
                    return -(100 + 200 * (undertricks - 1)) if undertricks > 1 else -100
                else:
                    score = -100
                    for i in range(1, undertricks):
                        score -= 200 if i < 3 else 300
                    return score
            else:
                return -50 * undertricks if not vulnerable else -100 * undertricks

        # Made contract
        # Trick score
        if suit == Suit.NOTRUMP:
            trick_score = 40 + 30 * (level - 1)
        elif suit in (Suit.SPADES, Suit.HEARTS):
            trick_score = 30 * level
        else:
            trick_score = 20 * level

        if contract.doubled:
            trick_score *= 2
        if contract.redoubled:
            trick_score *= 4

        score = trick_score

        # Game/partscore bonus
        if trick_score >= 100:
            score += 500 if vulnerable else 300  # Game bonus
        else:
            score += 50  # Partscore bonus

        # Slam bonuses
        if level == 6:
            score += 750 if vulnerable else 500
        elif level == 7:
            score += 1500 if vulnerable else 1000

        # Doubled/redoubled bonus
        if contract.doubled:
            score += 50
        if contract.redoubled:
            score += 100

        # Overtricks
        if result > 0:
            if contract.redoubled:
                score += result * (400 if vulnerable else 200)
            elif contract.doubled:
                score += result * (200 if vulnerable else 100)
            else:
                if suit == Suit.NOTRUMP or suit in (Suit.SPADES, Suit.HEARTS):
                    score += result * 30
                else:
                    score += result * 20

        return score

    def log_board_run(self, board_run, deal_text: str = ""):
        """Log a completed BenBoardRun (e.g., from closed room).

        Args:
            board_run: BenBoardRun object with completed board data
            deal_text: Optional description text for the deal
        """
        if board_run.contract is None:
            return  # Can't log passed-out hands meaningfully

        # Create a BoardState-like object for logging
        from backend.models import BoardState, Vulnerability

        # Determine dealer and vulnerability from board number
        dealer, vuln = BoardState._board_dealer_vuln(board_run.board_number)

        pavlicek_id = board_run.pavlicek_id

        # Calculate result
        contract = board_run.contract
        target = contract.target_tricks()
        made = board_run.declarer_tricks
        result_diff = made - target

        # Format result string
        if result_diff >= 0:
            result_str = f"+{result_diff}" if result_diff > 0 else "="
        else:
            result_str = str(result_diff)

        # Get score from board_run
        score = board_run.ns_score if contract.declarer.is_ns() else board_run.ew_score

        # Format table indicator for deal text
        table_str = "Closed Room" if board_run.table.value == 1 else "Open Room"
        if not deal_text:
            deal_text = f"Board #{board_run.board_number} ({table_str})"
        else:
            deal_text = f"{deal_text} ({table_str})"

        # Build the log entry
        entry_lines = []
        entry_lines.append(f"Deal         :   {pavlicek_id}")
        entry_lines.append(f"Deal-text    :   {deal_text}")
        entry_lines.append(f"Dealer       :   {self._seat_to_str(dealer)}")
        entry_lines.append(f"Vuln         :   {self._vuln_to_str(vuln)}  ")

        # Format cards block from original_hands
        display_board = BoardState(
            board_number=board_run.board_number,
            dealer=dealer,
            vulnerability=vuln,
            hands=board_run.original_hands
        )
        entry_lines.append(self._format_cards_block(display_board))

        entry_lines.append("")

        # Opening leader
        opening_leader = contract.declarer.next()
        leader_char = {Seat.NORTH: 'N', Seat.EAST: 'E',
                       Seat.SOUTH: 'S', Seat.WEST: 'W'}[opening_leader]
        entry_lines.append(f"Trick-prep   : {leader_char} ")

        # Contract
        doubled_str = "+0"
        if contract.redoubled:
            doubled_str = "+2"
        elif contract.doubled:
            doubled_str = "+1"
        entry_lines.append(f"Contract     :  {self._contract_to_str(contract)}   {self._seat_to_str(contract.declarer)}     {doubled_str}")

        entry_lines.append("")

        # Tricks - format from board_run.tricks
        if board_run.tricks:
            entry_lines.append(self._format_tricks_from_run(board_run))

        entry_lines.append("")

        # Result
        entry_lines.append(f"Result       :  {self._contract_to_str(contract)}   {self._seat_to_str(contract.declarer)}    {result_str}      {score}      {pavlicek_id}")

        entry_lines.append("")
        entry_lines.append("************************************************************")
        entry_lines.append("")
        entry_lines.append("")

        # Write to file
        with open(self.current_log_file, 'a') as f:
            f.write("\n".join(entry_lines))

        print(f"Closed room hand logged to {self.current_log_file}")

    def _format_tricks_from_run(self, board_run) -> str:
        """Format the tricks section from a BenBoardRun."""
        lines = []
        trump = board_run.contract.suit if board_run.contract.suit.value < 4 else None
        declarer_side_ns = board_run.contract.declarer.is_ns()

        suit_chars = {Suit.SPADES: 'S', Suit.HEARTS: 'H',
                      Suit.DIAMONDS: 'D', Suit.CLUBS: 'C'}
        seat_chars = {Seat.NORTH: 'N', Seat.EAST: 'E',
                      Seat.SOUTH: 'S', Seat.WEST: 'W'}

        for i, trick in enumerate(board_run.tricks):
            trick_num = i + 1
            leader = trick.leader
            winner = trick.winner

            # Determine if declarer won this trick
            declarer_won = winner.is_ns() == declarer_side_ns

            # Format each card with winner marker
            card_strs = []
            for j, card in enumerate(trick.cards):
                card_str = self._card_to_str(card)
                player_seat = Seat((leader.value + j) % 4)
                if player_seat == winner:
                    # + for declarer win, - for defender win
                    card_str += "+" if declarer_won else "-"
                card_strs.append(f"{card_str:<4}")

            # Winning suit indicator
            winning_suit = trick.cards[0].suit  # Lead suit or trump
            if winner is not None:
                winner_idx = (winner.value - leader.value) % 4
                winning_suit = trick.cards[winner_idx].suit
            suit_indicator = suit_chars.get(winning_suit, '')

            # Format spacing based on whether declarer won
            spacing = "     " if declarer_won else "         "

            leader_char = seat_chars[leader]
            cards_str = " ".join(card_strs)

            if trick_num == 1:
                lines.append(f"Tricks       : {trick_num:2d}  {leader_char}    {cards_str}{spacing}{suit_indicator} ")
            else:
                lines.append(f"             : {trick_num:2d}  {leader_char}    {cards_str}{spacing}{suit_indicator} ")

        return "\n".join(lines)

    def start_new_log(self):
        """Start a new log file."""
        self.log_number += 1
        self.current_log_file = self.log_dir / f"log-{self.log_number:03d}.bdl"
        self._write_header()

    def _save_pbn_alongside(self, board: BoardState, original_hands: dict = None):
        """Save the hand in PBN format alongside the BDL file."""
        pbn_file = self.current_log_file.with_suffix('.pbn')

        hands = original_hands if original_hands else board.hands
        suit_order = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]

        def hand_to_pbn(hand):
            parts = []
            for suit in suit_order:
                cards = sorted([c for c in hand.cards if c.suit == suit],
                               key=lambda c: c.rank, reverse=True)
                parts.append("".join(c.rank.to_char() for c in cards))
            return ".".join(parts)

        seat_order = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]
        deal_str = "N:" + " ".join(hand_to_pbn(hands[s]) for s in seat_order)

        vuln_map = {
            Vulnerability.NONE: "None", Vulnerability.NS: "NS",
            Vulnerability.EW: "EW", Vulnerability.BOTH: "All"
        }
        dealer_map = {Seat.NORTH: "N", Seat.EAST: "E", Seat.SOUTH: "S", Seat.WEST: "W"}

        lines = []
        lines.append(f'[Event "BridgeIQ Session"]')
        lines.append(f'[Board "{board.board_number}"]')
        lines.append(f'[Dealer "{dealer_map[board.dealer]}"]')
        lines.append(f'[Vulnerable "{vuln_map.get(board.vulnerability, "None")}"]')
        lines.append(f'[Deal "{deal_str}"]')
        if board.contract:
            suit_chars = {Suit.SPADES: 'S', Suit.HEARTS: 'H', Suit.DIAMONDS: 'D',
                          Suit.CLUBS: 'C', Suit.NOTRUMP: 'NT'}
            contract_str = f"{board.contract.level}{suit_chars[board.contract.suit]}"
            if board.contract.redoubled:
                contract_str += "XX"
            elif board.contract.doubled:
                contract_str += "X"
            lines.append(f'[Contract "{contract_str}"]')
            lines.append(f'[Declarer "{dealer_map[board.contract.declarer]}"]')
            if board.declarer_tricks is not None:
                target = board.contract.target_tricks()
                diff = board.declarer_tricks - target
                result = f"+{diff}" if diff > 0 else ("=" if diff == 0 else str(diff))
                lines.append(f'[Result "{board.declarer_tricks}"]')
        lines.append("")

        # Append to PBN file (one file can hold multiple boards)
        with open(pbn_file, 'a') as f:
            f.write("\n".join(lines) + "\n")

    def _append_commentary_to_bdl(self, commentary: str):
        """Append a Commentary block to the current BDL file before the last **** separator."""
        if not commentary or not self.current_log_file or not self.current_log_file.exists():
            return
        lines = commentary.split('\n')
        formatted = []
        for i, line in enumerate(lines):
            if i == 0:
                formatted.append(f"Commentary   :  {line}")
            else:
                formatted.append(f".            :  {line}")
        block = "\n".join(formatted) + "\n"

        content = self.current_log_file.read_text()
        last_sep = content.rfind("****")
        if last_sep >= 0:
            content = content[:last_sep] + block + "\n" + content[last_sep:]
            self.current_log_file.write_text(content)

    def _save_ppl_alongside(self, board: BoardState, original_hands: dict = None):
        """Save the hand in Bridge Baron PPL binary format (774 bytes per deal).

        PPL layout: hand bitmasks at 0x30 (4 players × 4 suits × 2 bytes LE,
        bit 0=rank '2', bit 12='A', suit order C/D/H/S, player order N/E/S/W),
        play permutation at 0x78 (card ID = suit_idx*13 + rank_idx).
        """
        import struct
        ppl_file = self.current_log_file.with_suffix('.ppl')
        hands = original_hands if original_hands else board.hands

        BB12_SUITS = {Suit.CLUBS: 0, Suit.DIAMONDS: 1, Suit.HEARTS: 2, Suit.SPADES: 3}
        BB12_RANKS = {
            Rank.TWO: 0, Rank.THREE: 1, Rank.FOUR: 2, Rank.FIVE: 3,
            Rank.SIX: 4, Rank.SEVEN: 5, Rank.EIGHT: 6, Rank.NINE: 7,
            Rank.TEN: 8, Rank.JACK: 9, Rank.QUEEN: 10, Rank.KING: 11, Rank.ACE: 12
        }
        SEAT_ORDER = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]

        try:
            buf = bytearray(774)
            buf[0] = 0x01; buf[1] = 0x04

            name = f"Board {board.board_number}"[:9]
            buf[0x02:0x02 + len(name)] = name.encode('ascii')
            desc = "BridgeIQ"[:35]
            buf[0x0C:0x0C + len(desc)] = desc.encode('ascii')

            # Hand bitmasks
            for pi, seat in enumerate(SEAT_ORDER):
                hand = hands.get(seat)
                if not hand:
                    continue
                for card in hand.cards:
                    si = BB12_SUITS.get(card.suit)
                    ri = BB12_RANKS.get(card.rank)
                    if si is not None and ri is not None:
                        offset = 0x30 + (pi * 4 + si) * 2
                        mask = struct.unpack_from('<H', buf, offset)[0]
                        mask |= (1 << ri)
                        struct.pack_into('<H', buf, offset, mask)

            # Play permutation
            if board.tricks:
                play_idx = 0
                for trick in board.tricks:
                    for card in trick.cards:
                        si = BB12_SUITS.get(card.suit, 0)
                        ri = BB12_RANKS.get(card.rank, 0)
                        buf[0x78 + play_idx] = si * 13 + ri
                        play_idx += 1
                        if play_idx >= 52:
                            break
                buf[0xAC] = 52
                buf[0xAD] = 0xFF; buf[0xAE] = 0xFF; buf[0xAF] = 0xFF

            with open(ppl_file, 'wb') as f:
                f.write(buf)
        except Exception:
            pass  # PPL generation is best-effort
