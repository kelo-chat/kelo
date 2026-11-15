"""Kelo server releases: the signed images a release is made of, and the manifest that names them.

A release is built by .github/workflows/release-images.yml in the public source repository (kelo-chat/kelo) on a version tag
(`v0.5.0`). It builds every image for linux/amd64 and linux/arm64, pushes them to `<registry>/<component>:<version>` and moves the
channel tag (`stable`), signs each one with cosign keyless (the workflow's GitHub OIDC identity; no key exists anywhere), and
writes this manifest (tools/release_manifest.py), which the operator publishes with `assistant publish-server <file>` so that
https://kelo.chat/download/latest.json carries it under the key "server":

    {"schema": 1, "version": "0.5.0", "channel": "stable", "date": "2026-11-09T00:00:00Z", "registry": "ghcr.io/kelo-chat",
     "images": {"server":   {"repo": "ghcr.io/kelo-chat/server",   "digest": "sha256:<64 hex>"},
                "workroom": {"repo": "ghcr.io/kelo-chat/workroom", "digest": "sha256:..."},
                "updater":  {"repo": "ghcr.io/kelo-chat/updater",  "digest": "sha256:..."}},
     "min_app": {"android": "1.0.3", "ios": "1.0.0", "desktop": "1.0.0"},
     "compose_level": 1,
     "notes": {"en": "Short notes", "ko": "짧은 설명"}}

The "server" image runs three roles (server, browser, egress: compose.yml starts it with ROLE=...), so it is one image, as in a
local build. Images built with Google Chrome are never published (CHROME_NOTICE in cli.py); a copy that chose Chrome builds
from source. The Claude and agy CLI sidecars (Dockerfile.cli) are NEVER published either: they contain proprietary programs
(@anthropic-ai/claude-code, Google's agy) that may not be redistributed, so they are always built on the user's own machine; an
update leaves them as they are (each CLI updates itself from the app, providers/cli_update.py; `assistant update --cli claude`
rebuilds one here). A manifest that names them is refused.

What a server trusts (updater_service.py, and `assistant update` when the copy runs release images):
  - the manifest only names images: each repo must be exactly `<configured registry>/<known component>` and each digest a sha256;
    nothing else in it is ever run, mounted or passed to Docker;
  - an image is pulled by that digest (`repo@sha256:...`), and Docker must report the same digest afterwards;
  - its signature must verify with cosign against the release workflow's identity (CERT_IDENTITY / OIDC_ISSUER), or against a
    public key the operator configured (KELO_COSIGN_KEY, for a private registry of your own);
  - its label org.opencontainers.image.version (part of the signed image) must equal the manifest's version, so a manifest cannot
    relabel an older signed release as a newer one, and a version older than the running one is never installed by an update
    (`assistant rollback` is the only way down);
  - `compose_level` above the one in this computer's compose.yml (`x-kelo-compose-level`) means the release needs newer setup files:
    the update is refused with a message to run the installer again.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SCHEMA = 1
DEFAULT_REGISTRY = "ghcr.io/kelo-chat"
DEFAULT_RELEASES_URL = "https://kelo.chat/download/latest.json"
DEFAULT_CHANNEL = "stable"
# what the images are called in the registry -> the compose image variable that runs them (instances.IMAGE_VARS, plus the updater).
# Only Kelo's own images: the CLI sidecars hold proprietary programs and are built locally, never published (see above).
COMPONENTS: dict[str, str] = {
    "server": "KELO_IMAGE",
    "workroom": "KELO_WORKROOM_IMAGE",
    "updater": "KELO_UPDATER_IMAGE",
}
NEVER_PUBLISHED = ("cli-claude", "cli-agy")
REQUIRED = ("server", "workroom", "updater")
VERSION_LABEL = "org.opencontainers.image.version"
RELEASE_LABEL = "chat.kelo.release"           # present (the version) on every published image, never on a local build
# the release workflow that may sign images: only a version tag of kelo-chat/kelo
CERT_IDENTITY = r"^https://github\.com/kelo-chat/kelo/\.github/workflows/release-images\.yml@refs/tags/v[0-9][0-9A-Za-z.+-]*$"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
COSIGN_IMAGE = "ghcr.io/sigstore/cosign/cosign:v2.4.1"   # used when a computer has no cosign of its own (same as Dockerfile.updater)

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]{1,30})?$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REGISTRY = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,252}[a-z0-9])?(:[0-9]{1,5})?(/[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?){1,3}$")
CHANNEL = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
APP_VERSION = re.compile(r"^[0-9][0-9A-Za-z.-]{0,39}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
NOTES_MAX = 2000


class ReleaseError(Exception):
    """The release list or an image failed a check; the message says which (never a secret)."""


@dataclass(frozen=True, slots=True)
class Image:
    repo: str
    digest: str

    @property
    def ref(self) -> str:
        return f"{self.repo}@{self.digest}"


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    channel: str
    date: str
    registry: str
    images: dict[str, Image]
    min_app: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    compose_level: int = 1

    def note(self, lang: str | None) -> str:
        """The notes in the user's language (its base language, then English, then any)."""
        lang = (lang or "").replace("_", "-")
        for key in (lang, lang.split("-")[0], "en"):
            if key and self.notes.get(key):
                return self.notes[key]
        return next(iter(self.notes.values()), "")

    def summary(self, lang: str | None = None) -> dict[str, Any]:
        return {"version": self.version, "channel": self.channel, "date": self.date, "notes": self.note(lang),
                "min_app": dict(self.min_app)}

    def to_json(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "version": self.version, "channel": self.channel, "date": self.date, "registry": self.registry,
                "images": {c: {"repo": i.repo, "digest": i.digest} for c, i in sorted(self.images.items())},
                "min_app": dict(sorted(self.min_app.items())), "compose_level": self.compose_level, "notes": dict(sorted(self.notes.items()))}


def check_registry(registry: str) -> str:
    registry = (registry or "").strip().rstrip("/").lower()
    if not REGISTRY.fullmatch(registry):
        raise ReleaseError(f"{registry!r} is not a registry prefix (example: ghcr.io/kelo-chat)")
    return registry


def version_key(v: str | None) -> tuple[Any, ...]:
    """'0.10.2' > '0.9.9'; a pre-release ('0.5.0-rc.1') sorts before its release; anything unreadable sorts first."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", (v or "").strip())
    if not m:
        return (-1,)
    pre = m.group(4)
    tail: tuple[Any, ...] = (1,) if pre is None else (0, *((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), *tail)


def newer(candidate: str | None, current: str | None) -> bool:
    return bool(candidate) and version_key(candidate) > version_key(current)


def parse(raw: Any, registry: str = DEFAULT_REGISTRY) -> Release:
    """A manifest (the "server" entry of latest.json), checked field by field against `registry`. Raises ReleaseError."""
    registry = check_registry(registry)
    if not isinstance(raw, dict):
        raise ReleaseError("the release list has no server release")
    if raw.get("schema", SCHEMA) != SCHEMA:
        raise ReleaseError(f"the release list uses format {raw.get('schema')!r}; this server reads format {SCHEMA}")
    version = raw.get("version")
    if not (isinstance(version, str) and VERSION.fullmatch(version)):
        raise ReleaseError("the release has no valid version")
    channel = raw.get("channel") or DEFAULT_CHANNEL
    if not (isinstance(channel, str) and CHANNEL.fullmatch(channel)):
        raise ReleaseError("the release has no valid channel")
    date = raw.get("date") or ""
    if not (isinstance(date, str) and (date == "" or DATE.fullmatch(date))):
        raise ReleaseError("the release has no valid date")
    given = raw.get("registry")
    if given is not None and (not isinstance(given, str) or given.rstrip("/").lower() != registry):
        raise ReleaseError(f"the release names another registry than the configured one ({registry})")
    imgs = raw.get("images")
    if not isinstance(imgs, dict):
        raise ReleaseError("the release names no images")
    images: dict[str, Image] = {}
    for comp, row in imgs.items():
        if comp in NEVER_PUBLISHED:
            raise ReleaseError(f"the release names {comp}, which is built on each machine and never published")
        if comp not in COMPONENTS:
            raise ReleaseError(f"the release names an unknown image {str(comp)[:40]!r}")
        if not isinstance(row, dict) or set(row) - {"repo", "digest", "platforms"}:
            raise ReleaseError(f"the release entry for {comp} is not readable")
        repo, digest = row.get("repo"), row.get("digest")
        if repo != f"{registry}/{comp}":
            raise ReleaseError(f"the image {comp} is not in the configured registry ({registry}/{comp})")
        if not (isinstance(digest, str) and DIGEST.fullmatch(digest)):
            raise ReleaseError(f"the image {comp} has no valid sha256 digest")
        images[comp] = Image(repo, digest)
    missing = [c for c in REQUIRED if c not in images]
    if missing:
        raise ReleaseError(f"the release is missing images: {', '.join(missing)}")
    min_app = raw.get("min_app") or {}
    if not isinstance(min_app, dict) or any(k not in ("android", "ios", "desktop") or not (isinstance(v, str) and APP_VERSION.fullmatch(v))
                                            for k, v in min_app.items()):
        raise ReleaseError("the release's minimum app versions are not readable")
    notes = raw.get("notes") or {}
    if isinstance(notes, str):
        notes = {"en": notes}
    if not isinstance(notes, dict) or any(not (isinstance(k, str) and re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", k)) or not isinstance(v, str)
                                          for k, v in notes.items()):
        raise ReleaseError("the release notes are not readable")
    level = raw.get("compose_level", 1)
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 1000:
        raise ReleaseError("the release's setup file level is not readable")
    return Release(version, channel, date, registry, images, dict(min_app), {k: v.strip()[:NOTES_MAX] for k, v in notes.items()}, level)


def from_latest(latest: Any, registry: str = DEFAULT_REGISTRY) -> Release:
    """The server release out of the public list (kelo.chat/download/latest.json)."""
    if not isinstance(latest, dict) or "server" not in latest:
        raise ReleaseError("the release list has no server release yet")
    return parse(latest["server"], registry)


def fetch(url: str = DEFAULT_RELEASES_URL, registry: str = DEFAULT_REGISTRY, *, timeout: float = 15.0,
          get: Callable[[str, float], bytes] | None = None) -> Release:
    """Reads the public list over https (http only for a list on this same machine, used by tests) and returns its server release."""
    if not (url.startswith("https://") or re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/", url)):
        raise ReleaseError("the release list must be read over https")
    body = (get or _get)(url, timeout)
    if len(body) > 1_000_000:
        raise ReleaseError("the release list is too large")
    try:
        latest = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseError("the release list is not readable") from None
    return from_latest(latest, registry)


def _get(url: str, timeout: float) -> bytes:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Kelo-Updater", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - the scheme is checked above
            return r.read(1_000_001)
    except urllib.error.HTTPError as e:
        raise ReleaseError(f"the release list answered HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ReleaseError(f"the release list could not be reached ({type(e).__name__})") from None


# ---------------------------------------------------------------------------------------------------------------- checking images
class Verifier:
    """Pulls each image by its digest and checks it before anything is tagged: same digest, valid signature, matching version.

    `run` is subprocess.run (a fake in tests). `cosign` is the command that runs cosign: ["cosign"] in the updater image, or a
    one-off container of the updater image on a computer without cosign. `key` is a public key file for a registry of the
    operator's own; without it the signature must come from the release workflow (keyless, CERT_IDENTITY)."""

    def __init__(self, run: Callable[..., Any] = subprocess.run, *, cosign: list[str] | None = None, key: str | None = None,
                 identity: str = CERT_IDENTITY, issuer: str = OIDC_ISSUER):
        self.run, self.cosign, self.key, self.identity, self.issuer = run, cosign or ["cosign"], key or None, identity, issuer

    def _r(self, cmd: list[str]) -> Any:
        return self.run(cmd, capture_output=True, text=True)

    @staticmethod
    def _why(r: Any) -> str:
        lines = [ln.strip() for ln in ((getattr(r, "stderr", "") or "") + "\n" + (getattr(r, "stdout", "") or "")).splitlines() if ln.strip()]
        return (lines[-1] if lines else "no output")[:200]

    def pull(self, image: Image) -> None:
        r = self._r(["docker", "pull", "--quiet", image.ref])
        if r.returncode != 0:
            raise ReleaseError(f"downloading {image.repo} failed: {self._why(r)}")

    def digest_ok(self, image: Image) -> None:
        r = self._r(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", image.ref])
        try:
            digests = json.loads((r.stdout or "").strip() or "[]") if r.returncode == 0 else []
        except ValueError:
            digests = []
        if image.ref not in (digests or []):
            raise ReleaseError(f"{image.repo}: the downloaded image does not have the digest the release names")

    def signature_ok(self, image: Image) -> None:
        how = ["--key", self.key] if self.key else ["--certificate-identity-regexp", self.identity, "--certificate-oidc-issuer", self.issuer]
        r = self._r([*self.cosign, "verify", *how, "--output", "json", image.ref])
        if r.returncode != 0:
            raise ReleaseError(f"{image.repo}: the signature does not verify as an official Kelo release ({self._why(r)})")
        try:
            rows = json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            rows = None
        signed = {((row.get("critical") or {}).get("image") or {}).get("docker-manifest-digest") for row in rows or [] if isinstance(row, dict)}
        if image.digest not in signed:
            raise ReleaseError(f"{image.repo}: the signature is not for this digest")

    def labels(self, ref: str) -> dict[str, str]:
        r = self._r(["docker", "image", "inspect", "-f", "{{json .Config.Labels}}", ref])
        try:
            got = json.loads((r.stdout or "").strip() or "null") if r.returncode == 0 else None
        except ValueError:
            got = None
        return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}

    def version_ok(self, image: Image, version: str) -> None:
        if self.labels(image.ref).get(VERSION_LABEL) != version:
            raise ReleaseError(f"{image.repo}: the image says it is another version than the release ({version})")

    def fetch_and_check(self, image: Image, version: str, step: Callable[[str], None] = lambda s: None) -> None:
        step("download")
        self.pull(image)
        step("verify")
        self.digest_ok(image)
        self.signature_ok(image)
        self.version_ok(image, version)


def build_manifest(version: str, digests: dict[str, str], *, registry: str = DEFAULT_REGISTRY, channel: str = DEFAULT_CHANNEL,
                   date: str = "", min_app: dict[str, str] | None = None, notes: dict[str, str] | None = None,
                   compose_level: int = 1) -> dict[str, Any]:
    """The manifest for a release (tools/release_manifest.py in the workflow); checked with parse() before it is returned."""
    registry = check_registry(registry)
    raw = {"schema": SCHEMA, "version": version.lstrip("v"), "channel": channel, "date": date, "registry": registry,
           "images": {c: {"repo": f"{registry}/{c}", "digest": d} for c, d in sorted(digests.items())},
           "min_app": dict(min_app or {}), "compose_level": compose_level, "notes": dict(notes or {})}
    return parse(raw, registry).to_json()


def compose_level(text: str) -> int:
    """`x-kelo-compose-level: N` from a compose.yml (1 when it is not there)."""
    m = re.search(r"^x-kelo-compose-level:\s*(\d+)\s*$", text, re.M)
    return int(m.group(1)) if m else 1


def cosign_command() -> list[str]:
    """cosign on this computer, or the official cosign image run once with Docker."""
    import shutil

    return ["cosign"] if shutil.which("cosign") else ["docker", "run", "--rm", COSIGN_IMAGE]
