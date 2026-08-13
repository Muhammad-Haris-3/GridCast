export const metadata = {
  title: "About GridCast — what this is, in plain words",
  description:
    "What GridCast is for, what it actually does, what it is built with, and which part was hardest. No jargon.",
};

export default function AboutPage() {
  return (
    <>
      <h1>What is this, in plain words?</h1>
      <p className="lede">
        No jargon on this page. If you only read one line: GridCast tells you
        the cleanest time to run something that uses electricity, and it keeps a
        public record of how often it was right.
      </p>

      <h2>Why does this exist?</h2>
      <div className="card">
        <p>
          The electricity in your socket is not equally clean all day. When the
          wind is blowing and the sun is out, it comes mostly from wind and
          solar. When it is still and dark, gas plants fill the gap. The same
          dishwasher, run at two different times, can be responsible for three
          times as much carbon.
        </p>
        <p style={{ marginBottom: 0 }}>
          Most people who want to help already have the habit of running things
          at night. It turns out that habit barely does anything. GridCast
          exists to give a better answer, and — more importantly — to prove
          whether that answer is actually any good.
        </p>
      </div>

      <h2>What does it actually do?</h2>
      <div className="card">
        <p>Three things, in order:</p>
        <ol className="notes" style={{ color: "var(--text)" }}>
          <li>
            <strong>It predicts.</strong> Every half hour, it works out how
            clean the electricity will be for each half hour of the next two
            days.
          </li>
          <li>
            <strong>It writes the prediction down, permanently.</strong> Before
            anyone knows what actually happened. The record cannot be edited or
            deleted afterwards — not by us either.
          </li>
          <li>
            <strong>It marks its own homework.</strong> Once the real number
            arrives, it goes back, compares, and publishes the score. Including
            when the score is bad.
          </li>
        </ol>
        <p style={{ marginBottom: 0 }}>
          Step 2 is the unusual one. Anyone can claim their forecast is
          accurate. The only way to prove it is to publish the guess before the
          answer exists, and never be able to quietly change it.
        </p>
      </div>

      <h2>Who is it for?</h2>
      <div className="card">
        <p style={{ marginBottom: 0 }}>
          Anyone with something they can run later instead of now — a
          dishwasher, a washing machine, an electric car, a heat pump, a batch
          of work on a computer. If you cannot move it, this cannot help you.
        </p>
      </div>

      <h2>What did it find?</h2>
      <div className="card table-scroll">
        <p>
          The thing worth knowing is that the popular advice is close to
          useless. For one recent forecast &mdash; lower is cleaner, and the
          number is roughly &ldquo;grams of carbon per unit of electricity&rdquo;:
        </p>
        <table>
          <thead>
            <tr>
              <th>When you run it</th>
              <th>How dirty it is</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Right now</td>
              <td>168.0</td>
              <td style={{ color: "var(--muted)" }}>the baseline</td>
            </tr>
            <tr>
              <td>Wait until 3am</td>
              <td>166.8</td>
              <td style={{ color: "var(--bad)" }}>
                almost exactly the same as not waiting
              </td>
            </tr>
            <tr>
              <td>Pick a time at random</td>
              <td>125.6</td>
              <td style={{ color: "var(--muted)" }}>
                better than the 3am habit
              </td>
            </tr>
            <tr>
              <td>
                <strong>The time GridCast suggests</strong>
              </td>
              <td>
                <strong>52.5</strong>
              </td>
              <td style={{ color: "var(--accent)" }}>about a third as dirty</td>
            </tr>
          </tbody>
        </table>
        <p style={{ marginBottom: 0 }}>
          Waiting until 3am is <em>worse than guessing</em>. The reason is that
          overnight tariffs were invented to solve a different problem — too
          many people using power at 6pm — not to solve carbon. On a grid full
          of solar panels, the cleanest hours have quietly moved to the middle
          of the day, and the advice never caught up.
        </p>
      </div>

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
              <td>
                The filing cabinet. Holds every reading and every past forecast
              </td>
            </tr>
            <tr>
              <td>dbt</td>
              <td>
                Turns messy raw data into clean, checked tables, and fails
                loudly when something looks wrong
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
              <td>
                The alarm clock. Wakes everything up on a schedule, with nobody
                watching
              </td>
            </tr>
          </tbody>
        </table>
        <p style={{ marginBottom: 0, color: "var(--muted)" }}>
          The data itself is free and public: carbon intensity from National
          Grid, demand from Elexon, weather from Open-Meteo. Everything runs on
          free hosting tiers, which is a real constraint rather than a detail —
          see below.
        </p>
      </div>

      <h2>How hard was it, and which part was hardest?</h2>
      <div className="card table-scroll">
        <table>
          <thead>
            <tr>
              <th>Part of the job</th>
              <th>How hard</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Getting the data</td>
              <td>
                <span className="pill ok">Easy</span>
              </td>
              <td>It is free and public</td>
            </tr>
            <tr>
              <td>Building the website</td>
              <td>
                <span className="pill ok">Easy</span>
              </td>
              <td>Ordinary work</td>
            </tr>
            <tr>
              <td>Making a decent prediction</td>
              <td>
                <span className="pill warn">Medium</span>
              </td>
              <td>
                Standard machine learning. Weather explains most of it
              </td>
            </tr>
            <tr>
              <td>Keeping it running unattended</td>
              <td>
                <span className="pill warn">Medium</span>
              </td>
              <td>
                Data sources go down. It has to notice and fix its own gaps
              </td>
            </tr>
            <tr>
              <td>
                <strong>Stopping the model from cheating</strong>
              </td>
              <td>
                <span className="pill bad">Hardest</span>
              </td>
              <td>Explained below</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: "1rem" }}>
        <p>
          <strong>The hardest part is the one nobody sees.</strong> When you
          test a forecasting model, it is extremely easy to accidentally let it
          peek at information that would not have existed yet at the time it was
          supposedly making the guess. Use yesterday&rsquo;s weather report and
          you are fine. Use what the weather <em>turned out to be</em> and your
          model looks brilliant and is worthless.
        </p>
        <p>
          The trap is that cheating does not look like a bug. Nothing crashes.
          There is no error message. Your accuracy just quietly gets better, and
          you feel pleased with yourself.
        </p>
        <p style={{ marginBottom: 0 }}>
          So a large part of this project is machinery whose only job is to
          block that: every piece of information carries a timestamp for when it
          became knowable, and the model is refused anything it should not have
          had yet. There are tests designed to catch the model reaching into the
          future, and they have caught it.
        </p>
      </div>

      <h2>What is the real goal here?</h2>
      <div className="card">
        <p>
          The goal is <strong>not</strong> to have the most accurate forecast.
          Plenty of people have better models.
        </p>
        <p style={{ marginBottom: 0 }}>
          The goal is to build something that <strong>cannot lie about how
          good it is</strong>. Predictions get written down before the answer
          exists, the record cannot be edited, the rule for &ldquo;is the new
          model better?&rdquo; was written down before either model existed, and
          the failures are published next to the successes. Accuracy is the easy
          part. Being trustworthy about your accuracy is the hard part, and it is
          the part almost everything skips.
        </p>
      </div>

      <h2>What should you not trust yet?</h2>
      <div className="card">
        <ul className="notes" style={{ color: "var(--text)" }}>
          <li>
            <strong>The live scoreboard is still filling up.</strong> The system
            is young, so there are not yet enough scored predictions to make a
            confident claim. The Accuracy page says how far off it is rather
            than showing a flattering early number.
          </li>
          <li>
            <strong>A better model exists and is not being used.</strong> A
            stronger model roughly halves the error, but it is sitting on the
            bench being scored, not playing. The rules for promoting it were
            written down in advance and it has not met them yet. Promoting it
            early because it looks good would make the rules meaningless.
          </li>
          <li>
            <strong>The advice can be wrong.</strong> Roughly one time in ten,
            the suggested window turns out worse than the alternative. The Plan
            page shows that pessimistic case rather than hiding it.
          </li>
          <li>
            <strong>There is no price in pounds.</strong> Only carbon. The price
            data was switched off to stay inside a free storage limit, so a
            money figure would be a guess. It is left out instead of estimated.
          </li>
        </ul>
      </div>

      <h2>How do you know any of this is true?</h2>
      <div className="card">
        <p style={{ marginBottom: 0 }}>
          You do not have to take our word for it. Once a month, a fingerprint
          of the entire forecast record is published publicly and permanently.
          If anyone went back and quietly improved an old prediction, the
          fingerprint would no longer match, and anyone can check. The{" "}
          <a href="/status">Status</a> page shows what the system is doing right
          now, and the <a href="/accuracy">Accuracy</a> page shows the score.
        </p>
      </div>
    </>
  );
}
