"""Context-aware coaching notes — short, specific, no citations.

`coaching_note(...)` returns ONE short paragraph tuned to the live situation:
the active bidding system's real parameters, the vulnerability, and the
specific cards of THIS deal (visible and inferable). The wording is authored
from the book corpus (see tools/ingest_book_library.py) and stored as
templates in backend/data/coach/note_catalog.json; the rendered text is built
entirely from live board state, so it can never leak a hidden card or mis-cite.

NO PEEKING, EVER:
  * bidding notes read only the asking seat's own hand (legitimate — the hint
    is gated to the user's own turn);
  * play notes flow through `known_layout(board, visible)` and the `visible`
    set, exactly like the instrumentation view — never `board.hands[hidden]`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from backend.models import Seat, Suit, Rank, BoardState

from ui.teaching_view import (
    SUIT_ROWS, known_layout, holdup_rule_of_seven, danger_info, finesse_hints,
    boss_rank, _holder_of, split_odds,
)

_COACH_DIR = (Path(__file__).resolve().parent.parent
              / "backend" / "data" / "coach")
_CATALOG_PATH = _COACH_DIR / "note_catalog.json"
_CATALOG: Optional[dict] = None
_INDEX: Optional[dict] = None
_CORPUS: Optional[Dict[str, str]] = None


def _catalog() -> dict:
    global _CATALOG
    if _CATALOG is None:
        try:
            _CATALOG = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            _CATALOG = {"rules": []}
    return _CATALOG


def book_passages(topics, limit: int = 3) -> list:
    """Return up to `limit` book excerpts (dicts {book,text}) most relevant to
    `topics`, for grounding the optional 'Ask Claude' advice. Reads only the
    committed corpus/index (stdlib json) — never the heavy build-time libs.

    Returns [] on any problem; this is best-effort context, not a hard dep.
    """
    global _INDEX, _CORPUS
    topics = [t for t in (topics or [])]
    if not topics:
        return []
    try:
        if _INDEX is None:
            _INDEX = json.loads((_COACH_DIR / "topic_index.json")
                                .read_text(encoding="utf-8"))
        # Rank passage ids by how many of the requested topics list them.
        score: Dict[str, int] = {}
        for t in topics:
            for pid in _INDEX.get("topics", {}).get(t, []):
                score[pid] = score.get(pid, 0) + 1
        if not score:
            return []
        wanted = sorted(score, key=lambda p: -score[p])[:limit]
        if _CORPUS is None:
            _CORPUS = {}
            with (_COACH_DIR / "corpus.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        _CORPUS[rec["id"]] = (rec.get("book", ""), rec.get("text", ""))
                    except Exception:
                        continue
        out = []
        for pid in wanted:
            book, text = _CORPUS.get(pid, ("", ""))
            if text:
                out.append({"book": book, "text": text})
        return out
    except Exception:
        return []


class _Blank(dict):
    """format_map helper: unknown fields render as '' rather than raising."""
    def __missing__(self, key):
        return ""


def _vuln_clause(vulnerable: bool) -> str:
    return ("Vulnerable — keep it sound." if vulnerable
            else "Not vulnerable — you can stretch a little.")


# --------------------------------------------------------------------------
def _bidding_features(board, seat, system) -> Tuple[dict, dict]:
    """Return (feature_dict, value_dict) for a bidding-phase note. Reads only
    the asking seat's own hand (legitimate on the user's turn)."""
    from backend.native_bidder import evaluate_hand

    feats: dict = {"phase": "bidding"}
    vals = _Blank(system=getattr(system, "name", "your system"),
                  strong_call=_call_glyph(getattr(system, "strong_open_call", "2C")),
                  nt_min=getattr(system, "one_nt_min_hcp", 15),
                  nt_max=getattr(system, "one_nt_max_hcp", 17),
                  nt2_min=getattr(system, "two_nt_min_hcp", 20),
                  nt2_max=getattr(system, "two_nt_max_hcp", 21),
                  wk_min=getattr(system, "weak_two_min_hcp", 6),
                  wk_max=getattr(system, "weak_two_max_hcp", 11))

    auction = list(getattr(board, "auction", None) or [])
    is_opening = (not auction) or all(getattr(b, "is_pass", False) for b in auction)
    feats["situation"] = "opening" if is_opening else "later"

    hand = board.hands.get(seat) if board.hands else None
    if hand is None or not hand.cards:
        return feats, vals
    e = evaluate_hand(hand)
    vals["hcp"] = e.hcp
    longest = e.longest_suit
    vals["long_suit"] = longest.symbol()
    vals["long_suit_char"] = longest.to_char()
    vals["long_n"] = e.suit_lengths[longest]

    strong_min = getattr(system, "strong_open_min_hcp", 22)
    nt_min, nt_max = vals["nt_min"], vals["nt_max"]
    nt2_min, nt2_max = vals["nt2_min"], vals["nt2_max"]

    if e.hcp >= strong_min:
        feats["strength"] = "strong"
    if e.is_balanced and nt_min <= e.hcp <= nt_max:
        feats["nt_range"] = "1nt"
    elif e.is_balanced and nt2_min <= e.hcp <= nt2_max:
        feats["nt_range"] = "2nt"
    else:
        feats["nt_range"] = "none"
    feats["preempt"] = bool(e.six_card_suits) and (
        vals["wk_min"] <= e.hcp <= vals["wk_max"])
    feats["has_5card_major"] = bool(e.five_card_majors)
    feats["openable"] = e.hcp >= 12 and e.hcp < strong_min

    # Context-sensitive "what to do now". The opening-bid flowchart only makes
    # sense when nobody has opened; once the auction is live, use a role-aware
    # note (opener's rebid / responder / overcaller / competitive) so the hint
    # actually matches the seat's position instead of always showing the generic
    # "responding to partner" menu.
    if is_opening:
        try:
            from backend.bidding_flowchart import flowchart_for
            _, commentary = flowchart_for(board, seat, system)
            vals["what"] = commentary or "Open by your system for this hand."
        except Exception:
            vals["what"] = "Open by your system for this hand."
    else:
        vals["what"] = _bidding_role_note(board, seat, system, e)

    vals["vuln_clause"] = _vuln_clause(board.vulnerability.is_vulnerable(seat))
    return feats, vals


def _call_sym(b) -> str:
    """Display a call as e.g. '1♣' / 'Pass' / 'Dbl'."""
    try:
        if getattr(b, "is_pass", False):
            return "Pass"
        if getattr(b, "is_double", False):
            return "Dbl"
        if getattr(b, "is_redouble", False):
            return "Rdbl"
        return b.symbol()
    except Exception:
        try:
            return b.to_str()
        except Exception:
            return "?"


def _bidding_role_note(board, seat, system, e) -> str:
    """A short, auction-aware coaching note: who opened, did the opponents act,
    and what is THIS seat's job now (rebid / respond / overcall / compete)."""
    auction = list(getattr(board, "auction", None) or [])
    dealer = board.dealer

    def bidder(i):
        return Seat((int(dealer) + i) % 4)

    opener = opener_call = None
    for i, b in enumerate(auction):
        if not getattr(b, "is_pass", False):
            opener, opener_call = bidder(i), b
            break
    partner = seat.partner()
    opp_acted = any((bidder(i) not in (seat, partner))
                    and not getattr(b, "is_pass", False)
                    for i, b in enumerate(auction))
    my_calls = [b for i, b in enumerate(auction)
                if bidder(i) == seat and not getattr(b, "is_pass", False)]
    hcp = e.hcp
    longest = e.longest_suit
    long_n = e.suit_lengths[longest]
    sym = longest.symbol()
    two_one = getattr(system, "two_over_one_min_hcp", 10)
    oc = _call_sym(opener_call) if opener_call is not None else "1 of a suit"

    if opener is None:
        return "Nobody has opened — open by your system, or pass a weak hand."

    # You opened — this is your REBID.
    if opener == seat:
        comp = (" The opponents overcalled, and partner is limited, so don't "
                "overreach.") if opp_acted else ""
        if long_n >= 6:
            return (f"You opened — this is your rebid. With {hcp} HCP and {long_n} "
                    f"{sym}, a simple rebid of your suit is a minimum (~12-15); "
                    f"jump in it to show extras (16-18).{comp}")
        return (f"You opened — this is your rebid. {hcp} HCP: rebid 1NT/2NT by "
                f"strength, raise partner with support, or show a second suit; "
                f"stay low when minimum.{comp}")

    # Double is only on the menu when the last non-pass call is an
    # opponent's contract bid (an undoubled one).
    last_i = next((i for i in range(len(auction) - 1, -1, -1)
                   if not getattr(auction[i], "is_pass", False)), None)
    can_double = (last_i is not None
                  and bidder(last_i) not in (seat, partner)
                  and not getattr(auction[last_i], "is_double", False)
                  and not getattr(auction[last_i], "is_redouble", False))
    partner_calls = [b for i, b in enumerate(auction)
                     if bidder(i) == partner and not getattr(b, "is_pass", False)]

    # Partner opened — you are RESPONDING.
    if opener == partner:
        if my_calls:
            return (f"Partner opened {oc} and you have already responded. With "
                    f"{hcp} HCP, show the rest of your hand: support partner, "
                    f"rebid a good suit, or bid notrump with the opponents' suit "
                    f"stopped; stop low when you have shown your values.")
        if opp_acted:
            dbl = (", a negative double shows the unbid major(s)"
                   if can_double else "")
            return (f"Partner opened {oc} and the opponents overcalled. With {hcp} "
                    f"HCP: a new suit is forcing{dbl}, a cue-bid is a strong "
                    f"raise, a simple raise is competitive — pass a flat minimum.")
        return (f"Partner opened {oc}. Respond by your {hcp} HCP: 1NT (6-10), a new "
                f"suit at the 1-level (6+ HCP, 4+ cards), or a raise with support. "
                f"A new suit at the 2-level is game-forcing (~{two_one}+).")

    # An opponent opened.
    their_suit = (opener_call.suit.symbol()
                  if opener_call is not None
                  and getattr(opener_call, "suit", None) is not None
                  and opener_call.suit != Suit.NOTRUMP else "their suit")
    if not my_calls and partner_calls:
        pc = _call_sym(partner_calls[0])
        return (f"{opener.to_char()} opened {oc} and partner acted with {pc}. "
                f"With {hcp} HCP: raise partner with support (3+ for an "
                f"overcall), bid a good suit of your own, or notrump with "
                f"{their_suit} stopped; a cue-bid of {their_suit} is a good "
                f"raise. Pass a weak hand without a fit.")
    if not my_calls:
        dbl = (f", double for takeout (short in {their_suit}, support for the "
               f"others)" if can_double else "")
        return (f"{opener.to_char()} opened {oc}. Overcall a good 5+ suit "
                f"(~8-16){dbl}, or pass a flat minimum.")
    dbl = (" Double is takeout/penalty by agreement." if can_double else "")
    return ("Competitive auction — both sides are bidding. Compete to the level of "
            f"your fit; don't push {hcp} HCP into their strength.{dbl}")


def _call_glyph(call: str) -> str:
    """'2C' -> '2♣' for display in notes."""
    if not call or len(call) < 2:
        return call or ""
    try:
        return f"{call[0]}{Suit.from_char(call[1]).symbol()}"
    except Exception:
        return call


def _play_features(board, seat, declarer, dummy, contract, visible, layout
                   ) -> Tuple[dict, dict]:
    feats: dict = {"phase": "play"}
    vals = _Blank()
    if contract is None or declarer is None:
        return feats, vals
    trump = contract.suit if contract.suit != Suit.NOTRUMP else None
    feats["trump"] = "yes" if trump is not None else "no"
    declarer_side = seat is None or seat.is_ns() == declarer.is_ns()
    feats["role"] = "declarer" if declarer_side else "defender"

    rem = layout["rem_ranks"]
    # Longest combined declaring suit (declarer perspective) — no-peek.
    if declarer in visible and dummy is not None and dummy in visible:
        best, best_n = None, 0
        for su in SUIT_ROWS:
            n = len(layout["known"][declarer][su]) + len(layout["known"][dummy][su])
            if n > best_n:
                best, best_n = su, n
        if best is not None:
            vals["long_suit"] = best.symbol()
            vals["long_n"] = best_n

    if declarer_side:
        if trump is None:
            hu = holdup_rule_of_seven(board, contract, declarer, dummy,
                                      visible, layout)
            if hu is not None and hu.get("n_total", 0) > 0:
                feats["holdup"] = True
                vals["holdup_suit"] = hu["suit"].symbol()
                vals["holdup_n"] = hu["n_total"]
            try:
                dgr = danger_info(board, contract, declarer, visible, layout, {})
                vals["danger_seat"] = (dgr["seat"].to_char() if dgr
                                       else "the danger hand")
            except Exception:
                vals["danger_seat"] = "the danger hand"
        else:
            vals["trump_sym"] = trump.symbol()
            held = (len(layout["known"][declarer][trump]) +
                    len(layout["known"][dummy][trump])
                    if declarer in visible and dummy in visible else 0)
            out = max(0, len(rem[trump]) - held)
            vals["out"] = out
            odds = split_odds(out) if out else None
            vals["split"] = (f"{odds[0][0]} {odds[0][1]}%" if odds else "drawing")
            boss_t = boss_rank(rem, trump)
            holder = _holder_of(layout, trump, boss_t, visible)
            feats["opp_master_trump"] = (boss_t is not None and holder is not None
                                         and holder not in (declarer, dummy))
            try:
                dgr = danger_info(board, contract, declarer, visible, layout, {})
                if dgr is not None and dgr.get("mode") == "ruff":
                    feats["danger_ruff"] = True
                    vals["danger_seat"] = dgr["seat"].to_char()
                    vals["threat"] = dgr["threat"].symbol()
            except Exception:
                pass
        hints = finesse_hints(declarer, dummy, visible, layout)
        vals["finesse"] = hints[0] if hints else ""
    else:
        # Defender — tricks needed to set; never reads hidden hands.
        vals["set_target"] = 14 - contract.target_tricks()
        if trump is not None:
            vals["trump_sym"] = trump.symbol()
    return feats, vals


def cards_played(board) -> int:
    """Total cards played so far (completed tricks + the current trick)."""
    n = 0
    for t in (getattr(board, "tricks", None) or []):
        n += len(getattr(t, "cards", None) or [])
    ct = getattr(board, "current_trick", None)
    if ct is not None:
        n += len(getattr(ct, "cards", None) or [])
    return n


def is_play_start(board) -> bool:
    """True while still on the FIRST trick — the moment a whole-deal plan is
    made. After trick one completes, advice should be about the next card."""
    return len(getattr(board, "tricks", None) or []) == 0


def _mid_play_note(board, seat, declarer, dummy, contract, visible, layout
                   ) -> Tuple[str, list]:
    """Technique-of-the-moment for the CURRENT trick (not a whole-deal plan).
    No-peek: reads only `layout` (visible-derived) + the public current trick.
    The concrete card is supplied separately by biq's engine in the dialog."""
    trump = (contract.suit if contract.suit != Suit.NOTRUMP else None) \
        if contract else None
    rem = layout["rem_ranks"]
    ct = getattr(board, "current_trick", None)
    trick_cards = list(getattr(ct, "cards", None) or []) if ct else []
    led = trick_cards[0].suit if trick_cards else None
    on_lead = led is None
    declarer_side = seat is None or (declarer is not None
                                     and seat.is_ns() == declarer.is_ns())

    if declarer_side and declarer is not None and dummy is not None:
        if on_lead:
            if trump is not None and declarer in visible and dummy in visible:
                held = (len(layout["known"][declarer][trump]) +
                        len(layout["known"][dummy][trump]))
                out = max(0, len(rem[trump]) - held)
                boss_t = boss_rank(rem, trump)
                holder = _holder_of(layout, trump, boss_t, visible)
                opp_master = (boss_t is not None and holder is not None
                              and holder not in (declarer, dummy))
                if out > 0 and not opp_master:
                    return (f"Draw the last {out} trump(s), then cash your "
                            f"side winners.", ["drawing_trumps"])
                if out > 0 and opp_master:
                    return ("Keep trump control — take your winners and concede "
                            "the master trump late.", ["drawing_trumps"])
            cash = [su for su in SUIT_ROWS
                    if (b := boss_rank(rem, su)) is not None
                    and (b in layout["known"][declarer][su]
                         or b in layout["known"][dummy][su])]
            if cash:
                syms = "".join(s.symbol() for s in cash)
                return (f"On lead: cash your winners ({syms}) — high card from "
                        f"the short hand first.", ["entries"])
            return ("On lead: lead toward your high cards or cross for a marked "
                    "finesse; save entries for the long suit.", ["entries"])
        return ("Win in the hand that protects your entries; duck when you can "
                "spare it to keep communications.", ["entries"])

    # Defender — own hand (+ dummy) only.
    if on_lead:
        return ("On lead: continue your established suit or return partner's; "
                "shift only with a clear reason.", ["opening_lead", "signals"])
    my = (layout["known"].get(seat) if seat is not None else None) or {}
    have_led = bool(my.get(led)) if led is not None else True
    if not have_led and trump is not None:
        return (f"Out of {led.symbol()} — ruff if it gains a trick, else pitch "
                f"a loser and signal.", ["signals", "ruff"])
    pos = 1
    if ct is not None and trick_cards and getattr(ct, "leader", None) is not None:
        pos = ((int(seat) - int(ct.leader)) % 4) + 1
    if pos == 2:
        return ("Second hand low — keep your honours to capture declarer's, "
                "unless you must grab a setting trick.", ["signals"])
    if pos == 3:
        return ("Third hand high — play high to force declarer's honour "
                "(finesse against dummy only when it's marked).", ["signals"])
    return ("Follow suit and signal honestly (attitude/count) so partner can "
            "read the position.", ["signals"])


def _match(rule_when: dict, feats: dict) -> bool:
    return all(feats.get(k) == v for k, v in rule_when.items())


def _fallback(feats: dict, vals: dict) -> str:
    if feats.get("phase") == "bidding":
        return vals.get("what") or "Bid what your system shows for this hand."
    role = feats.get("role")
    if role == "declarer":
        return ("Make a plan before trick one: count winners and losers, then "
                "decide trumps, entries and which suit to develop.")
    if role == "defender":
        return ("Count declarer's tricks, signal honestly, and decide active "
                "vs passive defence before you commit.")
    return "Make a plan: count your tricks before you play."


def coaching_context(board: BoardState, seat: Optional[Seat], phase: str,
                     system_ns=None, system_ew=None,
                     visible: Optional[Set[Seat]] = None, layout=None
                     ) -> Tuple[str, list]:
    """Short, situation-specific coaching note PLUS the matched topic tags
    (used to book-ground the optional Claude advice). Returns (note, topics).

    `phase` is 'bidding' or 'play'. `visible` is the legitimately face-up seat
    set; `layout` is an optional precomputed `known_layout(board, visible)`.
    """
    try:
        visible = set(visible or set())
        if phase == "bidding":
            system = system_ns if (seat is None or seat.is_ns()) else system_ew
            feats, vals = _bidding_features(board, seat, system)
        else:
            declarer = board.contract.declarer if getattr(
                board, "contract", None) else None
            dummy = declarer.partner() if declarer is not None else None
            if layout is None:
                layout = known_layout(board, visible)
            # Whole-deal planning advice only at the START of play; once trick
            # one is over, switch to technique-of-the-moment for the next card.
            if board.contract is not None and declarer is not None \
                    and not is_play_start(board):
                return _mid_play_note(board, seat, declarer, dummy,
                                      board.contract, visible, layout)
            feats, vals = _play_features(board, seat, declarer, dummy,
                                         board.contract, visible, layout)

        for rule in _catalog().get("rules", []):
            if rule.get("phase") != phase:
                continue
            if _match(rule.get("when", {}), feats):
                # Collapse any double spaces left by empty optional fields.
                text = " ".join(rule["template"].format_map(vals).split())
                if text:
                    return text, list(rule.get("topics", []))
        return _fallback(feats, vals), []
    except Exception:
        # A coaching note must never break the caller (hint dialog / view).
        return _fallback({"phase": phase,
                          "role": "declarer" if phase == "play" else None},
                         {}), []


def coaching_note(board: BoardState, seat: Optional[Seat], phase: str,
                  system_ns=None, system_ew=None,
                  visible: Optional[Set[Seat]] = None, layout=None) -> str:
    """Short, situation-specific coaching note (no citations)."""
    return coaching_context(board, seat, phase, system_ns, system_ew,
                            visible, layout)[0]
