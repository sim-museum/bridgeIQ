# bridgeIQ

A bridge program for Linux with a classic desktop interface. Bidding is rule-based and system-driven (seven
systems, including SAYC, 2/1, Precision and Acol); card play uses a no-peek engine (double-dummy analysis of
sampled hidden layouts, never the real cards) on top of the libdds double-dummy solver. No neural networks.

## Features

- **Full Bridge Play**: Rule-based bidding + DDS/MC card play opponents
- **Classic Interface**: Familiar layout with table view, bidding box, and menus
- **Multiple Game Modes**:
  - 4-player (human South, computer N/E/W)
  - 1-player (single hand visible)
  - All-computer (auto-play)
- **Scoring**: IMP, Matchpoints, and Rubber Bridge scoring
- **Analysis**: Double-dummy analysis, bid simulation, hints
- **Deal Management**: Random deals, deal filters, PBN file support

## Requirements

- Ubuntu 24.04 or later (or a compatible Linux)
- Python 3.10 or later
- libdds0, the double-dummy solver library: `sudo apt install libdds0`

## Install and run

```bash
git clone <this repository> bridgeIQ
cd bridgeIQ
./run.sh
```

The first run creates a Python virtual environment (`venv/`) and installs PyQt6 and numpy into it
(`requirements.txt`); later runs start straight away. Settings, logs and saved matches are kept under
`bridgeIQ/CONFIG`, `bridgeIQ/DATA` and `~/.local/share/bridgeIQ`.

## Text mode (no window)

```bash
./run.sh --textmode [--seed N]
```

Plays in the terminal (also chosen automatically when there is no display):
you are South, biq plays the other three seats with the same bidder and
card engine as the window. Type calls (`1c`, `1nt`, `p`, `x`) and cards
(`sa`, `10h`); `?` asks biq, `m` explains the auction, `auto` lets biq
finish the board, `h` is help, `q` quits. Each board shows the full deal,
score, double-dummy result and par, and is logged like a window board.

## Usage Guide

### Starting a New Game

1. Launch the application
2. Press `Ctrl+N` or select `File > New Deal`
3. A random deal is generated with you as South
4. bridgeIQ plays North, East, and West

### Bidding

| Action | Mouse | Keyboard |
|--------|-------|----------|
| Pass | Click "Pass" | `p` |
| Double | Click "X" | `x` |
| Redouble | Click "XX" | `xx` |
| Bid 1♣ | Click "1♣" | `1c` |
| Bid 3NT | Click "3NT" | `3n` |

- Check "Alert" checkbox before bidding to mark alertable calls
- Enter explanation in the text field

### Card Play

- Click a card in your hand to play it
- Follow suit if possible (legal cards are highlighted)
- Current trick shows in the center
- Trick count updates automatically

### Analysis Features

| Feature | Access |
|---------|--------|
| Double Dummy | `View > Double Dummy Analysis` |
| Bid Simulation | `View > Bid Simulation` |
| Hint | Click "Hint" in toolbar |
| Show All Hands | `F2` or `View > Show All Hands` |

### Menu Reference

- **File**: New deal, Open/Save files, Export HTML, Exit
- **Deal**: Match control, Repeat deal, Random seed, Deal filters
- **Configuration**: Players, Bidding systems, Preferences
- **View**: Show hands, Review deal, Scores, DD analysis, Simulation
- **Extras**: MiniBridge mode, One-player mode
- **Help**: About, Help contents

## Configuration

### Player Types

In `Configuration > Players`:
- **Human**: You control this seat
- **Computer**: bridgeIQ plays this seat
- **External**: a networked seat (LAN play, or a Q-NET seat)

### Game Modes

- **4-Player**: Human South, bridgeIQ plays N/E/W (default)
- **1-Player**: Only your hand visible, realistic play
- **All-Computer**: bridgeIQ plays all seats, for analysis

### Scoring Methods

In `Deal > Match Control`:
- **IMP**: Team match scoring
- **Matchpoints**: Pairs tournament scoring
- **Rubber**: Traditional rubber bridge

## Troubleshooting

### "Engine: Failed" in status bar

DDS library failed to load. Verify libdds0 is installed:
```bash
ldconfig -p | grep libdds
sudo apt install libdds0
```

### No window appears

Check if display is available:
```bash
echo $DISPLAY
```

### Segfault on exit

libdds spawns worker threads that outlive Python's interpreter shutdown;
the harmless segfault only occurs at process exit, never mid-game.

## Known Limitations

- **Linux only**: tested on Ubuntu 24.04
- **Not implemented**:
  - Full bidding system editor
  - Competition/network mode
  - PBN import/export
  - HTML export

## Credits

- **libdds**: Double-dummy solver by dds-bridge
- **UI Design**: Classic desktop bridge style
- **Framework**: PyQt6
- **Card faces**: public-domain vector deck by Byron Knoll
