import type { PlanPeriod } from "@/lib/api";

const LONDON: Intl.DateTimeFormatOptions = {
  timeZone: "Europe/London",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
};

const H = 220; // plot height inside the 260-unit viewBox; the rest is the axis
const W = 1000;

/**
 * The 48-hour forecast series with its 80% interval, and the recommended window
 * marked. Pure render from `all_periods` — no client JS, so it costs nothing on
 * the wire beyond the path strings.
 *
 * Periods with a null q10/q90 (models that issue no interval) simply get no
 * band: the point line is still drawn.
 */
export default function ForecastChart({
  periods,
  windowStartUtc,
  windowEndUtc,
}: {
  periods: PlanPeriod[];
  windowStartUtc?: string;
  windowEndUtc?: string;
}) {
  if (periods.length < 2) return null;

  const n = periods.length;
  const x = (i: number) => (i / (n - 1)) * W;

  const highs = periods.map((p) => p.q90_gco2_kwh ?? p.point_gco2_kwh);
  const top = Math.max(...highs) * 1.08;
  const y = (v: number) => H - (v / top) * H;

  const line = periods
    .map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.point_gco2_kwh).toFixed(1)}`)
    .join(" ");

  // The band is only drawn across the leading run of periods that carry an
  // interval, so a partial quantile series cannot produce a bogus closed shape.
  const withInterval = periods.filter(
    (p) => p.q10_gco2_kwh != null && p.q90_gco2_kwh != null
  );
  const band =
    withInterval.length === n
      ? periods
          .map(
            (p, i) =>
              `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.q90_gco2_kwh!).toFixed(1)}`
          )
          .join(" ") +
        " " +
        periods
          .slice()
          .reverse()
          .map((p, j) => `L${x(n - 1 - j).toFixed(1)} ${y(p.q10_gco2_kwh!).toFixed(1)}`)
          .join(" ") +
        " Z"
      : null;

  // Window rect, located by timestamp rather than by index arithmetic.
  const startMs = windowStartUtc ? Date.parse(windowStartUtc) : NaN;
  const endMs = windowEndUtc ? Date.parse(windowEndUtc) : NaN;
  const inWindow = periods
    .map((p, i) => ({ i, t: Date.parse(p.target_sp_start_utc) }))
    .filter((p) => p.t >= startMs && p.t < endMs)
    .map((p) => p.i);
  const hasWindow = inWindow.length > 0;
  const wx = hasWindow ? x(inWindow[0]) : 0;
  const wRight = hasWindow ? x(inWindow[inWindow.length - 1] + 1 > n - 1 ? n - 1 : inWindow[inWindow.length - 1] + 1) : 0;

  const step = top > 300 ? 100 : top > 150 ? 50 : 25;
  const gridValues: number[] = [];
  for (let v = step; v < top; v += step) gridValues.push(v);

  const tickIndices = [0, Math.round((n - 1) * 0.25), Math.round((n - 1) * 0.5), Math.round((n - 1) * 0.75), n - 1];

  return (
    <div className="chart-frame">
      <div className="chart-head">
        <h3 style={{ margin: 0 }}>The next {Math.round((n * 30) / 60)} hours</h3>
        <div className="chart-legend">
          <span>
            <i className="line" />
            point forecast
          </span>
          {band ? (
            <span>
              <i className="band" />
              80% interval
            </span>
          ) : null}
          {hasWindow ? (
            <span>
              <i className="win" />
              chosen window
            </span>
          ) : null}
        </div>
      </div>

      <svg
        viewBox="0 0 1000 260"
        preserveAspectRatio="none"
        style={{ width: "100%", height: 260, display: "block", overflow: "visible" }}
        role="img"
        aria-label="Forecast carbon intensity for each half hour, with its 80 percent interval and the recommended window highlighted."
      >
        {/* No fill or stroke literals below: every colour comes from the themed
            rules in globals.css, because this chart has to read on a near-black
            ground and on white. CSS beats a presentation attribute, so a stray
            hardcoded stroke here would be silently ignored in one theme and
            wrong in the other. */}
        {gridValues.map((v) => (
          <g key={v}>
            <line className="chart-grid" x1={0} y1={y(v)} x2={W} y2={y(v)} strokeWidth={1} />
            <text className="chart-axis" x={0} y={y(v) - 6} fontSize={11}>
              {v} g
            </text>
          </g>
        ))}

        {hasWindow ? (
          <rect
            className="chart-window"
            x={wx}
            y={0}
            width={Math.max(6, wRight - wx)}
            height={H}
            strokeWidth={1}
          />
        ) : null}

        {band ? <path className="chart-band" d={band} /> : null}

        <path
          className="chart-line"
          d={line}
          fill="none"
          strokeWidth={2.2}
          strokeLinejoin="round"
          strokeLinecap="round"
          strokeDasharray={2600}
        />

        {tickIndices.map((i) => (
          <text
            key={i}
            className="chart-axis"
            x={x(i)}
            y={248}
            fontSize={11}
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
          >
            {new Date(periods[i].target_sp_start_utc).toLocaleString("en-GB", LONDON)}
          </text>
        ))}
      </svg>

      <p className="chart-note">
        Every half hour the planner searched, as forecast at issue time. Times are Europe/London.
      </p>
    </div>
  );
}
