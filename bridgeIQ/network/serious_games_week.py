"""Serious Games Week matchmaker integration for bridgeIQ.

Serious Games Week is an iGOR-style lobby the PLAYER chooses: `sgw url http://<matchmaker>:8090` (or $SGW_URL). bridgeIQ talks to it
through the `sgw` client (github.com/sim-museum/serious-games-week), never directly, the same way MiG Alley, Battle of Britain and
FreeFalcon do:

  hosting -> `sgw announce --game bridgeiq ...` runs for as long as the table is open; its stdin is a pipe we hold, so
             the listing is withdrawn when the table closes or bridgeIQ exits. Bridge is Friday's game: on any other day
             the matchmaker refuses the listing (the table still runs for players who know the address), and
             `last_message` says why.
  joining -> `sgw list --game bridgeiq --json` gives the open tables for the join dialog.

Everything here is inert unless a matchmaker is configured. SGW_OFF=1 disables it.
"""
import json
import os
import shutil
import subprocess
import sys
import threading

GAME_ID = "bridgeiq"


def configured():
    if os.environ.get("SGW_OFF"):
        return False
    if os.environ.get("SGW_URL"):
        return True
    for path in ("~/.config/sgw/url", "~/.config/sgweek/url"):   # second: read-only fallback, setups before 2026-10-05
        try:
            with open(os.path.expanduser(path)) as f:
                return bool(f.readline().strip())
        except OSError:
            continue
    return False


def find_sgw():
    """The sgw command line: $SGW_BIN, the AppImage's own, ~/serious-games-week/sgw.py, or `sgw` on PATH."""
    cands = [os.environ.get("SGW_BIN")]
    if os.environ.get("APPDIR"):
        cands.append(os.path.join(os.environ["APPDIR"], "usr", "bin", "sgw"))
    cands += [os.path.expanduser("~/serious-games-week/sgw.py")]
    for c in cands:
        if c and os.path.isfile(c):
            return [sys.executable, c] if c.endswith(".py") else [c]
    p = shutil.which("sgw")
    return [p] if p else None


class Announcer:
    """Keeps one hosted table listed on the matchmaker while it is open."""

    def __init__(self):
        self.proc = None
        self.last_message = ""

    def start(self, port, title, name="", max_players=0, version=""):
        if self.proc or not configured():
            return False
        cmd = find_sgw()
        if not cmd:
            self.last_message = "Serious Games Week: no sgw client found"
            return False
        args = cmd + ["announce", "--game", GAME_ID, "--port", str(int(port)), "--title", title or "Bridge table",
                      "--max", str(int(max_players or 0))]
        if name:
            args += ["--name", name]
        if version:
            args += ["--version", str(version)]
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            self.last_message = "Serious Games Week: %s" % e
            return False
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self.last_message = line
                print("[sgw] " + line, flush=True)

    def stop(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()          # sgw withdraws the listing and exits
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        self.proc = None


def list_tables(timeout=5):
    """Open bridgeIQ tables on the matchmaker: [{host, port, title, name, players, max_players, ...}], or []."""
    if not configured():
        return []
    cmd = find_sgw()
    if not cmd:
        return []
    try:
        out = subprocess.run(cmd + ["list", "--game", GAME_ID, "--json"], capture_output=True, text=True,
                             timeout=timeout)
        return json.loads(out.stdout or "[]") if out.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
