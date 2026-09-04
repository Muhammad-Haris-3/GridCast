"""What the published snapshot tree has to contain besides the payloads.

The snapshots branch is an orphan commit of derived JSON, force-pushed every
half hour. Vercel deploys every branch it is given, and it decides whether to
deploy one by reading vercel.json out of the commit it is about to build — at
the project's Root Directory, which for this project is `web`.

That is why the opt-out committed to main on 2026-08-20 never worked. It sits
at web/vercel.json on main, and main is not the branch being deployed. The
snapshots branch had no web/ directory at all, so the file was absent exactly
where it would have been read, and every push failed in one second on "Root
Directory web does not exist" — 48 of them a day, for four months of pipeline
runs, on a branch nobody ever meant to build.

A setting that lives on one branch cannot govern another. These tests hold the
declaration to the tree it describes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridcast import snapshot


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A published tree, built with no database behind it.

    The payload builders are emptied rather than mocked. What is being tested
    is what the tree carries regardless of whether any payload succeeded —
    and the run where every payload fails is exactly the run whose push must
    still not queue a Vercel build.
    """
    monkeypatch.setattr(snapshot, "BUILDERS", {})
    monkeypatch.setattr(
        snapshot.status_router,
        "build_status",
        lambda **_: {"database": "unreachable", "warnings": []},
    )
    snapshot.build(tmp_path)
    return tmp_path


def test_the_published_tree_carries_vercels_opt_out(published: Path) -> None:
    """Without this file on the branch, the setting on main cannot be read."""
    config = published / "web" / "vercel.json"
    assert config.exists(), (
        "The snapshots branch must carry web/vercel.json. Vercel reads it from "
        "the commit being deployed, at the project Root Directory — not from main."
    )

    document = json.loads(config.read_text(encoding="utf-8"))
    assert document["git"]["deploymentEnabled"]["snapshots"] is False


def test_the_opt_out_names_the_branch_rather_than_disabling_everything(
    published: Path,
) -> None:
    """`deploymentEnabled: false` would read more simply and risk far more.

    This copy is published to a branch, and a branch-scoped file being read
    more widely than intended is the kind of thing that is discovered by the
    production site failing to deploy. Naming the branch bounds the blast
    radius to doing nothing.
    """
    document = json.loads((published / "web" / "vercel.json").read_text(encoding="utf-8"))
    enabled = document["git"]["deploymentEnabled"]

    assert isinstance(enabled, dict), "must name branches, not disable all deployments"
    assert set(enabled) == {"snapshots"}


def test_the_opt_out_matches_the_declaration_on_main() -> None:
    """One rule, stated identically in both places it has to hold.

    If these drift, the branch that carries data and the branch that carries
    the site disagree about which branches deploy, and only one of them is
    ever read at the moment it matters.
    """
    on_main = json.loads(Path("web/vercel.json").read_text(encoding="utf-8"))
    assert on_main["git"] == snapshot.VERCEL_OPT_OUT["git"]


def test_the_opt_out_is_not_listed_as_a_payload(published: Path) -> None:
    """The manifest is what the frontend consults to find out what is here.

    Listing a file that carries no snapshot would invite a page to load it and
    render whatever a Vercel config looks like when read as an envelope.
    """
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert "web/vercel.json" not in manifest["files"]
    assert "vercel" not in json.dumps(manifest["files"])
