"""What Q-Plus's calls show, learned from Q-Plus's own recorded auctions.

biq's simulation (bid_sim sampler, simulated leads, card-play sampling)
decides which hidden hands are consistent with the auction. For partner that
means "biq's rules make these calls"; for Q-Plus opponents that test is
wrong wherever Q-Plus bids differently (a 3-level preempt on 5 HCP biq would
make, Q-Plus never does). This module holds, for each Q-Plus call in each
situation, the HCP and suit-length ranges of the hands Q-Plus actually made
it with (built by tools/qplus_profile_build.py from the four-hand deal
records Q-Plus writes, backend/data/qplus_call_profiles.json).

Situations back off from exact to coarse:
  K1  every call so far, seat-relative (M/L/P/R = me/LHO/partner/RHO),
  K2  the same without the passes,
  K3  a role: opening / overcall over X / response to X / rebid after X.
A key needs MIN_N examples to be used; otherwise the caller falls back to
biq's own rules for that call.

BIQ_OPP_MODEL=qplus switches the samplers to this model for opponents' calls
(the Q-NET client sets it: its opponents are always Q-Plus).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_PATH = Path(__file__).resolve().parent / "data" / "qplus_call_profiles.json"
MIN_N = {"K1": 6, "K2": 6, "K3": 10}
_TABLE: Optional[dict] = None


def enabled() -> bool:
    return os.environ.get("BIQ_OPP_MODEL", "biq") == "qplus"


def context(prefix_keys: Sequence[str], dealer_value: int, seat_value: int) -> List[str]:
    """Seat-relative tags of the calls so far, e.g. ['L:P', 'P:1S', 'R:X']."""
    rel = {0: "M", 1: "L", 2: "P", 3: "R"}
    out, s = [], dealer_value
    for k in prefix_keys:
        out.append(f"{rel[(s - seat_value) % 4]}:{k}")
        s = (s + 1) % 4
    return out


def keys(ctx: Sequence[str], call: str) -> Dict[str, str]:
    nonpass = [t for t in ctx if not t.endswith(":P")]
    mine = [t[2:] for t in nonpass if t.startswith("M:")]
    part = [t[2:] for t in nonpass if t.startswith("P:")]
    opp = [t[2:] for t in nonpass if t[0] in "LR"]
    if not nonpass:
        role = f"open@{len(ctx)}"
    elif mine:
        role = f"rebid:{mine[0]}:{part[-1] if part else '-'}:{'c' if opp else 'u'}"
    elif part:
        role = f"resp:{part[0]}:{'c' if opp else 'u'}"
    else:
        role = f"ovc:{opp[-1]}:{len(opp)}"
    return {"K1": " ".join(ctx) + f" => {call}",
            "K2": " ".join(nonpass) + f" => {call}",
            "K3": f"{role} => {call}"}


def build(rows: List[dict]) -> dict:
    """rows: {'ctx': [...tags], 'call': 'P'|'1S'|..., 'hcp': int, 'len': [S,H,D,C]}"""
    groups: Dict[str, List[dict]] = {}
    for r in rows:
        for lvl, k in keys(r["ctx"], r["call"]).items():
            groups.setdefault(k, []).append(r)
    table = {}
    for k, rs in groups.items():
        n = len(rs)
        if n < min(MIN_N.values()):
            continue

        def rng(vals):
            v = sorted(vals)
            if n >= 20:                   # shed outliers on big groups
                lo, hi = v[int(0.05 * n)], v[int(0.95 * n) - 1]
                return [lo, max(lo, hi)]
            return [v[0], v[-1]]
        table[k] = {"n": n, "hcp": rng([r["hcp"] for r in rs]),
                    "len": [rng([r["len"][i] for r in rs]) for i in range(4)]}
    return table


def _table() -> dict:
    global _TABLE
    if _TABLE is None:
        try:
            _TABLE = json.loads(_PATH.read_text())
        except (OSError, ValueError):
            _TABLE = {}
    return _TABLE


def set_table(table: dict) -> None:
    """Use `table` instead of the shipped file (held-out evaluation)."""
    global _TABLE
    _TABLE = table


def profile(ctx: Sequence[str], call: str) -> Optional[dict]:
    t = _table()
    for lvl, k in keys(ctx, call).items():
        p = t.get(k)
        if p is not None and p["n"] >= MIN_N[lvl]:
            return p
    return None


def fits(p: dict, hcp: int, lens: Sequence[int], slack: int = 1) -> bool:
    """Is a hand inside the profile (HCP ±slack, suit lengths exact)?"""
    lo, hi = p["hcp"]
    if hcp < lo - slack or hcp > hi + slack:
        return False
    for i in range(4):
        a, b = p["len"][i]
        if lens[i] < a or lens[i] > b:
            return False
    return True
