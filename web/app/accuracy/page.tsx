import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";

type AccuracyRow = {
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

const HORIZON_LABEL: Record<string, string> = {
  H1: "0–3 h",
  H2: "3–12 h",
  H3: "12–24 h",
  H4: "24–48 h",
};

async function get<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
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
    return (
      <>
        <h1>How wrong were we?</h1>
        <div className="card">
          <span className="pill warn">API unreachable</span>
          <p>
            The free-tier API sleeps after inactivity and can take up to a
            minute to wake. Reload in a moment.
          </p>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>How wrong were we?</h1>
      <p className="lede">
        Every forecast here was written to an append-only register{" "}
        <strong>before its outcome existed</strong>, then scored automatically
        once the actual arrived. Nothing on this page is a backtest.
      </p>

      <div className="grid">
        <section className="card">
          <h2 style={{ marginTop: 0 }}>The register</h2>
          <dl>
            <div className="kv">
              <dt>Forecasts issued</dt>
              <dd>{integrity.register.forecasts.toLocaleString("en-GB")}</dd>
            </div>
            <div className="kv">
              <dt>Models issuing</dt>
              <dd>{integrity.register.models}</dd>
            </div>
            <div className="kv">
              <dt>Scored so far</dt>
              <dd>{integrity.scored.toLocaleString("en-GB")}</dd>
            </div>
            <div className="kv">
              <dt>Months sealed</dt>
              <dd>{integrity.seals.length}</dd>
            </div>
          </dl>
        </section>

        <section className="card">
          <h2 style={{ marginTop: 0 }}>Why you can check this</h2>
          <p style={{ fontSize: "0.92rem" }}>{integrity.guarantee}</p>
          {integrity.recent_audits.length > 0 && (
            <p style={{ marginBottom: 0 }}>
              Latest integrity audit:{" "}
              <span
                className={
                  integrity.recent_audits[0].passed ? "pill ok" : "pill bad"
                }
              >
                {integrity.recent_audits[0].passed ? "passed" : "FAILED"}
              </span>
            </p>
          )}
        </section>
      </div>

      <h2>Accuracy by horizon</h2>

      {!accuracy.any_publishable ? (
        <div className="card">
          <span className="pill warn">Not yet publishable</span>
          <p>
            <strong>
              {accuracy.total_scored_points.toLocaleString("en-GB")} scored
              forecast{accuracy.total_scored_points === 1 ? "" : "s"}
            </strong>{" "}
            so far. This page publishes a figure only once a horizon group holds
            at least {accuracy.min_publishable_n.toLocaleString("en-GB")} scored
            points.
          </p>
          <p style={{ marginBottom: 0, color: "var(--muted)" }}>
            A forecast becomes scoreable roughly a day after it is issued, once
            the settlement period has passed and the actual has been published
            and settled. A scoreboard whose entire claim is honesty cannot open
            with a handful of points, so it says so instead.
          </p>
        </div>
      ) : (
        <div className="card table-scroll">
          <table>
            <thead>
              <tr>
                <th>Horizon</th>
                <th>Model</th>
                <th>n</th>
                <th>MAE</th>
                <th>MASE</th>
                <th>80% coverage</th>
              </tr>
            </thead>
            <tbody>
              {accuracy.rows.map((row) => (
                <tr key={`${row.horizon_group}-${row.model_version}`}>
                  <td>{HORIZON_LABEL[row.horizon_group] ?? row.horizon_group}</td>
                  <td>
                    <code>{row.model_version}</code>
                  </td>
                  <td>{row.n.toLocaleString("en-GB")}</td>
                  <td>{row.publishable ? row.mae?.toFixed(2) : "—"}</td>
                  <td>{row.publishable ? row.mase?.toFixed(3) : "—"}</td>
                  <td>
                    {row.publishable && row.coverage_80 != null
                      ? `${(row.coverage_80 * 100).toFixed(1)}%`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            {accuracy.note}
          </p>
        </div>
      )}

      <h2>What these numbers mean</h2>
      <ul className="notes">
        <li>
          <strong>MASE</strong> below 1.0 beats predicting yesterday&rsquo;s
          value at the same time of day. Above 1.0 does not.
        </li>
        <li>
          <strong>80% coverage</strong> should be close to 80%. An interval
          containing 62% of actuals is a broken product, not a small miss &mdash;
          it means every stated uncertainty understates the risk.
        </li>
        <li>
          <code>ESO_published</code> is National Grid ESO&rsquo;s own forecast,
          recorded at the horizon we received it. Because the ESO revises
          continuously as the horizon shortens, this is the only place the
          comparison is like-for-like &mdash; a backtest against their stored
          history compares our 48-hour forecast to their near-final one.
        </li>
        <li>
          Where GridCast loses, it will be shown losing. That is the point.
        </li>
      </ul>
    </>
  );
}
