from sidequest.game.creature_core import RecoveryTrigger


def test_recovery_trigger_on_yield_constant():
    assert RecoveryTrigger.OnYield == "OnYield"
