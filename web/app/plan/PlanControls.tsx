"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";

const WITHIN = [12, 24, 48];

/**
 * The planner's inputs. They are URL state, not component state: the server page
 * reads duration/within/kwh from searchParams, and getPlan caches per parameter
 * combination. So the sliders write to the URL and let the server re-render.
 *
 * Slider drags are debounced — without it every intermediate value would push a
 * history entry and fire a request.
 */
export default function PlanControls({
  duration,
  within,
  kwh,
}: {
  duration: number;
  within: number;
  kwh: number;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const [isPending, startTransition] = useTransition();

  const [local, setLocal] = useState({ duration, within, kwh });

  // Keep in step when the server sends different values back (or on Back).
  useEffect(() => {
    setLocal({ duration, within, kwh });
  }, [duration, within, kwh]);

  function push(next: { duration: number; within: number; kwh: number }) {
    const q = new URLSearchParams(params.toString());
    q.set("duration", String(next.duration));
    q.set("within", String(next.within));
    q.set("kwh", String(next.kwh));
    startTransition(() => {
      router.replace(`/plan?${q.toString()}`, { scroll: false });
    });
  }

  // Debounce the two sliders; the segmented control commits immediately.
  useEffect(() => {
    if (local.duration === duration && local.kwh === kwh && local.within === within) return;
    const t = setTimeout(() => push(local), 320);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [local.duration, local.kwh]);

  return (
    <div className="controls" data-pending={isPending}>
      <div className="field">
        <label htmlFor="duration">
          <span>Duration</span>
          <b>{local.duration} h</b>
        </label>
        <input
          id="duration"
          type="range"
          min={0.5}
          max={8}
          step={0.5}
          value={local.duration}
          onChange={(e) => setLocal((s) => ({ ...s, duration: Number(e.target.value) }))}
        />
      </div>

      <div className="field">
        <label htmlFor="kwh">
          <span>Energy</span>
          <b>{local.kwh} kWh</b>
        </label>
        <input
          id="kwh"
          type="range"
          min={0.2}
          max={12}
          step={0.2}
          value={local.kwh}
          onChange={(e) => setLocal((s) => ({ ...s, kwh: Number(e.target.value) }))}
        />
      </div>

      <div className="field">
        {/* Not a <label htmlFor>: pointing one at a button makes the button's
            accessible name "Search window" instead of "12 h". The group's own
            aria-labelledby carries the label to the assistive tree. */}
        <span className="field-label" id="within-label">
          Search window
        </span>
        <div className="seg" role="group" aria-labelledby="within-label">
          {WITHIN.map((w) => (
            <button
              key={w}
              type="button"
              aria-pressed={local.within === w}
              onClick={() => {
                const next = { ...local, within: w };
                setLocal(next);
                push(next);
              }}
            >
              {w} h
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
