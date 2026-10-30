"""
Hand Evaluation Dialog - shows detailed hand evaluation including point counts,
suit lengths, controls, and stoppers.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QCheckBox, QComboBox, QGroupBox,
    QFrame, QTextEdit, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from .dialog_style import apply_dialog_style, make_detachable
from backend.models import Rank


class HandEvaluationDialog(QDialog):
    """Dialog showing detailed evaluation of a hand."""

    SUIT_SYMBOLS = {'S': '\u2660', 'H': '\u2665', 'D': '\u2666', 'C': '\u2663'}
    SUIT_COLORS = {'S': '#000000', 'H': '#cc0000', 'D': '#cc0000', 'C': '#000000'}

    def __init__(self, parent=None, seat=None, hand=None, bid_round=1, board=None,
                 ask_seat=None, view=None):
        super().__init__(parent)
        self.seat = seat
        # `view` (backend.hand_evaluation.evaluate): exact for a hand the
        # asker sees (its original 13 cards), else the asker's belief.
        self.view = view
        if view is not None and view.get("exact"):
            hand = view.get("hand") or hand
        self.hand = hand
        self.bid_round = bid_round
        self.board = board
        self.ask_seat = ask_seat

        seat_name = seat.name.capitalize() if seat is not None else "Player"
        ask_name = ask_seat.name.capitalize() if ask_seat is not None else seat_name
        self.setWindowTitle(f"Bidround {bid_round}: {ask_name}'s evaluation of {seat_name}'s hand")
        self.setMinimumWidth(950)
        self.setMinimumHeight(650)
        self.resize(1000, 700)
        apply_dialog_style(self)
        # Scale fonts for 1920x1080 readability
        self.setStyleSheet(self.styleSheet() + """
            QLabel { font-size: 14px; }
            QLineEdit { font-size: 14px; padding: 3px; }
            QComboBox { font-size: 14px; padding: 3px; }
            QCheckBox { font-size: 14px; }
            QPushButton { font-size: 14px; padding: 6px 12px; }
            QGroupBox { font-size: 14px; }
            QFrame QLabel { font-size: 13px; border: none; background: transparent; }
        """)

        self._setup_ui()
        self._populate_values()

        # Promote to a real top-level window so the user can drag it off
        # the main BridgeIQ window onto a second monitor (default Qt
        # behavior pins QDialogs to the parent on most Linux WMs).
        make_detachable(self)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Top row: bid, artificial, forcing, name
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("bid:"))
        self.bid_edit = QLineEdit()
        self.bid_edit.setMaximumWidth(80)
        self.bid_edit.setReadOnly(True)
        top_row.addWidget(self.bid_edit)

        self.artificial_cb = QCheckBox("artif.")
        self.artificial_cb.setEnabled(False)
        top_row.addWidget(self.artificial_cb)

        self.forcing_cb = QCheckBox("forc.")
        self.forcing_cb.setEnabled(False)
        top_row.addWidget(self.forcing_cb)

        top_row.addWidget(QLabel("name:"))
        self.name_combo = QComboBox()
        self.name_combo.setMinimumWidth(150)
        self.name_combo.setEnabled(False)
        top_row.addWidget(self.name_combo)
        top_row.addStretch()

        layout.addLayout(top_row)

        # Points section
        points_frame = QFrame()
        # Scoped by name: a bare "QFrame" rule also boxes every QLabel
        # (QLabel is a QFrame), which truncated the captions.
        points_frame.setObjectName("points_frame")
        points_frame.setStyleSheet("""
            QFrame#points_frame {
                background-color: #f5f5f5;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
            }
        """)
        points_layout = QGridLayout(points_frame)
        points_layout.setSpacing(6)

        # Trump selector — changing this recomputes TP for the new
        # trump (declarer gets distribution points for short SIDE
        # suits, so the trump matters even when LP is fixed).
        points_layout.addWidget(QLabel("trump"), 0, 0)
        self.trump_combo = QComboBox()
        self.trump_combo.addItems(['NT', 'S', 'H', 'D', 'C'])
        self.trump_combo.setMaximumWidth(60)
        self.trump_combo.currentTextChanged.connect(
            lambda *_: self._populate_values())
        points_layout.addWidget(self.trump_combo, 1, 0)

        # HP, LP, TP rows
        headers = ['', 'min', 'exp.', 'max']
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            points_layout.addWidget(lbl, 0, col + 1)

        point_types = [
            ('HP [0.0 - 40.0]:', 'hp'),
            ('LP [0.0 - 40.0]:', 'lp'),
            ('TP [0.0 - 40.0]:', 'tp'),
        ]

        self.point_edits = {}
        for row, (label, key) in enumerate(point_types, 1):
            points_layout.addWidget(QLabel(label), row, 1)
            for col, field in enumerate(['min', 'exp', 'max'], 2):
                edit = QLineEdit()
                edit.setMaximumWidth(50)
                edit.setReadOnly(True)
                edit.setAlignment(Qt.AlignmentFlag.AlignRight)
                self.point_edits[f"{key}_{field}"] = edit
                points_layout.addWidget(edit, row, col)

        # Aces and Kings row
        row = 4
        points_layout.addWidget(QLabel("# aces  min"), row, 1)
        self.aces_plus = QLineEdit()
        self.aces_plus.setMaximumWidth(40)
        self.aces_plus.setReadOnly(True)
        points_layout.addWidget(self.aces_plus, row, 2)
        points_layout.addWidget(QLabel("max"), row, 3)
        self.aces_minus = QLineEdit()
        self.aces_minus.setMaximumWidth(40)
        self.aces_minus.setReadOnly(True)
        points_layout.addWidget(self.aces_minus, row, 4)
        self.aces_result = QLineEdit()
        self.aces_result.setVisible(False)
        self.aces_result.setMaximumWidth(40)
        self.aces_result.setReadOnly(True)
        points_layout.addWidget(self.aces_result, row, 6)
        points_layout.addWidget(QLabel("| exp. [0.0 - 4.0]:"), row, 7)
        self.aces_exp = QLineEdit()
        self.aces_exp.setMaximumWidth(40)
        self.aces_exp.setReadOnly(True)
        points_layout.addWidget(self.aces_exp, row, 8)

        row = 5
        points_layout.addWidget(QLabel("# kings  min"), row, 1)
        self.kings_plus = QLineEdit()
        self.kings_plus.setMaximumWidth(40)
        self.kings_plus.setReadOnly(True)
        points_layout.addWidget(self.kings_plus, row, 2)
        points_layout.addWidget(QLabel("max"), row, 3)
        self.kings_minus = QLineEdit()
        self.kings_minus.setMaximumWidth(40)
        self.kings_minus.setReadOnly(True)
        points_layout.addWidget(self.kings_minus, row, 4)
        self.kings_result = QLineEdit()
        self.kings_result.setVisible(False)
        self.kings_result.setMaximumWidth(40)
        self.kings_result.setReadOnly(True)
        points_layout.addWidget(self.kings_result, row, 6)
        points_layout.addWidget(QLabel("| exp. [0.0 - 4.0]:"), row, 7)
        self.kings_exp = QLineEdit()
        self.kings_exp.setMaximumWidth(40)
        self.kings_exp.setReadOnly(True)
        points_layout.addWidget(self.kings_exp, row, 8)

        row = 6
        points_layout.addWidget(QLabel("aces[2] + kings[1] = [0 - 12]"), row, 1, 1, 4)
        self.controls_total = QLineEdit()
        self.controls_total.setMaximumWidth(40)
        self.controls_total.setReadOnly(True)
        points_layout.addWidget(self.controls_total, row, 5)
        self.any_void_cb = QCheckBox("any void")
        self.any_void_cb.setEnabled(False)
        points_layout.addWidget(self.any_void_cb, row, 6)
        self.any_single_cb = QCheckBox("any single")
        self.any_single_cb.setEnabled(False)
        points_layout.addWidget(self.any_single_cb, row, 7)

        layout.addWidget(points_frame)

        # Suit rows
        suits_frame = QFrame()
        # Scoped by name: a bare "QFrame" rule also boxes every QLabel
        # (QLabel is a QFrame), which truncated the captions.
        suits_frame.setObjectName("suits_frame")
        suits_frame.setStyleSheet("""
            QFrame#suits_frame {
                background-color: #f5f5f5;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
            }
        """)
        suits_layout = QVBoxLayout(suits_frame)

        self.suit_widgets = {}
        for suit in ['S', 'H', 'D', 'C']:
            suit_row = self._create_suit_row(suit)
            suits_layout.addLayout(suit_row)

        layout.addWidget(suits_frame)

        # Dependencies text area
        dep_layout = QHBoxLayout()
        dep_layout.addWidget(QLabel("dependencies"))
        self.dependencies_edit = QTextEdit()
        self.dependencies_edit.setMaximumHeight(80)
        self.dependencies_edit.setReadOnly(True)
        dep_layout.addWidget(self.dependencies_edit)
        layout.addLayout(dep_layout)

        # Buttons
        button_layout = QHBoxLayout()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setMinimumWidth(80)
        button_layout.addWidget(self.close_btn)

        button_layout.addStretch()

        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumWidth(80)
        button_layout.addWidget(self.help_btn)

        layout.addLayout(button_layout)

    def _create_suit_row(self, suit):
        """Create a row for suit evaluation."""
        row_layout = QHBoxLayout()

        # Suit symbol
        symbol = self.SUIT_SYMBOLS[suit]
        color = self.SUIT_COLORS[suit]
        suit_label = QLabel(f'<span style="color:{color}; font-size:16px;">{symbol}</span>')
        suit_label.setMinimumWidth(20)
        row_layout.addWidget(suit_label)

        widgets = {}

        # Length fields
        row_layout.addWidget(QLabel("length  min"))
        widgets['len_min'] = QLineEdit()
        widgets['len_min'].setMaximumWidth(40)
        widgets['len_min'].setReadOnly(True)
        row_layout.addWidget(widgets['len_min'])

        row_layout.addWidget(QLabel("exp."))
        widgets['len_exp'] = QLineEdit()
        widgets['len_exp'].setMaximumWidth(40)
        widgets['len_exp'].setReadOnly(True)
        row_layout.addWidget(widgets['len_exp'])

        row_layout.addWidget(QLabel("max"))
        widgets['len_max'] = QLineEdit()
        widgets['len_max'].setMaximumWidth(40)
        widgets['len_max'].setReadOnly(True)
        row_layout.addWidget(widgets['len_max'])

        row_layout.addWidget(QLabel("exp. HP"))
        widgets['hp_exp'] = QLineEdit()
        widgets['hp_exp'].setMaximumWidth(40)
        widgets['hp_exp'].setReadOnly(True)
        row_layout.addWidget(widgets['hp_exp'])

        # Second line info (percentages)
        row_layout.addWidget(QLabel("%  A"))
        widgets['pct_a'] = QLineEdit()
        widgets['pct_a'].setMaximumWidth(35)
        widgets['pct_a'].setReadOnly(True)
        row_layout.addWidget(widgets['pct_a'])

        row_layout.addWidget(QLabel("K"))
        widgets['pct_k'] = QLineEdit()
        widgets['pct_k'].setMaximumWidth(35)
        widgets['pct_k'].setReadOnly(True)
        row_layout.addWidget(widgets['pct_k'])

        row_layout.addWidget(QLabel("Q"))
        widgets['pct_q'] = QLineEdit()
        widgets['pct_q'].setMaximumWidth(35)
        widgets['pct_q'].setReadOnly(True)
        row_layout.addWidget(widgets['pct_q'])

        # Control dropdown
        row_layout.addWidget(QLabel("control"))
        widgets['control'] = QComboBox()
        widgets['control'].addItems(['--', '1st', '2nd'])
        widgets['control'].setMaximumWidth(60)
        widgets['control'].setEnabled(False)
        row_layout.addWidget(widgets['control'])

        # Stopper dropdown
        row_layout.addWidget(QLabel("stopper"))
        widgets['stopper'] = QComboBox()
        widgets['stopper'].addItems(['--', 'yes', 'no', 'half'])
        widgets['stopper'].setMaximumWidth(60)
        widgets['stopper'].setEnabled(False)
        row_layout.addWidget(widgets['stopper'])

        row_layout.addStretch()

        self.suit_widgets[suit] = widgets
        return row_layout

    def _populate_values(self):
        """Populate fields with actual hand evaluation. Re-runs on
        every trump-combo change so TP / distribution points stay
        in sync."""
        if self.view is not None and not self.view.get("exact"):
            self._populate_belief(self.view)
            return
        self._populate_last_call()
        if not self.hand:
            return

        # --- HP ------------------------------------------------------
        hcp = self.hand.hcp()
        self.point_edits['hp_exp'].setText(f"{hcp:.1f}")
        self.point_edits['hp_min'].setText(f"{hcp:.0f}")
        self.point_edits['hp_max'].setText(f"{hcp:.0f}")

        # --- Per-suit metrics (length, suit HCP, stoppers, etc.) ----
        from backend.models import Suit
        suit_objs = {
            'S': Suit.SPADES, 'H': Suit.HEARTS,
            'D': Suit.DIAMONDS, 'C': Suit.CLUBS,
        }
        lengths = {}
        suit_hcps = {}
        mod_lengths = {}      # spec: mS / mH / mD / mC
        stoppers = {}         # spec stopper categories: >0 / >=0 / <=0 / <0

        has_void = False
        has_singleton = False

        for suit_key, suit in suit_objs.items():
            suit_cards = self.hand.get_suit_cards(suit) or []
            length = len(suit_cards)
            lengths[suit_key] = length
            if length == 0:
                has_void = True
            elif length == 1:
                has_singleton = True

            suit_hcp = sum(max(0, 4 - c.rank.value) for c in suit_cards)
            suit_hcps[suit_key] = suit_hcp

            # Modified length = length + strength bonus. Spec examples:
            #   C = 6 → mC >= 6.0 (six-card with at least average)
            #   S = 4 → mS >= 4.2 (good four-card suit)
            # A reasonable approximation: add (suit_hcp / 10) to the
            # raw length so AKQxxx (length 6, HCP 9) → 6.9; a flat
            # 6-card with no honours stays at 6.0.
            mod_lengths[suit_key] = length + suit_hcp / 10.0

            has_ace = any(c.rank == Rank.ACE for c in suit_cards)
            has_king = any(c.rank == Rank.KING for c in suit_cards)
            has_queen = any(c.rank == Rank.QUEEN for c in suit_cards)

            # Q-Plus stopper categories (.bid-eval-out):
            #   > 0  : surely stopped
            #   >= 0 : likely stopped
            #   <= 0 : likely not stopped
            #   < 0  : surely not stopped
            if has_ace:
                stoppers[suit_key] = '> 0'
            elif has_king and length >= 2:
                stoppers[suit_key] = '> 0'
            elif has_queen and length >= 3:
                stoppers[suit_key] = '>= 0'
            elif has_king or (has_queen and length >= 2):
                stoppers[suit_key] = '<= 0'
            else:
                stoppers[suit_key] = '< 0'

            widgets = self.suit_widgets.get(suit_key)
            if widgets:
                widgets['len_min'].setText(str(length))
                widgets['len_exp'].setText(f"{length:.1f}")
                widgets['len_max'].setText(str(length))
                widgets['hp_exp'].setText(f"{suit_hcp:.1f}")
                widgets['pct_a'].setText("100" if has_ace else "0")
                widgets['pct_k'].setText("100" if has_king else "0")
                widgets['pct_q'].setText("100" if has_queen else "0")
                if has_ace:
                    widgets['control'].setCurrentText('1st')
                elif has_king:
                    widgets['control'].setCurrentText('2nd')
                else:
                    widgets['control'].setCurrentText('--')
                # Map the Q-Plus category back to the dropdown's
                # legacy values so the existing items still cover the
                # case-set. yes = surely stopped; half = likely;
                # no = unlikely / never.
                cat = stoppers[suit_key]
                stopper_label = {
                    '> 0':  'yes',
                    '>= 0': 'half',
                    '<= 0': 'half',
                    '< 0':  'no',
                }[cat]
                widgets['stopper'].setCurrentText(stopper_label)

        # --- LP / TP -------------------------------------------------
        # Length points: one for every card over 4 in each suit (the
        # most common "Goren / Modern" convention; matches what Q-Plus
        # describes by LP).
        lp = sum(max(0, l - 4) for l in lengths.values())
        self.point_edits['lp_exp'].setText(f"{lp:.1f}")
        self.point_edits['lp_min'].setText(f"{lp:.0f}")
        self.point_edits['lp_max'].setText(f"{lp:.0f}")

        # Trump-dependent TP: declarer (with a known trump fit) adds
        # distribution points for shortness in SIDE suits — void=3,
        # singleton=2, doubleton=1. For NT, TP = HP only (length
        # points are already in the long-suit metrics; we keep LP in
        # the displayed TP for usefulness against the no-trump
        # column too).
        trump_char = self.trump_combo.currentText()
        if trump_char == 'NT':
            tp = hcp + lp
        else:
            dist = 0
            for k, l in lengths.items():
                if k == trump_char:
                    continue
                if l == 0:
                    dist += 3
                elif l == 1:
                    dist += 2
                elif l == 2:
                    dist += 1
            tp = hcp + lp + dist
        self.point_edits['tp_exp'].setText(f"{tp:.1f}")
        self.point_edits['tp_min'].setText(f"{tp:.0f}")
        self.point_edits['tp_max'].setText(f"{tp:.0f}")

        # --- Aces & Kings ------------------------------------------
        aces = sum(1 for c in self.hand.cards if c.rank == Rank.ACE)
        kings = sum(1 for c in self.hand.cards if c.rank == Rank.KING)
        self.aces_plus.setText(str(aces))
        self.aces_minus.setText(str(aces))
        self.aces_result.setText(str(aces))
        self.aces_exp.setText(f"{aces:.1f}")
        self.kings_plus.setText(str(kings))
        self.kings_minus.setText(str(kings))
        self.kings_result.setText(str(kings))
        self.kings_exp.setText(f"{kings:.1f}")
        self.controls_total.setText(str(aces * 2 + kings))
        self.any_void_cb.setChecked(has_void)
        self.any_single_cb.setChecked(has_singleton)

        # --- Dependencies block -----------------------------------
        # Generate the factual constraints the spec demonstrates with
        # examples like "H <= 2 & S >= 3 -> HP >= 18". When the hand
        # is known we render concrete equalities; when only a partial
        # hand is known the same machinery applies once a bid-meaning
        # database lands.
        dep_lines = []
        for k in ('S', 'H', 'D', 'C'):
            dep_lines.append(
                f"{k} = {lengths[k]} -> m{k} = {mod_lengths[k]:.1f}"
                f"   HP~{k} = {hcp - suit_hcps[k]}")
        # Longest-suit shorthand (S1 = longest length, mS1 = its
        # modified length) — spec lists these in the legend.
        sorted_keys = sorted(lengths,
                             key=lambda k: (-lengths[k], k))
        for i, k in enumerate(sorted_keys, start=1):
            dep_lines.append(
                f"S{i} = {k}: length {lengths[k]}, "
                f"mS{i} = {mod_lengths[k]:.1f}")
        # Stopper summary, one line per suit using the Q-Plus
        # > 0 / >= 0 / <= 0 / < 0 notation.
        for k in ('S', 'H', 'D', 'C'):
            dep_lines.append(f"s{k} {stoppers[k]} (stopper)")
        dep_lines.append(
            f"HP = {hcp}, LP = {lp:.0f}, "
            f"TP({trump_char}) = {tp:.1f}, "
            f"aces[2]+kings[1] = {aces * 2 + kings}")
        self.dependencies_edit.setPlainText("\n".join(dep_lines))

    # --- shared ---------------------------------------------------------
    def _populate_last_call(self):
        """Top row: the seat's latest call, as Q-Plus shows it."""
        board, seat = self.board, self.seat
        if board is None or seat is None or not getattr(board, "auction", None):
            return
        idx = [i for i in range(len(board.auction))
               if (board.dealer.value + i) % 4 == seat.value]
        if not idx:
            return
        i = idx[-1]
        bid = board.auction[i]
        self.bid_edit.setText(bid.to_str())
        self.artificial_cb.setChecked(bool(getattr(bid, "alert", False)))
        try:
            from backend.bid_descriptions import describe_bid, system_for_prefs
            from backend.config import get_config_manager
            prefs = get_config_manager().config.preferences
            sys_ = system_for_prefs(prefs, 'NS' if seat.is_ns() else 'EW')
            _pts, _len, text, art = describe_bid(bid, board.auction[:i], seat,
                                                 board.dealer, sys_)
            if art:
                self.artificial_cb.setChecked(True)
            if text:
                self.name_combo.clear()
                self.name_combo.addItem(text)
        except Exception:
            pass

    # --- a hand the asker can't see ---------------------------------------
    def _populate_belief(self, v):
        """The asker's belief: ranges / expectations / honour odds over the
        layouts consistent with what it knows."""
        from backend.models import Suit
        from backend.hand_evaluation import length_points, total_points
        self._populate_last_call()

        def put(prefix, rng3, digits=0):
            lo, ex, hi = rng3
            self.point_edits[f"{prefix}_min"].setText(f"{lo:.0f}")
            self.point_edits[f"{prefix}_exp"].setText(f"{ex:.1f}")
            self.point_edits[f"{prefix}_max"].setText(f"{hi:.0f}")

        put("hp", v["hp"])
        lh = v["lens_hp"]
        n = len(lh)
        lps = [length_points(lens) for _hp, lens in lh]
        put("lp", (min(lps), sum(lps) / n, max(lps)))
        trump_char = self.trump_combo.currentText()
        trump = None if trump_char == 'NT' else Suit.from_char(trump_char)
        tps = [total_points(hp, lens, trump) for hp, lens in lh]
        put("tp", (min(tps), sum(tps) / n, max(tps)))

        a_lo, a_ex, a_hi = v["aces"]
        k_lo, k_ex, k_hi = v["kings"]
        self.aces_plus.setText(f"{a_lo:.0f}")
        self.aces_minus.setText(f"{a_hi:.0f}")
        self.aces_exp.setText(f"{a_ex:.1f}")
        self.kings_plus.setText(f"{k_lo:.0f}")
        self.kings_minus.setText(f"{k_hi:.0f}")
        self.kings_exp.setText(f"{k_ex:.1f}")
        self.controls_total.setText(f"{2 * a_ex + k_ex:.1f}")
        self.any_void_cb.setChecked(v["p_void"] >= 0.5)
        self.any_void_cb.setText(f"any void {v['p_void']:.0%}")
        self.any_single_cb.setChecked(v["p_single"] >= 0.5)
        self.any_single_cb.setText(f"any single {v['p_single']:.0%}")

        for key, suit in (('S', Suit.SPADES), ('H', Suit.HEARTS),
                          ('D', Suit.DIAMONDS), ('C', Suit.CLUBS)):
            w = self.suit_widgets.get(key)
            sv = v["suits"][suit]
            lo, ex, hi = sv["len"]
            w['len_min'].setText(f"{lo:.0f}")
            w['len_exp'].setText(f"{ex:.1f}")
            w['len_max'].setText(f"{hi:.0f}")
            w['hp_exp'].setText(f"{sv['hp_exp']:.1f}")
            w['pct_a'].setText(f"{100 * sv['pA']:.0f}")
            w['pct_k'].setText(f"{100 * sv['pK']:.0f}")
            w['pct_q'].setText(f"{100 * sv['pQ']:.0f}")
            if sv['pA'] >= 0.5:
                w['control'].setCurrentText('1st')
            elif sv['pA'] + sv['pK'] >= 0.5:
                w['control'].setCurrentText('2nd')
            else:
                w['control'].setCurrentText('--')
            w['stopper'].setCurrentText(
                'yes' if sv['stop'] >= 0.9 else 'half' if sv['stop'] >= 0.4 else 'no')

        asker = (self.ask_seat.name.capitalize() if self.ask_seat is not None
                 else "The asker")
        lines = [f"{asker} can't see this hand: the figures are over "
                 f"{v['samples']} layouts that fit {asker}'s cards, dummy, "
                 f"the play so far and every call (as biq's bidder makes them)."]
        if v.get("relaxed"):
            lines.append("(Few layouts fit every call exactly; the closest "
                         "ones are included.)")
        reasons = v.get("reasons") or []
        if reasons:
            lines.append("What the calls and play showed:")
            lines += [f"  - {r}" for r in reasons]
        self.dependencies_edit.setPlainText("\n".join(lines))
