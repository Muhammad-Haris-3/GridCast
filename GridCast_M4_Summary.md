# GridCast — M4 Summary: Baselines and the Backtesting Harness

**Milestone:** M4
**Date:** 2026-08-12
**Status:** Complete for the harness and baselines. D-3 was reported resolved and is not — see §6.

---

## 1. Exit criterion

> Rolling-origin harness with all baselines and the ESO benchmark scored on
> history; leakage controls tested.

Met. The leakage controls are tested rather than asserted (§5), which matters
more than usual here: **leakage makes a model look better, not broken**, so it is
invisible in results and has to be caught by construction.

---

## 2. The finding that reframes the whole benchmark

**The ESO forecast in backfilled history is not horizon-matched, and a backtest
comparison against it is therefore not like-for-like.**

Measured directly on 2026-08-12: of 46 future settlement periods held in the
warehouse, **33 had their ESO forecast revised within roughly two hours**, by 5
to 8 gCO₂/kWh.

```
2026-08-12T21:00Z   stored 147  ->  now 142   (-5)
2026-08-12T22:00Z   stored 148  ->  now 141   (-7)
2026-08-12T23:00Z   stored 149  ->  now 141   (-8)
```

The ESO revises continuously as the horizon shortens. So the value stored
against a 2019 period is their **final near-term** forecast, not a 48-hour-ahead
one. A backtest compares GridCast at 48 hours against the ESO at something much
shorter.

**Consequences, and they are not symmetric.** A GridCast win in backtesting
would be a strong result. A GridCast loss is close to uninformative, and must
never be reported as "the ESO forecasts better" without this attached. The
harness prints the caveat on every run and the column is named `ESO_final` so
the limitation travels with the number.

This is the **second independent reason** design §8.3 keeps backtest and live
results in separate columns and never pools them — the first being reconstructed
vintages. Only the live scoreboard can compare like with like, because only
there is each ESO forecast captured with the time we saw it. That makes M5 the
first fair comparison this project will ever produce, which is precisely the
claim it was built to make.

---

## 3. A defect that inflated the benchmark's RMSE sixfold

The first full backtest returned an RMSE/MAE ratio of **9.4** for the ESO at H1
against 1.6 at H2. A well-behaved error distribution gives about 1.3, so a
handful of enormous errors had to exist.

They did. The ESO forecast field carries physically impossible values:

| Period | Actual | ESO forecast |
|---|---|---|
| 2019-07-24 21:00 | 304 | **13,579** |
| 2019-07-24 22:00 | 279 | **11,513** |
| 2019-01-10 00:30 | 287 | **9,899** |

Sixteen periods in 2018–2019, against a highest-ever actual of 447 and an
all-coal ceiling near 900. They are corrupt upstream, not extreme.

**Excluding 16 rows out of 143,996 — 0.011% — changed the benchmark this much:**

| RMSE/MAE ratio | H1 | H2 | H3 | H4 |
|---|---|---|---|---|
| Before | **9.40** | 1.60 | **6.69** | 5.79 |
| After | 1.56 | 1.47 | 1.42 | 1.46 |

MAE barely moved (8.07 → 7.49) because MAE is robust to outliers. RMSE collapsed
because it is not. That is the clearest possible demonstration of why both are
reported rather than one, and it would have been invisible had only MAE been
tracked.

**The uncomfortable part:** design §5.1 specified a range test on
`actual_gco2_kwh`, and it was never implemented. Even implemented it would have
missed this, because the corruption is in the *forecast*. The M2 audit examined
nulls, revisions and band drift in that column and never checked its range.

Now fixed in three places: an `is_eso_forecast_plausible` flag, exclusion from
`is_comparable`, and a committed test. The corrupt rows are flagged rather than
deleted — that the ESO published them is itself worth being able to query.

---

## 4. Backtest results

Seven years, 2,777 origins stepping 24 hours, 96 horizons, 24-hour embargo.
**1,313,678 scored forecast points.** MASE denominator (seasonal naive) = 41.24.

| Model | H1 (0–3h) | H2 (3–12h) | H3 (12–24h) | H4 (24–48h) |
|---|---|---|---|---|
| **ESO_final** *(not horizon-matched)* | **0.182** | **0.236** | **0.247** | **0.235** |
| B0 persistence | 1.033 | 1.281 | 1.427 | 1.474 |
| B1 seasonal naive | 1.282 | 1.305 | 1.235 | 1.376 |
| B2 weekly naive | 1.461 | 1.403 | 1.377 | 1.397 |
| B3 climatology | 1.348 | 1.371 | 1.293 | 1.329 |

*MASE — lower is better; 1.0 means no better than predicting yesterday.*

Three things worth reading off this:

**Persistence decays with horizon exactly as it should** — 1.03 at H1 to 1.47 at
H4. A baseline that did not would indicate a bug in the harness.

**Climatology carries a large positive bias** (+19 to +31 gCO₂/kWh). A trailing
three-year median is anchored in a dirtier grid than the one being forecast:
mean intensity fell from 236 in 2018 to 123 in 2026. On a decarbonising system,
climatology is systematically wrong in a known direction — which is a useful
thing for a baseline to demonstrate.

**The bar is high.** Even discounted for the horizon mismatch, an MAE near 7–10
gCO₂/kWh on a mean of ~125 is a good forecast. Beating it at long horizons is
the interesting question, and nothing here answers it yet.

---

## 5. Leakage controls, tested

The tests use a series whose value equals its own index position, so a returned
value names exactly which period a baseline reached for. A test that only checks
"the number looks plausible" cannot distinguish a correct lookup from one that
reached a day into the future.

| Test | What it catches |
|---|---|
| Guard raises on a leaked frame | The guard itself failing open |
| Guard refuses a frame with no knowability column | Protection depending on someone remembering to carry it |
| Seasonal naive never returns a future period | The big one — see below |
| Seasonal naive steps back in whole days | Landing on something merely old rather than same-time-of-day |
| Seasonal naive skips a missing period | The five upstream outages from M2 A02 |
| Climatology window ends at the issue time | A 2020 forecast informed by 2021 |
| Every baseline respects the embargo | Training on actuals still pending at the origin |

**Why the seasonal-naive test matters most.** A naive implementation reaches
exactly one day back from the *target*. At a 48-hour horizon that period is
still in the future at issue time. The baseline would then be scored using data
it could not have had — and every real model would appear to lose to an
impossible opponent. The harness steps back in whole days until it lands on
something observable, which is what a real forecaster would have to do.

One test failed on first run, and the test was wrong rather than the code: it
planted a missing value at period 1952 when the lookup targets 1953. Recorded
because "the test failed so the code is broken" is the wrong reflex.

---

## 6. D-3 is NOT resolved, and an earlier version of this document said it was

This section originally reported D-3 as settled and `scotland_north` as dropped.
That conclusion was wrong, and the way it went wrong is worth more than the
conclusion would have been.

**The correlations were computed on 44% of the history.** `lnd_om_vintage`
covers 2018-05-09 to 2021-12-19 only — 3.6 years of 8.25. The backfill had died
partway through, and `landing.run_log` recorded exactly why:

```
om_vintage | ingest | failed | rows_read 200,880 | rows_written 193,604
DiskFull: could not extend file because project size limit (512 MB) has been exceeded
```

Three failures compounded:

1. The ingest hit Neon's storage ceiling and stopped.
2. The shell wrapper that ran it ended with `echo "### DONE"`, so the block
   exited 0 and masked the CLI's non-zero status.
3. The correlation query returned 61,105 observations per location — plausible
   enough that nobody asked why it was not 144,000.

The run log did its job. It recorded the failure, with the error class and the
message, at the moment it happened. It was simply never read — which is a worse
failure than not having built it, because the information existed and the
decision was made anyway.

**The figures, valid only for 2018-2021:**

| Location | corr(wind speed, wind share) |
|---|---|
| irish_sea | 0.726 |
| north_sea | 0.716 |
| midlands | 0.715 |
| scotland_south | 0.652 |
| south_coast | 0.589 |
| scotland_north | 0.460 |

They cannot carry the decision. Mean intensity across that window was 180-236
gCO2/kWh against roughly 125 today, and the generation mix that produced these
correlations is not the mix being forecast now. Using them would be fitting a
feature set to a grid that no longer exists — the same drift argument that
disqualified `intensity.index` at M2.

`EXCLUDED_FROM_FEATURES` is now empty. No location is dropped until the backfill
completes and E01 is re-run over the full period.

### 6.1 Completing it needs storage that does not currently exist

The remaining 2021-12 to 2026-08 is roughly 242,000 more landing rows, about
104 MB, plus the same again doubled in `fct_weather_period` because that model
stores one row per settlement period rather than per hour. Against 92 MB free,
it does not fit.

The cheapest fix is the one already applied to the generation mix at M3:
materialise `fct_weather_period` as a view rather than a table. It doubles every
hourly row into two settlement periods purely for join convenience, which is the
same "storing a shape that is only consumed in another shape" habit that cost
115 MB last milestone.

## 7. Where backtest results live

A `backtest` schema of its own, with `bt_run` and `bt_score`. Pooling backtest
and live results now requires an explicit cross-schema join — somebody may still
do it, but not by accident.

Two constraints are enforced in DDL rather than convention: `embargo_hours > 0`,
because an embargo of zero silently permits the most common leakage in
time-series backtesting; and `n > 0` on every score row, so NFR-9's "no accuracy
figure without its sample size" cannot be violated by a row existing.

Per-point scores are **not** stored — 1.3M rows against 95 MB of headroom — and
do not need to be, because the harness is deterministic and committed.

---

## 8. Still open

| Decision | State |
|---|---|
| **D-1** ESO actuals lag | Still placeholders. Requires forward observation. **M5 is blocked on this** |
| **D-2** weather alignment | `step_hold`, provisional. Now measurable via backtest error comparison |
| **D-7** holiday calendar | Demand backfill complete; the comparison is now possible |

### 8.1 The blocking issue

**The scheduled pipeline is not running.** The GitHub Actions workflows exist but
the repository secrets have never been added, so no forward observation is
accumulating — and forward observation is the only way to obtain the ESO actuals
lag (D-1) or a horizon-matched ESO forecast history.

Both are prerequisites for M5's scoreboard. Until the secrets are set, M5 cannot
begin, and the clock on collecting that data has not started.

---

## 9. Next milestone

**M5 — the live forecasting loop**, and the project's defensible stopping point:
forecasts issued on schedule into the append-only register, scored automatically
as actuals arrive, the integrity audit running, and accuracy visible in
production. It cannot start until §8.1 is resolved.
