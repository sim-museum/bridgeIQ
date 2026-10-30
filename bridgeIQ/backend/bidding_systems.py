"""Bidding-system specifications.

Each system is a set of named settings (on/off, some with a number: the
1NT range, the 2/1 minimum, the RKC variant, ...), kept in
`backend/data/systems/<name>.json`. If a file is missing, the catalog falls
back to hand-coded defaults.

This module exposes a `BiddingSystem` dataclass that captures the numeric
knobs in structured fields plus the raw flag set, so the rule-based bidder
can ask `system.has("nt.stayman")` for any convention we model.

Systems shipped:

  * **`SAYC`** — *Standard American Yellow Card*, 15-17 1NT,
    5-card majors, 2/1 ≥ 10 HCP.
  * **`TwoOverOne`** — *2-over-1 Game Forcing*.
  * **`StandardAcol`** — *Standard Acol*.
  * **`StandardFrench`** — *Standard French / SEF*.
  * **`Precision90M`** — *Precision Club 90 modern*, 14-16 1NT,
    light-opening 1-suit, Kokish, Bergen-3C, Ghestem.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class SystemFlag:
    """One setting of a system (from backend/data/systems/*.json): its
    name, whether it is on, and its number for the settings that carry one.
    """

    name: str
    enabled: bool
    value: Optional[int] = None
    raw: str = ""

    def matches(self, prefix: str) -> bool:
        """True if this rule's name starts with `prefix` (dot- or full-match)."""
        return self.name == prefix or self.name.startswith(prefix + ".") \
            or self.name.startswith(prefix + "-")


# ---------------------------------------------------------------------------
# BiddingSystem dataclass
# ---------------------------------------------------------------------------


@dataclass
class BiddingSystem:
    """Structured view of a bidding system.

    Fields fall into two groups:

      * **Numeric parameters** — read from the system's settings, with
        sensible defaults if the file is absent. These are what the
        rule-based bidder reads when deciding ranges / thresholds.
      * **Convention set** — `conventions` is the set of settings that are
        on. Code asks `system.has("nt.stayman")` to gate a convention.

    `raw_rules` holds every setting, on or off, by name.
    """

    name: str
    description: str = ""

    # ----- Opening structure -----
    one_nt_min_hcp: int = 15
    one_nt_max_hcp: int = 17
    # Some systems allow 5-card majors / 6-card minors inside 1NT.
    one_nt_allow_five_card_heart: bool = False
    one_nt_allow_five_card_spade: bool = False
    one_nt_allow_six_card_minor: bool = False
    two_nt_min_hcp: int = 20
    two_nt_max_hcp: int = 21
    # Strong artificial opening (2♣ in SAYC, 1♣ in Precision).
    strong_open_call: str = "2C"   # "2C" or "1C"
    strong_open_min_hcp: int = 22  # 22+ for 2C; 16+ for Precision 1C
    one_major_card_min: int = 5    # 5-card-majors flag
    # Weak two opening style.
    weak_two_diamonds: bool = True
    weak_two_majors: bool = True
    # Acol strong twos (`open.2d.strong`, `open.2major.strong`): 6+ card suit,
    # 18-22 HCP, forcing for one round.
    strong_two_diamonds: bool = False
    strong_two_majors: bool = False
    # Benjamin twos: 2C strong but passable (22-23 balanced or a strong
    # suit), 2D artificial game force (24+).
    benjamin: bool = False
    weak_two_min_hcp: int = 6
    weak_two_max_hcp: int = 11
    # Precision 1♦ opening; only used when strong_open_call == "1C".
    one_diamond_min_hcp: int = 11
    one_diamond_max_hcp: int = 15
    # Precision 2♣ — long-club opening (Precision) range.
    precision_two_clubs_min_hcp: int = 11
    precision_two_clubs_max_hcp: int = 15
    # Precision 2♦ — 4-4-1-4 / 4-4-0-5 three-suiter (some flavours 4-4-x-x).
    precision_two_diamonds_shape: str = "4-4-1"   # "4-4-1" or "4-3-x"

    # ----- Responses -----
    two_over_one_min_hcp: int = 10  # SAYC: 10+. 2/1 GF systems use 13.
    one_nt_response_min_hcp: int = 6
    one_nt_response_max_hcp: int = 9
    rebid_one_nt_range: Tuple[int, int] = (12, 14)  # opener's 1-1-1NT rebid
    jump_rebid_two_nt_range: Tuple[int, int] = (18, 19)

    # ----- Slam -----
    rkc_variant: str = "1430"     # "1430", "0314" or "classic"

    # ----- Convention set -----
    conventions: Set[str] = field(default_factory=set)

    # Original parsed rules for unmodelled queries.
    raw_rules: Dict[str, SystemFlag] = field(default_factory=dict)

    # Path the system was loaded from (empty when using fallbacks).
    source_path: str = ""

    # --------- Convenience queries -------------------------------------

    def has(self, flag: str) -> bool:
        """True iff a convention flag is enabled in this system."""
        return flag in self.conventions

    def value(self, flag: str, default: Optional[int] = None) -> Optional[int]:
        """Return the numeric .pNN value attached to `flag`, or `default`."""
        rule = self.raw_rules.get(flag)
        return rule.value if (rule is not None and rule.value is not None) else default

    def summary(self) -> str:
        """One-line text summary suitable for status / log lines."""
        return (
            f"{self.name}: 1NT={self.one_nt_min_hcp}-{self.one_nt_max_hcp}, "
            f"strong-open={self.strong_open_call} ({self.strong_open_min_hcp}+), "
            f"2/1≥{self.two_over_one_min_hcp}, RKC={self.rkc_variant}, "
            f"{len(self.conventions)} conventions enabled"
        )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _apply_one_nt_range(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    """The 1NT range: the non-vulnerable setting, else the vulnerable one."""
    mn = rules.get("open.1nt.min_hcp_nv") or rules.get("open.1nt.min_hcp_vul")
    mx = rules.get("open.1nt.max_hcp_nv") or rules.get("open.1nt.max_hcp_vul")
    if mn is not None and mn.value is not None:
        sys_.one_nt_min_hcp = mn.value
    if mx is not None and mx.value is not None:
        sys_.one_nt_max_hcp = mx.value


def _apply_two_nt_range(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    # `open.2nt.range-20-21`: the range is in the name. Any range: Acol's
    # is 21-22 (it used to be read as the default 20-21).
    for key in rules:
        m = re.match(r"open\.2nt\.range-(\d+)-(\d+)$", key)
        if m and rules[key].enabled:
            sys_.two_nt_min_hcp = int(m.group(1))
            sys_.two_nt_max_hcp = int(m.group(2))
            return


def _apply_strong_open(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    # SAYC: open.strong_2c.forcing_to_3major → strong 2♣.
    # Precision: open.strong_1c.min_hcp-16 → strong 1♣ (16+).
    if any(k.startswith("open.strong_1c") for k in rules):
        sys_.strong_open_call = "1C"
        for k in rules:
            m = re.match(r"open\.strong_1c\.min_hcp-(\d+)$", k)
            if m:
                sys_.strong_open_min_hcp = int(m.group(1))
                return
        sys_.strong_open_min_hcp = 16
    else:
        sys_.strong_open_call = "2C"
        # SAYC: 22+ HCP or 8.5 quick tricks — we model the HCP arm. Above
        # the 2NT opening: Acol's 2NT is 21-22, so its 2C starts at 23.
        sys_.strong_open_min_hcp = max(22, sys_.two_nt_max_hcp + 1)


def _apply_two_diamonds(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    if "open.2d.weak" in rules:
        sys_.weak_two_diamonds = True
    elif "open.2d.strong" in rules:
        # Acol strong two (6+ diamonds, 18-22), not a weak two.
        sys_.weak_two_diamonds = False
        sys_.strong_two_diamonds = True
    elif "open.strong_2c.benjamin" in rules:
        # Benjamin (Standard French): 2D is the artificial game force (24+
        # or equivalent), 2C the strong but passable opening.
        sys_.weak_two_diamonds = False
        sys_.benjamin = True
    elif any(k.startswith("open.2d.three_suiter") for k in rules):
        sys_.weak_two_diamonds = False
        for k in rules:
            m = re.match(r"open\.2d\.three_suiter\.majors-(\d)-(\d)$", k)
            if m:
                # "majors-4-3": a four-card and a 3+ card major. We use the
                # larger of the two as the shape descriptor.
                sys_.precision_two_diamonds_shape = f"{m.group(1)}-{m.group(2)}-1"


def _apply_rkc(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    # Acol's `slam.blackwood_classic`: 4NT asks for aces (the reference answered 5D with
    # one ace in 9 of 9 judge records; biq gave 1430's 5C). BIQ_CLASSIC_BW.
    if os.environ.get("BIQ_CLASSIC_BW", "1") == "1" and "slam.blackwood_classic" in rules:
        sys_.rkc_variant = "classic"
        return
    # The variant is the last part of the setting's name.
    for k in rules:
        if k.startswith("slam.rkc."):
            tail = k.split(".")[-1]
            if tail.endswith("1430"):
                sys_.rkc_variant = "1430"
                return
            if tail.endswith("0314"):
                sys_.rkc_variant = "0314"
                return
    # Fallback: a setting named for ace-asking Blackwood means classic
    # Blackwood, aces only (5♣=0/4, 5♦=1, 5♥=2, 5♠=3).
    for r in rules.values():
        nm = (getattr(r, "name", "") or "").lower()
        if "blackwood" in nm and "ace-ask" in nm:
            sys_.rkc_variant = "classic"
            return


def _apply_two_over_one(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    # SAYC and Precision both use `resp.two_over_one.min_hcp-10`.
    for k in rules:
        m = re.match(r"resp\.two_over_one\.min_hcp-(\d+)$", k)
        if m:
            sys_.two_over_one_min_hcp = int(m.group(1))
            return


def _apply_rebid_1nt(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    # `rebid.1nt_after_1_1.width-3`: the rebid covers a 3-HCP range,
    # 12-14.
    for k in rules:
        m = re.match(r"rebid\.2nt_jump\.range-(\d+)-(\d+)$", k)
        if m:
            sys_.jump_rebid_two_nt_range = (int(m.group(1)), int(m.group(2)))
    if sys_.one_nt_max_hcp <= 14:
        # Weak notrump (Acol): a 1NT rebid is the band between the 1NT
        # opening and the 2NT jump rebid (15-16 with 2NT = 17-18).
        sys_.rebid_one_nt_range = (sys_.one_nt_max_hcp + 1,
                                   sys_.jump_rebid_two_nt_range[0] - 1)
        return
    for k in rules:
        m = re.match(r"rebid\.1nt_after_1_1\.width-(\d+)$", k)
        if m:
            width = int(m.group(1))
            sys_.rebid_one_nt_range = (12, 12 + width - 1)
            return


def _apply_major_open_card_min(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    """How many cards a 1♥ / 1♠ opening promises:
      `open.major.five_cards`       → 5 in every seat (SAYC, 2/1, MSF)
      `open.major.five_cards_seats_1_2`   → 5 in 1st/2nd seat, 4 OK in 3rd/4th
      `open.major.five_cards_unpassed`   → same as above
      `open.major.five_cards_always`       → 5 always, strictly enforced
      (missing entirely)              → 4-card majors (Acol family)
    We collapse to a single integer: the strictest minimum the system
    actively requires. The bidder uses this to decide whether to open
    1♥/1♠ on a 4-card holding.
    """
    if "open.major.five_cards" in rules \
            or "open.major.five_cards_always" in rules:
        sys_.one_major_card_min = 5
        return
    if "open.major.five_cards_seats_1_2" in rules \
            or "open.major.five_cards_unpassed" in rules:
        # Effectively 5 except in pass-out seats; the bidder treats this
        # as 5 since seat-aware opener logic isn't yet differentiated.
        sys_.one_major_card_min = 5
        return
    # No 5-card-majors flag — system uses 4-card majors (Acol-style).
    sys_.one_major_card_min = 4


def _apply_one_nt_style(sys_: BiddingSystem, rules: Dict[str, SystemFlag]) -> None:
    sys_.one_nt_allow_five_card_heart = (
        "open.1nt.weak_five_heart" in rules
        or "open.1nt.five_heart" in rules
    )
    sys_.one_nt_allow_five_card_spade = (
        "open.1nt.weak_five_spade" in rules
        or "open.1nt.five_spade" in rules
    )
    sys_.one_nt_allow_six_card_minor = "open.1nt.weak_six_minor" in rules


# biq's own settings for each system: these JSON files are the systems'
# definitions.
_SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "systems"


def _snapshot_path(name: str) -> Path:
    return _SNAPSHOT_DIR / f"{name}.json"


def _build_from_snapshot(name: str) -> Optional[BiddingSystem]:
    import json
    path = _snapshot_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rules = {key: SystemFlag(name=key, enabled=bool(on), value=value)
             for key, on, value in data.get("settings", [])}
    return _build_from_rules(name, rules, data.get("description", ""),
                             str(path))


def _build_from_rules(name: str, rules: Dict[str, SystemFlag], description: str,
                      source_path: str) -> BiddingSystem:
    enabled = {k for k, r in rules.items() if r.enabled}
    sys_ = BiddingSystem(
        name=name,
        description=description,
        conventions=enabled,
        raw_rules=rules,
        source_path=source_path,
    )
    _apply_one_nt_range(sys_, rules)
    _apply_two_nt_range(sys_, rules)
    _apply_strong_open(sys_, rules)
    _apply_two_diamonds(sys_, rules)
    _apply_rkc(sys_, rules)
    _apply_two_over_one(sys_, rules)
    _apply_rebid_1nt(sys_, rules)
    _apply_one_nt_style(sys_, rules)
    _apply_major_open_card_min(sys_, rules)
    _apply_weak_two_majors(sys_, rules)
    _synthesize_convention_aliases(sys_)
    return sys_


def _apply_weak_two_majors(sys_: BiddingSystem,
                           rules: Dict[str, SystemFlag]) -> None:
    """The 2♥ / 2♠ opening style:
      `open.2major.weak`     → weak two (5-10 HCP, 6-card suit)
      `open.2major.strong`   → strong / Acol two (game-forcing-ish)
    Acol uses `open.2major.strong`; bridgeIQ's bidder must not open a weak
    two with it or the auction goes off the rails.
    """
    sys_.weak_two_majors = "open.2major.weak" in rules
    sys_.strong_two_majors = "open.2major.strong" in rules


def _synthesize_convention_aliases(sys_: BiddingSystem) -> None:
    """Add the bidder's alias `resp.major.jacoby_2nt` when a system's
    2NT four-card game-forcing raise (`resp.major.2nt_four_card_gf`) is on,
    so the bidder gates on one name.
    """
    # Jacoby 2NT: a 2NT response shows 4+ support, game forcing.
    if "resp.major.2nt_four_card_gf" in sys_.conventions \
            or "resp.major.2nt_j2nt" in sys_.conventions:
        sys_.conventions.add("resp.major.jacoby_2nt")


# ---------------------------------------------------------------------------
# Catalog (built-in systems)
# ---------------------------------------------------------------------------


# Each entry is (canonical name, the system's code in Q-NET network play,
# fallback factory). The fallback is used when the system file is
# missing, so the bidder stays usable.

def _fallback_sayc() -> BiddingSystem:
    return BiddingSystem(
        name="SAYC",
        description="Standard American Yellow Card (built-in fallback)",
        one_nt_min_hcp=15, one_nt_max_hcp=17,
        two_nt_min_hcp=20, two_nt_max_hcp=21,
        strong_open_call="2C", strong_open_min_hcp=22,
        weak_two_diamonds=True, weak_two_majors=True,
        weak_two_min_hcp=6, weak_two_max_hcp=11,
        two_over_one_min_hcp=10,
        rebid_one_nt_range=(12, 14),
        jump_rebid_two_nt_range=(18, 19),
        rkc_variant="1430",
        conventions=set(_FALLBACK_SAYC_CONVENTIONS),
    )


def _fallback_precision90m() -> BiddingSystem:
    return BiddingSystem(
        name="Precision90M",
        description="Precision Club 90 modern (built-in fallback)",
        # Precision 90 modern's limited 1NT is 14-16.
        one_nt_min_hcp=14, one_nt_max_hcp=16,
        two_nt_min_hcp=20, two_nt_max_hcp=21,
        strong_open_call="1C", strong_open_min_hcp=16,
        weak_two_diamonds=False, weak_two_majors=True,
        weak_two_min_hcp=6, weak_two_max_hcp=11,
        one_diamond_min_hcp=11, one_diamond_max_hcp=15,
        precision_two_clubs_min_hcp=11, precision_two_clubs_max_hcp=15,
        precision_two_diamonds_shape="4-4-1",
        two_over_one_min_hcp=10,
        rebid_one_nt_range=(12, 14),
        jump_rebid_two_nt_range=(18, 19),
        rkc_variant="1430",
        conventions=set(_FALLBACK_PRECISION_CONVENTIONS),
    )


def _fallback_two_over_one() -> BiddingSystem:
    """SAYC variant where new-suit-at-2-level is game-forcing.

    The 2/1 threshold is `resp.two_over_one.min_hcp-11` (game-forcing
    strength). Other openings and conventions match SAYC closely. Used by wBridge5's "Wbridge5" preset and bb12's "2/1".
    """
    s = _fallback_sayc()
    s.name = "TwoOverOne"
    s.description = "2/1 game forcing (built-in fallback)"
    s.two_over_one_min_hcp = 11  # game forcing
    return s


def _fallback_acol() -> BiddingSystem:
    """4-card-majors Acol with weak 1NT.

    1NT is 12-14 balanced. 1♥/1♠ may be opened
    on 4 cards in any seat. Strong 2♣ is the only forcing 2-bid;
    2♦/2♥/2♠ are typically used as Benjaminized weak / Acol weak twos.
    Used by bb12's "ACOL" preset.
    """
    s = _fallback_sayc()
    s.name = "StandardAcol"
    s.description = "standard Acol (built-in fallback)"
    s.one_nt_min_hcp = 12
    s.one_nt_max_hcp = 14
    s.one_major_card_min = 4
    s.two_over_one_min_hcp = 9
    return s


def _fallback_standard_french() -> BiddingSystem:
    """Modern French / SEF.

    Mostly SAYC-shaped: 5-card majors, strong
    1NT 15-17, weak 2-bids, 2/1 = 10+ HCP. Differences are mostly in
    response handling (some French-specific cuebid agreements not in
    SAYC). Used by wBridge5's "SEF" and bb12's "La Majeure 5eme".
    """
    s = _fallback_sayc()
    s.name = "StandardFrench"
    s.description = "modern Standard French (built-in fallback)"
    return s


# Short convention list for the fallback systems. The system files hold
# the full set; these are the headline conventions the bidder implements.
_FALLBACK_SAYC_CONVENTIONS = {
    "nt.stayman", "nt.transfers.always",
    "nt.texas",
    "resp.major.splinters", "resp.major.truscott_2nt", "resp.major.jacoby_2nt",
    "resp.strong_2c.2d_negative",
    "comp.negative_double.through-2S",
    "gen.new_minor_forcing", "gen.fourth_suit_forcing",
    "def.michaels", "def.unusual_nt", "def.1nt.landy",
    "def.weak_jump_overcalls.all_suits",
    "slam.rkc.1430", "slam.gerber",
    "open.2nt.range-20-21",
    "open.2major.weak", "open.2d.weak",
}


_FALLBACK_PRECISION_CONVENTIONS = (_FALLBACK_SAYC_CONVENTIONS | {
    "open.strong_1c.min_hcp-16",
    "resp.strong_1c.switch_1h_1nt",
    "open.2d.three_suiter.majors-4-3",
    "def.strong_1c.double_majors",
}) - {"resp.strong_2c.2d_negative", "open.2d.weak"}


_CATALOG: List[Tuple[str, str, "callable"]] = [
    ("SAYC",            "A-SAYC-I", _fallback_sayc),
    ("TwoOverOne",      "A-2-1-A",  _fallback_two_over_one),
    ("StandardAcol",    "B-ACL-S",  _fallback_acol),
    ("StandardFrench",  "F-FRA-M",  _fallback_standard_french),
    ("Precision90M",    "P-P90M-A", _fallback_precision90m),
]


# Aliases so the config can keep using "Precision" or "SAYC" generically.
_ALIASES = {
    "Precision": "Precision90M",
    "Precision Club": "Precision90M",
    "Precision Club 90 modern": "Precision90M",
    "Standard American": "SAYC",
    "Std. Amer.": "SAYC",
    "2/1": "TwoOverOne",
    "SEF": "StandardFrench",
    "ACOL": "StandardAcol",
    "Acol": "StandardAcol",
    "La Majeure 5eme": "StandardFrench",
    "La Majeure Cinquieme": "StandardFrench",
}


_SYSTEM_CACHE: Dict[str, BiddingSystem] = {}


def list_systems() -> List[str]:
    """Canonical names available to the rule-based bidder."""
    return [name for name, _, _ in _CATALOG]


def system_name_for_qplus_code(code: str) -> Optional[str]:
    """Map a Q-Plus bidding-system code — what its Q-NET `set_config` reports
    as ``conv.bidding.N/S = A-SAYC-I`` — to the canonical biq system name.
    Exact match on the catalog's codes first (A-SAYC-I, A-2-1-A, B-ACL-S,
    F-FRA-M, P-P90M-A), then a family heuristic so a Q-Plus variant we don't
    ship (e.g. another Precision file) still lands on the nearest system.
    Returns None when nothing fits, so the caller can keep its current system."""
    if not code:
        return None
    key = str(code).strip().upper()
    for name, code_, _ in _CATALOG:
        if code_.upper() == key:
            return name
    if key in _ALIASES:
        return _ALIASES[key]
    if "SAYC" in key:
        return "SAYC"
    if "2-1" in key or "2/1" in key or "TWO" in key:
        return "TwoOverOne"
    if "ACL" in key or "ACOL" in key:
        return "StandardAcol"
    if "FRA" in key or "FRENCH" in key:
        return "StandardFrench"
    if key.startswith("P-") or "P90" in key or "PREC" in key:
        return "Precision90M"
    return None


def get_system(name: str) -> BiddingSystem:
    """Look up a bidding system by canonical name or alias.

    Loads biq's file for the system and caches the result.
    Falls back to a hand-coded approximation when the file is missing
    so the bidder always returns *something* defensible.
    """
    canonical = _ALIASES.get(name, name)
    if canonical in _SYSTEM_CACHE:
        return _SYSTEM_CACHE[canonical]

    for cname, fname, fallback in _CATALOG:
        if cname == canonical:
            # biq's own system file.
            sys_ = _build_from_snapshot(cname) or fallback()
            _SYSTEM_CACHE[cname] = sys_
            return sys_

    # Unknown — fall back to SAYC so callers always get a usable spec.
    return get_system("SAYC")
