"""Dynamic bidding-flowchart generator (Bridge Baron 12 idea #2).

Generates a plantuml flowchart of the decision-tree the bidder is
walking right now, with the actual path your hand took highlighted.
Customised per (system, auction-position) rather than serving a
generic encyclopedia entry.

Public API:
  flowchart_for(board, seat, system) → (puml_string, commentary)

The PNG is produced by piping `puml_string` through the system
`plantuml` binary. The UI (ui.bidding_flowchart_dialog) handles
that and displays the image in a modal dialog.

Decision-tree coverage (initial):
  * Pre-bid (no auction yet, dealer to bid) — "Deciding on an
    Opening Bid".
  * First response (partner opened, this is responder's first
    call) — "Responding to an Opening Bid".

More contexts (opener rebid, slam machinery, etc.) can be added
as nodes; the renderer is content-agnostic.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .models import BoardState, Seat, Suit, Bid
from .native_bidder import evaluate_hand, HandEval


# ---------------------------------------------------------------------------
# Decision-tree primitive
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """One decision-tree node.

    A leaf has `bid_label` set; intermediate nodes have a `predicate`
    (returns True/False from a HandEval) and two children.

    `label` is the question or the bid shown in the diagram.
    """
    label: str
    predicate: Optional[Callable[[HandEval], bool]] = None
    yes_child: Optional["Node"] = None
    no_child: Optional["Node"] = None
    bid_label: Optional[str] = None   # leaf: the bid this node decides
    commentary: str = ""

    def is_leaf(self) -> bool:
        return self.bid_label is not None


# ---------------------------------------------------------------------------
# Trees for specific situations
# ---------------------------------------------------------------------------

def _opening_tree_sayc(system_min_nt: int = 15,
                       system_max_nt: int = 17) -> Node:
    """Decision tree for opening, SAYC-style (5cM, 15-17 NT, 22+ 2C).

    Reflects the high-level rules in native_bidder._open_sayc — not
    every nuance, but the path a learner can follow.
    """
    leaf = lambda lab, why: Node(label=lab, bid_label=lab,
                                 commentary=why)

    # Leaves first.
    pass_leaf = leaf(
        "Pass",
        "Not enough strength for an opening — under ~12 HCP and no "
        "shapely hand for a preempt or weak two.")
    strong_2c = leaf(
        "2♣ (artificial)",
        "22+ HCP balanced, or unbalanced hand with 9+ playing "
        "tricks. Strong, artificial, game-forcing unless 2C-2D-2NT.")
    nt_1 = leaf(
        f"1NT",
        f"{system_min_nt}-{system_max_nt} HCP balanced — flat shape, "
        "no singleton/void, at most one 5-card suit.")
    nt_2 = leaf(
        "2NT",
        "20-21 HCP balanced — too strong for 1NT but not 2♣-strong.")
    one_major = leaf(
        "1 of major",
        "12+ HCP with 5+ cards in the major. Major suits are bid "
        "first because game in a major (10 tricks) is easier than "
        "game in a minor (11 tricks).")
    one_minor = leaf(
        "1 of minor",
        "12+ HCP with no 5-card major. Open the longer minor "
        "(diamonds with 4-4); some systems open 1♣ with 3-3.")
    weak_two = leaf(
        "Weak 2 (2♦ / 2♥ / 2♠)",
        "5-10 HCP with a 6-card suit headed by at least one of the "
        "top three honors. Preemptive — makes life hard for opps.")
    three_preempt = leaf(
        "3-level preempt",
        "5-10 HCP with a 7-card suit, decent quality. Even more "
        "preemptive than a weak two.")
    rule_of_20 = leaf(
        "1 of suit (Rule of 20)",
        "10-11 HCP with sufficient distribution: HCP + length-in-"
        "two-longest-suits ≥ 20. A light opening hand that "
        "qualifies on shape.")

    # Decision tree (predicates run on HandEval).
    return Node(
        label="22+ HCP balanced\nor very strong hand?",
        predicate=lambda e: (
            e.hcp >= 22
            or (e.hcp >= 20 and e.controls >= 5
                and e.quick_tricks >= 5
                and not (e.is_balanced or e.is_semi_balanced))),
        yes_child=strong_2c,
        no_child=Node(
            label=f"{system_min_nt}-{system_max_nt} HCP balanced?",
            predicate=lambda e: (
                system_min_nt <= e.hcp <= system_max_nt
                and (e.is_balanced or e.is_semi_balanced)),
            yes_child=nt_1,
            no_child=Node(
                label="20-21 HCP balanced?",
                predicate=lambda e: (
                    20 <= e.hcp <= 21
                    and (e.is_balanced or e.is_semi_balanced)),
                yes_child=nt_2,
                no_child=Node(
                    label="12+ HCP with 5-card major?",
                    predicate=lambda e: (
                        e.hcp >= 12
                        and (e.suit_lengths[Suit.HEARTS] >= 5
                             or e.suit_lengths[Suit.SPADES] >= 5)),
                    yes_child=one_major,
                    no_child=Node(
                        label="12+ HCP?",
                        predicate=lambda e: e.hcp >= 12,
                        yes_child=one_minor,
                        no_child=Node(
                            label="6-card suit, 5-10 HCP\n(weak two)?",
                            predicate=lambda e: (
                                5 <= e.hcp <= 10
                                and any(e.suit_lengths[s] == 6
                                        for s in (Suit.DIAMONDS,
                                                  Suit.HEARTS,
                                                  Suit.SPADES))),
                            yes_child=weak_two,
                            no_child=Node(
                                label="7-card suit, 5-10 HCP\n(3-level preempt)?",
                                predicate=lambda e: (
                                    5 <= e.hcp <= 10
                                    and any(e.suit_lengths[s] == 7
                                            for s in Suit
                                            if s != Suit.NOTRUMP)),
                                yes_child=three_preempt,
                                no_child=Node(
                                    label="10-11 HCP +\nRule of 20?",
                                    predicate=_rule_of_20,
                                    yes_child=rule_of_20,
                                    no_child=pass_leaf))))))))


def _rule_of_20(e: HandEval) -> bool:
    if not (10 <= e.hcp <= 11):
        return False
    lens = sorted(e.suit_lengths.values(), reverse=True)
    return e.hcp + lens[0] + lens[1] >= 20


def _response_tree_sayc() -> Node:
    """Decision tree for responder's first call after partner's
    opening — covers the common SAYC paths."""
    leaf = lambda lab, why: Node(label=lab, bid_label=lab,
                                 commentary=why)

    return Node(
        label="Did partner open 1NT?",
        predicate=lambda e: False,   # placeholder: opening-bid context
                                      # is encoded by which tree we use
        yes_child=Node(
            label="4+ card major +\n8+ HCP?",
            predicate=lambda e: (
                e.hcp >= 8
                and (e.suit_lengths[Suit.HEARTS] >= 4
                     or e.suit_lengths[Suit.SPADES] >= 4)),
            yes_child=leaf(
                "2♣ Stayman",
                "Asks 1NT opener for a 4-card major. Promises 8+ "
                "HCP (invitational+) and at least one 4-card major."),
            no_child=Node(
                label="5+ card major?",
                predicate=lambda e: (
                    e.suit_lengths[Suit.HEARTS] >= 5
                    or e.suit_lengths[Suit.SPADES] >= 5),
                yes_child=leaf(
                    "Jacoby transfer\n(2♦ for ♥ / 2♥ for ♠)",
                    "Tells partner to bid the next major up so the "
                    "stronger NT hand becomes declarer. Any HCP."),
                no_child=Node(
                    label="0-7 HCP balanced?",
                    predicate=lambda e: e.hcp <= 7 and e.is_balanced,
                    yes_child=leaf(
                        "Pass",
                        "Weak balanced hand — 1NT will play fine."),
                    no_child=leaf(
                        "Game/slam exploration",
                        "Stronger balanced or distributional — pick "
                        "from 2NT/3NT (invite/game), 4NT quant, "
                        "Texas transfers, etc.")))),
        no_child=Node(
            label="4+ in partner's major?",
            predicate=lambda e: False,  # filled in dynamically
                                         # via context-tree variant
            yes_child=Node(
                label="HCP for raise?",
                predicate=lambda e: True,
                yes_child=leaf(
                    "Raise of partner's major",
                    "Simple (6-9), limit (10-12, 4-card), or "
                    "Jacoby 2NT (13+, 4-card, GF)."),
                no_child=leaf("Bid new suit",
                              "Look for better strain.")),
            no_child=leaf(
                "Bid new suit / NT response",
                "1NT (6-9 / 6-12 forcing), new suit at 1 (4+ in M, "
                "6+ HCP), or 2-of-suit (2/1 GF in modern style).")))


# ---------------------------------------------------------------------------
# Walk the tree on a hand → return path of node labels visited.
# ---------------------------------------------------------------------------

def walk(tree: Node, e: HandEval) -> List[Node]:
    """Walk the decision tree on this hand. Returns the list of nodes
    visited from root → leaf."""
    path = [tree]
    cur = tree
    while not cur.is_leaf():
        if cur.predicate is None:
            break
        nxt = cur.yes_child if cur.predicate(e) else cur.no_child
        if nxt is None:
            break
        path.append(nxt)
        cur = nxt
    return path


# ---------------------------------------------------------------------------
# Plantuml renderer
# ---------------------------------------------------------------------------

def _puml_id(n: int) -> str:
    return f"N{n}"


def render_plantuml(tree: Node, highlight_path: List[Node],
                    title: str) -> str:
    """Render the tree as a plantuml decision diagram with the
    highlighted path drawn thicker / green. Returns the .puml text.

    Plantuml-quirks notes:
      • Multi-line labels need the literal two-char escape `\\n`,
        not a real newline character.
      • `rectangle "lbl" as N1 #color` works; `usecase` with a
        color suffix sometimes fails parsing.
    """
    def esc(label: str) -> str:
        # Real newlines → plantuml's \n escape (two chars).
        return label.replace("\n", "\\n").replace('"', "'")

    lines: List[str] = [
        "@startuml",
        f"title {esc(title)}",
        "skinparam wrapWidth 200",
        "skinparam defaultTextAlignment center",
        "skinparam roundCorner 8",
        "",
    ]
    id_of = {}
    counter = [0]

    def _walk(node: Node):
        if id(node) in id_of:
            return
        counter[0] += 1
        id_of[id(node)] = _puml_id(counter[0])
        if not node.is_leaf():
            if node.yes_child is not None:
                _walk(node.yes_child)
            if node.no_child is not None:
                _walk(node.no_child)

    _walk(tree)
    highlighted = {id(n) for n in highlight_path}

    for node in _all_nodes(tree):
        nid = id_of[id(node)]
        label = esc(node.label)
        if node.is_leaf():
            colour = "#a8d4a0" if id(node) in highlighted else "#e8e8e8"
            lines.append(f'rectangle "{label}" as {nid} {colour}')
        else:
            colour = "#ffd87c" if id(node) in highlighted else "#fffaf0"
            lines.append(f'rectangle "{label}" as {nid} {colour}')

    lines.append("")
    for node in _all_nodes(tree):
        if node.is_leaf():
            continue
        nid = id_of[id(node)]
        if node.yes_child is not None:
            yid = id_of[id(node.yes_child)]
            on_path = (id(node) in highlighted
                       and id(node.yes_child) in highlighted)
            arrow = "-[#22aa55,thickness=3]->" if on_path else "-->"
            lines.append(f"{nid} {arrow} {yid} : Y")
        if node.no_child is not None:
            nzid = id_of[id(node.no_child)]
            on_path = (id(node) in highlighted
                       and id(node.no_child) in highlighted)
            arrow = "-[#22aa55,thickness=3]->" if on_path else "-->"
            lines.append(f"{nid} {arrow} {nzid} : N")

    lines.append("@enduml")
    return "\n".join(lines)


def _all_nodes(root: Node) -> List[Node]:
    out: List[Node] = []
    seen = set()

    def _go(n):
        if id(n) in seen:
            return
        seen.add(id(n))
        out.append(n)
        if not n.is_leaf():
            if n.yes_child is not None:
                _go(n.yes_child)
            if n.no_child is not None:
                _go(n.no_child)

    _go(root)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def flowchart_for(board: BoardState, seat: Seat, system
                  ) -> Tuple[str, str]:
    """Decide which flowchart applies for `seat` at the current
    auction position, walk it on `seat`'s hand, and return
    (puml_text, commentary).

    commentary is the natural-language line for the LEAF the hand
    ended up at — what the chosen bid means.
    """
    hand = board.hands.get(seat)
    if hand is None:
        return "", "Need a hand to render a flowchart."
    e = evaluate_hand(hand)

    # Pick the relevant tree by auction position.
    auction = list(getattr(board, "auction", None) or [])
    title = ""
    if not auction or all(b.is_pass for b in auction):
        nt_min = getattr(system, "one_nt_min_hcp", 15) if system else 15
        nt_max = getattr(system, "one_nt_max_hcp", 17) if system else 17
        tree = _opening_tree_sayc(nt_min, nt_max)
        sys_name = getattr(system, "name", "system")
        title = f"Deciding on an Opening Bid ({sys_name})"
    else:
        tree = _response_tree_sayc()
        title = "Responding (early-stage placeholder)"

    path = walk(tree, e)
    puml = render_plantuml(tree, path, title)
    leaf = path[-1] if path else None
    commentary = (
        leaf.commentary if (leaf is not None and leaf.commentary)
        else "")
    return puml, commentary
