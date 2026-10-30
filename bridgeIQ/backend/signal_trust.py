"""Partner SIGNAL-TRUST — escalating feedback + auto-disable for signal reading.

biq reads partner's signals (signal_read) to sharpen its defence; that only helps
if partner signals reliably. A biq partner does. A HUMAN may not. After each hand
we check partner's ACTUAL play for MIS-SIGNALS (cards the standard convention
would NOT have produced from their real hand) and escalate:

  TRUSTING  read partner's signals (default).
  COMPLAIN  a slip: a gentle after-hand note.
  WARN      it keeps happening: an explicit warning.
  DISABLED  still happening: STOP reading partner's signals + raise a flag the
            UI shows, so the user knows biq no longer trusts their carding.

A clean signalling hand decays the pressure, so a one-off slip is forgiven, and a
reformed partner earns trust back. State is per-session; call reset() per match.
"""
from __future__ import annotations
from typing import Callable, List, Optional, Tuple

from . import signal_read

# pressure thresholds
_WARN_AT = 3.0
_DISABLE_AT = 5.0

_RANKS = "AKQJT98765432"
_SUIT_SYM = {0: "♠", 1: "♥", 2: "♦", 3: "♣"}  # S H D C


def _card(c52: int) -> str:
    return _SUIT_SYM[c52 // 13] + _RANKS[c52 % 13]


class SignalTrust:
    def __init__(self, on_message: Optional[Callable[[str], None]] = None):
        self.pressure = 0.0
        self.disabled = False
        self.on_message = on_message
        self.total_mis = 0
        self.hands = 0
        self.last_mis: List[dict] = []

    def reset(self):
        self.pressure = 0.0
        self.disabled = False
        self.total_mis = 0
        self.hands = 0

    def reading_active(self) -> bool:
        """Should biq read partner's signals right now?"""
        return not self.disabled

    def status_flag(self) -> Optional[str]:
        """A short banner for the UI when biq has stopped trusting partner."""
        return "⛔ Not reading partner signals (unreliable carding)" \
            if self.disabled else None

    def review_hand(self, records: List[dict],
                    partner_actual_remaining: set) -> Tuple[str, List[str]]:
        """Call once per completed hand. `records` = signal_read.read(...) over
        the finished deal; `partner_actual_remaining` = the empty set at end of
        play (partner's holding is reconstructed from its plays). Returns
        (level, messages) and emits messages via on_message."""
        self.hands += 1
        mis = signal_read.mis_signals(records, partner_actual_remaining)
        self.last_mis = list(mis)
        msgs: List[str] = []
        if mis:
            self.total_mis += len(mis)
            self.pressure += len(mis)
        elif records:
            self.pressure = max(0.0, self.pressure - 1.0)   # clean hand forgives

        was_disabled = self.disabled
        if self.pressure >= _DISABLE_AT:
            self.disabled = True
        elif self.disabled and self.pressure == 0.0:
            self.disabled = False                            # reformed — trust back
            msgs.append("✅ Your signals look reliable again — biq will read "
                        "them once more.")

        if mis:
            detail = "; ".join(
                f"trick {m['trick'] + 1} you played {_card(m['played52'])}, "
                f"standard is {_card(m['expected52'])}" for m in mis[:3])
            if self.disabled and not was_disabled:
                level = "DISABLED"
                msgs.append(f"⛔ Signals off: too many mis-signals "
                            f"({self.total_mis}). biq will stop reading your "
                            f"carding until it's reliable. Latest — {detail}.")
            elif self.pressure >= _WARN_AT:
                level = "WARN"
                msgs.append(f"⚠ Watch your signals — {detail}. If this keeps "
                            f"up biq will stop trusting them.")
            else:
                level = "COMPLAIN"
                msgs.append(f"Note: {detail}. (Standard attitude/count signals.)")
        else:
            level = "DISABLED" if self.disabled else "TRUSTING"

        for m in msgs:
            if self.on_message:
                self.on_message(m)
        return level, msgs


# Module-level singleton used by the engine gate + live client.
_TRUST = SignalTrust()


def reset(on_message: Optional[Callable[[str], None]] = None):
    _TRUST.on_message = on_message
    _TRUST.reset()


def reading_active() -> bool:
    return _TRUST.reading_active()


def status_flag() -> Optional[str]:
    return _TRUST.status_flag()


def review_hand(records, partner_actual_remaining):
    return _TRUST.review_hand(records, partner_actual_remaining)


def last_mis_signals() -> List[dict]:
    """The mis-signals found on the last reviewed hand (for the UI)."""
    return list(_TRUST.last_mis)
