"""
Table session: manages seats, spectators, pending requests, and hand lifecycle.

Thread-safety: Flask-SocketIO handlers may be called from multiple worker
greenlets; the REPL runs on a separate native thread. All mutation goes
through methods guarded by a single RLock. Keep critical sections short.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .engine import (
    ActionType,
    HandState,
    Street,
    apply_action,
    legal_actions,
    public_snapshot,
    start_hand,
)

MAX_SEATS = 10


def _format_action(action: str, amount: int) -> str:
    if action == "fold":
        return "folds"
    if action == "check":
        return "checks"
    if action == "call":
        return f"calls {amount}"
    if action == "bet":
        return f"bets {amount}"
    if action == "raise":
        return f"raises to {amount}"
    return action


@dataclass
class Connection:
    """A connected browser. `username` may be None until they submit join form."""

    sid: str
    username: Optional[str] = None


@dataclass
class Seat:
    username: str
    stack: int


@dataclass
class PendingRequest:
    username: str
    requested_stack: int
    kind: str  # "join" or "seat"
    sid: str


class Table:
    """
    Central authority over the session state.

    Broadcast strategy:
        - `on_broadcast(payload)` is called for full public state pushes.
        - `on_private(username, payload)` is called for hole-card pushes.
        - `on_log(msg)` logs a host-facing event.
    """

    def __init__(
        self,
        on_broadcast: Callable[[dict], None],
        on_private: Callable[[str, dict], None],
        on_log: Callable[[str], None],
    ) -> None:
        self.lock = threading.RLock()
        self.on_broadcast = on_broadcast
        self.on_private = on_private
        self.log = on_log

        self.seats: dict[int, Seat] = {}              # seat_num -> Seat
        self.spectators: set[str] = set()             # usernames
        self.pending: list[PendingRequest] = []       # approval queue
        self.connections: dict[str, Connection] = {}  # sid -> Connection
        self.username_to_sid: dict[str, str] = {}

        self.small_blind = 5
        self.big_blind = 10
        self.button_seat: Optional[int] = None
        self.hand: Optional[HandState] = None
        self.session_ended = False

    @property
    def _hand_in_progress(self) -> bool:
        return self.hand is not None and self.hand.street != Street.COMPLETE

    def connect(self, sid: str) -> None:
        with self.lock:
            self.connections[sid] = Connection(sid=sid)

    def disconnect(self, sid: str) -> None:
        with self.lock:
            conn = self.connections.pop(sid, None)
            if conn and conn.username:
                self.username_to_sid.pop(conn.username, None)
            # Note: we keep their seat/stack; they can reconnect under the same name.

    def attach_username(self, sid: str, username: str) -> bool:
        """Called on reconnect. Returns True if username is recognized."""
        with self.lock:
            if username not in self.username_to_sid and not self._known_username(username):
                return False
            conn = self.connections.get(sid)
            if not conn:
                return False
            conn.username = username
            self.username_to_sid[username] = sid
            return True

    def _known_username(self, username: str) -> bool:
        return (
            any(s.username == username for s in self.seats.values())
            or username in self.spectators
            or any(r.username == username for r in self.pending)
        )

    def request_join(self, sid: str, username: str, requested_stack: int) -> tuple[bool, str]:
        """Initial join from landing page. Returns (ok, message)."""
        with self.lock:
            if self.session_ended:
                return False, "session has ended"
            if not username or not username.strip():
                return False, "username required"
            username = username.strip()
            if len(username) > 24:
                return False, "username too long"
            if requested_stack <= 0:
                return False, "stack must be positive"
            if self._known_username(username):
                return False, "username taken"

            conn = self.connections.get(sid)
            if not conn:
                return False, "no connection"
            conn.username = username
            self.username_to_sid[username] = sid

            self.pending.append(
                PendingRequest(username=username, requested_stack=requested_stack, kind="join", sid=sid)
            )
            self.log(f"JOIN REQUEST: {username} (stack {requested_stack})")
            return True, "waiting for host approval"

    def request_seat(self, username: str) -> tuple[bool, str]:
        """Spectator asks to be seated (or busted player asks for rebuy)."""
        with self.lock:
            if self.session_ended:
                return False, "session has ended"
            if username not in self.spectators:
                return False, "not a spectator"
            if any(r.username == username for r in self.pending):
                return False, "already pending"
            self.pending.append(
                PendingRequest(username=username, requested_stack=0, kind="seat", sid=self.username_to_sid.get(username, ""))
            )
            self.log(f"SEAT REQUEST: {username}")
            return True, "waiting for host approval"

    def host_approve(self, username: str) -> tuple[bool, str]:
        with self.lock:
            req = next((r for r in self.pending if r.username == username), None)
            if not req:
                return False, f"no pending request for {username}"
            self.pending.remove(req)

            seat_num = self._first_free_seat()
            if seat_num is None:
                self.spectators.add(username)
                self.log(f"APPROVED: {username} -> spectator (table full)")
            elif req.kind == "join":
                self.seats[seat_num] = Seat(username=username, stack=req.requested_stack)
                self.log(f"APPROVED: {username} -> seat {seat_num} with {req.requested_stack}")
            else:
                # Rebuy: default to 100 BBs; host can adjust via `stack`.
                default_stack = self.big_blind * 100
                self.seats[seat_num] = Seat(username=username, stack=default_stack)
                self.spectators.discard(username)
                self.log(f"APPROVED: {username} -> seat {seat_num} (rebuy {default_stack})")
            return True, "ok"

    def host_deny(self, username: str) -> tuple[bool, str]:
        with self.lock:
            req = next((r for r in self.pending if r.username == username), None)
            if not req:
                return False, f"no pending request for {username}"
            self.pending.remove(req)
            self.log(f"DENIED: {username}")
            return True, "ok"

    def host_pending(self) -> list[dict]:
        with self.lock:
            return [{"username": r.username, "kind": r.kind, "requested_stack": r.requested_stack} for r in self.pending]

    def host_players(self) -> dict:
        with self.lock:
            return {
                "seated": [
                    {"seat": s, "username": seat.username, "stack": seat.stack}
                    for s, seat in sorted(self.seats.items())
                ],
                "spectators": sorted(self.spectators),
            }

    def host_seat(self, username: str, seat_num: int) -> tuple[bool, str]:
        with self.lock:
            if self._hand_in_progress:
                return False, "cannot change seats during a hand"
            if not 1 <= seat_num <= MAX_SEATS:
                return False, f"seat must be 1..{MAX_SEATS}"
            current_seat = next(
                (s for s, seat in self.seats.items() if seat.username == username),
                None,
            )
            if seat_num in self.seats and self.seats[seat_num].username != username:
                return False, f"seat {seat_num} is occupied"
            if current_seat is not None:
                self.seats[seat_num] = self.seats.pop(current_seat)
            elif username in self.spectators:
                self.seats[seat_num] = Seat(username=username, stack=self.big_blind * 100)
                self.spectators.discard(username)
            else:
                return False, f"unknown player {username}"
            self.log(f"SEAT: {username} -> {seat_num}")
            return True, "ok"

    def host_shuffle(self) -> tuple[bool, str]:
        with self.lock:
            if self._hand_in_progress:
                return False, "cannot shuffle during a hand"
            names = [s.username for s in self.seats.values()]
            stacks = {s.username: s.stack for s in self.seats.values()}
            random.shuffle(names)
            seat_nums = sorted(self.seats.keys())
            self.seats = {
                num: Seat(username=name, stack=stacks[name])
                for num, name in zip(seat_nums, names)
            }
            self.log(f"SHUFFLE: {', '.join(f'{n}->{s.username}' for n, s in self.seats.items())}")
            return True, "ok"

    def host_stack(self, username: str, amount: int) -> tuple[bool, str]:
        with self.lock:
            if self._hand_in_progress:
                return False, "cannot adjust stacks during a hand"
            if amount < 0:
                return False, "stack cannot be negative"
            for seat in self.seats.values():
                if seat.username == username:
                    seat.stack = amount
                    self.log(f"STACK: {username} -> {amount}")
                    return True, "ok"
            return False, f"{username} is not seated"

    def host_blinds(self, sb: int, bb: int) -> tuple[bool, str]:
        with self.lock:
            if self._hand_in_progress:
                return False, "cannot change blinds during a hand"
            if sb <= 0 or bb <= 0 or bb < sb:
                return False, "invalid blinds"
            self.small_blind = sb
            self.big_blind = bb
            self.log(f"BLINDS: {sb}/{bb}")
            return True, "ok"

    def host_kick(self, username: str) -> tuple[bool, str]:
        """Remove a player from the table; their chips are forfeit."""
        with self.lock:
            if self._hand_in_progress:
                return False, "cannot kick during a hand"
            seat_num = next(
                (s for s, seat in self.seats.items() if seat.username == username),
                None,
            )
            if seat_num is not None:
                self.seats.pop(seat_num)
            self.spectators.discard(username)
            self.pending = [r for r in self.pending if r.username != username]
            self.log(f"KICK: {username} (chips forfeit)")
            return True, "ok"

    def host_start(self) -> tuple[bool, str]:
        with self.lock:
            if self._hand_in_progress:
                return False, "hand already in progress"
            playable = [(s, seat) for s, seat in self.seats.items() if seat.stack > 0]
            if len(playable) < 2:
                return False, "need at least 2 seated players with chips"

            playable_seats = sorted(s for s, _ in playable)
            if self.button_seat is None:
                self.button_seat = playable_seats[0]
            else:
                self.button_seat = next(
                    (s for s in playable_seats if s > self.button_seat),
                    playable_seats[0],
                )

            seated_for_hand = [(s, seat.username, seat.stack) for s, seat in playable]
            self.hand = start_hand(
                seated=seated_for_hand,
                button_seat=self.button_seat,
                small_blind=self.small_blind,
                big_blind=self.big_blind,
            )
            names = ", ".join(n for _, n, _ in seated_for_hand)
            self.log(f"HAND START: button=seat{self.button_seat}, players=[{names}]")
            self._broadcast_state(new_hand=True)
            return True, "ok"

    def host_end(self) -> dict:
        with self.lock:
            self.session_ended = True
            final = self.host_players()
            self.log("SESSION ENDED. Final stacks:")
            for s in final["seated"]:
                self.log(f"  seat {s['seat']}: {s['username']} = {s['stack']}")
            for u in final["spectators"]:
                self.log(f"  spectator: {u}")
            self.on_broadcast({"type": "session_ended", "final": final})
            return final

    def player_action(self, username: str, action: str, amount: int = 0) -> tuple[bool, str]:
        with self.lock:
            if not self.hand or self.hand.street == Street.COMPLETE:
                return False, "no active hand"
            try:
                atype = ActionType(action)
            except ValueError:
                return False, f"unknown action {action}"
            try:
                apply_action(self.hand, username, atype, amount)
            except (ValueError, KeyError) as e:
                return False, str(e)

            self.log(f"ACTION: {username} {_format_action(action, amount)}")

            if self.hand.street == Street.COMPLETE:
                self._settle_hand()
            self._broadcast_state()
            return True, "ok"

    def _settle_hand(self) -> None:
        """Copy final stacks back into seats; busted players become spectators."""
        for p in self.hand.players:
            seat_num = next(
                (s for s, seat in self.seats.items() if seat.username == p.username),
                None,
            )
            if seat_num is None:
                continue
            self.seats[seat_num].stack = p.stack
            if p.stack == 0:
                self.seats.pop(seat_num)
                self.spectators.add(p.username)
                self.log(f"BUST: {p.username} -> spectator")
        for w in self.hand.winners:
            self.log(f"WINNER: {', '.join(w['usernames'])} wins {w['amount']} (pot {w['pot_idx']})")

    def _first_free_seat(self) -> Optional[int]:
        for i in range(1, MAX_SEATS + 1):
            if i not in self.seats:
                return i
        return None

    def public_state(self) -> dict:
        """Full table state for spectators/seated (no hole cards)."""
        with self.lock:
            state = {
                "seats": [
                    {"seat": s, "username": seat.username, "stack": seat.stack}
                    for s, seat in sorted(self.seats.items())
                ],
                "spectators": sorted(self.spectators),
                "small_blind": self.small_blind,
                "big_blind": self.big_blind,
                "max_seats": MAX_SEATS,
                "session_ended": self.session_ended,
                "hand": None,
            }
            if self.hand:
                state["hand"] = public_snapshot(self.hand)
            return state

    def legal_actions_for(self, username: str) -> dict:
        with self.lock:
            if not self.hand or self.hand.street == Street.COMPLETE:
                return {}
            try:
                return legal_actions(self.hand, username)
            except KeyError:
                return {}

    def hole_for(self, username: str) -> list[str]:
        with self.lock:
            if not self.hand:
                return []
            try:
                return list(self.hand.player_by_name(username).hole)
            except KeyError:
                return []

    def _broadcast_state(self, new_hand: bool = False) -> None:
        """Push full state to all, plus hole cards privately to seated players."""
        state = self.public_state()
        self.on_broadcast({"type": "state", "state": state, "new_hand": new_hand})
        if self.hand:
            for p in self.hand.players:
                self.on_private(p.username, {"type": "hole", "cards": list(p.hole)})
