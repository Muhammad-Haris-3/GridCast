/**
 * Static snapshot access.
 *
 * Every payload this site renders is precomputed by the pipeline and published
 * to the `snapshots` branch as flat JSON (see gridcast/snapshot.py). Reading
 * those files instead of calling the API is what makes a page load cost no
 * database reads and no container wake-up.
 *
 * That matters here for a specific reason. The serving database is a free-tier
 * Neon project with a **data transfer** allowance, and on 2026-08-17 it ran
 * out: the pipeline could not read, the API returned 500s, and every page that
 * asked the database a question got nothing. Page views were part of what
 * spent it. Serving a precomputed file removes visitors from that budget
 * entirely, and — because the file survives the database — keeps the site
 * answering during the outage rather than apologising for one.
 *
 * The order is snapshot first, live second, and it escalates rather than
 * alternates:
 *
 *   1. Read the snapshot. If it is recent, that is the answer, and nothing
 *      touches Postgres.
 *   2. If it is missing or stale, the pipeline has stopped publishing — which
 *      is itself the interesting fact. Only then is the live API worth asking,
 *      because /v1/status is the thing that can say why.
 *   3. If that fails too, serve the stale snapshot with its real age. A number
 *      from an hour ago, labelled as being from an hour ago, beats an error.
 */

const REPO_SNAPSHOTS =
  "https://raw.githubusercontent.com/Muhammad-Haris-3/GridCast/snapshots";

export const SNAPSHOT_BASE =
  process.env.NEXT_PUBLIC_SNAPSHOT_BASE_URL ?? REPO_SNAPSHOTS;

/**
 * How old a snapshot may be before the live API is worth waking.
 *
 * The pipeline publishes every thirty minutes, so anything past ninety has
 * missed three runs and is a failure rather than a slow cron. Set too low,
 * this reintroduces the database reads the snapshot exists to remove; set too
 * high, a dead pipeline goes unnoticed behind plausible-looking numbers.
 */
const STALE_AFTER_SECONDS = 90 * 60;

/**
 * Snapshot files are immutable per pipeline run and GitHub's raw host puts its
 * own five-minute cache in front of them, so a shorter window here would buy
 * staleness we cannot act on.
 */
const SNAPSHOT_REVALIDATE_SECONDS = 300;

export type Envelope<T> = {
  snapshot: {
    name: string;
    captured_at_utc: string;
    commit: string;
    params: Record<string, number> | null;
  };
  payload: T;
};

export type Loaded<T> = {
  data: T;
  /** Where this copy came from. Shown to the reader, not just logged. */
  source: "snapshot" | "live";
  /** When the data was computed. Null for a live read, which is by definition now. */
  capturedAt: string | null;
  ageSeconds: number;
  stale: boolean;
};

function ageOf(capturedAt: string): number {
  return Math.max(0, (Date.now() - new Date(capturedAt).getTime()) / 1000);
}

async function readSnapshot<T>(name: string): Promise<Envelope<T> | null> {
  try {
    const response = await fetch(`${SNAPSHOT_BASE}/${name}.json`, {
      next: { revalidate: SNAPSHOT_REVALIDATE_SECONDS },
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) return null;
    return (await response.json()) as Envelope<T>;
  } catch (e) {
    // Logged rather than swallowed: if the static path is broken, every page
    // silently falls back to the database and the transfer allowance starts
    // draining again with nothing to say why.
    console.error(`snapshot ${name} unreadable:`, e);
    return null;
  }
}

/**
 * Load a payload, preferring the published snapshot.
 *
 * `live` is optional. Where it is absent — because the caller has no
 * equivalent API route, or because waking the container is not worth it — a
 * stale snapshot is served rather than nothing.
 */
export async function load<T>(
  name: string,
  live?: () => Promise<T | null>
): Promise<Loaded<T> | null> {
  const envelope = await readSnapshot<T>(name);
  const age = envelope ? ageOf(envelope.snapshot.captured_at_utc) : Infinity;

  if (envelope && age <= STALE_AFTER_SECONDS) {
    return {
      data: envelope.payload,
      source: "snapshot",
      capturedAt: envelope.snapshot.captured_at_utc,
      ageSeconds: age,
      stale: false,
    };
  }

  if (live) {
    const fresh = await live();
    if (fresh !== null && fresh !== undefined) {
      return { data: fresh, source: "live", capturedAt: null, ageSeconds: 0, stale: false };
    }
  }

  if (envelope) {
    return {
      data: envelope.payload,
      source: "snapshot",
      capturedAt: envelope.snapshot.captured_at_utc,
      ageSeconds: age,
      stale: true,
    };
  }

  return null;
}

/**
 * Load a snapshot only if it was built for these exact parameters.
 *
 * The planner is the one parameterised surface, and only its default
 * combination is published. Answering a request for a four-hour window with
 * the two-hour snapshot would be a wrong answer rather than a stale one, so
 * a mismatch declines the snapshot and falls through to the live call.
 */
export async function loadMatching<T>(
  name: string,
  params: Record<string, number>,
  live: () => Promise<T | null>
): Promise<Loaded<T> | null> {
  const envelope = await readSnapshot<T>(name);
  const published = envelope?.snapshot.params;
  const matches =
    published != null &&
    Object.keys(params).length === Object.keys(published).length &&
    Object.entries(params).every(([key, value]) => published[key] === value);

  if (envelope && matches) {
    const age = ageOf(envelope.snapshot.captured_at_utc);
    if (age <= STALE_AFTER_SECONDS) {
      return {
        data: envelope.payload,
        source: "snapshot",
        capturedAt: envelope.snapshot.captured_at_utc,
        ageSeconds: age,
        stale: false,
      };
    }
  }

  const fresh = await live();
  if (fresh !== null && fresh !== undefined) {
    return { data: fresh, source: "live", capturedAt: null, ageSeconds: 0, stale: false };
  }

  if (envelope && matches) {
    return {
      data: envelope.payload,
      source: "snapshot",
      capturedAt: envelope.snapshot.captured_at_utc,
      ageSeconds: ageOf(envelope.snapshot.captured_at_utc),
      stale: true,
    };
  }

  return null;
}

/** "4 minutes ago", for a reader who needs to know how much to trust a number. */
export function describeAge(seconds: number): string {
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}
