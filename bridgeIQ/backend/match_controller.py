"""
Match Controller - Controls teams matches with Open and Closed rooms.
"""

import copy
import threading
from typing import Optional, Dict, List
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal, QEventLoop, QTimer

from .models import (
    BoardState, Seat, Bid, Card, Contract, Trick, Hand,
    BenTable, BenBoardRun, BenTeamsMatch
)
from .pavlicek import deal_to_number, format_deal_base72


class ClosedRoomWorker(QThread):
    """Background worker for BEN vs BEN closed room play.

    Plays out a board with all four positions controlled by BEN,
    running in a background thread to avoid blocking the UI.
    """

    finished = pyqtSignal(object)  # Emits BenBoardRun
    progress = pyqtSignal(str)  # Progress messages
    error = pyqtSignal(str)  # Error messages

    def __init__(self, engine, board: BoardState, ns_system: str = "BEN-NN",
                 ew_system: str = "BEN-NN", parent=None):
        super().__init__(parent)
        self.engine = engine
        self.board = board
        self.ns_system = ns_system
        self.ew_system = ew_system
        self._stop_requested = False

    def stop(self):
        """Request the worker to stop."""
        self._stop_requested = True

    def run(self):
        """Run the closed room play."""
        try:
            result = self._play_board()
            if not self._stop_requested:
                self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _play_board(self) -> BenBoardRun:
        """Play out the board with BEN controlling all four seats."""
        # Deep copy the board to avoid modifying original
        board = copy.deepcopy(self.board)

        # Store original hands for the result
        original_hands = {}
        for seat, hand in board.hands.items():
            original_hands[seat] = Hand(cards=list(hand.cards))

        pavlicek_id = format_deal_base72(deal_to_number(original_hands))

        # Create result object
        result = BenBoardRun(
            table=BenTable.CLOSED,
            board_number=board.board_number,
            pavlicek_id=pavlicek_id,
            original_hands=original_hands,
            ns_bidding_system=self.ns_system,
            ew_bidding_system=self.ew_system
        )

        # Phase 1: Bidding
        self.progress.emit("Closed Room: Bidding...")
        current_bidder = board.dealer
        consecutive_passes = 0
        first_bid_made = False

        # Resolve the bidder once: BEN by default, native engine if the
        # preference is set. Closed-room replays the same kind of auction
        # the user is bidding live, so we honour the toggle here too.
        bidder = self.engine
        try:
            from .config import get_config_manager
            prefs = get_config_manager().config.preferences
            if getattr(prefs, 'bidding_engine', 'BEN') == 'native':
                from .native_bidder import NativeBiddingEngine
                bidder = NativeBiddingEngine(
                    system=getattr(prefs, 'native_bidding_system', 'Precision90M')
                )
        except Exception:
            bidder = self.engine

        while not self._stop_requested:
            # Get bid from engine
            response = bidder.get_bid(board, current_bidder)
            bid = response.action

            board.auction.append(bid)
            result.auction.append(bid)

            # Check for passed out
            if bid.is_pass:
                consecutive_passes += 1
                if consecutive_passes >= 4 and not first_bid_made:
                    # Passed out
                    result.played = True
                    result.ns_score = 0
                    result.ew_score = 0
                    return result
                if consecutive_passes >= 3 and first_bid_made:
                    # Auction complete
                    break
            else:
                consecutive_passes = 0
                first_bid_made = True

            current_bidder = current_bidder.next()

        if self._stop_requested:
            return result

        # Determine contract
        contract = self._determine_contract(board)
        if contract is None:
            result.played = True
            return result

        board.contract = contract
        result.contract = contract

        # Phase 2: Card play
        self.progress.emit(f"Closed Room: Playing {contract.to_str()}...")

        declarer = contract.declarer
        dummy = declarer.partner()
        current_player = declarer.next()  # Opening leader

        trump = contract.suit if contract.suit.value < 4 else None  # Not NT

        use_nopeek = True
        try:
            from .config import get_config_manager
            use_nopeek = getattr(get_config_manager().config.preferences,
                                 'use_nopeek_play', True)
        except Exception:
            pass

        for trick_num in range(13):
            if self._stop_requested:
                break

            trick = Trick(leader=current_player)
            board.current_trick = trick

            for card_num in range(4):
                if self._stop_requested:
                    break

                # Get card play
                trick_cards = trick.cards if trick.cards else []

                # The same no-peek engine the open-room robots play with
                # (get_card_play is only the lowest-card fallback).
                card = None
                if use_nopeek:
                    try:
                        from . import nopeek
                        card = nopeek.decide(board, current_player,
                                             list(trick_cards))
                    except Exception:
                        card = None
                if card is None:
                    if card_num == 0 and trick_num == 0:
                        response = self.engine.get_opening_lead(board)
                    else:
                        response = self.engine.get_mc_card_play(
                            board, current_player, trick_cards
                        )
                    card = response.action
                if card is None:
                    # Fallback: play first legal card
                    hand = board.hands.get(current_player)
                    if hand and hand.cards:
                        lead_suit = trick.cards[0].suit if trick.cards else None
                        if lead_suit:
                            suit_cards = [c for c in hand.cards if c.suit == lead_suit]
                            card = suit_cards[0] if suit_cards else hand.cards[0]
                        else:
                            card = hand.cards[0]

                if card:
                    board.hands[current_player].remove_card(card)
                    trick.add_card(card, trump)
                    current_player = current_player.next()

            # Complete trick
            if trick.is_complete():
                winner = trick.winner
                board.tricks.append(trick)
                result.tricks.append(trick)

                if winner.is_ns() == declarer.is_ns():
                    board.declarer_tricks += 1
                else:
                    board.defense_tricks += 1

                current_player = winner
                board.current_trick = None

        # Calculate score
        result.declarer_tricks = board.declarer_tricks
        vulnerable = board.vulnerability.is_vulnerable(declarer)
        score = self.engine.calculate_score(contract, board.declarer_tricks, vulnerable)

        # Assign score to correct side
        if declarer.is_ns():
            result.ns_score = score
            result.ew_score = -score
        else:
            result.ns_score = -score
            result.ew_score = score

        result.played = True
        return result

    def _determine_contract(self, board: BoardState) -> Optional[Contract]:
        """Determine the final contract from the auction."""
        auction = board.auction
        if not auction:
            return None

        level = 0
        suit = None
        declarer = None
        doubled = False
        redoubled = False

        for i, bid in enumerate(auction):
            if not bid.is_pass and not bid.is_double and not bid.is_redouble:
                level = bid.level
                suit = bid.suit
                bidder_seat = Seat((board.dealer.value + i) % 4)

                # Find first bid of this suit by this side
                side_ns = bidder_seat.is_ns()
                for j, b in enumerate(auction[:i+1]):
                    if not b.is_pass and not b.is_double and not b.is_redouble:
                        if b.suit == suit:
                            b_seat = Seat((board.dealer.value + j) % 4)
                            if b_seat.is_ns() == side_ns:
                                declarer = b_seat
                                break
                doubled = False
                redoubled = False
            elif bid.is_double:
                doubled = True
                redoubled = False
            elif bid.is_redouble:
                redoubled = True

        if level == 0 or declarer is None:
            return None

        return Contract(
            level=level,
            suit=suit,
            doubled=doubled,
            redoubled=redoubled,
            declarer=declarer
        )


class TeamsMatchController:
    """Controls a teams match with Open and Closed rooms.

    Manages the flow of a teams match where:
    - Open Room: Human plays as South, BEN plays other seats
    - Closed Room: BEN plays all four seats (same hands, swapped orientation)
    """

    def __init__(self, engine, match: BenTeamsMatch):
        self.engine = engine
        self.match = match
        # Keep a strong reference to every in-flight worker so Python
        # can't garbage-collect a QThread mid-run (the worker → finished
        # lambda chain only references the controller; without this dict
        # the worker's Python wrapper was eligible for GC the moment
        # self.closed_room_worker was reassigned by an overlapping call,
        # which crashed Qt). Indexed by board_num so we can detect
        # double-starts.
        self._workers: Dict[int, 'ClosedRoomWorker'] = {}
        self._closed_room_callbacks: Dict[int, callable] = {}
        # Mutations to match.board_runs happen on the GUI thread (open
        # room) and on a closed-room worker thread (_on_closed_room_
        # complete is invoked via Qt's queued signal so it lands on the
        # GUI thread, but we hold the lock anyway to make reads from any
        # thread safe).
        self._runs_lock = threading.RLock()

    def start_board(self, board_num: int, board: BoardState) -> BenBoardRun:
        """Start a new board at the Open Room.

        Args:
            board_num: Board number
            board: The board state to play

        Returns:
            BenBoardRun for tracking the Open Room result
        """
        # Store original hands
        original_hands = {}
        for seat, hand in board.hands.items():
            original_hands[seat] = Hand(cards=list(hand.cards))

        pavlicek_id = format_deal_base72(deal_to_number(original_hands))

        # Create board run for Open Room
        open_run = BenBoardRun(
            table=BenTable.OPEN,
            board_number=board_num,
            pavlicek_id=pavlicek_id,
            original_hands=original_hands,
            ns_bidding_system=self.match.ns_bidding_system,
            ew_bidding_system=self.match.ew_bidding_system
        )

        # Initialize board runs dict for this board
        if board_num not in self.match.board_runs:
            self.match.board_runs[board_num] = {}
        self.match.board_runs[board_num][BenTable.OPEN] = open_run

        return open_run

    def complete_open_room(self, board_num: int, board: BoardState):
        """Record the completion of Open Room play.

        Args:
            board_num: Board number
            board: Completed board state
        """
        if board_num not in self.match.board_runs:
            return

        open_run = self.match.board_runs[board_num].get(BenTable.OPEN)
        if not open_run:
            return

        # Update the run with results
        open_run.auction = list(board.auction)
        open_run.tricks = list(board.tricks)
        open_run.contract = board.contract
        open_run.declarer_tricks = board.declarer_tricks

        # Calculate score
        if board.contract:
            vulnerable = board.vulnerability.is_vulnerable(board.contract.declarer)
            score = self.engine.calculate_score(
                board.contract, board.declarer_tricks, vulnerable
            )
            if board.contract.declarer.is_ns():
                open_run.ns_score = score
                open_run.ew_score = -score
            else:
                open_run.ns_score = -score
                open_run.ew_score = score
        else:
            open_run.ns_score = 0
            open_run.ew_score = 0

        open_run.played = True

    @property
    def closed_room_worker(self):
        """Backwards-compat accessor used by callers (and stop_closed_
        room) that expect a single worker handle. Returns the most
        recently started worker — multiple workers can be in flight
        because start_closed_room_async now refuses to overwrite an
        active board but a different board's worker may still run."""
        with self._runs_lock:
            for w in reversed(list(self._workers.values())):
                if w.isRunning():
                    return w
            return None

    def start_closed_room_async(self, board_num: int, callback: callable = None):
        """Start Closed Room play in background.

        Args:
            board_num: Board number
            callback: Optional callback(BenBoardRun) when complete

        Idempotent for the same board_num: if a worker is already
        running for this board (e.g. _show_result fired twice), the
        new callback is appended and the existing worker's finished
        signal will call both. Different board numbers run in
        parallel.
        """
        with self._runs_lock:
            if board_num not in self.match.board_runs:
                return
            open_run = self.match.board_runs[board_num].get(BenTable.OPEN)
            if not open_run:
                return

            # Don't start a second worker for the same board.
            existing = self._workers.get(board_num)
            if existing is not None and existing.isRunning():
                if callback is not None:
                    chain = self._closed_room_callbacks.get(board_num)
                    if chain is None:
                        self._closed_room_callbacks[board_num] = callback
                    else:
                        # Compose so both fire.
                        def _both(result, a=chain, b=callback):
                            try:
                                a(result)
                            except Exception:
                                pass
                            try:
                                b(result)
                            except Exception:
                                pass
                        self._closed_room_callbacks[board_num] = _both
                return

        # Create board from original hands.
        from .models import BoardState
        board = BoardState(
            board_number=board_num,
            hands=copy.deepcopy(open_run.original_hands)
        )
        dealer, vuln = BoardState._board_dealer_vuln(board_num)
        board.dealer = dealer
        board.vulnerability = vuln

        # Register callback BEFORE starting so a fast finish doesn't
        # race the assignment.
        if callback:
            self._closed_room_callbacks[board_num] = callback

        worker = ClosedRoomWorker(
            self.engine, board,
            self.match.ns_bidding_system,
            self.match.ew_bidding_system,
        )
        with self._runs_lock:
            self._workers[board_num] = worker
        worker.finished.connect(
            lambda result, bn=board_num: self._on_closed_room_complete(
                bn, result)
        )
        # An error during play should still clean the worker out of
        # the registry so the next start_closed_room_async for this
        # board isn't blocked thinking the old one is still running.
        worker.error.connect(
            lambda _msg, bn=board_num: self._on_closed_room_error(bn))
        worker.start()

    def _on_closed_room_complete(self, board_num: int, result: BenBoardRun):
        """Handle completion of Closed Room play. Runs on the GUI
        thread because Qt's queued signal delivery hops from the
        worker thread back to the controller's thread; the lock is
        belt-and-braces in case a caller wires up a direct
        (non-queued) connection."""
        with self._runs_lock:
            if board_num not in self.match.board_runs:
                self.match.board_runs[board_num] = {}
            self.match.board_runs[board_num][BenTable.CLOSED] = result
            callback = self._closed_room_callbacks.pop(board_num, None)
            # Drop the worker handle once it's done so the dict
            # doesn't accumulate dead QThreads.
            self._workers.pop(board_num, None)
        if callback:
            try:
                callback(result)
            except Exception:
                pass

    def _on_closed_room_error(self, board_num: int):
        """Worker emitted an error — clear bookkeeping so the next
        start_closed_room_async for this board can proceed."""
        with self._runs_lock:
            self._workers.pop(board_num, None)
            self._closed_room_callbacks.pop(board_num, None)

    def wait_for_closed_room(self, board_num: int,
                              timeout_ms: int = 60000) -> bool:
        """Block until the closed room for ``board_num`` is complete,
        keeping the Qt event loop pumping so signals can deliver and
        the UI stays responsive. Returns True on completion, False
        on timeout. Replaces the old tight polling loops in
        main_window._show_result / _show_local_closed_room.

        Uses a private QEventLoop so we don't have to spin on
        QApplication.processEvents + time.sleep, which races the
        worker's finished signal (the signal can land between an
        is_board_complete() check and the next processEvents pump,
        leaving the loop stuck until the next 100 ms tick).
        """
        if self.is_board_complete(board_num):
            return True
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)

        def _stop_loop(_result=None):
            if loop.isRunning():
                loop.quit()

        # Connect to the worker if there is one. Falls back to the
        # callback chain so the same wait works whether the caller
        # supplied a callback or not.
        with self._runs_lock:
            worker = self._workers.get(board_num)
            prev_cb = self._closed_room_callbacks.get(board_num)
        if worker is not None and worker.isRunning():
            worker.finished.connect(_stop_loop)
            # If the worker errors out, also unblock so the caller
            # sees an incomplete board rather than a 60 s hang.
            worker.error.connect(_stop_loop)
        else:
            # No worker — chain the existing callback so we still
            # exit the loop when whatever runs the closed room calls
            # _on_closed_room_complete.
            def _new_cb(result, prev=prev_cb):
                if prev is not None:
                    try:
                        prev(result)
                    except Exception:
                        pass
                _stop_loop()
            with self._runs_lock:
                self._closed_room_callbacks[board_num] = _new_cb
        timer.timeout.connect(_stop_loop)
        timer.start(max(1, int(timeout_ms)))
        loop.exec()
        timer.stop()
        return self.is_board_complete(board_num)

    def get_closed_room_result(self, board_num: int) -> Optional[BenBoardRun]:
        """Get the Closed Room result for a board if available."""
        with self._runs_lock:
            if board_num not in self.match.board_runs:
                return None
            return self.match.board_runs[board_num].get(BenTable.CLOSED)

    def get_open_room_result(self, board_num: int) -> Optional[BenBoardRun]:
        """Get the Open Room result for a board if available."""
        with self._runs_lock:
            if board_num not in self.match.board_runs:
                return None
            return self.match.board_runs[board_num].get(BenTable.OPEN)

    def stop_closed_room(self):
        """Stop every running closed-room worker (used when the match
        is torn down). Iterates a snapshot of the worker dict so
        finished-signal cleanup mutating the dict mid-loop is safe."""
        with self._runs_lock:
            snapshot = list(self._workers.values())
        for w in snapshot:
            try:
                if w.isRunning():
                    w.stop()
                    w.wait(2000)
            except RuntimeError:
                pass

    def is_board_complete(self, board_num: int) -> bool:
        """Check if both rooms have completed for a board."""
        with self._runs_lock:
            if board_num not in self.match.board_runs:
                return False
            runs = self.match.board_runs[board_num]
            open_run = runs.get(BenTable.OPEN)
            closed_run = runs.get(BenTable.CLOSED)
        return (open_run is not None and open_run.played and
                closed_run is not None and closed_run.played)

    def get_all_results(self) -> List[Dict]:
        """Get all results for display in score table.

        Returns:
            List of dicts with board results
        """
        results = []
        for board_num in sorted(self.match.board_runs.keys()):
            runs = self.match.board_runs[board_num]
            open_run = runs.get(BenTable.OPEN)
            closed_run = runs.get(BenTable.CLOSED)

            result = {
                'board_num': board_num,
                'open': open_run,
                'closed': closed_run,
                'imp_swing': self.match.get_imp_swing(board_num) if self.is_board_complete(board_num) else None
            }
            results.append(result)

        return results
