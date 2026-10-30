"""
Scoring System for BridgeIQ - Handles all scoring methods and IMP conversion.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum

from .models import Seat, Vulnerability, Contract


class ScoringType(Enum):
    RUBBER = "Rubber"
    CHICAGO = "Chicago"
    PAIRS = "Pairs"
    TEAMS = "Teams"


@dataclass
class BoardResult:
    """Result of a single board."""
    board_number: int
    pavlicek_id: str
    dealer: Seat
    vulnerability: Vulnerability
    contract: Optional[Contract]
    declarer: Optional[Seat]
    tricks_made: int
    ns_score: int
    ew_score: int
    matchpoints: Optional[float] = None
    imps: Optional[int] = None
    notes: str = ""
    ns_bidding_system: str = "BEN-NN"
    ew_bidding_system: str = "BEN-NN"
    board_run: object = None  # BenBoardRun for replay (not serialized)


# IMP conversion table (difference -> IMPs)
IMP_TABLE = [
    (20, 0), (50, 1), (90, 2), (130, 3), (170, 4),
    (220, 5), (270, 6), (320, 7), (370, 8), (430, 9),
    (500, 10), (600, 11), (750, 12), (900, 13), (1100, 14),
    (1300, 15), (1500, 16), (1750, 17), (2000, 18), (2250, 19),
    (2500, 20), (3000, 21), (3500, 22), (4000, 23), (float('inf'), 24)
]


def diff_to_imps(diff: int) -> int:
    """Convert score difference to IMPs."""
    abs_diff = abs(diff)
    for threshold, imps in IMP_TABLE:
        if abs_diff < threshold:
            return imps if diff >= 0 else -imps
    return 24 if diff >= 0 else -24


def calculate_contract_score(contract: Contract, tricks_made: int,
                            vulnerable: bool) -> int:
    """Calculate score for making or going down in a contract.

    Args:
        contract: The contract being played
        tricks_made: Number of tricks actually made
        vulnerable: Whether declarer is vulnerable

    Returns:
        Score (positive for making, negative for going down)
    """
    level = contract.level
    suit = contract.suit
    target = 6 + level
    result = tricks_made - target

    if result < 0:
        # Undertricks
        undertricks = -result
        if contract.redoubled:
            if vulnerable:
                # Vul redoubled: 400 first, 600 each subsequent
                return -(400 + 600 * (undertricks - 1)) if undertricks > 0 else 0
            else:
                # Non-vul redoubled: 200 first, 400 2nd/3rd, 600 subsequent
                score = 0
                for i in range(undertricks):
                    if i == 0:
                        score += 200
                    elif i < 3:
                        score += 400
                    else:
                        score += 600
                return -score
        elif contract.doubled:
            if vulnerable:
                # Vul doubled: 200 first, 300 each subsequent
                return -(200 + 300 * (undertricks - 1)) if undertricks > 0 else 0
            else:
                # Non-vul doubled: 100 first, 200 2nd/3rd, 300 subsequent
                score = 0
                for i in range(undertricks):
                    if i == 0:
                        score += 100
                    elif i < 3:
                        score += 200
                    else:
                        score += 300
                return -score
        else:
            # Undoubled undertricks
            return -50 * undertricks if not vulnerable else -100 * undertricks

    # Made contract - calculate trick score
    from .models import Suit
    if suit == Suit.NOTRUMP:
        trick_score = 40 + 30 * (level - 1)  # 40 for first, 30 for rest
    elif suit in (Suit.SPADES, Suit.HEARTS):
        trick_score = 30 * level
    else:  # Clubs, Diamonds
        trick_score = 20 * level

    # Apply doubled/redoubled multiplier
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
        score += 750 if vulnerable else 500  # Small slam
    elif level == 7:
        score += 1500 if vulnerable else 1000  # Grand slam

    # Doubled/redoubled insult bonus
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
            # Undoubled overtricks
            if suit == Suit.NOTRUMP or suit in (Suit.SPADES, Suit.HEARTS):
                score += result * 30
            else:
                score += result * 20

    return score


class ScoringTable:
    """Manages scoring for a session/match."""

    def __init__(self, scoring_type: ScoringType = ScoringType.TEAMS):
        self.scoring_type = scoring_type
        self.results: List[BoardResult] = []
        self.session_name: str = ""
        self.date: str = ""
        # The reference .score-edit-names — labels for the four players /
        # teams on the scoring table. Used for export, display, and
        # for distinguishing several saved tables.
        self.player_names: dict = {
            'N': '', 'E': '', 'S': '', 'W': '',
            'NS': '', 'EW': '',
        }
        # Free-text advice / coaching comments to be written into the .qss as
        # leading `#` comment lines whenever it is next exported. There is no
        # live .qss during play (it's produced at close / export), so advice is
        # queued here and rides along the next write. See record_comment().
        self._pending_comments: List[str] = []

    def add_result(self, result: BoardResult):
        """Add a board result."""
        self.results.append(result)

        # Recalculate cumulative scores
        if self.scoring_type == ScoringType.PAIRS:
            self._calculate_matchpoints()
        elif self.scoring_type == ScoringType.TEAMS:
            self._calculate_imps()

    def _calculate_matchpoints(self):
        """Calculate matchpoints (pairs scoring)."""
        # Group by board number
        boards = {}
        for r in self.results:
            if r.board_number not in boards:
                boards[r.board_number] = []
            boards[r.board_number].append(r)

        # Calculate MP for each board
        for board_num, board_results in boards.items():
            ns_scores = [r.ns_score for r in board_results]
            n = len(ns_scores)

            for result in board_results:
                # Count how many you beat + half for ties
                better = sum(1 for s in ns_scores if result.ns_score > s)
                equal = sum(1 for s in ns_scores if result.ns_score == s) - 1
                result.matchpoints = better + equal / 2.0

    def _calculate_imps(self):
        """Calculate IMPs for every board that has BOTH an open- and a
        closed-room result.

        The previous implementation only paired consecutive same-board
        rows, which silently dropped the IMP whenever an unrelated row
        sat between them — a real failure mode once we batch-ingest a
        closed-room LIN database alongside live open-room play.
        """
        # Group by board number; closed-room rows are tagged with
        # "Closed room" in their notes (set by both the reference ingest
        # path and the LIN loader). The first non-closed row on a
        # board is open; if a board ends up with two non-closed rows
        # (rare, but possible if a result was added via a non-standard
        # path) treat the second as closed for pairing.
        groups = {}
        for r in self.results:
            g = groups.setdefault(r.board_number,
                                  {"open": None, "closed": None})
            is_closed = bool(r.notes and "Closed room" in r.notes)
            if is_closed:
                g["closed"] = r
            elif g["open"] is None:
                g["open"] = r
            else:
                g["closed"] = r

        # Reset every IMP first so removing one side later doesn't leave
        # a stale value behind on the partner row.
        for r in self.results:
            r.imps = None

        for pair in groups.values():
            o, c = pair["open"], pair["closed"]
            if o is None or c is None:
                continue
            diff = o.ns_score - c.ns_score
            o.imps = diff_to_imps(diff)
            c.imps = -o.imps

    def cross_imps_for(self, result: 'BoardResult') -> Optional[float]:
        """Compute cross-IMPs for one row: IMP-convert (this NS score
        − average NS score of every OTHER row on the same board),
        sign from N/S. Returns None when only one row exists for
        the board (cross-IMP needs at least two)."""
        same_board = [r for r in self.results
                      if r.board_number == result.board_number]
        if len(same_board) < 2:
            return None
        others = [r for r in same_board if r is not result]
        avg = sum(r.ns_score for r in others) / len(others)
        diff = result.ns_score - avg
        # diff_to_imps is integer-rounded; cross-IMP keeps a float
        # so a 1-IMP-per-table average can still surface as 0.x.
        sign = 1 if diff >= 0 else -1
        abs_diff = abs(diff)
        for threshold, imps in IMP_TABLE:
            if abs_diff < threshold:
                # Linear interpolation inside the bucket so cross-IMP
                # remains a useful tie-breaker.
                prev_threshold = (
                    IMP_TABLE[IMP_TABLE.index((threshold, imps)) - 1][0]
                    if (threshold, imps) != IMP_TABLE[0] else 0)
                prev_imps = (
                    IMP_TABLE[IMP_TABLE.index((threshold, imps)) - 1][1]
                    if (threshold, imps) != IMP_TABLE[0] else 0)
                span = max(1, threshold - prev_threshold)
                frac = (abs_diff - prev_threshold) / span
                interp = prev_imps + frac * (imps - prev_imps)
                return sign * round(interp, 2)
        return sign * 24.0

    def get_ns_total(self) -> int:
        """Get total N/S score."""
        return sum(r.ns_score for r in self.results)

    def get_ew_total(self) -> int:
        """Get total E/W score."""
        return sum(r.ew_score for r in self.results)

    def get_ns_imps(self) -> int:
        """Get total N/S IMPs."""
        return sum(r.imps or 0 for r in self.results if r.imps is not None)

    def get_ns_matchpoints(self) -> float:
        """Get total N/S matchpoints."""
        return sum(r.matchpoints or 0 for r in self.results if r.matchpoints is not None)

    def get_summary(self) -> str:
        """Get text summary of results."""
        lines = [
            f"Session: {self.session_name}" if self.session_name else "Session Results",
            f"Scoring: {self.scoring_type.value}",
            f"Boards: {len(self.results)}",
            "",
        ]

        if self.scoring_type == ScoringType.TEAMS:
            lines.append(f"N/S IMPs: {self.get_ns_imps():+d}")
        elif self.scoring_type == ScoringType.PAIRS:
            lines.append(f"N/S Matchpoints: {self.get_ns_matchpoints():.1f}")
        else:
            lines.append(f"N/S Total: {self.get_ns_total()}")
            lines.append(f"E/W Total: {self.get_ew_total()}")

        return "\n".join(lines)

    def record_comment(self, comment: str):
        """Queue a free-text comment to be written into the .qss (as `#`
        lines) the next time it is exported. Multi-line text is split so each
        physical line becomes its own `#` comment. De-duplicated."""
        if not comment:
            return
        for ln in str(comment).splitlines() or [str(comment)]:
            entry = ln.rstrip()
            if entry and entry not in self._pending_comments:
                self._pending_comments.append(entry)

    def append_comment(self, filepath: str, comment: str):
        """Append `#` comment lines to an ALREADY-saved .qss file."""
        if not comment:
            return
        block = "".join(f"# {ln.rstrip()}\n" for ln in str(comment).splitlines())
        with open(filepath, 'a') as f:
            f.write(block)

    def to_qss_format(self) -> str:
        """Export to BridgeIQ Score Sheet format."""
        lines = [
            "# BridgeIQ score sheet",
            ".version = 17.1",
            f".date = {self.date}",
            f"scoring.method = {self.scoring_type.value}",
        ]
        # Coaching / advice comments captured during the session (hint dialog,
        # instrumentation). Kept as `#` lines so the loader skips them.
        for c in self._pending_comments:
            lines.append(f"# {c}")
        for key in ('N', 'E', 'S', 'W', 'NS', 'EW'):
            name = (self.player_names or {}).get(key, '')
            if name:
                lines.append(f"player.{key} = {name}")
        lines.append("")

        for i, r in enumerate(self.results, 1):
            lines.append(f"board.{i}.number = {r.board_number}")
            lines.append(f"board.{i}.pavlicek = {r.pavlicek_id}")
            if r.contract:
                lines.append(f"board.{i}.contract = {r.contract.to_str()}")
                lines.append(f"board.{i}.declarer = {r.declarer.to_char() if r.declarer else ''}")
            lines.append(f"board.{i}.tricks = {r.tricks_made}")
            lines.append(f"board.{i}.ns_score = {r.ns_score}")
            lines.append(f"board.{i}.ew_score = {r.ew_score}")
            lines.append(f"board.{i}.ns_system = {r.ns_bidding_system}")
            lines.append(f"board.{i}.ew_system = {r.ew_bidding_system}")
            if r.imps is not None:
                lines.append(f"board.{i}.imps = {r.imps}")
            if r.matchpoints is not None:
                lines.append(f"board.{i}.mp = {r.matchpoints}")
            lines.append("")

        return "\n".join(lines)

    def save(self, filepath: str):
        """Save scoring table to file."""
        content = self.to_qss_format()
        with open(filepath, 'w') as f:
            f.write(content)

    def save_to_local_matches(self, match_name: str = None) -> str:
        """Save to DATA/LOCAL-MATCHES directory.

        Creates a uniquely numbered QSS file in the LOCAL-MATCHES directory.

        Args:
            match_name: Optional name for the match file. If not provided,
                       uses incrementing numbers (match-001.qss, etc.)

        Returns:
            Path to the saved file
        """
        from pathlib import Path
        from datetime import datetime

        # Determine the local matches directory
        local_dir = Path(__file__).parent.parent / "DATA" / "LOCAL-MATCHES"
        local_dir.mkdir(parents=True, exist_ok=True)

        if match_name:
            # Use provided name
            filename = f"{match_name}.qss"
            filepath = local_dir / filename

            # Add number suffix if file exists
            counter = 1
            while filepath.exists():
                filename = f"{match_name}-{counter:03d}.qss"
                filepath = local_dir / filename
                counter += 1
        else:
            # Find next available match number
            existing = list(local_dir.glob("match-*.qss"))
            if existing:
                numbers = []
                for f in existing:
                    try:
                        num = int(f.stem.split("-")[1])
                        numbers.append(num)
                    except (IndexError, ValueError):
                        pass
                next_num = max(numbers) + 1 if numbers else 1
            else:
                next_num = 1

            filename = f"match-{next_num:03d}.qss"
            filepath = local_dir / filename

        # Set date if not already set
        if not self.date:
            self.date = datetime.now().strftime("%Y-%m-%d")

        # Save the file
        self.save(str(filepath))

        return str(filepath)

    @classmethod
    def load(cls, filepath: str) -> 'ScoringTable':
        """Load scoring table from file."""
        table = cls()

        with open(filepath, 'r') as f:
            current_board = {}
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()

                    if key == "scoring.method":
                        for st in ScoringType:
                            if st.value == value:
                                table.scoring_type = st
                                break
                    elif key == ".date":
                        table.date = value
                    elif key.startswith("player."):
                        slot = key.split('.', 1)[1].strip().upper()
                        if slot in table.player_names:
                            table.player_names[slot] = value
                    elif key.startswith("board."):
                        parts = key.split('.')
                        if len(parts) == 3:
                            board_idx, field = parts[1], parts[2]
                            if board_idx not in current_board:
                                current_board[board_idx] = {}
                            current_board[board_idx][field] = value

        # Convert parsed data to BoardResult objects
        for idx in sorted(current_board.keys(), key=int):
            data = current_board[idx]
            result = BoardResult(
                board_number=int(data.get('number', 0)),
                pavlicek_id=data.get('pavlicek', ''),
                dealer=Seat.NORTH,  # Default, could be parsed
                vulnerability=Vulnerability.NONE,  # Default
                contract=None,  # Would need parsing
                declarer=None,
                tricks_made=int(data.get('tricks', 0)),
                ns_score=int(data.get('ns_score', 0)),
                ew_score=int(data.get('ew_score', 0)),
                imps=int(data['imps']) if 'imps' in data else None,
                matchpoints=float(data['mp']) if 'mp' in data else None,
            )
            table.results.append(result)

        return table


# ---------------------------------------------------------------------------
# Rubber scoring — standard rubber bridge scoring
# ---------------------------------------------------------------------------

@dataclass
class RubberEntry:
    """One scorecard entry on a rubber. Side is 'NS' or 'EW'; line is
    'below' (trick score that counts toward game) or 'above'
    (overtricks / penalties / honours / slam / rubber / insult).

    ``label`` is the human-readable description rendered into the
    scorecard cell (e.g. "3NT by S, made +1", "100 honours", "down 2
    doubled vul").
    """
    side: str          # 'NS' or 'EW'
    line: str          # 'below' or 'above'
    points: int
    label: str = ""


@dataclass
class RubberScore:
    """Full state of a rubber bridge match.

    The reference reference (per the spec) tracks above-line and
    below-line columns for each side, games won, vulnerability, and
    a running game-by-game divider after each game ends. We model
    that as a flat list of `RubberEntry` rows with a `game_dividers`
    list pointing at the entries where a game ended — the dialog
    renders horizontal lines at those positions.
    """
    entries: List[RubberEntry] = field(default_factory=list)
    games_won_ns: int = 0
    games_won_ew: int = 0
    # Indices INTO entries marking "the entry that completed a game".
    # The dialog inserts a horizontal rule below each of these rows.
    game_dividers: List[int] = field(default_factory=list)
    # Per-side below-line totals SINCE the start of the current game
    # (reset when a game is won).
    current_below_ns: int = 0
    current_below_ew: int = 0
    rubber_complete: bool = False
    # Honours and slam bonus tracking; the spec keeps them in the
    # above-line column with the rest, so we just route them to add().

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    def vulnerable(self, side: str) -> bool:
        """A side becomes vulnerable as soon as they've won a game."""
        if side == 'NS':
            return self.games_won_ns >= 1
        if side == 'EW':
            return self.games_won_ew >= 1
        return False

    def total(self, side: str) -> int:
        """Sum of every entry (above + below) for a side, plus the
        rubber bonus once awarded (it's stored as an above-line
        entry by add_rubber_bonus()).
        """
        return sum(e.points for e in self.entries if e.side == side)

    def above_total(self, side: str) -> int:
        return sum(e.points for e in self.entries
                   if e.side == side and e.line == 'above')

    def below_total(self, side: str) -> int:
        return sum(e.points for e in self.entries
                   if e.side == side and e.line == 'below')

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(self, side: str, line: str, points: int, label: str = ""):
        """Add a raw entry. Mostly called via add_hand_result and
        add_rubber_bonus; exposed publicly so the dialog's Edit menu
        can splice in manual corrections."""
        if side not in ('NS', 'EW') or line not in ('above', 'below'):
            raise ValueError(
                f"invalid rubber entry side={side} line={line}")
        self.entries.append(RubberEntry(
            side=side, line=line, points=int(points), label=label))
        if line == 'below':
            if side == 'NS':
                self.current_below_ns += int(points)
            else:
                self.current_below_ew += int(points)
            self._maybe_complete_game()

    def add_hand_result(self, contract, tricks_made: int,
                         declarer_is_ns: bool, honours: int = 0):
        """Score a finished hand into the rubber scorecard.

        Splits the calculated contract score into below-line (trick
        score, which counts toward game) and above-line (everything
        else — overtricks, slam bonus, doubled bonus, penalties).
        Penalties are written entirely above the line on the
        DEFENDING side. ``honours`` adds an above-line honours bonus
        on declarer's side (100 for 4 trump honours, 150 for all 5
        or 4 aces in NT).
        """
        if contract is None:
            return
        side = 'NS' if declarer_is_ns else 'EW'
        defender = 'EW' if declarer_is_ns else 'NS'
        vul = self.vulnerable(side)

        target = 6 + contract.level
        result = tricks_made - target
        contract_str = (contract.to_str()
                        if hasattr(contract, 'to_str') else '?')

        if result < 0:
            # Set — entire penalty goes to the defending side, above.
            undertricks = -result
            penalty = self._penalty_points(
                undertricks, contract, vul)
            label = (f"set {undertricks} in {contract_str}"
                     + (" (vul)" if vul else ""))
            self.add(defender, 'above', penalty, label)
            return

        # Made — split into trick score (below) and bonuses (above).
        trick_score = self._trick_score(contract)
        overtricks = result
        over_pts, made_bonuses = self._made_bonuses(
            contract, overtricks, vul)
        # Game bonus is NOT a separate above-line entry in the reference
        # rubber — instead, the trick score crossing 100 below the
        # line is what awards a game (and ultimately a rubber bonus).
        # Slam / doubled / overtricks go above.
        made_str = ("made" if overtricks == 0 else
                    f"made +{overtricks}")
        self.add(side, 'below', trick_score,
                 f"{contract_str} {made_str}")
        if over_pts:
            self.add(side, 'above', over_pts,
                     f"overtricks +{overtricks}")
        if made_bonuses:
            self.add(side, 'above', made_bonuses,
                     "slam / doubled bonuses")
        if honours > 0:
            self.add(side, 'above', honours, f"{honours} honours")

    def add_rubber_bonus(self):
        """Award the rubber bonus when one side reaches 2 games. The reference spec uses the classic 700 (2-0) / 500 (2-1) values.
        Called automatically by _maybe_complete_game when the rubber
        finishes; idempotent (safe to call twice)."""
        if self.rubber_complete:
            return
        winner = None
        bonus = 0
        if self.games_won_ns >= 2:
            winner = 'NS'
            bonus = 700 if self.games_won_ew == 0 else 500
        elif self.games_won_ew >= 2:
            winner = 'EW'
            bonus = 700 if self.games_won_ns == 0 else 500
        if winner and bonus:
            self.add(winner, 'above', bonus,
                     f"rubber bonus ({bonus})")
            self.rubber_complete = True

    def delete_last_entry(self):
        """the reference Edit / delete-last button. Reverses any game / rubber
        state the last entry implied."""
        if not self.entries:
            return
        last = self.entries.pop()
        if last.line == 'below':
            if last.side == 'NS':
                self.current_below_ns = max(
                    0, self.current_below_ns - last.points)
            else:
                self.current_below_ew = max(
                    0, self.current_below_ew - last.points)
        # If the popped entry was a game-ending below-line row, peel
        # the divider and the game-counter back off too.
        if self.game_dividers and self.game_dividers[-1] >= len(
                self.entries):
            self.game_dividers.pop()
            # The most recent game-ending side is whoever's
            # current_below hit 100 — easiest way to recover is to
            # decrement whichever side has more games than entries
            # justify and re-sum below totals from scratch.
            self._recount_game_state_from_entries()
        # If we just removed a rubber-bonus entry, the rubber isn't
        # complete any more.
        if last.label.startswith("rubber bonus") or self.rubber_complete:
            self.rubber_complete = (
                self.games_won_ns >= 2 or self.games_won_ew >= 2)

    def clear(self):
        """Wipe every entry. Used by the dialog's Clear button."""
        self.entries.clear()
        self.games_won_ns = 0
        self.games_won_ew = 0
        self.game_dividers.clear()
        self.current_below_ns = 0
        self.current_below_ew = 0
        self.rubber_complete = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _trick_score(contract) -> int:
        from .models import Suit
        level, suit = contract.level, contract.suit
        if suit == Suit.NOTRUMP:
            base = 40 + 30 * (level - 1)
        elif suit in (Suit.SPADES, Suit.HEARTS):
            base = 30 * level
        else:
            base = 20 * level
        if contract.redoubled:
            base *= 4
        elif contract.doubled:
            base *= 2
        return base

    @staticmethod
    def _made_bonuses(contract, overtricks: int, vul: bool) -> tuple:
        """Return (overtrick_points, bonuses) for a made contract.
        bonuses covers slam + doubled insult; the game / rubber
        bonus is awarded separately via _maybe_complete_game and
        add_rubber_bonus."""
        from .models import Suit
        # Overtricks
        if contract.redoubled:
            over_pts = overtricks * (400 if vul else 200)
        elif contract.doubled:
            over_pts = overtricks * (200 if vul else 100)
        else:
            if contract.suit == Suit.NOTRUMP or \
                    contract.suit in (Suit.SPADES, Suit.HEARTS):
                over_pts = overtricks * 30
            else:
                over_pts = overtricks * 20

        # Slam + doubled insult
        bonuses = 0
        if contract.level == 6:
            bonuses += 750 if vul else 500
        elif contract.level == 7:
            bonuses += 1500 if vul else 1000
        if contract.doubled:
            bonuses += 50
        if contract.redoubled:
            bonuses += 100
        return over_pts, bonuses

    @staticmethod
    def _penalty_points(undertricks: int, contract, vul: bool) -> int:
        # Mirrors calculate_contract_score's undertrick logic.
        if contract.redoubled:
            if vul:
                return 400 + 600 * (undertricks - 1)
            score = 0
            for i in range(undertricks):
                if i == 0:
                    score += 200
                elif i < 3:
                    score += 400
                else:
                    score += 600
            return score
        if contract.doubled:
            if vul:
                return 200 + 300 * (undertricks - 1)
            score = 0
            for i in range(undertricks):
                if i == 0:
                    score += 100
                elif i < 3:
                    score += 200
                else:
                    score += 300
            return score
        return undertricks * (100 if vul else 50)

    def _maybe_complete_game(self):
        """Check whether the just-added below-line row pushed a side
        over 100, awarding them a game. Records the divider so the
        scorecard can draw a horizontal line, resets current_below
        on BOTH sides (a new game starts fresh), and triggers the
        rubber bonus if the winner reaches 2 games."""
        if self.current_below_ns >= 100:
            self.games_won_ns += 1
            self.game_dividers.append(len(self.entries) - 1)
            self.current_below_ns = 0
            self.current_below_ew = 0
            if self.games_won_ns >= 2:
                self.add_rubber_bonus()
        elif self.current_below_ew >= 100:
            self.games_won_ew += 1
            self.game_dividers.append(len(self.entries) - 1)
            self.current_below_ns = 0
            self.current_below_ew = 0
            if self.games_won_ew >= 2:
                self.add_rubber_bonus()

    def _recount_game_state_from_entries(self):
        """Walk the entries again from scratch to recompute
        games_won / current_below. Called by delete_last_entry so
        peeling the last row back-rolls game progress correctly."""
        self.games_won_ns = 0
        self.games_won_ew = 0
        self.current_below_ns = 0
        self.current_below_ew = 0
        # Re-derive dividers by replaying below-line scoring.
        new_dividers: List[int] = []
        for i, e in enumerate(self.entries):
            if e.line != 'below':
                continue
            if e.side == 'NS':
                self.current_below_ns += e.points
            else:
                self.current_below_ew += e.points
            if self.current_below_ns >= 100:
                self.games_won_ns += 1
                new_dividers.append(i)
                self.current_below_ns = 0
                self.current_below_ew = 0
            elif self.current_below_ew >= 100:
                self.games_won_ew += 1
                new_dividers.append(i)
                self.current_below_ns = 0
                self.current_below_ew = 0
        self.game_dividers = new_dividers

    # ------------------------------------------------------------------
    # Save / load — File → Restore Score Table needs round-trip JSON.
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            'version': 1,
            'entries': [
                {'side': e.side, 'line': e.line,
                 'points': e.points, 'label': e.label}
                for e in self.entries
            ],
            'games_won_ns': self.games_won_ns,
            'games_won_ew': self.games_won_ew,
            'game_dividers': list(self.game_dividers),
            'current_below_ns': self.current_below_ns,
            'current_below_ew': self.current_below_ew,
            'rubber_complete': self.rubber_complete,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RubberScore':
        rs = cls()
        for e in data.get('entries', []):
            rs.entries.append(RubberEntry(
                side=e.get('side', 'NS'),
                line=e.get('line', 'above'),
                points=int(e.get('points', 0)),
                label=e.get('label', ''),
            ))
        rs.games_won_ns = int(data.get('games_won_ns', 0))
        rs.games_won_ew = int(data.get('games_won_ew', 0))
        rs.game_dividers = list(data.get('game_dividers', []))
        rs.current_below_ns = int(data.get('current_below_ns', 0))
        rs.current_below_ew = int(data.get('current_below_ew', 0))
        rs.rubber_complete = bool(data.get('rubber_complete', False))
        return rs

    def save(self, filepath: str):
        import json
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> 'RubberScore':
        import json
        with open(filepath, 'r') as f:
            return cls.from_dict(json.load(f))


# A-priori suit-split percentages (standard bridge odds)
SUIT_SPLIT_PROBABILITIES = {
    # Cards missing -> splits with probabilities
    2: [(1, 1, 52), (2, 0, 48)],
    3: [(2, 1, 78), (3, 0, 22)],
    4: [(2, 2, 40), (3, 1, 50), (4, 0, 10)],
    5: [(3, 2, 68), (4, 1, 28), (5, 0, 4)],
    6: [(3, 3, 36), (4, 2, 48), (5, 1, 15), (6, 0, 1)],
    7: [(4, 3, 62), (5, 2, 31), (6, 1, 7), (7, 0, 0.5)],
    8: [(4, 4, 33), (5, 3, 47), (6, 2, 17), (7, 1, 3), (8, 0, 0.2)],
}


def get_suit_split_probability(missing: int, split: Tuple[int, int]) -> float:
    """Get probability of a specific suit split.

    Args:
        missing: Number of cards missing in suit
        split: Tuple of (left_count, right_count)

    Returns:
        Probability as percentage (0-100)
    """
    if missing not in SUIT_SPLIT_PROBABILITIES:
        return 0.0

    a, b = min(split), max(split)
    for left, right, prob in SUIT_SPLIT_PROBABILITIES[missing]:
        if (left, right) == (a, b) or (right, left) == (a, b):
            return prob

    return 0.0
