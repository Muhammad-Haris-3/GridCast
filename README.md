# GridCast

**Forecasts that grade themselves.**

GridCast forecasts Great Britain's electricity grid carbon intensity 48 hours
ahead, publishes each forecast **before the outcome exists**, and scores it
automatically once the actual arrives — against naive baselines and against
National Grid ESO's own published forecast.

The central claim of this project is not "my model is accurate." It is: *here is
exactly how accurate it has been, measured continuously, against benchmarks that
could not have been chosen after the fact.*

---

## Why this project exists

Carbon intensity swings between roughly 20 and 400 gCO₂/kWh within a day.
Anyone with a flexible load — EV charging, heat pumps, batteries, deferrable
compute — can cut its emissions substantially by choosing *when* to run, at no
capital cost.

Forecasts are freely published. What is not published, by anyone, is a
continuously maintained out-of-sample record of how wrong those forecasts turned
out to be, by horizon and by condition. A load-shifting decision is only as good
as the forecast behind it, and nobody is keeping score.

GridCast keeps score, including on itself.

---

## What makes it hard to fake

| Mechanism | What it prevents |
|---|---|
| **Append-only register.** The application role has no `UPDATE` or `DELETE` permission on the forecast table — enforced by a database grant, not a code convention | Retrospective editing of forecasts |
| **Monthly integrity seals** committed to git as `seals/YYYY-MM.json` | Silent tampering — the live database can be checked against public commit history by someone with access to neither |
| **`CHECK (target > run_at)`** in the table definition | Backdated forecasts, the simplest way to fake accuracy |
| **Point-in-time features.** Every feature is expressed relative to *issue time*, never to the target period; a knowability guard raises on any value we could not have held | Lookahead leakage, the failure that silently invalidates every accuracy claim |
| **Pre-registered promotion rule**, committed at M0 before any model existed | Choosing the decision rule after seeing which model won |
| **Backtest and live results reported in separate columns**, never pooled | Presenting approximate reconstructed-vintage results as if they were leakage-proof |

---

## Architecture

```
Carbon Intensity API · Elexon BMRS · Open-Meteo
      │  (all keyless, verified 2026-08-12)
      ▼
GitHub Actions — cron, backfill, training      idempotent · gap-filling · run-logged
      ▼
landing   append-only raw payloads, insert-if-changed
      ▼
staging   typed views, UTC-normalised, never joins
      ▼
marts     incremental star schema + snapshots
      ├──► training jobs ──► model registry
      │                          ▼
      │            register  append-only forecasts
      │                          ▼
      │                     scoring ──► accuracy marts
      ▼                                      │
FastAPI (read-only, Render) ◄────────────────┘
      ▼
Next.js (Vercel)
```

The API reads precomputed rows and **never trains**. Training runs offline in
GitHub Actions. That split is what keeps the service inside a 512 MB free-tier
container, and it is also simply the correct production pattern.

---

## Live

| Surface | URL |
|---|---|
| Application | https://grid-cast-sigma.vercel.app |
| **When should I run it?** | https://grid-cast-sigma.vercel.app/plan |
| How wrong were we? | https://grid-cast-sigma.vercel.app/accuracy |
| Pipeline status | https://grid-cast-sigma.vercel.app/status |
| API | https://gridcast-api-xhca.onrender.com |

**Start here:** [DECISION_MEMO.md](DECISION_MEMO.md) — the finding, in plain
English, in two pages. [METHODS.md](METHODS.md) — how the numbers are produced,
with the failures listed first.

The API runs on a free instance that sleeps after inactivity, so the first
request in a while takes 30-60 seconds to wake it. The status page says so
rather than showing a broken screen.

## Status

**M9 complete — the site no longer depends on the database being reachable.**
M7 is the one milestone still open, and it is waiting on data rather than on
code: the pre-registered promotion rule cannot be evaluated until enough
forecasts have been scored.

**The challenger did not issue between 2026-08-15 and 2026-09-04**, and for the
three days before that it issued without the weather it was trained on. Both
had the same cause and neither was noticed at the time. What that means for the
figures below is set out under *First live figures*; what it means for the
project is that a silent failure survived three weeks of a system built to make
failures loud, which is recorded here rather than quietly repaired.

M9 was not planned. On 2026-08-17 the database's free-tier **data transfer**
allowance ran out and everything stopped at once — the API returned 500s, the
pages went blank, and the pipeline could not read either, so the register
stopped growing. Page views were part of what spent it: every visit asked
Postgres a question that the last pipeline run had already answered.

Serving now reads static snapshots published by the pipeline, so traffic costs
no database transfer and the site keeps answering with the last good figures,
labelled with their age, while the database is unreachable. Issuing reads what
it consults and no more: the interval calibration that needed a year of history
is computed once a day rather than 48 times, which is the change that mattered
most. See NFR-13 in the SRS for the requirement this should have started with.

There is also now a counter. Every read is measured and attributed to the job
that made it, and the period total is published on the
[status page](https://grid-cast-sigma.vercel.app/status) beside everything
else — the allowance is what stopped the register growing, so how close it is
to running out is part of knowing whether the evidence is still accumulating.
Past 90% the daily calibration stands down; issuing and scoring never do,
because a forecast not written is evidence permanently missing.

The figure is an estimate, measured from returned value widths rather than the
wire, and it says so wherever it appears. It exists to make a tenfold
regression obvious on the day it lands, not to say how much allowance is left.

| Milestone | State |
|---|---|
| M0 Foundation & walking skeleton | ✅ Complete — deployed |
| M1 Ingestion & backfill | ✅ Complete |
| M2 Data-quality audit | ✅ Complete |
| M3 Warehouse | ✅ Complete |
| M4 Baselines & backtesting harness | ✅ Complete |
| M5 Live forecasting loop | ✅ Complete — *the defensible stopping point* |
| M6 Modelling depth | ✅ Complete |
| M7 Champion/challenger & monitoring | Waiting on data — the pre-registered rule needs ~1,440 scored points per horizon group. The challenger's clock restarted on 2026-09-04: everything scored before that was issued without weather features |
| M8 Product & communication | ✅ Planner, decision memo, methods |
| M9 Operational resilience | ✅ Site serves from published snapshots; issuing reads bounded by what it consults |
| M9.1 The three-week hole | ✅ Issuing reads the live weather forecast; a challenger that cannot build is recorded in the run log rather than printed |

Four models issue forecasts every run — a seasonal-naive champion, a persistence
baseline, a gradient-boosting challenger, and National Grid ESO's own forecast
recorded at the horizon we received it. Three of them have issued every run
since 2026-08-12. The fourth is the subject of the caveat below.

### First live figures

The accuracy page refuses to print a number until a horizon group holds 200
scored points. Every group has now passed it. **14,513 forecasts have been
scored**, all of them issued before the outcome existed, none of them
reconstructed.

MASE is the ratio to the seasonal-naive baseline on the same periods: below 1.0
beats it, 1.0 is it. At 0–3 hours:

| Model | n | MAE gCO₂/kWh | MASE |
|---|---:|---:|---:|
| ESO_published — National Grid's own forecast | 416 | 21.5 | 0.54 |
| G2_gbm_v1 — the challenger *(see caveat)* | 341 | 32.2 | 0.81 |
| B1_seasonal_naive_q_v1 — the champion | 422 | 49.9 | 1.26 |
| B0_persistence_v1 — the reference baseline | 416 | 55.0 | 1.39 |

The headline is not flattering and is not meant to be: **ESO's published
forecast is the most accurate thing on this scoreboard, at every horizon.**
That is worth knowing, and it is the kind of result a project that graded itself
after the fact would have found a reason not to publish. The champion is a
baseline on purpose (see M5), so losing to a national control room's model is
the expected shape of the first scoreboard, not a surprise.

**The caveat, and it is a large one.** Every G2 figure above was produced
between 12 and 15 August, when a crossed wire meant issuing read weather from a
relation that holds no row for any period being forecast. Its forward weather
features were all NaN, and gradient boosting consumes NaN without complaining.
So those are the scores of a model running without the inputs that distinguish
it — not of G2 as designed and backtested, which scored MASE 0.46–0.55
out-of-sample. The two numbers are not comparable and the gap is not evidence
of anything.

G2 resumed issuing on 2026-09-04 with the weather it was built to use. Its live
record starts again from there, and until it has 200 scored points of its own
the figures above should be read as a floor on a broken configuration rather
than as a measurement of the model. Nothing has been removed from the register
— the rows are evidence of what was issued, and the register cannot be edited —
but they are the wrong rows to judge the challenger by.

The intervals tell the same story from the other side: G2's 80% band covered
42–51% of outcomes, against a nominal 80%. An interval calibrated on a model
with weather, applied to a model without it, is not calibrated at all.

---

## Running it locally

Requires Python 3.11+, Node 22+, and PostgreSQL 16+.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env          # then fill in your database URLs
```

Create the database and apply the schema:

```bash
createdb gridcast && python -m gridcast.migrate
```

Build the warehouse:

```bash
cd dbt_gridcast && dbt build --profiles-dir .
```

Run the tests, including the append-only guarantees against a real database:

```bash
pytest -v
```

Start the API and the frontend:

```bash
python -m uvicorn api.main:app --port 8000
```

```bash
npm --prefix web run dev
```

---

## Repository layout

| Path | Contents |
|---|---|
| `GridCast_SRS_v1.0.md` | Requirements: FR-1→32, NFR-1→12, milestones, risks |
| `GridCast_Design_Phase_v1.0.md` | Every table, its grain, incremental strategy and tests |
| `PREREGISTRATION.md` | The model promotion rule, frozen before any model existed |
| `sql/` | Schema DDL, applied idempotently on every deploy |
| `gridcast/` | Pipeline: config, database, HTTP client, run log |
| `dbt_gridcast/` | Warehouse models and tests |
| `api/` | Read-only FastAPI service |
| `web/` | Next.js frontend |
| `seals/` | Monthly register integrity seals — externally verifiable |

---

## Data sources

All three are free, keyless, and were verified live on 2026-08-12.

- **[Carbon Intensity API](https://api.carbonintensity.org.uk)** — National Grid
  ESO. Half-hourly national intensity with both forecast and actual, back to
  2018-05-09. Licensed CC BY 4.0.
- **[Elexon BMRS Insights](https://data.elexon.co.uk)** — half-hourly demand
  outturn in MW and market index price, with a `publishTime` distinct from the
  settlement period, which is what makes point-in-time-correct features possible.
- **[Open-Meteo](https://open-meteo.com)** — hourly weather history, forecasts,
  and archived past forecast runs as issued.

**Known constraint:** regional intensity carries a forecast but **no actual**.
Regional figures therefore can never be scored, and are labelled unvalidated
wherever they appear. This is enforced in the schema, not by discipline.
