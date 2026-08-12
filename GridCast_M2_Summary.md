# GridCast — M2 Summary: Data-Quality Audit

**Milestone:** M2
**Date:** 2026-08-12
**Status:** Complete for the loaded sources. Three queries pending the weather
and price backfill (§7), listed rather than quietly omitted.

---

## 1. Exit criterion

> Committed queries quantifying actuals lag, revision behaviour, clock-change
> handling, missing periods and mix-sum tolerance. Deferred design decisions
> resolved.

Ten committed queries in `audit/`, run by `python -m gridcast.audit`. Every
figure below comes from one of them. Nothing is quoted from a notebook cell that
no longer exists.

---

## 2. Findings that change the design

### 2.1 The warehouse would not have been reproducible (B02)

**The most consequential finding of the milestone, and it is in GridCast's code
rather than in the data.**

The Carbon Intensity API returned settlement period `2021-04-19 19:00` **twice
within a single response**, with conflicting values:

| | actual | forecast |
|---|---|---|
| First copy | 303 | 294 |
| Second copy | 295 | 289 |

Insert-if-changed correctly wrote both — that is what an append-only log is for.
But both were written in one transaction, so they share a `fetched_at_utc`.

The design specified that staging resolves to a current value with
`DISTINCT ON (sp_start_utc) ... ORDER BY fetched_at_utc DESC`. **With that tie,
there is no defined winner.** Postgres may return either row, and may return a
different one on a later build. Two otherwise identical warehouse builds could
disagree about a published figure — breaking reproducibility (NFR-3) silently,
in a way no test comparing a build to itself would ever catch.

**Resolution:** every `DISTINCT ON` in the project now orders by
`fetched_at_utc DESC, landing_id DESC`. `landing_id` is a bigserial: unique,
monotonic, never tied. The design document is amended at §5.

Two rows in 144,763 is not a large problem. An unreproducible warehouse is.

A second period, `2019-12-17 23:30`, carries identical numbers with a different
`index` band five seconds apart — which leads directly to the next finding.

### 2.2 `intensity.index` must never be a feature or a label (B03, B04)

The API returns a categorical band with every value. It is tempting as a
ready-made classification target. It is unusable, and the bands prove it:

| band | min actual | max actual |
|---|---|---|
| very low | 0 | 79 |
| low | 25 | 179 |
| moderate | 90 | 279 |
| high | 170 | 379 |
| very high | 230 | 447 |

If the band were a function of the value, those ranges would be disjoint. They
overlap because the ESO recalibrates thresholds as the grid decarbonises. B04
shows the drift directly — the moderate/high boundary, by year:

| 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| 260 | 230 | 221 | 210 | 200 | 190 | 180 | 170 | 170 |

A clean staircase: roughly 10 gCO₂/kWh per year, 100 g over eight years.

**Consequences, both mandatory.** `index` may not be a feature — it encodes the
publication year as much as the intensity, so a model using it would partly be
learning what year it is, then be asked to predict a year it has never seen. And
it may not be a target — a classifier trained on it fits a moving definition, and
its accuracy would drift with the bands rather than with the grid.

If a banded presentation is wanted, GridCast derives its own thresholds and
states them.

### 2.3 Some periods can never be scored (B01)

Two distinct failures, neither visible as a gap because the row exists.

**625 periods have a permanently null actual**, concentrated in 2019 (308,
1.76% of the year). None since 2024 — upstream quality improved.

**43 periods have a null ESO forecast**, in exactly two blocks:

```
2025-01-12 23:00 -> 2025-01-13 11:30   26 periods
2025-08-10 23:00 -> 2025-08-11 07:00   17 periods
```

The second is the subtle one. SRS FR-20 requires every model scored on
*identical* periods. A period where the ESO forecast is absent must therefore be
excluded from the comparison for **all** models — otherwise GridCast would be
credited with periods its benchmark never had the chance to attempt. That is
precisely the quiet advantage this project exists not to take.

**Consequence for M5:** a period older than the maturity threshold whose actual
is still null is *permanently unscoreable*, not pending. The scoring job must
retire it rather than wait forever.

### 2.4 Five gaps are the ESO's, not ours (A01, A02)

179 settlement periods are missing across 2021, 2023 and 2024. The backfill
covered every window contiguously, so the question is whose gap it is.

Verified by re-requesting each window directly from the API: **all five are
upstream and permanent.** The October 2023 response jumps from
`2023-10-20T21:30Z` straight to `2023-10-22T19:30Z`.

| From | To | Periods | Hours |
|---|---|---|---|
| 2021-04-19 17:00 | 2021-04-19 17:00 | 1 | 0.5 |
| 2021-04-19 22:00 | 2021-04-19 22:30 | 2 | 1.0 |
| 2021-12-26 15:00 | 2021-12-27 17:30 | 54 | 27.0 |
| 2023-10-20 22:00 | 2023-10-22 19:00 | 91 | 45.5 |
| 2024-06-11 23:00 | 2024-06-12 14:00 | 31 | 15.5 |

**Consequence for gap-fill:** these must be recorded as permanently absent. The
daily deep-heal currently re-requests all five windows every night, against a
free API, with no terminating condition. That is an impolite loop and it is
GridCast's fault, not the ESO's. Coverage of 99.88% is the true ceiling for
historical data, and NFR-1's 99% target is met against it.

---

## 3. Deferred decisions resolved

### D-6 — mix-sum tolerance: **±0.5 percentage points**

Measured across 140,398 periods:

| sum | periods | share |
|---|---|---|
| 99.7 | 4 | 0.003% |
| 99.8 | 2,167 | 1.54% |
| 99.9 | 27,348 | 19.48% |
| **100.0** | **87,439** | **62.28%** |
| 100.1 | 22,020 | 15.68% |
| 100.2 | 1,413 | 1.01% |
| 100.3 | 7 | 0.005% |

The spread is cumulative rounding across nine one-decimal figures, not error.
Observed range is ±0.3, so the tolerance is set at **±0.5**: loose enough never
to fire on rounding, tight enough that a fuel category with more than half a
percentage point of share disappearing would breach it.

Stated limitation: a fuel below 0.5% share vanishing would not trip this test.
C02 covers that case instead.

### D-6 (second part) — the fuel set is stable

Nine fuels — biomass, coal, gas, hydro, imports, nuclear, other, solar, wind —
in every one of the nine years. The long-format storage decision holds, and the
test is cheap insurance rather than a live concern.

### D-1 (partial) — demand publishes at period end + 30 minutes

Measured from `publishTime`, across 18,960 observations:

| min | p50 | p95 | p99 | max |
|---|---|---|---|---|
| 30 min | 30 min | 30 min | 30 min | 312 min |

Zero negative lags, confirming the field is read correctly. Exactly one vintage
per period so far — `publish_time_utc` stays in the grain regardless, because
INDO is explicitly an *initial* outturn and later settlement runs are expected.

**Why only demand.** This is the one publication lag measurable from backfilled
data, and only because Elexon states `publishTime` inside the payload. Asking
backfilled Carbon Intensity data when its actuals appeared would answer "all of
them, today" — confidently and wrongly, because a backfilled row's
`fetched_at_utc` is when the backfill ran. That asymmetry is exactly why design
§8.3 keeps backtest and live results in separate columns and never pools them.
The ESO actuals lag must be observed forward, and M5 cannot open its scoreboard
until it has been.

---

## 4. A silent failure found inside the audit tool

The first run of `python -m gridcast.audit` reported clean, plausible results
across every query. They were wrong.

`connect(readonly=True)` resolves to `GRIDCAST_READONLY_DATABASE_URL`, which on
this machine points at a local development database. The audit answered from
**239 local rows instead of 144,763 in production** — and every number looked
reasonable. The error surfaced only because the same queries had been run
through `psql` against Neon minutes earlier and the answers disagreed.

Fixed by passing the pipeline URL explicitly and opening a read-only *session*
rather than using the read-only *role*. The runner now prints the host it
connected to before running anything.

An analysis tool reading a different database than it was told to is the exact
failure this directory exists to catch. It is recorded here because it is a
better argument for cross-checking than any amount of assertion would be.

---

## 5. Clock changes

`dim_settlement_period` was verified at M0: seven fully covered years, each with
exactly one 46-period day and one 50-period day, and no gap other than 30
minutes anywhere on the UTC axis. Re-verified against landed data here — the
coverage query (A01) reconciles landing to the spine year by year, and the only
discrepancies are the five upstream outages in §2.4.

---

## 6. Committed queries

| Query | Answers |
|---|---|
| `A01_coverage_by_year` | Spine-to-landing reconciliation per year |
| `A02_structural_gaps` | Whether each gap is ours or upstream |
| `B01_unscoreable_periods` | Null actuals and null ESO forecasts |
| `B02_conflicting_versions` | Periods reported more than one way, and ordering ambiguity |
| `B03_index_bands_are_not_stable` | Whether `index` is a function of the value |
| `B04_index_band_drift` | The band boundary moving year on year |
| `C01_mix_sum_tolerance` | The distribution the D-6 tolerance is set from |
| `C02_fuel_set_stability` | Whether the fuel categories change |
| `D01_demand_publication_lag` | Demand lag from `publishTime` |
| `E01_weather_location_correlation` | D-3, pending the weather backfill |

---

## 7. What remains open

| Decision | Status |
|---|---|
| **D-1** ESO actuals lag | Requires forward observation. Not obtainable from backfilled data (§3), and M5 must not open its scoreboard before it exists |
| **D-2** weather half-hourly alignment | Needs the weather backfill, then a backtest error comparison |
| **D-3** weather locations | Query committed (E01), pending `lnd_om_vintage` |
| **D-7** holiday calendar | Needs the full demand backfill for a like-for-like weekday comparison |

The backfill for demand, price and weather was still running when this milestone
closed. The three pending queries are listed by the runner as pending rather than
skipped silently — an audit that quietly omits what it could not check is worse
than one that says so, because the omission reads as a clean result.

---

## 8. Next milestone

**M3 — Warehouse.** dbt incremental marts and snapshots, with the M2 findings
built in from the start rather than retrofitted: the `landing_id` tiebreak in
every `DISTINCT ON`, `index` excluded from every model, a permanently-absent
register for the five upstream gaps so the deep-heal stops chasing them, and the
±0.5 mix-sum tolerance as a committed test.
