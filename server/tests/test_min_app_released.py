"""A minimum app version is never a version that was not released to people (docs/versioning.md, Minimum app version rule).

The first test is the guard in CI: a commit that raises version.json's min_app above a released version fails here."""

import json
from pathlib import Path

from assistant import version as release_version
from assistant.app_releases import AppReleases

ROOT = Path(__file__).resolve().parents[2]


def test_version_json_min_app_is_not_above_a_released_app():
    record = AppReleases.read(ROOT / "releases" / "apps.json")
    rel = release_version.read(ROOT / "version.json")
    problems = record.check_min(rel.min_app)
    assert not problems, (
        f"version.json min_app {rel.min_app} is above an app people can install. Lower it, or record the release once it is live "
        "(assistant release-record <platform> <version> <build> <channel>):\n" + "\n".join(p.message for p in problems))


def test_the_record_is_valid_and_shipped_in_the_image():
    record = AppReleases.read(ROOT / "releases" / "apps.json")
    assert set(record.entries) == {"android", "ios", "desktop"}
    assert json.loads((ROOT / "releases" / "apps.json").read_text()) == record.to_json()   # saved in the form the tool writes
    assert (ROOT / "releases" / "apps.json").read_text() == record.dumps()
    assert "COPY releases ./releases" in (ROOT / "docker" / "server.Dockerfile").read_text()
