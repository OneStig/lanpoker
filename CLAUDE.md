# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the server
uv run python -m server.run                        # defaults to localhost:3000
uv run python -m server.run --host 0.0.0.0 --port 3000  # LAN-accessible

# Run tests
uv run pytest server/test_engine.py

# Run a single test
uv run pytest server/test_engine.py::test_name
```

## Architecture

**lanpoker** is a LAN poker server: the host runs a Flask server, shares their hotspot, and players connect via browser. It's single-table No-Limit Hold'em for up to 10 players.

### Layer separation

| Module | Role |
|---|---|
| `server/engine.py` | Pure NLHE logic — no Flask imports, no side effects. Input: state + action → output: new state. Fully unit-testable. |
| `server/table.py` | Session state: seats, spectators, pending queue, hand lifecycle. Guards all mutations with `threading.RLock()`. |
| `server/app.py` | Flask + SocketIO handlers. Thin transport layer: validates input, calls `table`, emits back. |
| `server/repl.py` | Host REPL running on a daemon thread. Commands: `approve`, `deny`, `seat`, `start`, `end`, `blinds`, etc. |
| `server/run.py` | Entry point. Detects LAN IP, sets up threads, starts eventlet. |
| `static/index.html` | Single-page vanilla JS + Socket.IO client. No build step. |

### Threading model
- SocketIO handlers run in **eventlet greenlets**
- REPL runs on a **native daemon thread**
- Both paths mutate `Table` state — always acquire `RLock` before touching shared state

### Game flow
1. Player submits username/stack → queued in `table.pending`
2. Host approves via REPL → player auto-assigned to first free seat, or to spectators if full
3. Host runs `start` → engine deals, broadcasts public state, sends private hole cards per-player via targeted emit
4. Player action → `app.py` → `table.player_action()` → engine validates + applies → broadcast new state
5. Showdown or fold → engine resolves pots, awards chips, busted players (stack=0) move to spectators

### State broadcast pattern
- **Public state** (board, pot, active player, stack sizes): broadcast to all via `socketio.emit("table", ...)`
- **Private state** (hole cards): emitted only to that player's `sid`
- Frontend reconstructs full UI from each broadcast; no incremental diffing

### Logging
Werkzeug and SocketIO request logs are suppressed (`logging_setup.py`). Host sees only meaningful game events formatted as `[HH:MM:SS] message`.

## Decided ambiguities (from spec)

- **`kick` behavior**: chips are forfeit (not returned). Documented in REPL `help` output.
- **Seat assignment on approval**: `approve <username>` auto-assigns to first free seat. Host can then use `seat <username> <num>` to move them.

## Known gaps vs spec

The spec calls for **React + component library (shadcn/ui)** with a **mobile-first layout**. The current frontend is vanilla JS with minimal styling. Remaining work:

1. Replace `static/index.html` with a React app (Vite recommended for small bundle)
2. Mobile-first CSS — table must be usable on a phone in portrait orientation
3. Bet/raise **slider + input** (spec requires slider; current UI has input only)
4. Complete `README.md` (install steps, run instructions, LAN connection guide, host command reference)
