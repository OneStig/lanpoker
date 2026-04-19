# NLHE Poker Webapp — Build Spec

## Project Context
A Flask-based No-Limit Hold'em poker webapp for LAN play. The host runs the Flask server on their machine (bound to `0.0.0.0:<port>`), shares their hotspot, and players connect via browser. One table, up to 10 players. `uv init` is already done — use `uv add` for dependencies.

## Architecture

**Backend:** Flask + Flask-SocketIO (WebSockets for all game state sync). Use `eventlet` or `gevent` as the async mode.

**Frontend:** React + a minimal component library (shadcn/ui or similar lightweight option). Prioritize maintainability and mobile-friendliness. Keep the bundle small — no heavy animation libs, no state management libs beyond React's built-ins unless strictly necessary.

**Transport:** WebSockets for game state, bets, cards, turn changes. HTTP only for initial page load and join requests.

**Logging:** Suppress Flask/Werkzeug request logs (GET/POST noise). Host should see only meaningful events (player joined, hand started, action taken, hand ended, etc.) in a clean format.

## Player Experience

**Join flow:**
1. Land on page → enter username + requested stack size → submit
2. Wait screen while host approves
3. On approval, enter spectate mode at the table (or seated if host assigns immediately — see host commands)

**Spectator view:**
- See full table state: all seated players, their stacks, current bets, pot, community cards, whose turn it is
- Button to "request a seat" (sends to host approval queue)

**Seated player view:**
- Everything the spectator sees, plus their own hole cards
- Action UI appears only when it's their turn: check / call / bet / raise / fold (only valid actions shown)
- Bet/raise uses a slider + input, enforcing min-raise and all-in rules per NLHE
- Mobile-first layout: table view must be readable and actionable on a phone in portrait

**Bust behavior:** When a player's stack hits 0, they return to spectate mode automatically. They can request a seat again (host approves, which functions as a rebuy).

**No time limits, no chat.**

## Game Rules Enforcement

The server is authoritative for all game logic. Enforce standard NLHE:
- Dealer button rotates clockwise each hand
- Small blind / big blind posted automatically
- Preflop action starts left of BB; postflop starts left of dealer
- Min-raise = size of previous raise (or BB preflop)
- All-in handling with proper side pots when players are all-in for different amounts
- Showdown: evaluate 5-card best hand from 7 (hole + board), award pot(s) correctly, split on ties
- Use a well-tested hand evaluator library (e.g., `treys` or `pokerkit`) — don't hand-roll hand ranking

Players should never need to know the rules — the UI only offers legal actions.

## Host Experience

The host interacts via the terminal where `flask run` (or equivalent) is executing. Implement a REPL that runs in a separate thread alongside the Flask app.

**Commands to implement:**

| Command | Behavior |
|---|---|
| `approve <username>` | Approve a pending join/seat request |
| `deny <username>` | Reject a pending request |
| `pending` | List pending requests |
| `players` | List seated players + stacks + spectators |
| `seat <username> <seat_num>` | Assign/move a player to a seat |
| `shuffle` | Randomize seat assignments (only when no hand in progress) |
| `stack <username> <amount>` | Adjust a player's stack |
| `blinds <small> <big>` | Set blind levels |
| `kick <username>` | Remove a player from the table (their chips are forfeit or returned to pool — your call, pick one and document) |
| `start` | Start the next hand (requires ≥2 seated players, blinds set) |
| `end` | End the session; broadcast final chip counts to all clients and display them in the terminal |
| `help` | List all commands |

Commands that don't make sense mid-hand (shuffle, seat changes) should be rejected with a clear message.

## Code Quality Requirements

- **Minimal and clean.** No speculative features. If a requirement is ambiguous, stop and ask rather than guess.
- **Structured project layout.** Separate concerns: game engine (pure logic, testable without Flask), server (Flask + SocketIO handlers), host REPL, frontend.
- **The poker engine must be unit-testable in isolation** — pure functions / classes with no Flask imports. Write a few sanity tests for hand evaluation, side pots, and betting round progression.
- **CSS/styling:** use the component library's defaults. No sprawling custom CSS. If custom styles are needed, keep them co-located and minimal.
- **Performance:** LAN bandwidth is limited. Send diffs or minimal state payloads over the socket where reasonable; don't re-broadcast the entire game state on every trivial event if a smaller message suffices. That said, don't prematurely optimize — correctness first.
- **Logging:** suppress Werkzeug access logs. Host terminal output should be readable — timestamps, player names, actions. No raw JSON dumps.

## Deliverables

1. Working Flask + SocketIO backend with authoritative game logic
2. React frontend (player + spectator views, mobile-friendly)
3. Host terminal REPL with all commands above
4. `README.md` with: install steps (`uv add` commands), how to run, how to connect from another device on the LAN, list of host commands
5. Minimal test suite for the poker engine

## Ask-Before-Assuming Checklist

Before writing code, confirm anything ambiguous. Specifically flag:
- The `kick` behavior (forfeit chips vs. return)
- Whether seat assignment on approval is automatic or requires a separate `seat` command
- Any library choices that pull in significant dependencies