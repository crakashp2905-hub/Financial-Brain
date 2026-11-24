import pytest

from financial_brain.learning.bellman import BellmanError, Transition, fitted_q_iteration, target


def test_two_step_hand_computed_bellman_backup_and_terminal_value():
    terminal = Transition("s1", "EXIT", 2.0, None, True)
    first = Transition("s0", "ENTER", 1.0, "s1", False)
    q = fitted_q_iteration((first, terminal), gamma=0.5, iterations=2)
    assert q[("s1", "EXIT")] == 2.0
    assert q[("s0", "ENTER")] == 2.0
    assert target(terminal, q, gamma=0.5) == 2.0  # no terminal bootstrap


def test_three_step_fixture_uses_logged_action_support_and_deterministic_updates():
    rows = (
        Transition("s2", "CASH", 3.0, None, True),
        Transition("s1", "HOLD", 2.0, "s2", False),
        Transition("s0", "ENTER", 1.0, "s1", False),
        Transition("s1", "CASH", 0.0, "s2", False),
    )
    q = fitted_q_iteration(rows, gamma=0.5, iterations=3)
    assert q == fitted_q_iteration(rows, gamma=0.5, iterations=3)
    assert q[("s2", "CASH")] == 3.0
    assert q[("s1", "HOLD")] == 3.5
    assert q[("s0", "ENTER")] == 2.75
    assert ("s0", "CASH") not in q  # no unseen action value is generated


def test_terminal_state_and_missing_support_are_rejected_or_do_not_bootstrap():
    with pytest.raises(BellmanError, match="cannot bootstrap"):
        Transition("s", "a", 1.0, "next", True)
    row = Transition("s", "a", 1.0, "next", False)
    with pytest.raises(BellmanError, match="no supported action"):
        target(row, {}, gamma=0.9)


@pytest.mark.parametrize("gamma", [-0.1, 1.1, float("nan")])
def test_invalid_discount_is_rejected(gamma):
    with pytest.raises(BellmanError, match="gamma"):
        target(Transition("s", "a", 1.0, None, True), {}, gamma=gamma)
