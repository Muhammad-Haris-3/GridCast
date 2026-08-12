# GridCast — Design Phase Document v1.0

**Companion to:** `GridCast_SRS_v1.0.md`
**Date:** 2026-08-12
**Status:** Approved for M0
**Purpose:** Translate the SRS requirements into an implementable design — every
table, its grain, its incremental strategy, its tests, and why it is shaped that
way. Plus the mechanics that the SRS only promises: append-only forecasting,
point-in-time features, and the promotion rule.

This document is written to be executable: implementation should be typing, not
deciding.

---

## 1. Layer architecture

Four layers, each with exactly one job.

| Layer | Schema | Materialisation | Written by | Job |
|---|---|---|---|---|
| **Landing** | `landing` | Tables, append-only | Python ingestion | Source fidelity + revision history |
| **Staging** | `staging` | Views | dbt | Type, rename, UTC-normalise, resolve to current value |
| **Marts** | `marts` | Incremental tables | dbt | The dimensional model everything queries |
| **Register** | `register` | Tables, append-only | Python forecast/score jobs | Forecasts issued and forecasts scored |

### 1.1 Why the register is not a dbt layer

dbt models are *rebuildable by definition* — that is their virtue and, here,
their disqualification. The forecast register's entire value is that it cannot
be rebuilt: a forecast is evidence of what was believed at a moment in time. If
`dbt build` could regenerate it, the integrity guarantee (FR-19) would be
theatre.

The register is therefore written once by the job that issues the forecast,
declared to dbt as a **source**, and never as a model. dbt may read it. dbt may
never write it.

### 1.2 Why landing is append-only

Every ingestion writes a row only when the payload for a key **differs from the
last payload stored for that key** (§4.2). Two consequences fall out for free:

1. **Idempotency (FR-3).** Re-running an unchanged window writes zero rows.
2. **Revision history (FR-9).** When the ESO revises an actual, the change is a
   new row, not an overwrite. The revision record is a by-product of the
   idempotency mechanism rather than a separate system.

### 1.3 Why marts are incremental

Rebuilding eight years of settlement periods on every run wastes free-tier
compute to reproduce rows that cannot have changed. Marts process only periods
touched since the last build — but with a deliberate lookback window, because
actuals *do* arrive late (§6.2).

---

## 2. The time design

This is the decision that everything else depends on.

### 2.1 The primary key is `sp_start_utc`

Every half-hourly table is keyed on `sp_start_utc timestamptz` — the settlement
period's start instant in UTC, taken directly from the API's `from` field.

**Rejected alternative:** `(settlement_date, settlement_period)`, the GB industry
key. It is the natural key in the domain, and it is a trap. On clock-change days
a settlement date has 46 or 50 periods, period numbers do not map to a fixed
wall-clock offset, and any arithmetic of the form `period + 48 = same time
tomorrow` silently produces wrong answers twice a year. Those two days are not
edge cases to be swept up later; they are the days a reviewer will check.

`settlement_date` and `settlement_period` are carried as **attributes** for
domain readability and for joining to Elexon, never as keys.

### 2.2 Local time is presentation only

No model stores local time. `Europe/London` is applied at the presentation
boundary — the API response serialiser and the frontend. A `is_bst` flag on
`dim_settlement_period` exists for analysis of clock effects, not for joining.

### 2.3 `dim_settlement_period` is a generated spine

Generated from 2018-05-09T00:00Z to `now() + 60 days`, one row per half hour,
independent of any source. It is the spine that makes absence detectable: a
missing period is a spine row with no fact, which is queryable. Without a spine,
missing data is invisible — you cannot `WHERE` your way to rows that were never
inserted.

---

## 3. Naming conventions

| Pattern | Meaning |
|---|---|
| `lnd_<source>_<entity>` | Landing table, append-only |
| `stg_<source>_<entity>` | Staging view, resolves to current value |
| `dim_<entity>` | Dimension — a thing |
| `fct_<process>` | Fact — an event or measurement |
| `snp_<entity>` | dbt snapshot — typed SCD2 revision history |
| `reg_<entity>` | Register table — append-only, Python-written |
| `*_utc` | A `timestamptz` anchored in UTC. Any timestamp column without this suffix is a bug |
| `*_gco2_kwh` | Carbon intensity. Unit in the name, always |
| `*_mw` / `*_gbp_mwh` | Power / price. Unit in the name, always |
| `knowable_at_utc` | The instant this value first became available to us. The leakage guard |
| `is_<condition>` | Boolean |

Units live in column names because a unit mismatch that goes unnoticed is the
most expensive class of silent error in analytics work.

---

## 4. Ingestion design

### 4.1 Source jobs

One job per source, independently runnable and independently failing (R-3).

| Job | Source | Window per call | Cadence | Landing table |
|---|---|---|---|---|
| `ingest_intensity` | `/intensity/{from}/{to}` | ≤ 14 days | 30 min | `lnd_ci_intensity` |
| `ingest_genmix` | `/generation/{from}/{to}` | ≤ 14 days | 30 min | `lnd_ci_genmix` |
| `ingest_regional` | `/regional/intensity/{from}/{to}` | ≤ 14 days | 6 h | `lnd_ci_regional` |
| `ingest_demand` | `/demand/outturn` | ≤ 7 days | 30 min | `lnd_ex_demand` |
| `ingest_price` | `/balancing/pricing/market-index` | ≤ 7 days | 6 h | `lnd_ex_price` |
| `ingest_weather_actual` | Open-Meteo archive | ≤ 90 days | daily | `lnd_om_archive` |
| `ingest_weather_forecast` | Open-Meteo forecast | 48 h ahead | 30 min | `lnd_om_forecast` |
| `ingest_weather_vintage` | Open-Meteo historical-forecast | ≤ 30 days | backfill only | `lnd_om_vintage` |

`ingest_weather_vintage` exists solely to obtain **weather forecasts as they were
issued**, which is what training on history requires (§8.3). Reanalysis actuals
would leak perfect weather knowledge into a model that will never have it in
production. Endpoint availability is confirmed in M1 (**D-4**).

### 4.2 The insert-if-changed mechanism

Every landing table has the same shape:

| Column | Type | Meaning |
|---|---|---|
| `natural_key` | varies per table | The source's own identity for the record |
| `payload` | `jsonb` | The record exactly as returned |
| `payload_hash` | `bytea` | `sha256(payload::text)` |
| `fetched_at_utc` | `timestamptz` | When *we* received it |
| `run_id` | `uuid` | The run that wrote it |

Write logic, per record:

```sql
INSERT INTO landing.lnd_ci_intensity (sp_start_utc, payload, payload_hash, fetched_at_utc, run_id)
SELECT :sp, :payload, :hash, :now, :run_id
WHERE NOT EXISTS (
  SELECT 1 FROM landing.lnd_ci_intensity
  WHERE sp_start_utc = :sp
  ORDER BY fetched_at_utc DESC LIMIT 1
  -- guarded by a lateral latest-row lookup comparing payload_hash = :hash
);
```

Implemented as a single statement with `DISTINCT ON` over the latest row per
key. The practical effect: a re-run of an identical window is a no-op, an actual
arriving where there was `null` is one new row, and a revision is one new row.

**`fetched_at_utc` is the leakage boundary.** Whatever the publisher claims about
its own timing, we could not have known a value before we held it. This is the
strictest available definition and the one the feature builder uses (§8.2).

### 4.3 Backfill

`backfill.yml` is a `workflow_dispatch` workflow taking `source`, `date_from`,
`date_to`. It chunks the range to each source's window limit, sleeps between
calls to stay a polite client, writes through the same insert-if-changed path,
and is therefore safely re-runnable after a failure at any point.

Backfill is not a special code path. It is the normal ingestion function called
with a different window — which is why a partial backfill can simply be run
again rather than reasoned about.

### 4.4 Gap detection and self-healing (FR-4)

A `gapfill` step runs at the end of every pipeline execution:

```
missing := SELECT sp_start_utc FROM marts.dim_settlement_period d
           WHERE d.sp_start_utc BETWEEN now() - 14 days AND now()
             AND NOT EXISTS (SELECT 1 FROM staging.stg_ci_intensity s
                             WHERE s.sp_start_utc = d.sp_start_utc)
```

Contiguous missing periods are coalesced into windows and re-fetched. A period
present with `actual IS NULL` is **not** a gap — it is pending, and §6.2's
maturity rule governs it instead. Conflating the two would cause the system to
hammer the API forever for values that do not exist yet.

### 4.5 The run log

`landing.run_log` — one row per (run, source):

`run_id`, `source`, `job`, `window_from_utc`, `window_to_utc`, `started_at_utc`,
`finished_at_utc`, `http_calls`, `rows_read`, `rows_written`, `status`
(`success` / `partial` / `failed`), `error_class`, `error_detail`.

`rows_read` and `rows_written` differing is the normal, healthy state — it is the
insert-if-changed mechanism working. A run where `rows_written` suddenly equals
`rows_read` across a long window means every payload changed, which means either
a mass revision or a schema change upstream. That ratio is a monitored signal,
not just a log field.

---

## 5. Staging models

All views in `staging`. Each reads exactly one landing table. **Staging never
joins** — the same rule as the author's previous warehouse work, for the same
reason: when a mart is wrong, the fault must be in the mart or in one
identifiable staging model.

Every staging model resolves the append-only landing table to the **current
value per key**:

```sql
SELECT DISTINCT ON (sp_start_utc) ...
FROM landing.lnd_ci_intensity
ORDER BY sp_start_utc, fetched_at_utc DESC
```

### 5.1 `stg_ci_intensity`
**Grain:** one `sp_start_utc`.

| Column | Derivation |
|---|---|
| `sp_start_utc` | `(payload->>'from')::timestamptz` |
| `actual_gco2_kwh` | `(payload->'intensity'->>'actual')::int` — nullable by design |
| `eso_forecast_gco2_kwh` | `(payload->'intensity'->>'forecast')::int` |
| `intensity_index` | `payload->'intensity'->>'index'` |
| `knowable_at_utc` | `fetched_at_utc` of the winning row |
| `first_seen_at_utc` | `min(fetched_at_utc)` over the key |

**Tests:** unique + not_null on `sp_start_utc`; `actual_gco2_kwh` between 0 and
800 when not null; `eso_forecast_gco2_kwh` not_null; `intensity_index` in the
six accepted values.

The upper bound of 800 is deliberately loose. A tight bound fitted to observed
history would fail on a legitimately extreme day and teach us to ignore the test.

### 5.2 `stg_ci_genmix`
**Grain:** (`sp_start_utc`, `fuel`). Long format.

Nine fuels unnested from the payload array into rows. Storing nine columns would
make "share of low-carbon generation" a nine-term expression that has to be
edited whenever the ESO adds a fuel category.

**Tests:** compound uniqueness; `perc` between 0 and 100; **singular test**:
per-period sum of `perc` within 100 ± tolerance, tolerance set by M2 (**D-6**).

### 5.3 `stg_ci_regional`
**Grain:** (`sp_start_utc`, `region_id`).

Carries `forecast_gco2_kwh` and **no actual column at all** — not a nullable one.
A nullable `actual` here would invite a future join that quietly produces an
all-null accuracy table. The absence is enforced by schema, not by discipline
(SRS §6.4, NFR-9, R-6).

**Tests:** compound uniqueness; `region_id` in 1–17; relationship to `dim_region`.

### 5.4 `stg_ex_demand`
**Grain:** (`sp_start_utc`, `publish_time_utc`) — **vintage is in the grain.**

This staging model is the one exception to "resolve to current value": it keeps
every vintage, because point-in-time feature construction needs to ask "what did
we believe demand was at 14:00 yesterday" and the latest revision cannot answer
that.

| Column | Derivation |
|---|---|
| `sp_start_utc` | `(payload->>'startTime')::timestamptz` |
| `publish_time_utc` | `(payload->>'publishTime')::timestamptz` |
| `demand_indo_mw` | `(payload->>'initialDemandOutturn')::int` |
| `demand_itsdo_mw` | `(payload->>'initialTransmissionSystemDemandOutturn')::int` |
| `settlement_date`, `settlement_period` | passthrough attributes |
| `knowable_at_utc` | `fetched_at_utc` |

**Tests:** compound uniqueness on the grain; `demand_indo_mw` between 10,000 and
70,000; `publish_time_utc >= sp_start_utc` (singular test — a demand outturn
published before the period it measures would indicate a parsing error).

### 5.5 `stg_ex_price`, `stg_om_archive`, `stg_om_forecast`, `stg_om_vintage`

| Model | Grain | Notes |
|---|---|---|
| `stg_ex_price` | (`sp_start_utc`, `data_provider`) | Multiple index providers exist; provider stays in the grain rather than being averaged away at staging |
| `stg_om_archive` | (`location_id`, `hour_start_utc`) | Reanalysis actuals. Used for descriptive analysis and **never for training features** |
| `stg_om_forecast` | (`location_id`, `hour_start_utc`, `issued_at_utc`) | Forward forecasts, vintage in grain |
| `stg_om_vintage` | (`location_id`, `hour_start_utc`, `issued_at_utc`) | Historical forecasts as issued, for training on backfilled history |

`stg_om_archive` being excluded from training is enforced by a lineage test
(§13.4), not by remembering.

---

## 6. Dimension and fact models

### 6.1 `dim_settlement_period`
**Grain:** one half hour. Generated, not sourced (§2.3).

`sp_start_utc`, `sp_end_utc`, `settlement_date_local`, `settlement_period_no`,
`date_local`, `hour_local`, `dow`, `is_weekend`, `is_gb_holiday`, `is_bst`,
`periods_in_local_day` (46/48/50), `month`, `year`.

`is_gb_holiday` source resolved in M2 (**D-7**); England-and-Wales calendar,
since the demand effect is dominated by population.

**Tests:** unique + not_null; singular test asserting exactly 46, 48 or 50 periods
per `settlement_date_local`, and that exactly two days per year are not 48.

### 6.2 `fct_intensity_period`
**Grain:** one `sp_start_utc`. **Incremental.**

| Column | Meaning |
|---|---|
| `sp_start_utc` | Key |
| `actual_gco2_kwh` | Realised intensity, nullable while pending |
| `eso_forecast_gco2_kwh` | ESO's published forecast |
| `eso_error_gco2_kwh` | `eso_forecast - actual`, null while pending |
| `is_matured` | Whether the actual is considered final (§6.2.1) |
| `actual_first_known_at_utc` | When the actual first appeared with a non-null value |
| `revision_count` | Number of distinct payloads seen for this period |

**Incremental strategy:**

```sql
{{ config(materialized='incremental', unique_key='sp_start_utc',
          incremental_strategy='delete+insert') }}
{% if is_incremental() %}
WHERE sp_start_utc >= (SELECT max(sp_start_utc) FROM {{ this }}) - interval '{{ var("lookback_days") }} days'
{% endif %}
```

`lookback_days` defaults to 14 and is a dbt variable, not a literal, so M2's
measurement of the revision tail can change it without a code edit.

**The lookback is the whole design.** A naive `sp_start_utc > max(sp_start_utc)`
incremental model would append new periods and never revisit the ones that were
`null` when first seen — permanently freezing pending periods as missing actuals.
That failure is silent, compounding, and would invalidate every accuracy figure
downstream. It is the single most likely way this project could produce
confidently wrong numbers.

#### 6.2.1 Maturity

A period is `is_matured` when `now() - sp_start_utc > maturity_hours` **and** the
payload has been stable for `stability_hours`. Both values are set by M2's
measurement of the actuals lag (**D-1**), not guessed. Only matured periods are
used for scoring (§10) and training (§8).

**Tests:** unique + not_null; `actual_gco2_kwh` not_null where `is_matured`;
singular test — no period may transition from matured back to non-matured.

### 6.3 `fct_generation_mix`
**Grain:** (`sp_start_utc`, `fuel`). Incremental, same lookback.

Adds `is_low_carbon` (wind, solar, hydro, nuclear, biomass — biomass flagged as a
contested classification in the methods document rather than silently included).

Derived companion `fct_mix_wide` provides `wind_perc`, `solar_perc`,
`low_carbon_perc`, `fossil_perc` as columns for the feature builder. Long format
is the source of truth; wide is a convenience view built from it, so the two can
never disagree.

### 6.4 `fct_demand_period`
**Grain:** (`sp_start_utc`, `publish_time_utc`). Incremental.

Vintage retained in the grain (§5.4). A companion view `fct_demand_current`
exposes `DISTINCT ON (sp_start_utc) ... ORDER BY publish_time_utc DESC` for
descriptive use — clearly named so that using the current view inside a feature
builder is an obvious error rather than a subtle one.

### 6.5 `fct_weather_hour` and half-hourly alignment

Weather is hourly; the grid is half-hourly. The join requires an explicit
decision, deferred to M2 (**D-2**): linear interpolation to the half hour, or
step-hold of the hour value.

Interpolation is intuitive but it is a small act of invention — it manufactures
a value nobody published. Step-hold is honest but introduces a sawtooth into a
smooth physical variable. M2 decides by measuring which produces lower backtest
error on the baseline model, and the decision is recorded with its evidence.

Whichever is chosen, it happens **once**, in `fct_weather_period`, so every
downstream consumer inherits the same answer.

### 6.6 `dim_region`
17 DNO regions with `is_scoreable = false` hard-coded for every row, carried
through to the API response so the frontend cannot render a regional accuracy
figure even by accident.

### 6.7 `snp_intensity_actual`
dbt snapshot, `check` strategy on `actual_gco2_kwh`, keyed on `sp_start_utc`.

The landing layer already records every revision. The snapshot exists to give
that history a typed SCD2 shape with `valid_from`/`valid_to`, so the question
"how much do actuals move after first publication, and for how long" is a simple
query rather than a window function over JSON. It is a convenience over the
record of truth, not the record of truth.

---

## 7. The forecast register

The evidential core. Written by Python, read by everything, rebuilt by nothing.

### 7.1 `reg_forecast_point`
**Grain:** (`model_version`, `run_at_utc`, `target_sp_start_utc`).

| Column | Meaning |
|---|---|
| `forecast_id` | `uuid`, primary key |
| `model_version` | FK to `dim_model_version` |
| `run_id` | The issuing run |
| `run_at_utc` | Issue time. Immutable |
| `target_sp_start_utc` | The period forecast |
| `horizon_periods` | `(target - run_at) / 30 min`, 1–96 |
| `point_gco2_kwh` | Central estimate |
| `q10`, `q90`, `q025`, `q975` | Quantiles for 80% and 95% intervals |
| `code_commit` | Git SHA of the issuing code |
| `feature_snapshot_hash` | `sha256` of the exact feature vector used |
| `row_hash` | `sha256` of all of the above |

`feature_snapshot_hash` is what makes a disputed forecast resolvable: the exact
inputs can be recomputed from the warehouse's vintage history and compared.

**Constraints enforced in DDL, not in application code:**

```sql
ALTER TABLE register.reg_forecast_point
  ADD CONSTRAINT forecast_is_forward CHECK (target_sp_start_utc > run_at_utc),
  ADD CONSTRAINT horizon_in_range   CHECK (horizon_periods BETWEEN 1 AND 96);
REVOKE UPDATE, DELETE ON register.reg_forecast_point FROM gridcast_app;
```

The `REVOKE` is the actual guarantee. Append-only enforced by a code convention
is a promise; enforced by a database grant, it is a property. The application
role physically cannot rewrite history.

### 7.2 The integrity seal (FR-19)

`register.reg_forecast_seal` — one row per closed month:

`period_month`, `row_count`, `seal_hash`, `sealed_at_utc`, `sealed_by_commit`.

`seal_hash = sha256(string_agg(row_hash, '' ORDER BY forecast_id))` over the
month's rows, computed once when the month closes.

A daily job recomputes each closed month's hash and compares. Mismatch or count
change → pipeline failure and an alert, both loud.

**The seals are also committed to the repository** as `seals/YYYY-MM.json`. This
converts the guarantee from "trust my database" to "check my git history against
my live database" — an external party can verify it without access to anything
private. Seal commits are the one thing in this project it would be worth
signing.

### 7.3 `reg_forecast_score`
**Grain:** one scored forecast point. Written by the scoring job.

`forecast_id`, `scored_at_utc`, `actual_gco2_kwh`, `abs_error`, `sq_error`,
`pinball_10`, `pinball_90`, `in_80_interval`, `in_95_interval`,
`scale_mae_seasonal_naive` (the MASE denominator, computed on the training
window and stored so the ratio is reproducible years later).

Scoring is a **separate table joined by `forecast_id`**, never a column added to
the register. Adding an `actual` column to the register would mean the register
gets written twice, which would mean it is not append-only, which would mean the
seal is meaningless.

---

## 8. Feature construction

### 8.1 Direct multi-horizon formulation

One model serves all 96 horizons, with `horizon_periods` as a feature.

**Rejected — one model per horizon:** 96 artefacts to train, version, store and
promote on a free tier, for a gain that the data volume does not support.

**Rejected — recursive forecasting:** feeding predictions back as inputs
accumulates error across 96 steps and, worse, makes point-in-time auditing
nearly impossible, because a horizon-96 forecast would depend on 95 intermediate
values that were never published or sealed.

The direct formulation means **every feature is expressed relative to
`run_at_utc`, never to the target period.** "Intensity 24 hours before the
target" is not a legal feature; "intensity 24 hours before issue time" is. This
constraint is what makes leakage structurally difficult rather than merely
discouraged.

### 8.2 The knowability guard

Every feature query passes through one function:

```python
def assert_knowable(df: pd.DataFrame, run_at: datetime) -> pd.DataFrame:
    """Every row must have been available to us at run_at. No exceptions."""
    violations = df.loc[df["knowable_at_utc"] > run_at]
    if len(violations):
        raise LeakageError(f"{len(violations)} rows not knowable at {run_at}")
    return df
```

Called in training and in serving, from the same module. A unit test asserts it
raises on a deliberately leaked frame (§13.3). A feature source without a
`knowable_at_utc` column cannot pass through it, which is why every staging model
carries one.

### 8.3 Vintage reconstruction for backfilled history

Backfilled rows have `fetched_at_utc` = the backfill date in 2026, which is later
than every target period before it. Applying the guard literally would make all
history unusable; ignoring the guard would leak.

**Resolution:** for periods ingested by backfill, `knowable_at_utc` is
*reconstructed* as `sp_start_utc + measured_publication_lag`, where the lag is
measured in M2 from live observation (**D-1**) and stored per source. Rows carry
`knowable_is_reconstructed = true`.

Consequently, results are reported in two columns that are **never pooled**:

| Column | Basis | Guarantee |
|---|---|---|
| **Backtest** | Reconstructed vintages | Approximate. Publication lag assumed constant |
| **Live** | True `fetched_at_utc` | Exact. Leakage-proof by construction |

The gap between the two columns is published as a finding, not hidden. If the
backtest column is materially better than the live column, that difference is
itself the measurement of how much optimism reconstruction introduced — which is
a more interesting result than either number alone.

### 8.4 Feature set

| Group | Features |
|---|---|
| Horizon | `horizon_periods`, `horizon_hours` |
| Calendar (of target) | `sin/cos` of period-of-day, `sin/cos` of day-of-year, `dow`, `is_weekend`, `is_gb_holiday`, `is_bst` |
| Intensity lags (of issue time) | last known actual, actual at `run_at − 24h`, `− 48h`, `− 168h`; rolling mean and SD over the last 24h and 168h |
| Demand (vintage-correct) | last known `demand_indo_mw`, same-period-yesterday demand as known at issue time |
| Mix (of issue time) | `wind_perc`, `solar_perc`, `low_carbon_perc` at last known period; 24h rolling mean of `wind_perc` |
| Weather forecast (for target) | `wind_speed_100m`, `temperature_2m`, `shortwave_radiation`, `cloud_cover` at the target hour, taken from the forecast vintage issued at or before `run_at` |
| Weather deltas | Target-hour wind minus 24h-trailing mean wind — the ramp signal |
| ESO forecast (augmented models only) | `eso_forecast_gco2_kwh` for the target period |

The last group is quarantined for the reason in §9.2.

---

## 9. Model roster

### 9.1 Baselines and benchmark

| ID | Model | Definition |
|---|---|---|
| `B0` | Persistence | Last known actual at issue time, held flat across all horizons |
| `B1` | Seasonal naive | Actual at target − 48 periods, subject to knowability |
| `B2` | Weekly naive | Actual at target − 336 periods |
| `B3` | Climatology | Median by (period-of-day × month) over the trailing 3 years |
| `ESO` | **Benchmark** | National Grid ESO's published forecast for the target period |

`B1` is the MASE denominator. `ESO` is a benchmark, not a baseline — the
distinction matters: baselines establish that the model has learned anything at
all, the benchmark establishes whether it is competitive with the institution
that operates the grid.

### 9.2 GridCast models

| ID | Model | Features | Status |
|---|---|---|---|
| `G1` | SARIMAX + exogenous weather | Weather, calendar | Independent |
| `G2` | HistGradientBoostingRegressor | All except ESO group | **Independent — the fair competitor** |
| `G3` | Quantile GBM (α = 0.025, 0.1, 0.9, 0.975) | Same as G2 | Independent, supplies intervals |
| `G4` | G2 features **+ ESO forecast** | All | **Augmented — not an independent competitor** |

**The G4 quarantine.** A model that takes the ESO forecast as an input and then
beats the ESO forecast has not out-forecast the grid operator; it has
bias-corrected them. That is genuinely useful — it is probably the most accurate
thing this project will produce — but describing it as "beating National Grid"
would be the single most dishonest sentence available in this project.

Therefore: **G4's accuracy is never reported in the same table as `ESO`.** It
appears on a separate surface labelled *ESO-augmented*, with the relationship
stated in the caption. The headline comparison is always `G2` versus `ESO` —
independent versus independent.

### 9.3 Training

Weekly, in GitHub Actions. Expanding window from 2018 with an optional recency
weight, evaluated in M6. Artefacts (`joblib`) stored in the repository under
`models/` with the version in the filename, referenced by `dim_model_version`.

Artefacts are committed rather than stored in Postgres or object storage:
they are small (< 20 MB), they version naturally with the code that produced
them, and the serving API never loads them at all (§11.1), so retrieval latency
is irrelevant.

---

## 10. Backtesting and scoring

### 10.1 Rolling-origin harness (M4)

```
for origin in origins:                    # every 24h across the evaluation span
    train  = data[data.knowable_at <= origin - embargo]
    issue  = origin
    targets = periods in (origin, origin + 48h]
    predict and score
```

**The embargo** is a deliberate gap between the end of training data and the
issue time, sized to the measured actuals maturity lag. Without it, training
includes actuals that would still have been pending at issue time — the most
common leakage in time-series backtesting, and one that flatters results
precisely at short horizons where the model is supposed to be strongest.

Origins step by 24 hours, giving ~2,900 origins across seven years and ~278,000
scored forecast points per model. That is ample for the horizon-group tests in
§11 without approaching free-tier limits.

### 10.2 Metrics

| Metric | Applied to | Purpose |
|---|---|---|
| MAE, RMSE | Point forecasts | Interpretable in gCO₂/kWh |
| **MASE** | Point forecasts | Headline metric — scale-free, safe at low intensity |
| Pinball loss | Quantiles | Proper scoring rule for the intervals |
| Empirical coverage | 80% / 95% intervals | Honesty check: an 80% interval containing 62% of actuals is a broken product, not a small miss |
| Interval width | 80% / 95% | Guards against the degenerate fix of widening intervals until coverage passes |

MAPE is deliberately excluded. GB intensity now reaches single digits on windy
nights, where percentage error explodes and produces a headline number driven
entirely by the calmest hours of the cleanest days.

### 10.3 Scoring job (FR-18)

Runs every 6 hours:

1. Select register rows where the target period is now `is_matured` and no score
   row exists.
2. Join to `fct_intensity_period` on `target_sp_start_utc`.
3. Compute metrics, insert into `reg_forecast_score`.
4. Rebuild the accuracy marts.
5. Run the seal audit (§7.2).

Scoring is strictly insert-only and idempotent through a unique constraint on
`forecast_id`. A forecast can be scored once, ever.

### 10.4 Accuracy marts

`mart_accuracy_rolling` — grain (`model_version`, `horizon_group`, `window_days`,
`as_of_date`): MAE, MASE, coverage, width, sample size.

**Every row carries `n`.** NFR-9 forbids displaying an accuracy figure without
its sample size, and the cheapest way to comply is to make it impossible to
query the number without the count sitting beside it.

---

## 11. Champion / challenger

### 11.1 Mechanics

Exactly two models forecast live: the champion, whose forecasts serve the
application, and the challenger, whose forecasts are written to the register and
scored identically but are never shown as the product's answer. Both write with
their own `model_version`; nothing distinguishes them in the register except the
registry's `role` column at that time.

### 11.2 Horizon groups

| Group | Horizons | Hours ahead |
|---|---|---|
| `H1` | 1–6 | 0–3 |
| `H2` | 7–24 | 3–12 |
| `H3` | 25–48 | 12–24 |
| `H4` | 49–96 | 24–48 |

Four groups, fixed here, before any challenger exists. Testing all 96 horizons
individually would guarantee a "significant" winner by multiplicity alone
(R-10).

### 11.3 The pre-registered promotion rule

Committed to `PREREGISTRATION.md` at M0 and referenced by commit hash in every
result. **This is the content, fixed in advance:**

- **Test:** Diebold–Mariano on the loss differential of absolute errors, with the
  Harvey–Leybourne–Newbold small-sample correction, `h`-step autocorrelation
  robust.
- **Confirmatory check:** paired Wilcoxon signed-rank on the same differentials.
  Both must agree in direction.
- **Minimum sample:** 1,440 scored points per horizon group — 30 days of live
  operation. No test is computed before this threshold, and the threshold is
  checked on scored, matured points only.
- **α:** 0.05, two-sided, Benjamini–Hochberg adjusted across the four groups.
- **Promotion requires:** challenger better in ≥ 3 of 4 groups at adjusted
  p < 0.05, **and** not significantly worse in any group.
- **Cooldown:** 14 days after any promotion before another comparison starts, so
  the champion is never a model that won on a single fortnight's weather.
- **Interval models** are additionally required not to degrade 80% coverage by
  more than 2 percentage points, regardless of point-accuracy gains.

Every evaluation — promoting or not — is written to `dim_model_version`'s
promotion history with its test statistics. **Non-promotions are published too.**
A challenger that failed is evidence the rule is real; a repository containing
only successful promotions is evidence of nothing.

### 11.4 Drift detection

Weekly: PSI and two-sample KS on each feature's distribution, trailing 30 days
versus the training window. CUSUM on the champion's rolling MASE.

A drift alert does **not** trigger automatic retraining or promotion. It raises
an alert and opens a decision, because automatic reaction to a drift signal is
how a system quietly chases seasonal noise into a worse model.

---

## 12. The action layer and application

### 12.1 Load-shift planner (FR-22–24)

Input: `duration_minutes`, `power_kw`, `earliest_start`, `latest_finish`.

For every feasible start period, compute expected kgCO₂ from the champion's
point forecast integrated across the run window, and a saving interval from the
q10/q90 forecasts. Cost uses the market index price where the horizon permits.

Reported against three counterfactuals (FR-23): run now, run at a uniformly
random feasible time, run at 03:00 — the folk heuristic. The random counterfactual
is the honest one; "run now" flatters the tool whenever now happens to be dirty.

**The hit rate.** Alongside every recommendation, the planner reports how often a
recommendation made at this horizon, historically, actually landed in the
cleanest tercile of its feasible window. That figure comes from replaying the
planner over the scored register — so it is measured, not modelled. It is
entirely possible that this number is unimpressive at 48 hours. Publishing it
anyway is the point of the project.

### 12.2 API surface (read-only)

| Route | Returns |
|---|---|
| `GET /health` | Liveness, build SHA |
| `GET /v1/forecast/current` | Champion forecast, 96 periods, with intervals |
| `GET /v1/accuracy` | Rolling accuracy by model, horizon group, window — always with `n` |
| `GET /v1/leaderboard` | All models and benchmarks on identical scored periods |
| `GET /v1/plan` | Load-shift recommendation with saving interval and hit rate |
| `GET /v1/status` | Per-source freshness, last runs, open test failures, latest seal check |
| `GET /v1/alerts` | Alert feed |
| `GET /v1/models` | Registry, including promotion and demotion history |

All routes read precomputed rows (FR-29, NFR-6). The API imports neither
scikit-learn nor statsmodels — enforced by a CI check on the serving
requirements file, so the constraint cannot erode through a convenient import.

Database access uses a role with `SELECT` only.

### 12.3 Frontend pages

| Page | Content |
|---|---|
| `/` | Current forecast fan chart, cleanest windows, freshness banner |
| `/accuracy` | Error by horizon over 7/30/90 days; GridCast, ESO and baselines on one axis |
| `/plan` | The planner, with saving interval and historical hit rate |
| `/models` | Registry, live champion/challenger status, promotion decisions including failures |
| `/status` | Pipeline health, source freshness, seal audit result |
| `/methods` | Leakage controls, backtesting protocol, pre-registration, known limitations |

Fan charts encode intervals with width and pattern as well as colour (NFR-10);
the accuracy chart is readable in greyscale.

---

## 13. Test strategy

### 13.1 dbt tests
Generic tests on every model as listed per-model above. Singular tests for the
semantic conditions schema validation cannot express:

| Test | Catches |
|---|---|
| Periods per local day ∈ {46, 48, 50} | Clock-change corruption |
| Exactly two non-48 days per year | Over-eager clock-change "fixes" |
| Mix percentages sum to 100 ± tol | Upstream fuel-category changes |
| No matured period with null actual | The frozen-pending-period failure (§6.2) |
| No period reverts from matured | Non-monotonic maturity logic |
| `publish_time >= sp_start` on demand | Timestamp field confusion |
| Regional models expose no actual column | R-6, enforced structurally |

### 13.2 Ingestion tests
Run the same window twice → zero rows written on the second pass. Induce a gap,
run gap-fill, assert the gap closes. Replay a payload with a changed value →
exactly one new landing row, and the staging view reflects the new value while
the old row survives.

### 13.3 Leakage tests
`assert_knowable` raises on a frame containing a future `knowable_at_utc`.
Feature builder invoked at a historical `run_at` produces a frame whose maximum
`knowable_at_utc` is ≤ `run_at`. Backtest origins respect the embargo.

### 13.4 Lineage tests
A test parses the dbt graph and fails if any model feeding the feature builder
depends on `stg_om_archive` (§5.5) or on `fct_demand_current` (§6.4). These are
the two paths by which leakage would look like ordinary code.

### 13.5 Register tests
The application role cannot `UPDATE` or `DELETE` the register — asserted by
attempting it and expecting a permissions error. Seal recomputation matches the
committed seal file. A forecast with `target <= run_at` is rejected by the check
constraint.

### 13.6 CI
Every push: lint, type-check, unit tests, dbt build against a fixture warehouse
of two weeks of sampled data. The fixture makes the pipeline verifiable without
network access or the full history — the same reasoning applied in the author's
previous project, where CI on sample fixtures proved the build worked for anyone
cloning the repository.

---

## 14. Orchestration

Three scheduled workflows plus two manual ones. Fewer workflows than jobs,
because each workflow is a thing that can independently break.

| Workflow | Trigger | Steps |
|---|---|---|
| `pipeline.yml` | `*/30 * * * *` | ingest (all 30-min sources) → gapfill → `dbt build` → issue forecasts (champion + challenger) → seal check |
| `daily.yml` | `0 3 * * *` | score matured forecasts → rebuild accuracy marts → weather archive → drift check → freshness alerts |
| `train.yml` | `0 4 * * 1` | retrain → backtest → register new version → open a promotion evaluation if the sample threshold is met |
| `backfill.yml` | manual | Parameterised historical load |
| `ci.yml` | push / PR | Tests, lint, fixture dbt build |

**The repository must be public** for unlimited Actions minutes, which it will be
anyway.

**Known scheduler behaviour:** GitHub cron is best-effort and can be delayed
under load, and scheduled workflows are disabled after 60 days of repository
inactivity. Neither is worked around — FR-4's gap-filling makes lateness
harmless, and the inactivity rule is handled by the seal commits, which touch the
repository monthly as a side effect of the integrity mechanism.

---

## 15. Decisions deferred to M2

Each resolves by measurement, and each has a home in this document waiting for
the answer.

| ID | Decision | Resolved by |
|---|---|---|
| **D-1** | Actuals maturity lag and stability window → `maturity_hours`, `stability_hours`, embargo size, reconstructed vintage offsets | Measuring first-publication and revision timing over a live observation period |
| **D-2** | Weather half-hourly alignment: interpolate or step-hold | Backtest error comparison on the baseline model |
| **D-3** | Weather sample locations and their weighting | Correlation of candidate sites against national wind share |
| **D-4** | Availability and route of Open-Meteo historical-forecast data; Elexon day-ahead endpoints | Direct probing |
| **D-5** | Landing retention policy under Neon free-tier storage | Growth measurement after full backfill |
| **D-6** | Generation-mix percentage sum tolerance; whether mix × demand yields usable MW estimates | Distribution of observed sums |
| **D-7** | GB holiday calendar source and whether Scottish holidays materially affect demand | Demand comparison on divergent dates |

**M2 is permitted to change this document.** A design phase that survives contact
with the data unchanged usually means the data was not examined closely enough.
Changes are recorded in §16's change log with the finding that caused them.

---

## 16. Document control

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-08-12 | Muhammad Haris Khokhar | Initial design. Grain, incremental strategy, append-only register mechanics, leakage controls and promotion rule fixed in advance of implementation |

### Change log
*(Entries added as M2 findings force revisions.)*
