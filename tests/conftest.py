"""Fixtures shared by every suite.

`settings` is deliberately defined per-suite rather than here: the server and the
client have different Settings classes now, and a test asking for "settings"
means the settings of whichever side it is exercising.
"""
import pytest


class FakeClock:
    """A monotonic clock the tests drive by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
