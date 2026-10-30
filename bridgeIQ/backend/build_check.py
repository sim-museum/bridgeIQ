"""Build integrity check, run before biq starts (main.py) or by hand:

    python3 main.py --check

A broken build must not play: a missing or stale file, a module loaded from
another copy of biq, or an engine falling back to a weaker substitute makes
biq bid and play "super weird" with no visible error. So the app stops with a
message naming each problem instead.

Checks:
  * every file in BUILD_MANIFEST.json is present and matches its SHA-256
    (a git checkout may differ only in files git shows as locally edited);
  * every loaded biq module comes from this install, not another copy;
  * Python, PyQt6 + QtSvg, numpy and the DDS library are there;
  * each bidding system loads from biq's own file (not the fallback) and
    opens two known hands correctly; DDS solves a known deal; the card
    engine returns a legal opening lead.

tools/write_build_manifest.py rewrites the manifest; run it before every
commit (test_build_integrity.py fails while it is stale).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

APP_DIR = Path(__file__).resolve().parent.parent
MANIFEST = APP_DIR / "BUILD_MANIFEST.json"

# What the manifest covers (relative to APP_DIR).
MANIFEST_GLOBS = [
    "main.py", "logging_config.py", "text_mode.py",
    "backend/**/*.py", "backend/data/**/*",
    "ui/**/*.py", "ui/assets/deck/svg/*", "ui/assets/icons/*",
]

MIN_PYTHON = (3, 10)


def manifest_files() -> List[Path]:
    out = set()
    for pattern in MANIFEST_GLOBS:
        for p in APP_DIR.glob(pattern):
            if p.is_file() and "__pycache__" not in p.parts:
                out.add(p)
    return sorted(out - _git_ignored(out))


def _git_ignored(paths) -> set:
    """Of `paths`, the ones git ignores in this checkout (local developer
    files that are never published); empty outside a git checkout."""
    try:
        r = subprocess.run(["git", "-C", str(APP_DIR), "check-ignore", "--stdin"],
                           input="\n".join(str(p) for p in paths),
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return set()
    return {Path(line) for line in r.stdout.splitlines() if line}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git(*args) -> Tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(APP_DIR), *args],
                           capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def git_locally_modified() -> set:
    """Paths (relative to APP_DIR) that git shows as edited or new here, or
    None when this is not a git checkout."""
    rc, top = _git("rev-parse", "--show-toplevel")
    if rc != 0:
        return None
    rc, out = _git("status", "--porcelain", "--untracked-files=all", "--", ".")
    if rc != 0:
        return None
    prefix = os.path.relpath(APP_DIR, top.strip())
    mods = set()
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        rel = os.path.relpath(path, prefix) if prefix != "." else path
        mods.add(rel)
    return mods


def check_files() -> Tuple[List[str], List[str]]:
    """(problems, notes) from comparing the tree with BUILD_MANIFEST.json."""
    problems, notes = [], []
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"BUILD_MANIFEST.json is missing or unreadable ({e})."], notes
    files = manifest.get("files", {})
    edited = git_locally_modified()
    missing, skewed, dev_edits = [], [], []
    for rel, digest in files.items():
        p = APP_DIR / rel
        if not p.is_file():
            missing.append(rel)
        elif sha256(p) != digest:
            if edited is not None and rel in edited:
                dev_edits.append(rel)
            else:
                skewed.append(rel)
    if missing:
        problems.append(f"{len(missing)} file(s) missing: "
                        + ", ".join(missing[:8])
                        + (" …" if len(missing) > 8 else ""))
    if skewed:
        problems.append(
            f"{len(skewed)} file(s) differ from build "
            f"{manifest.get('version', '?')} (version skew: a partial update "
            f"or files from another version): " + ", ".join(skewed[:8])
            + (" …" if len(skewed) > 8 else ""))
    if dev_edits:
        notes.append(f"development build: {len(dev_edits)} locally edited "
                     f"file(s) differ from build {manifest.get('version', '?')}")
    return problems, notes


def check_module_origins() -> List[str]:
    """Every loaded biq module must come from this install."""
    problems = []
    for name, mod in list(sys.modules.items()):
        if not (name == "backend" or name.startswith("backend.")
                or name == "ui" or name.startswith("ui.")):
            continue
        f = getattr(mod, "__file__", None)
        if f and APP_DIR not in Path(f).resolve().parents:
            problems.append(f"module {name} was loaded from {f}, not from "
                            f"{APP_DIR} (two copies of biq on the Python path)")
    return problems


def check_dependencies() -> List[str]:
    problems = []
    if sys.version_info < MIN_PYTHON:
        problems.append(f"Python {sys.version.split()[0]} is too old "
                        f"(need {'.'.join(map(str, MIN_PYTHON))}+).")
    for mod, why, fix in (
            ("PyQt6.QtWidgets", "the user interface", "pip install PyQt6"),
            ("PyQt6.QtSvg", "the card faces",
             "pip install PyQt6, or apt install python3-pyqt6.qtsvg"),
            ("numpy", "the engines", "pip install numpy")):
        try:
            __import__(mod)
        except Exception as e:
            problems.append(f"{mod} can't be imported ({e}); needed for "
                            f"{why} ({fix}).")
    return problems


def check_engines() -> List[str]:
    """Smoke tests: the systems, the bidder, DDS and the card engine."""
    problems = []
    try:
        from backend import bidding_systems as bs
        for name in bs.list_systems():
            spec = bs.get_system(name)
            if not spec.source_path.endswith(f"systems/{name}.json"):
                problems.append(f"bidding system {name} did not load from "
                                f"backend/data/systems/{name}.json (fallback "
                                f"in use).")
            elif len(spec.conventions) < 20:
                problems.append(f"bidding system {name} has only "
                                f"{len(spec.conventions)} conventions on.")
    except Exception as e:
        problems.append(f"bidding systems failed to load: {e!r}")
        return problems

    deals = {
        "16 HCP balanced": "N:AQ5.KJ4.Q976.A82 KJ2.Q873.KJ5.J65 "
                           "T98.AT9.T843.KQ4 7643.652.A2.T973",
        "19 HCP": "N:AKQ5.KJ4.Q97.A82 J2.Q873.KJ5.J765 "
                  "T98.AT9.T8432.KQ 7643.652.A6.T943",
    }
    expected = {
        "SAYC": ("1NT", "1C"), "TwoOverOne": ("1NT", "1C"),
        "StandardAcol": ("1D", "1S"), "StandardFrench": ("1NT", "1C"),
        "Precision90M": ("1NT", "1C"),
    }
    try:
        from backend.models import BoardState, Seat
        from backend.native_bidder import NativeBiddingEngine
        for name, want in expected.items():
            nb = NativeBiddingEngine(system=name)
            for (label, deal), call in zip(deals.items(), want):
                b = BoardState.from_pbn_deal(deal, 1)
                b.dealer = Seat.NORTH
                got = nb.get_bid(b, Seat.NORTH).action.to_str()
                if got != call:
                    problems.append(f"bidder ({name}) opened {got} with the "
                                    f"{label} test hand; expected {call}.")
    except Exception as e:
        problems.append(f"the bidder failed its smoke test: {e!r}")

    try:
        from backend.dds import DDSolver
        tab = DDSolver().solve_dd_table(
            "N:AKQJT98765432... .AKQJT98765432.. "
            "..AKQJT98765432. ...AKQJT98765432")
        if tab["N"]["S"] != 13 or tab["N"]["NT"] != 0:
            problems.append("the double-dummy solver gave wrong answers on a "
                            "known deal.")
    except Exception as e:
        problems.append(f"the double-dummy solver (libdds) failed: {e!r}")

    try:
        from backend import nopeek
        from backend.models import BoardState, Seat, Contract, Suit
        b = BoardState.from_pbn_deal(deals["16 HCP balanced"], 1)
        b.contract = Contract(level=3, suit=Suit.NOTRUMP, declarer=Seat.NORTH)
        card = nopeek.decide(b, Seat.EAST, [])
        if card is None or card not in b.hands[Seat.EAST].cards:
            problems.append("the card engine did not return a legal opening "
                            "lead.")
    except Exception as e:
        problems.append(f"the card engine failed its smoke test: {e!r}")
    return problems


def run_all() -> Tuple[List[str], List[str]]:
    """(problems, notes). Any problem means biq must not start."""
    t = time.time()
    problems = check_dependencies()
    files, notes = check_files()
    problems += files
    if not problems:
        problems += check_engines()
    problems += check_module_origins()
    notes.append(f"build check took {time.time() - t:.1f} s")
    return problems, notes
