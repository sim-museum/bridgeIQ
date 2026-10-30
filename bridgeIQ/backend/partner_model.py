"""What partner holds, learned from biq's own bidding.

Partner is biq, so the honest answer to "what has partner shown so far?" is
"whatever hands make biq's rules produce these calls". Hand-written
inference (auction_inference) misses that answer 12-22% of the time on
biq's own auctions (HCP outside its range, 2026-10-04 check), so game and
slam decisions can't lean on it. This table is learned instead: biq bids
hundreds of thousands of random deals at all four seats
(tools/biq_profile_build.py), and at every turn we record partner's actual
HCP and suit lengths under the auction seen from that seat.

Lookups back off from exact to coarse, like qplus_model:
  K1  every call so far, seat-relative (M/L/P/R = me/LHO/partner/RHO),
  K2  the same without the passes,
  K3  our side's non-pass calls in order + how many calls the opponents
      made (contest level), no opponent detail.
A key needs MIN_N deals behind it to be used. BIQ_PARTNER_PROFILES=<path>
reads another table (held-out checks, a fresh build before it ships).

There is one table per bidding system (path_for); a system without one
gets no profiles, so the callers fall back to the rules alone.

The table goes stale when the rules change: rebuild it after bidder edits
that change what partner's calls mean (the file records the bidder
fingerprint it was built with; test_partner_model.py warns on a mismatch).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_DATA = Path(__file__).resolve().parent / "data"
_PATH = _DATA / "biq_partner_profiles.json.gz"          # SAYC
MIN_N = {"K1": 20, "K2": 30, "K3": 40}
_TABLES: Dict[str, dict] = {}


def path_for(system: str) -> Path:
    """One table per bidding system: the same auction means different hands
    in SAYC and Precision (1C is 16+ in Precision)."""
    return _PATH if system == "SAYC" else _DATA / f"biq_partner_profiles_{system}.json.gz"


def system_name(system) -> str:
    return getattr(system, "name", None) or str(system or "SAYC")


def call_key(b) -> str:
    if b.is_pass:
        return "P"
    if b.is_double:
        return "X"
    if b.is_redouble:
        return "XX"
    s = "NT" if b.suit.value == 4 else b.suit.to_char()
    return f"{b.level}{s}"


def context(calls: Sequence[str], dealer_value: int, seat_value: int) -> List[str]:
    """Seat-relative tags of the calls so far, e.g. ['L:P', 'P:1S', 'R:X']."""
    rel = {0: "M", 1: "L", 2: "P", 3: "R"}
    out, s = [], dealer_value
    for k in calls:
        out.append(f"{rel[(s - seat_value) % 4]}:{k}")
        s = (s + 1) % 4
    return out


def keys(ctx: Sequence[str]) -> Dict[str, str]:
    nonpass = [t for t in ctx if not t.endswith(":P")]
    ours = [t for t in nonpass if t[0] in "MP"]
    n_opp = sum(1 for t in nonpass if t[0] in "LR")
    return {"K1": " ".join(ctx) or "-",
            "K2": " ".join(nonpass) or "-",
            "K3": (" ".join(ours) or "-") + f" | opp {min(n_opp, 2)}"}


def pack(hcp: int, lens: Sequence[int]) -> int:
    return (hcp << 16) | (lens[0] << 12) | (lens[1] << 8) | (lens[2] << 4) | lens[3]


def unpack(v: int):
    return v >> 16, [(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15]


def summarize(samples: Sequence[int]) -> dict:
    """HCP percentiles [10, 25, 50, 75, 90] and mean; per suit (S, H, D, C)
    length [p10, p50, p90] and mean."""
    hs = sorted(v >> 16 for v in samples)
    n = len(hs)

    def pct(v, p):
        return v[min(n - 1, int(p * n))]
    out = {"n": n, "hcp": [pct(hs, p) for p in (0.1, 0.25, 0.5, 0.75, 0.9)],
           "hcp_mean": round(sum(hs) / n, 1), "len": [], "len_mean": []}
    for i in range(4):
        sh = 12 - 4 * i
        ls = sorted((v >> sh) & 15 for v in samples)
        out["len"].append([pct(ls, p) for p in (0.1, 0.5, 0.9)])
        out["len_mean"].append(round(sum(ls) / n, 1))
    return out


def _table(system: str = "SAYC") -> dict:
    t = _TABLES.get(system)
    if t is None:
        path = os.environ.get("BIQ_PARTNER_PROFILES") or path_for(system)
        try:
            with gzip.open(path, "rt") as fh:
                t = json.load(fh)
        except (OSError, ValueError):
            t = {"profiles": {}}            # no table: callers fall back
        _TABLES[system] = t
    return t


def set_table(table: dict, system: str = "SAYC") -> None:
    """Use `table` instead of the shipped file (tests, held-out checks)."""
    _TABLES[system] = table


def reset() -> None:
    _TABLES.clear()


def built_with(system: str = "SAYC") -> str:
    return _table(system).get("bidder", "")


def bidder_fingerprint() -> str:
    p = Path(__file__).resolve().parent / "native_bidder.py"
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10]


def lookup(ctx: Sequence[str], system: str = "SAYC") -> Optional[dict]:
    t = _table(system).get("profiles", {})
    for lvl, k in keys(ctx).items():
        p = t.get(f"{lvl} {k}")
        if p is not None and p["n"] >= MIN_N[lvl]:
            return dict(p, level=lvl)
    return None


def partner_range(state, system="SAYC") -> Optional[dict]:
    """Partner's profile at `state`, or when the current auction has none (27%
    of biq's turns after partner's second call), the profile at my latest
    earlier turn that has an exact (K1) one: later calls only narrow partner's
    hand, so that range still holds, like the reference accumulating each call's
    shown range. 'level' says which was used ('K1'..'K3' or 'prefix-<calls back>')."""
    pv = partner_view(state, system)
    if pv is not None:
        return pv
    try:
        calls = [call_key(b) for _s, b in state.bids]
        name = system_name(system)
        t = _table(name).get("profiles", {})
        for k in range(len(calls) - 4, -1, -4):
            ctx = context(calls[:k], state.dealer.value, state.seat.value)
            p = t.get("K1 " + keys(ctx)["K1"])
            if p is not None and p["n"] >= MIN_N["K1"]:
                return dict(p, level=f"prefix-{len(calls) - k}")
    except Exception:
        return None
    return None


def partner_view(state, system="SAYC") -> Optional[dict]:
    """Partner's learned profile at `state` (an AuctionState) under
    `system` (name or BiddingSystem), or None."""
    try:
        calls = [call_key(b) for _s, b in state.bids]
        ctx = context(calls, state.dealer.value, state.seat.value)
    except Exception:
        return None
    return lookup(ctx, system_name(system))
