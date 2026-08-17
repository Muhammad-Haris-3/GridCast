"""Publish the serving payloads as static JSON (M9, NFR-13).

    python -m gridcast.snapshot                  # write to snapshots/
    python -m gridcast.snapshot --out some/dir

Every route this project serves reads rows that already exist and change at
most once every thirty minutes, when the pipeline issues. Serving them from
Postgres per visitor was therefore paying a database read — and a cold start —
for an answer that was decided long before anyone asked.

That was not a theoretical cost. On 2026-08-17 the Neon project's **data
transfer** allowance ran out. Everything that reads draws on it: the scheduled
pipeline, the serving API, and every page view. When it went, it took the whole
surface with it — `/v1/accuracy` and `/v1/leaderboard` returned 500, the status
page reported the database unreachable, and the register stopped growing,
because the pipeline could not read either. The scoreboard that is the entire
claim of this project went blank, and the milestone waiting on live data
(M7) stopped being a matter of waiting.

This module breaks the dependency. Once per run the payloads are computed
**once**, from the pipeline's own connection, and written as flat JSON. The
frontend reads those files from a CDN. A visitor then costs zero database
reads, zero container wake-ups, and keeps working while the database is down —
serving the last good answer with its age stated, which is a far better page
than a 500.

The snapshot is a **cache, not a record**. The register in Postgres and the
seals in git are the record, and nothing here is load-bearing for the
append-only guarantee. That distinction is why the publishing branch is
force-pushed rather than accumulated (see .github/workflows/pipeline.yml): a
derived file with a git history invites someone to audit the wrong artefact.
"""

from __future__ import annotations

import argparse
import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

from api.routers import forecast as forecast_router
from api.routers import plan as plan_router
from api.routers import status as status_router
from api.routers.status import _redact
from gridcast.config import get_settings
from gridcast.usage import record_on_exit

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"

# The planner is the one parameterised surface, so a snapshot of it is a
# snapshot of one parameter combination. These are the defaults web/app/plan
# renders when nobody has touched the controls, which is what almost every
# visitor sees; other combinations fall through to the live API, which is the
# right split because moving a slider is a deliberate act and waiting for it is
# tolerable in a way that waiting for the first paint is not.
#
# Keep in step with the defaults in web/app/plan/page.tsx. They are duplicated
# rather than shared because the alternative is a build-time dependency from the
# pipeline to the frontend, and a mismatch here degrades to a live fetch rather
# than to a wrong answer.
PLAN_DEFAULTS = {"duration_hours": 2.0, "within_hours": 24.0, "appliance_kwh": 1.4}

# Every argument is passed explicitly, including the ones that have defaults.
#
# These are FastAPI route functions, and their defaults are `Query(...)`
# objects rather than values — FastAPI substitutes real arguments when it
# serves a request, and nothing does that here. Calling `accuracy()` bare would
# quietly pass a Query instance as the model filter and compare it to a text
# column. Only the request path gets to rely on those defaults.
BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "forecast_current": lambda: forecast_router.current_forecast(),
    "accuracy": lambda: forecast_router.accuracy(model=None),
    "leaderboard": lambda: forecast_router.leaderboard(),
    "models": lambda: forecast_router.models(),
    "integrity": lambda: forecast_router.integrity(),
    "plan": lambda: plan_router.plan(**PLAN_DEFAULTS),
}

# Recorded alongside the plan snapshot so the frontend can refuse to use it for
# a different parameter combination rather than silently answering the wrong
# question.
PARAMS: dict[str, dict[str, Any]] = {"plan": PLAN_DEFAULTS}


def _reason(exc: Exception) -> str:
    """The failure, in a form that is safe to publish.

    The manifest is pushed to a public branch and served to every visitor, so
    it is held to the same standard as /v1/status: classify or redact, never
    echo. A driver message carries the host, port and role, and the failure
    this module exists to survive is precisely a connection failure — so the
    unredacted path is not the rare case here, it is the expected one.

    The full text still reaches the Actions log a few lines below. Those logs
    are private, and that is where an operator debugging this will be looking.
    """
    return f"{type(exc).__name__}: {_redact(exc)}"


def envelope(name: str, payload: dict[str, Any], captured_at: datetime) -> dict[str, Any]:
    """Wrap a payload with its age and provenance.

    Wrapped rather than merged. Adding `captured_at_utc` beside the payload's
    own keys would work today and collide the first time a route grows a field
    with that name, and the failure would be a silently overwritten value
    rather than an error.

    The age is the point of the envelope. A page serving a cached answer has to
    be able to say how old it is; one that cannot is indistinguishable from a
    page serving a live answer, which is how a frozen dashboard goes unnoticed
    for a week.
    """
    return {
        "snapshot": {
            "name": name,
            "captured_at_utc": captured_at.isoformat(),
            "commit": get_settings().build_id,
            "params": PARAMS.get(name),
        },
        "payload": jsonable_encoder(payload),
    }


def write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def build(out_dir: Path) -> int:
    """Compute every payload and write the ones that succeed.

    A payload that raises leaves the previous file untouched. That is the
    important behaviour in this module: the failure this exists to survive is
    the database being unreachable, and the worst possible response to it would
    be to overwrite six good answers with six error documents. A stale
    scoreboard labelled stale is useful. A scoreboard replaced by an error is
    the outage, propagated.

    Returns the number of payloads that failed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(UTC)

    files: dict[str, Any] = {}
    failures: dict[str, str] = {}

    # Status first, and unconditionally.
    #
    # It is the one route built to degrade instead of raising: when the
    # database is unreachable it returns a diagnosis of *why*, which is
    # precisely the payload worth publishing during an outage. Treating it like
    # the others — write only on success — would withhold the explanation at
    # the exact moment every other page needs to point at one.
    try:
        write(out_dir / "status.json", envelope("status", status_router.status(), captured_at))
        files["status"] = {"captured_at_utc": captured_at.isoformat()}
    except Exception as exc:  # noqa: BLE001 — a broken status page must not stop the rest
        failures["status"] = _reason(exc)
        print(f"  {'status':<17}FAILED  {type(exc).__name__}: {exc}")
        traceback.print_exc()

    for name, builder in BUILDERS.items():
        try:
            payload = builder()
        except Exception as exc:  # noqa: BLE001 — one bad payload must not lose the others
            failures[name] = _reason(exc)
            print(f"  {name:<17}FAILED  {type(exc).__name__}: {exc}")
            continue

        write(out_dir / f"{name}.json", envelope(name, payload, captured_at))
        files[name] = {"captured_at_utc": captured_at.isoformat()}
        print(f"  {name:<17}ok")

    # The manifest is written even when everything else failed, because "the
    # snapshot job ran at 14:30 and could produce nothing" is a fact the
    # frontend needs in order to distinguish a stale cache from an abandoned
    # one. Its own timestamp is the only evidence that anything tried.
    write(
        out_dir / "manifest.json",
        {
            "captured_at_utc": captured_at.isoformat(),
            "commit": get_settings().build_id,
            "files": files,
            "failed": failures,
            "note": (
                "Derived cache of the serving payloads, rebuilt every pipeline run. "
                "The forecast register and the monthly seals are the record; this is not."
            ),
        },
    )

    return len(failures)


def main() -> int:
    # Registered before any work, so a run that dies mid-read still
    # accounts for what it spent (NFR-13).
    record_on_exit("snapshot")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=SNAPSHOT_DIR,
        help="Directory to write the JSON files into (default: snapshots/)",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"database: {settings.serving_host}")
    print(f"writing snapshots to {args.out}:")

    failures = build(args.out)
    produced = len(BUILDERS) + 1 - failures

    if failures:
        print(
            f"::warning title=Snapshot::{failures} payload(s) could not be built and their "
            "previous files were left in place. The frontend will serve them with their real age."
        )

    # Non-zero only when nothing at all could be produced. A partial snapshot is
    # a working site with one stale panel; an empty one means the publisher is
    # broken in a way the pipeline's other steps do not already report.
    if produced == 0:
        print(
            "::error title=Snapshot::No payload could be built. The static surface is "
            "now serving entirely stale data and will keep doing so until this passes."
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
