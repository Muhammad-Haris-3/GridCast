import { API_BASE, unavailableReason } from "@/lib/api";
import Scoreboard, { type AccuracyRow } from "./Scoreboard";

// Accuracy changes only as forecasts mature and are scored, hours behind the
// clock. There is nothing to gain from rendering it per visitor.
export const revalidate = 900;

type Accuracy = {
  min_publishable_n: number;
  total_scored_points: number;
  any_publishable: boolean;
  rows: AccuracyRow[];
  note: string;
};

type Integrity = {
  register: {
    forecasts: number;
    models: number;
    first_issued: string | null;
    last_issued: string | null;
  };
  scored: number;
  seals: { period_month: string; row_count: number }[];
  recent_audits: { checked_at_utc: string; passed: boolean }[];
  guarantee: string;
};

async function get<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API_BASE}${path}`, {
      next: { revalidate: 900 },
      signal: AbortSignal.timeout(60_000),
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export default async function AccuracyPage() {
  const [accuracy, integrity] = await Promise.all([
    get<Accuracy>("/v1/accuracy"),
    get<Integrity>("/v1/integrity"),
  ]);

  if (!accuracy || !integrity) {
    // The status endpoint answers even when the database does not, so the reason
    // can be the real one rather than an assumption about sleep.
    const reason = await unavailableReason();
    return (
      <>
        <span className="kicker">Scoreboard</span>
        <h1>How wrong were we?</h1>
        <div className="card caution">
          <span className="pill warn">Not available</span>
          <p style={{ marginBottom: 0, marginTop: 18 }}>
            {reason ??
              "The free-tier API sleeps after inactivity and can take up to a minute to wake. Reload in a moment."}
          </p>
        </div>
      </>
    );
  }

  // How close the leading horizon group is to publishable — the honest progress
  // figure, computed from the rows rather than asserted.
  const leader = accuracy.rows.reduce((max, r) => Math.max(max, r.n), 0);
  const pct = Math.min(100, (leader / accuracy.min_publishable_n) * 100);

  return (
    <>
      <span className="kicker">Scoreboard</span>
      <h1>How wrong were we?</h1>
      <p className="lede">
        Every forecast here was written to an append-only register{" "}
        <strong>before its outcome existed</strong>, then scored automatically once the actual
        arrived. Nothing on this page is a backtest.
      </p>

      <div className="cells">
        <div>
          <div className="stat-label">Forecasts issued</div>
          <div className="stat-value">{integrity.register.forecasts.toLocaleString("en-GB")}</div>
        </div>
        <div>
          <div className="stat-label">Models issuing</div>
          <div className="stat-value">{integrity.register.models}</div>
        </div>
        <div>
          <div className="stat-label">Scored so far</div>
          <div className="stat-value">{integrity.scored.toLocaleString("en-GB")}</div>
        </div>
        <div>
          <div className="stat-label">Months sealed</div>
          <div className="stat-value">{integrity.seals.length}</div>
        </div>
      </div>

      <h2>Accuracy by horizon</h2>

      {!accuracy.any_publishable ? (
        <div className="card caution" style={{ padding: "38px 34px" }}>
          <span className="pill warn">Not yet publishable</span>
          <div
            style={{ display: "flex", alignItems: "baseline", gap: 16, marginTop: 24, flexWrap: "wrap" }}
          >
            <span className="big-number">
              {accuracy.total_scored_points.toLocaleString("en-GB")}
            </span>
            <span style={{ color: "var(--muted)", fontSize: 16 }}>
              scored forecast{accuracy.total_scored_points === 1 ? "" : "s"} so far
            </span>
          </div>

          <div style={{ marginTop: 28, maxWidth: 560 }}>
            <div className="meter-head">
              <span>largest horizon group</span>
              <span>
                {leader.toLocaleString("en-GB")} / {accuracy.min_publishable_n.toLocaleString("en-GB")}
              </span>
            </div>
            <div className="meter">
              <i style={{ width: `${pct}%` }} />
            </div>
          </div>

          <p style={{ color: "var(--muted)", fontSize: 14.5, margin: "26px 0 0", maxWidth: "66ch" }}>
            A forecast becomes scoreable roughly a day after it is issued, once the settlement period
            has passed and the actual has been published and settled. A scoreboard whose entire claim
            is honesty cannot open with a handful of points, so it says so instead.
          </p>
        </div>
      ) : (
        <Scoreboard
          rows={accuracy.rows}
          minPublishableN={accuracy.min_publishable_n}
          note={accuracy.note}
        />
      )}

      <div className="grid">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Why you can check this</h3>
          <p style={{ color: "var(--muted)", fontSize: 14, lineHeight: 1.65 }}>
            {integrity.guarantee}
          </p>
          {integrity.recent_audits.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
                Latest integrity audit
              </span>
              <span className={integrity.recent_audits[0].passed ? "pill ok" : "pill bad"}>
                {integrity.recent_audits[0].passed ? "passed" : "FAILED"}
              </span>
            </div>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>What these numbers mean</h3>
          <ul className="notes">
            <li>
              <strong>MASE</strong> below 1.0 beats predicting yesterday&rsquo;s value at the same
              time of day. Above 1.0 does not.
            </li>
            <li>
              <strong>80% coverage</strong> should be close to 80%. An interval containing 62% of
              actuals is a broken product, not a small miss &mdash; it means every stated uncertainty
              understates the risk.
            </li>
            <li>
              <code>ESO_published</code> is National Grid ESO&rsquo;s own forecast, recorded at the
              horizon we received it. Because the ESO revises continuously as the horizon shortens,
              this is the only place the comparison is like-for-like &mdash; a backtest against their
              stored history compares our 48-hour forecast to their near-final one.
            </li>
            <li style={{ color: "var(--accent)" }}>
              Where GridCast loses, it will be shown losing. That is the point.
            </li>
          </ul>
        </div>
      </div>
    </>
  );
}
