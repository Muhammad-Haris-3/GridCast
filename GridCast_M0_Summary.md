# GridCast — M0 Summary: Foundation and Walking Skeleton

**Milestone:** M0
**Date:** 2026-08-12
**Status:** **Complete and verified in production.**

| Surface | URL |
|---|---|
| Application | https://grid-cast-sigma.vercel.app |
| API | https://gridcast-api-xhca.onrender.com |
| Repository | https://github.com/Muhammad-Haris-3/GridCast |
| Warehouse | Neon, AWS `us-east-2`, PostgreSQL 18.4 |

---

## 1. Exit criterion

> Repo, CI, Neon project, and a deployed Vercel→Render→Neon skeleton with a green
> health check. Pre-registration document committed.

**Met locally in full; the cloud half is blocked only on account creation**, which
requires credentials this environment does not have. Everything that can be
verified without those accounts has been verified against a real PostgreSQL 17
instance, not a mock. §6 lists exactly what remains.

---

## 2. What was built

| Area | Artefact |
|---|---|
| Requirements | `GridCast_SRS_v1.0.md` — FR-1→32, NFR-1→12, M0–M8, R-1→10 |
| Design | `GridCast_Design_Phase_v1.0.md` — every table, grain, incremental strategy, test |
| **Pre-registration** | `PREREGISTRATION.md` — promotion rule frozen **before any model exists** |
| Schema | `sql/001`–`004` — four layers, run log, register, roles and grants |
| Pipeline core | `gridcast/` — settings, database, retrying HTTP client, run-log context manager, migration runner |
| Warehouse | `dbt_gridcast/` — settlement-period spine plus 13 tests |
| API | `api/` — read-only FastAPI, `/health` and `/v1/status` |
| Frontend | `web/` — Next.js App Router, landing and status pages |
| CI | `.github/workflows/ci.yml` — lint, format, DDL, dbt build, pytest against a real `postgres:16` service |
| Deployment | `render.yaml` blueprint |

---

## 3. Verification performed

All run against PostgreSQL 17.6 locally, not stubs.

| Check | Result |
|---|---|
| `python -m gridcast.migrate` | 4 files applied; **re-run produced no changes**, confirming idempotency |
| `dbt build` | **14/14 pass** (1 model, 13 tests) |
| `pytest` | **14/14 pass**, including 6 database-backed register tests |
| `ruff check` / `ruff format --check` | Clean |
| `npm run typecheck` / `npm run build` | Clean; 3 routes built |
| `npm audit` | **0 vulnerabilities** |
| API `/health` | 200, no database dependency |
| API `/v1/status` | 200, `database: ok`, `readonly_role_in_use: true` |
| Browser → Next.js → FastAPI → Postgres | Verified end to end; spine and warnings rendered correctly |

### 3.1 The append-only guarantee, actually tested

The six register tests connect as a **member of `gridcast_app`**, not as the
owner or a superuser — both of which bypass grants and would make the tests pass
while proving nothing.

| Test | Result |
|---|---|
| App role **can** `INSERT` a forecast | ✅ |
| App role **cannot** `UPDATE` a forecast | ✅ `InsufficientPrivilege` |
| App role **cannot** `DELETE` a forecast | ✅ `InsufficientPrivilege` |
| Backdated forecast (`target <= run_at`) rejected | ✅ `CheckViolation` |
| Crossed quantiles rejected | ✅ `CheckViolation` |
| Same (model, issue time, target) issued twice rejected | ✅ `UniqueViolation` |

The insert test passing alongside the update test failing is what makes this
meaningful: the role genuinely has write access and is genuinely denied the
ability to rewrite history.

### 3.2 The spine is correct across clock changes

147,696 half-hourly periods from 2018-05-09 to 2026-10-10.

```
settlement_date_local | periods
2018-10-28            |      50
2019-03-31            |      46
2019-10-27            |      50
2020-03-29            |      46
2020-10-25            |      50
```

Seven fully covered calendar years, each containing **exactly two** non-48-period
days. That coverage count matters: a clock-change test computed over zero
qualifying years would pass vacuously, which is worse than having no test. It
was checked explicitly rather than inferred from a green tick.

---

## 4. Problems found

Five, all fixed. Recorded because a milestone summary that reports only
successes is not evidence that nothing went wrong.

### 4.1 `date_trunc` on a `timestamptz` cannot be indexed

`CREATE INDEX ... (date_trunc('month', run_at_utc))` failed with *"functions in
index expression must be marked IMMUTABLE"*.

`date_trunc(text, timestamptz)` is only `STABLE` — it depends on the session
timezone. **This was more than an indexing inconvenience.** The register is
sealed by month, so a month boundary that moves with the server's timezone would
partition the seal differently on different machines and produce a mismatching
hash over identical data — an integrity failure with no tampering behind it.

Fixed by casting to a naive UTC timestamp inside the index expression, which
pins the boundary and restores immutability.

### 4.2 Session timezone leaked into both the API and the warehouse

The API returned `2018-05-09T05:00:00+05:00` — correct instant, wrong contract,
since every time rule in the design is UTC-anchored.

Investigating it surfaced a worse instance of the same fault: the spine's forward
boundary is `date_trunc('day', now())`, which dbt had evaluated in local time.
The spine was ending **five hours early**, short by exactly 10 half-hour periods.
A silent five-hour shortfall at the forward edge is precisely where 48-hour
forecast targets live.

Fixed at two levels — `ALTER DATABASE ... SET timezone TO 'UTC'` so every client
(dbt, psql, psycopg) inherits it structurally, plus an explicit `SET TIME ZONE
'UTC'` per connection so the guarantee survives a database restored without the
setting. After the fix the boundary is exactly `23:30:00+00` and the count moved
by exactly the 10 missing periods.

### 4.3 Next.js 15.1.3 carried 3 vulnerabilities, one critical

The pinned version predated a large batch of advisories (cache poisoning, SSRF,
RCE in the React flight protocol, middleware authorization bypass). Bumped to
16.3.0 with matching React 19.2.8; audit now reports **0 vulnerabilities**, and
typecheck and build both pass on the new major.

### 4.4 A `DO $$` block cannot take bind parameters

The test fixture creating the probe role failed with *"could not determine data
type of parameter $1"*: the body of a `DO` block is a string literal, so
placeholders never reach the planner. Rewritten using `psycopg.sql.Identifier`
and `Literal` composition.

### 4.5 A dbt test referenced a package that is not yet a dependency

`schema.yml` used `dbt_utils.accepted_range` behind `enabled: false`, but dbt
resolves macro references at parse time regardless. Removed; the same range is
already asserted by the singular test `assert_periods_per_local_day`.

---

## 5. Decisions taken during M0

| Decision | Choice | Reason |
|---|---|---|
| Spine primary key | `sp_start_utc` | `(settlement_date, settlement_period)` breaks twice a year on clock-change days |
| Landing idempotency | Insert-if-changed on payload hash | Yields idempotency and revision history from one mechanism |
| Register ownership | Python-written, dbt source only | Anything dbt can rebuild is not evidence |
| Two database roles | Separate pipeline and serving credentials | If the API holds write credentials, the guarantee rests on it choosing not to use them |
| `is_gb_holiday` | Present, always `false`, with `is_gb_holiday_resolved` beside it | A null would poison downstream booleans silently; a flagged placeholder cannot be mistaken for real data |
| Deployment timing | Skeleton first, before any analysis | The previous project discovered platform memory limits late and paid for it |

---

## 6. Production verification

All six deployment steps are complete. Confirmed live at
https://grid-cast-sigma.vercel.app/status:

| Check | Result |
|---|---|
| Database | `ok` |
| Read-only serving role | `IN USE` |
| Spine periods | 147,696 |
| Spine first period | `2018-05-09 00:00Z` |
| Build | `fec76b17ecaa` — a real commit, not `local` |
| API `/health` | 200 |

The Definition of Done (SRS §4.2) required production verification rather than a
successful merge. That bar is now met.

### 6.1 Findings from the deployment itself

Four further problems, found only because the deployment was actually performed
rather than assumed to work.

**6.1.1 CI had never once imported the project.** Every run since the repository
was created had failed with `ModuleNotFoundError` for both `gridcast` and `api`.
The cause was the invocation: `python -m pytest`, used locally, puts the working
directory on `sys.path`; the `pytest` console script that CI runs does not. Two
of four test modules failed at collection, so the only assertions ever executed
in CI were the register tests — and the suite had never imported the code it
claims to verify. Fixed with `pythonpath` in the pytest configuration, which
works under both invocations rather than papering over it in the workflow.

The same class of bug was then checked on the deployment path, since Render
starts the API with the `uvicorn` console script. It does not apply: uvicorn
inserts its `--app-dir` (default `.`) into `sys.path`. Verified by running that
exact command and getting 200 from `/health`.

**6.1.2 A public endpoint served a database password.** During the first Render
deployment the read-only connection string was pasted into `GRIDCAST_ENV`
instead of `GRIDCAST_READONLY_DATABASE_URL`. `/health` reports `env` verbatim
and is public and unauthenticated, so the credential — password included — was
served on the open internet until the variable was corrected.

The operator error is ordinary and will recur. Publishing the result of it was
the defect, and it belonged to the application. `env` is now validated as a
short lowercase label and replaced with `misconfigured` if it is anything else,
so a value never intended to be public cannot reach a response; `/v1/status`
raises a warning stating that any credential pasted there must be treated as
exposed. The affected password was rotated.

Rejecting the value at startup was considered and refused: it would take the
service down over a cosmetic field and remove the very status page that explains
what is wrong.

**6.1.3 A false failure in the bootstrap script.** The timezone check compared
the zone *name* to `UTC` and failed against Neon, which reports the identical
zero-offset zone as `GMT`. Widening it to accept both names would have admitted
`Europe/London`, which is also zero-offset in January and then shifts an hour in
July. The check now probes the zone at two instants six months apart and
requires zero offset at both — verified to accept `UTC` and `GMT` and to reject
`Europe/London` and `Asia/Karachi`.

**6.1.4 Re-running the bootstrap script rotated a deployed password.** Anyone
re-running it to re-verify a database would have broken the live credential at
the moment they believed they were confirming things worked. It now leaves an
existing login untouched unless `--readonly-password` or `--rotate-password` is
given, and reports the read-only checks as *skipped* rather than passing them
when it cannot log in.

### 6.2 Environment corrections

- Render region moved to Ohio to sit beside the Neon project in `us-east-2`.
  Every request the API serves is a database round trip; splitting them across
  the Atlantic would have added one to each, on an instance already cold-starting.
- CI Postgres moved from 16 to 18 to match the Neon server (18.4).
- CI failures now publish into a GitHub annotation. GitHub requires sign-in to
  read job logs even on a public repository, but annotations are readable
  anonymously — so a build failure is diagnosable without an account, which is
  the same principle the forecast seals run on. This is how 6.1.1 was found.

---

## 7. Next milestone

**M1 — Ingestion and backfill.** Exit criterion: all three sources ingesting on
schedule, full history from 2018 loaded, run log populated, and gap-fill
demonstrated by deliberately skipping a run. Includes closing the open
investigation into the Elexon day-ahead demand and wind forecast endpoint routes,
which returned 404 at the paths tried during specification (SRS §6.2) and which
no requirement currently depends on.
