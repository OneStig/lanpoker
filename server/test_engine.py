"""Sanity tests for the NLHE engine."""

import random

import pytest

from server.engine import (
    ActionType,
    HandState,
    PlayerState,
    Street,
    _build_pots,
    _evaluate,
    apply_action,
    start_hand,
)


def test_royal_flush_beats_straight_flush():
    royal = _evaluate(["As", "Ks"], ["Qs", "Js", "Ts", "2h", "3d"])
    straight_flush = _evaluate(["9s", "8s"], ["7s", "6s", "5s", "2h", "3d"])
    assert royal > straight_flush


def test_pair_beats_high_card():
    pair = _evaluate(["As", "Ad"], ["Kh", "Qc", "Jd", "2h", "3s"])
    high = _evaluate(["As", "Kd"], ["Qh", "Jc", "9d", "2h", "3s"])
    assert pair > high


def test_tie_same_straight():
    a = _evaluate(["9s", "8d"], ["7h", "6c", "5d", "2h", "3s"])
    b = _evaluate(["9c", "8h"], ["7h", "6c", "5d", "2h", "3s"])
    assert a == b


def _mk(name, stack, committed, folded=False):
    p = PlayerState(seat=0, username=name, stack=stack)
    p.committed_hand = committed
    p.folded = folded
    return p


def test_single_pot_when_all_commit_equal():
    state = HandState(
        players=[_mk("a", 0, 100), _mk("b", 0, 100), _mk("c", 0, 100)],
        button_seat=0, small_blind=1, big_blind=2, deck=[],
    )
    pots = _build_pots(state)
    assert len(pots) == 1
    assert pots[0].amount == 300
    assert pots[0].eligible == {"a", "b", "c"}


def test_side_pot_short_all_in():
    # a all-in for 50, b and c continue to 200 each
    state = HandState(
        players=[_mk("a", 0, 50), _mk("b", 0, 200), _mk("c", 0, 200)],
        button_seat=0, small_blind=1, big_blind=2, deck=[],
    )
    pots = _build_pots(state)
    assert len(pots) == 2
    # Main: 50 * 3 = 150, eligible = all three
    assert pots[0].amount == 150
    assert pots[0].eligible == {"a", "b", "c"}
    # Side: 150 * 2 = 300, eligible = b, c
    assert pots[1].amount == 300
    assert pots[1].eligible == {"b", "c"}


def test_folded_player_contributes_but_ineligible():
    # a contributes 100 then folds, b and c go to 200
    state = HandState(
        players=[_mk("a", 0, 100, folded=True), _mk("b", 0, 200), _mk("c", 0, 200)],
        button_seat=0, small_blind=1, big_blind=2, deck=[],
    )
    pots = _build_pots(state)
    # Layer 1 (0..100): 300 chips, eligible = b, c (a folded)
    # Layer 2 (100..200): 200 chips, eligible = b, c
    # Identical eligibility -> merged into one pot
    assert len(pots) == 1
    assert pots[0].amount == 500
    assert pots[0].eligible == {"b", "c"}


def test_three_way_all_in_different_stacks():
    # a=50, b=100, c=200 all-in
    state = HandState(
        players=[_mk("a", 0, 50), _mk("b", 0, 100), _mk("c", 0, 200)],
        button_seat=0, small_blind=1, big_blind=2, deck=[],
    )
    pots = _build_pots(state)
    assert len(pots) == 3
    assert pots[0].amount == 150 and pots[0].eligible == {"a", "b", "c"}
    assert pots[1].amount == 100 and pots[1].eligible == {"b", "c"}
    assert pots[2].amount == 100 and pots[2].eligible == {"c"}


def test_preflop_3way_fold_around_bb_wins():
    rng = random.Random(42)
    state = start_hand(
        seated=[(1, "alice", 1000), (2, "bob", 1000), (3, "carl", 1000)],
        button_seat=1, small_blind=5, big_blind=10, rng=rng,
    )
    # button=alice(1) -> SB=bob(2), BB=carl(3), UTG/button=alice acts first.
    apply_action(state, "alice", ActionType.FOLD)
    apply_action(state, "bob", ActionType.FOLD)
    assert state.street == Street.COMPLETE
    # Carl (BB) wins SB(5) + alice's posted 0 + his own 10 back -> 990+15=1005
    assert state.player_by_name("carl").stack == 1005


def test_preflop_all_call_advances_to_flop():
    rng = random.Random(1)
    state = start_hand(
        seated=[(1, "a", 1000), (2, "b", 1000), (3, "c", 1000)],
        button_seat=1, small_blind=5, big_blind=10, rng=rng,
    )
    # c is UTG (left of BB=b... wait: button=1=a, SB=b, BB=c) - check ordering
    # Actually: button_seat=1, so rotation starts after seat 1 -> b(2), c(3), a(1)
    # players[0]=b (SB), players[1]=c (BB), players[2]=a (button)
    # Preflop first to act = left of BB = button (a)
    assert state.to_act().username == "a"
    apply_action(state, "a", ActionType.CALL)   # a calls 10
    apply_action(state, "b", ActionType.CALL)   # b (SB) completes
    apply_action(state, "c", ActionType.CHECK)  # c (BB) checks option
    assert state.street == Street.FLOP
    assert len(state.board) == 3
    # Postflop: first to act = left of button = SB (b)
    assert state.to_act().username == "b"


def test_min_raise_enforced():
    rng = random.Random(1)
    state = start_hand(
        seated=[(1, "a", 1000), (2, "b", 1000), (3, "c", 1000)],
        button_seat=1, small_blind=5, big_blind=10, rng=rng,
    )
    # a tries to raise to 15 (only +5 over BB=10, less than min-raise of 10) -> illegal
    with pytest.raises(ValueError):
        apply_action(state, "a", ActionType.RAISE, amount=15)
    # Min raise is to 20
    apply_action(state, "a", ActionType.RAISE, amount=20)
    assert state.current_bet == 20
    assert state.min_raise == 10


def test_heads_up_button_acts_first_preflop():
    rng = random.Random(1)
    state = start_hand(
        seated=[(1, "a", 1000), (2, "b", 1000)],
        button_seat=1, small_blind=5, big_blind=10, rng=rng,
    )
    # Heads up: button = SB = a. a acts first preflop.
    assert state.to_act().username == "a"


def test_fold_out_awards_pot():
    rng = random.Random(1)
    state = start_hand(
        seated=[(1, "a", 1000), (2, "b", 1000)],
        button_seat=1, small_blind=5, big_blind=10, rng=rng,
    )
    # a (button/SB) folds -> b wins 5 + 10 ... wait, a posted 5, b posted 10. a folds.
    # b wins a's 5, so b = 1000 - 10 + (5 + 10) = 1005
    apply_action(state, "a", ActionType.FOLD)
    assert state.street == Street.COMPLETE
    assert state.player_by_name("b").stack == 1005
    assert state.player_by_name("a").stack == 995


def test_all_in_showdown_runs_out_board():
    rng = random.Random(7)
    state = start_hand(
        seated=[(1, "a", 100), (2, "b", 100)],
        button_seat=1, small_blind=5, big_blind=10, rng=rng,
    )
    # a shoves, b calls all-in
    apply_action(state, "a", ActionType.RAISE, amount=100)  # a all-in (total)
    apply_action(state, "b", ActionType.CALL)
    assert state.street == Street.COMPLETE
    assert len(state.board) == 5
    # One of them won 200 (or split)
    total = state.player_by_name("a").stack + state.player_by_name("b").stack
    assert total == 200
