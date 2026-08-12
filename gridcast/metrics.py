"""Forecast scoring.

MAPE is deliberately absent. GB carbon intensity now reaches single digits on
windy nights, and a percentage error divided by 8 gCO2/kWh explodes — a headline
MAPE would be driven almost entirely by the calmest hours of the cleanest days,
which are also the hours nobody needs a forecast for. MASE replaces it: scale
free, and stable near zero because it divides by a naive model's error rather
than by the observation.
"""

from __future__ import annotations

import numpy as np


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def bias(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean signed error. Positive means the forecast runs high.

    Reported alongside MAE because they fail differently: a model can have
    excellent MAE and a persistent bias, and a biased forecast systematically
    misprices every load-shifting decision in the same direction.
    """
    return float(np.mean(predicted - actual))


def mase(actual: np.ndarray, predicted: np.ndarray, scale: float) -> float:
    """Mean absolute scaled error.

    `scale` is the MAE of the seasonal naive baseline on the *training* window,
    computed once and passed in rather than derived from the evaluation set.
    Deriving it from the evaluation set would make the denominator depend on the
    period being scored, so the same forecast would score differently depending
    on which window it was measured over.

    MASE = 1 means "no better than predicting yesterday's value at this time".
    """
    if scale <= 0:
        return float("nan")
    return mae(actual, predicted) / scale


def seasonal_naive_scale(series: np.ndarray, season: int = 48) -> float:
    """The MASE denominator: MAE of a seasonal naive forecast on the series."""
    if len(series) <= season:
        return float("nan")
    return float(np.nanmean(np.abs(series[season:] - series[:-season])))


def pinball(actual: np.ndarray, quantile_pred: np.ndarray, tau: float) -> float:
    """Pinball loss — the proper scoring rule for a quantile forecast.

    Penalises being above and below asymmetrically according to tau, which is
    what makes it impossible to game by simply widening the interval: a wider
    q10 costs more when the actual lands above it.
    """
    delta = actual - quantile_pred
    return float(np.mean(np.maximum(tau * delta, (tau - 1) * delta)))


def coverage(actual: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> float:
    """Proportion of actuals inside the interval.

    An 80% interval containing 62% of actuals is a broken product, not a small
    miss — it means every stated uncertainty is an understatement, and a user
    acting on it is taking more risk than they were told.
    """
    return float(np.mean((actual >= lower) & (actual <= upper)))


def interval_width(lower: np.ndarray, upper: np.ndarray) -> float:
    """Mean interval width.

    Reported beside coverage to close the obvious loophole: coverage alone can
    always be fixed by widening the interval until it is useless.
    """
    return float(np.mean(upper - lower))
