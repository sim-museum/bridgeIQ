"""Q-Plus diff oracle: feed deals in via OWN-DEALS .BDE, capture BDL output.

Q-Plus has no Bridge Table Manager protocol — its networking is
Q-NET.EXE, a proprietary peer-to-peer over TCP/DDE that's
expensive to reverse-engineer. The practical alternative is file
injection:

  1. Write a multi-deal `.BDE` file into Q-Plus's
     `DATA/OWN-DEALS/` directory.
  2. User loads it (Own deals → Open → BB_DIFF.BDE) and steps
     through each deal letting Q-Plus auto-play.
  3. Q-Plus writes one `log-NNN.bdl` per played deal into
     `DATA/LOG/`.
  4. We snapshot LOG/ before, poll for new BDLs after each
     deal completes, and parse them through `bdl_reader` to
     extract the auction for diffing.

This module provides the file I/O and polling helpers; the
orchestrator lives in `tools/qplus_diff.py`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .models import BoardState, Hand, Card, Rank, Seat, Suit, Vulnerability


# Q-Plus DATA tree layout under the project's Wine prefix.
def qplus_install_dir() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        for sub in ("WP/drive_c/games/qbridge17",
                    "WP/drive_c/games/qbridge15"):
            cand = parent / sub
            if cand.is_dir():
                return cand
    return None


def qplus_own_deals_dir() -> Optional[Path]:
    d = qplus_install_dir()
    return (d / "DATA" / "OWN-DEALS") if d else None


def qplus_log_dir() -> Optional[Path]:
    d = qplus_install_dir()
    return (d / "DATA" / "LOG") if d else None


def qplus_local_matches_dir() -> Optional[Path]:
    """The directory that holds Q-Plus's saved-match savescore files
    (savescore.qss + per-match snapshots). The savescore is the only
    place per-deal `.Bidding cnv` markers survive after save+exit —
    the standalone .BDE in OWN-DEALS is stripped of those markers."""
    d = qplus_install_dir()
    return (d / "DATA" / "LOCAL-MATCHES") if d else None


def qplus_match_boards_own_deals() -> Optional[int]:
    """Read Q-Plus's current 'Deal → Match control → # boards' value
    for the Own-deals match source from CONFIG/B-MATCH.CFB.

    The file looks like:
        general.deal_own = 16.".\\data\\own-deals\\b1_71352.BDE"
    where the leading integer is the # boards. Q-Plus flushes this on
    exit, so if Q-Plus is currently running the value reflects the
    LAST exit, not the live setting. Returns None if the file is
    missing or unparseable.
    """
    import re
    d = qplus_install_dir()
    if d is None:
        return None
    f = d / "CONFIG" / "B-MATCH.CFB"
    if not f.is_file():
        return None
    try:
        txt = f.read_text(encoding="latin-1", errors="replace")
    except Exception:
        return None
    m = re.search(r"^general\.deal_own\s*=\s*(\d+)\.",
                  txt, flags=re.MULTILINE)
    if not m:
        return None
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Multi-deal BDE writer
# ---------------------------------------------------------------------------


BDE_SEPARATOR = "*********************************************************"


_DEALER_TO_BDE = {"N": "North", "E": "East", "S": "South", "W": "West"}
_VULN_TO_BDE = {
    Vulnerability.NONE: "---",
    Vulnerability.NS:   "N/S",
    Vulnerability.EW:   "E/W",
    Vulnerability.BOTH: "All",
}


def _rank_char(rank: Rank) -> str:
    return {Rank.ACE: "A", Rank.KING: "K", Rank.QUEEN: "Q",
            Rank.JACK: "J", Rank.TEN: "T", Rank.NINE: "9",
            Rank.EIGHT: "8", Rank.SEVEN: "7", Rank.SIX: "6",
            Rank.FIVE: "5", Rank.FOUR: "4", Rank.THREE: "3",
            Rank.TWO: "2"}[rank]


def _suit_line(cards_of_suit: Iterable[Card]) -> str:
    """Render one suit's holding as 'A K Q J' / '-' for void."""
    ranks = sorted((c.rank.value for c in cards_of_suit))
    if not ranks:
        return "-"
    return " ".join(_rank_char(Rank(v)) for v in ranks)


def _seat_suits(hand: Hand):
    by_suit = {s: [] for s in (Suit.SPADES, Suit.HEARTS,
                               Suit.DIAMONDS, Suit.CLUBS)}
    for c in hand.cards:
        by_suit[c.suit].append(c)
    return by_suit


def _format_one_deal(board: BoardState, label: str) -> str:
    """Render a single deal block in Q-Plus BDE format."""
    suits = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]
    n_s = _seat_suits(board.hands[Seat.NORTH])
    e_s = _seat_suits(board.hands[Seat.EAST])
    s_s = _seat_suits(board.hands[Seat.SOUTH])
    w_s = _seat_suits(board.hands[Seat.WEST])
    dealer = _DEALER_TO_BDE[board.dealer.to_char()]
    vuln = _VULN_TO_BDE[board.vulnerability]
    lines: List[str] = []
    lines.append(f"Deal         :   {label}")
    lines.append(f"Dealer       :   {dealer}")
    lines.append(f"Vuln         :   {vuln}")
    lines.append(f"Cards        :                  {_suit_line(n_s[suits[0]])}")
    for su in suits[1:]:
        lines.append(f"             :                  {_suit_line(n_s[su])}")
    for su in suits:
        wcol = _suit_line(w_s[su])
        ecol = _suit_line(e_s[su])
        lines.append(f"             :   {wcol:<30}{ecol}")
    for su in suits:
        lines.append(f"             :                  {_suit_line(s_s[su])}")
    return "\n".join(lines) + "\n"


def write_multi_deal_bde(
        boards: List[BoardState],
        out_path: Path,
        *,
        description: str = "bridgeIQ diff batch",
        label_prefix: str = "BB-diff",
) -> Path:
    """Write `boards` as a multi-deal BDE file Q-Plus can load.

    File format matches Q-Plus's shipped EXAMPLE.BDE — one DOCTYPE
    line up top, one description, then deal blocks separated by
    a row of asterisks. Returns the absolute path written.

    `label_prefix` is the per-deal label (e.g. "BB-diff" yields
    labels "BB-diff-001", "BB-diff-002", …). Q-Plus shows this
    label in its title bar / status area; pick a prefix that
    identifies which corpus the deal came from.
    """
    parts: List[str] = []
    parts.append("DOCTYPE: BDL 7.1")
    parts.append(f'.description.eng = "{description}"')
    parts.append("")
    for i, b in enumerate(boards, start=1):
        label = f"{label_prefix}-{i:03d}"
        parts.append(_format_one_deal(b, label))
        parts.append(BDE_SEPARATOR)
        parts.append("")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="latin-1")
    return out_path


# ---------------------------------------------------------------------------
# BDL output polling
# ---------------------------------------------------------------------------


def snapshot_log_files(log_dir: Path) -> dict:
    """Map `{filename: (mtime, size)}` of .bdl/.BDL files in `log_dir`.

    Returning the stat tuple (not just the name) lets the poller
    detect files Q-Plus *modifies in place* — overwriting an
    existing log-NNN.bdl with this session's deals — not just
    files with brand-new names.
    """
    if not log_dir.is_dir():
        return {}
    out = {}
    # Match both lowercase `.bdl` (the usual Q-Plus naming) and
    # uppercase `.BDL` in case a future Q-Plus release flips the
    # extension casing.
    for pattern in ("*.bdl", "*.BDL"):
        for p in log_dir.glob(pattern):
            try:
                st = p.stat()
                out[p.name] = (st.st_mtime, st.st_size)
            except OSError:
                continue
    return out


def wait_for_new_bdls(
        log_dir: Path,
        previous,
        expected: int,
        *,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
        heartbeat_seconds: float = 30.0,
        progress: callable = None,
) -> List[Path]:
    """Block until Q-Plus has produced a BDL (new or modified),
    returning the list of paths sorted by mtime (oldest first).

    Q-Plus writes either one BDL per deal OR one BDL containing
    every deal of the session (latter when "Save match and exit"
    is used). It MAY also overwrite an existing log-NNN.bdl —
    e.g. when the user re-saves a match that already had a
    previous log file. So "detection" must catch both:

      * Fresh filename that wasn't in `previous`, OR
      * Existing filename whose (mtime, size) differs from what
        was in `previous`.

    `previous` is either a `set` of bare filenames (legacy
    callers) or a `dict {name: (mtime, size)}` from
    `snapshot_log_files`. The richer dict form enables
    overwrite detection.

    The function returns as soon as the detected file's size
    has been stable for ~3 s (the buffered-write quiet window).

    `progress` is an optional callable invoked once per heartbeat
    interval with a status string — used by the CLI to print
    something so the user knows the loop is still alive.
    """
    legacy = isinstance(previous, set)
    deadline = time.time() + timeout
    last_sizes: dict = {}
    last_change_t: dict = {}
    last_heartbeat = time.time()
    while True:
        current = snapshot_log_files(log_dir)
        # Detect both new names and in-place modifications. Skip
        # 0-byte files — Q-Plus creates an empty `log-NNN.bdl`
        # the moment you open Own-Deals as a placeholder, then
        # only writes contents on "Save match and exit". If we
        # latched onto the empty placeholder, the parser
        # downstream finds zero auctions.
        changed_names = set()
        for name, (mtime, size) in current.items():
            if size == 0:
                continue
            if legacy:
                if name not in previous:
                    changed_names.add(name)
            else:
                prev_stat = previous.get(name)
                if prev_stat is None or prev_stat != (mtime, size):
                    changed_names.add(name)
        if changed_names:
            stable = True
            for name in changed_names:
                size = current[name][1]
                if last_sizes.get(name) != size:
                    last_sizes[name] = size
                    last_change_t[name] = time.time()
                    stable = False
            # Consider the file written when no size change for
            # at least 3 seconds — covers Q-Plus's buffered writes.
            now = time.time()
            if stable and all(now - last_change_t[name] >= 3.0
                              for name in changed_names):
                return sorted((log_dir / n for n in changed_names),
                              key=lambda p: p.stat().st_mtime)
        now = time.time()
        if progress is not None and (now - last_heartbeat) >= heartbeat_seconds:
            elapsed = int(now - (deadline - timeout))
            remaining = max(0, int(deadline - now))
            progress(f"still waiting — {elapsed}s elapsed, "
                     f"{remaining}s left ({len(current)} bdl files in "
                     f"log dir, {len(changed_names)} changed since "
                     f"snapshot). "
                     f"In Q-Plus, finish the deals and pick "
                     f"\"Save match and exit\" to flush a BDL.")
            last_heartbeat = now
        if time.time() > deadline:
            raise TimeoutError(
                f"Q-Plus produced no new or modified BDL within "
                f"{timeout:.0f}s (expected up to {expected} deals). "
                f"Tip: in Q-Plus, finish your deals and use "
                f"\"Save match and exit\" — Q-Plus only flushes the "
                f"BDL on save, not after each individual deal.")
        time.sleep(poll_interval)
