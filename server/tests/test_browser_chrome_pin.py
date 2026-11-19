"""The browser image pins its Google Chrome build (review 2026-11-18, fix 4): a rebuild must not move Chrome by itself. The pin is an
explicit build argument, installed from the exact versioned package and held after install."""

import re
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parents[2] / "docker/server.Dockerfile").read_text()


def test_the_chrome_build_is_a_build_argument_with_a_full_version():
    m = re.search(r"^ARG CHROME_VERSION=(\d+\.\d+\.\d+\.\d+-\d+)$", DOCKERFILE, re.M)
    assert m, "docker/server.Dockerfile must pin CHROME_VERSION (full version and package revision)"


def test_chrome_is_installed_from_that_exact_package_and_held():
    assert "google-chrome-stable_${CHROME_VERSION}_${arch}.deb" in DOCKERFILE
    assert "apt-get install -y --no-install-recommends google-chrome-stable;" not in DOCKERFILE   # no unpinned install any more
    assert "apt-mark hold google-chrome-stable" in DOCKERFILE
    assert "/etc/kelo-chrome-version" in DOCKERFILE                                            # the image records which build it has
