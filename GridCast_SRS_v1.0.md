# GridCast — Software Requirements Specification v1.0

**Project:** GridCast — A Self-Grading Grid Carbon Forecasting Service
**Author:** Muhammad Haris Khokhar
**Date:** 2026-08-12
**Status:** Draft for approval — M0 not yet started

---

## 1. Introduction

### 1.1 Purpose

This document specifies the requirements for **GridCast**, a continuously
running forecasting service that predicts Great Britain's electricity grid
carbon intensity 48 hours ahead, **publishes each forecast before the outcome
exists**, then automatically grades it against reality when the actual value
arrives — and shows the resulting accuracy history in public.

The deliverable is a **deployed software product** whose central claim is not
"my model is accurate" but "here is exactly how accurate my model has been,
measured continuously, against benchmarks I did not choose after the fact."

### 1.2 Scope

GridCast ingests half-hourly grid data and hourly weather from three public
APIs, maintains an incremental data warehouse, trains forecasting models
offline, publishes forecasts on a schedule, scores them once actuals land, runs
a live champion/challenger comparison decided by a statistical test, and
converts the forecast into a costed load-shifting recommendation.

**In scope:** scheduled incremental ingestion with backfill, an incremental
dbt warehouse with snapshots, data-quality and freshness monitoring,
time-series forecasting with prediction intervals, rolling-origin backtesting,
continuous out-of-sample scoring, live model comparison via hypothesis testing,
drift detection, an alerting feed, a public web application, a read-only JSON
API, and a written decision memo.

**Out of scope (v1.0):** sub-half-hourly or streaming ingestion; user accounts,
login, or personalised settings; email or SMS delivery of alerts; deep learning;
markets or trading; any country other than Great Britain; forecasting regional
intensity as a *scored* product (see §6.4 — the data does not permit it);
mobile applications; any claim of causal inference.

### 1.3 Definitions

| Term | Meaning |
|---|---|
| **Settlement period (SP)** | The GB electricity industry's half-hour trading block. 48 per normal day, 46 or 50 on clock-change days |
| **Carbon intensity** | Grams of CO₂ equivalent emitted per kilowatt-hour of electricity generated (gCO₂/kWh) |
| **Actual** | The realised carbon intensity for a settlement period, published after the fact |
| **ESO forecast** | National Grid ESO's own published forecast for a settlement period — the institutional benchmark |
| **Run** | One execution of the scheduled pipeline, identified by `run_id` |
| **Issue time (`run_at`)** | The instant a forecast was generated. Immutable |
| **Target period** | The settlement period a forecast refers to |
| **Horizon** | Target period minus issue time, in half-hour steps (1–96) |
| **Forecast register** | The append-only table of every forecast ever issued. The project's evidential core |
| **Vintage / point-in-time** | Using only data that was *published* at or before the issue time, never data revised later |
| **Lookahead leakage** | Training or forecasting using information unavailable at issue time. The primary validity threat |
| **Champion / challenger** | The model currently serving, versus a rival model scored in parallel without serving |
| **MASE** | Mean Absolute Scaled Error — MAE divided by the MAE of a seasonal naive baseline. Scale-free and safe near zero |
| **Pinball loss** | The scoring rule for quantile forecasts; the basis for judging prediction intervals |
| **Coverage** | The proportion of actuals that fell inside a stated prediction interval. An 80% interval should contain 80% |
| **Load shifting** | Moving a flexible electrical task to a cleaner or cheaper half hour |

### 1.4 Intended audience

Primarily hiring managers and technical interviewers assessing analytical and
engineering capability. Sections 2, 12 and the decision memo are written to be
read by a non-technical reader; Sections 6–11 are written so a technical reader
can reproduce every published number and audit every claim of accuracy.

---

## 2. Business context and problem statement

### 2.1 Context

GB grid carbon intensity varies between roughly 20 and 400 gCO₂/kWh within a
single day, driven mostly by wind output and demand. Any consumer with a
flexible load — EV charging, heat pumps, battery storage, industrial batch
processes, deferrable compute — can cut the emissions of that load substantially
by choosing *when* to run it, at no capital cost.

Acting on that requires a forecast. Forecasts are freely published. What is not
published, by anyone, is a continuously maintained, out-of-sample record of how
wrong those forecasts turned out to be, broken down by horizon and by condition.

### 2.2 Problem statement

> A load-shifting decision is only as good as the forecast behind it. Published
> forecasts carry no error bars, no accuracy history, and no statement of the
> conditions under which they fail. A user shifting a load 18 hours ahead has no
> basis to judge whether the predicted saving is real or noise, and no way to
> know whether the forecast degrades exactly when it matters most.

### 2.3 Primary business question

**When should a flexible load run, how much carbon and cost does shifting it
actually save, and how much confidence does the forecast record justify?**

Decomposed into answerable sub-questions:

| # | Question | Method |
|---|---|---|
| BQ-1 | How does carbon intensity vary by time of day, week and season, and how much of it is predictable structure? | STL decomposition, descriptive |
| BQ-2 | How accurate is the ESO's published forecast, by horizon, measured out of sample? | Continuous scoring |
| BQ-3 | Can a model using weather and demand beat naive baselines — and can it beat the ESO? | Rolling-origin backtesting, MASE |
| BQ-4 | Are the model's prediction intervals honest, or too narrow? | Pinball loss, empirical coverage |
| BQ-5 | Under what conditions does forecast accuracy break down? | Error segmentation, drift analysis |
| BQ-6 | Is challenger model B genuinely better than champion A, or is the difference noise? | Diebold–Mariano / paired Wilcoxon |
| BQ-7 | What does shifting a defined load actually save, in kg CO₂ and £, net of forecast error? | Cost-benefit under uncertainty |

### 2.4 Success criteria

The project succeeds if a visitor can, without contacting the author:

1. See the current forecast **and** the record of how wrong previous forecasts
   at that same horizon have been.
2. Verify that no published forecast was edited after its target outcome became
   known (FR-19).
3. Read a recommendation that names **what to run, in which half hour, saving
   how much CO₂ and how much money, with what stated uncertainty** — every
   figure traceable to a committed query or script.

**The project does not require that the model beat the ESO forecast.** A
rigorously measured loss, with error decomposition explaining where and why, is
an acceptable and honestly reportable outcome. Section 13, R-4 makes this a
governed risk rather than a hope.

---

## 3. Feasibility study

| Dimension | Assessment |
|---|---|
| **Technical** | Feasible. Three public HTTP APIs, all verified keyless on 2026-08-12. Data volume is low millions of rows; Postgres and scikit-learn handle it on free tiers. No distributed computing, no GPU. |
| **Data** | Feasible. Carbon Intensity API returns forecast *and* actual per settlement period back to at least 2018-05-09 — roughly 140,000 national observations with ground truth already attached. Elexon supplies half-hourly demand in MW with an explicit `publishTime`, enabling point-in-time correctness. Open-Meteo supplies matched hourly weather history and forecasts. |
| **Economic** | Zero cost. Neon Postgres free tier, GitHub Actions (free minutes on a public repository), Render free tier, Vercel hobby tier, all libraries open source. |
| **Schedule** | Feasible for a solo developer across nine milestones at part-time pace, *provided* the milestone boundaries in §12 are respected. This is the largest of the author's three projects and the schedule risk is real (R-8). |
| **Operational** | Feasible but non-trivial — this is the first of the author's projects with a live production dependency. Mitigated by making every pipeline stage idempotent and gap-filling, so a missed or delayed run self-heals rather than requiring intervention. |
| **Ethical/legal** | Carbon Intensity API data is CC BY 4.0 with attribution. Elexon BMRS and Open-Meteo permit non-commercial use. No personal data of any kind is collected, stored, or processed. |

**Verdict: feasible on all dimensions.**

### 3.1 Principal risk to validity

The primary threat is **lookahead leakage**. Because actuals, demand figures and
weather are all revised or published on lags, it is easy to build a model that
appears excellent in backtesting because it silently used information that did
not exist at issue time. Every accuracy claim in this project depends on that
not happening.

This is why the forecast register (FR-14) is append-only, why forecasts are
issued *before* the target period rather than reconstructed afterwards, and why
FR-19 requires a scheduled cryptographic integrity check. Backtested results and
live-scored results are reported in **separate columns, never pooled** — the
live column is the only one that is leakage-proof by construction, and the gap
between the two columns is itself a published finding.

---

## 4. SDLC methodology

**Iterative and incremental**, solo-adapted, with continuous deployment from M0.

Each milestone is independently demonstrable and produces a committed artefact.
Analysis milestones additionally require that **every published number is
reproducible from a committed query or script** — no figures derived in an
unsaved notebook cell.

### 4.1 Walking skeleton first

Unlike the author's previous projects, deployment is not a late milestone. M0
deploys an end-to-end skeleton — Vercel frontend calling a Render API reading a
Neon database, with a green health check — before any analysis exists. Every
subsequent milestone deploys on merge.

**Rationale:** the previous project deferred deployment and then spent
significant effort discovering platform memory and cold-start limits late. Here
the platform constraints are known from day one and the architecture is designed
around them (§9.3).

### 4.2 Definition of Done (applies to every milestone)

1. Artefacts committed to the repository.
2. All automated tests pass — dbt tests, Python tests, linting, type checks.
3. Deployed to production and verified live, not merely merged.
4. Every figure in prose traceable to a committed query or script.
5. Assumptions and limitations recorded, not just results.
6. A milestone summary document written, **including problems found and
   decisions reversed**.

---

## 5. Stakeholders and user characteristics

| Stakeholder | Interest | Implication |
|---|---|---|
| Flexible-load operator (simulated primary user) | Wants to know when to run a load and whether to trust the answer | Recommendation must be specific, costed, and accompanied by its own error record |
| Sustainability analyst | Wants defensible numbers for reporting | Methods and assumptions must be inspectable; uncertainty must be stated, not hidden |
| Non-technical reviewer | Wants the finding in plain language | Decision memo and the accuracy page must stand alone |
| Technical interviewer | Wants to verify rigour and detect overstatement | Leakage controls, immutability guarantees and negative results must be prominent, not buried |

---

## 6. Data source specification

All three sources were probed live on 2026-08-12 and returned HTTP 200 without
credentials. Findings below are observed behaviour, not documentation claims.

### 6.1 Carbon Intensity API (National Grid ESO) — primary source

Base: `https://api.carbonintensity.org.uk` · No authentication · CC BY 4.0

| Endpoint | Grain | Returns |
|---|---|---|
| `/intensity/{from}/{to}` | National, half-hourly | `forecast`, `actual`, `index` |
| `/intensity/date/{date}` | National, one day | As above |
| `/generation/{from}/{to}` | National, half-hourly | Percentage share for 9 fuel types |
| `/regional/intensity/{from}/{to}` | 14 DNO regions, half-hourly | `forecast` **only**, plus regional mix |

**Verified:** `/intensity/date/2018-05-10` returns populated `forecast` and
`actual` pairs, establishing usable history from at least 2018-05-09.

### 6.2 Elexon BMRS Insights — demand and price

Base: `https://data.elexon.co.uk/bmrs/api/v1` · No authentication

| Endpoint | Verified | Provides |
|---|---|---|
| `/demand/outturn` | HTTP 200 | `initialDemandOutturn` and `initialTransmissionSystemDemandOutturn` in MW, per settlement period, **with `publishTime` distinct from `startTime`** |
| `/balancing/pricing/market-index` | HTTP 200 | Market index price — enables the £ component of the recommendation |

Day-ahead demand and wind forecast endpoints returned HTTP 404 at the paths
tried; their correct routes are an **M1 investigation item**, not an assumption.
No requirement in this document depends on them.

### 6.3 Open-Meteo — weather

Base: `https://archive-api.open-meteo.com/v1/archive` (history) and
`https://api.open-meteo.com/v1/forecast` (forward) · No authentication · free
for non-commercial use.

Hourly variables required: `temperature_2m`, `wind_speed_100m`,
`shortwave_radiation`, `cloud_cover`. Sampled at a small fixed set of
generation-weighted GB coordinates, defined and justified in M1.

`wind_speed_100m` is specified rather than 10m because turbine hub height is
the physically relevant level for wind output.

### 6.4 Source characteristics requiring handling

Anticipated here, confirmed or refuted in M2:

- **Regional data has no `actual` field.** Verified directly: regional responses
  carry `intensity.forecast` only. Regional intensity therefore **cannot be
  scored** and no accuracy claim may ever be made about it. It may inform the
  recommendation, labelled as unvalidated. This constraint is load-bearing and
  appears again in NFR-9 and R-6.
- **Clock changes** produce 46- and 50-period days. A model keyed on "period 1
  to 48" silently corrupts two days per year. All time arithmetic is in UTC;
  local time is a derived presentation attribute only.
- **Actuals arrive on a lag and may be revised.** The lag must be measured in
  M2, not assumed, and drives the scoring schedule.
- **The API returns `actual: null` for recent periods**, which is a normal
  pending state, not a data-quality failure. Scoring must distinguish "not yet
  known" from "known to be missing".
- **Weather is hourly; grid data is half-hourly.** The join requires an explicit
  interpolation decision, recorded in M3, not an implicit resample.
- **Elexon publishes an initial outturn that is later superseded.** Because
  `publishTime` is available, features must be built from the vintage available
  at issue time, not the latest revision.
- **Generation mix is percentage share, not MW.** Absolute generation cannot be
  derived from it alone; combining mix percentage with Elexon demand to estimate
  MW by fuel is an approximation and must be labelled as one if used.

---

## 7. Functional requirements

### 7.1 Ingestion and orchestration

| ID | Requirement |
|---|---|
| FR-1 | Ingest national carbon intensity (forecast + actual), generation mix, Elexon demand, market price, and Open-Meteo weather on a scheduled cadence of at most 60 minutes |
| FR-2 | Backfill all sources from 2018-05-09 to present via a parameterised, resumable job that paginates within each source's window limits |
| FR-3 | All loads are idempotent: re-running any window produces no duplicate rows and no altered history |
| FR-4 | Detect and automatically re-fetch gaps left by missed, delayed or failed runs, without manual intervention |
| FR-5 | Record every run in a run-log table capturing `run_id`, source, requested window, rows read, rows written, outcome, duration and error detail |

### 7.2 Warehouse and transformation

| ID | Requirement |
|---|---|
| FR-6 | Persist raw API responses in a source-faithful landing layer before any typing or reshaping |
| FR-7 | Transform via dbt into typed staging models and a dimensional mart layer |
| FR-8 | Fact models covering settlement periods must be **incremental**, not full-refresh, and must correctly handle late-arriving actuals |
| FR-9 | Capture revisions to previously published values using dbt snapshots, preserving the prior value rather than overwriting it |

### 7.3 Data quality and observability

| ID | Requirement |
|---|---|
| FR-10 | Enforce automated tests covering uniqueness of grain, referential integrity, value ranges, settlement-period completeness per day (46/48/50), and fuel percentages summing to 100 within tolerance |
| FR-11 | Publish a freshness metric per source and raise an alert when any source exceeds its stated staleness threshold (NFR-4) |
| FR-12 | Expose a data-quality status surface showing per-source freshness, last successful run, and open test failures |

### 7.4 Forecasting

| ID | Requirement |
|---|---|
| FR-13 | Produce a forecast of national carbon intensity for every settlement period up to 48 hours ahead, with a central estimate and 80% and 95% prediction intervals |
| FR-14 | Write every forecast to an **append-only forecast register** keyed by `(model_version, run_at, target_period)`. Rows are never updated or deleted |
| FR-15 | Construct features **point-in-time correctly**, using only values whose publication time precedes the issue time |
| FR-16 | Train models offline in scheduled jobs and persist versioned artefacts; the serving API must never train (NFR-6) |
| FR-17 | Maintain a model registry recording version, training window, hyperparameters, feature set, code commit hash, and promotion history |

### 7.5 Evaluation and experimentation

| ID | Requirement |
|---|---|
| FR-18 | Automatically score every forecast against the actual once it arrives, computing MAE, RMSE, MASE, pinball loss and interval coverage, segmented by horizon |
| FR-19 | Run a scheduled integrity audit that recomputes a checksum over historical forecast-register partitions and fails loudly if any past row has changed |
| FR-20 | Score, on identical periods and horizons, at minimum: persistence, seasonal naive (t−48), a climatological profile, the ESO published forecast, and the GridCast champion |
| FR-21 | Operate a champion/challenger comparison in which the challenger forecasts live without serving, and promotion is decided by a **pre-registered rule** — a paired test (Diebold–Mariano with small-sample correction, and paired Wilcoxon as a distribution-free check), a minimum sample size fixed before the comparison starts, and Benjamini–Hochberg correction across horizon groups |

### 7.6 Decision and action layer

| ID | Requirement |
|---|---|
| FR-22 | Given a flexible load defined by duration and power draw, identify the optimal start half hour within the next 48 hours and quantify the saving in kg CO₂ and £ |
| FR-23 | Express savings against three explicit counterfactuals: running immediately, running at a uniformly random time, and running at a fixed off-peak heuristic hour |
| FR-24 | Propagate forecast uncertainty into the recommendation, reporting the saving as an interval and disclosing the historical hit rate of the recommendation at that horizon |

### 7.7 Application and API

| ID | Requirement |
|---|---|
| FR-25 | Deploy a public web application presenting the current forecast with prediction-interval fan chart, the accuracy history, the model leaderboard, and the load-shift planner |
| FR-26 | Provide a public accuracy page reporting error by horizon over rolling 7/30/90-day windows for every scored model, including GridCast's own losses where they occur |
| FR-27 | Expose a documented read-only JSON API serving current forecast, historical accuracy, and system status |
| FR-28 | Provide an in-app alert feed for anomalous intensity, source staleness, and detected model drift |
| FR-29 | Serve only precomputed results; no request may trigger model training or a full-history scan |

### 7.8 Communication

| ID | Requirement |
|---|---|
| FR-30 | Publish a decision memo readable without technical background, stating the recommendation, the saving, and the confidence justified by the accuracy record |
| FR-31 | Publish a methods document covering leakage controls, backtesting protocol, and the pre-registered promotion rule |
| FR-32 | Publish negative results — horizons where the model loses to a baseline or to the ESO, and any promoted model later demoted — **above** the summary of successes, following the precedent set in the author's previous project |

---

## 8. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Scheduled ingestion succeeds or self-heals | ≥ 99% of settlement periods present within 6 hours |
| NFR-2 | API response time for precomputed reads | p95 < 500 ms excluding cold start |
| NFR-3 | Reproducibility | Any published figure regenerable from a committed script against the warehouse |
| NFR-4 | Freshness thresholds | Intensity ≤ 2 h, demand ≤ 6 h, weather ≤ 12 h before alerting |
| NFR-5 | Cost | £0 — free tiers only |
| NFR-6 | Serving memory footprint | API container stays within 512 MB; training never occurs in the request path |
| NFR-7 | Warehouse footprint | Stays within Neon free-tier storage; retention policy defined in M3 |
| NFR-8 | Auditability | Every forecast traceable to model version, code commit, and issue time |
| NFR-9 | Honesty of presentation | No accuracy figure displayed without its horizon and sample size; no unscoreable quantity (e.g. regional intensity) presented as validated |
| NFR-10 | Accessibility | WCAG 2.1 AA for the public application; charts readable without relying on colour alone |
| NFR-11 | Security | No secrets in the repository; API keys for third-party services (if ever introduced) held in platform secret stores; read-only database role for the serving API |
| NFR-12 | Rate limiting | Public JSON API rate-limited per IP to protect free-tier quotas |

### 8.1 How NFR-3 and NFR-8 are satisfied together

A forecast row records `model_version` and `code_commit`. The model registry
maps `model_version` to its training window and feature set. The warehouse
retains the vintage of every input via snapshots. Therefore any historical
forecast can be regenerated from first principles and compared to the stored
row — and FR-19's checksum audit proves the stored row was never touched.

---

## 9. Architecture

### 9.1 Layered design

```
Public APIs          Carbon Intensity · Elexon BMRS · Open-Meteo
      │
      ▼
Orchestration        GitHub Actions (cron + backfill + training workflows)
      │                  idempotent · gap-filling · run-logged
      ▼
Landing layer        Raw API payloads, source-faithful, untyped
      │
      ▼
Staging (dbt)        Typed, deduplicated, UTC-normalised, no joins
      │
      ▼
Marts (dbt)          Star schema · incremental facts · snapshots
      │
      ├──────────────► Training jobs ──► Model registry + artefacts
      │                                          │
      │                                          ▼
      │                            Forecast register (append-only)
      │                                          │
      │                                          ▼
      │                            Scoring job ──► Accuracy marts
      ▼                                          │
FastAPI (read-only, Render)  ◄───────────────────┘
      │
      ▼
Next.js (Vercel) — dashboard · accuracy history · planner · alerts
```

### 9.2 Technology decisions and rejected alternatives

| Decision | Chosen | Rejected | Rationale |
|---|---|---|---|
| Orchestrator | GitHub Actions cron | Airflow, Prefect, Dagster | Free, versioned with the code, doubles as CI. Real orchestrators need a host this project cannot fund. The trade-off — imprecise scheduling — is answered by FR-4 rather than by paying for punctuality |
| Warehouse | Neon Postgres, dedicated project | Reusing an existing project, DuckDB, BigQuery | Isolation from the author's other projects; free tier sufficient; SQL-native so dbt applies |
| Transformation | dbt-core, incremental + snapshots | Pure Python, SQL scripts | Lineage, testing and incrementality are the specific skills this project exists to demonstrate |
| Forecasting | statsmodels SARIMAX + scikit-learn gradient boosting | Prophet, deep learning | Prophet's footprint is poor value on a 512 MB container; deep learning is unjustifiable at this data volume and would weaken, not strengthen, the analytical claim |
| Intervals | Quantile regression, pinball-loss optimised | Normal-theory intervals | Intensity errors are skewed and heteroscedastic; symmetric intervals would misstate risk |
| Serving | Read-only FastAPI over precomputed tables | Compute-on-request | Learned constraint: training in the request path is what breaks free-tier containers. Offline training with online reads is also the correct production pattern |
| Frontend | Next.js on Vercel | Streamlit, Tableau Public | The project must be a full-stack product; Tableau cannot express the planner or alert feed |
| Alerts | In-app feed + public JSON | Email/SMS | Email introduces PII, deliverability and cost for no analytical gain |

### 9.3 Free-tier constraints treated as design inputs

| Constraint | Design response |
|---|---|
| Render free instances sleep and cold-start | API serves precomputed rows only; the frontend degrades gracefully during cold start rather than appearing broken |
| Render 512 MB memory | All training in GitHub Actions runners; the API never imports a training stack |
| GitHub Actions cron is best-effort and pauses after 60 days of repository inactivity | Gap-filling ingestion (FR-4) makes lateness harmless; a repository-activity check is part of the operational runbook |
| Neon free-tier storage | Landing-layer retention policy; marts retained in full |

---

## 10. Conceptual data model

**Grain declarations — the single most important design decisions in the project:**

| Model | Grain | Note |
|---|---|---|
| `fct_intensity_period` | One national settlement period | Holds `actual`, `eso_forecast`, and derived error |
| `fct_generation_mix` | One fuel × settlement period | Long format, not nine columns |
| `fct_demand_period` | One settlement period × publication vintage | Vintage in the grain is what makes point-in-time features possible |
| `fct_weather_hour` | One location × hour | Interpolated to half-hourly downstream, explicitly |
| `fct_forecast_point` | One `(model_version, run_at, target_period)` | **Append-only.** The evidential core of the project |
| `fct_forecast_score` | One scored forecast point | Derived; joins the register to actuals once known |
| `dim_settlement_period` | One half hour | UTC-anchored; carries clock-change flags |
| `dim_model_version` | One model version | Registry: training window, features, commit, promotion history |
| `dim_region` | One DNO region | Present but flagged unscoreable per §6.4 |

The forecast register never joins to actuals *in place*. Scoring is a derived
model. This separation is what makes retrospective editing detectable.

---

## 11. Analysis plan and statistical methods

| Stage | Method | Guards against |
|---|---|---|
| Structure | STL decomposition, ACF/PACF, period-of-day and seasonal profiles | Modelling noise as signal |
| Baselines | Persistence, seasonal naive (t−48 and t−336), climatological median | Claiming skill that a trivial rule already has |
| Validation | Rolling-origin (walk-forward) backtesting with a fixed gap between train and target | Leakage across the split boundary |
| Point accuracy | MAE, RMSE, **MASE** | MAPE instability near low intensity |
| Interval accuracy | Pinball loss, empirical coverage of 80%/95% intervals | Confident-looking intervals that are simply too narrow |
| Model comparison | Diebold–Mariano with Harvey–Leybourne–Newbold correction; paired Wilcoxon as a distribution-free check | Declaring a winner from noise on autocorrelated errors |
| Multiplicity | Benjamini–Hochberg across horizon groups | Finding a "significant" horizon by testing 96 of them |
| Drift | PSI/KS on feature distributions; CUSUM on the error series | Silent degradation as seasons and grid mix change |
| Error segmentation | Accuracy by horizon, season, wind share, demand level, ramp magnitude | A single headline number hiding failure where it matters |
| Decision | Expected saving under the forecast distribution, against three counterfactuals | Overstating benefit by assuming a perfect forecast |

**Pre-registration.** The promotion rule, minimum sample size and significance
level for FR-21 are committed to the repository *before* the first challenger
runs, and the commit is referenced in the results. A rule written after seeing
the outcome is not a rule.

---

## 12. Milestone plan

| ID | Milestone | Exit criterion |
|---|---|---|
| **M0** | Foundation & walking skeleton | Repo, CI, Neon project, and a deployed Vercel→Render→Neon skeleton with a green health check. Pre-registration document committed |
| **M1** | Ingestion & backfill | All three sources ingesting on schedule; full history 2018→present loaded; run-log populated; gap-fill demonstrated by deliberately skipping a run. Elexon forecast-endpoint investigation closed |
| **M2** | Data-quality audit | Committed queries quantifying actuals lag, revision behaviour, clock-change handling, missing periods and mix-sum tolerance. Deferred design decisions resolved |
| **M3** | Warehouse | dbt incremental marts + snapshots + full test suite green; retention policy applied |
| **M4** | Baselines & backtesting harness | Rolling-origin harness with all baselines and the ESO benchmark scored on history; leakage controls tested |
| **M5** | Live forecasting loop *(first complete vertical slice)* | Forecasts issued on schedule to the append-only register, scored automatically on arrival, integrity audit running, accuracy visible in production |
| **M6** | Modelling depth | SARIMAX and gradient-boosting models with quantile intervals; coverage validated; error segmentation complete |
| **M7** | Champion/challenger & monitoring | Live challenger running under the pre-registered rule; drift detection and alert feed in production |
| **M8** | Product & communication | Full application — planner, accuracy page, alerts, documented API — plus decision memo, methods document, and published negative results |

M5 is the point at which the project is defensible even if later milestones
slip: a deployed service issuing forecasts and grading itself in public is
already the project's central claim.

---

## 13. Risks and mitigations

| ID | Risk | Mitigation |
|---|---|---|
| R-1 | **Lookahead leakage** invalidates every accuracy claim | Vintage in the demand grain; point-in-time feature construction (FR-15); live scoring reported separately from backtests; gap between them published |
| R-2 | Retrospective editing of forecasts, or the appearance of it | Append-only register; scheduled checksum audit (FR-19); public statement of the guarantee |
| R-3 | Upstream API changes shape or disappears | Landing layer stores raw payloads; schema tests fail loudly; ingestion isolated per source so one failure does not halt the rest |
| R-4 | **The model loses to the ESO forecast** | Reframed as an outcome, not a failure: the deliverable is the scoreboard. FR-32 requires losses published above successes. A win claimed without this framing would be the actual failure |
| R-5 | Scheduler unreliability produces gaps | FR-4 gap-filling; ≥99% coverage target (NFR-1) rather than a punctuality target |
| R-6 | Regional intensity presented as validated when it cannot be | NFR-9; regional surfaces carry a permanent unvalidated label |
| R-7 | Free-tier limits breached as history accumulates | Retention policy in M3; storage tracked as an operational metric |
| R-8 | **Scope exceeds available time** | M5 defined as a defensible stopping point; M6–M8 additive. No milestone depends on a later one |
| R-9 | Data volume overstated in communication | The dataset is ~140k national periods plus derived rows — low millions. Documentation states this plainly; no "big data" claim is made anywhere |
| R-10 | Multiple-comparison abuse across 96 horizons | Benjamini–Hochberg correction; horizon groups fixed in the pre-registration |

---

## 14. Acceptance criteria

The project is complete when all of the following hold in production:

1. All three sources ingest on schedule, with ≥99% settlement-period coverage
   over the preceding 30 days, and a demonstrated self-heal after an induced gap.
2. The warehouse builds incrementally with a fully green dbt test suite.
3. Forecasts for every horizon to 48 hours are issued on schedule and written to
   the append-only register.
4. Every forecast older than the actuals lag is automatically scored, and the
   integrity audit has run without failure since M5.
5. The public accuracy page reports GridCast, the ESO forecast and all baselines
   on identical periods, with horizon and sample size shown against every figure.
6. A challenger has run live under the pre-registered rule, and the promotion or
   non-promotion decision is documented with its test statistic.
7. Interval coverage is measured and reported against nominal, whether or not it
   is favourable.
8. The load-shift planner returns a costed recommendation with an uncertainty
   interval and a historical hit rate.
9. The decision memo, methods document and negative-results section are
   published, with losses stated before wins.
10. The application meets WCAG 2.1 AA and the JSON API is documented and
    rate-limited.

---

## 15. Document control

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-08-12 | Muhammad Haris Khokhar | Initial specification. Data-source claims in §6 verified against live APIs on this date |

**Verification note.** All endpoint behaviours recorded in §6 — including the
absence of an `actual` field on regional intensity and the presence of a
distinct `publishTime` on Elexon demand — were observed directly rather than
taken from documentation, and materially shaped §7 and §10.
