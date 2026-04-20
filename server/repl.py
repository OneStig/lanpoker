"""Host terminal REPL. Runs on a native thread alongside the SocketIO server."""

from __future__ import annotations

import shlex
import sys
import threading
from typing import Callable

from .table import Table

HELP = """\
Commands:
  approve <username>           Approve a pending join/seat request
  deny <username>              Reject a pending request
  pending                      List pending requests
  players                      List seated players + stacks + spectators
  seat <username> <seat_num>   Assign/move a player to a seat
  shuffle                      Randomize seat assignments (only between hands)
  stack <username> <amount>    Adjust a player's stack (only between hands)
  blinds <small> <big>         Set blind levels (only between hands)
  kick <username>              Remove player (chips forfeit)
  start                        Start the next hand
  end                          End session and broadcast final stacks
  help                         Show this help
"""


class HostRepl:
    def __init__(self, table: Table, on_exit: Callable[[], None]):
        self.table = table
        self.on_exit = on_exit

    def run(self) -> None:
        sys.stdout.write("Poker host REPL ready. Type `help` for commands.\n> ")
        sys.stdout.flush()
        while True:
            try:
                line = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            line = line.strip()
            if not line:
                sys.stdout.write("> ")
                sys.stdout.flush()
                continue
            # Catch broadly: a handler bug must not kill the host's REPL thread.
            try:
                self._dispatch(line)
            except Exception as e:
                print(f"error: {e}")
            if self.table.session_ended:
                print("Session ended. Shutting down.")
                self.on_exit()
                return
            sys.stdout.write("> ")
            sys.stdout.flush()

    def _dispatch(self, line: str) -> None:
        parts = shlex.split(line)
        if not parts:
            return
        cmd, *args = parts
        handler = getattr(self, f"cmd_{cmd}", None)
        if not handler:
            print(f"unknown command: {cmd}  (try `help`)")
            return
        handler(args)

    def _broadcast_state(self) -> None:
        self.table.on_broadcast({"type": "state", "state": self.table.public_state()})

    def _report(self, ok: bool, msg: str) -> None:
        print(msg if ok else f"error: {msg}")
        if ok:
            self._broadcast_state()

    def cmd_help(self, args):
        print(HELP)

    def cmd_pending(self, args):
        items = self.table.host_pending()
        if not items:
            print("(no pending requests)")
            return
        for r in items:
            print(f"  {r['username']}  [{r['kind']}]  requested_stack={r['requested_stack']}")

    def cmd_players(self, args):
        info = self.table.host_players()
        if info["seated"]:
            print("Seated:")
            for s in info["seated"]:
                print(f"  seat {s['seat']}: {s['username']} ({s['stack']})")
        else:
            print("(no seated players)")
        if info["spectators"]:
            print("Spectators: " + ", ".join(info["spectators"]))

    def cmd_approve(self, args):
        if len(args) != 1:
            print("usage: approve <username>")
            return
        self._report(*self.table.host_approve(args[0]))

    def cmd_deny(self, args):
        if len(args) != 1:
            print("usage: deny <username>")
            return
        self._report(*self.table.host_deny(args[0]))

    def cmd_seat(self, args):
        if len(args) != 2:
            print("usage: seat <username> <seat_num>")
            return
        try:
            n = int(args[1])
        except ValueError:
            print("seat_num must be an integer")
            return
        self._report(*self.table.host_seat(args[0], n))

    def cmd_shuffle(self, args):
        self._report(*self.table.host_shuffle())

    def cmd_stack(self, args):
        if len(args) != 2:
            print("usage: stack <username> <amount>")
            return
        try:
            n = int(args[1])
        except ValueError:
            print("amount must be an integer")
            return
        self._report(*self.table.host_stack(args[0], n))

    def cmd_blinds(self, args):
        if len(args) != 2:
            print("usage: blinds <small> <big>")
            return
        try:
            sb, bb = int(args[0]), int(args[1])
        except ValueError:
            print("blinds must be integers")
            return
        self._report(*self.table.host_blinds(sb, bb))

    def cmd_kick(self, args):
        if len(args) != 1:
            print("usage: kick <username>")
            return
        self._report(*self.table.host_kick(args[0]))

    def cmd_start(self, args):
        ok, msg = self.table.host_start()
        print(msg if ok else f"error: {msg}")

    def cmd_end(self, args):
        self.table.host_end()


def start_repl(table: Table, on_exit: Callable[[], None]) -> threading.Thread:
    repl = HostRepl(table, on_exit)
    t = threading.Thread(target=repl.run, name="host-repl", daemon=True)
    t.start()
    return t
