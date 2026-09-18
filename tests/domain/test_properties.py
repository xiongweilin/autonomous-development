from hypothesis import given
from hypothesis import strategies as st

from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.domain.transitions import StaleCycleError, transition_cycle


@given(actual=st.integers(min_value=0, max_value=100), delta=st.integers(min_value=1, max_value=100))
def test_any_non_current_version_is_rejected(actual: int, delta: int) -> None:
    cycle = DevelopmentCycle(
        id="cycle",
        target_id="target",
        objective_revision_id="objective",
        baseline_release_id="release",
        state=CycleState.NEW,
        version=actual,
    )
    try:
        transition_cycle(cycle, CycleState.BASELINE_VERIFIED, expected_version=actual + delta)
    except StaleCycleError:
        return
    raise AssertionError("stale version unexpectedly accepted")
