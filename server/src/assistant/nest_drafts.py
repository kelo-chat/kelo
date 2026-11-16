"""Kelo Nest drafts made by talking, and sharing on GitHub (docs/nest.md)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from . import nest_manifest as nm
from . import secret_guard
from .agent.loop import Tool, emit_ui
from .i18n import t
from .netguard import NetGuardError, Reply, fetch
from .oauth_connect import DeviceFlows
from .package_index import MAX_DOWNLOAD, git_archive_url, netguard_fetcher, unpack
from .packages import (
    CONNECTOR_ID,
    FILE_NAME,
    Package,
    PackageError,
    SKILL_ID,
    _scan,
    builtin_ids,
    content_hash,
    contents,
    file_hashes,
    load_package,
)
from .store import Store

log = logging.getLogger("nest.drafts")

MAX_DRAFT_FILE = 256 * 1024       # at most 256 KB per file
MAX_DRAFT_TOTAL = 2 * 1024 * 1024  # at most 2 MB per draft
LINK_PREVIEW_SECONDS = 15 * 60     # a package read from a GitHub link can be installed this long after it was shown
MAX_PREVIEWS = 20                  # link previews kept at once (the oldest go first)
ALLOWED_EXACT = ("kelo-package.json", "SKILL.md")
ALLOWED_PREFIXES = ("ui/", "assets/")
FORBIDDEN_EXTS = {
    ".py", ".sh", ".bash", ".zsh", ".exe", ".bat", ".cmd", ".bin",
    ".elf", ".so", ".dylib", ".dll", ".ps1", ".jar", ".app",
}
NEST_SHARE_MARK = "[Handoff to share a Nest draft to GitHub, not from the user]"
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
GITHUB_URL_RE = re.compile(
    r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:\.git)?(?:/tree/([a-zA-Z0-9_./-]+))?/?$"
)
GITHUB_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def github_token(store: Store) -> str:
    """The owner's GitHub token (the github-api credential), or "" when there is none."""
    cred = store.credential("github-api") or {}
    token = str(cred.get("secret") or "")
    if token.startswith("{"):
        try:
            token = json.loads(token).get("access_token", token)
        except Exception:
            pass
    return token


def github_headers(store: Store) -> dict[str, str]:
    """Headers for api.github.com: the owner's token when there is one (it is sent to that host only)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Kelo-Assistant"}
    token = github_token(store)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_repo(url: str) -> tuple[str, str] | None:
    """(owner, repo) of a plain https://github.com/<owner>/<repo> address, else None."""
    m = GITHUB_URL_RE.match((url or "").strip())
    if not m or m.group(3):
        return None
    owner, repo = m.group(1), m.group(2)
    if owner in (".", "..") or repo in (".", ".."):
        return None
    return (owner, repo)


class DraftError(ValueError):
    """An error handling a package draft or sharing it."""

    def __init__(self, message: str, status: int = 400, code: str = "", extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.extra = extra or {}


def bump_draft_version(version: str) -> str:
    """Bump version to 0.0.N+1 (default 0.0.1)."""
    m = re.match(r"^0\.0\.(\d+)$", (version or "").strip())
    if m:
        return f"0.0.{int(m.group(1)) + 1}"
    return "0.0.1"


def check_draft_path(path: str) -> str:
    """Validate relative path within a draft folder: only kelo-package.json, SKILL.md, ui/*, assets/*."""
    p = (path or "").replace("\\", "/").strip()
    if p.startswith("/") or p.startswith("\\"):
        raise DraftError(f"Absolute path not allowed: {path!r}", 422)
    if not p or "\0" in p:
        raise DraftError(f"Invalid draft path: {path!r}", 422)
    parts = [seg for seg in p.split("/") if seg]
    if ".." in parts or "." in parts:
        raise DraftError(f"Path traversal not allowed: {path!r}", 422)
    norm = "/".join(parts)
    if norm in ALLOWED_EXACT:
        pass
    elif any(norm.startswith(pre) for pre in ALLOWED_PREFIXES):
        pass
    else:
        raise DraftError(
            f"Path {path!r} is not allowed: only kelo-package.json, SKILL.md, and files under ui/ and assets/ are allowed",
            422,
        )
    ext = Path(norm).suffix.lower()
    if ext in FORBIDDEN_EXTS:
        raise DraftError(f"Executable or script file {norm!r} is not allowed in package drafts", 422)
    return norm


class DraftManager:
    """Manages package drafts on this server (<state>/package-drafts/<id>/)."""

    def __init__(
        self,
        drafts_dir: Path | str | None,
        registry: Any,
        store: Store,
        *,
        privacy_guard: Any = None,
        http: Any = None,
        fetcher: Any = None,
        clock: Callable[[], float] = time.time,
    ):
        self.drafts_dir = Path(drafts_dir) if drafts_dir else None
        self.registry = registry
        self.store = store
        self.privacy_guard = privacy_guard
        self.http = http or fetch
        self.fetcher = fetcher or netguard_fetcher
        self.clock = clock
        self._previews: dict[str, dict[str, Any]] = {}   # link previews waiting for the owner's install (install_link)

    def draft_folder(self, pid: str) -> Path:
        if self.drafts_dir is None:
            raise DraftError("Drafts are not configured on this server", 404)
        return self.drafts_dir / pid

    def create(self, kind: str, pid: str, name: str, description: str) -> dict[str, Any]:
        """Create a new package draft."""
        if self.drafts_dir is None:
            raise DraftError("Drafts are not configured on this server", 404)
        kind = (kind or "").strip().lower()
        if kind not in ("skill", "connector", "extension"):
            raise DraftError("kind must be skill, connector or extension", 422)
        pid = (pid or "").strip().lower()
        rule = CONNECTOR_ID if kind == "connector" else SKILL_ID
        if not rule.fullmatch(pid):
            msg = (
                "lower-case letters, digits and '_', starting with a letter"
                if kind == "connector"
                else "lower-case letters, digits and '-'"
            )
            raise DraftError(f"id {pid!r} is not valid ({msg}, 2-48 characters)", 422)
        if pid in builtin_ids():
            raise DraftError(f"id {pid!r} belongs to a built-in package", 422)
        folder = self.draft_folder(pid)
        if folder.exists():
            raise DraftError(f"Draft {pid!r} already exists", 409)
        folder.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "format": 1,
            "id": pid,
            "kind": kind,
            "version": "0.0.1",
            "name": name.strip(),
            "description": description.strip(),
        }
        if kind == "skill":
            manifest["skill"] = {}
            skill_text = f"# {name.strip()}\n\n{description.strip()}\n"
            (folder / "SKILL.md").write_text(skill_text, encoding="utf-8")
        elif kind == "extension":
            manifest["ui"] = {"schema": 1, "surfaces": [], "screens": {}}
        elif kind == "connector":
            manifest["connector"] = {
                "regions": [],
                "connect": {"type": "mcp", "url": "https://example.com/mcp", "blurb": description.strip()},
            }

        (folder / "kelo-package.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.registry.refresh()
        return {"ok": True, "id": pid, "kind": kind, "version": "0.0.1"}

    def write(self, pid: str, path: str, content: str) -> dict[str, Any]:
        """Write or update a file in a draft folder, bumping version to 0.0.N."""
        folder = self.draft_folder(pid)
        if not folder.is_dir():
            raise DraftError(f"Draft {pid!r} not found", 404)
        rel = check_draft_path(path)
        raw = content.encode("utf-8")
        if len(raw) > MAX_DRAFT_FILE:
            raise DraftError(f"File {rel} exceeds 256 KB limit ({len(raw)} bytes)", 422)

        target = folder / rel
        old_size = target.stat().st_size if target.exists() else 0
        _, current_total = _scan(folder)
        new_total = current_total - old_size + len(raw)
        if new_total > MAX_DRAFT_TOTAL:
            raise DraftError(f"Draft exceeds 2 MB total limit ({new_total} bytes)", 422)

        manifest_file = folder / "kelo-package.json"
        prev_ver = "0.0.0"
        if manifest_file.exists():
            try:
                m_obj = json.loads(manifest_file.read_text(encoding="utf-8"))
                prev_ver = m_obj.get("version", "0.0.0")
            except Exception:
                pass
        new_ver = bump_draft_version(prev_ver)

        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == "kelo-package.json":
            try:
                m_data = json.loads(content)
            except Exception as e:
                raise DraftError(f"kelo-package.json is not valid JSON: {e}", 422) from None
            m_data["version"] = new_ver
            target.write_text(json.dumps(m_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            target.write_text(content, encoding="utf-8")
            if manifest_file.exists():
                try:
                    m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    m_data["version"] = new_ver
                    manifest_file.write_text(
                        json.dumps(m_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                except Exception:
                    pass

        self.registry.refresh()
        return {"ok": True, "id": pid, "path": rel, "version": new_ver}

    def check(self, pid: str) -> dict[str, Any]:
        """Validate a draft package and return findings."""
        folder = self.draft_folder(pid)
        if not folder.is_dir():
            raise DraftError(f"Draft {pid!r} not found", 404)
        warnings: list[str] = []
        try:
            pkg = load_package(folder, source="draft", warnings=warnings, check_name=False)
            hashes = file_hashes(folder)
            digest = content_hash(hashes)
            ctype = pkg.connect.get("type", "") if pkg.kind == "connector" else ""
            words = nm.describe(pkg.permissions, kind=pkg.kind, connect_type=ctype, lang="en")
            return {
                "valid": True,
                "id": pkg.id,
                "kind": pkg.kind,
                "version": pkg.version,
                "content_hash": digest,
                "warnings": warnings,
                "permissions": words,
                "declared": pkg.declared,
            }
        except (PackageError, OSError) as e:
            return {"valid": False, "id": pid, "error": str(e), "warnings": warnings}

    def delete(self, pid: str) -> bool:
        """Delete a draft folder."""
        folder = self.draft_folder(pid)
        if not folder.exists():
            return False
        shutil.rmtree(folder)
        self.registry.refresh()
        return True

    def details(self, pid: str) -> dict[str, Any]:
        """Return the files (text) and the validator's findings for GET /api/nest/drafts/{id}."""
        folder = self.draft_folder(pid)
        if not folder.is_dir():
            raise DraftError(f"Draft {pid!r} not found", 404)
        files: dict[str, str] = {}
        for root, _, fnames in os.walk(folder):
            for f in sorted(fnames):
                if f.startswith(".shared"):
                    continue
                full = Path(root) / f
                rel = full.relative_to(folder).as_posix()
                try:
                    files[rel] = full.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        chk = self.check(pid)
        findings = []
        if not chk.get("valid"):
            findings.append(chk.get("error", "Validation failed"))
        findings.extend(chk.get("warnings", []))
        return {
            "id": pid,
            "files": files,
            "valid": chk.get("valid", False),
            "findings": findings,
            "permissions": chk.get("permissions", []),
            "version": chk.get("version", "0.0.1"),
            "kind": chk.get("kind", ""),
        }

    def summary_list(self, lang: str | None = None) -> list[dict[str, Any]]:
        """Return items list for GET /api/nest/drafts."""
        out: list[dict[str, Any]] = []
        if not self.drafts_dir or not self.drafts_dir.is_dir():
            return out
        for d in sorted(self.drafts_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            manifest_file = d / "kelo-package.json"
            if not manifest_file.is_file():
                continue
            try:
                m = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            try:
                mtime = max(f.stat().st_mtime for f in d.glob("**/*") if f.is_file())
            except ValueError:
                mtime = d.stat().st_mtime
            shared = None
            shared_file = d / ".shared.json"
            if shared_file.is_file():
                try:
                    shared = json.loads(shared_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            out.append({
                "id": d.name,
                "kind": m.get("kind", "skill"),
                "name": m.get("name", d.name),
                "version": m.get("version", "0.0.1"),
                "updated_at": mtime,
                "shared": shared,
            })
        return out

    drafts_summary = summary_list
    summary = summary_list
    draft_details = details

    def copy_custom_skill(self, skill_id: str, draft_id: str | None = None) -> Package:
        """Turn an approved custom skill into a draft ('share this skill')."""
        target_id = draft_id or skill_id
        target_folder = self.draft_folder(target_id)
        if target_folder.exists():
            raise DraftError(f"Draft {target_id!r} already exists", 409)
        src_dir = self.registry.custom_dir / skill_id if self.registry.custom_dir else None
        if not src_dir or not src_dir.is_dir():
            raise DraftError(f"Custom skill {skill_id!r} not found", 404)
        target_folder.mkdir(parents=True, exist_ok=True)
        for f in src_dir.glob("*.md"):
            shutil.copy2(f, target_folder / f.name)
        src_manifest = src_dir / "kelo-package.json"
        if src_manifest.is_file():
            shutil.copy2(src_manifest, target_folder / "kelo-package.json")
        else:
            skill_file = target_folder / "SKILL.md"
            name = target_id.replace("-", " ").title()
            desc = ""
            if skill_file.is_file():
                from .home import front_matter
                meta, _ = front_matter(skill_file.read_text(encoding="utf-8"))
                name = meta.get("name") or name
                desc = meta.get("description") or desc
            manifest = {
                "format": 1,
                "id": target_id,
                "kind": "skill",
                "version": "0.0.1",
                "name": name,
                "description": desc,
                "skill": {},
            }
            (target_folder / "kelo-package.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        self.registry.refresh()
        pkg = self.registry.draft(target_id)
        if not pkg:
            raise DraftError(f"Could not load copied draft {target_id}", 500)
        return pkg

    def draft_files(self, pid: str) -> list[str]:
        """List relative file paths in a draft."""
        folder = self.draft_folder(pid)
        if not folder.is_dir():
            return []
        files = []
        for root, _, fnames in os.walk(folder):
            for f in sorted(fnames):
                if f.startswith(".shared"):
                    continue
                files.append(Path(root, f).relative_to(folder).as_posix())
        return files

    def has_github_write(self) -> bool:
        """Whether a GitHub connection exists that can write repositories."""
        cred = self.store.credential("github-api")
        if not cred or not cred.get("secret"):
            return False
        scope = DeviceFlows(self.store).granted_scope("github")
        return scope in ("private", "public")

    def get_github_account(self) -> str:
        """Owner's GitHub account name."""
        cred = self.store.credential("github-api") or {}
        label = str(cred.get("label") or "")
        if " · @" in label:
            return label.split(" · @")[-1].strip()
        if " · " in label:
            return label.split(" · ")[-1].strip()
        return "owner"

    async def share_to_github(
        self, pid: str, repo: str, public: bool = True, http: Any = None
    ) -> dict[str, Any]:
        """Share a draft to GitHub with Git Data API. Performs pre-flight checks before any HTTP call."""
        if not self.has_github_write():
            raise DraftError("github_write_needed", status=409, code="github_write_needed")

        folder = self.draft_folder(pid)
        if not folder.is_dir():
            raise DraftError(f"Draft {pid!r} not found", 404)

        # 1. Pre-flight checks on EVERY file
        try:
            pkg = load_package(folder, source="draft", check_name=False)
        except PackageError as e:
            raise DraftError(f"Pre-flight validator failed: {e}", 422) from None

        file_contents: dict[str, str] = {}
        for root, _, fnames in os.walk(folder):
            for f in sorted(fnames):
                if f.startswith(".shared"):
                    continue
                full = Path(root) / f
                rel = full.relative_to(folder).as_posix()
                content = full.read_text(encoding="utf-8", errors="replace")

                # Secret scan
                hits = secret_guard.find(content)
                if hits:
                    kinds = ", ".join(secret_guard.kinds(hits))
                    raise DraftError(
                        f"Secret scan found {kinds} in {rel}; stopped before sharing.",
                        422,
                        code="secret_found",
                    )

                # Privacy guard
                if self.privacy_guard is not None:
                    cats = self.privacy_guard.scan(content)
                    if cats:
                        raise DraftError(
                            f"Privacy guard found personal data ({', '.join(cats)}) in {rel}; stopped before sharing.",
                            422,
                            code="privacy_data_found",
                        )

                file_contents[rel] = content

        # Ensure README.md and MIT LICENSE
        if "README.md" not in file_contents:
            file_contents["README.md"] = f"# {pkg.name}\n\n{pkg.description}\n\nA Kelo {pkg.kind} package.\n"
        if "LICENSE" not in file_contents and "LICENSE.md" not in file_contents:
            account_name = self.get_github_account()
            file_contents["LICENSE"] = (
                f"MIT License\n\nCopyright (c) {account_name}\n\n"
                "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
                "of this software and associated documentation files (the \"Software\"), to deal\n"
                "in the Software without restriction, including without limitation the rights\n"
                "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
                "copies of the Software, and to permit persons to whom the Software is\n"
                "furnished to do so, subject to the following conditions:\n\n"
                "The above copyright notice and this permission notice shall be included in all\n"
                "copies or substantial portions of the Software.\n"
            )

        # 2. HTTP Git Data API
        token = github_token(self.store)
        http_fn = http or self.http

        async def gh_call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
            url = f"https://api.github.com{path}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Kelo-Assistant",
            }
            content = json.dumps(body).encode("utf-8") if body is not None else None
            r = await http_fn(method, url, allowed_hosts=["api.github.com"], headers=headers, content=content)
            data = None
            if r.body:
                try:
                    data = json.loads(r.body.decode("utf-8"))
                except Exception:
                    data = r.body.decode("utf-8")
            return r.status, data

        # Get authenticated user login
        u_status, u_data = await gh_call("GET", "/user")
        owner = u_data.get("login") if isinstance(u_data, dict) else None
        if not owner:
            owner = self.get_github_account()

        # Check or create repo
        repo_clean = repo.strip()
        r_status, r_data = await gh_call("GET", f"/repos/{owner}/{repo_clean}")
        if r_status == 404:
            create_payload = {
                "name": repo_clean,
                "private": not public,
                "description": pkg.description or f"Kelo package: {pkg.name}",
                "auto_init": False,
            }
            c_status, c_data = await gh_call("POST", "/user/repos", create_payload)
            if c_status >= 400:
                err_msg = c_data.get("message") if isinstance(c_data, dict) else str(c_status)
                raise DraftError(f"Could not create repository {repo_clean}: {err_msg}", c_status)

        # Blobs -> Tree -> Commit -> Update Ref
        # 2a. Blobs
        tree_items = []
        for rel_path, text in sorted(file_contents.items()):
            b_status, b_data = await gh_call(
                "POST", f"/repos/{owner}/{repo_clean}/git/blobs", {"content": text, "encoding": "utf-8"}
            )
            blob_sha = b_data.get("sha") if isinstance(b_data, dict) else None
            if not blob_sha:
                raise DraftError(f"Failed to create blob for {rel_path}", 502)
            tree_items.append({"path": rel_path, "mode": "100644", "type": "blob", "sha": blob_sha})

        # 2b. Check current ref
        ref_status, ref_data = await gh_call("GET", f"/repos/{owner}/{repo_clean}/git/ref/heads/main")
        if ref_status == 404:
            ref_status, ref_data = await gh_call("GET", f"/repos/{owner}/{repo_clean}/git/refs/heads/main")
        parent_commit = None
        base_tree = None
        ref_existed = False
        if ref_status == 200 and isinstance(ref_data, dict):
            parent_commit = ref_data.get("object", {}).get("sha")
            ref_existed = bool(parent_commit)
            if parent_commit:
                cm_status, cm_info = await gh_call("GET", f"/repos/{owner}/{repo_clean}/git/commits/{parent_commit}")
                if isinstance(cm_info, dict):
                    base_tree = cm_info.get("tree", {}).get("sha")

        # 2c. Tree
        tree_payload: dict[str, Any] = {"tree": tree_items}
        if base_tree:
            tree_payload["base_tree"] = base_tree
        t_status, t_data = await gh_call("POST", f"/repos/{owner}/{repo_clean}/git/trees", tree_payload)
        tree_sha = t_data.get("sha") if isinstance(t_data, dict) else None
        if not tree_sha:
            raise DraftError("Failed to create tree", 502)

        # 2d. Commit
        commit_payload = {
            "message": f"Kelo Nest: {pkg.name} {pkg.version}",
            "tree": tree_sha,
            "parents": [parent_commit] if parent_commit else [],
        }
        cm_status, cm_data = await gh_call("POST", f"/repos/{owner}/{repo_clean}/git/commits", commit_payload)
        commit_sha = cm_data.get("sha") if isinstance(cm_data, dict) else None
        if not commit_sha or len(commit_sha) != 40:
            raise DraftError("Failed to create commit", 502)

        # 2e. Update ref
        if ref_existed:
            await gh_call(
                "PATCH", f"/repos/{owner}/{repo_clean}/git/refs/heads/main", {"sha": commit_sha, "force": False}
            )
        else:
            await gh_call(
                "POST", f"/repos/{owner}/{repo_clean}/git/refs", {"ref": "refs/heads/main", "sha": commit_sha}
            )

        # Record shared on draft
        shared_info = {
            "repo": f"{owner}/{repo_clean}",
            "commit": commit_sha,
            "at": int(self.clock()),
        }
        (folder / ".shared.json").write_text(
            json.dumps(shared_info, indent=2) + "\n", encoding="utf-8"
        )
        self.registry.refresh()
        return {
            "repo": f"{owner}/{repo_clean}",
            "url": f"https://github.com/{owner}/{repo_clean}",
            "commit": commit_sha,
        }

    async def fetch_commit(self, owner: str, repo: str, commit: str, tmp: Path, fetch: Any = None,
                           lang: str | None = None) -> tuple[Path, str, str]:
        """One commit of a GitHub repository, unpacked into `tmp`: (the package folder, the commit the archive says it is, its content
        hash). The archive is fetched from the forge's commit archive only. Raises DraftError (422)."""
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        if safe_owner in (".", "..") or safe_repo in (".", ".."):
            raise DraftError("invalid repository name", status=422)
        try:
            data = await (fetch or self.fetcher)(git_archive_url(f"https://github.com/{safe_owner}/{safe_repo}", commit), MAX_DOWNLOAD)
        except Exception as e:
            raise DraftError(f"Could not download package archive: {e}", status=422) from None
        try:
            folder, got = unpack(data, tmp, strip_top=True)
        except PackageError as e:
            raise DraftError(t("not a package: {why}", lang=lang, why=str(e)), status=422) from None
        return folder, got, content_hash(file_hashes(folder))

    def _installed_pkg(self, pid: str) -> Package | None:
        """The installed package with this id (turned off ones too), or None."""
        p = self.registry.get(pid)
        if p is not None and p.installed:
            return p
        return self.registry.disabled_package(pid)

    async def install_from_link(
        self, url: str, fetcher: Any = None, http: Any = None, lang: str | None = None
    ) -> dict[str, Any]:
        """Read the newest commit of a GitHub repository (https://github.com/<owner>/<repo>) and check it like any package. Nothing is
        installed here: the card carries a `token` that install_link takes, for LINK_PREVIEW_SECONDS, with the permissions hash the
        owner saw."""
        clean_url = (url or "").strip()
        m = GITHUB_URL_RE.match(clean_url)
        if not m:
            raise DraftError("only https://github.com/<owner>/<repo>[/tree/<ref>]", status=422)
        owner, repo, ref = m.group(1), m.group(2), m.group(3)
        if owner in (".", "..") or repo in (".", ".."):
            raise DraftError("invalid repository name", status=422)
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        if safe_owner in (".", "..") or safe_repo in (".", ".."):
            raise DraftError("invalid repository name", status=422)

        if ref:
            if not GITHUB_REF_RE.fullmatch(ref):
                raise DraftError(f"invalid git ref: {ref!r}", status=422)
            segs = ref.split("/")
            if any(s in (".", "..", "") for s in segs):
                raise DraftError("git ref cannot contain dot segments or empty parts", status=422)
            safe_ref = "/".join(quote(s, safe="") for s in segs)
        else:
            safe_ref = "HEAD"

        http_fn = http or self.http

        # Get newest commit
        commit_url = f"https://api.github.com/repos/{safe_owner}/{safe_repo}/commits/{safe_ref}"
        c_reply = await http_fn("GET", commit_url, allowed_hosts=["api.github.com"], headers=github_headers(self.store))
        if c_reply.status != 200:
            raise DraftError(f"Could not read repository {owner}/{repo}: HTTP {c_reply.status}", status=422)
        try:
            c_info = json.loads(c_reply.body.decode("utf-8"))
        except Exception:
            raise DraftError("Bad commit response from GitHub", status=422) from None
        commit_sha = c_info.get("sha", "")
        if not SHA40_RE.match(commit_sha):
            raise DraftError("Invalid commit SHA from GitHub", status=422)

        with tempfile.TemporaryDirectory(prefix="kelo-link-") as tmp:
            folder, _, digest = await self.fetch_commit(owner, repo, commit_sha, Path(tmp), fetcher, lang=lang)
            notes: list[str] = []
            try:
                pkg = load_package(folder, source="installed", check_name=False, strict=True, warnings=notes)
            except PackageError as e:
                raise DraftError(t("not a package: {why}", lang=lang, why=str(e)), status=422) from None

            token = secrets.token_urlsafe(18)
            now = self.clock()
            self._previews = {k: v for k, v in self._previews.items() if v["until"] > now}
            while len(self._previews) >= MAX_PREVIEWS:
                self._previews.pop(next(iter(self._previews)))
            perms_hash = nm.permissions_hash(pkg.permissions)
            self._previews[token] = {"until": now + LINK_PREVIEW_SECONDS, "owner": owner, "repo": repo, "commit": commit_sha,
                                     "id": pkg.id, "version": pkg.version, "content_hash": digest, "permissions_hash": perms_hash}

            ctype = pkg.connect.get("type", "") if pkg.kind == "connector" else ""
            cur = self._installed_pkg(pkg.id)
            return {
                "token": token,
                "id": pkg.id,
                "name": pkg.text("name", lang),
                "description": pkg.text("description", lang),
                "kind": pkg.kind,
                "kinds": list(pkg.kinds),
                "version": pkg.version,
                "author": pkg.author,
                "homepage": pkg.homepage,
                "permissions": {
                    "list": nm.plain(pkg.permissions),
                    "hash": perms_hash,
                    "connect": ctype,
                    "words": nm.describe(pkg.permissions, kind=pkg.kind, connect_type=ctype, lang=lang),
                },
                "source": {"type": "git", "url": f"https://github.com/{owner}/{repo}", "commit": commit_sha},
                "installed": cur.version if cur is not None else None,
                "repo": f"{owner}/{repo}",
                "contents": contents(folder, pkg, notes),
            }

    async def install_link(self, token: str, accept: str, fetcher: Any = None) -> Package:
        """Install the package a link preview showed: only if the owner accepts the permissions they saw (`accept`) and the repository
        still holds that commit with those files. The preview is used up, unless the permissions were not accepted. Raises DraftError
        (410 link_expired, 409 look_again | already_installed | changed, 422 refused)."""
        p = self._previews.pop(token, None)
        if p is None or p["until"] <= self.clock():
            raise DraftError("link_expired", 410, code="link_expired")
        if accept != p["permissions_hash"]:
            self._previews[token] = p
            raise DraftError("look_again", 409, code="look_again")
        if self._installed_pkg(p["id"]) is not None:
            raise DraftError("already_installed", 409, code="already_installed")
        owner, repo, commit = p["owner"], p["repo"], p["commit"]
        with tempfile.TemporaryDirectory(prefix="kelo-link-") as tmp:
            folder, got, digest = await self.fetch_commit(owner, repo, commit, Path(tmp), fetcher)
            if got != commit or digest != p["content_hash"]:
                raise DraftError("changed_since_preview", 409, code="changed")
            source = {"type": "git", "url": f"https://github.com/{owner}/{repo}", "commit": commit, "content_hash": digest, "link": True}
            try:
                return self.registry.install_from_dir(folder, source=source, reviewed=False, expect_id=p["id"], strict=True)
            except PackageError as e:
                if "already installed" in str(e) or "Switching where a package comes from" in str(e):
                    raise DraftError("already_installed", 409, code="already_installed") from None
                raise DraftError(str(e), 422, code="refused") from None


def draft_tools(
    manager: DraftManager,
    current_turn: Callable[[], Any] | None = None,
    emit_fn: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
) -> list[Tool]:
    """Agent tools for package drafts and sharing."""

    async def emit_change(pid: str) -> None:
        efn = emit_fn or emit_ui
        try:
            await efn({"type": "nest_changed", "id": pid})
        except Exception:
            pass

    async def handle_create(args: dict[str, Any]) -> str:
        try:
            res = manager.create(args["kind"], args["id"], args["name"], args["description"])
            await emit_change(args["id"])
            return f"Created package draft {res['id']!r} ({res['kind']}, version {res['version']})."
        except (DraftError, ValueError) as e:
            return f"Could not create draft: {e}"

    async def handle_write(args: dict[str, Any]) -> str:
        try:
            res = manager.write(args["id"], args["path"], args["content"])
            await emit_change(args["id"])
            return f"Updated {res['path']} in draft {res['id']!r} (version {res['version']})."
        except (DraftError, ValueError, FileNotFoundError) as e:
            return f"Could not write draft file: {e}"

    async def handle_check(args: dict[str, Any]) -> str:
        try:
            chk = manager.check(args["id"])
            if not chk["valid"]:
                return f"Draft {args['id']!r} has validation errors: {chk.get('error')}"
            perms_text = "\n".join(f"  - {p['text']}" for p in chk.get("permissions", []))
            return (
                f"Draft {chk['id']!r} ({chk['kind']}, version {chk['version']}) is valid.\n"
                f"Content hash: {chk['content_hash']}\n"
                f"Permissions:\n{perms_text}"
            )
        except (DraftError, FileNotFoundError) as e:
            return f"Could not check draft: {e}"

    async def handle_delete(args: dict[str, Any]) -> str:
        try:
            ok = manager.delete(args["id"])
            if ok:
                await emit_change(args["id"])
                return f"Deleted package draft {args['id']!r}."
            return f"No draft {args['id']!r}."
        except Exception as e:
            return f"Could not delete draft: {e}"

    async def handle_from_skill(args: dict[str, Any]) -> str:
        try:
            pkg = manager.copy_custom_skill(args["skill_id"], args.get("draft_id"))
            await emit_change(pkg.id)
            return f"Copied custom skill {args['skill_id']!r} into draft {pkg.id!r} (version {pkg.version})."
        except (DraftError, ValueError) as e:
            return f"Could not make draft from skill: {e}"

    async def describe_share(args: dict[str, Any]) -> str:
        pid = args.get("id", "")
        repo = args.get("repo", "")
        public = args.get("public", True)
        account = manager.get_github_account()
        files = manager.draft_files(pid)
        vis = "public" if public else "private"
        return f"Share draft '{pid}' to GitHub: account '{account}', repository '{repo}' ({vis}), files: {', '.join(files)}"

    def share_approval_extra(approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        pid = args.get("id", "")
        repo = args.get("repo", "")
        public = args.get("public", True)
        account = manager.get_github_account()
        files = manager.draft_files(pid)
        return {
            "account": account,
            "repo": repo,
            "public": public,
            "files": files,
            "lines": [
                {"label": "Account", "value": account},
                {"label": "Repository", "value": repo},
                {"label": "Visibility", "value": "public" if public else "private"},
                {"label": "Files", "value": ", ".join(files)},
            ],
        }

    async def handle_share(args: dict[str, Any]) -> str:
        pid = args.get("id", "")
        repo = args.get("repo", "")
        public = args.get("public", True)
        try:
            res = await manager.share_to_github(pid, repo, public)
            return (
                f"Package draft {pid!r} shared to GitHub: {res['url']} (commit {res['commit']}).\n"
                "Now give the owner the repository link and say anyone can install it by pasting that link in "
                "My -> Nest -> Install from link in their Kelo app."
            )
        except DraftError as e:
            return f"Could not share draft to GitHub: {e}"

    return [
        Tool(
            "nest_draft_create",
            "Create a new Kelo package draft folder (skill, connector or extension) on this server.",
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["skill", "connector", "extension"], "description": "Kind of package"},
                    "id": {"type": "string", "description": "Package id: lowercase letters, digits and '-' (or '_' for connectors)"},
                    "name": {"type": "string", "description": "Human-readable name of the package"},
                    "description": {"type": "string", "description": "Short description of what the package does"},
                },
                "required": ["kind", "id", "name", "description"],
            },
            handle_create,
        ),
        Tool(
            "nest_draft_write",
            "Write or update a file in a package draft folder. Each write bumps the version (0.0.N) and sends a live reload event.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Package draft id"},
                    "path": {"type": "string", "description": "Relative file path: kelo-package.json, SKILL.md, ui/... or assets/..."},
                    "content": {"type": "string", "description": "Full file content (text)"},
                },
                "required": ["id", "path", "content"],
            },
            handle_write,
        ),
        Tool(
            "nest_draft_check",
            "Validate a package draft: returns validator findings, errors, warnings, and what the owner would be shown.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Package draft id"},
                },
                "required": ["id"],
            },
            handle_check,
            read_only=True,
        ),
        Tool(
            "nest_draft_delete",
            "Delete a package draft folder from this server.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Package draft id"},
                },
                "required": ["id"],
            },
            handle_delete,
        ),
        Tool(
            "nest_draft_from_skill",
            "Copy an approved custom skill into a new package draft ('share this skill').",
            {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "description": "Custom skill id to copy"},
                    "draft_id": {"type": "string", "description": "Optional id for the new draft (defaults to skill_id)"},
                },
                "required": ["skill_id"],
            },
            handle_from_skill,
        ),
        Tool(
            "nest_share_github",
            "Share a package draft to the owner's GitHub repository using the Git Data API. Always asks for approval.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Package draft id"},
                    "repo": {"type": "string", "description": "GitHub repository name (e.g. kelo-my-pack)"},
                    "public": {"type": "boolean", "default": True, "description": "Whether the repository should be public"},
                },
                "required": ["id", "repo"],
            },
            handle_share,
            requires_approval=True,
            describe=describe_share,
            approval_extra=share_approval_extra,
        ),
    ]


__all__ = [
    "ALLOWED_EXACT",
    "ALLOWED_PREFIXES",
    "DraftError",
    "DraftManager",
    "LINK_PREVIEW_SECONDS",
    "MAX_DRAFT_FILE",
    "MAX_DRAFT_TOTAL",
    "MAX_PREVIEWS",
    "NEST_SHARE_MARK",
    "bump_draft_version",
    "check_draft_path",
    "draft_tools",
    "github_headers",
    "github_repo",
    "github_token",
]
