"""Deal Analysis + Evaluation of Actions — Q-Plus `.analys-ctl`
and `.analys-show`.

Two modals that wrap up the post-mortem analysis flow:

  DealAnalysisDialog — auction/card-play checkboxes + N/E/S/W seat
      radios + Start/Stop buttons. Picks WHAT to analyse and FOR
      WHOM. On Start the analyser asks Claude Opus 4.7 to walk the
      chosen actions and grade each one (good / questionable / bad),
      then opens an EvaluationOfActionsDialog with the results.

  EvaluationOfActionsDialog — flat list of every analysed action.
      Each row carries an evaluation marker (green dot = good
      / yellow = questionable / red = clear error) plus the brief
      reason Claude returned. Selecting a row could be wired to
      jump the underlying review dialog to that action; for the
      MVP the list is informational.

The previous implementation drove evaluation from BEN's
``get_card_play`` and ``get_bid`` calls, but BEN refuses to give a
prediction for out-of-sequence positions (it's a stateful neural
net), so every action came back with "(engine declined)". Claude
handles arbitrary positions with the same hit rate as a strong human
analyst, in one batched call per analysis run.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QCheckBox,
    QRadioButton, QPushButton, QLabel, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

from .dialog_style import apply_dialog_style


class DealAnalysisDialog(QDialog):
    """Pick which actions to analyse, then launch the action list."""

    def __init__(self, parent=None, board=None, auction=None,
                 tricks=None, engine=None):
        super().__init__(parent)
        self.board = board
        self.auction = auction or []
        self.tricks = tricks or []
        self.engine = engine
        self.setWindowTitle("Deal Analysis")
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # "analyse:" — auction vs card play checkboxes.
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("analyse:"))
        self.auction_cb = QCheckBox("auction")
        self.cards_cb = QCheckBox("card play")
        self.cards_cb.setChecked(True)
        row1.addWidget(self.auction_cb)
        row1.addWidget(self.cards_cb)
        row1.addStretch()
        root.addLayout(row1)

        # "of:" — seat selection (single-pick; Q-Plus actually uses
        # checkboxes here but we keep it simple with radios since
        # the analyser walks one seat at a time anyway).
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("of:"))
        self._seat_group = QButtonGroup(self)
        self.seat_radios = {}
        for sc in ('N', 'E', 'S', 'W'):
            rb = QRadioButton(
                {'N': 'North', 'E': 'East',
                 'S': 'South', 'W': 'West'}[sc])
            self._seat_group.addButton(rb)
            self.seat_radios[sc] = rb
            row2.addWidget(rb)
        # Default: South — matches Q-Plus's default seat.
        self.seat_radios['S'].setChecked(True)
        row2.addStretch()
        root.addLayout(row2)

        # "progress:" — informational, mirrors the Q-Plus label.
        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("progress:"))
        self.progress_label = QLabel("finished")
        self.progress_label.setStyleSheet(
            "QLabel { background: white; padding: 2px 6px;"
            " border: 1px solid #aaa; min-width: 120px; }")
        progress_row.addWidget(self.progress_label)
        progress_row.addStretch()
        root.addLayout(progress_row)

        # Buttons.
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._on_start)
        btns.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        btns.addWidget(self.stop_btn)
        btns.addStretch()
        help_btn = QPushButton("Help")
        help_btn.clicked.connect(self._on_help)
        btns.addWidget(help_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    def _selected_seat(self) -> str:
        for sc, rb in self.seat_radios.items():
            if rb.isChecked():
                return sc
        return 'S'

    def _on_start(self):
        from backend.models import Seat
        seat_char = self._selected_seat()
        evaluations: List[tuple] = []  # (label, status, detail, key)

        # Pass 1: collect the actions we want Claude to evaluate.
        # `key` is the prompt-side identifier ("#NN") used to match
        # Claude's per-line replies back to rows in the result table.
        actions_for_claude = []   # (key, kind, payload)
        if self.auction_cb.isChecked() and self.auction and self.board:
            dealer = self.board.dealer
            seat_chars = ['N', 'E', 'S', 'W']
            for i, bid in enumerate(self.auction):
                bidder = seat_chars[(dealer.value + i) % 4]
                if bidder != seat_char:
                    continue
                key = f"#{(i + 1):02d}"
                label = f"{key}  bid  {self._fmt_bid(bid)}"
                evaluations.append((label, 'pending', '…', key))
                actions_for_claude.append(
                    (key, 'bid', (i, self._fmt_bid(bid))))
        if self.cards_cb.isChecked() and self.tricks:
            for ti, t in enumerate(self.tricks, start=1):
                leader = t.leader if hasattr(t, 'leader') else None
                if leader is None:
                    continue
                for ci, card in enumerate(t.cards or []):
                    player_idx = (leader.value + ci) % 4
                    player_char = ['N', 'E', 'S', 'W'][player_idx]
                    if player_char != seat_char:
                        continue
                    key = f"#{ti:02d}.{ci + 1}"
                    label = f"{key}  {self._fmt_card(card)}"
                    evaluations.append((label, 'pending', '…', key))
                    actions_for_claude.append(
                        (key, 'card', (ti, ci, card)))

        if not actions_for_claude:
            EvaluationOfActionsDialog(self, evaluations).exec()
            return

        # Pass 2: build one Claude prompt covering every action and
        # parse the per-line response back into the table.
        prompt = self._build_claude_prompt(seat_char, actions_for_claude)
        claude_text, claude_err = self._run_claude(prompt)
        if claude_err == "cancelled":
            return

        if claude_err:
            # Surface the failure on every pending row so the user
            # can see what went wrong instead of bare "(engine
            # declined)" markers.
            evaluations = [
                (lbl, 'neutral', f"(Claude error: {claude_err})", key)
                for (lbl, _st, _dt, key) in evaluations
            ]
        else:
            verdicts = self._parse_claude_verdicts(claude_text)
            evaluations = [
                (lbl, *verdicts.get(key, ('neutral', '(no verdict)')), key)
                for (lbl, _st, _dt, key) in evaluations
            ]

        result = EvaluationOfActionsDialog(self, evaluations)
        result.exec()

    # ---- Claude prompt + parsing --------------------------------------

    def _build_claude_prompt(self, seat_char: str, actions) -> str:
        """Build a single prompt that asks Claude to grade every action.

        The shape is deliberately rigid — one line per action — so the
        regex parser in `_parse_claude_verdicts` lines up reliably.
        """
        from backend.models import Seat

        lines = [
            "You are an expert bridge analyst. Grade each action below",
            "from the perspective of the named seat.",
            "",
            "Use exactly this format, one line per action, no preamble:",
            "  <KEY> | good | <≤80-char reason>",
            "  <KEY> | questionable | <≤80-char reason>",
            "  <KEY> | bad | <≤80-char reason>",
            "where <KEY> is the identifier in the action list",
            "(e.g. #03 or #02.4). Be concise. Do not add headers,",
            "summaries, or markdown.",
            "",
            "Deal:",
        ]
        if self.board is not None:
            for s in (Seat.NORTH, Seat.EAST, Seat.SOUTH, Seat.WEST):
                hand = self.board.hands.get(s)
                if hand is not None:
                    lines.append(f"  {s.to_char()}: {self._fmt_hand(hand)}")
            v = getattr(self.board, 'vulnerability', None)
            d = getattr(self.board, 'dealer', None)
            if v is not None and d is not None:
                lines.append(f"  Dealer: {d.to_char()}    Vul: {v.value}")
        if self.auction:
            lines.append("")
            lines.append("Auction (in dealer order):")
            lines.append("  " + "  ".join(self._fmt_bid(b) for b in self.auction))
        if self.tricks:
            lines.append("")
            lines.append("Play (each trick is `leader: c1 c2 c3 c4`):")
            for ti, t in enumerate(self.tricks, start=1):
                leader = t.leader.to_char() if t.leader is not None else '?'
                cs = " ".join(self._fmt_card(c) for c in (t.cards or []))
                w = (f"  (won by {t.winner.to_char()})"
                     if getattr(t, 'winner', None) is not None else "")
                lines.append(f"  #{ti:02d} {leader}: {cs}{w}")
        try:
            from ui.teaching_view import signalling_conventions_text
            lines.append("")
            lines.extend(signalling_conventions_text().split("\n"))
        except Exception:
            pass
        lines.append("")
        lines.append(f"Seat to grade: {seat_char}")
        lines.append("")
        lines.append("Actions:")
        for key, kind, payload in actions:
            if kind == 'bid':
                _, bid_str = payload
                lines.append(f"  {key}  bid  {bid_str}")
            else:
                ti, ci, card = payload
                lines.append(f"  {key}  card  {self._fmt_card(card)} "
                             f"(trick {ti}, position {ci + 1})")
        lines.append("")
        lines.append("Begin output:")
        return "\n".join(lines)

    @staticmethod
    def _fmt_hand(hand) -> str:
        """Compact suit-grouped representation of a hand."""
        from backend.models import Suit
        by_suit = {Suit.SPADES: [], Suit.HEARTS: [],
                   Suit.DIAMONDS: [], Suit.CLUBS: []}
        for c in hand.cards:
            by_suit[c.suit].append(c.rank)
        parts = []
        for s, sym in [(Suit.SPADES, '♠'), (Suit.HEARTS, '♥'),
                       (Suit.DIAMONDS, '♦'), (Suit.CLUBS, '♣')]:
            ranks = sorted(by_suit[s], key=lambda r: -r.value
                           if hasattr(r, 'value') else 0)
            chars = ''.join(r.to_char() if hasattr(r, 'to_char')
                            else str(r) for r in ranks) or '—'
            parts.append(f"{sym}{chars}")
        return ' '.join(parts)

    def _run_claude(self, prompt: str):
        """Invoke Claude Opus 4.7 with a progress dialog.

        Returns (text, error). Either may be None. Mirrors the shape
        of MainWindow._run_claude_with_dialog but returns the raw
        text so the caller can parse line-by-line verdicts instead
        of dumping it into a result dialog.
        """
        import shutil
        import subprocess
        import threading
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                                     QProgressBar, QApplication, QPushButton)
        from PyQt6.QtGui import QFont

        if not shutil.which('claude'):
            return None, "Claude CLI not installed on this machine"

        progress = QDialog(self)
        progress.setWindowTitle("Deal Analysis")
        progress.setFixedSize(460, 140)
        p_layout = QVBoxLayout(progress)
        p_label = QLabel("Claude is grading your actions…")
        p_label.setFont(QFont("Arial", 12))
        p_layout.addWidget(p_label)
        p_bar = QProgressBar()
        p_bar.setRange(0, 0)
        p_layout.addWidget(p_bar)
        state = {'cancelled': False, 'done': False, 'proc': None}

        def _cancel():
            # Closing the dialog after success also emits `rejected`.
            if state['done']:
                return
            state['cancelled'] = True
            proc = state['proc']
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(_cancel)
        progress.rejected.connect(_cancel)
        p_layout.addWidget(cancel_btn)
        progress.show()
        QApplication.processEvents()

        result = {'text': None, 'error': None}

        def _run():
            try:
                # stdin closed: an inherited open stdin makes `claude -p`
                # wait for piped input forever. Popen so Cancel can kill it.
                proc = subprocess.Popen(
                    ['claude', '-p',
                     '--model', 'claude-opus-4-8',
                     '--max-turns', '1', prompt],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                state['proc'] = proc
                try:
                    out, err = proc.communicate(timeout=600)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    result['error'] = "claude timed out (600 s)"
                    return
                if proc.returncode == 0 and (out or '').strip():
                    result['text'] = out.strip()
                else:
                    result['error'] = (
                        (err or '').strip()[:160]
                        or f"claude exit {proc.returncode}")
            except Exception as ex:
                result['error'] = f"{ex!r}"

        import time
        t0 = time.monotonic()
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        while thread.is_alive() and not state['cancelled']:
            QApplication.processEvents()
            thread.join(timeout=0.1)
            el = int(time.monotonic() - t0)
            try:
                p_label.setText(f"Claude is grading your actions… "
                                f"{el // 60}:{el % 60:02d} (usually 1-3 minutes)")
            except RuntimeError:
                # The window was closed under us: Qt deleted the dialog.
                _cancel()
                break
        state['done'] = True
        try:
            progress.close()
        except RuntimeError:
            pass
        if state['cancelled']:
            return None, "cancelled"
        return result['text'], result['error']

    @staticmethod
    def _parse_claude_verdicts(text: str) -> dict:
        """Parse Claude's per-action grading into {key: (status, reason)}.

        Lines look like ``#03 | good | reason`` or ``#02.4 | bad | …``.
        Anything that doesn't match is skipped — the result dict is
        keyed by the action identifier so the caller can render a
        neutral marker for any key Claude omitted.
        """
        import re
        out = {}
        if not text:
            return out
        line_re = re.compile(
            r"^\s*(#\d+(?:\.\d+)?)\s*\|\s*"
            r"(good|questionable|bad)\s*\|\s*(.+?)\s*$",
            re.IGNORECASE)
        status_map = {
            'good': 'good',
            'questionable': 'neutral',
            'bad': 'bad',
        }
        for ln in text.splitlines():
            m = line_re.match(ln)
            if not m:
                continue
            key, verdict, reason = m.group(1), m.group(2).lower(), m.group(3)
            out[key] = (status_map.get(verdict, 'neutral'), reason)
        return out

    @staticmethod
    def _fmt_bid(bid) -> str:
        try:
            return bid.symbol()
        except Exception:
            return str(bid)

    @staticmethod
    def _fmt_card(card) -> str:
        try:
            sym = {0: '♠', 1: '♥', 2: '♦', 3: '♣'}[card.suit.value]
            return f"{sym} {card.rank.to_char()}"
        except Exception:
            return str(card)

    def _on_help(self):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Deal Analysis",
            "Pick which actions to analyse (auction, card play, or "
            "both) and for whom. Click Start to walk the chosen "
            "actions and compare each one against the engine's "
            "recommendation — agreement shows green, disagreement "
            "red.")


class EvaluationOfActionsDialog(QDialog):
    """Q-Plus `.analys-show` — list view of evaluated actions.

    Two columns: Action (formatted label) and Evaluation (coloured
    marker + brief detail). Selecting a row could be wired to
    re-position the underlying review walker; the MVP leaves that
    hook open.
    """

    def __init__(self, parent=None, evaluations: Optional[List[tuple]] = None):
        super().__init__(parent)
        self.evaluations = evaluations or []
        self.setWindowTitle("Evaluation of actions")
        self.resize(420, 560)
        apply_dialog_style(self)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        self.table = QTableWidget(len(self.evaluations), 2, self)
        self.table.setHorizontalHeaderLabels(
            ["Action", "Evaluation"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        for row, entry in enumerate(self.evaluations):
            # Accept both legacy 3-tuples (label, status, detail) and
            # the new 4-tuples (label, status, detail, key). The key
            # is only needed by future row-click navigation.
            label, status, detail = entry[0], entry[1], entry[2]
            self.table.setItem(row, 0, QTableWidgetItem(label))
            # Marker glyph: ● in green / yellow / red / grey.
            marker = "●"
            colors = {
                'good': QColor(0, 160, 50),
                'bad': QColor(192, 0, 0),
                'neutral': QColor(180, 140, 0),   # questionable = amber
                'pending': QColor(140, 140, 140),
            }
            item = QTableWidgetItem(f"{marker}  {detail}")
            item.setForeground(colors.get(status, QColor(128, 128, 128)))
            item.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            self.table.setItem(row, 1, item)
        root.addWidget(self.table)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)
