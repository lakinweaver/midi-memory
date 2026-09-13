import pytest

from app.config import Settings


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


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "data",
        idle_seconds=45.0,
        min_notes=4,
        min_seconds=2.0,
        password="",
    )
    s.ensure_dirs()
    return s
