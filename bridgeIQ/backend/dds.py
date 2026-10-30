"""Direct ctypes binding to libdds.so.0 — the Double-Dummy Solver.

Replaces the third-party `ddsolver` Python wrapper that previously
lived under `ben/src/ddsolver/`. We load `libdds.so.0` (provided by
the Ubuntu `libdds0` package) and call its C entry points directly.

Public surface (matches the call sites in `backend.engine`):

  ddTableDealPBN, ddTableResults   — ctypes Structures
  CalcDDtablePBN(deal, table_ptr)  — fills a 5x4 trick table
  get_error_message(code)          — DDS error text
  DDSolver()                       — high-level helper:
      .version() -> str
      .solve(strain_i, leader, current_trick_52, sample_pbns,
             solutions=3) -> Dict[card52, List[tricks_per_sample]]

Encoding conventions:
  Suit (DDS native):  0=S, 1=H, 2=D, 3=C, 4=NT
  Seat (DDS native):  0=N, 1=E, 2=S, 3=W
  Rank (DDS native):  2..14 (2=two .. 14=ace)
  card52              suit * 13 + (Rank.ACE=0 .. Rank.TWO=12)
                      so DDS rank == 14 - (card52 % 13)
  strain_i (BEN-style, kept for caller compatibility):
                      1=S, 2=H, 3=D, 4=C, 5=NT  →  DDS trump = strain_i - 1
"""

import atexit
import ctypes
import os
import sys
import threading
from ctypes import c_int, c_char, Structure, POINTER
from ctypes.util import find_library
from typing import Tuple, Dict, List


# ---------- Library load ----------

_lib_path = find_library("dds") or "libdds.so.0"
_dds = ctypes.CDLL(_lib_path)

# Every solver call passes threadIndex 0, and ctypes drops the GIL during the
# call, so two Python threads (e.g. two QThreads) solving at once share one DDS
# workspace and corrupt it; libdds then aborts with "Sum 3 is not four" and
# segfaults in its exit teardown (board 247 crash, 2026-10-24). Serialise.
_DDS_LOCK = threading.RLock()


# ---------- C struct mirrors (from libdds dll.h) ----------

class _DealPBN(Structure):
    _fields_ = [
        ("trump", c_int),
        ("first", c_int),
        ("currentTrickSuit", c_int * 3),
        ("currentTrickRank", c_int * 3),
        ("remainCards", c_char * 80),
    ]


class _FutureTricks(Structure):
    _fields_ = [
        ("nodes", c_int),
        ("cards", c_int),
        ("suit", c_int * 13),
        ("rank", c_int * 13),
        ("equals", c_int * 13),
        ("score", c_int * 13),
    ]


class ddTableDealPBN(Structure):
    _fields_ = [("cards", c_char * 80)]


class ddTableResults(Structure):
    # libdds: int resTable[DDS_STRAINS][DDS_HANDS] = [5][4]
    # Indexed as resTable[strain][declarer] where
    #   strain ∈ {0=S, 1=H, 2=D, 3=C, 4=NT}
    #   declarer ∈ {0=N, 1=E, 2=S, 3=W}
    _fields_ = [("resTable", (c_int * 4) * 5)]


# Batch table solving (libdds CalcAllTablesPBN, DDS 2.9: MAXNOOFTABLES=40).
_MAXNOOFTABLES = 40


class ddTableDealsPBN(Structure):
    _fields_ = [("noOfTables", c_int),
                ("deals", ddTableDealPBN * (_MAXNOOFTABLES * 5))]


class ddTablesRes(Structure):
    _fields_ = [("noOfBoards", c_int),
                ("results", ddTableResults * (_MAXNOOFTABLES * 5))]


class _parResults(Structure):
    _fields_ = [("parScore", (c_char * 16) * 2),
                ("parContractsString", (c_char * 128) * 2)]


class _allParResults(Structure):
    _fields_ = [("presults", _parResults * _MAXNOOFTABLES)]


# ---------- Function prototypes ----------

_dds.SolveBoardPBN.argtypes = [
    _DealPBN, c_int, c_int, c_int, POINTER(_FutureTricks), c_int
]
_dds.SolveBoardPBN.restype = c_int

_dds.CalcDDtablePBN.argtypes = [ddTableDealPBN, POINTER(ddTableResults)]
_dds.CalcDDtablePBN.restype = c_int

_dds.ErrorMessage.argtypes = [c_int, c_char * 80]
_dds.ErrorMessage.restype = None

_dds.CalcAllTablesPBN.argtypes = [POINTER(ddTableDealsPBN), c_int,
                                  POINTER(c_int), POINTER(ddTablesRes),
                                  POINTER(_allParResults)]
_dds.CalcAllTablesPBN.restype = c_int

_dds.SetMaxThreads.argtypes = [c_int]
_dds.SetMaxThreads.restype = None

_dds.FreeMemory.argtypes = []
_dds.FreeMemory.restype = None

# Auto-detect threads once on import. 0 = let libdds choose.
_dds.SetMaxThreads(0)

# libdds spawns persistent worker threads that dereference freed state
# during interpreter finalization (after atexit handlers run, as the C
# runtime tears down) → SIGSEGV at PROCESS EXIT — *after* all real work and
# output are done. The crash is cosmetic but corrupts the exit code (139)
# and prints a scary "Segmentation fault" (and breaks anything that checks
# the exit code). FreeMemory/SetMaxThreads/RTLD_NODELETE/OMP_NUM_THREADS do
# NOT prevent it; the worker threads crash regardless.
#
# Fix: short-circuit the crash-prone finalization. At exit, free the DDS
# pool, flush, and os._exit() with the program's INTENDED code — so normal
# runs exit 0 and failures still report non-zero. We capture that code from
# sys.exit() calls and uncaught exceptions.
_exit_code = [0]

_real_exit = sys.exit


def _exit_capture(code=None):
    _exit_code[0] = 0 if code is None else (
        int(code) if isinstance(code, (int, bool)) else 1)
    _real_exit(code)


sys.exit = _exit_capture

_real_excepthook = sys.excepthook


def _excepthook_capture(exc_type, exc, tb):
    if exc_type is not SystemExit:
        _exit_code[0] = 1
    _real_excepthook(exc_type, exc, tb)


sys.excepthook = _excepthook_capture


def _dds_clean_exit():
    # Don't free the pool under a solve still running on another thread.
    if _DDS_LOCK.acquire(timeout=5):
        try:
            _dds.FreeMemory()
        except Exception:
            pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(_exit_code[0])


atexit.register(_dds_clean_exit)


# ---------- Module-level helpers (drop-ins for the old `dds` module) ----------

def CalcDDtablePBN(deal: ddTableDealPBN, table_ptr) -> int:
    """Compute the 5x4 double-dummy table.

    `deal.cards` must be a PBN deal string (`b"N:..."`).
    On success returns 1; on error returns a libdds error code
    (use `get_error_message` to format).
    """
    with _DDS_LOCK:
        return _dds.CalcDDtablePBN(deal, table_ptr)


def get_error_message(code: int) -> str:
    buf = (c_char * 80)()
    _dds.ErrorMessage(c_int(code), buf)
    return buf.value.decode("ascii", errors="replace")


# ---------- High-level DDSolver ----------

def _c52_to_dds(c52: int):
    return (c52 // 13, 14 - (c52 % 13))


def _dds_to_c52(suit: int, rank: int) -> int:
    return suit * 13 + (14 - rank)


# libdds SolveBoard mode (see DDSolver.solve). BIQ_DDS_MODE=0 restores the
# old behaviour for A/B measurement only.
import os as _os
_SOLVE_MODE = int(_os.environ.get("BIQ_DDS_MODE", "1"))


class DDSolver:
    """Per-call solver. Stateless wrapper over `SolveBoardPBN`.

    Constructor accepts a `dds_mode` argument for source compatibility
    with the old upstream wrapper, but it has no effect — libdds
    picks its own search strategy.
    """

    def __init__(self, dds_mode: int = 0):
        self.dds_mode = dds_mode

    def version(self) -> str:
        return "libdds.so.0 (system)"

    def solve(self, strain_i: int, leader: int,
              current_trick_52: List[int],
              sample_pbns: List[str],
              solutions: int = 3) -> Dict[int, List[int]]:
        """Solve a card-play position across Monte-Carlo samples.

        Args:
            strain_i: trump (BEN convention, 1-based — 1=S..5=NT).
            leader: 0=N, 1=E, 2=S, 3=W — who led the current trick.
            current_trick_52: cards already played in the current
                trick, in lead order (length 0..3).
            sample_pbns: PBN deal strings ("N:AKQ.JT9.876.5432 ...")
                where each hand reflects only the cards still in
                play (already-played cards stripped).
            solutions: SolveBoard `solutions` param —
                1 = best card only; 2 = all best-scoring cards;
                3 = every legal card with its trick count.

        Returns dict {card52: [tricks_per_sample]}. Trick counts are
        for the side currently on play.
        """
        trump = strain_i - 1   # BEN 1-based → DDS 0-based

        cur_suit = [0, 0, 0]
        cur_rank = [0, 0, 0]
        for i, c52 in enumerate(current_trick_52[:3]):
            s, r = _c52_to_dds(c52)
            cur_suit[i] = s
            cur_rank[i] = r

        results: Dict[int, List[int]] = {}
        for pbn in sample_pbns:
            dl = _DealPBN()
            dl.trump = trump
            dl.first = leader
            dl.currentTrickSuit = (c_int * 3)(*cur_suit)
            dl.currentTrickRank = (c_int * 3)(*cur_rank)
            dl.remainCards = pbn.encode("utf-8")

            fut = _FutureTricks()
            target = -1   # -1 = find max tricks
            # mode 1 = ALWAYS search. In mode 0 libdds skips the search when
            # the hand to play has only one card (counting equivalents) and
            # returns score -2 for it. Callers read score as a trick count,
            # so every alpha-mu leaf whose leader held e.g. just c3-c2 was
            # valued at -2 tricks: in RUN2-004 dummy led c3 from J932 into
            # partner's c6 (stranding three club winners, 3 tricks) because
            # cashing the J reached such a leaf and scored "0".
            mode = _SOLVE_MODE
            with _DDS_LOCK:
                rc = _dds.SolveBoardPBN(
                    dl, target, solutions, mode,
                    ctypes.byref(fut), 0
                )
            if rc != 1:
                continue

            for i in range(fut.cards):
                s = fut.suit[i]
                r = fut.rank[i]
                score = fut.score[i]
                c52 = _dds_to_c52(s, r)
                results.setdefault(c52, []).append(score)
                # Expand the "equals" bitmap — bits 2..14 mark other
                # ranks the same player holds that are touching this
                # one (e.g. K returned with A and Q set).
                eq = fut.equals[i]
                bit = 2
                while bit <= 14:
                    if eq & (1 << bit):
                        results.setdefault(
                            _dds_to_c52(s, bit), []).append(score)
                    bit += 1

        return results

    def solve_dd_table(self, pbn: str) -> Dict[str, Dict[str, int]]:
        """Compute the full 5x4 double-dummy table for a complete deal.

        `pbn` is a deal string ("N:AKQ.JT9.876.5432 ..."). Returns
        a nested dict declarer → strain → tricks.
        """
        deal = ddTableDealPBN()
        deal.cards = pbn.encode("utf-8")
        table = ddTableResults()
        with _DDS_LOCK:
            rc = _dds.CalcDDtablePBN(deal, ctypes.byref(table))
        if rc != 1:
            raise RuntimeError(
                f"CalcDDtablePBN failed: {get_error_message(rc)}")

        seat_map = ["N", "E", "S", "W"]
        strain_map = ["S", "H", "D", "C", "NT"]
        out: Dict[str, Dict[str, int]] = {s: {} for s in seat_map}
        for st_i, st in enumerate(strain_map):
            for se_i, se in enumerate(seat_map):
                out[se][st] = int(table.resTable[st_i][se_i])
        return out

    def solve_dd_tables_par(self, pbns: List[str], vul_mode: int
                            ) -> List[Tuple[Dict[str, Dict[str, int]], int]]:
        """Full double-dummy tables plus the N/S PAR score for many deals that
        share one vulnerability (libdds mode: 0 none, 1 both, 2 N/S, 3 E/W)."""
        seat_map = ["N", "E", "S", "W"]
        strain_map = ["S", "H", "D", "C", "NT"]
        out = []
        for i in range(0, len(pbns), _MAXNOOFTABLES // 5 or 1):
            chunk = pbns[i:i + (_MAXNOOFTABLES // 5 or 1)]
            deals = ddTableDealsPBN()
            deals.noOfTables = len(chunk)
            for k, pbn in enumerate(chunk):
                deals.deals[k].cards = pbn.encode("utf-8")
            res = ddTablesRes()
            par = _allParResults()
            filt = (c_int * 5)(0, 0, 0, 0, 0)
            with _DDS_LOCK:
                rc = _dds.CalcAllTablesPBN(ctypes.byref(deals), vul_mode, filt,
                                           ctypes.byref(res), ctypes.byref(par))
            if rc != 1:
                raise RuntimeError(
                    f"CalcAllTablesPBN failed: {get_error_message(rc)}")
            for k in range(len(chunk)):
                t = res.results[k]
                tab = {se: {st: int(t.resTable[st_i][se_i])
                            for st_i, st in enumerate(strain_map)}
                       for se_i, se in enumerate(seat_map)}
                txt = par.presults[k].parScore[0].value.decode()   # "NS 620"
                try:
                    ns_par = int(txt.split()[-1])
                except (ValueError, IndexError):
                    ns_par = 0
                out.append((tab, ns_par))
        return out

    def solve_dd_tables(self, pbns: List[str],
                        strains=("S", "H", "D", "C", "NT")
                        ) -> List[Dict[str, Dict[str, int]]]:
        """Double-dummy tables for MANY deals at once (libdds solves them in
        parallel on all cores). `strains` limits the work to the strains the
        caller needs; the others come back missing from each table. Same
        {declarer: {strain: tricks}} shape as solve_dd_table."""
        seat_map = ["N", "E", "S", "W"]
        strain_map = ["S", "H", "D", "C", "NT"]
        want = [1 if st not in strains else 0 for st in strain_map]  # 0 = solve
        n_strains = sum(1 for w in want if w == 0) or 1
        per_call = max(1, (_MAXNOOFTABLES * 5) // n_strains)
        per_call = min(per_call, _MAXNOOFTABLES)
        out: List[Dict[str, Dict[str, int]]] = []
        for i in range(0, len(pbns), per_call):
            chunk = pbns[i:i + per_call]
            deals = ddTableDealsPBN()
            deals.noOfTables = len(chunk)
            for k, pbn in enumerate(chunk):
                deals.deals[k].cards = pbn.encode("utf-8")
            res = ddTablesRes()
            par = _allParResults()
            filt = (c_int * 5)(*want)
            with _DDS_LOCK:
                rc = _dds.CalcAllTablesPBN(ctypes.byref(deals), -1, filt,
                                           ctypes.byref(res), ctypes.byref(par))
            if rc != 1:
                raise RuntimeError(
                    f"CalcAllTablesPBN failed: {get_error_message(rc)}")
            for k in range(len(chunk)):
                t = res.results[k]
                tab: Dict[str, Dict[str, int]] = {se: {} for se in seat_map}
                for st_i, st in enumerate(strain_map):
                    if want[st_i] == 0:
                        for se_i, se in enumerate(seat_map):
                            tab[se][st] = int(t.resTable[st_i][se_i])
                out.append(tab)
        return out
