import { describeAge, type Loaded } from "@/lib/snapshot";

/**
 * Where the numbers on this page came from, and when.
 *
 * Pages are served from a precomputed snapshot rather than a live query (see
 * lib/snapshot.ts), which is what keeps them fast and free. The cost of that
 * choice is that a reader can no longer assume a loaded page means a working
 * pipeline — so the page has to say so itself. A dashboard that renders
 * perfectly from four-day-old data, with nothing on it admitting that, is
 * worse than one that fails: it is confidently wrong, and nobody checks.
 *
 * Rendered for every source, including live. "This is current" is information
 * too, and a provenance line that only appears when something is wrong trains
 * readers to read its absence as an endorsement.
 */
export default function DataAge({ loaded }: { loaded: Loaded<unknown> }) {
  const { source, capturedAt, ageSeconds, stale } = loaded;

  const label =
    source === "live"
      ? "Read live from the database just now."
      : `Served from a snapshot computed ${describeAge(ageSeconds)}.`;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
        margin: "0 0 26px",
        fontSize: 12.5,
        color: "var(--faint)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <span className={stale ? "pill warn" : "pill"}>
        {stale ? "stale" : source === "live" ? "live" : "cached"}
      </span>
      <span>
        {label}
        {stale && (
          <>
            {" "}
            The pipeline publishes every 30 minutes, so this one has missed at least three
            runs — the numbers below are real but no longer current, and the status page
            says why.
          </>
        )}
      </span>
      {capturedAt && (
        <time dateTime={capturedAt} style={{ color: "var(--dim)" }}>
          {capturedAt.replace("T", " ").slice(0, 16)}Z
        </time>
      )}
    </div>
  );
}
