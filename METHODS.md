# GridCast — Methods

**How the numbers are produced, and what would invalidate them.**

Read §1 first. It is the list of things this project got wrong or cannot yet
claim, and it is at the top deliberately (SRS FR-32). A methods document whose
limitations live in an appendix is a sales document.

---

## 1. What has gone wrong, and what is still not known

### 1.1 Failures found and fixed

| What happened | Consequence | Found by |
|---|---|---|
| A staging rule ordered only by fetch time, and one settlement period arrived twice in one response with conflicting values | Two identical warehouse builds could have disagreed about a published figure | M2 audit query B02 |
| The ESO forecast field carries 16 physically impossible values, up to 13,579 gCO₂/kWh | Inflated the benchmark's RMSE to 9× its MAE | M4, from an implausible RMSE/MAE ratio |
| Quantile intervals were overconfident: nominal 80% covered 59–63% | Every stated uncertainty understated the risk | M6, by measuring coverage |
| A maturity rule measured stability from the backfill time | Marked all 144,761 periods immature; the scoring job would have found nothing to score | M3, by inspecting counts rather than trusting a green build |
| A model materialised as a table instead of incrementally | Destroyed 434,592 rows of weather history in a step that reported success | M8 retention, after the fact |
| The test suite could write to the production register | Four test forecasts entered the live evidential record | M6, while investigating an unexpected model in the register |
| A weather-location decision was made on 44% of the history | Wrong process, right answer — retracted and redone | M4, by a cross-check that disagreed |

Every one of these is written up in the milestone summary that found it,
including the ones that make the project look careless.

### 1.2 What cannot yet be claimed

**The challenger's live figures do not measure the challenger.** As of
2026-09-04 the accuracy page publishes every horizon group: 14,513 scored
points, all issued before the outcome existed. Three of the four series mean
what they appear to mean. G2's does not.

Every G2 point in the register was issued between 12 and 15 August, when
issuing read weather from the vintage relation — which, being backward-looking
by construction, holds no row for any period being forecast. The forward
weather features were NaN at every horizon, and gradient boosting accepts NaN
silently, so the model issued and scored as though nothing were wrong. Those
are the scores of G2 without the inputs that distinguish it from the baselines,
and they are not comparable to its backtest of MASE 0.46–0.55. The interval
coverage says the same thing independently: 42–51% against a nominal 80%.

The rows stay in the register, which cannot be edited and should not be. They
are an accurate record of what was issued. They are simply not a measurement of
the model, and this section exists so that nobody reads them as one. G2 resumed
issuing with live forecast weather on 2026-09-04; its record starts there.

**The champion's and the benchmark's figures do stand.** B0, B1 and
ESO_published have issued every run since 2026-08-12 and take no weather
features, so nothing about the fault above touches them.

**The hit rate does not exist yet.** Whether a recommendation actually lands in
a good window — the thing that decides if the product works — is unmeasured.

**The ESO comparison in backtesting is not fair.** The stored ESO forecast is
their final near-term value, not a 48-hour-ahead one; 33 of 46 future periods
were revised within two hours of being recorded. A backtest therefore compares
GridCast at 48 hours against the ESO at something much shorter. Only the live
scoreboard compares like with like.

**The maturity threshold is a placeholder.** The ESO actuals lag needs forward
observation and has not accumulated.

**Interval coverage is 5 points short of nominal.** 74.9% against 80% after
conformal calibration, up from 59–63%. The residual is distribution shift, and
tuning it away against the test set would buy a better number and a worse model.

---

## 2. The central claim, and what protects it

Every forecast is written to an append-only register **before its outcome
exists**, and scored automatically once the actual arrives.

| Protection | Mechanism |
|---|---|
| Forecasts cannot be edited | The application role holds `INSERT` on the register and has no `UPDATE` or `DELETE` — a database grant, not a convention |
| Backdating is impossible | `CHECK (target_sp_start_utc > run_at_utc)` |
| Issue time is not rounded | Rounding down would claim up to 29 minutes more lead time than the model had |
| Tampering is detectable by outsiders | Monthly hashes over the register are committed to git; the live database can be checked against public history |
| The seal has been shown to work | Verified by appending a row after sealing and confirming the audit fails loudly with a non-zero exit |

---

## 3. Leakage controls

Leakage makes a model look **better**, not broken. It is invisible in results,
so it is prevented by construction and tested directly.

**Features are relative to issue time, never to the target.** "Intensity 24
hours before the target" is not a legal feature at a 48-hour horizon, because
that period has not happened when the forecast is made.

**Three sources are barred:**

- `stg_om_archive` — reanalysis weather. What the weather *turned out* to be.
- `fct_demand_current` — demand at its latest revision rather than the vintage
  known at issue time.
- `intensity.index` — the ESO band, whose thresholds move about 10 gCO₂/kWh a
  year, so it encodes the publication year as much as the intensity.

**Tests, not assertions.** The leakage suite builds a series whose value equals
its own index position, so a returned value names exactly which period a model
reached for. A test that only checks plausibility cannot tell a correct lookup
from one that reached a day into the future.

**The embargo.** Training data ends 24 hours before the issue time. Without it,
an origin trains on actuals that would still have been pending — the most common
leakage in time-series work, and one that flatters short horizons specifically.

---

## 4. Backtesting protocol

Rolling-origin, walk-forward, never a random split. Origins step 24 hours;
each issues forecasts for the next 48 hours using only what was observable, and
is scored against what happened.

**Metrics:** MAE, RMSE, bias, MASE, pinball loss, empirical coverage, interval
width.

**MAPE is deliberately absent.** GB intensity reaches single digits on windy
nights, where percentage error explodes; a headline MAPE would be driven by the
calmest hours of the cleanest days.

**Backtest and live results are never pooled.** They live in separate database
schemas so combining them requires an explicit cross-schema join. Two
independent reasons: backfilled rows carry reconstructed knowability, and the
ESO benchmark is not horizon-matched in history.

---

## 5. Model promotion

Fixed in `PREREGISTRATION.md` **before any model existed**, and referenced by
commit hash in every decision.

- Diebold–Mariano on absolute-error differentials, Harvey–Leybourne–Newbold
  corrected, with a paired Wilcoxon confirmation; both must agree in direction
- Minimum 1,440 scored points per horizon group before any test is computed
- α = 0.05, Benjamini–Hochberg adjusted across four horizon groups
- Promotion needs a win in ≥3 of 4 groups and no significant loss in any
- Interval coverage may not degrade more than 2 points regardless of point gains
- 14-day cooldown after any promotion

**No optional stopping.** The test is computed once, when the threshold is
reached — not recomputed daily until it passes.

**Non-promotions are published with the same prominence as promotions.** A
registry containing only successful promotions is evidence that failures went
unrecorded.

This is why a model that halves the incumbent's error is currently a challenger
rather than the champion.

---

## 6. A model that uses the benchmark is not competing with it

A model taking the ESO forecast as an input and then beating the ESO has not
out-forecast the grid operator — it has bias-corrected them. Such a model may
never appear in the same table as the ESO benchmark, and the registry records
`uses_eso_forecast` for exactly this reason.

The headline comparison is always an ESO-free model against the ESO.

---

## 7. Reproducibility

Every published figure is regenerable from a committed script against the
warehouse. Audit queries live in `audit/`, run by `python -m gridcast.audit`.
Nothing is quoted from a notebook cell that no longer exists.

Forecast rows record the model version, the code commit, and a hash of the exact
feature vector used, so a disputed forecast can be recomputed from the
warehouse's vintage history and compared.

---

## 8. Known limits of the data

- **179 settlement periods never existed upstream** — verified by re-requesting
  each window. Recorded as permanently absent so the pipeline stops chasing them.
- **625 periods have a permanently null actual** and can never be scored.
- **43 periods have no ESO forecast**, and are excluded from the comparison for
  *every* model, not just the ESO.
- **Regional intensity carries a forecast and no actual.** It can never be
  scored, and is labelled unvalidated wherever it appears.
- **Raw payloads are pruned** to a short retention window once typed into the
  marts. History before that window is reproducible from the marts, not from raw.
