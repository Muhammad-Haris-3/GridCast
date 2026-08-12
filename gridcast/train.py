"""Train and evaluate the gradient-boosting forecaster (design 9.2, model G2).

    python -m gridcast.train --origins-from 2023-01-01 --test-from 2026-01-01

G2 uses every feature except the ESO forecast. That exclusion is what makes it
the *fair* competitor: a model taking the ESO forecast as an input and then
beating the ESO has not out-forecast the grid operator, it has bias-corrected
them (design 9.2, the G4 quarantine). G4 may be built later; it will never
appear in the same table as the ESO benchmark.

TRAINING IS OFFLINE. This runs in a scheduled job and writes a joblib artefact.
The serving API never imports scikit-learn, and CI asserts that.

THE SPLIT IS CHRONOLOGICAL, never random. A random split over a time series
lets the model see Tuesday while predicting Monday, which produces a beautiful
score and a useless model.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from gridcast import metrics
from gridcast.baselines import PERIOD, LeakageError
from gridcast.features import (
    assert_no_banned_columns,
    build_features,
    load_intensity_history,
    load_mix_history,
    load_weather_history,
)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
HORIZONS = 96

# The embargo between the newest training data and the issue time. Without it an
# origin trains on actuals that would still have been pending at that moment —
# the most common leakage in time-series work, and one that flatters short
# horizons specifically.
EMBARGO = timedelta(hours=24)

QUANTILES = {"q025": 0.025, "q10": 0.10, "q90": 0.90, "q975": 0.975}


def origins_between(start: datetime, end: datetime, step_hours: int) -> list[datetime]:
    origins, cursor = [], start
    while cursor < end:
        origins.append(cursor)
        cursor += timedelta(hours=step_hours)
    return origins


def build_dataset(
    origins: list[datetime],
    intensity: pd.DataFrame,
    mix: pd.DataFrame,
    weather: pd.DataFrame,
) -> pd.DataFrame:
    """One row per (origin, horizon), with the realised actual as the label."""
    actual = intensity["actual_gco2_kwh"]
    blocks: list[pd.DataFrame] = []

    for origin in origins:
        targets = pd.DatetimeIndex([origin + h * PERIOD for h in range(1, HORIZONS + 1)])
        targets = targets[targets.isin(actual.index)]
        if targets.empty:
            continue

        try:
            frame = build_features(
                origin - EMBARGO,
                targets,
                intensity=intensity,
                mix=mix,
                weather=weather,
                anchor=origin,
            )
        except LeakageError:
            # The only expected failure: an origin so early that nothing was
            # observable yet. Anything else is a bug and must propagate — a bare
            # `except Exception: continue` here silently produced an empty
            # training set and reported "insufficient data", which is a
            # diagnosis of the data rather than of the code that was wrong.
            continue

        frame["target_utc"] = targets
        frame["origin_utc"] = origin
        frame["y"] = actual.reindex(targets).to_numpy(dtype=float)
        blocks.append(frame)

    if not blocks:
        return pd.DataFrame()

    dataset = pd.concat(blocks, ignore_index=True)
    return dataset.dropna(subset=["y"])


def feature_columns(dataset: pd.DataFrame) -> list[str]:
    return [c for c in dataset.columns if c not in {"y", "target_utc", "origin_utc"}]


def horizon_group(horizon: float) -> str:
    if horizon <= 6:
        return "H1"
    if horizon <= 24:
        return "H2"
    if horizon <= 48:
        return "H3"
    return "H4"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origins-from", default="2023-01-01")
    parser.add_argument("--test-from", default="2026-01-01")
    parser.add_argument("--step-hours", type=int, default=12)
    parser.add_argument("--save", action="store_true", help="Write the model artefact")
    args = parser.parse_args()

    print("loading history…")
    intensity = load_intensity_history()
    mix = load_mix_history()
    weather = load_weather_history()
    print(
        f"  intensity {len(intensity):,} | mix {len(mix):,} | "
        f"weather {len(weather):,} rows x {len(weather.columns)} cols"
    )

    train_start = datetime.fromisoformat(args.origins_from).replace(tzinfo=UTC)
    test_start = datetime.fromisoformat(args.test_from).replace(tzinfo=UTC)
    test_end = datetime.now(UTC) - timedelta(days=3)

    train_origins = origins_between(train_start, test_start, args.step_hours)
    test_origins = origins_between(test_start, test_end, args.step_hours)
    print(f"origins: {len(train_origins):,} train, {len(test_origins):,} test")

    print("building features…")
    train_set = build_dataset(train_origins, intensity, mix, weather)
    test_set = build_dataset(test_origins, intensity, mix, weather)
    if train_set.empty or test_set.empty:
        print("insufficient data to train")
        return 1

    columns = feature_columns(train_set)
    assert_no_banned_columns(train_set[columns])
    print(
        f"  train {len(train_set):,} rows | test {len(test_set):,} rows | {len(columns)} features"
    )

    x_train = train_set[columns].to_numpy(dtype=float)
    y_train = train_set["y"].to_numpy(dtype=float)
    x_test = test_set[columns].to_numpy(dtype=float)
    y_test = test_set["y"].to_numpy(dtype=float)

    print("fitting G2…")
    model = HistGradientBoostingRegressor(
        max_iter=400,
        learning_rate=0.06,
        max_depth=7,
        min_samples_leaf=40,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=17,
    )
    model.fit(x_train, y_train)
    predicted = model.predict(x_test)

    # The MASE denominator, from the training period only. Deriving it from the
    # evaluation set would make the same forecast score differently depending on
    # which window it was measured over.
    scale = metrics.seasonal_naive_scale(
        intensity.loc[intensity.index < test_start, "actual_gco2_kwh"].to_numpy()
    )

    results = pd.DataFrame(
        {
            "horizon": test_set["horizon_periods"].to_numpy(),
            "actual": y_test,
            "pred": predicted,
        }
    )
    results["horizon_group"] = results["horizon"].map(horizon_group)

    print(f"\nG2 out-of-sample, {test_start:%Y-%m-%d} onward | MASE scale {scale:.2f}\n")
    print(f"  {'group':<7}{'n':>9}{'MAE':>9}{'RMSE':>9}{'bias':>9}{'MASE':>9}")
    summary = {}
    for group, block in results.groupby("horizon_group"):
        a, p = block["actual"].to_numpy(), block["pred"].to_numpy()
        row = {
            "n": len(block),
            "mae": metrics.mae(a, p),
            "rmse": metrics.rmse(a, p),
            "bias": metrics.bias(a, p),
            "mase": metrics.mase(a, p, scale),
        }
        summary[group] = row
        print(
            f"  {group:<7}{row['n']:>9,}{row['mae']:>9.2f}"
            f"{row['rmse']:>9.2f}{row['bias']:>9.2f}{row['mase']:>9.3f}"
        )

    if args.save:
        MODELS_DIR.mkdir(exist_ok=True)
        import joblib

        artefact = MODELS_DIR / "G2_gbm_v1.joblib"
        joblib.dump({"model": model, "features": columns, "mase_scale": scale}, artefact)
        (MODELS_DIR / "G2_gbm_v1.json").write_text(
            json.dumps(
                {
                    "model_version": "G2_gbm_v1",
                    "family": "hist_gradient_boosting",
                    "trained_at_utc": datetime.now(UTC).isoformat(),
                    "train_from": train_start.isoformat(),
                    "train_to": test_start.isoformat(),
                    "embargo_hours": EMBARGO.total_seconds() / 3600,
                    "features": columns,
                    "mase_scale": scale,
                    "uses_eso_forecast": False,
                    "holdout": summary,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nsaved {artefact.name} ({artefact.stat().st_size / 1e6:.1f} MB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
