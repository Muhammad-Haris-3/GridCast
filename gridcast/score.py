"""Score matured forecasts against what actually happened (SRS FR-18).

    python -m gridcast.score

Insert-only, and idempotent through a primary key on `forecast_id`: a forecast
can be scored once, ever. Re-running after a partial failure is safe and
re-scoring is impossible, which matters because a scoring job that could revise
its own past verdicts would undo the point of an append-only register.

SCORING IS SEPARATE FROM ISSUING, deliberately. Different job, different table,
different run. A model that graded its own homework would be a different kind of
project.

WHAT COUNTS AS SCOREABLE.

A forecast is scored when its target period has matured and carries an actual.
Two categories never become scoreable, and both are excluded here rather than
waited on:

  * 625 periods have a permanently null actual (M2 finding B01). A matured
    period with no actual is not pending — there is nothing coming.
  * Periods whose ESO benchmark is missing are excluded from the *comparison*,
    though not from scoring. That distinction lives in the accuracy mart, which
    is where FR-20's "identical periods" is enforced.
"""

from __future__ import annotations

import argparse
import uuid

from gridcast.config import get_settings
from gridcast.db import connect
from gridcast.runlog import RunContext
from gridcast.usage import record_on_exit

# The MASE denominator, computed on history and stored with each score so the
# ratio stays reproducible years later even if the reference series is revised.
MASE_SCALE_SQL = """
    SELECT avg(abs(a.actual_gco2_kwh - b.actual_gco2_kwh))
      FROM marts.fct_intensity_period a
      JOIN marts.fct_intensity_period b
        ON b.sp_start_utc = a.sp_start_utc - interval '24 hours'
     WHERE a.actual_gco2_kwh IS NOT NULL
       AND b.actual_gco2_kwh IS NOT NULL
       AND a.sp_start_utc > now() - interval '2 years'
"""

SCORE_SQL = """
INSERT INTO register.reg_forecast_score
    (forecast_id, actual_gco2_kwh, abs_error, sq_error,
     pinball_10, pinball_90, in_80_interval, in_95_interval,
     scale_mae_seasonal_naive, scoring_commit)
SELECT
    f.forecast_id,
    i.actual_gco2_kwh,
    abs(f.point_gco2_kwh - i.actual_gco2_kwh),
    (f.point_gco2_kwh - i.actual_gco2_kwh) ^ 2,

    -- Pinball loss: the proper scoring rule for a quantile. Asymmetric by tau,
    -- which is what stops an interval being gamed by simply widening it.
    CASE WHEN f.q10_gco2_kwh IS NOT NULL THEN
        greatest(0.10 * (i.actual_gco2_kwh - f.q10_gco2_kwh),
                (0.10 - 1) * (i.actual_gco2_kwh - f.q10_gco2_kwh))
    END,
    CASE WHEN f.q90_gco2_kwh IS NOT NULL THEN
        greatest(0.90 * (i.actual_gco2_kwh - f.q90_gco2_kwh),
                (0.90 - 1) * (i.actual_gco2_kwh - f.q90_gco2_kwh))
    END,

    CASE WHEN f.q10_gco2_kwh IS NOT NULL AND f.q90_gco2_kwh IS NOT NULL
         THEN i.actual_gco2_kwh BETWEEN f.q10_gco2_kwh AND f.q90_gco2_kwh END,
    CASE WHEN f.q025_gco2_kwh IS NOT NULL AND f.q975_gco2_kwh IS NOT NULL
         THEN i.actual_gco2_kwh BETWEEN f.q025_gco2_kwh AND f.q975_gco2_kwh END,

    %(scale)s,
    %(commit)s
FROM register.reg_forecast_point f
JOIN marts.fct_intensity_period i
  ON i.sp_start_utc = f.target_sp_start_utc
WHERE i.is_matured
  AND i.actual_gco2_kwh IS NOT NULL
  AND NOT i.is_permanently_unscoreable
  AND NOT EXISTS (
      SELECT 1 FROM register.reg_forecast_score s
       WHERE s.forecast_id = f.forecast_id
  )
"""


def main() -> int:
    # Registered before any work, so a run that dies mid-read still
    # accounts for what it spent (NFR-13).
    record_on_exit("score")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    print(f"database: {settings.database_url.split('@')[-1].split('?')[0] or 'NOT CONFIGURED'}")

    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(MASE_SCALE_SQL)
        row = cur.fetchone()
        scale = float(row["avg"]) if row and row["avg"] is not None else None

        cur.execute("""
            SELECT count(*) AS pending
              FROM register.reg_forecast_point f
              JOIN marts.fct_intensity_period i ON i.sp_start_utc = f.target_sp_start_utc
             WHERE i.is_matured AND i.actual_gco2_kwh IS NOT NULL
               AND NOT i.is_permanently_unscoreable
               AND NOT EXISTS (SELECT 1 FROM register.reg_forecast_score s
                                WHERE s.forecast_id = f.forecast_id)
        """)
        pending = cur.fetchone()["pending"]

    if scale is None:
        print("no seasonal-naive scale available; cannot compute MASE")
        return 1

    print(f"MASE scale (seasonal naive, trailing 2y) = {scale:.2f}")
    print(f"forecasts awaiting a score: {pending:,}")

    if args.dry_run or pending == 0:
        if pending == 0:
            print("nothing matured yet — expected while the register is young")
        return 0

    run_id = uuid.uuid4()
    with RunContext(run_id, source="register", job="score") as run:
        run.rows_read = pending
        with connect() as conn, conn.cursor() as cur:
            cur.execute(SCORE_SQL, {"scale": round(scale, 4), "commit": settings.build_id})
            run.rows_written = cur.rowcount

    print(f"scored {run.rows_written:,} forecast(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
