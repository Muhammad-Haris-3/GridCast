"""The serving container must never grow a training stack.

SRS NFR-6 and design doc 12.2 both say the API imports no modelling libraries.
A rule like that erodes the first time someone adds `import pandas` for one
convenient reshape, so it is asserted here rather than remembered.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FORBIDDEN_IN_SERVING = {
    "scikit-learn",
    "sklearn",
    "statsmodels",
    "scipy",
    "pandas",
    "numpy",
    "dbt-core",
    "dbt-postgres",
    "httpx",
    "tenacity",
}


def _package_names(requirements: Path) -> set[str]:
    names: set[str] = set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        name = line.split("==")[0].split(">=")[0].split("[")[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_serving_requirements_contain_no_modelling_stack() -> None:
    installed = _package_names(REPO / "requirements-serve.txt")
    violations = installed & FORBIDDEN_IN_SERVING
    assert not violations, (
        f"requirements-serve.txt must not contain {sorted(violations)}. "
        "The API reads precomputed rows and never trains (NFR-6). If a "
        "serving feature genuinely needs one of these, that feature belongs "
        "in a pipeline job that writes a table the API can read."
    )


def test_api_does_not_import_modelling_libraries() -> None:
    """Static check across the api/ package, in case a dependency arrives transitively."""
    offenders: list[str] = []
    for path in (REPO / "api").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for banned in ("import pandas", "import numpy", "import sklearn", "import statsmodels"):
            if banned in source:
                offenders.append(f"{path.name}: {banned}")
    assert not offenders, f"Modelling imports found in the serving package: {offenders}"
