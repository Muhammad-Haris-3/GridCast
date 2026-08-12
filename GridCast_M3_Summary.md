# GridCast — M3 Summary: The Warehouse

**Milestone:** M3
**Date:** 2026-08-12
**Status:** Complete. 70/70 dbt build green against Neon, incremental verified.

---

## 1. Exit criterion

> dbt incremental marts + snapshots + full test suite green; retention policy
> applied.

Met. 4 incremental models, 1 seed, 1 snapshot, 3 tables, 10 views, 51 tests —
`PASS=70 WARN=0 ERROR=0`. Retention policy applied under a hard constraint the
build discovered for itself (§4).

---

## 2. What was built

| Layer | Models |
|---|---|
| Staging (views) | `stg_ci_intensity`, `stg_ci_genmix`, `stg_ci_regional`, `stg_ex_demand`, `stg_ex_price`, `stg_om_archive`, `stg_om_forecast`, `stg_om_vintage` |
| Marts | `dim_settlement_period`, `dim_region`, `fct_intensity_period`, `fct_generation_mix`, `fct_mix_wide`, `fct_demand_period`, `fct_demand_current`, `fct_weather_period`, `mart_absent_periods` |
| Snapshot | `snp_intensity_actual` |
| Seed | `known_absent_windows` |

Warehouse contents: 144,761 settlement periods, 144,192 demand vintages,
173,088 weather-period rows, 179 verified-absent periods.

### 2.1 The M2 findings are structural, not remembered

Each of M2's findings became a mechanism rather than a note.

| M2 finding | How M3 enforces it |
|---|---|
| Ordering ambiguity (B02) | `latest_landing_row` macro — every staging model resolves through it, so a new model cannot forget the `landing_id` tiebreak |
| `index` is unusable (B03/B04) | Dropped at staging, plus `assert_no_intensity_index_downstream` scanning `information_schema` so it cannot reappear |
| Unscoreable periods (B01) | `is_permanently_unscoreable` and `eso_benchmark_missing` columns on the fact |
| Upstream gaps (A02) | `known_absent_windows` seed → `mart_absent_periods` → gap-fill exclusion |
| Mix tolerance ±0.5 (C01) | `assert_mix_sums_to_100` |
| Fuel set stability (C02) | `assert_fuel_set_is_stable` |

Verified against the warehouse: 625 permanently unscoreable, 43 without an ESO
benchmark, 179 known absent — matching M2 exactly.

### 2.2 Gap-fill stops chasing the ESO's holes

With `mart_absent_periods` wired into `find_gaps`, a full-history sweep now
reports cleanly:

```
python -m gridcast.gapfill --source ci_intensity --lookback 3650 --detect-only
  ci_intensity   no gaps
```

Before this, the daily deep-heal would have re-requested five windows every
night, for ever, against a free public API with no terminating condition.

---

## 3. Two defects the build found in itself

### 3.1 The maturity rule made all of history unscoreable

The first full build produced `is_matured = 0` across all 144,761 periods.

The design measured stability from `first_seen_at_utc`. For a backfilled row
that is when the *backfill* ran — an hour ago — not when the value settled. So
every period failed the stability half of the test, and the scoring job at M5
would have found nothing to score.

Worse, it made `assert_matured_periods_have_an_actual` pass **vacuously**: with
no matured rows, a test filtered on matured rows returns nothing and reports
success. That is the same failure mode checked for at M0 with the clock-change
test, and it recurred here undetected until the counts were inspected directly.

**Fixed** by expressing what the window is actually for. The point is not to
score a value that is still being revised: if only one version has ever been
seen there is nothing to wait for; if it has changed, the latest must hold for
`stability_hours`. Result: 144,617 matured, 143,949 matured *and* comparable —
the real scoreable universe for backtesting.

### 3.2 An incremental model that built once and failed thereafter

`fct_weather_period` filtered on `hour_start_utc`, which is not one of its own
columns. The subquery `select max(hour_start_utc) from {{ this }}` therefore
resolved against the outer query, and Postgres rejected an aggregate in `WHERE`.

It passed the first build because the filter is only emitted when
`is_incremental()` is true. A model that builds clean once and fails on every
run afterwards is a failure that arrives at 04:00 in a scheduled pipeline rather
than in development.

**Fixed** to filter on `sp_start_utc`, which the model does emit. The other
three incremental models were checked for the same mistake; none had it.

---

## 4. The retention policy, decided by hitting the wall

M2 projected storage from a measured 322 bytes per row and excluded regional
intensity on that basis — correctly, as it would have been ~880 MB. The
projection for everything else was too low, and the build said so without
ambiguity:

```
could not extend file because project size limit (512 MB) has been exceeded
```

Measured at that point — 489 MB of 512:

| Object | Rows | Size |
|---|---|---|
| `marts.fct_generation_mix` | 1,263,582 | **115 MB** |
| `landing.lnd_ci_genmix` | 135,235 | 99 MB |
| `landing.lnd_om_vintage` | 172,944 | 82 MB |
| `landing.lnd_ex_demand` | 136,484 | 60 MB |
| `landing.lnd_ci_intensity` | 144,763 | 44 MB |

The largest object in the warehouse was the long-format generation mix — nine
rows per settlement period — and **nothing reads it in that shape**. Every
consumer goes through `fct_mix_wide`.

**Decision: invert the materialisation.** `fct_generation_mix` becomes a view,
`fct_mix_wide` becomes the table. 140k stored rows instead of 1.26M, for the
same information. Long format remains authoritative — every mix test still runs
against it, and a fuel appearing or disappearing still shows up as a row count
rather than being absorbed by fixed columns. What changed is which model is
*stored*, not which is *true*.

Result: **489 MB → 416 MB**, with 96 MB of headroom, and the full build green.

The honest note: paying to store a shape that is always consumed in another
shape was the most expensive habit in the warehouse, and it took running out of
disk to notice. The projection in M2 was not wrong in method — it was measured
on the wrong object.

---

## 5. Test suite

51 data tests, all green. The ones carrying real weight:

| Test | Guards against |
|---|---|
| `assert_mix_sums_to_100` | A fuel category silently vanishing; tolerance ±0.5 from M2's measured distribution |
| `assert_fuel_set_is_stable` | The sub-0.5% case the tolerance cannot catch |
| `assert_matured_periods_have_an_actual` | The silent-freeze failure — now non-vacuous |
| `assert_no_intensity_index_downstream` | `index` reappearing in any model, scanned via `information_schema` |
| `assert_periods_per_local_day` | Clock-change corruption |
| `assert_two_clock_changes_per_year` | An over-eager clock-change fix |
| `assert_spine_has_no_gaps` | A timezone conversion applied to the time axis itself |
| `dim_region.is_scoreable = false` | Regional accuracy being rendered anywhere |

---

## 6. Still provisional

| Decision | State |
|---|---|
| **D-1** ESO actuals lag | `maturity_hours = 24`, `stability_hours = 6` remain placeholders. Requires forward observation — M5 must not open its scoreboard before it exists |
| **D-2** weather alignment | `weather_alignment = step_hold`, provisional. Step-hold invents nothing; interpolation manufactures a value nobody published. Decided by measured backtest error at M4 |
| **D-3** weather locations | `lnd_om_vintage` now holds 190,224 rows, so `E01` can finally run |
| **D-7** holiday calendar | Demand backfill now complete (144,192 rows), so the comparison is possible |

Two of these became answerable during this milestone because the backfill
finished. They are M4's opening work, not M3's omission.

---

## 7. Next milestone

**M4 — Baselines and the backtesting harness.** Rolling-origin backtesting with
the embargo, all baselines plus the ESO benchmark scored on identical periods,
and the leakage controls tested rather than asserted. The scoreable universe is
143,949 periods with both an actual and an ESO forecast.
