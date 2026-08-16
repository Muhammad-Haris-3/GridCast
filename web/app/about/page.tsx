export const metadata = {
  title: "About GridCast — what this is, in plain words",
  description:
    "What GridCast is for, what it actually does, what it is built with, and which part was hardest. No jargon.",
};

// The four figures below were already hard-coded on this page: one recent
// forecast, quoted as an illustration. They are not read from the API.
const FINDINGS = [
  { label: "Right now", value: 168.0, verdict: "the baseline", tone: "ref" },
  { label: "Wait until 3am", value: 166.8, verdict: "almost exactly the same as not waiting", tone: "loss" },
  { label: "Pick a time at random", value: 125.6, verdict: "better than the 3am habit", tone: "ref" },
  { label: "The time GridCast suggests", value: 52.5, verdict: "about a third as dirty", tone: "win" },
];

const DIFFICULTY = [
  { part: "Getting the data", level: "Easy", pill: "ok", why: "It is free and public" },
  { part: "Building the website", level: "Easy", pill: "ok", why: "Ordinary work" },
  {
    part: "Making a decent prediction",
    level: "Medium",
    pill: "warn",
    why: "Standard machine learning. Weather explains most of it",
  },
  {
    part: "Keeping it running unattended",
    level: "Medium",
    pill: "warn",
    why: "Data sources go down. It has to notice and fix its own gaps",
  },
  {
    part: "Stopping the model from cheating",
    level: "Hardest",
    pill: "bad",
    why: "Explained below",
  },
];

const TONE_COLOR: Record<string, string> = {
  win: "var(--accent)",
  loss: "var(--bad)",
  ref: "var(--muted)",
};

const BAR_COLOR: Record<string, string> = {
  win: "var(--accent)",
  loss: "rgba(255,107,107,.7)",
  ref: "rgba(255,255,255,.28)",
};

export default function AboutPage() {
  const worst = Math.max(...FINDINGS.map((f) => f.value));

  return (
    <>
      <span className="kicker">Plain words</span>
      <h1 style={{ maxWidth: "22ch", marginTop: 18 }}>What is this, in plain words?</h1>
      <p className="lede">
        No jargon on this page. If you only read one line: GridCast tells you the cleanest time to run
        something that uses electricity, and it keeps a public record of how often it was right.
      </p>

      <div className="grid">
        <section className="card">
          <h2 style={{ marginTop: 0, fontSize: 26 }}>Why does this exist?</h2>
          <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.65 }}>
            The electricity in your socket is not equally clean all day. When the wind is blowing and
            the sun is out, it comes mostly from wind and solar. When it is still and dark, gas plants
            fill the gap. The same dishwasher, run at two different times, can be responsible for
            three times as much carbon.
          </p>
          <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.65, marginBottom: 0 }}>
            Most people who want to help already have the habit of running things at night. It turns
            out that habit barely does anything. GridCast exists to give a better answer, and — more
            importantly — to prove whether that answer is actually any good.
          </p>
        </section>

        <section className="card">
          <h2 style={{ marginTop: 0, fontSize: 26 }}>What does it actually do?</h2>
          <ol className="notes">
            <li>
              <strong>It predicts.</strong> Every half hour, it works out how clean the electricity
              will be for each half hour of the next two days.
            </li>
            <li>
              <strong>It writes the prediction down, permanently.</strong> Before anyone knows what
              actually happened. The record cannot be edited or deleted afterwards — not by us either.
            </li>
            <li>
              <strong>It marks its own homework.</strong> Once the real number arrives, it goes back,
              compares, and publishes the score. Including when the score is bad.
            </li>
          </ol>
        </section>
      </div>

      <h2>Who is it for?</h2>
      <div className="card">
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5, lineHeight: 1.65 }}>
          Anyone with something they can run later instead of now — a dishwasher, a washing machine,
          an electric car, a heat pump, a batch of work on a computer. If you cannot move it, this
          cannot help you.
        </p>
      </div>

      <h2>What did it find?</h2>
      <p style={{ color: "var(--muted)", fontSize: 15.5, margin: "0 0 24px", maxWidth: "60ch" }}>
        The thing worth knowing is that the popular advice is close to useless. For one recent
        forecast — lower is cleaner, and the number is roughly &ldquo;grams of carbon per unit of
        electricity&rdquo;:
      </p>

      <div style={{ display: "grid", gap: 12 }}>
        {FINDINGS.map((f) => (
          <div
            key={f.label}
            className="card"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 20,
              flexWrap: "wrap",
              padding: "16px 22px",
              borderRadius: 12,
            }}
          >
            <div
              style={{
                minWidth: 190,
                fontSize: 15,
                fontWeight: f.tone === "win" ? 600 : 400,
              }}
            >
              {f.label}
            </div>
            <div
              style={{
                flex: 1,
                minWidth: 120,
                height: 10,
                borderRadius: 2,
                background: "rgba(255,255,255,.05)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${(f.value / worst) * 100}%`,
                  borderRadius: 2,
                  background: BAR_COLOR[f.tone],
                }}
              />
            </div>
            <div
              style={{
                width: 74,
                textAlign: "right",
                fontFamily: "var(--font-mono)",
                fontSize: 15,
                color: f.tone === "win" ? "var(--accent)" : "var(--text)",
              }}
            >
              {f.value.toFixed(1)}
            </div>
            <div style={{ width: 210, fontSize: 13, color: TONE_COLOR[f.tone] }}>{f.verdict}</div>
          </div>
        ))}
      </div>

      <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.65, margin: "22px 0 0", maxWidth: "70ch" }}>
        Waiting until 3am is <em>worse than guessing</em>. The reason is that overnight tariffs were
        invented to solve a different problem — too many people using power at 6pm — not to solve
        carbon. On a grid full of solar panels, the cleanest hours have quietly moved to the middle of
        the day, and the advice never caught up.
      </p>

      <h2>What is it built with?</h2>
      <div className="card table-scroll">
        <table>
          <thead>
            <tr>
              <th>Tool</th>
              <th>What it is doing here</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Python</td>
              <td>Fetches the data and makes the predictions</td>
            </tr>
            <tr>
              <td>PostgreSQL</td>
              <td>The filing cabinet. Holds every reading and every past forecast</td>
            </tr>
            <tr>
              <td>dbt</td>
              <td>
                Turns messy raw data into clean, checked tables, and fails loudly when something looks
                wrong
              </td>
            </tr>
            <tr>
              <td>scikit-learn</td>
              <td>The machine learning library that learns the patterns</td>
            </tr>
            <tr>
              <td>FastAPI</td>
              <td>Serves the numbers to this website</td>
            </tr>
            <tr>
              <td>Next.js</td>
              <td>This website</td>
            </tr>
            <tr>
              <td>GitHub Actions</td>
              <td>The alarm clock. Wakes everything up on a schedule, with nobody watching</td>
            </tr>
          </tbody>
        </table>
        <p>
          The data itself is free and public: carbon intensity from National Grid, demand from Elexon,
          weather from Open-Meteo. Everything runs on free hosting tiers, which is a real constraint
          rather than a detail.
        </p>
      </div>

      <h2>How hard was it, and which part was hardest?</h2>
      <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", overflow: "hidden" }}>
        {DIFFICULTY.map((d) => (
          <div
            key={d.part}
            style={{
              display: "flex",
              gap: 20,
              alignItems: "center",
              flexWrap: "wrap",
              padding: "16px 22px",
              borderBottom: "1px solid var(--rule)",
              background: "rgba(255,255,255,.018)",
            }}
          >
            <div
              style={{
                flex: 1,
                minWidth: 200,
                fontSize: 14.5,
                fontWeight: d.pill === "bad" ? 700 : 400,
              }}
            >
              {d.part}
            </div>
            <div style={{ width: 100 }}>
              <span className={`pill ${d.pill}`}>{d.level}</span>
            </div>
            <div style={{ flex: 1.4, minWidth: 220, fontSize: 13.5, color: "var(--muted)" }}>
              {d.why}
            </div>
          </div>
        ))}
      </div>

      <div className="card alarm" style={{ marginTop: 40, padding: 32 }}>
        <h3 style={{ marginTop: 0 }}>The hardest part is the one nobody sees.</h3>
        <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.68, maxWidth: "74ch" }}>
          When you test a forecasting model, it is extremely easy to accidentally let it peek at
          information that would not have existed yet at the time it was supposedly making the guess.
          Use yesterday&rsquo;s weather report and you are fine. Use what the weather{" "}
          <em>turned out to be</em> and your model looks brilliant and is worthless.
        </p>
        <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.68, maxWidth: "74ch" }}>
          The trap is that cheating does not look like a bug. Nothing crashes. There is no error
          message. Your accuracy just quietly gets better, and you feel pleased with yourself.
        </p>
        <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.68, maxWidth: "74ch", marginBottom: 0 }}>
          So a large part of this project is machinery whose only job is to block that: every piece of
          information carries a timestamp for when it became knowable, and the model is refused
          anything it should not have had yet. There are tests designed to catch the model reaching
          into the future, and they have caught it.
        </p>
      </div>

      <h2>What is the real goal here?</h2>
      <div className="card">
        <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.65, marginTop: 0 }}>
          The goal is <strong>not</strong> to have the most accurate forecast. Plenty of people have
          better models.
        </p>
        <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.65, marginBottom: 0 }}>
          The goal is to build something that <strong>cannot lie about how good it is</strong>.
          Predictions get written down before the answer exists, the record cannot be edited, the rule
          for &ldquo;is the new model better?&rdquo; was written down before either model existed, and
          the failures are published next to the successes. Accuracy is the easy part. Being
          trustworthy about your accuracy is the hard part, and it is the part almost everything skips.
        </p>
      </div>

      <h2>What should you not trust yet?</h2>
      <div className="cells" style={{ marginTop: 0, gridTemplateColumns: "1fr" }}>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5, lineHeight: 1.62 }}>
            <strong>The live scoreboard is still filling up.</strong> The system is young, so there
            are not yet enough scored predictions to make a confident claim. The Accuracy page says
            how far off it is rather than showing a flattering early number.
          </p>
        </div>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5, lineHeight: 1.62 }}>
            <strong>A better model exists and is not being used.</strong> A stronger model roughly
            halves the error, but it is sitting on the bench being scored, not playing. The rules for
            promoting it were written down in advance and it has not met them yet. Promoting it early
            because it looks good would make the rules meaningless.
          </p>
        </div>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5, lineHeight: 1.62 }}>
            <strong>The advice can be wrong.</strong> Roughly one time in ten, the suggested window
            turns out worse than the alternative. The Plan page shows that pessimistic case rather
            than hiding it.
          </p>
        </div>
        <div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 14.5, lineHeight: 1.62 }}>
            <strong>There is no price in pounds.</strong> Only carbon. The price data was switched off
            to stay inside a free storage limit, so a money figure would be a guess. It is left out
            instead of estimated.
          </p>
        </div>
      </div>

      <div className="card accent" style={{ marginTop: 40, padding: 32 }}>
        <h3 style={{ marginTop: 0 }}>How do you know any of this is true?</h3>
        <p style={{ color: "var(--muted)", fontSize: 14.5, lineHeight: 1.68, maxWidth: "74ch", marginBottom: 0 }}>
          You do not have to take our word for it. Once a month, a fingerprint of the entire forecast
          record is published publicly and permanently. If anyone went back and quietly improved an
          old prediction, the fingerprint would no longer match, and anyone can check. The{" "}
          <a href="/status">Status</a> page shows what the system is doing right now, and the{" "}
          <a href="/accuracy">Accuracy</a> page shows the score.
        </p>
      </div>
    </>
  );
}
