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

## Status

**M0 complete — walking skeleton.** The path from browser to API to warehouse is
deployed and verified before any analysis exists.

| Milestone | State |
|---|---|
| M0 Foundation & walking skeleton | ✅ Complete |
| M1 Ingestion & backfill | Next |
| M2 Data-quality audit | Pending |
| M3 Warehouse | Pending |
| M4 Baselines & backtesting harness | Pending |
| M5 Live forecasting loop | Pending — *the defensible stopping point* |
| M6 Modelling depth | Pending |
| M7 Champion/challenger & monitoring | Pending |
| M8 Product & communication | Pending |

There are deliberately **no forecasts and no accuracy figures yet**. Publishing
one before a single out-of-sample score exists would be precisely the behaviour
this project is built to avoid.

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
