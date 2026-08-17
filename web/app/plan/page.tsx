import { getPlan, unavailableReason, type PlanResult } from "@/lib/api";
import { loadMatching } from "@/lib/snapshot";
import DataAge from "../DataAge";
import PlanControls from "./PlanControls";
import ForecastChart from "./ForecastChart";

// This page reads searchParams, so Next renders it dynamically whatever is
// declared here. The saving is in the fetch itself, which caches per parameter
// combination — see getPlan.

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

  // Only the default combination is published as a snapshot, so this serves
  // the untouched page — which is what nearly every visitor loads — without a
  // database read, and falls through to the live planner the moment a control
  // is moved. The parameter names must match PLAN_DEFAULTS in
  // gridcast/snapshot.py; a mismatch declines the snapshot rather than
  // answering a four-hour question with the two-hour answer.
  const loaded = await loadMatching<PlanResult>(
    "plan",
    { duration_hours: duration, within_hours: within, appliance_kwh: kwh },
    () => getPlan(duration, within, kwh)
  );
  const plan = loaded?.data;

  if (!plan || !loaded || plan.detail || !plan.best_window) {
    // Ask the status endpoint why, rather than guessing. It answers even when
    // the database does not, and it names the actual cause.
    const reason = plan?.detail ?? (await unavailableReason());
    return (
      <>
        <span className="kicker">Planner</span>
        <h1>When should I run it?</h1>
        <PlanControls duration={duration} within={within} kwh={kwh} />
        <div className="card caution" style={{ marginTop: 24 }}>
          <span className="pill warn">Not available</span>
          <p style={{ marginBottom: 0 }}>
            {reason ??
              "The API is unreachable. On the free tier it sleeps after inactivity and can take a minute to wake."}
          </p>
        </div>
      </>
    );
  }

  const best = plan.best_window;
  const cf = plan.counterfactuals;
  const hit = plan.confidence?.hit_rate;

  const options = [
    { key: "now", label: "Run it now", data: cf?.now },
    { key: "average", label: "Pick a time at random", data: cf?.average },
    { key: "overnight", label: "Wait until 3am", data: cf?.overnight },
  ].filter((o) => o.data && o.data.mean_gco2_kwh != null);

  return (
    <>
      <span className="kicker">Planner</span>
      <h1>When should I run it?</h1>
      <p className="lede">
        A <strong>{duration}</strong>-hour load using <strong>{kwh}</strong> kWh, any time in the
        next <strong>{within}</strong> hours.
      </p>

      <PlanControls duration={duration} within={within} kwh={kwh} />

      <div style={{ marginTop: 24 }}>
        <DataAge loaded={loaded} />
      </div>

      <div className="card accent" style={{ padding: "30px 32px" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 32,
            flexWrap: "wrap",
            alignItems: "flex-end",
          }}
        >
          <div>
            <span className="pill ok">Recommended</span>
            <h2 style={{ fontSize: "clamp(34px,4.6vw,50px)", lineHeight: 1.04, margin: "18px 0 0" }}>
              {when(best.start_utc)} &ndash; {when(best.end_utc)}
            </h2>
            <p style={{ color: "var(--muted)", fontSize: 13.5, margin: "14px 0 0" }}>
              Forecast issued {when(plan.run_at_utc ?? undefined)} by{" "}
              <code>{plan.model_version}</code>, {best.horizon_group} horizon.
            </p>
          </div>
          <div style={{ textAlign: "right" }}>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 76,
                lineHeight: 0.9,
                letterSpacing: "-0.03em",
                color: "var(--accent)",
              }}
            >
              {best.mean_gco2_kwh}
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.14em",
                color: "var(--dim)",
                marginTop: 8,
              }}
            >
              gCO₂ / kWh forecast avg
            </div>
          </div>
        </div>
      </div>

      {plan.all_periods && plan.all_periods.length > 1 ? (
        <ForecastChart
          periods={plan.all_periods}
          windowStartUtc={best.start_utc}
          windowEndUtc={best.end_utc}
        />
      ) : null}

      <h2>What that saves</h2>
      <div className="card table-scroll">
        <table>
          <thead>
            <tr>
              <th>Instead of…</th>
              <th className="num">Its intensity</th>
              <th className="num">You save</th>
              <th className="num">CO₂ saved</th>
              <th className="num">Could be as bad as</th>
            </tr>
          </thead>
          <tbody>
            {options.map(({ key, label, data }) => {
              const range = data!.saving_gco2_kwh_range;
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td className="num">{data!.mean_gco2_kwh} g</td>
                  <td className="num">
                    <strong>{data!.saving_gco2_kwh} g/kWh</strong>{" "}
                    <span style={{ color: "var(--faint)" }}>({data!.saving_pct}%)</span>
                  </td>
                  <td className="num">{data!.co2_saved_g} g</td>
                  <td className="num" style={{ color: range && range[0] < 0 ? "var(--warn)" : undefined }}>
                    {range ? `${range[0]} g/kWh` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p>
          The last column is the pessimistic end of the forecast&rsquo;s own 80% interval. Where it
          is negative, the recommendation could turn out worse than the alternative. That is a real
          possibility and it is shown rather than hidden.
        </p>
      </div>

      <h2>Should you believe it?</h2>
      {hit?.available ? (
        <div className="card">
          <p style={{ fontSize: 17, marginTop: 0 }}>
            <strong>{((hit.hit_rate ?? 0) * 100).toFixed(1)}%</strong> of past recommendations at
            this horizon landed in the cleanest third of their window, over{" "}
            {hit.decisions?.toLocaleString("en-GB")} decisions.
          </p>
          <p style={{ marginBottom: 0, color: "var(--muted)" }}>
            Picking at random would land there 33.3% of the time. Anything near that means the
            forecast is adding little at this horizon.
          </p>
        </div>
      ) : (
        <div className="card caution">
          <span className="pill warn">Not yet measurable</span>
          <p style={{ marginBottom: 0, marginTop: 18 }}>{hit?.note}</p>
        </div>
      )}

      <h2>Two things worth knowing</h2>
      <div className="grid">
        <div className="card">
          <h4>3am is not the clean hour.</h4>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: 0 }}>
            On a grid running on wind and solar the cleanest half hours are often the middle of the
            day, when solar peaks. The overnight habit is a leftover from a system whose problem was
            demand peaks, not carbon.
          </p>
        </div>
        <div className="card">
          <h4>The dirtiest window is not a counterfactual.</h4>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: 0 }}>
            Nobody deliberately runs a load at the worst hour, so GridCast does not quote a saving
            against it. It appears in the API as <code>upper_bound</code>, labelled as a bound.
          </p>
        </div>
        <div className="card">
          <h4>Savings are in carbon only.</h4>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: 0 }}>
            Cost in £ needs the market price series, which is deferred until the storage budget
            allows it &mdash; so it is absent rather than estimated.
          </p>
        </div>
      </div>
    </>
  );
}
