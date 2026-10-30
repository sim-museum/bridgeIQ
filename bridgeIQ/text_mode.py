"""biq text mode: play bridge in a terminal, no window needed.

    python3 main.py --textmode [--seed N]      (or ./run.sh --textmode)

You sit South; biq plays North, East and West with the same engines the GUI
robots use: the native bidder in the configured system (Preferences,
Precision by default) and the no-peek card engine. When North-South declare
you play both hands, as in the GUI. Each finished board shows the full deal,
the score, the double-dummy result and par, and is logged to DATA/LOG like a
GUI board (BDL + PBN + PPL).

Commands: a call (1c 1nt 2h p x xx) or a card (sa, 10h, h10, th; a bare rank
when it is unambiguous); ? = biq's suggestion; m = the meaning of every call
so far; auto = biq plays the rest of the board for you; h = help; q = quit.
No Qt is imported, so it runs on a console or over ssh.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
import time
from typing import Dict, List, Optional

from backend.models import (BoardState, Bid, Card, Contract, Hand, Rank, Seat,
                            Suit, Trick, Vulnerability)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SEATS = [Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST]
SEAT_NAME = {Seat.NORTH: "North", Seat.EAST: "East",
             Seat.SOUTH: "South", Seat.WEST: "West"}
VUL_NAME = {Vulnerability.NONE: "None", Vulnerability.NS: "N/S",
            Vulnerability.EW: "E/W", Vulnerability.BOTH: "Both"}
_DDS_VUL = {Vulnerability.NONE: 0, Vulnerability.BOTH: 1,
            Vulnerability.NS: 2, Vulnerability.EW: 3}


def _utf8() -> bool:
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


SYM = ({Suit.SPADES: "♠", Suit.HEARTS: "♥", Suit.DIAMONDS: "♦",
        Suit.CLUBS: "♣", Suit.NOTRUMP: "NT"} if _utf8() else
       {Suit.SPADES: "S", Suit.HEARTS: "H", Suit.DIAMONDS: "D",
        Suit.CLUBS: "C", Suit.NOTRUMP: "NT"})


class QuitGame(Exception):
    """Raised from any prompt to end the session (q, end of input, Ctrl-C)."""


# --- formatting -------------------------------------------------------------

def card_str(c: Card) -> str:
    return f"{SYM[c.suit]}{c.rank.to_char()}"


def call_str(b: Bid) -> str:
    if b.is_pass:
        return "Pass"
    if b.is_double:
        return "X"
    if b.is_redouble:
        return "XX"
    return f"{b.level}{SYM[b.suit]}" + ("*" if b.alert else "")


def contract_str(c: Contract) -> str:
    dbl = "XX" if c.redoubled else ("X" if c.doubled else "")
    return f"{c.level}{SYM[c.suit]}{dbl} by {SEAT_NAME[c.declarer]}"


def suit_line(hand: Hand, suit: Suit) -> str:
    ranks = sorted((c.rank for c in hand.cards if c.suit == suit))
    return f"{SYM[suit]} " + ("".join(r.to_char() for r in ranks) or "-")


def hand_lines(hand: Optional[Hand]) -> List[str]:
    if hand is None:
        return ["", "", "", ""]
    return [suit_line(hand, s) for s in (Suit.SPADES, Suit.HEARTS,
                                         Suit.DIAMONDS, Suit.CLUBS)]


def pbn_for_dds(hands: Dict[Seat, Hand]) -> str:
    return "N:" + " ".join(hands[s].to_pbn().replace("-", "") for s in SEATS)


# --- auction rules ----------------------------------------------------------

def _seat_of(board: BoardState, i: int) -> Seat:
    return Seat((board.dealer.value + i) % 4)


def legal_calls(board: BoardState, seat: Seat) -> List[Bid]:
    """Pass, every higher bid, X of the opponents' undoubled bid, XX of the
    opponents' double (the GUI's rule: the last non-pass call is theirs)."""
    out = [Bid.make_pass()]
    last_bid, last_i, last_call, last_call_i = None, -1, None, -1
    for i, b in enumerate(board.auction):
        if b.is_pass:
            continue
        last_call, last_call_i = b, i
        if not b.is_double and not b.is_redouble:
            last_bid, last_i = b, i
    if last_call is not None:
        theirs = _seat_of(board, last_call_i).is_ns() != seat.is_ns()
        if theirs and not last_call.is_double and not last_call.is_redouble:
            out.append(Bid.make_double())
        if theirs and last_call.is_double:
            out.append(Bid.make_redouble())
    floor = (0 if last_bid is None
             else last_bid.level * 5 + _strain_order(last_bid.suit) + 1)
    for level in range(1, 8):
        for suit in (Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES,
                     Suit.NOTRUMP):
            if level * 5 + _strain_order(suit) >= floor:
                out.append(Bid(level=level, suit=suit))
    return out


def _strain_order(suit: Suit) -> int:
    return {Suit.CLUBS: 0, Suit.DIAMONDS: 1, Suit.HEARTS: 2,
            Suit.SPADES: 3, Suit.NOTRUMP: 4}[suit]


def same_call(a: Bid, b: Bid) -> bool:
    if a.is_pass or a.is_double or a.is_redouble:
        return (a.is_pass, a.is_double, a.is_redouble) == \
            (b.is_pass, b.is_double, b.is_redouble)
    return (not (b.is_pass or b.is_double or b.is_redouble)
            and (a.level, a.suit) == (b.level, b.suit))


def parse_call(text: str) -> Optional[Bid]:
    t = text.strip().upper().replace(" ", "")
    for sym, letter in (("♠", "S"), ("♥", "H"), ("♦", "D"), ("♣", "C")):
        t = t.replace(sym, letter)
    if t in ("P", "PASS", "-"):
        return Bid.make_pass()
    if t in ("X", "D", "DBL", "DOUBLE"):
        return Bid.make_double()
    if t in ("XX", "R", "RD", "RDBL", "REDOUBLE"):
        return Bid.make_redouble()
    if len(t) >= 2 and t[0] in "1234567":
        strain = t[1:]
        if strain in ("N", "NT"):
            return Bid(level=int(t[0]), suit=Suit.NOTRUMP)
        if strain in ("S", "H", "D", "C"):
            return Bid(level=int(t[0]), suit=Suit.from_char(strain))
    return None


def final_contract(board: BoardState) -> Optional[Contract]:
    """The contract after a finished auction (None when passed out); the
    declarer is the first of the side to name the strain."""
    level, suit, decl_side, dbl, rdbl, last_i = 0, None, None, False, False, -1
    for i, b in enumerate(board.auction):
        if b.is_pass:
            continue
        if b.is_double:
            dbl, rdbl = True, False
        elif b.is_redouble:
            rdbl = True
        else:
            level, suit, last_i = b.level, b.suit, i
            decl_side = _seat_of(board, i).is_ns()
            dbl = rdbl = False
    if not level:
        return None
    for i, b in enumerate(board.auction[:last_i + 1]):
        s = _seat_of(board, i)
        if (not (b.is_pass or b.is_double or b.is_redouble)
                and b.suit == suit and s.is_ns() == decl_side):
            return Contract(level=level, suit=suit, doubled=dbl,
                            redoubled=rdbl, declarer=s)
    return None


# --- card rules -------------------------------------------------------------

def legal_cards(hand: Hand, trick: Trick) -> List[Card]:
    if trick.cards:
        follow = [c for c in hand.cards if c.suit == trick.cards[0].suit]
        if follow:
            return follow
    return list(hand.cards)


_RANKS = "AKQJT98765432"


def parse_card(text: str, legal: List[Card]) -> Optional[Card]:
    """'sa', 'as', '♠a', '10h', 'h10', 'th'; a bare rank ('a', '2') when one
    legal card has it; a bare suit when it is the only legal card of it."""
    t = text.strip().upper().replace(" ", "").replace("10", "T")
    for sym, letter in (("♠", "S"), ("♥", "H"), ("♦", "D"), ("♣", "C")):
        t = t.replace(sym, letter)
    suits = [ch for ch in t if ch in "SHDC"]
    ranks = [ch for ch in t if ch in _RANKS]
    if len(t) != len(suits) + len(ranks) or len(suits) > 1 or len(ranks) > 1:
        return None
    cands = legal
    if suits:
        cands = [c for c in cands if c.suit == Suit.from_char(suits[0])]
    if ranks:
        cands = [c for c in cands if c.rank == Rank.from_char(ranks[0])]
    return cands[0] if len(cands) == 1 else None


# --- the game ---------------------------------------------------------------

HELP = """\
BIDDING   type a call: 1c 1d 1h 1s 1nt (or 1n) ... 7nt, p (pass), x, xx
          ?      biq's suggestion for you, with its reason
          m      what every call so far means
PLAY      type a card: sa  as  10h  h10  th  (a bare rank or suit is enough
          when only one legal card matches)
          ?      biq's suggestion for you
          auto   biq plays the rest of this board for your side
ANY TIME  h = this help, q = quit
You are South. When North-South declare you play both hands."""


class TextGame:
    def __init__(self, seed: Optional[int] = None, out=print, inp=input,
                 log: bool = True):
        from backend.config import get_config_manager
        from backend.native_bidder import NativeBiddingEngine
        from backend.bid_descriptions import system_for_prefs
        self.print = out
        self._raw_input = inp
        self.rng = random.Random(seed)
        prefs = get_config_manager().config.preferences
        self.prefs = prefs
        native = getattr(prefs, "native_bidding_system", "") or "Precision90M"
        self.system_name = {
            "NS": getattr(prefs, "ns_bidding_system", "") or native,
            "EW": getattr(prefs, "ew_bidding_system", "") or native,
        }
        self.bidders = {side: NativeBiddingEngine(system=name)
                        for side, name in self.system_name.items()}
        self.systems = {side: system_for_prefs(prefs, side)
                        for side in ("NS", "EW")}
        self.use_nopeek = getattr(prefs, "use_nopeek_play", True)
        self.prompt_ctx = None      # (seat, legal calls / cards) at a prompt
        self.board_number = 0
        self.total_ns = 0
        self.total_imps_vs_par = 0
        self.boards_played = 0
        self.log = log
        self.logger = None          # made at the first finished board

    # --- input -----------------------------------------------------------
    def ask(self, prompt: str) -> str:
        try:
            text = self._raw_input(prompt)
        except (EOFError, KeyboardInterrupt):
            self.print()
            raise QuitGame()
        if text.strip().lower() in ("q", "quit", "exit"):
            raise QuitGame()
        return text

    # --- logging (same files as the GUI: DATA/LOG/log-NNN.bdl + .pbn/.ppl)
    def _make_logger(self):
        try:
            path = os.path.join(APP_DIR, "ui", "game_logger.py")
            spec = importlib.util.spec_from_file_location("biq_game_logger", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.GameLogger(ns_system=self.system_name["NS"],
                                  ew_system=self.system_name["EW"])
        except Exception as e:
            self.print(f"(boards will not be logged: {e})")
            self.log = False
            return None

    # --- session ---------------------------------------------------------
    def run(self) -> int:
        self.print("=" * 64)
        self.print("bridgeIQ - text mode".center(64))
        self.print("=" * 64)
        self.print(f"You are South. N/S play {self.system_name['NS']}, "
                   f"E/W play {self.system_name['EW']}.")
        self.print("Type h for help, q to quit.")
        try:
            while True:
                self.play_board()
                self.ask("\nPress Enter for the next board (q to quit) ")
        except QuitGame:
            pass
        self.print(f"\nBoards played: {self.boards_played}.  N/S total "
                   f"{self.total_ns:+d}.  IMPs vs par {self.total_imps_vs_par:+d}.")
        if self.logger is not None:
            self.print(f"Log: {self.logger.current_log_file}")
        return 0

    def new_board(self) -> BoardState:
        self.board_number += 1
        deck = [Card(s, r) for s in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS,
                                     Suit.CLUBS) for r in Rank]
        self.rng.shuffle(deck)
        hands = {seat: Hand(cards=sorted(deck[i * 13:(i + 1) * 13],
                                         key=lambda c: (c.suit, c.rank)))
                 for i, seat in enumerate(SEATS)}
        dealer, vul = BoardState._board_dealer_vuln(self.board_number)
        return BoardState(board_number=self.board_number, dealer=dealer,
                          vulnerability=vul, hands=hands)

    def play_board(self, board: Optional[BoardState] = None) -> BoardState:
        board = board or self.new_board()
        self.original = {s: Hand(cards=list(h.cards))
                         for s, h in board.hands.items()}
        self.auto = False
        self.print("\n" + "=" * 64)
        self.print(f"Board {board.board_number}   Dealer {SEAT_NAME[board.dealer]}"
                   f"   Vulnerable {VUL_NAME[board.vulnerability]}")
        self.print("=" * 64)
        self.bid_auction(board)
        board.contract = final_contract(board)
        if board.contract is None:
            self.print("\nPassed out.")
            self.finish(board)
            return board
        self.print(f"\nContract: {contract_str(board.contract)}")
        self.play_cards(board)
        self.finish(board)
        return board

    # --- bidding -------------------------------------------------------------
    def side(self, seat: Seat) -> str:
        return "NS" if seat.is_ns() else "EW"

    def robot_call(self, board: BoardState, seat: Seat) -> Bid:
        resp = self.bidders[self.side(seat)].get_bid(board, seat)
        bid = resp.action
        legal = legal_calls(board, seat)
        if not any(same_call(bid, b) for b in legal):
            bid = Bid.make_pass()
        return bid

    def meaning(self, board: BoardState, i: int) -> str:
        from backend.bid_descriptions import describe_bid
        seat = _seat_of(board, i)
        try:
            pts, length, text, art = describe_bid(
                board.auction[i], board.auction[:i], seat, board.dealer,
                self.systems[self.side(seat)])
        except Exception:
            return ""
        bits = [x for x in (pts and f"{pts} HCP", length, text) if x]
        return ", ".join(bits) + (" (artificial)" if art else "")

    def show_auction(self, board: BoardState):
        order = [Seat.WEST, Seat.NORTH, Seat.EAST, Seat.SOUTH]
        self.print("  " + "".join(f"{SEAT_NAME[s]:<9}" for s in order))
        cells = [""] * order.index(board.dealer)
        cells += [call_str(b) for b in board.auction]
        if not board.is_auction_complete():
            cells.append("?")
        for r in range(0, len(cells), 4):
            self.print("  " + "".join(f"{c:<9}" for c in cells[r:r + 4]))

    def bid_auction(self, board: BoardState):
        seen = 0
        while not board.is_auction_complete():
            seat = board.get_current_bidder()
            if seat != Seat.SOUTH or self.auto:
                bid = self.robot_call(board, seat)
                board.auction.append(bid)
                continue
            # South to call: show the hand, the auction and the new calls.
            self.print("")
            self.show_auction(board)
            for i in range(seen, len(board.auction)):
                b = board.auction[i]
                if not b.is_pass:
                    m = self.meaning(board, i)
                    self.print(f"    {SEAT_NAME[_seat_of(board, i)]} {call_str(b)}"
                               + (f": {m}" if m else ""))
            seen = len(board.auction)
            self.print("  Your hand (South, " +
                       f"{board.hands[Seat.SOUTH].hcp()} HCP):  "
                       + "   ".join(hand_lines(board.hands[Seat.SOUTH])))
            board.auction.append(self.human_call(board))
            seen = len(board.auction)
        self.print("")
        self.show_auction(board)

    def human_call(self, board: BoardState) -> Bid:
        legal = legal_calls(board, Seat.SOUTH)
        self.prompt_ctx = (Seat.SOUTH, legal)
        while True:
            text = self.ask("  Your call: ").strip()
            low = text.lower()
            if low in ("h", "help"):
                self.print(HELP)
                continue
            if low in ("?", "hint"):
                bid = self.robot_call(board, Seat.SOUTH)
                board.auction.append(bid)
                m = self.meaning(board, len(board.auction) - 1)
                board.auction.pop()
                why = (bid.explanation or "").strip()
                self.print(f"  biq would bid {call_str(bid)}"
                           + (f" ({m})" if m else "")
                           + (f"\n    reason: {why}" if why else ""))
                continue
            if low == "m":
                for i, b in enumerate(board.auction):
                    m = self.meaning(board, i)
                    self.print(f"    {SEAT_NAME[_seat_of(board, i)]:<6} "
                               f"{call_str(b):<5} {m}")
                continue
            bid = parse_call(text)
            if bid is None:
                self.print("  Not a call. Examples: 1c 2nt p x xx  (h = help)")
                continue
            if not any(same_call(bid, b) for b in legal):
                self.print(f"  {call_str(bid)} is not legal here.")
                continue
            return bid

    # --- play ----------------------------------------------------------------
    def human_seats(self, board: BoardState) -> set:
        if self.auto:
            return set()
        if board.contract.declarer.is_ns():
            return {Seat.NORTH, Seat.SOUTH}
        return {Seat.SOUTH}

    def robot_card(self, board: BoardState, seat: Seat, trick: Trick) -> Card:
        card = None
        if self.use_nopeek:
            try:
                from backend import nopeek
                card = nopeek.decide(board, seat, list(trick.cards))
            except Exception:
                card = None
        legal = legal_cards(board.hands[seat], trick)
        if card is None or card not in legal:
            card = legal[0]
        return card

    def show_table(self, board: BoardState, trick: Trick, to_play: Seat):
        c = board.contract
        dummy = c.declarer.partner()
        dummy_up = bool(board.tricks or trick.cards)
        visible = {Seat.SOUTH}
        if dummy_up:
            visible.add(dummy)
        if c.declarer.is_ns():
            visible.add(c.declarer)

        def block(seat):
            tag = " (dummy)" if seat == dummy and dummy_up else ""
            tag = " (declarer)" if seat == c.declarer else tag
            head = f"{SEAT_NAME[seat]}{tag}"
            if seat in visible:
                return [head] + hand_lines(board.hands[seat])
            return [head, f"{len(board.hands[seat].cards)} cards", "", "", ""]

        n, w, e, s = (block(Seat.NORTH), block(Seat.WEST), block(Seat.EAST),
                      block(Seat.SOUTH))
        self.print("")
        for line in n:
            self.print(" " * 20 + line)
        for lw, le in zip(w, e):
            self.print(f"{lw:<40}{le}")
        for line in s:
            self.print(" " * 20 + line)
        decl_ns = c.declarer.is_ns()
        ns = board.declarer_tricks if decl_ns else board.defense_tricks
        ew = board.defense_tricks if decl_ns else board.declarer_tricks
        self.print(f"\n  {contract_str(c)}   Tricks N/S {ns} - E/W {ew}"
                   f"   Trick {len(board.tricks) + 1}")
        played = "  ".join(f"{SEAT_NAME[Seat((trick.leader.value + k) % 4)][0]}"
                           f" {card_str(cd)}" for k, cd in enumerate(trick.cards))
        self.print(f"  This trick: {played or '-'}")

    def human_card(self, board: BoardState, seat: Seat, trick: Trick) -> Card:
        legal = legal_cards(board.hands[seat], trick)
        if len(legal) == 1:
            self.print(f"  {SEAT_NAME[seat]} plays {card_str(legal[0])} (only card)")
            return legal[0]
        self.show_table(board, trick, seat)
        if seat == board.contract.declarer.partner():
            who = f"dummy ({SEAT_NAME[seat]})"
        elif seat == Seat.SOUTH:
            who = "your hand (South)"
        else:
            who = f"declarer's hand ({SEAT_NAME[seat]})"
        self.prompt_ctx = (seat, legal)
        while True:
            text = self.ask(f"  Play from {who}: ").strip()
            low = text.lower()
            if low in ("h", "help"):
                self.print(HELP)
                continue
            if low in ("?", "hint"):
                self.print(f"  biq would play {card_str(self.robot_card(board, seat, trick))}")
                continue
            if low == "auto":
                self.auto = True
                return self.robot_card(board, seat, trick)
            card = parse_card(text, legal)
            if card is None:
                follow = (f" (you must follow {SYM[trick.cards[0].suit]})"
                          if trick.cards and len(legal) < len(board.hands[seat].cards)
                          else "")
                self.print(f"  Not a legal card{follow}. Legal: "
                           + " ".join(card_str(c) for c in legal))
                continue
            return card

    def play_cards(self, board: BoardState):
        c = board.contract
        trump = c.suit if c.suit != Suit.NOTRUMP else None
        leader = c.declarer.next()
        for _ in range(13):
            trick = Trick(leader=leader)
            board.current_trick = trick
            seat = leader
            for _k in range(4):
                if seat in self.human_seats(board):
                    card = self.human_card(board, seat, trick)
                else:
                    card = self.robot_card(board, seat, trick)
                    self.print(f"  {SEAT_NAME[seat]} plays {card_str(card)}")
                board.hands[seat].remove_card(card)
                trick.add_card(card, trump)
                seat = seat.next()
            board.tricks.append(trick)
            board.current_trick = None
            if trick.winner.is_ns() == c.declarer.is_ns():
                board.declarer_tricks += 1
            else:
                board.defense_tricks += 1
            self.print(f"  -> {SEAT_NAME[trick.winner]} wins trick "
                       f"{len(board.tricks)}")
            leader = trick.winner

    # --- result --------------------------------------------------------------
    def finish(self, board: BoardState):
        from backend.scoring import calculate_contract_score, diff_to_imps
        self.boards_played += 1
        self.print("\nThe deal:")
        n, s = hand_lines(self.original[Seat.NORTH]), hand_lines(self.original[Seat.SOUTH])
        w, e = hand_lines(self.original[Seat.WEST]), hand_lines(self.original[Seat.EAST])
        for line in ["North"] + n:
            self.print(" " * 20 + line)
        for lw, le in zip(["West"] + w, ["East"] + e):
            self.print(f"{lw:<40}{le}")
        for line in ["South"] + s:
            self.print(" " * 20 + line)

        c = board.contract
        ns_score = 0
        if c is not None:
            vul = board.vulnerability.is_vulnerable(c.declarer)
            score = calculate_contract_score(c, board.declarer_tricks, vul)
            ns_score = score if c.declarer.is_ns() else -score
            diff = board.declarer_tricks - c.target_tricks()
            res = "made" if diff >= 0 else "down"
            self.print(f"\n{contract_str(c)}: {board.declarer_tricks} tricks "
                       f"({res}{'' if diff == 0 else ' ' + str(abs(diff))}).  "
                       f"N/S {ns_score:+d}")
        self.total_ns += ns_score

        try:
            from backend.dds import DDSolver
            pbn = pbn_for_dds(self.original)
            (tab, par), = DDSolver().solve_dd_tables_par(
                [pbn], _DDS_VUL[board.vulnerability])
            if c is not None:
                strain = "NT" if c.suit == Suit.NOTRUMP else c.suit.to_char()
                dd = tab[c.declarer.to_char()][strain]
                self.print(f"Double dummy: {c.level}{SYM[c.suit]} by "
                           f"{SEAT_NAME[c.declarer]} takes {dd}.")
            imps = diff_to_imps(ns_score - par)
            self.total_imps_vs_par += imps
            self.print(f"Par N/S {par:+d}.  This board vs par: {imps:+d} IMP"
                       f"  (session {self.total_imps_vs_par:+d})")
        except Exception as e:
            self.print(f"(double-dummy analysis unavailable: {e})")

        if self.log and self.logger is None and c is not None:
            self.logger = self._make_logger()
        if self.logger is not None and c is not None:
            try:
                self.logger.log_hand(board, self.original)
            except Exception as e:
                self.print(f"(could not log the board: {e})")


def main(argv: List[str]) -> int:
    seed = None
    if "--seed" in argv:
        try:
            seed = int(argv[argv.index("--seed") + 1])
        except (IndexError, ValueError):
            print("--seed needs a number")
            return 2
    return TextGame(seed=seed).run()
