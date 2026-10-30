"""Plain-English explanations for biq's actions — bids and card plays.

biq is rule-based: every call it makes follows from the chosen bidding
system, and every card it plays follows from its card-play technique. This
module turns that reasoning into prose for the optional "Explain action"
popup (Preferences → *Explain biq's actions*, default **OFF**).

It is a pure PRESENTATION layer. It never makes or changes a decision; it
only describes one already made, using:

* for bids — the engine's own reason captured on the `Bid.explanation`
  field at decision time, plus the system-grounded meaning from
  `bid_descriptions.describe_bid` (point range / shape / convention);
* for cards — the PUBLIC trick situation (cards on the table, the
  contract, who is winning) plus the technique label. It deliberately
  does NOT read biq's concealed holding, so the popup can never leak
  hidden cards (the no-peek instrumentation rule).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .models import Bid, Card, Seat, Suit, Contract


@dataclass
class Explanation:
    """Structured explanation rendered by the ExplanationDialog."""
    title: str = ""            # e.g. "North bids 2♥"
    subtitle: str = ""         # system / contract context line
    headline: str = ""         # one-line "why this action"
    points: List[str] = field(default_factory=list)  # supporting detail lines
    citation: str = ""         # convention or technique that applies
    note: str = ""             # honesty caveat / which engine chose it


_SEAT_NAME = {
    Seat.NORTH: "North", Seat.EAST: "East",
    Seat.SOUTH: "South", Seat.WEST: "West",
}

# Convention / technique names we can cite if they appear in a reason or in
# the system-grounded help text. Order matters: longer / more specific names
# first so "Jacoby 2NT" wins over "Jacoby".
_KNOWN_CONVENTIONS = [
    "Roman Key Card", "RKCB", "RKC", "Jacoby 2NT", "Jacoby transfer",
    "Texas transfer", "Garbage Stayman", "Stayman", "Smolen", "Lebensohl",
    "Gerber", "Blackwood", "Splinter", "Michaels", "Unusual NT",
    "Drury", "Negative double", "Takeout double", "Quantitative",
    "weak two", "Weak two", "preempt", "cuebid", "cue-bid", "transfer",
]


def _citation_from_text(text: str) -> str:
    """Return the first known convention name mentioned in `text`, or ""."""
    if not text:
        return ""
    low = text.lower()
    for name in _KNOWN_CONVENTIONS:
        if name.lower() in low:
            return name
    return ""


# ---------------------------------------------------------------------------
# Bids
# ---------------------------------------------------------------------------

def explain_bid(bid: Bid,
                auction_before: List[Bid],
                seat: Seat,
                dealer: Seat,
                system,
                engine_who: str = "") -> Explanation:
    """Explain a single call `bid` made by `seat`.

    `auction_before` is the auction up to (not including) this call.
    `system` is the BiddingSystem the seat is playing (may be None).
    `bid.explanation` carries biq's own reason when biq made the call.
    """
    ex = Explanation()
    ex.title = f"{_SEAT_NAME[seat]} bids {bid.symbol()}"
    sysname = getattr(system, "name", "") if system is not None else ""
    ex.subtitle = f"Bidding system: {sysname}" if sysname else ""

    # System-grounded meaning of the call, derived from the auction WITHOUT
    # looking at the concealed hand (point range / shape / help / artificial).
    points = length = help_ = ""
    artificial = False
    if system is not None:
        try:
            from .bid_descriptions import describe_bid
            points, length, help_, artificial = describe_bid(
                bid, list(auction_before), seat, dealer, system)
        except Exception:
            pass

    # biq's own reason, captured at decision time (present for biq's calls).
    why = (getattr(bid, "explanation", "") or "").strip()

    # Headline = why this choice; fall back to the system meaning.
    ex.headline = why or (help_ or "Natural call.")

    # Supporting lines: how the call fits the system.
    sys_label = sysname or "this system"
    if help_ and help_.strip() and help_.strip() != ex.headline:
        ex.points.append(f"In {sys_label}, this call means: {help_}.")
    rng = ""
    if points:
        rng = f"Shows about {points} HCP"
        if length:
            rng += f", {length}"
    elif length:
        rng = length
    if rng:
        ex.points.append(rng + ".")
    if artificial or getattr(bid, "alert", False):
        ex.points.append(
            "Conventional / alertable call — it carries an agreed message "
            "rather than naturally showing the named denomination.")
    if why and (help_ or points):
        # Make the two halves explicit for the learner.
        ex.points.append(
            "The system constraints above are WHAT the call shows; the "
            "headline is WHY biq chose it from the available calls.")

    ex.citation = (_citation_from_text(why)
                   or _citation_from_text(help_))
    ex.note = _engine_note(engine_who, bidding=True)
    return ex


# ---------------------------------------------------------------------------
# Card play
# ---------------------------------------------------------------------------

# Rank.value is ACE=0 .. TWO=12, so a SMALLER value is a HIGHER card.
def _beats(card: Card, other: Card, led: Suit, trump: Optional[Suit]) -> bool:
    """True if `card` would currently be beating `other` in the trick."""
    c_tr = trump is not None and card.suit == trump
    o_tr = trump is not None and other.suit == trump
    if c_tr != o_tr:
        return c_tr                      # trump beats non-trump
    if c_tr and o_tr:
        return card.rank < other.rank    # higher trump
    # Neither is trump: only the led suit can win.
    if card.suit == led and other.suit != led:
        return True
    if card.suit != led and other.suit == led:
        return False
    if card.suit == led and other.suit == led:
        return card.rank < other.rank
    return False                         # neither follows nor trumps


def _winning_index(trick: List[Card], trump: Optional[Suit]) -> int:
    """Index of the card currently winning a (partial) trick."""
    if not trick:
        return 0
    led = trick[0].suit
    best = 0
    for i in range(1, len(trick)):
        if _beats(trick[i], trick[best], led, trump):
            best = i
    return best


def explain_card(card: Card,
                 seat: Seat,
                 trick_before: List[Card],
                 leader: Seat,
                 contract: Optional[Contract],
                 is_opening_lead: bool,
                 engine_who: str = "",
                 engine_reason: str = "",
                 engine_tag: str = "") -> Explanation:
    """Explain a single card play by `seat`.

    `trick_before` are the cards already played to the current trick, in
    order, BEFORE `seat` plays `card`. `leader` is who led the trick.

    When `engine_reason` is supplied it is biq's ACTUAL recorded rationale for
    this exact card (captured by the play engine at the moment it chose), and
    it is shown verbatim. Otherwise the explanation is reconstructed from
    public information only — never biq's concealed holding.
    """
    recon = _reconstruct_card(card, seat, trick_before, leader, contract,
                              is_opening_lead, engine_who)
    if not engine_reason:
        return recon

    # biq HAS a recorded reason for this exact card — show it verbatim. For the
    # search engines (alpha-mu / Monte-Carlo) that reason explains HOW the card
    # was chosen, not WHAT it does, so also append the public technique
    # classification. For technique-named reasons (opening lead, signals, draw
    # trumps, …) the reason already IS the technique, so that would be redundant.
    ex = Explanation()
    ex.title = recon.title
    ex.subtitle = recon.subtitle
    ex.headline = engine_reason
    ex.citation = engine_tag or "biq's line"
    if engine_tag in ("Alpha-mu search", "Monte-Carlo + DDS") and recon.headline:
        ex.points.append(f"In technique terms: {recon.headline}")
        ex.points.extend(recon.points)
    ex.note = _engine_note(engine_who, bidding=False)
    return ex


def _reconstruct_card(card: Card,
                      seat: Seat,
                      trick_before: List[Card],
                      leader: Seat,
                      contract: Optional[Contract],
                      is_opening_lead: bool,
                      engine_who: str = "") -> Explanation:
    """Reconstruct a card explanation from PUBLIC information only — never
    biq's concealed holding. Used when biq has no stored reason, and as the
    technique classification appended to a search-engine reason."""
    ex = Explanation()
    ex.title = f"{_SEAT_NAME[seat]} plays {card.symbol()}"

    trump = None
    declarer = seat
    if contract is not None:
        if contract.suit != Suit.NOTRUMP:
            trump = contract.suit
        declarer = contract.declarer
        ex.subtitle = (f"Contract: {contract.to_str()} "
                       f"by {_SEAT_NAME[declarer]}")

    defender = seat.is_ns() != declarer.is_ns()
    role = "defending" if defender else "for the declaring side"

    if is_opening_lead:
        ex.headline = "Opening lead."
        ex.points.append(
            "biq picks the opening lead from standard lead tables — top of a "
            "sequence, fourth-best from a long suit, the unbid-suit guides, "
            "etc. — chosen before seeing any concealed hand.")
        ex.citation = "Opening-lead conventions"
        ex.note = _engine_note(engine_who, bidding=False)
        return ex

    on_lead = not trick_before
    if on_lead:
        ex.headline = f"Leading to the trick ({role})."
        if not defender:
            ex.points.append(
                "Declarer leads to advance the plan for the contract — "
                "drawing trumps, establishing a long suit, or taking a "
                "marked finesse, as the position calls for.")
        else:
            ex.points.append(
                "On lead as a defender, biq continues the defence — often "
                "returning partner's suit or leading through declarer's "
                "strength toward partner's.")
        ex.citation = "Card-play technique"
        ex.note = _engine_note(engine_who, bidding=False)
        return ex

    # Following / discarding to a trick already under way.
    led = trick_before[0].suit
    following = card.suit == led
    win_idx = _winning_index(trick_before, trump)
    winner_seat = Seat((leader.value + win_idx) % 4)
    partner_winning = winner_seat.is_ns() == seat.is_ns()
    now_winning = _beats(card, trick_before[win_idx], led, trump)
    pos = len(trick_before) + 1          # 2 = second hand, 3 = third, 4 = last
    pos_name = {2: "second hand", 3: "third hand", 4: "fourth hand"}.get(pos, "")
    winning_card = trick_before[win_idx]

    if not following:
        # Could not follow suit: ruff or discard.
        if trump is not None and card.suit == trump:
            ex.headline = "Ruffing — out of the led suit, trumping in to win."
            ex.points.append(
                f"Could not follow {led.symbol()}, so biq plays a trump to "
                "win or contest the trick.")
            ex.citation = "Ruff"
        else:
            ex.headline = f"Discarding — void of, or out of, {led.symbol()}."
            ex.points.append(
                "Unable to follow and not ruffing, biq throws from another "
                "suit, keeping its winners and guards intact.")
            if defender:
                ex.points.append(
                    "A defender's discard also carries a signal to partner "
                    "(standard attitude / suit-preference carding).")
            ex.citation = "Discard"
        ex.note = _engine_note(engine_who, bidding=False)
        return ex

    # Following suit.
    if now_winning:
        if pos == 4:
            ex.headline = "Winning the trick as cheaply as it can (last hand)."
            ex.points.append(
                "Last to play and able to win, biq takes the trick with the "
                "cheapest card that does the job.")
        elif partner_winning:
            ex.headline = "Overtaking — going past partner's winner deliberately."
            ex.points.append(
                "biq plays higher than partner's card on purpose, usually to "
                "keep the lead in the right hand or to unblock a suit.")
        else:
            ex.headline = "Playing high to win or contest the trick."
            ex.points.append(
                f"biq beats {winning_card.symbol()} to try to win the trick.")
        ex.citation = "Card-play technique"
    else:
        # Following but cannot (or chooses not to) win.
        if partner_winning:
            ex.headline = "Partner is winning — no need to spend a high card."
            ex.points.append(
                f"{_SEAT_NAME[winner_seat]} (partner) already holds the "
                f"trick with {winning_card.symbol()}, so biq plays low and "
                "keeps its higher cards.")
            if defender:
                ex.points.append(
                    "Among equal spot-cards biq plays the standard "
                    "attitude / count signal for partner.")
        elif pos == 2:
            ex.headline = "Second hand low — keeping high cards for later."
            ex.points.append(
                "An opponent led; with nothing to gain by rising, biq follows "
                "low and saves its honours for when they matter (the classic "
                "'second hand low' guideline).")
        else:
            ex.headline = f"Cannot beat {winning_card.symbol()} — following low."
            ex.points.append(
                "biq cannot win this trick, so it follows with a low card and "
                "preserves its higher ones.")
            if defender:
                ex.points.append(
                    "The exact spot also carries biq's standard signal to "
                    "partner.")
        ex.citation = "Card-play technique"

    if pos_name and pos != 4:
        ex.subtitle = (ex.subtitle + f"  ·  {pos_name} to play"
                       if ex.subtitle else f"{pos_name.capitalize()} to play")
    ex.note = _engine_note(engine_who, bidding=False)
    return ex


def _engine_note(engine_who: str, *, bidding: bool) -> str:
    """One-line footer naming which engine produced the action."""
    who = (engine_who or "").lower()
    if bidding:
        if not who:
            return ""        # not biq's call — no engine attribution
        return ("Chosen by biq's rule-based bidder; the reason above is the "
                "exact rule that fired.")
    if "no-peek" in who or "alpha" in who or "amu" in who:
        return ("Chosen by biq's no-peek engine (alpha-mu search over sampled "
                "layouts) — it never sees the concealed cards.")
    if "mc" in who or "monte" in who or "dds" in who:
        return ("Chosen by biq's Monte-Carlo + double-dummy card-play engine.")
    if who:
        return f"Chosen by biq's card-play engine ({engine_who})."
    return ""
