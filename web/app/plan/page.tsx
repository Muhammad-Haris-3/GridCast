import { getPlan } from "@/lib/api";

export const dynamic = "force-dynamic";

const LONDON: Intl.DateTimeFormatOptions = {
  timeZone: "Europe/London",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
};

function when(iso: string | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", LONDON);
}

type SearchParams = { [key: string]: string | string[] | undefined };

export default async function PlanPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const duration = Number(params.duration ?? 2);
  const within = Number(params.within ?? 24);
  const kwh = Number(params.kwh ?? 1.4);

  const plan = await getPlan(duration, within, kwh);

  if (!plan || plan.detail || !plan.best_window) {
    return (
      <>
        <h1>When should I run it?</h1>
        <div className="card">
          <span className="pill warn">Not available</span>
          <p>
            {plan?.detail ??
              "The API is unreachable. On the free tier it sleeps after inactivity and can take a minute to wake."}
          </p>
        </div>
      </>
    );
  }

  const best = plan.best_window;
  const cf = plan.counterfactuals;
  const hit = plan.confidence?.hit_rate as
    | { available: boolean; note: string; hit_rate?: number; decisions?: number }
    | undefined;

  const options = [
    { key: "now", label: "Run it now", data: cf?.now },
    { key: "average", label: "Pick a time at random", data: cf?.average },
    { key: "overnight", label: "Wait until 3am", data: cf?.overnight },
  ].filter((o) => o.data && o.data.mean_gco2_kwh != null);

  return (
    <>
      <h1>When should I run it?</h1>
      <p className="lede">
        A {duration}-hour load using {kwh} kWh, any time in the next {within}{" "}
        hours.
      </p>

      <div className="card" style={{ borderColor: "var(--accent)" }}>
        <span className="pill ok">Recommended</span>
        <h2 style={{ marginTop: "0.75rem", marginBottom: "0.25rem" }}>
          {when(best.start_utc)} &ndash; {when(best.end_utc)}
        </h2>
        <p style={{ fontSize: "1.1rem", marginBottom: "0.5rem" }}>
          <strong>{best.mean_gco2_kwh} gCO₂/kWh</strong> forecast average
        </p>
        <p style={{ color: "var(--muted)", marginBottom: 0 }}>
          Forecast issued {when(plan.run_at_utc ?? undefined)} by{" "}
          <code>{plan.model_version}</code>, {best.horizon_group} horizon.
        </p>
      </div>

      <h2>What that saves</h2>
      <div className="card table-scroll">
        <table>
          <thead>
            <tr>
              <th>Instead of…</th>
              <th>Its intensity</th>
              <th>You save</th>
              <th>CO₂ saved</th>
              <th>Could be as bad as</th>
            </tr>
          </thead>
          <tbody>
            {options.map(({ key, label, data }) => {
              const range = data!.saving_gco2_kwh_range as
                | [number, number]
                | undefined;
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td>{data!.mean_gco2_kwh} g</td>
                  <td>
                    <strong>{data!.saving_gco2_kwh} g/kWh</strong>{" "}
                    <span style={{ color: "var(--muted)" }}>
                      ({data!.saving_pct}%)
                    </span>
                  </td>
                  <td>{data!.co2_saved_g} g</td>
                  <td
                    style={{
                      color: range && range[0] < 0 ? "var(--warn)" : "inherit",
                    }}
                  >
                    {range ? `${range[0]} g/kWh` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          The last column is the pessimistic end of the forecast&rsquo;s own 80%
          interval. Where it is negative, the recommendation could turn out
          worse than the alternative. That is a real possibility and it is shown
          rather than hidden.
        </p>
      </div>

      <h2>Should you believe it?</h2>
      <div className="card">
        {hit?.available ? (
          <>
            <p style={{ fontSize: "1.05rem" }}>
              <strong>{((hit.hit_rate ?? 0) * 100).toFixed(1)}%</strong> of past
              recommendations at this horizon landed in the cleanest third of
              their window, over {hit.decisions?.toLocaleString("en-GB")}{" "}
              decisions.
            </p>
            <p style={{ marginBottom: 0, color: "var(--muted)" }}>
              Picking at random would land there 33.3% of the time. Anything
              near that means the forecast is adding little at this horizon.
            </p>
          </>
        ) : (
          <>
            <span className="pill warn">Not yet measurable</span>
            <p style={{ marginBottom: 0 }}>{hit?.note}</p>
          </>
        )}
      </div>

      <h2>Two things worth knowing</h2>
      <ul className="notes">
        <li>
          <strong>3am is not the clean hour.</strong> On a grid running on wind
          and solar the cleanest half hours are often the middle of the day,
          when solar peaks. The overnight habit is a leftover from a system
          whose problem was demand peaks, not carbon.
        </li>
        <li>
          <strong>The dirtiest window is not a counterfactual.</strong> Nobody
          deliberately runs a load at the worst hour, so GridCast does not quote
          a saving against it. It appears in the API as{" "}
          <code>upper_bound</code>, labelled as a bound.
        </li>
        <li>
          Savings are in carbon only. Cost in £ needs the market price series,
          which is deferred until the storage budget allows it &mdash; so it is
          absent rather than estimated.
        </li>
      </ul>
    </>
  );
}
