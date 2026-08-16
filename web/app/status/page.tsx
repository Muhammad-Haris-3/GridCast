import { getStatus, API_BASE } from "@/lib/api";

// Rendered once a minute and shared, rather than once per visitor.
export const revalidate = 60;

function formatUtc(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toISOString().replace("T", " ").slice(0, 16) + "Z";
}

function statusClass(status: string): string {
  if (status === "success") return "pill ok";
  if (status === "failed") return "pill bad";
  return "pill warn";
}

export default async function StatusPage() {
  const status = await getStatus();

  if (!status) {
    return (
      <>
        <span className="kicker">Operations</span>
        <h1>Pipeline status</h1>
        <div className="card caution">
          <span className="pill warn">API unreachable</span>
          <p style={{ marginBottom: 0, marginTop: 18 }}>
            No response from <code>{API_BASE}</code>. On the free tier the API container sleeps after
            inactivity and can take up to a minute to wake, so this is often a cold start rather than
            an outage. Reload in a moment.
          </p>
        </div>
      </>
    );
  }

  const dbOk = status.database === "ok";

  return (
    <>
      <span className="kicker">Operations</span>
      <h1>Pipeline status</h1>
      <p className="lede">
        Live health of the ingestion, warehouse and serving layers. {status.milestone}.
      </p>

      <div className="grid">
        <section className="card">
          <h2 style={{ marginTop: 0 }}>Service</h2>
          <dl style={{ margin: 0 }}>
            <div className="kv">
              <dt>Environment</dt>
              <dd>{status.env}</dd>
            </div>
            <div className="kv">
              <dt>Build</dt>
              <dd>
                <code>{status.commit.slice(0, 12)}</code>
              </dd>
            </div>
            <div className="kv">
              <dt>Database</dt>
              <dd>
                <span className={dbOk ? "pill ok" : "pill bad"}>{status.database}</span>
              </dd>
            </div>
            <div className="kv">
              <dt>Read-only serving role</dt>
              <dd>
                <span className={status.readonly_role_in_use ? "pill ok" : "pill warn"}>
                  {status.readonly_role_in_use ? "in use" : "not configured"}
                </span>
              </dd>
            </div>
          </dl>
        </section>

        <section className="card">
          <h2 style={{ marginTop: 0 }}>Settlement period spine</h2>
          {status.spine ? (
            <dl style={{ margin: 0 }}>
              <div className="kv">
                <dt>Periods</dt>
                <dd>{status.spine.periods.toLocaleString("en-GB")}</dd>
              </div>
              <div className="kv">
                <dt>First</dt>
                <dd>{formatUtc(status.spine.first_period_utc)}</dd>
              </div>
              <div className="kv">
                <dt>Last</dt>
                <dd>{formatUtc(status.spine.last_period_utc)}</dd>
              </div>
            </dl>
          ) : (
            <p>Spine not built yet.</p>
          )}
          <p style={{ color: "var(--faint)", fontSize: 12.5, margin: "18px 0 0" }}>
            The spine is generated, not sourced. It is what makes a missing settlement period
            detectable: absence is a spine row with no fact.
          </p>
        </section>
      </div>

      {status.warnings.length > 0 && (
        <>
          <h2>Warnings</h2>
          <div className="card caution">
            <ul className="notes">
              {status.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        </>
      )}

      <h2>Recent runs</h2>
      {status.recent_runs.length === 0 ? (
        <div className="card">
          <p style={{ margin: 0 }}>
            No pipeline runs recorded. The run log table exists and is empty, which is different from
            missing.
          </p>
        </div>
      ) : (
        <div className="card table-scroll">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Job</th>
                <th>Status</th>
                <th>Started (UTC)</th>
                <th className="num">Read</th>
                <th className="num">Written</th>
              </tr>
            </thead>
            <tbody>
              {status.recent_runs.map((run) => (
                <tr key={`${run.source}-${run.started_at_utc}`}>
                  <td>{run.source}</td>
                  <td className="mono">{run.job}</td>
                  <td>
                    <span className={statusClass(run.status)}>{run.status}</span>
                  </td>
                  <td className="mono">{formatUtc(run.started_at_utc)}</td>
                  <td className="num">{run.rows_read.toLocaleString("en-GB")}</td>
                  <td className="num">{run.rows_written.toLocaleString("en-GB")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            Written being lower than read is the healthy state: rows are only inserted when a payload
            differs from the last one stored for that key.
          </p>
        </div>
      )}
    </>
  );
}
