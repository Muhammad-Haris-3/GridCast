# GridCast — Pre-Registration of the Model Promotion Rule

**Committed:** M0, before any model, feature or challenger exists.
**Status:** Frozen. Amendable only by the procedure in §7.
**Governs:** SRS FR-21, design doc §11.3.

---

## 0. Why this document exists

A decision rule written after seeing the result is not a rule; it is a
description of what already happened. This document fixes the rule for promoting
a challenger model to champion **before any model has been trained**, so that
every promotion decision GridCast later publishes can be checked against a
standard that could not have been chosen to suit the outcome.

Every published promotion or non-promotion cites the commit hash of this file as
it stood when the comparison **began**. If that hash differs from the current
file, §7 requires the reason to be visible in the change log below.

---

## 1. What is being compared

Two models forecast live simultaneously:

- the **champion**, whose forecasts the application serves, and
- the **challenger**, whose forecasts are written to the append-only register and
  scored identically, but are never presented as the product's answer.

Both write to `register.reg_forecast_point` under their own `model_version`.
Nothing about a forecast row records which was which — that is held in
`marts.dim_model_version.role` with effective dates, so role history cannot be
back-edited into the evidence.

## 2. The outcome measure

The **absolute error** of the point forecast against the matured actual, in
gCO₂/kWh, on the set of target periods for which **both** models produced a
forecast at the **same issue time**. Periods forecast by only one model are
excluded, not imputed.

Loss differential for period *i*: `d_i = |e_champion,i| − |e_challenger,i|`.
Positive `d_i` favours the challenger.

## 3. Horizon groups

Fixed here, in advance. All tests are computed within groups, never on
individual horizons.

| Group | Horizons (half-hour steps) | Hours ahead |
|---|---|---|
| `H1` | 1–6 | 0–3 |
| `H2` | 7–24 | 3–12 |
| `H3` | 25–48 | 12–24 |
| `H4` | 49–96 | 24–48 |

**Rationale:** testing all 96 horizons separately would produce a "significant"
winner from multiplicity alone. Four groups reflect genuinely different
forecasting regimes — persistence-dominated, intraday, day-ahead, and
multi-day — rather than being chosen to produce a convenient number of tests.

## 4. The test

**Primary.** Diebold–Mariano on `d_i` within each horizon group, with the
Harvey–Leybourne–Newbold small-sample correction, using a
heteroscedasticity-and-autocorrelation-consistent variance estimate with lag
truncation `h − 1`, where `h` is the group's maximum horizon.

**Confirmatory.** Paired Wilcoxon signed-rank on the same differentials, as a
distribution-free check. Forecast errors are skewed and heavy-tailed; a result
that depends on the normality assumption is not a result.

**Both tests must agree in direction.** Disagreement is reported as an
inconclusive outcome, and the challenger is not promoted.

**Significance.** α = 0.05, two-sided, adjusted across the four horizon groups by
the Benjamini–Hochberg procedure.

## 5. Minimum sample and timing

- **No test is computed** until each horizon group holds at least **1,440 scored
  forecast points** — approximately 30 days of live operation.
- Only **matured** actuals count (design doc §6.2.1). Pending periods are not
  scored and do not accrue toward the threshold.
- The test is computed **once**, when the threshold is reached. It is not
  recomputed daily until it passes. Repeatedly testing an accumulating sample and
  stopping at the first significant result is optional stopping, and it inflates
  the false-positive rate far beyond the stated α.
- If the result is inconclusive, the comparison may be **restarted** with a fresh
  sample, which is disclosed as a second comparison rather than a continuation of
  the first.

## 6. The promotion decision

The challenger is promoted **only if all** of the following hold:

1. It is better (positive mean `d_i`) in **at least 3 of the 4** horizon groups
   at BH-adjusted *p* < 0.05.
2. It is **not significantly worse** in any horizon group at BH-adjusted
   *p* < 0.05.
3. Both the Diebold–Mariano and Wilcoxon tests agree in direction in every group
   counted toward condition 1.
4. **For models producing prediction intervals:** empirical 80% coverage does not
   fall more than 2 percentage points below the champion's, regardless of any
   point-accuracy gain. A model that becomes more accurate on average while
   understating its own uncertainty is a worse product, not a better one.

**Cooldown.** After any promotion, 14 days must pass before a new comparison
begins. Without it, a champion could be replaced on a single fortnight's weather.

**Demotion.** There is no automatic demotion. If a champion's rolling MASE
degrades past the drift threshold, an alert is raised and a new challenger
comparison is opened under this same rule. Automatic reaction to a drift signal
is how a system chases seasonal noise into a worse model.

## 7. Amendment procedure

This document may be amended only:

- **before** a comparison begins, never during one, and
- with the reason recorded in §9, and
- with any in-flight comparison abandoned and restarted under the new rule.

An amendment made while a comparison is running invalidates that comparison. It
does not invalidate comparisons already concluded under the previous version.

## 8. What gets published

Every evaluation is published, whether or not it promotes:

- the test statistics and adjusted *p*-values for all four horizon groups,
- the sample size per group,
- the decision, and
- the commit hash of this file as it stood when the comparison began.

**Non-promotions are published with the same prominence as promotions.** A
repository containing only successful promotions is evidence that failures went
unrecorded, not that none occurred (SRS FR-32).

## 9. Change log

| Version | Date | Change | Reason | In-flight comparison |
|---|---|---|---|---|
| 1.0 | 2026-08-12 | Initial pre-registration | — | None |
