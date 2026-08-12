"""Leakage controls, tested rather than asserted.

Every accuracy claim GridCast makes depends on one thing: that no forecast used
information unavailable when it was issued. That property is invisible in
results — leakage makes a model look *better*, not broken — so it has to be
tested directly.

The trick used throughout: build a series whose value equals its own position,
so a returned value names exactly which period a baseline reached for. A test
that only checks "the number looks plausible" cannot tell a correct lookup from
one that reached a day into the future.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from gridcast.baselines import (
    PERIOD,
    PERIODS_PER_DAY,
    PERIODS_PER_WEEK,
    LeakageError,
    Observed,
    assert_knowable,
    build_climatology,
    persistence,
    seasonal_naive,
    weekly_naive,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def positional_series(periods: int = 4000) -> pd.Series:
    """A series where value == index position, so a value names its own period."""
    index = pd.DatetimeIndex([START + i * PERIOD for i in range(periods)])
    return pd.Series(range(periods), index=index, dtype=float)


def at(position: int) -> datetime:
    return START + position * PERIOD


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


def test_guard_raises_on_a_deliberately_leaked_frame() -> None:
    run_at = datetime(2024, 6, 1, tzinfo=UTC)
    frame = pd.DataFrame(
        {"knowable_at_utc": [run_at - timedelta(hours=1), run_at + timedelta(minutes=1)]}
    )
    with pytest.raises(LeakageError, match="not knowable"):
        assert_knowable(frame, run_at)


def test_guard_accepts_a_frame_that_is_entirely_in_the_past() -> None:
    run_at = datetime(2024, 6, 1, tzinfo=UTC)
    frame = pd.DataFrame({"knowable_at_utc": [run_at - timedelta(hours=5), run_at]})
    assert len(assert_knowable(frame, run_at)) == 2


def test_guard_refuses_a_frame_it_cannot_check() -> None:
    """A source without a knowability column must not pass silently.

    Silently allowing it would make the guard's protection depend on whether
    somebody remembered to carry the column — which is exactly the kind of rule
    that erodes.
    """
    with pytest.raises(LeakageError, match="no knowable_at_utc"):
        assert_knowable(pd.DataFrame({"value": [1, 2]}), datetime(2024, 6, 1, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Baselines must never reach past the issue time
# ---------------------------------------------------------------------------


def test_persistence_uses_the_last_observable_period_not_the_latest_one() -> None:
    observed = Observed(actual=positional_series())
    run_at = at(1000)
    targets = pd.DatetimeIndex([at(1000 + h) for h in range(1, 97)])

    predicted = persistence(observed, run_at, targets)

    assert set(predicted) == {1000.0}, "persistence must hold the value at the issue time"


def test_seasonal_naive_never_returns_a_future_period() -> None:
    """The failure this catches would flatter every long horizon.

    A naive implementation reaches exactly one day back from the *target*. For a
    48-hour horizon that period is still in the future at issue time, so the
    baseline would be scored using data it could not have had — and the real
    models would then appear to lose to an impossible opponent.
    """
    observed = Observed(actual=positional_series())
    run_at = at(2000)
    targets = pd.DatetimeIndex([at(2000 + h) for h in range(1, 97)])

    predicted = seasonal_naive(observed, run_at, targets)

    assert (
        predicted <= 2000
    ).all(), f"seasonal naive reached period {predicted.max():.0f} from an issue time at 2000"


def test_seasonal_naive_steps_back_in_whole_days() -> None:
    """It must land on the same time of day, not merely on something old enough."""
    observed = Observed(actual=positional_series())
    run_at = at(2000)

    # A target 60 periods ahead: one day back is period 2012, still in the
    # future, so it must step back a second day to 1964.
    target = at(2060)
    (value,) = seasonal_naive(observed, run_at, pd.DatetimeIndex([target]))

    assert value == 2060 - 2 * PERIODS_PER_DAY
    assert (2060 - value) % PERIODS_PER_DAY == 0


def test_weekly_naive_never_returns_a_future_period() -> None:
    observed = Observed(actual=positional_series())
    run_at = at(2000)
    targets = pd.DatetimeIndex([at(2000 + h) for h in range(1, 97)])

    predicted = weekly_naive(observed, run_at, targets)

    assert (predicted <= 2000).all()
    assert all(
        (2000 + h - v) % PERIODS_PER_WEEK == 0 for h, v in zip(range(1, 97), predicted, strict=True)
    )


def test_seasonal_naive_skips_a_missing_period_rather_than_returning_nan() -> None:
    """Five upstream outages exist in real history (M2 finding A02)."""
    # Target 2001 steps back one day to 1953, so that is the period to remove.
    series = positional_series()
    series.iloc[2001 - PERIODS_PER_DAY] = float("nan")
    observed = Observed(actual=series)

    (value,) = seasonal_naive(observed, at(2000), pd.DatetimeIndex([at(2001)]))

    assert value == 2001 - 2 * PERIODS_PER_DAY, "must step a further day back, not return NaN"


# ---------------------------------------------------------------------------
# Climatology
# ---------------------------------------------------------------------------


def test_climatology_window_ends_at_the_issue_time() -> None:
    """A climatology used to forecast 2020 must not contain 2021."""
    observed = Observed(actual=positional_series(periods=40000))
    run_at = at(20000)

    profile = build_climatology(observed, run_at)

    assert not profile.empty
    assert profile.max() <= 20000, "climatology drew on periods after the issue time"


def test_climatology_is_empty_before_any_history_exists() -> None:
    observed = Observed(actual=positional_series())
    profile = build_climatology(observed, START - timedelta(days=1))
    assert profile.empty


# ---------------------------------------------------------------------------
# The embargo
# ---------------------------------------------------------------------------


def test_every_baseline_respects_an_embargo() -> None:
    """The harness trains to origin - embargo, never to the origin itself.

    Without the gap an origin can train on actuals that would still have been
    pending at that moment — the most common leakage in time-series
    backtesting, and one that flatters short horizons specifically.
    """
    observed = Observed(actual=positional_series())
    origin = at(2000)
    embargo_periods = 48  # 24 hours
    train_until = origin - embargo_periods * PERIOD
    targets = pd.DatetimeIndex([origin + h * PERIOD for h in range(1, 97)])

    for name, fn in (
        ("persistence", persistence),
        ("seasonal_naive", seasonal_naive),
        ("weekly_naive", weekly_naive),
    ):
        predicted = fn(observed, train_until, targets)
        assert (
            predicted <= 2000 - embargo_periods
        ).all(), f"{name} used data inside the embargo window"
