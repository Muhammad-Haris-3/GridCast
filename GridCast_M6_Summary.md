# GridCast — M6 Summary: Modelling Depth

**Milestone:** M6
**Date:** 2026-08-13
**Status:** Complete for modelling. G2 trained, calibrated and issuing live as a
challenger. Error segmentation and D-2 deferred to M7 (§9).

---

## 1. The result

G2 — HistGradientBoosting on 39 point-in-time features, no ESO input — trained
on 209,924 rows from 2023–2025 and evaluated out-of-sample on 2026.

| Horizon | n | MAE | RMSE | bias | **MASE** |
|---|---|---|---|---|---|
| H1 (0–3 h) | 2,652 | 19.11 | 24.82 | −0.77 | **0.460** |
| H2 (3–12 h) | 7,956 | 21.94 | 27.76 | +0.63 | **0.529** |
| H3 (12–24 h) | 10,608 | 21.74 | 27.68 | −0.07 | **0.524** |
| H4 (24–48 h) | 21,216 | 22.71 | 28.45 | +0.45 | **0.547** |

Against the M4 baselines on the same metric:

| Model | H1 | H2 | H3 | H4 |
|---|---|---|---|---|
| **G2 gradient boosting** | **0.460** | **0.529** | **0.524** | **0.547** |
| B0 persistence | 1.033 | 1.281 | 1.427 | 1.474 |
| B1 seasonal naive | 1.282 | 1.305 | 1.235 | 1.376 |
| B3 climatology | 1.348 | 1.371 | 1.293 | 1.329 |
| *ESO_final (not horizon-matched)* | *0.182* | *0.236* | *0.247* | *0.235* |

**G2 roughly halves the seasonal-naive error at every horizon.**

The shape is more informative than the level. G2's MASE moves from 0.460 to
0.547 across a sixteen-fold increase in horizon; persistence moves from 1.033 to
1.474. A model whose accuracy barely degrades with horizon is one that holds
genuine information about the future rather than inertia about the past — which
is the weather features working. Bias stays inside ±0.8 gCO₂/kWh at every
horizon, against climatology's +19 to +31.

**G2 does not beat `ESO_final`, and that comparison is not evidence either way.**
The stored ESO forecast is their final near-term value, not a 48-hour-ahead one
(M4 §2), so this pits G2 at 48 hours against the ESO at something much shorter.
The live scoreboard is the only place that question gets a fair answer, and it
is now accumulating the data to answer it.

---

## 2. What the model is allowed to see

39 features, every one expressed relative to the **issue time** rather than the
target. "Intensity 24 hours before the target" is not a legal feature at a
48-hour horizon; "24 hours before issue time" is.

| Group | Features |
|---|---|
| Horizon | `horizon_periods` — one model, all 96 horizons |
| Calendar (of target) | sin/cos period-of-day, sin/cos day-of-year, day-of-week, is_weekend |
| Intensity lags (of issue time) | 0 h, 24 h, 48 h, 168 h |
| Rolling | mean and standard deviation over 24 h and 168 h |
| Mix (at issue time) | wind, solar, low-carbon share; 24 h mean wind share |
| Weather (for target) | wind at 100 m, temperature, radiation across 5 locations |
| Weather ramps | target-hour wind minus trailing 24 h mean, per location |

Three sources are barred and the bans are enforced rather than remembered:
`stg_om_archive` (reanalysis actuals), `fct_demand_current` (latest revision
rather than the vintage known at issue time), and `intensity.index` (which
encodes the publication year, M2 B03/B04). `assert_no_banned_columns` fails on
any frame carrying the target or the ESO forecast under any name.

---

## 3. The bug that would have invalidated everything

The first training run returned **"insufficient data to train"** on eight years
of loaded history.

The cause was the reconstructed-vintage problem the design predicted at §8.3 and
that had never been implemented. Every backfilled row carries
`knowable_at_utc` = the instant the *backfill* ran — today. Applied literally,
the knowability guard correctly concluded that nothing in history was knowable
at any historical origin, and rejected all 2,192 of them.

**The guard was right.** The data was mislabelled.

Fixed by reconstructing knowability as `sp_start_utc + RECONSTRUCTED_LAG` for
backfilled rows while keeping the true fetch time for rows observed live, with
`knowable_is_reconstructed` still distinguishing them — because results built on
the two are reported separately and never pooled.

The lag is 24 hours, provisional, matching the maturity threshold so the two
cannot disagree. It is deliberately generous: assuming a value took *longer* to
publish than it did withholds information from the model rather than granting
it, which is the safe direction for an error to run.

### 3.1 A diagnosis that blamed the data

The first attempt to debug this failed because `build_dataset` wrapped feature
construction in `except Exception: continue`. Every origin failed, silently, and
the run reported *"insufficient data to train"* — a statement about the data,
when the fault was in the code.

Narrowed to `except LeakageError`, which is the one genuinely expected failure
(an origin early enough that nothing was observable). Anything else now
propagates. This is the second time in this project that broad exception
handling turned a fixable error into a misleading conclusion, after a shell
wrapper's trailing `echo` masked the backfill's non-zero exit and led to a
retracted finding.

---

## 4. A test suite that could write to production

During M5 verification the suite was run with `GRIDCAST_DATABASE_URL` pointing
at Neon. Four `test-probe` forecasts landed in the **live evidential register**.

Contained: no seal existed to invalidate, the landing fixtures cleaned up after
themselves, and nothing reached the marts. The four rows were removed as the
database owner — the only mutation of the register this project will make, and
justified narrowly because they were never forecasts.

The register tests cannot clean up after themselves *by design*: they prove the
application role cannot delete what it inserts, which is exactly the guarantee
being tested.

`tests/conftest.py` now **refuses to run** when `GRIDCAST_DATABASE_URL` points
anywhere but localhost. It aborts collection rather than skipping — a skip reads
as "the tests passed", and the person who pointed at production needs to know
immediately.

---

## 5. D-7 resolved: holidays are not weekends

Measured over 2019–2026, mean daytime demand (08:00–18:00 local):

| Day type | Days | Mean demand |
|---|---|---|
| Ordinary weekday | 1,963 | 30,820 MW |
| **Bank holiday** | 21 | **29,753 MW** (−3.5%) |
| Weekend | 789 | 26,400 MW (−14.3%) |

The intuition that a bank holiday behaves like a Sunday is wrong: the weekend
effect is **four times larger**, and `is_weekend` already captures it.

**Decision:** a holiday calendar is a minor feature, not a missing one. It fires
on under 1% of days for a 3.5% signal, against a weekend effect already in the
model. It is worth adding for correctness at Christmas and is not expected to
move the aggregate — which is a more useful thing to know before building it
than after.

Stated limitation: the sample covers the winter cluster (Christmas, Boxing Day,
New Year) rather than all eight GB bank holidays, so it measures the strongest
cases. A full calendar would likely show a smaller average effect, not a larger
one.

---

## 6. Model artefact

`models/G2_gbm_v1.joblib` — 5.4 MB (point model plus four quantile models),
committed alongside
`G2_gbm_v1.json` recording the training window, embargo, feature list, MASE
scale and holdout results.

Committed rather than stored externally: small, versions naturally with the code
that produced it, and the serving API never loads it — forecasts are written to
the register by an offline job and the API only reads rows.

---

## 7. Intervals: broken, then fixed, and still not perfect

The first quantile models were fitted with the pinball loss and used directly.
Their **nominal 80% intervals achieved 59–63% empirical coverage** — overconfident
by nearly twenty points.

That is exactly the failure `metrics.coverage` was written to name: *an 80%
interval containing 62% of actuals is a broken product, not a small miss*. It
means every stated uncertainty understates the risk, and a user shifting load on
it is taking more than they were told.

Two causes. Quantile GBMs are reliably overconfident out of sample, and the
early-stopping validation split cannot detect it because it is drawn at random
from a time series and so is not out of sample at all.

**Conformalized quantile regression** fixes it with a distribution-free
guarantee. Quantiles are fitted on the earlier 75% of the training window; the
later 25% is held back purely to measure how far outside its own interval
reality actually falls. The interval is then widened by that measured amount —
9.4 gCO₂/kWh each side at 80%, 10.1 at 95%. The calibration split is
chronological, like every other split here; a random one would calibrate against
days the model had effectively seen.

| Interval | Before | After | Nominal |
|---|---|---|---|
| 80% | 59–63% | **74.9%** | 80% |
| 95% | — | **93.9%** | 95% |

By horizon, 80% coverage: H1 79.9%, H2 73.8%, H3 75.4%, H4 74.3%.

**It is still 5 points short at 80%, and that is reported rather than tuned
away.** The residual is distribution shift: conformal guarantees coverage under
exchangeability, and a decarbonising grid violates it — the calibration data is
2025, the test is 2026. Further tuning against the test set would buy a better
number and a worse model.

The consequence is a labelling obligation, not a licence. The interval is
published as nominal-80 with its **measured** coverage shown beside it, which is
the same rule NFR-9 applies to accuracy figures.

The live path applies the same widening. Serving raw quantiles while reporting
calibrated coverage in the model card would be worse than shipping no intervals.

---

## 8. G2 is live as a challenger

Issuing every pipeline run alongside the champion, the persistence baseline and
the ESO benchmark — all at one shared `run_at`, so identical periods hold by
construction.

```
B1_seasonal_naive_q_v1    96 forecast(s)
B0_persistence_v1         96 forecast(s)
ESO_published             94 forecast(s)
G2_gbm_v1                 96 forecast(s)
```

Interval widths at 80%, one issue time:

| Horizon | G2 | B1 seasonal naive |
|---|---|---|
| 1 | 76 | 137 |
| 48 | 93 | 137 |
| 96 | 96 | 174 |

G2's intervals are roughly 45% narrower than the incumbent's *and* better
calibrated, and they widen with horizon as they should.

**G2 is a challenger, not the champion.** It forecasts and is scored; it does not
serve. Promotion is decided only by the rule in `PREREGISTRATION.md` — 1,440
scored points per horizon group, Diebold–Mariano with a Wilcoxon confirmation,
Benjamini–Hochberg across four groups. A model that looks better offline does not
get promoted for looking better offline.

The features are built by the same function used in training, at the same
embargo. A separate serving-time feature path is the classic way a model that
scored well offline quietly degrades in production.

If the artefact is missing the run prints so and continues: a challenger failing
must never stop the champion and the benchmark being recorded, because a hole in
their series cannot be refilled once the moment has passed.

---

## 9. What M6 has not done

| Item | State |
|---|---|
| **Error segmentation** | Not done. Accuracy by season, wind share, demand level and ramp magnitude belongs with drift monitoring at M7 |
| **D-2 weather alignment** | Still `step_hold`, provisional. Now measurable by retraining G2 under each and comparing |
| **Landing retention** | 41 MB headroom at ~0.15 MB/day, roughly nine months |

The milestone is reported partially complete rather than adjusted to fit what
was finished. Registering G2 live is the highest-value remaining item: it starts
the pre-registered comparison against the incumbent, and every day it is not
running is a day of live evidence not collected.

---

## 10. Next

Finish M6 — quantile intervals, coverage validation, segmentation, and G2 issuing
live as a challenger under `PREREGISTRATION.md`. Then M7 for the champion/
challenger decision and drift monitoring.
