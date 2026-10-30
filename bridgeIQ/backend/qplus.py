"""Q-Plus Bridge availability and BDL log discovery.

Used by the closed-room ingest flow so menu items can grey themselves out
when Q-Plus isn't installed and the file picker can default to the BDL log
directory if it is.
"""

import os
import shutil
from pathlib import Path
from typing import Optional


def _wineprefix() -> Path:
    """The Wine prefix that the FRI scripts install Q-Plus into."""
    env = os.environ.get("WINEPREFIX")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "qplus.sh").is_file():
            return parent / "WP"
    return Path.home() / ".wine"


def qplus_install_dir() -> Optional[Path]:
    """Path of the installed Q-Plus directory, or None if not installed.

    Q-Plus 17.1 lands in `qbridge17`, the older 15 in `qbridge15`. Caller
    decides what to do with the bare existence check (e.g. enable a menu).
    """
    wp = _wineprefix()
    for name in ("qbridge17", "qbridge15"):
        candidate = wp / "drive_c" / "games" / name
        if candidate.is_dir():
            return candidate
    return None


def qplus_bdl_log_dir() -> Optional[Path]:
    """Directory Q-Plus writes its BDL game logs into."""
    install = qplus_install_dir()
    if install is None:
        return None
    log_dir = install / "DATA" / "LOG"
    if log_dir.is_dir():
        return log_dir
    return install


def qplus_available() -> bool:
    """True iff a closed-room ingest can plausibly succeed.

    Doesn't check `wine` itself — Q-Plus is the consumer, not us. If the
    user manually copied a BDL elsewhere, the ingest still works via a
    plain file picker; this flag only governs *menu enablement* and the
    file-dialog default directory.
    """
    return qplus_install_dir() is not None


def newest_bdl_path() -> Optional[Path]:
    """Most recently modified BDL in the Q-Plus log dir, or None.

    Convenience for the post-game flow: when the user finishes a Q-Plus
    closed room, the newest log is almost always the one to ingest.
    """
    log_dir = qplus_bdl_log_dir()
    if log_dir is None:
        return None
    candidates = sorted(
        (p for p in log_dir.glob("*.bdl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None
