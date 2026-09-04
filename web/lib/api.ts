/**
 * API access.
 *
 * The Render free tier sleeps after inactivity, so the first request of the day
 * can take 30+ seconds to wake the container. That is normal, not an outage —
 * the UI has to say "waking up" rather than "failed", or every cold start looks
 * like a broken deployment.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type SpineSummary = {
  periods: number;
  first_period_utc: string;
  last_period_utc: string;
} | null;

export type RunLogEntry = {
  source: string;
  job: string;
  status: string;
  started_at_utc: string;
  finished_at_utc: string | null;
  rows_read: number;
  rows_written: number;
};

/** Estimated database egress for the current billing period.
 *
 * Null when the counter is unavailable — it postdates the outage it exists to
 * predict, so a deployment without sql/008 applied is a real state, not a bug.
 */
export type TransferBudget = {
  period_start_utc: string;
  bytes_estimated: number;
  bytes_estimated_human: string;
  budget_bytes: number;
  budget_human: string;
  fraction_used: number;
  rows_returned: number;
  runs_recorded: number;
  state: "ok" | "warn" | "over";
  estimate_note: string;
} | null;

/** A numeric column, as it actually arrives over the wire.
 *
 * Postgres `numeric` becomes a Python Decimal, and a Decimal is serialised to
 * JSON as a STRING to preserve its precision: the accuracy payload really
 * contains `"mae": "21.577"`, quoted. Declaring these as `number` was a lie
 * that TypeScript could not catch, because the lie was on the far side of a
 * `fetch`.
 *
 * It survived three weeks because JavaScript coerces silently in arithmetic
 * and comparison — `mase < 1` and `value * 100` are correct with a string on
 * the left — and every route through this file that would not coerce was
 * unreachable while no horizon group had 200 scored points. When the first one
 * crossed the threshold on 2026-09-04, `r.mae?.toFixed(2)` finally ran,
 * `.toFixed` is a method on Number and not on String, and /accuracy is
 * prerendered at build time. So a data threshold being crossed failed the
 * production build, and the site stopped deploying at all.
 *
 * Typed as the union it really is, so that calling a Number method on one is a
 * compile error rather than a deploy failure. Nothing may use these directly:
 * `normaliseRow` is the only way in. */
type Numeric = number | string | null;

/** The accuracy payload exactly as the API and the snapshots publish it. */
export type WireAccuracyRow = {
  model_version: string;
  horizon_group: string;
  n: number;
  mae: Numeric;
  rmse: Numeric;
  mase: Numeric;
  coverage_80: Numeric;
  interval_width_80: Numeric;
  publishable: boolean;
};

/** The same row after the boundary, with real numbers. What this file renders. */
export type AccuracyRow = {
  model_version: string;
  horizon_group: string;
  n: number;
  mae: number | null;
  rmse: number | null;
  mase: number | null;
  coverage_80: number | null;
  interval_width_80: number | null;
  publishable: boolean;
};

function num(value: Numeric): number | null {
  if (value == null) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  // NaN rather than a crash is the wrong trade here: a figure that cannot be
  // parsed is a figure this page must not print, and null already means "not
  // publishable" everywhere below.
  return Number.isFinite(parsed) ? parsed : null;
}

/** Convert one wire row at the boundary, once.
 *
 * Once rather than at each use: the bug this fixes was not that a conversion
 * was wrong, it was that six call sites each decided for themselves whether
 * one was needed, and five of them happened to be right.
 */
export function normaliseRow(row: WireAccuracyRow): AccuracyRow {
  return {
    ...row,
    mae: num(row.mae),
    rmse: num(row.rmse),
    mase: num(row.mase),
    coverage_80: num(row.coverage_80),
    interval_width_80: num(row.interval_width_80),
  };
}

/** Scored forecasts deliberately left out of the published accuracy figures.
 *
 * A model that issued in a configuration it was not built for is not the model
 * the scoreboard claims to measure, and pooling those scores with valid ones
 * produces a number describing neither. The warehouse excludes them; this is
 * how the page can say so. An exclusion nobody can see is an edit. */
export type ExcludedScores = {
  model_version: string;
  reason: string;
  n_excluded: number;
  first_issued: string;
  last_issued: string;
  first_target: string;
  last_target: string;
};

export type SystemStatus = {
  /** Which process produced this payload.
   *
   * The API answers for itself; gridcast.snapshot answers from a CI runner so
   * the site can render without waking it. Most fields are warehouse facts and
   * read the same either way — env and readonly_role_in_use are not, and are
   * null from the pipeline rather than filled in with the runner's own
   * configuration. Absent on snapshots published before 2026-09-04. */
  reported_by?: "serving" | "pipeline";
  /** Null unless the serving API is the reporter. */
  env: string | null;
  commit: string;
  milestone: string;
  database: "ok" | "unreachable";
  /** Null unless the serving API is the reporter. */
  readonly_role_in_use: boolean | null;
  warnings: string[];
  spine: SpineSummary;
  recent_runs: RunLogEntry[];
  transfer: TransferBudget;
  detail?: string;
  diagnosis?: string;
  driver_message?: string;
};

/** Why the data is missing, in one sentence, or null if the API itself is down.
 *
 * Every page that cannot load data used to blame a sleeping free-tier
 * container. That is one cause among many and it was the wrong one during the
 * outage this was written in: the API was up and answering, and the database
 * was refusing it. Telling a reader the service is asleep when it is awake
 * sends them to wait for something that will not happen. */
export async function unavailableReason(): Promise<string | null> {
  const status = await getStatus();
  if (!status) return null; // The API really is down; the caller's default fits.
  if (status.diagnosis) return status.diagnosis;
  if (status.warnings.length > 0) return status.warnings[0];
  if (status.database === "ok") {
    // Both layers up and the view still empty. Saying "it is waking" here
    // would send the reader to reload for something a reload cannot fix.
    return (
      "The API and the database are both up, so this is not a cold start. " +
      "This view's data has not been produced yet — the pipeline status page " +
      "shows the most recent runs."
    );
  }
  return null;
}

export async function getStatus(): Promise<SystemStatus | null> {
  try {
    const response = await fetch(`${API_BASE}/v1/status`, {
      // Status is a live health surface, so this window is short — but it is
      // not zero any more. Under no-store every visitor, and every refresh by
      // the same visitor, became its own read of the database. That is a poor
      // trade at the best of times and it helped exhaust the transfer
      // allowance, which took the page down entirely. A health surface that
      // is 60 seconds stale beats one that is 503.
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(60_000),
    });
    if (!response.ok) return null;
    return (await response.json()) as SystemStatus;
  } catch (e) {
    // Logged, not swallowed. Every page falls back to this function to explain
    // itself, so when it is the thing that failed the reader gets the generic
    // message and nobody finds out why. This lands in the serving logs.
    console.error("getStatus failed:", e);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Planner (M8)
// ---------------------------------------------------------------------------

export type PlanPeriod = {
  target_sp_start_utc: string;
  horizon_periods: number;
  point_gco2_kwh: number;
  q10_gco2_kwh: number | null;
  q90_gco2_kwh: number | null;
};

export type PlanWindow = {
  start_utc: string;
  end_utc: string;
  mean_gco2_kwh: number;
  periods: PlanPeriod[];
  horizon_group: string;
  min_horizon: number;
  max_horizon: number;
};

export type PlanCounterfactual = {
  mean_gco2_kwh?: number;
  saving_gco2_kwh?: number;
  saving_pct?: number;
  co2_saved_g?: number;
  // Pessimistic and optimistic ends of the saving, from the forecast's own
  // q10/q90. The lower bound can be negative — the recommendation turning out
  // worse than the alternative — and that is shown rather than clipped.
  saving_gco2_kwh_range?: [number, number];
  co2_saved_g_range?: [number, number];
  could_be_worse?: boolean;
  start_utc?: string;
  end_utc?: string;
  note?: string;
};

// The dirtiest feasible window. Deliberately NOT a counterfactual: nobody runs
// a load at the worst hour on purpose, so a saving measured against it is not a
// saving anyone would make.
export type PlanUpperBound = {
  mean_gco2_kwh: number;
  start_utc: string;
  end_utc: string;
  saving_gco2_kwh: number;
  note: string;
};

export type PlanHitRate = {
  available: boolean;
  note: string;
  hit_rate?: number;
  decisions?: number;
  hits?: number;
  baseline?: number;
};

export type PlanConfidence = {
  horizon_group: string;
  mae_gco2_kwh?: number;
  n?: number;
  note?: string;
  hit_rate?: PlanHitRate;
};

export type PlanResult = {
  model_version: string | null;
  run_at_utc: string | null;
  search_window_hours?: number;
  duration_hours?: number;
  appliance_kwh?: number;
  best_window?: PlanWindow;
  counterfactuals?: {
    now: PlanCounterfactual;
    // The expected result of picking a feasible time at random — what you get
    // by not thinking about it, and the honest baseline.
    average: PlanCounterfactual;
    // 03:00 local, the folk heuristic. Can be negative on a wind-and-solar grid.
    overnight: PlanCounterfactual;
  };
  upper_bound?: PlanUpperBound;
  all_periods?: PlanPeriod[];
  confidence?: PlanConfidence;
  detail?: string;
};

export async function getPlan(
  durationHours: number = 1.0,
  withinHours: number = 24.0,
  applianceKwh: number = 1.0
): Promise<PlanResult | null> {
  try {
    const params = new URLSearchParams({
      duration_hours: durationHours.toString(),
      within_hours: withinHours.toString(),
      appliance_kwh: applianceKwh.toString(),
    });
    const response = await fetch(`${API_BASE}/v1/plan?${params}`, {
      // Cached per parameter combination. The underlying forecasts only change
      // when the pipeline issues, every 30 minutes, so a plan recomputed per
      // visitor was reading the same rows to produce the same answer.
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(60_000),
    });
    if (!response.ok) return null;
    return (await response.json()) as PlanResult;
  } catch {
    return null;
  }
}
