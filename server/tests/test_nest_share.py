"""Tests for sharing Nest package drafts to GitHub (Git Data API) and installing from a GitHub link (preview, then install)."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from assistant.nest import Nest, NestError
from assistant.nest_drafts import DraftError, DraftManager, draft_tools
from assistant.netguard import Reply
from assistant.oauth_connect import DeviceFlows
from assistant.packages import Registry
from assistant.privacy_guard import PrivacyGuard
from assistant.server import Settings, create_app
from assistant.store import Store
from test_nest import github_archive

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "server" / "examples" / "packages"
TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode(), False)


class FakeGitHub:
    def __init__(self, owner: str = "octocat"):
        self.owner = owner
        self.calls: list[tuple[str, str, dict]] = []
        self.commit_sha = "c" * 40
        self.blob_sha = "b" * 40
        self.tree_sha = "t" * 40
        self.archive_bytes: bytes = b""

    async def http(self, method: str, url: str, *, allowed_hosts=None, headers=None, content=None):
        payload = {}
        if content:
            try:
                payload = json.loads(content.decode("utf-8"))
            except Exception:
                payload = {"_raw": content}
        parsed = urlsplit(url)
        path = parsed.path
        self.calls.append((method, path, payload))

        # 0. User
        if method == "GET" and path == "/user":
            return j({"login": self.owner}, 200)
        # 1. Check repo
        if method == "GET" and path.startswith("/repos/") and not "/git/" in path and not "/commits" in path:
            return j({"message": "Not Found"}, 404)
        # 2. Create repo
        if method == "POST" and path == "/user/repos":
            return j({"full_name": f"{self.owner}/{payload.get('name')}", "name": payload.get("name")}, 201)
        # 3. Create blob
        if method == "POST" and "/git/blobs" in path:
            return j({"sha": self.blob_sha}, 201)
        # 4. Check ref
        if method == "GET" and "/git/ref" in path:
            return j({"message": "Not Found"}, 404)
        # 5. Create tree
        if method == "POST" and "/git/trees" in path:
            return j({"sha": self.tree_sha}, 201)
        # 6. Create commit
        if method == "POST" and "/git/commits" in path:
            return j({"sha": self.commit_sha}, 201)
        # 7. Create ref
        if method == "POST" and "/git/refs" in path:
            return j({"ref": payload.get("ref"), "object": {"sha": self.commit_sha}}, 201)
        # 8. Get commit for install-from-link
        if method == "GET" and "/commits/" in path:
            return j({"sha": self.commit_sha}, 200)

        return j({"message": "Not found"}, 404)

    async def fetch(self, url: str, limit: int = 0):
        self.calls.append(("FETCH", url, {}))
        return self.archive_bytes


def setup_github_write(store: Store, owner: str = "octocat", scope: str = "public"):
    store.save_credential("github-api", f"GitHub · @{owner}", ["api.github.com"], {"type": "header", "name": "Authorization", "prefix": "Bearer "})
    store.set_credential_secret("github-api", "ghp_test_token_12345")
    store.set_meta("oauth_scope.github", scope)


def test_share_without_github_write_returns_409(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    mgr.create("skill", "share-409", "Share 409", "Test without GitHub")

    # Has no credential yet
    assert mgr.has_github_write() is False
    with pytest.raises(DraftError) as exc_info:
        import asyncio
        asyncio.run(mgr.share_to_github("share-409", "kelo-share-409"))
    assert exc_info.value.status == 409
    assert exc_info.value.code == "github_write_needed"

    # Now test the HTTP API endpoint
    app = create_app(
        Settings(
            token=TOKEN,
            provider="p",
            model="m",
            cdp_host=None,
            db_path=str(state_dir / "assistant.db"),
            home_dir=str(state_dir / "home"),
            package_drafts_dir=str(drafts_dir),
        ),
        tools=[],
        stream_fn=lambda *a, **k: None,
    )
    c = TestClient(app)
    r = c.post("/api/nest/drafts/share-409/share", headers=AUTH)
    assert r.status_code == 409
    assert r.json().get("code") == "github_write_needed"


def test_share_approval_card_contents(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    setup_github_write(store, owner="octocat", scope="public")

    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    mgr.create("skill", "card-test", "Card Test", "Testing approval card")
    mgr.write("card-test", "SKILL.md", "---\nname: Card Test\n---\nRun instructions.")

    tools = {t.name: t for t in draft_tools(mgr)}
    share_tool = tools["nest_share_github"]
    assert share_tool.requires_approval is True

    extra = share_tool.approval_extra("appr_123", {"id": "card-test", "repo": "kelo-card-test", "public": True})
    assert extra["account"] == "octocat"
    assert extra["repo"] == "kelo-card-test"
    assert extra["public"] is True
    assert "kelo-package.json" in extra["files"]
    assert "SKILL.md" in extra["files"]
    assert any(line["label"] == "Account" and line["value"] == "octocat" for line in extra["lines"])
    assert any(line["label"] == "Repository" and line["value"] == "kelo-card-test" for line in extra["lines"])


def test_secret_in_draft_file_stops_before_http_calls(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    setup_github_write(store, owner="octocat", scope="public")

    reg = Registry(drafts=drafts_dir)
    fake_gh = FakeGitHub()
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store, http=fake_gh.http)

    mgr.create("skill", "secret-test", "Secret Test", "Testing secret scanner")
    # Put an OpenAI secret key in SKILL.md
    mgr.write("secret-test", "SKILL.md", "My key is sk-proj-1234567890abcdefghijklmnopqrstuvwxyz1234567890")

    with pytest.raises(DraftError) as exc_info:
        import asyncio
        asyncio.run(mgr.share_to_github("secret-test", "kelo-secret-test", public=True))

    assert exc_info.value.status == 422
    assert exc_info.value.code == "secret_found"
    # Stopped before any HTTP call!
    assert len(fake_gh.calls) == 0


def test_privacy_guard_stops_before_http_calls(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    setup_github_write(store, owner="octocat", scope="public")

    # Set up privacy guard with a personal address
    class MockPrivacyGuard:
        def scan(self, text: str) -> list[str]:
            if "123 Private Street" in text:
                return ["address"]
            return []

    reg = Registry(drafts=drafts_dir)
    fake_gh = FakeGitHub()
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store, privacy_guard=MockPrivacyGuard(), http=fake_gh.http)

    mgr.create("skill", "privacy-test", "Privacy Test", "Testing privacy guard")
    mgr.write("privacy-test", "SKILL.md", "Contact me at 123 Private Street, Seoul.")

    with pytest.raises(DraftError) as exc_info:
        import asyncio
        asyncio.run(mgr.share_to_github("privacy-test", "kelo-privacy-test", public=True))

    assert exc_info.value.status == 422
    assert exc_info.value.code == "privacy_data_found"
    # Stopped before any HTTP call!
    assert len(fake_gh.calls) == 0


def test_git_data_api_call_order_and_returned_commit(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    setup_github_write(store, owner="octocat", scope="public")

    reg = Registry(drafts=drafts_dir)
    fake_gh = FakeGitHub(owner="octocat")
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store, http=fake_gh.http)

    mgr.create("skill", "good-pack", "Good Pack", "A fine package to share")
    mgr.write("good-pack", "SKILL.md", "---\nname: Good Pack\ndescription: A fine package\n---\nRun nicely.")

    import asyncio
    res = asyncio.run(mgr.share_to_github("good-pack", "kelo-good-pack", public=True))

    assert res["repo"] == "octocat/kelo-good-pack"
    assert res["url"] == "https://github.com/octocat/kelo-good-pack"
    assert res["commit"] == fake_gh.commit_sha
    assert len(res["commit"]) == 40

    # Verify call order:
    methods_and_paths = [(m, p) for m, p, _ in fake_gh.calls]
    # 0. GET /user (resolve owner login)
    assert methods_and_paths[0] == ("GET", "/user")
    # 1. GET /repos/octocat/kelo-good-pack (check)
    assert methods_and_paths[1] == ("GET", "/repos/octocat/kelo-good-pack")
    # 2. POST /user/repos (create)
    assert methods_and_paths[2] == ("POST", "/user/repos")
    # 3. Blobs: POST /repos/octocat/kelo-good-pack/git/blobs
    blobs = [x for x in methods_and_paths if x[1].endswith("/git/blobs")]
    assert len(blobs) >= 3  # kelo-package.json, SKILL.md, README.md, LICENSE
    # 4. Check ref
    refs_check = [x for x in methods_and_paths if x[0] == "GET" and "/git/ref" in x[1]]
    assert len(refs_check) >= 1
    # 5. Tree: POST /git/trees
    trees = [x for x in methods_and_paths if x[1].endswith("/git/trees")]
    assert len(trees) == 1
    # 6. Commit: POST /git/commits
    commits = [x for x in methods_and_paths if x[1].endswith("/git/commits")]
    assert len(commits) == 1
    # 7. Update/create ref: POST /git/refs
    ref_updates = [x for x in methods_and_paths if "/git/refs" in x[1] and x[0] in ("POST", "PATCH")]
    assert len(ref_updates) == 1

    # Check .shared.json recorded
    shared_file = drafts_dir / "good-pack" / ".shared.json"
    assert shared_file.is_file()
    shared = json.loads(shared_file.read_text(encoding="utf-8"))
    assert shared["repo"] == "octocat/kelo-good-pack"
    assert shared["commit"] == fake_gh.commit_sha

    # the share tool's answer tells the owner how others install it: by the repository link, never a pull request
    tools = {t.name: t for t in draft_tools(mgr)}
    share_tool = tools["nest_share_github"]
    share_res = asyncio.run(share_tool.handler({"id": "good-pack", "repo": "kelo-good-pack", "public": True}))
    assert "Install from link" in share_res and "My -> Nest" in share_res
    assert "pull request" not in share_res.lower() and "Kelo Nest" not in share_res


class Clock:
    """A clock the tests move by hand (previews expire after LINK_PREVIEW_SECONDS)."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def link_world(tmp_path: Path, clock: Clock | None = None):
    """A server's registry and Nest, with GitHub (FakeGitHub) serving the habit-tracker archive at the commit it names."""
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    setup_github_write(store, owner="octocat", scope="public")
    reg = Registry(installed=state_dir / "packages", drafts=drafts_dir)
    fake_gh = FakeGitHub(owner="octocat")
    fake_gh.archive_bytes = github_archive(EXAMPLES / "habit-tracker", fake_gh.commit_sha)
    nest = Nest(reg, store, data_dir=state_dir / "nest-data", drafts_dir=drafts_dir, fetcher=fake_gh.fetch, http=fake_gh.http,
                clock=clock or time.time)
    return nest, reg, fake_gh


LINK = "https://github.com/octocat/habit-tracker"


def test_link_preview_shows_the_package_and_installs_nothing_until_confirmed(tmp_path: Path):
    nest, reg, fake_gh = link_world(tmp_path)
    card = asyncio.run(nest.install_from_link(LINK, lang="en"))
    assert card["id"] == "habit-tracker" and card["repo"] == "octocat/habit-tracker" and card["installed"] is None
    assert card["source"] == {"type": "git", "url": LINK, "commit": fake_gh.commit_sha}
    assert not {"pinned", "review", "reviewed", "from_nest", "note"} & set(card)
    assert reg.get("habit-tracker") is None                      # the preview installs nothing

    with pytest.raises(NestError) as e:
        asyncio.run(nest.install_link(card["token"], "0" * 64))
    assert e.value.status == 409 and e.value.extra["error"] == "look_again"

    row = asyncio.run(nest.install_link(card["token"], card["permissions"]["hash"]))
    rec = reg.source_record("habit-tracker")
    assert rec["source"] == {"type": "git", "url": LINK, "commit": fake_gh.commit_sha, "content_hash": rec["approved_hash"], "link": True}
    assert rec["reviewed"] is False and reg.get("habit-tracker").installed
    assert row["state"] == "on" and row["repo"] == "octocat/habit-tracker" and "review" not in row and "from_nest" not in row
    assert row["permissions"] == card["permissions"]["list"]

    again = asyncio.run(nest.install_from_link(LINK))
    with pytest.raises(NestError) as e:
        asyncio.run(nest.install_link(again["token"], again["permissions"]["hash"]))
    assert e.value.status == 409 and e.value.extra["error"] == "already_installed"


def test_a_wrong_accept_keeps_the_preview_for_the_right_one(tmp_path: Path):
    nest, reg, fake_gh = link_world(tmp_path)
    card = asyncio.run(nest.install_from_link(LINK))
    with pytest.raises(NestError):
        asyncio.run(nest.install_link(card["token"], "f" * 64))
    got = asyncio.run(nest.install_link(card["token"], card["permissions"]["hash"]))
    assert got["id"] == "habit-tracker" and reg.get("habit-tracker").installed


def test_an_expired_preview_is_gone_after_fifteen_minutes(tmp_path: Path):
    clock = Clock()
    nest, reg, _ = link_world(tmp_path, clock)
    card = asyncio.run(nest.install_from_link(LINK))
    clock.now += 15 * 60 + 1
    with pytest.raises(NestError) as e:
        asyncio.run(nest.install_link(card["token"], card["permissions"]["hash"]))
    assert e.value.status == 410 and e.value.extra["error"] == "link_expired"
    assert reg.get("habit-tracker") is None


def test_a_repository_that_changed_after_the_preview_installs_nothing(tmp_path: Path):
    nest, reg, fake_gh = link_world(tmp_path)
    card = asyncio.run(nest.install_from_link(LINK))
    changed = tmp_path / "changed" / "habit-tracker"
    shutil.copytree(EXAMPLES / "habit-tracker", changed)
    (changed / "SKILL.md").write_text((changed / "SKILL.md").read_text() + "\nSomething new.\n", encoding="utf-8")
    fake_gh.archive_bytes = github_archive(changed, fake_gh.commit_sha)     # the same commit name, other files
    with pytest.raises(NestError) as e:
        asyncio.run(nest.install_link(card["token"], card["permissions"]["hash"]))
    assert e.value.status == 409 and e.value.extra["error"] == "changed"
    assert reg.get("habit-tracker") is None


def test_link_routes_on_a_real_app(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    store = Store(str(state_dir / "assistant.db"))
    setup_github_write(store, owner="octocat", scope="public")
    app = create_app(
        Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(state_dir / "assistant.db"),
                 home_dir=str(state_dir / "home"), package_drafts_dir=str(drafts_dir)),
        tools=[], stream_fn=lambda *a, **k: None,
    )
    fake_gh = FakeGitHub(owner="octocat")
    fake_gh.archive_bytes = github_archive(EXAMPLES / "habit-tracker", fake_gh.commit_sha)
    app.state.nest.http = fake_gh.http
    app.state.nest.draft_manager.http = fake_gh.http
    app.state.nest.draft_manager.fetcher = fake_gh.fetch
    c = TestClient(app)

    assert c.post("/api/nest/install-from-link", json={"url": "https://notgithub.com/a/b"}, headers=AUTH).status_code == 422
    card = c.post("/api/nest/install-from-link", json={"url": LINK}, headers=AUTH)
    assert card.status_code == 200 and "token" in card.json() and "review" not in card.json()
    token, perms = card.json()["token"], card.json()["permissions"]["hash"]
    r = c.post("/api/nest/install-from-link/confirm", json={"token": token, "accept": "0" * 64}, headers=AUTH)
    assert r.status_code == 409 and r.json()["error"] == "look_again" and r.json()["code"] == "look_again"   # the apps read "code"
    r = c.post("/api/nest/install-from-link/confirm", json={"token": token, "accept": perms}, headers=AUTH)
    assert r.status_code == 200 and r.json()["id"] == "habit-tracker" and r.json()["repo"] == "octocat/habit-tracker"
    installed = c.get("/api/nest/installed", headers=AUTH).json()["items"]
    assert [i["id"] for i in installed] == ["habit-tracker"] and "review" not in installed[0]
    assert c.get("/api/nest/packages/habit-tracker", headers=AUTH).json()["update"] is None
    r = c.post("/api/nest/install-from-link/confirm", json={"token": token, "accept": perms}, headers=AUTH)
    assert r.status_code == 410 and r.json()["error"] == "link_expired"


def test_github_link_refuses_dot_segments_and_invalid_chars(tmp_path: Path):
    nest, reg, fake_gh = link_world(tmp_path)
    for bad_url in [
        "https://github.com/octocat/habit-tracker/tree/../../../orgs/x/members",
        "https://github.com/octocat/habit-tracker/tree/main/../other",
        "https://github.com/octocat/habit-tracker/tree/./main",
        "https://github.com/octocat/habit-tracker/tree/main/.",
        "https://github.com/octocat/habit-tracker/tree//main",
        "https://github.com/octocat/habit-tracker/tree/main/",
        "https://github.com/octocat/habit-tracker/tree/branch with space",
        "https://github.com/./habit-tracker",
        "https://github.com/../habit-tracker",
    ]:
        with pytest.raises(NestError) as e:
            asyncio.run(nest.install_from_link(bad_url))
        assert e.value.status == 422


def test_concurrent_install_link_serialized(tmp_path: Path):
    import concurrent.futures
    nest, reg, fake_gh = link_world(tmp_path)
    card1 = asyncio.run(nest.install_from_link(LINK))
    card2 = asyncio.run(nest.install_from_link(LINK))
    assert card1["token"] != card2["token"]

    results = []
    errors = []

    def confirm(card):
        try:
            r = asyncio.run(nest.install_link(card["token"], card["permissions"]["hash"]))
            results.append(r)
        except NestError as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(confirm, card1)
        f2 = pool.submit(confirm, card2)
        f1.result()
        f2.result()

    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].status == 409 and errors[0].extra["error"] == "already_installed"
