import Link from "next/link";

// From README.md's milestone table. The "Build status" copy below states the
// same thing in prose, so move both together when a milestone changes.
const MILESTONES: { id: string; state: string; done: boolean }[] = [
  { id: "M0", state: "Foundation", done: true },
  { id: "M1", state: "Ingestion", done: true },
  { id: "M2", state: "Audit", done: true },
  { id: "M3", state: "Warehouse", done: true },
  { id: "M4", state: "Backtesting", done: true },
  { id: "M5", state: "Live loop", done: true },
  { id: "M6", state: "Modelling", done: true },
  { id: "M7", state: "Waiting on data", done: false },
  { id: "M8", state: "Product", done: true },
];

export default function Home() {
  return (
    <>
      <span className="kicker">Carbon intensity · Great Britain</span>
      <h1 style={{ maxWidth: "19ch", marginTop: 20 }}>Forecasts that grade themselves</h1>
      <p className="lede">
        GridCast forecasts Great Britain&rsquo;s grid carbon intensity 48 hours ahead, publishes each
        forecast <strong>before the outcome exists</strong>, and scores it automatically once the
        actual arrives &mdash; against naive baselines and against National Grid ESO&rsquo;s own
        published forecast.
      </p>

      <div className="actions">
        <Link href="/plan" className="btn btn-primary">
          When should I run it? &rarr;
        </Link>
        <Link href="/accuracy" className="btn btn-secondary">
          How wrong were we?
        </Link>
      </div>

      <div className="cells" style={{ marginTop: 72, gridTemplateColumns: "repeat(auto-fit,minmax(258px,1fr))" }}>
        <section>
          <span className="kicker" style={{ fontSize: 10, letterSpacing: "0.16em" }}>
            Append-only
          </span>
          <h2 style={{ fontSize: 27, margin: "16px 0 12px" }}>Nothing gets edited</h2>
          <p style={{ color: "var(--muted)", fontSize: 14.5, margin: 0 }}>
            Every forecast is written to a register the application role has no permission to{" "}
            <code>UPDATE</code> or <code>DELETE</code>. Monthly integrity seals are committed to git,
            so the live database can be checked against public commit history by anyone.
          </p>
        </section>

        <section>
          <span className="kicker" style={{ fontSize: 10, letterSpacing: "0.16em" }}>
            Measured, not claimed
          </span>
          <h2 style={{ fontSize: 27, margin: "16px 0 12px" }}>Errors published too</h2>
          <p style={{ color: "var(--muted)", fontSize: 14.5, margin: 0 }}>
            Accuracy is reported by horizon with its sample size, over rolling windows. Where
            GridCast loses to a baseline or to the ESO forecast, that is published as prominently as
            where it wins.
          </p>
        </section>

        <section>
          <span className="kicker" style={{ fontSize: 10, letterSpacing: "0.16em" }}>
            Acts on it
          </span>
          <h2 style={{ fontSize: 27, margin: "16px 0 12px" }}>A costed decision</h2>
          <p style={{ color: "var(--muted)", fontSize: 14.5, margin: 0 }}>
            The planner names the half hour to run a flexible load, the carbon and cost saved against
            three counterfactuals, and how often a recommendation at that horizon has historically
            been right.
          </p>
        </section>
      </div>

      <h2>Build status</h2>
      <p className="lede" style={{ animation: "none", marginBottom: 28 }}>
        The loop is live. <strong>Four models</strong> issue a 48-hour forecast every run &mdash; a
        seasonal-naive champion, a persistence baseline, a gradient-boosting challenger, and National
        Grid ESO&rsquo;s own forecast recorded at the horizon we received it. M7 is waiting on data
        rather than on code: the pre-registered promotion rule needs about 1,440 scored points per
        horizon group, roughly ten days of live operation.
      </p>

      {/* Styled in globals.css rather than inline: the done/pending tints are
          different colours in each theme, and an inline literal can only be
          one of them. */}
      <div className="rail">
        {MILESTONES.map((m) => (
          <div key={m.id} className={m.done ? "rail-item done" : "rail-item pending"}>
            <span className="rail-id">{m.id}</span>
            <span className="rail-state">{m.state}</span>
          </div>
        ))}
      </div>

      <p style={{ margin: "22px 0 0" }}>
        <Link href="/status">Check the live pipeline status &rarr;</Link>
      </p>

      <h2>What is deliberately not here yet</h2>
      <div className="cells" style={{ marginTop: 0, gridTemplateColumns: "1fr" }}>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--faint)", marginRight: 14 }}>
              01
            </span>
            No accuracy figures. A horizon group must hold 200 scored points before this site prints
            one, and a forecast only becomes scoreable about a day after it is issued. The Accuracy
            page reports how far off that is instead.
          </p>
        </div>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--faint)", marginRight: 14 }}>
              02
            </span>
            No promoted challenger. The gradient-boosting model roughly halves the error in
            backtesting, and it is still on the bench being scored. The rule for promoting it was
            committed to <code>PREREGISTRATION.md</code> before either model existed, and it has not
            been met yet.
          </p>
        </div>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--faint)", marginRight: 14 }}>
              03
            </span>
            No price in pounds. Savings are quoted in carbon only &mdash; the market price series is
            deferred until the storage budget allows it, so a cost figure would be a guess rather
            than a measurement.
          </p>
        </div>
      </div>
    </>
  );
}
