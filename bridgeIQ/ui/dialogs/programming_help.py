"""Programming-language Rosetta Stone for bridge.

For users new to bridge, the language of bidding systems
(SAYC, 2/1, Acol, …), conventions (Stayman, Jacoby, Blackwood,
splinters, …) and defensive carding (attitude, count, suit
preference, …) is gobbledygook. The analogy below maps each
bridge concept onto a programming-language concept. It is not a
precise correspondence — but it is a faithful enough Rosetta
Stone that programming intuition can scaffold the bridge side
while it is being learned.

The dialog wraps a scrollable QTextEdit rendering the help text
as Markdown (tables + bullet lists). Opened from
Help → Bridge for Programmers in the main menu.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt


ROSETTA_HELP_MARKDOWN = """\
# Bridge for Programmers — A Rosetta Stone

If you are new to bridge and find the language of bidding
systems, conventions, and defensive carding unfamiliar, this
help text maps each bridge concept onto a programming-language
concept. It is an analogy, not a precise correspondence — but
it is a faithful enough Rosetta Stone that programming
intuition can scaffold the bridge side while it is being
learned.

Three layers, working from outside in:

1. The **bidding system** you play (SAYC, 2/1, Acol, French,
   Precision) is a **programming language**.
2. Specific **conventions** (Stayman, Jacoby, Blackwood,
   splinters, …) are **libraries and type-system features**.
3. **Defensive carding and signaling** is the **IPC protocol**
   between two cooperating processes (the defenders) over a
   public channel with an adversarial eavesdropper (declarer).

---

## Part 1: Bidding Systems → Programming Languages

The five systems bridgeIQ models, each mapped to a language
whose design philosophy matches:

| Bidding system | Language | Why |
|---|---|---|
| **SAYC** (Standard American Yellow Card) | **Python** | The "official standard." Designed by committee (ACBL → PSF), batteries-included, deliberately readable, the default when partners don't know each other or share no prior config. Not the most expressive, but you can sit down with any other player on Earth and the auction will mostly work. |
| **TwoOverOne** (2/1 Game Force) | **TypeScript** | SAYC with stricter typing in the game-forcing zone. Most auctions transpile to SAYC, but the 2-over-1 response declares "this is at least game" the way `: Promise<T>` declares a contract — once you've made the call, the partnership is statically committed to it, and the runtime (cardplay) has less inferential work to do. |
| **Standard Acol** (British) | **Common Lisp** | Old, venerable, dialect-rich (Acol-as-played-in-Edinburgh ≠ Acol-as-played-in-Sydney). 4-card majors are the macros — flexible openings that mean what the partnership has agreed they mean. Tremendous expressive power for fluent partnerships; novices flounder because there's no system card to read off the call. Weak NT (12-14) is aggressive type narrowing — commit to a tight range up front, deal with the consequences later. |
| **Standard French** | **OCaml** | French academic origin (INRIA / FFB), sophisticated type system (forcing 1NT relay with specific rebid pattern = pattern-matching on opener's hand class), beloved by adherents, niche internationally but respected. The standardization comes from authority (FFB system notes) the way OCaml's comes from the compiler team. |
| **Precision** | **Rust** | The strong 1♣ is the borrow checker — it *owns* the 16+ HCP space, and every other opening is bounded by that ownership: 1M is 11-15, not "any opening hand," because 1♣ already claimed strong. This up-front strictness lets every subsequent bid be much more precisely typed than in natural systems. Steep learning curve, smaller user base, devoted enthusiasts who value the guarantees. |

---

## Part 2: Conventions → Libraries / Type-System Features

Where the *system* is the language, *conventions* are libraries
you `import`. Some libraries are convention-specific (Drury
only fits passed-hand 1M openings); others are universally
portable (Blackwood works in any system that bids slams).

| Convention | Programming analog |
|---|---|
| **Stayman** (1NT → 2♣) | `Optional<Major>` getter on opener's hand. The 2♣ call is a synthetic accessor — it doesn't promise clubs the way `getName()` doesn't promise to be the user's actual name. Three response branches: `None` (2♦), `Hearts`, `Spades`. |
| **Jacoby transfer** (1NT → 2♦/2♥) | Method dispatch with a role swap. "I have 5+ in the next-higher suit; you bid it" = `with opener_as_declarer(2H): ...`. Inversion of control — responder dictates the next call, opener executes, declarer/dummy roles flip. |
| **Jacoby 2NT** (1M → 2NT) | Forced introspection: `partner.describe_shape()`. The rebid schema (4♣/4♦/4♥/4♠/3NT all mean specific things) is the `__repr__()` contract. |
| **Blackwood** (4NT) | RPC with a serialized response schema. 5♣/5♦/5♥/5♠ = 0/1/2/3 aces. Caller (4NT bidder) is responsible for parsing the wire format. |
| **RKCB 1430 / 0314** | Same RPC, refined return type — adds the trump king to the "key card" set. The 1430 vs 0314 split is API versioning: same function, different schemas, partnership must agree on which dialect. |
| **Gerber** (4♣ over NT) | Same operation as Blackwood, different namespace. In NT auctions, `4♣` is the bound name; in suit auctions, `4NT` is. Like a Python protocol method that's named differently in `__iter__` vs `__aiter__`. |
| **Splinter** (1♠ → 4♣) | Packed multi-field literal. One bid simultaneously declares `{trump: spades, raise_to: 4, shortness: clubs, force: game}`. The bridge equivalent of a struct literal with named fields. |
| **Cue bidding** (4♣/4♦ in a slam auction) | Assertion chain. Each cue is `assert first_round_control(suit)`; chained, they bottom-up-prove enough controls for slam. The first failed assertion (skip a suit) signals "control missing here." |
| **Lebensohl** (2NT over interference) | Strict-mode switch. 2NT relay re-routes the auction into "weak / negative" branch; direct bids become "strong / positive." Same later bids, different semantics depending on which path you came through. Middleware. |
| **Michaels Cuebid** (opp 1♥ → 2♥) | Destructured tuple return. One call communicates "(5+ spades, 5+ in a minor)" — two type bounds in one operation. |
| **Unusual 2NT** (opp 1♠ → 2NT) | Operator overloading. 2NT normally = "16-18 balanced invitational"; in this context = "both lower unbid suits." Same syntax, dispatched on auction state. |
| **Negative double** (1♥-1♠-X) | Catch block. "I was going to respond to partner's 1♥ but the 1♠ overcall threw an exception — catching and re-routing to 'I have the other major.'" |
| **Takeout double** (opp 1♥ → X) | Polymorphic dispatch. The double's meaning resolves only when partner picks a suit — bridge's dynamic dispatch, dispatched on `partner.preferred_suit()`. |
| **Drury** (passed-hand 1♠ → 2♣) | Constrained getter. The passed-hand precondition narrows the type bounds at the gate: `assert hcp < 12` means the 2♣ response can't possibly mean what unpassed 2♣ would. |
| **Bergen raises** (1♥ → 3♣/3♦) | Enum with discrete cases replacing a wide single value. Where SAYC uses one "3♥" call that means "anything from 6-9 with 4+ trumps," Bergen factors it into `Bergen3C = 7-10` and `Bergen3D = 11-12`. Algebraic data type. |
| **Reverse** (1♣-1♠-2♥) | Type narrowing on rebid. The rebid pattern itself (higher-ranking suit at the 2-level after a 1-level response) is a structural assertion `assert opener.hcp >= 17`. Like a TypeScript type guard whose name doesn't appear in the AST. |
| **Fourth-suit forcing** (1♠-2♣-2♦-2♥) | Continuation token / null probe. "I haven't decided yet — keep emitting; give me more bits before I commit." The 4th-suit bid doesn't promise that suit at all, just defers commitment. |
| **Smolen** (1NT-2♣-2♦-3♥) | Clever encoded transform. The jump that *looks* like length (3♥) actually shows the *other* major's length — bit-twiddling in the auction, like packing two booleans into one byte where 1 means "this didn't happen." |
| **Texas transfer** (1NT → 4♦/4♥) | Same operation as Jacoby, overloaded at game level. Method overload by argument level — `transfer(2)` lands in invitational space, `transfer(4)` lands in game. |
| **Multi 2♦** (2♦ opening) | Discriminated union. The opening "is one of: weak 2♥, weak 2♠, strong balanced, or 20-22 balanced," and the rebid is the discriminator that narrows the type. Sum type that hasn't been pattern-matched yet. |
| **Weak two-bid** (2♥/2♠ opening) | Typed constructor with narrow bounds. `WeakTwo(suit, length=6, hcp=6..10)` — a literal of a specific narrow type, immediately committing to most of its parameters. |
| **DOPI / ROPI** (signaling over interference to Blackwood) | Error-recovery protocol. When opponents intervene over your RPC, DOPI / ROPI re-encodes the same data through a different channel. Fallback transport. |
| **Truscott 2NT** (1♠-X-2NT) | Substitution principle. Your normal "limit raise" call (3♠) now means something different because the opponents stole that bidding space, so the 2NT call substitutes in for it. |

### Extended observations

* **Alerting** = compile-time warning emission. When a bid is
  artificial (Stayman doesn't promise clubs), the bidder owes
  the opponents a heads-up. The bidding box lights up with a
  yellow "Alert" the way a TypeScript compiler emits
  `// @ts-expect-error: this looks weak but is forcing`.
  Failing to alert is the bridge equivalent of suppressing a
  warning to hide a footgun.

* **System notes** = the project README + API docs. Mid-
  tournament, opponents can request a copy. Disagreements
  between partners about what a bid means show up *exactly*
  like undefined behaviour: the auction "compiles" (no
  insufficient bid), but the contract reached is a runtime
  crash.

* **The convention card** = `package.json` + `tsconfig.json`
  in one document. The TD (director, the language runtime
  arbiter) is allowed to read it during a dispute, and
  partners are bound by what it says, not by what they wish
  they'd written.

* **"System on" / "system off"** over interference = middleware
  enable / disable. Some partnerships play "system on over
  double" (your conventional 1NT structure still applies if
  opp doubles), others play "system off" (revert to natural).
  Pure runtime config — the same auction code with the
  middleware stack swapped.

* **Pre-empts** are an adversarial DoS on the auction. A 3♥
  opening on a junk weak two-suiter consumes the opponents'
  bidding bandwidth before they can find their slam. Fuzzing
  the auction tree to crash opponents' search heuristics.

---

## Part 3: Defensive Carding → IPC

The auction was *language*. The bidding system was *the
compiler*. Conventions were *libraries*. Defensive carding is
the **runtime IPC layer between two cooperating processes
(the defenders) that share no memory, communicate only through
a public channel, and accept that the adversary (declarer)
reads every packet.**

### The IPC threat model

Two facts shape every signaling decision:

1. The defenders are two processes with no shared address
   space. They cannot speak, cannot gesture, cannot allocate a
   private channel. The only output device each holds is the
   card they're about to play.
2. The channel is public. Declarer reads every card too.
   There's no encryption — only the hope that the signal helps
   partner more than it helps declarer.

That's exactly the design constraint of a federated-learning
protocol whose aggregator is malicious, or a covert-channel
protocol with an eavesdropper on the wire. Every defensive
signaling agreement is a protocol RFC that has accepted this
threat model up front.

### Signaling methods → programming analogs

| Signal | Programming analog |
|---|---|
| **Standard attitude** (high = like, low = dislike) | `bool returncode` from a function. 1-bit channel — "encourage / discourage." The simplest possible RPC reply. |
| **UDCA — Upside-Down Count & Attitude** (low = like, high = dislike) | Active-low signalling / inverted polarity. Same payload, NOT-gated on the wire. The motivation is the same as active-low in digital logic: you're often forced to play a high card for tactical reasons, so making "high" the *negative* signal makes the line less noisy. Endianness debate; partnerships argue about it the way C devs argue about little-endian. |
| **Standard count** (high-low = even, low-high = odd) | 1-bit parity over two cards. A 2-packet sequence whose ordering encodes a single bit of length information. Like a checksum byte that's actually a parity bit. |
| **Lavinthal / McKenney / suit preference** (high = high suit, low = low suit) | Categorical enum returned as a magnitude. "Which other suit do you want?" answered by a 2-way discriminator that ranks the two remaining suits. Tagged-union discriminator field. |
| **Smith echo** (high-low on declarer's first suit = "I liked partner's opening lead") | Writing to a separate file descriptor to convey out-of-band metadata about a *prior* operation. The signal is sent on a future packet about a past event — eventually-consistent ACK. |
| **Trump echo** (high-low in trumps = "I have a ruff coming") | Interrupt request. Defender is signaling "schedule a context switch — I need to come on lead." Equivalent to raising `SIGUSR1` at partner. |
| **Coded 9s and 10s** (a 9 or 10 lead implies 0-2 higher cards) | Reserved bit-pattern. Specific magnitudes in the lead are repurposed as protocol-header bytes — the rank is overloaded with metadata the way `0x7F` is reserved in some binary formats. |
| **Odd-even / Roman discards** (odd = encourage, even = suit preference) | Tagged union with a 1-bit discriminator. The parity of the discarded rank says which interpretation to apply to the magnitude. Pack two protocols into one byte. |
| **Revolving discards** (low card asks for next-higher suit, high card asks for next-lower) | Round-robin scheduling. Suit preference encoded by stepping through a fixed cyclic order. |
| **Lavinthal discards** (discard high in suit X = "give me the high other suit") | Side-channel embedded in the *exception* path. You're discarding because you couldn't follow — the act of discarding is a signal, but the *choice* of which suit / rank you discard is a *second* signal layered on top. Steganographic doubling. |
| **4th-best opening lead** (the 4th-highest card in your longest suit) | Length-prefixed packet header. The lead carries a synthesised field — partner uses the rank to decode "leader has 3 cards higher than this in the suit." |
| **3rd-and-5th leads** (3rd from even-length, 5th from odd-length) | Variable-length encoding. The single-card payload tells partner the parity of the suit length at the cost of a more complex decoder. |
| **Rusinow leads** (2nd-highest from a sequence: K from AK, Q from KQ) | NACK / negative-presence acknowledgment. "K asks for unblock" because *leading K denies A.* A signal whose information is what it's *not*. |
| **MUD** — Middle-Up-Down from 3 small (8 from 8-5-3) | 3-packet handshake. The middle card sets up the sequence; the up-then-down ordering tells partner "I have exactly 3 small, no honour." |
| **11-rule** (count higher cards in suit using 11 − lead) | Client-side derivation from a small packet. Both defenders can compute declarer's holdings: `11 − partner's_4th_best − (your_visible_higher_cards)` = declarer's higher cards. A 4-byte lead carries an entire derived view of the database. |
| **Singleton lead** (declarer-cued aggressive partial lead) | One-shot send. After the lead, that channel is closed — you can't follow suit again. Honest, unrepeatable transmission. |
| **Suit-preference on the opening lead under a void** | Mode bits set at session start. The first card lays down the high-level dispatch table for the rest of defense. |

### Extended observations

* **Signals are encoded WITHIN the constraint of playing a
  legal card**, the way PHP can only encode information using
  valid HTML. You don't get to invent a new card — your
  protocol must steganographically piggyback on a play the
  rules already require. Most defensive cards do dual duty:
  legal play *plus* signal. Bridge's whole carding stack is
  *covert channel inside required output*.

* **Discards are the burst-bandwidth window**. The moment
  you're void in a suit, *any* card is legal, so the
  constraint on your signaling vanishes for one card. Discards
  are the equivalent of getting a brief unrestricted-syscall
  window in a heavily sandboxed program — defenders save their
  most ambiguous decisions for that moment.

* **Forced plays are a lossy channel**. Sometimes the rules
  force you to play a specific card and you can't signal what
  you'd like. You wanted to say "encouraging from KJ4" but you
  had to play the K, and now partner can't tell whether it's
  an honour-sequence top or a stuck singleton. Bridge's
  `errno` is silently `EAGAIN`.

* **The "standard vs UDCA" debate is unironically little-
  endian vs big-endian**. Both protocols carry the same
  information. UDCA gains a small advantage in cases where
  you'd be forced to play a high card anyway; standard gains a
  small advantage when leading a "look like an honour" card
  matters. Partnerships pick one and argue with strangers at
  the bar about it.

* **Smith echo's relationship to attitude is a callback**.
  Attitude says "I liked the lead" but you don't always get to
  say it on trick 1 (you might be forced to ruff, or to play a
  stiff). Smith echo lets you say it *later*, when declarer's
  first side suit is being run. That's a deferred ACK
  piggybacked on a later, unrelated message. Bridge's TCP
  retransmission.

* **The 11-rule is the SQL view defined by the lead**. Partner
  emits one row (the 4th-best card). Both other defenders
  evaluate the same SELECT against it and arrive at the same
  projection of declarer's hand. The lead is a tuple, the
  rule is the schema, the computation is purely client-side.

* **The reason there are so many *different* signaling
  agreements** (standard, UDCA, odd-even, Lavinthal, Smith,
  Revolving, etc.) is the same reason there are so many
  serialization formats (JSON, MessagePack, Protocol Buffers,
  Avro, Thrift). Each optimises for a different access
  pattern. Strong partnerships layer multiple methods — "UDCA
  count except in trump where it's a suit-preference if
  standout-low or standout-high" — and the resulting
  partnership-specific protocol stack reads exactly like a
  real production system: documented in a system note,
  debated annually, slightly incompatible with everyone
  else's.

* **False signals are an adversarial DoS on the defense**. A
  defender playing a misleading card is sending an
  adversarially crafted packet to partner, with declarer in
  scope as a beneficiary. Bridge's textbook chapter on "when
  to falsecard" is in fact the bridge textbook chapter on
  *adversarial inputs and which of your processes is robust
  to them*.

* **The reason great defense is rare**: the IPC protocol
  bandwidth is severely limited. Across a 13-trick deal each
  defender plays 13 cards, each carrying maybe 1-3 bits of
  meta on top of the legal play. ~40 bits per defender,
  total. The auction is comparatively rich (a 12-bid sequence
  with 38 distinct first-call slots is ~60+ bits). Most bridge
  education focuses on the auction because it's the
  higher-bandwidth channel; truly great defense is the
  discipline of squeezing every bit out of the impoverished
  one.

---

## Tying it all together

If a bidding system is a language, a convention is a library,
and a defensive signaling agreement is an IPC protocol — then
bridge's three communication layers are nothing more than what
any non-trivial program is:

> A language compiled with a particular set of libraries,
> communicating with cooperating peers over a constrained
> channel, in the presence of an adversary who reads the same
> wire.

The deal is the input data. The auction is the compilation
phase, source (calls) → binary (contract). Declarer play and
defense are the runtime: declarer runs the binary, defenders
run their own program against shared state through the
constrained signaling channel. A "wrong contract" is a
compile-time bug; a botched declarer-play line is a runtime
exception; a missed defensive signal is a dropped packet.

Bridge education usually teaches the layers in the order
auction → declarer play → defense, which mirrors the order
compiler → runtime → networking in a CS curriculum. Each
later layer is harder because it has less bandwidth, less
local context, and more adversarial constraints. The same is
true on both sides of the analogy.
"""


class ProgrammingHelpDialog(QDialog):
    """Modal dialog rendering the bridge-as-programming-language
    Rosetta Stone. Resizable; uses QTextEdit.setMarkdown() so the
    tables render with proper grid lines."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bridge for Programmers — Rosetta Stone")
        self.resize(1100, 800)
        layout = QVBoxLayout(self)
        view = QTextEdit()
        view.setReadOnly(True)
        # Use a monospace-ish font for the tables; the prose still
        # reads fine and the columns line up.
        view.setFont(QFont("DejaVu Sans", 10))
        view.setMarkdown(ROSETTA_HELP_MARKDOWN)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        # Close-only — single button, accept maps to close as well.
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(
            self.accept)
        layout.addWidget(buttons)
