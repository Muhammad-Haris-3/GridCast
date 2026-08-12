"""Baselines and the institutional benchmark.

A baseline establishes that a model has learned anything at all. The benchmark
establishes whether it is competitive with the organisation that runs the grid.
The distinction matters: beating persistence is table stakes, and beating the
ESO is a genuine claim.

Every baseline here takes an issue time and returns forecasts for targets after
it, using **only values observable at that issue time**. That constraint is the
entire point — a seasonal naive that reaches for yesterday's value when
yesterday has not been published yet is not a baseline, it is leakage wearing a
baseline's name, and it would set an impossibly high bar that the real models
then appear to fail against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

PERIOD = timedelta(minutes=30)
PERIODS_PER_DAY = 48
PERIODS_PER_WEEK = 336


class LeakageError(RuntimeError):
    """A value was used that could not have been known at issue time."""


def assert_knowable(
    frame: pd.DataFrame, run_at: datetime, column: str = "knowable_at_utc"
) -> pd.DataFrame:
    """Every row must have been available to us at `run_at`. No exceptions.

    Called from training and from serving, from the same module, so the two
    cannot drift apart. A feature source without a knowability column cannot
    pass through it — which is why every staging model carries one.
    """
    if column not in frame.columns:
        raise LeakageError(f"frame has no {column}; it cannot be checked for leakage")

    violations = frame.loc[frame[column] > run_at]
    if len(violations):
        latest = violations[column].max()
        raise LeakageError(
            f"{len(violations)} row(s) not knowable at {run_at:%Y-%m-%d %H:%M}; "
            f"the latest is {latest:%Y-%m-%d %H:%M}"
        )
    return frame


@dataclass(frozen=True)
class Observed:
    """The actual series, indexed by settlement period start.

    Held as a plain Series so a baseline is a lookup rather than a query. The
    whole history is ~144k float64 values — about 1 MB — so there is no reason
    to go back to the database inside a loop over 2,500 origins.
    """

    actual: pd.Series  # index: sp_start_utc (tz-aware), values: gCO2/kWh

    def knowable_at(self, run_at: datetime) -> pd.Series:
        """The slice of the series observable at an issue time."""
        return self.actual.loc[self.actual.index <= run_at]

    def last_known(self, run_at: datetime) -> float:
        observable = self.knowable_at(run_at).dropna()
        return float(observable.iloc[-1]) if len(observable) else float("nan")

    def most_recent_seasonal(self, target: datetime, run_at: datetime, season: int) -> float:
        """The most recent same-time-of-day value that was knowable at issue time.

        A naive implementation reaches back exactly one season from the target.
        For a 48-hour horizon that value often lies in the future relative to the
        issue time — so the honest version steps back in whole seasons until it
        lands on something observable, which is what a real seasonal naive
        forecaster would have to do.
        """
        step = season * PERIOD
        candidate = target - step
        while candidate > run_at:
            candidate -= step

        # Walk further back if the landed period is missing or still pending;
        # 14 attempts is two weeks of daily steps, well past any observed gap.
        for _ in range(14):
            if candidate in self.actual.index:
                value = self.actual.loc[candidate]
                if pd.notna(value):
                    return float(value)
            candidate -= step
        return float("nan")


def persistence(observed: Observed, run_at: datetime, targets: pd.DatetimeIndex) -> np.ndarray:
    """B0 — hold the last observed value flat across every horizon."""
    return np.full(len(targets), observed.last_known(run_at))


def seasonal_naive(observed: Observed, run_at: datetime, targets: pd.DatetimeIndex) -> np.ndarray:
    """B1 — the same settlement period yesterday. The MASE denominator."""
    return np.array([observed.most_recent_seasonal(t, run_at, PERIODS_PER_DAY) for t in targets])


def weekly_naive(observed: Observed, run_at: datetime, targets: pd.DatetimeIndex) -> np.ndarray:
    """B2 — the same settlement period last week.

    Weaker than the daily version on level, stronger on weekday/weekend shape,
    which is why both are kept rather than one being chosen in advance.
    """
    return np.array([observed.most_recent_seasonal(t, run_at, PERIODS_PER_WEEK) for t in targets])


def build_climatology(observed: Observed, run_at: datetime, years: int = 3) -> pd.Series:
    """B3 — the median by (period-of-day, month) over a trailing window.

    Strictly point-in-time: the window ends at the issue time, so a climatology
    used to forecast 2020 never contains 2021.

    Rebuilt at the start of each calendar month of origins rather than at every
    origin. A three-year median moves imperceptibly in thirty days, and
    recomputing it 2,500 times instead of 84 would dominate the harness runtime
    for no change in the answer. The approximation is one-directional and
    conservative: the climatology is always slightly staler than it could be,
    never fresher.
    """
    window = observed.actual.loc[
        (observed.actual.index <= run_at)
        & (observed.actual.index > run_at - timedelta(days=365 * years))
    ].dropna()

    if window.empty:
        return pd.Series(dtype=float)

    frame = pd.DataFrame({"actual": window})
    frame["period_of_day"] = frame.index.hour * 2 + (frame.index.minute >= 30).astype(int)
    frame["month"] = frame.index.month
    return frame.groupby(["month", "period_of_day"])["actual"].median()


def climatology(profile: pd.Series, targets: pd.DatetimeIndex) -> np.ndarray:
    if profile.empty:
        return np.full(len(targets), np.nan)
    keys = list(
        zip(targets.month, targets.hour * 2 + (targets.minute >= 30).astype(int), strict=True)
    )
    return np.array([profile.get(k, np.nan) for k in keys])


# The registry the harness iterates. The ESO benchmark is not here because it is
# not computed — it is read from the warehouse, having been published by someone
# else, which is exactly what makes it worth comparing against.
BASELINES = {
    "B0_persistence": persistence,
    "B1_seasonal_naive": seasonal_naive,
    "B2_weekly_naive": weekly_naive,
}
