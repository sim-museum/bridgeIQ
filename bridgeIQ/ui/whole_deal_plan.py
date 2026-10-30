"""No-peek whole-deal 13-trick plan, for declarer OR a defender.

Produces a short, structured overall line of play from ONLY what the asking
side legitimately sees:

  * declarer plans across its own hand + dummy (26 cards — legitimate once
    dummy is down). Returns None if both those hands aren't face-up.
  * a defender plans from its own hand + (after the lead) dummy. It never sees
    partner's or declarer's hidden cards.

NO PEEKING, EVER. Every fact comes from `known_layout(board, visible)` and the
`visible` set; this module never indexes `board.hands[s]` for a hidden seat. It
reuses the no-peek analytical helpers already proven in the instrumentation
view (`ui/teaching_view.py`) rather than re-deriving them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from backend.models import Seat, Suit, Rank, Contract, BoardState

# Reuse the instrumentation view's no-peek analytics (all `visible`-driven).
from ui.teaching_view import (
    SUIT_ROWS, known_layout, side_top_tricks, suit_loser_ranks,
    winners_in_hand, losing_trick_count, holdup_rule_of_seven, danger_info,
    finesse_hints, boss_rank, _holder_of, split_odds,
)


@dataclass
class PlanStep:
    kind: str                       # draw_trumps|establish|finesse|holdup|
                                    # entry|ruff|cash|count|danger|lead_philosophy
    text: str
    suit: Optional[Suit] = None


@dataclass
class WholeDealPlan:
    perspective: str                # "declarer" | "defender"
    target: int                     # tricks this side needs
    trump: Optional[Suit] = None
    top_tricks: Optional[int] = None
    losers: Optional[int] = None
    steps: List[PlanStep] = field(default_factory=list)

    def headline(self) -> str:
        if self.perspective == "declarer":
            bits = [f"need {self.target}"]
            if self.top_tricks is not None:
                bits.append(f"{self.top_tricks} on top")
            if self.losers is not None:
                bits.append(f"~{self.losers} losers")
            return "Declarer plan — " + ", ".join(bits) + "."
        return f"Defenders' plan — need {self.target} tricks to beat it."


def _combined_losers(declarer, dummy, visible, layout) -> Optional[int]:
    """Declaring-side losers ≈ losers of the longer holding per suit (the short
    hand's spare low cards get ruffed/pitched). Mirrors the Plan panel."""
    rem = layout["rem_ranks"]
    total = 0
    seen = False
    for su in SUIT_ROWS:
        holds = [layout["known"][hs][su] for hs in (declarer, dummy)
                 if hs is not None and hs in visible]
        if not holds:
            continue
        seen = True
        longer = max(holds, key=len)
        total += len(suit_loser_ranks(longer, rem[su])
                     - winners_in_hand(longer, rem[su]))
    return total if seen else None


def _longest_combined_suit(declarer, dummy, layout):
    """Suit where declarer+dummy hold the most cards (length to establish)."""
    best, best_len = None, 0
    for su in SUIT_ROWS:
        n = len(layout["known"][declarer][su]) + len(layout["known"][dummy][su])
        if n > best_len:
            best, best_len = su, n
    return best, best_len


def _declarer_steps(board, contract, declarer, dummy, visible, layout, trump):
    steps: List[PlanStep] = []
    rem = layout["rem_ranks"]

    if trump is None:
        steps.append(PlanStep(
            "count", "No-trump: count sure winners, then build the rest in "
            "your longest combined suit before cashing."))
        su, n = _longest_combined_suit(declarer, dummy, layout)
        if su is not None and n >= 6:
            steps.append(PlanStep(
                "establish", f"Develop {su.symbol()} ({n} together) for length "
                f"tricks — knock out the high cards while you hold entries.",
                suit=su))
        hu = holdup_rule_of_seven(board, contract, declarer, dummy, visible, layout)
        if hu is not None and hu["n_total"] > 0:
            steps.append(PlanStep(
                "holdup", f"Hold up {hu['suit'].symbol()}A (Rule of 7: duck "
                f"{hu['n_total']}, {hu['remaining']} to go) to cut the defence's "
                f"link.", suit=hu["suit"]))
    else:
        # Suit contract — the trump questions first (Reese §62).
        held = (len(layout["known"][declarer][trump]) +
                len(layout["known"][dummy][trump]))
        out = max(0, len(rem[trump]) - held)
        boss_t = boss_rank(rem, trump)
        holder = _holder_of(layout, trump, boss_t, visible)
        opp_has_master = (boss_t is not None and holder is not None
                          and holder not in (declarer, dummy))
        if opp_has_master:
            steps.append(PlanStep(
                "draw_trumps", f"An opponent holds the master trump "
                f"({trump.symbol()}{boss_t.to_char()}) — keep trump control; "
                f"don't blindly draw all {out}.", suit=trump))
        else:
            odds = split_odds(out) if out else None
            tail = (f" — {odds[0][0]} likely ({odds[0][1]}%)"
                    if odds else "")
            steps.append(PlanStep(
                "draw_trumps", f"Shall I draw trumps? {out} out{tail}. Draw "
                f"them unless you need a ruff or an entry first.", suit=trump))
        # Ruffing / length after the trump decision.
        su, n = _longest_combined_suit(declarer, dummy, layout)
        if su is not None and su != trump and n >= 7:
            steps.append(PlanStep(
                "establish", f"Side length in {su.symbol()} ({n}) — set it up "
                f"and use trumps as entries/ruffs.", suit=su))

    # Hold-up already covered for NT; danger applies to both.
    dgr = None
    try:
        dgr = danger_info(board, contract, declarer, visible, layout, {})
    except Exception:
        dgr = None
    if dgr is not None:
        ds = dgr["seat"].to_char()
        if dgr.get("mode") == "ruff":
            steps.append(PlanStep(
                "danger", f"Danger: {ds} is void in {dgr['threat'].symbol()} "
                f"and can ruff — draw trumps before conceding the lead."))
        else:
            steps.append(PlanStep(
                "danger", f"Keep {ds} off lead (can run "
                f"{dgr['threat'].symbol()}); don't finesse into {ds}."))

    for h in finesse_hints(declarer, dummy, visible, layout):
        steps.append(PlanStep("finesse", h))

    # Entry awareness — suits where a hand holds the current master.
    entries = []
    for hand_seat, tag in ((declarer, "your hand"), (dummy, "dummy")):
        suits = [su.symbol() for su in SUIT_ROWS
                 if (b := boss_rank(rem, su)) is not None
                 and b in layout["known"][hand_seat][su]]
        if suits:
            entries.append(f"{tag}: {''.join(suits)}")
    if entries:
        steps.append(PlanStep("entry", "Sure entries — " + "; ".join(entries) +
                              ". Cash from the short hand first."))
    return steps


def _defender_steps(board, contract, seat, dummy, visible, layout, trump,
                    set_target):
    steps: List[PlanStep] = []
    dummy_seen = dummy is not None and dummy in visible
    if trump is None:
        steps.append(PlanStep(
            "lead_philosophy", "Defending no-trump: establish your (or "
            "partner's) longest suit — fourth-best from length, top of a "
            "sequence. Count declarer's tricks and hold up your stoppers."))
    else:
        steps.append(PlanStep(
            "lead_philosophy", "Defending a suit contract: cash or set up "
            "winners before they're discarded; consider a forcing lead or a "
            "ruff. Lead trumps if declarer is cross-ruffing."))
    if dummy_seen:
        # Something specific from the one hidden-side hand we may legitimately
        # see — dummy's threat suits to attack or avoid.
        long_dummy = max(SUIT_ROWS, key=lambda su: len(layout["known"][dummy][su]))
        if len(layout["known"][dummy][long_dummy]) >= 5:
            steps.append(PlanStep(
                "establish", f"Dummy's long suit is {long_dummy.symbol()} "
                f"({len(layout['known'][dummy][long_dummy])}) — attack entries "
                f"or force it before that suit runs.", suit=long_dummy))
    steps.append(PlanStep(
        "count", f"You need {set_target} tricks to set it — count partner's "
        f"likely cards from the bidding and signals; signal honestly to share "
        f"the count."))
    return steps


def whole_deal_plan(board: BoardState, seat: Optional[Seat],
                    declarer: Optional[Seat], dummy: Optional[Seat],
                    contract: Optional[Contract], visible: Set[Seat],
                    layout=None) -> Optional[WholeDealPlan]:
    """Build a no-peek 13-trick plan from the `seat`'s perspective.

    Returns None when the legitimate information isn't available yet (no
    contract, declarer/dummy not both face-up for a declarer plan, etc.) —
    never peeks at hidden hands to fill the gap.
    """
    if contract is None or declarer is None:
        return None
    visible = set(visible or set())
    if layout is None:
        layout = known_layout(board, visible)
    trump = contract.suit if contract.suit != Suit.NOTRUMP else None
    target = contract.target_tricks()                # 6 + level

    declarer_side = (seat is None) or (seat.is_ns() == declarer.is_ns())
    if declarer_side:
        if dummy is None or declarer not in visible or dummy not in visible:
            return None                              # would have to peek
        plan = WholeDealPlan(
            perspective="declarer", target=target, trump=trump,
            top_tricks=side_top_tricks(board, (declarer, dummy), visible, layout),
            losers=_combined_losers(declarer, dummy, visible, layout),
            steps=_declarer_steps(board, contract, declarer, dummy, visible,
                                  layout, trump))
        return plan

    # Defender perspective — need at least the asking defender's own hand.
    if seat is not None and seat not in visible:
        return None
    set_target = 14 - target                         # tricks to beat the contract
    return WholeDealPlan(
        perspective="defender", target=set_target, trump=trump,
        steps=_defender_steps(board, contract, seat, dummy, visible, layout,
                              trump, set_target))


def render_whole_deal_plan(plan: Optional[WholeDealPlan], html: bool = False
                           ) -> str:
    """Short rendering: headline + numbered steps. `html` joins with <br> and
    bolds the headline for the instrumentation panel; otherwise plain text."""
    if plan is None:
        return ""
    sep = "<br>" if html else "\n"
    head = f"<b>{plan.headline()}</b>" if html else plan.headline()
    lines = [head]
    for i, st in enumerate(plan.steps, 1):
        lines.append(f"{i}. {st.text}")
    return sep.join(lines)
