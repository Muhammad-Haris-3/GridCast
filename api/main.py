"""GridCast serving API — read-only.

This service reads precomputed rows and never trains (SRS FR-29, NFR-6). It
imports neither scikit-learn nor statsmodels, and tests/test_serving_deps.py
fails the build if either appears in requirements-serve.txt.

That constraint is not housekeeping. Training in the request path is what makes
free-tier containers die under load; training offline and serving reads is also
simply the correct production split.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import forecast as forecast_router
from api.routers import plan as plan_router
from api.routers import status as status_router
from gridcast.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Fail fast and loudly if the two roles are the same. A misconfiguration
    # here silently voids the append-only guarantee, so it must not start.
    get_settings().assert_roles_distinct()
    yield


app = FastAPI(
    title="GridCast API",
    version="0.1.0",
    description=(
        "A self-grading grid carbon forecasting service. Every forecast is "
        "published before its outcome exists and scored automatically once the "
        "actual arrives."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # a public read-only API; tightened to the Vercel origin at M8
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(status_router.router)
app.include_router(forecast_router.router)
app.include_router(plan_router.router)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "service": "gridcast",
        "docs": "/docs",
        "health": "/health",
        "status": "/v1/status",
        "forecast": "/v1/forecast/current",
        "plan": "/v1/plan",
        "accuracy": "/v1/accuracy",
        "integrity": "/v1/integrity",
    }
