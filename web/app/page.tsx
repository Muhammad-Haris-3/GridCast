import Link from "next/link";

export default function Home() {
  return (
    <>
      <h1>Forecasts that grade themselves</h1>
      <p className="lede">
        GridCast forecasts Great Britain&rsquo;s grid carbon intensity 48 hours
        ahead, publishes each forecast <strong>before the outcome exists</strong>
        , and scores it automatically once the actual arrives &mdash; against
        naive baselines and against National Grid ESO&rsquo;s own published
        forecast.
      </p>

      <div className="grid">
        <section className="card">
          <span className="pill">Append-only</span>
          <h2 style={{ marginTop: "0.75rem" }}>Nothing gets edited</h2>
          <p>
            Every forecast is written to a register the application role has no
            permission to <code>UPDATE</code> or <code>DELETE</code>. Monthly
            integrity seals are committed to git, so the live database can be
            checked against public commit history by anyone.
          </p>
        </section>

        <section className="card">
          <span className="pill">Measured, not claimed</span>
          <h2 style={{ marginTop: "0.75rem" }}>Errors published too</h2>
          <p>
            Accuracy is reported by horizon with its sample size, over rolling
            windows. Where GridCast loses to a baseline or to the ESO forecast,
            that is published as prominently as where it wins.
          </p>
        </section>

        <section className="card">
          <span className="pill">Acts on it</span>
          <h2 style={{ marginTop: "0.75rem" }}>A costed decision</h2>
          <p>
            The planner names the half hour to run a flexible load, the carbon
            and cost saved against three counterfactuals, and how often a
            recommendation at that horizon has historically been right.
          </p>
        </section>
      </div>

      <h2>Build status</h2>
      <p className="lede">
        This is the <strong>M0 walking skeleton</strong>: the deployment path
        from browser to API to warehouse is live and verified end to end, before
        any analysis exists. Ingestion arrives at M1.
      </p>
      <p>
        <Link href="/status">Check the live pipeline status &rarr;</Link>
      </p>

      <h2>What is deliberately not here yet</h2>
      <ul className="notes">
        <li>
          No forecasts. The register schema and its constraints exist; no model
          has been trained.
        </li>
        <li>
          No accuracy figures. Publishing one before a single out-of-sample
          score exists would be exactly the behaviour this project is built to
          avoid.
        </li>
        <li>
          The promotion rule for model comparisons is already committed, in{" "}
          <code>PREREGISTRATION.md</code>, before any model exists.
        </li>
      </ul>
    </>
  );
}
