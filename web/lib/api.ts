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

export type SystemStatus = {
  env: string;
  commit: string;
  milestone: string;
  database: "ok" | "unreachable";
  readonly_role_in_use: boolean;
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
