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

export type SystemStatus = {
  env: string;
  commit: string;
  milestone: string;
  database: "ok" | "unreachable";
  readonly_role_in_use: boolean;
  warnings: string[];
  spine: SpineSummary;
  recent_runs: RunLogEntry[];
  detail?: string;
};

export async function getStatus(): Promise<SystemStatus | null> {
  try {
    const response = await fetch(`${API_BASE}/v1/status`, {
      // Status is a live health surface; a cached one would be worse than none.
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    if (!response.ok) return null;
    return (await response.json()) as SystemStatus;
  } catch {
    return null;
  }
}
