"use client";

import { useMemo, useState } from "react";

import type { AccuracyRow } from "@/lib/api";

const HORIZON_LABEL: Record<string, string> = {
  H1: "0–3 h",
  H2: "3–12 h",
  H3: "12–24 h",
  H4: "24–48 h",
};

/** The verdict is derived from MASE, not from a hardcoded model name: 1.000 is
 *  the seasonal-naive reference by construction, below it beats the naive, above
 *  it loses. Nothing here assumes which model is the champion. */
function verdictOf(mase: number | null): { label: string; cls: string } {
  if (mase == null) return { label: "—", cls: "ref" };
  if (Math.abs(mase - 1) < 0.0005) return { label: "reference", cls: "ref" };
  return mase < 1 ? { label: "beats naive", cls: "win" } : { label: "loses to naive", cls: "loss" };
}

export default function Scoreboard({
  rows,
  minPublishableN,
  note,
}: {
  rows: AccuracyRow[];
  minPublishableN: number;
  note: string;
}) {
  const groups = useMemo(() => {
    const seen: string[] = [];
    for (const r of rows) if (!seen.includes(r.horizon_group)) seen.push(r.horizon_group);
    return seen.sort();
  }, [rows]);

  const [horizon, setHorizon] = useState(groups[0] ?? "H1");

  const shown = useMemo(
    () =>
      rows
        .filter((r) => r.horizon_group === horizon)
        .slice()
        .sort((a, b) => (a.mae ?? Infinity) - (b.mae ?? Infinity)),
    [rows, horizon]
  );

  const bestMae = Math.min(...shown.map((r) => (r.publishable && r.mae != null ? r.mae : Infinity)));

  const models = useMemo(() => {
    const seen: string[] = [];
    for (const r of rows) if (!seen.includes(r.model_version)) seen.push(r.model_version);
    return seen;
  }, [rows]);

  return (
    <>
      {groups.length > 1 ? (
        <div className="chips" role="group" aria-label="Horizon group">
          {groups.map((g) => (
            <button key={g} type="button" aria-pressed={horizon === g} onClick={() => setHorizon(g)}>
              <b>{g}</b>
              <span>{HORIZON_LABEL[g] ?? g}</span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="card table-scroll">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th className="num">n</th>
              <th className="num">MAE</th>
              <th className="num">RMSE</th>
              <th className="num">MASE</th>
              <th className="num">80% cov.</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const v = verdictOf(r.publishable ? r.mase : null);
              const isBest = r.publishable && r.mae != null && r.mae === bestMae;
              return (
                <tr key={`${r.horizon_group}-${r.model_version}`} className={isBest ? "best" : undefined}>
                  <td>
                    <code>{r.model_version}</code>
                    {!r.publishable ? (
                      <div style={{ fontSize: 12, color: "var(--faint)", marginTop: 5 }}>
                        {(minPublishableN - r.n).toLocaleString("en-GB")} more scored points needed
                      </div>
                    ) : null}
                  </td>
                  <td className="num">{r.n.toLocaleString("en-GB")}</td>
                  <td className="num" style={{ color: isBest ? "var(--accent)" : "var(--text)" }}>
                    {r.publishable ? r.mae?.toFixed(2) : "—"}
                  </td>
                  <td className="num">{r.publishable ? r.rmse?.toFixed(2) : "—"}</td>
                  <td className={`num ${v.cls}`}>{r.publishable ? r.mase?.toFixed(3) : "—"}</td>
                  <td className="num">
                    {r.publishable && r.coverage_80 != null
                      ? `${(r.coverage_80 * 100).toFixed(1)}%`
                      : "—"}
                  </td>
                  <td>
                    <span className={`pill ${v.cls === "win" ? "ok" : v.cls === "loss" ? "bad" : ""}`}>
                      {v.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p>{note}</p>
      </div>

      {groups.length > 1 ? (
        <>
          <h3 style={{ margin: "54px 0 6px" }}>MASE across every horizon</h3>
          <p style={{ color: "var(--dim)", fontSize: 13.5, margin: "0 0 20px" }}>
            Below 1.00 beats the seasonal naive. The reference model is 1.00 by construction. A blank
            cell is a group that has not reached {minPublishableN.toLocaleString("en-GB")} scored
            points.
          </p>
          <div className="card table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  {groups.map((g) => (
                    <th key={g} className="num">
                      {HORIZON_LABEL[g] ?? g}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m}>
                    <td>
                      <code>{m}</code>
                    </td>
                    {groups.map((g) => {
                      const r = rows.find((z) => z.model_version === m && z.horizon_group === g);
                      const value = r && r.publishable && r.mase != null ? r.mase : null;
                      const v = verdictOf(value);
                      // The alpha is computed from how far MASE sits from 1.0, so
                      // this cannot be a plain class. The hue comes from a theme
                      // token holding bare rgb channels — the two themes disagree
                      // about the green, and light needs weaker alphas to stay
                      // readable on white, hence --tint-scale.
                      const tint =
                        value == null || v.cls === "ref"
                          ? "var(--cell-flat)"
                          : value < 1
                            ? `rgba(var(--win-rgb), calc(${(0.06 + (1 - value) * 0.3).toFixed(3)} * var(--tint-scale)))`
                            : `rgba(var(--bad-rgb), calc(${Math.min(0.34, 0.08 + (value - 1) * 0.28).toFixed(3)} * var(--tint-scale)))`;
                      return (
                        <td key={g} className={`num ${v.cls}`} style={{ background: tint }}>
                          {value == null ? "—" : value.toFixed(2)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </>
  );
}
