# GridCast — M1 Summary: Ingestion and Backfill

**Milestone:** M1
**Date:** 2026-08-12
**Status:** Complete

---

## 1. Exit criterion

> All three sources ingesting on schedule; full history 2018→present loaded;
> run-log populated; gap-fill demonstrated by deliberately skipping a run.
> Elexon forecast-endpoint investigation closed.

All met. The gap-fill demonstration is in §4 and the D-4 closure in §3.

---

## 2. What was built

Eight sources across three keyless APIs, all writing through one path.

| Source | Endpoint | Landing table | Cadence |
|---|---|---|---|
| `ci_intensity` | Carbon Intensity `/intensity` | `lnd_ci_intensity` | 30 min |
| `ci_genmix` | Carbon Intensity `/generation` | `lnd_ci_genmix` | 30 min |
| `ci_regional` | Carbon Intensity `/regional/intensity` | `lnd_ci_regional` | daily |
| `ex_demand` | Elexon `/demand/outturn` | `lnd_ex_demand` | 30 min |
| `ex_price` | Elexon `/balancing/pricing/market-index` | `lnd_ex_price` | daily |
| `om_archive` | Open-Meteo archive | `lnd_om_archive` | daily |
| `om_forecast` | Open-Meteo forecast | `lnd_om_forecast` | 30 min |
| `om_vintage` | Open-Meteo historical-forecast | `lnd_om_vintage` | backfill |

Adding a source is one module plus one line in the registry. Nothing in the
writer, the CLI, the gap detector or the workflows knows how many there are.

### 2.1 One mechanism, three requirements

Every write goes through insert-if-changed: a row lands only when its payload
differs from the last one stored for that key. That single rule satisfies three
separate requirements, and it was worth not building three mechanisms.

| Requirement | How it falls out |
|---|---|
| **FR-3** idempotency | Re-running a window finds identical payloads and writes nothing |
| **FR-9** revision history | A revised value differs, so it is a new row and the old one survives |
| **FR-15** knowability | `fetched_at_utc` on the winning row is the instant we first held that value |

Verified live: 192 rows written on a first pass, **0** on an identical re-run.

---

## 3. D-4 closed: the Elexon forecast endpoints exist

The SRS recorded that `/forecast/demand/day-ahead` and
`/forecast/generation/wind` returned 404 at the paths tried, and deliberately
made no requirement depend on them.

They exist. The 404s were wrong routes — `/latest` suffixes that are not part of
the API — not absent data. Both return 200 without credentials:

| Endpoint | Datasets | Carries |
|---|---|---|
| `/forecast/demand/day-ahead` | NDF, TSDF | `nationalDemand`, `transmissionSystemDemand`, **`publishTime`** |
| `/forecast/generation/wind` | WINDFOR | `generation` MW, hourly, **`publishTime`** |

Both carry a publication time distinct from the settlement period they describe,
which makes them point-in-time usable. This matters at M6: a *published*
day-ahead demand and wind forecast is information the live system genuinely has
at issue time, so a model may use it without leakage. That is a materially
stronger feature set than the SRS assumed was available.

Ingestion of these two is **not** added here. M1's scope is the sources the
warehouse needs; adding features the models cannot yet consume would be building
ahead of a requirement.

---

## 4. Gap detection proven by breaking it

The exit criterion asks for a demonstration, not an assurance.

```
1. Baseline           ci_intensity   no gaps
2. Delete 12 hours    DELETE 24
3. Detect             ci_intensity   24 missing period(s) in 1 window(s)
                          2026-08-11 08:00 -> 2026-08-11 20:00
4. Heal               ci_intensity   healed 24 of 24 period(s)
5. Re-detect          ci_intensity   no gaps
```

Two design points the demonstration exercises:

**Absence is the question, not emptiness.** A period whose row exists with a
pending null actual is *not* a gap. Conflating the two would have the pipeline
refetching forever for a value that does not exist yet. Only the spine can tell
the difference, which is why it is generated rather than derived from the data.

**Contiguous outages coalesce.** Twenty-four missing periods became one window,
not twenty-four requests. Six hundred missing periods are one outage.

### 4.1 A reporting defect found while proving it

The first run logged `rows_written = 0` for the heal, because the writes were
attributed to the nested ingestion run rather than the parent. Anyone reading
`run_log` after a real outage would have seen a heal that appeared to recover
nothing. Fixed: the parent now records the size of the hole as `rows_read` and
what filling it recovered as `rows_written`, and marks itself `partial` when
those differ — which is the honest state when upstream never published the
periods at all.

---

## 5. Findings

### 5.1 Three window limits differ from their documentation

Measured, not read. All three fail loudly rather than truncating, which is the
good case — a silent truncation would have written a partial window and left a
gap that looked like an upstream absence.

| Source | Documented | Actual | On exceeding |
|---|---|---|---|
| Carbon Intensity | 14 days | **30 days** | HTTP 400 naming the limit |
| Elexon demand | not stated | **28 days inclusive** | HTTP 400 naming the limit |
| Open-Meteo archive | not stated | 90+ days fine | — |

Both are used with margin — 28 and 21 days respectively — so the backfill costs
one extra request per fourteen months rather than depending on an undocumented
boundary staying exactly where it is today.

### 5.2 Backward-looking endpoints cannot be asked about the future

`om_archive` failed on its first real run:

```
400: Parameter 'end_date' is out of allowed range from 1940-01-01 to 2026-08-12
```

Ingestion windows deliberately run two days *past* now, so that sources carrying
forecasts — the ESO forecast and the weather forecast — collect periods which
have not happened yet. The archive and vintage endpoints describe only the past
and reject such a range.

Fixed by clamping inside those two sources rather than narrowing the shared
window. Narrowing it would have quietly stopped collecting every forward horizon
the project exists to measure, and nothing would have complained.

Worth noting what went right: the other sources in that run completed normally.
Per-source isolation (R-3) meant one upstream's rejection did not halt the rest.

### 5.3 The three Open-Meteo endpoints are not interchangeable

They are kept strictly apart because conflating them is leakage:

- **archive** — reanalysis actuals. What the weather *was*. Never a training
  feature, because production will never have it.
- **forecast** — what is predicted now. What the live system genuinely holds.
- **vintage** — what was predicted at a past moment, as issued. The only honest
  source for training on history.

A model trained on archive actuals learns to rely on perfect knowledge of future
weather. It would backtest beautifully and fail in production, and the gap would
stay invisible until the live scoreboard opened. A lineage test at M3 will make
this structural rather than remembered.

### 5.4 D-5 resolved by measurement: regional is not backfilled

Measured on Neon after the first backfill: **322 bytes per row** including
indexes. Projected across full history:

| Table | Rows | Projected |
|---|---|---|
| `lnd_ci_intensity` | 147,700 | ~47 MB |
| `lnd_ci_genmix` | 147,700 | ~75 MB |
| `lnd_ex_demand` | 147,700 | ~59 MB |
| `lnd_om_vintage` | 420,000 | ~130 MB |
| **`lnd_ci_regional`** | **2,510,000** | **~880 MB** |

Regional carries 17 DNO regions per settlement period, so it alone would exceed
everything else combined — to store data that **can never be scored**, because
regional responses carry a forecast and no actual (SRS §6.4).

**Decision:** regional is ingested forward on a daily cadence and backfilled only
90 days. It informs where a recommendation applies; it can never be measured, so
paying for eight years of it would be paying the most for the only series whose
accuracy is permanently unknowable.

This is D-5 answered with a number rather than a guess, which is what the design
document deferred it for.

---

## 6. Scheduling

| Workflow | Trigger | Does |
|---|---|---|
| `pipeline.yml` | `*/30 * * * *` | Ingest scheduled sources → heal 7-day gaps → rebuild warehouse |
| `daily.yml` | `0 4 * * *` | Ingest daily sources → heal 30-day gaps → report coverage |
| `backfill.yml` | manual | Parameterised historical load |

The heal step runs `if: always()`. A partial ingestion failure is exactly when
gaps appear, so skipping the heal on failure would leave the hole open until the
next successful run — the opposite of what is wanted.

`backfill.yml` is not a special code path. It is ordinary ingestion with a wider
window, which is why a backfill killed at GitHub's six-hour ceiling can simply be
run again: every write goes through insert-if-changed, so re-covering loaded
ground writes nothing.

---

## 7. Test coverage added

63 tests pass. The ones that matter:

| Test | Guards against |
|---|---|
| Hash ignores key order | jsonb reordering making every unchanged row look changed, forever |
| Hash notices null → number | Missing the pending-to-published transition, freezing every period as unknown |
| Windows never exceed the limit | An HTTP 400 mid-backfill |
| Windows are contiguous | A hole between chunks that nobody would notice |
| Unchanged payload writes nothing | FR-3 |
| A revision is a new row, old survives | FR-9 |
| A mixed batch writes only what changed | The realistic case, where most of a window is unchanged |
| Time column is part of every key | The write path degrading to a sequential scan on a table of millions |

---

## 8. What M1 deliberately did not do

- **No staging or mart models.** Landing holds raw payloads; typing and
  reshaping is M3's job, after M2 has measured what the data actually does.
- **No Elexon forecast ingestion.** The endpoints are confirmed available
  (§3); wiring them in before a model can consume them would be building ahead
  of a requirement.
- **No maturity logic.** `maturity_hours` and `stability_hours` are still
  placeholders. M2 measures the actuals lag rather than guessing it, and that
  measurement is what makes scoring trustworthy.

---

## 9. Next milestone

**M2 — Data-quality audit.** Committed queries quantifying the actuals lag and
revision behaviour (D-1), weather half-hourly alignment (D-2), weather location
correlation with national wind share (D-3), mix-sum tolerance (D-6) and the
holiday calendar (D-7). The audit resolves the deferred design decisions with
evidence, and is permitted to change the design document.
