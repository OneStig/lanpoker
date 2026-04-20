"""
Pure No-Limit Hold'em engine.

No Flask, no sockets, no I/O. Everything here is deterministic given the same
input (caller supplies the RNG-seeded deck for testability).

Terminology:
    - A "hand" (game of poker) has four streets: preflop, flop, turn, river.
    - A "betting round" is one street's worth of action.
    - "to_call" = amount a player must add to match the current bet.
    - "current_bet" = highest per-player commitment this street.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pokerkit import Card as PKCard, StandardHighHand

RANKS = "23456789TJQKA"
SUITS = "cdhs"


def make_deck(rng: Optional[random.Random] = None) -> list[str]:
    """Return a shuffled deck of 52 two-char card strings (e.g. 'As', 'Td')."""
    rng = rng or random.Random()
    deck = [r + s for r in RANKS for s in SUITS]
    rng.shuffle(deck)
    return deck


class Street(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    COMPLETE = "complete"


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"


@dataclass
class PlayerState:
    """A single player's state within one hand."""

    seat: int
    username: str
    stack: int
    hole: list[str] = field(default_factory=list)
    committed_street: int = 0
    committed_hand: int = 0
    folded: bool = False
    all_in: bool = False
    # Used for the "one full raise to re-open" rule against short all-in raises.
    last_full_raise_contrib: int = 0

    @property
    def in_hand(self) -> bool:
        return not self.folded

    @property
    def can_act(self) -> bool:
        return self.in_hand and not self.all_in and self.stack > 0


@dataclass
class Action:
    username: str
    type: ActionType
    amount: int = 0


@dataclass
class Pot:
    """One pot (main or side). `eligible` = usernames that can win it."""

    amount: int
    eligible: set[str]


@dataclass
class HandState:
    """Everything about a single hand in progress."""

    players: list[PlayerState]
    button_seat: int
    small_blind: int
    big_blind: int
    deck: list[str]
    board: list[str] = field(default_factory=list)
    street: Street = Street.PREFLOP
    current_bet: int = 0
    min_raise: int = 0
    to_act_idx: int = 0
    last_aggressor_idx: Optional[int] = None
    # Index of the player whose action closes the round. Moves to the player
    # before a new aggressor whenever betting is re-opened.
    action_closes_at_idx: int = 0
    history: list[Action] = field(default_factory=list)
    pots: list[Pot] = field(default_factory=list)
    winners: list[dict] = field(default_factory=list)

    def player_by_name(self, name: str) -> PlayerState:
        for p in self.players:
            if p.username == name:
                return p
        raise KeyError(name)

    def active_players(self) -> list[PlayerState]:
        return [p for p in self.players if p.in_hand]

    def players_who_can_act(self) -> list[PlayerState]:
        return [p for p in self.players if p.can_act]

    def to_act(self) -> Optional[PlayerState]:
        if self.street in (Street.SHOWDOWN, Street.COMPLETE):
            return None
        if not self.players_who_can_act():
            return None
        return self.players[self.to_act_idx]


def start_hand(
    seated: list[tuple[int, str, int]],
    button_seat: int,
    small_blind: int,
    big_blind: int,
    rng: Optional[random.Random] = None,
) -> HandState:
    """Build a HandState with blinds posted and hole cards dealt.

    `seated` is (seat, username, stack) per playable player. Caller guarantees
    at least two players with positive stacks.
    """
    if len(seated) < 2:
        raise ValueError("need at least 2 players")
    if small_blind <= 0 or big_blind <= 0 or big_blind < small_blind:
        raise ValueError("invalid blind levels")

    # Rotate so the player clockwise of the button comes first.
    seats_in_order = sorted(seated, key=lambda t: t[0])
    start = next(
        (i for i, (s, _, _) in enumerate(seats_in_order) if s > button_seat),
        0,
    )
    rotated = seats_in_order[start:] + seats_in_order[:start]
    players = [PlayerState(seat=s, username=u, stack=stk) for s, u, stk in rotated]

    deck = make_deck(rng)
    for _ in range(2):
        for p in players:
            p.hole.append(deck.pop())

    state = HandState(
        players=players,
        button_seat=button_seat,
        small_blind=small_blind,
        big_blind=big_blind,
        deck=deck,
    )

    n = len(players)
    if n == 2:
        # Heads-up: button is last in rotation, posts SB, and acts first preflop.
        sb_idx, bb_idx = n - 1, 0
        first_to_act = sb_idx
    else:
        sb_idx, bb_idx = 0, 1
        first_to_act = (bb_idx + 1) % n

    _post_blind(state, players[sb_idx], small_blind)
    _post_blind(state, players[bb_idx], big_blind)

    state.current_bet = big_blind
    state.min_raise = big_blind
    state.to_act_idx = first_to_act
    state.action_closes_at_idx = bb_idx
    state.last_aggressor_idx = bb_idx
    return state


def _post_blind(state: HandState, p: PlayerState, amount: int) -> None:
    post = min(amount, p.stack)
    p.stack -= post
    p.committed_street += post
    p.committed_hand += post
    if p.stack == 0:
        p.all_in = True


def legal_actions(state: HandState, username: str) -> dict:
    """
    Return a dict describing what `username` can legally do right now.
    Shape:
        {
          "can_check": bool,
          "can_call": bool,
          "call_amount": int,   # chips to put in to call (may be < to_call if short)
          "can_bet": bool,
          "can_raise": bool,
          "min_raise_to": int,  # total "raise to" amount
          "max_raise_to": int,  # total (= player's stack + committed_street)
        }
    """
    p = state.player_by_name(username)
    result = {
        "can_check": False,
        "can_call": False,
        "call_amount": 0,
        "can_bet": False,
        "can_raise": False,
        "min_raise_to": 0,
        "max_raise_to": 0,
    }
    if not p.can_act or state.to_act() is not p:
        return result

    to_call = state.current_bet - p.committed_street
    if to_call <= 0:
        result["can_check"] = True
        if p.stack > 0:
            result["can_bet"] = True
            result["min_raise_to"] = min(state.big_blind, p.stack)
            result["max_raise_to"] = p.stack
    else:
        result["can_call"] = True
        result["call_amount"] = min(to_call, p.stack)
        if p.stack > to_call:
            # Min raise-to is the full increment if stack permits, else a shove.
            full_min_raise_to = state.current_bet + state.min_raise
            max_raise_to = p.committed_street + p.stack
            if max_raise_to > state.current_bet:
                result["can_raise"] = True
                result["min_raise_to"] = min(full_min_raise_to, max_raise_to)
                result["max_raise_to"] = max_raise_to
    return result


def apply_action(state: HandState, username: str, action: ActionType, amount: int = 0) -> None:
    """
    Mutate `state` by applying `action` from `username`. Raises ValueError if
    illegal. Advances turn, street transitions, and hand completion as needed.
    """
    p = state.player_by_name(username)
    if state.to_act() is not p:
        raise ValueError(f"not {username}'s turn")
    legal = legal_actions(state, username)

    if action == ActionType.FOLD:
        p.folded = True
        state.history.append(Action(username, ActionType.FOLD))

    elif action == ActionType.CHECK:
        if not legal["can_check"]:
            raise ValueError("cannot check")
        state.history.append(Action(username, ActionType.CHECK))

    elif action == ActionType.CALL:
        if not legal["can_call"]:
            raise ValueError("cannot call")
        put = legal["call_amount"]
        p.stack -= put
        p.committed_street += put
        p.committed_hand += put
        if p.stack == 0:
            p.all_in = True
        state.history.append(Action(username, ActionType.CALL, put))

    elif action in (ActionType.BET, ActionType.RAISE):
        is_bet = action == ActionType.BET
        if is_bet and not legal["can_bet"]:
            raise ValueError("cannot bet")
        if not is_bet and not legal["can_raise"]:
            raise ValueError("cannot raise")

        raise_to = amount
        if raise_to < legal["min_raise_to"] or raise_to > legal["max_raise_to"]:
            raise ValueError(
                f"illegal size: got {raise_to}, need {legal['min_raise_to']}..{legal['max_raise_to']}"
            )

        put = raise_to - p.committed_street
        p.stack -= put
        p.committed_street = raise_to
        p.committed_hand += put
        if p.stack == 0:
            p.all_in = True

        raise_increment = raise_to - state.current_bet
        state.current_bet = raise_to
        # Short all-in raises move the bet but do not re-open action.
        if raise_increment >= state.min_raise:
            state.min_raise = raise_increment
            idx_p = state.players.index(p)
            state.action_closes_at_idx = (idx_p - 1) % len(state.players)
            state.last_aggressor_idx = idx_p
            p.last_full_raise_contrib = raise_to

        state.history.append(
            Action(username, ActionType.BET if is_bet else ActionType.RAISE, raise_to)
        )
    else:
        raise ValueError(f"unknown action {action}")

    _advance_turn(state)


def _advance_turn(state: HandState) -> None:
    """Move the turn forward; close the street or hand if appropriate."""
    alive = state.active_players()
    if len(alive) == 1:
        _award_fold_out(state, alive[0])
        return

    if not state.players_who_can_act():
        _run_out_and_showdown(state)
        return

    n = len(state.players)
    idx = state.to_act_idx
    while True:
        idx = (idx + 1) % n
        # Wrapping past the action-closer ends the street if everyone matched.
        if idx == (state.action_closes_at_idx + 1) % n:
            if _street_complete(state):
                _advance_street(state)
                return
        if state.players[idx].can_act:
            state.to_act_idx = idx
            return
        if idx == state.to_act_idx:
            if _street_complete(state):
                _advance_street(state)
            return


def _street_complete(state: HandState) -> bool:
    """True if every non-folded, non-all-in player has matched current_bet."""
    for p in state.players:
        if p.folded or p.all_in:
            continue
        if p.committed_street != state.current_bet:
            return False
    return True


def _advance_street(state: HandState) -> None:
    """Move to the next street or to showdown."""
    for p in state.players:
        p.committed_street = 0
    state.current_bet = 0
    state.min_raise = state.big_blind

    if state.street == Street.PREFLOP:
        state.street = Street.FLOP
        _burn_and_deal(state, 3)
    elif state.street == Street.FLOP:
        state.street = Street.TURN
        _burn_and_deal(state, 1)
    elif state.street == Street.TURN:
        state.street = Street.RIVER
        _burn_and_deal(state, 1)
    elif state.street == Street.RIVER:
        _showdown(state)
        return

    if not state.players_who_can_act():
        _run_out_and_showdown(state)
        return

    n = len(state.players)
    # Postflop the player left of the button acts first; in our rotation that
    # is index 0 (in heads-up that is the BB / non-button).
    idx = 0
    for _ in range(n):
        if state.players[idx].can_act:
            state.to_act_idx = idx
            break
        idx = (idx + 1) % n

    idx = (state.to_act_idx - 1) % n
    for _ in range(n):
        p = state.players[idx]
        if p.can_act or p.all_in or p.folded:
            state.action_closes_at_idx = idx
            break
        idx = (idx - 1) % n
    state.last_aggressor_idx = None


def _burn_and_deal(state: HandState, n: int) -> None:
    state.deck.pop()
    for _ in range(n):
        state.board.append(state.deck.pop())


def _award_fold_out(state: HandState, winner: PlayerState) -> None:
    """Single player left: wins entire pot."""
    total = sum(p.committed_hand for p in state.players)
    winner.stack += total
    state.pots = [Pot(amount=total, eligible={winner.username})]
    state.winners = [{"pot_idx": 0, "usernames": [winner.username], "amount": total}]
    state.street = Street.COMPLETE


def _run_out_and_showdown(state: HandState) -> None:
    """Deal remaining streets without further betting, then showdown."""
    for p in state.players:
        p.committed_street = 0
    state.current_bet = 0

    while state.street != Street.RIVER:
        if state.street == Street.PREFLOP:
            state.street = Street.FLOP
            _burn_and_deal(state, 3)
        elif state.street == Street.FLOP:
            state.street = Street.TURN
            _burn_and_deal(state, 1)
        elif state.street == Street.TURN:
            state.street = Street.RIVER
            _burn_and_deal(state, 1)
    _showdown(state)


def _showdown(state: HandState) -> None:
    """Evaluate hands, build pots with side-pot logic, award chips."""
    state.street = Street.SHOWDOWN
    pots = _build_pots(state)

    strengths: dict[str, StandardHighHand] = {}
    for p in state.players:
        if not p.folded:
            strengths[p.username] = _evaluate(p.hole, state.board)

    winners_log: list[dict] = []
    for pot_idx, pot in enumerate(pots):
        contenders = [u for u in pot.eligible if u in strengths]
        if not contenders:
            continue
        best = max(strengths[u] for u in contenders)
        winners = [u for u in contenders if strengths[u] == best]
        share = pot.amount // len(winners)
        remainder = pot.amount - share * len(winners)
        for w in winners:
            state.player_by_name(w).stack += share
        # Odd chip goes to the first winner left of the button.
        if remainder:
            for p in state.players:
                if p.username in winners:
                    p.stack += remainder
                    break
        winners_log.append(
            {"pot_idx": pot_idx, "usernames": winners, "amount": pot.amount}
        )

    state.pots = pots
    state.winners = winners_log
    state.street = Street.COMPLETE


def _build_pots(state: HandState) -> list[Pot]:
    """Build main + side pots layer by layer from each player's contribution.

    For each distinct commitment level L, the layer pot is
    (L - prev_L) * (players with commit >= L), eligible to non-folded players.
    Adjacent layers with identical eligibility are merged.
    """
    contribs = [(p.username, p.committed_hand, p.folded) for p in state.players]
    levels = sorted({c for _, c, _ in contribs if c > 0})
    pots: list[Pot] = []
    prev = 0
    for level in levels:
        layer = level - prev
        amount = 0
        eligible: set[str] = set()
        for name, contrib, folded in contribs:
            if contrib >= level:
                amount += layer
                if not folded:
                    eligible.add(name)
        if amount > 0:
            if pots and pots[-1].eligible == eligible:
                pots[-1].amount += amount
            else:
                pots.append(Pot(amount=amount, eligible=eligible))
        prev = level
    return pots


def _evaluate(hole: list[str], board: list[str]) -> StandardHighHand:
    """Best 5-of-7 via pokerkit. Returned objects are orderable (larger = better)."""
    cards = [PKCard(c[0], c[1]) for c in hole + board]
    return StandardHighHand.from_game(cards)


def public_snapshot(state: HandState) -> dict:
    """Serialize for broadcast. Excludes hole cards (sent separately/private)."""
    return {
        "street": state.street.value,
        "board": list(state.board),
        "current_bet": state.current_bet,
        "min_raise": state.min_raise,
        "button_seat": state.button_seat,
        "small_blind": state.small_blind,
        "big_blind": state.big_blind,
        "to_act": state.to_act().username if state.to_act() else None,
        "players": [
            {
                "seat": p.seat,
                "username": p.username,
                "stack": p.stack,
                "committed_street": p.committed_street,
                "committed_hand": p.committed_hand,
                "folded": p.folded,
                "all_in": p.all_in,
            }
            for p in state.players
        ],
        "pot_total": sum(p.committed_hand for p in state.players),
        "pots": [
            {"amount": pot.amount, "eligible": sorted(pot.eligible)}
            for pot in state.pots
        ],
        "winners": state.winners,
        "history": [
            {"username": a.username, "type": a.type.value, "amount": a.amount}
            for a in state.history
        ],
    }
