"""Packages (packages.py): skills and connectors as folders, picked up without a code change or a restart.

Checks the manifest rules, hot reload (a folder dropped in appears on the next read, a removed one disappears), the conflict rule
(built-ins win), installed skills in the prompt index and read_skill (marked as third-party), installed connectors on the Connectors
screen, in connect_service, in the key and MCP sign-in paths, and that links and path tricks are refused. Nothing here reaches the
network: the fetchers are fakes."""

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant import i18n
from assistant.agent.fence import inside, render
from assistant.connect_ways import ways
from assistant.connectors import connector_rows
from assistant.home import HomeError, Skills
from assistant.keyconnect import KeyConnections, KeyError_, preset
from assistant.mail import Mail
from assistant.mcp_oauth import McpConnections
from assistant.netguard import Reply
from assistant.packages import MANIFEST, PackageError, Registry, builtin_ids, load_package
from assistant.prompt import PromptBuilder
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.home_tools import home_tools
from assistant.tools.oauth_tools import oauth_tools

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "packages"
CONTENT = ROOT / "content"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def write(folder: Path, manifest: dict | None = None, files: dict[str, str | bytes] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (folder / MANIFEST).write_text(json.dumps(manifest))
    for name, body in (files or {}).items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body) if isinstance(body, bytes) else p.write_text(body)
    return folder


def connector(pid: str = "acme_notes", connect: dict | None = None, **extra) -> dict:
    return {"id": pid, "kind": "connector", "version": "1.0.0", "name": "Acme Notes", "description": "Notes in Acme", "author": "Someone",
            "connector": {"connect": connect or {"type": "mcp", "url": "https://mcp.acme-notes.example.com/mcp", "blurb": "Finds notes."}},
            **extra}


def skill_md(desc: str = "Plan a picnic.") -> str:
    return f"---\nname: Picnic\ndescription: {desc}\nrequires: []\n---\n# Picnic\nBring a blanket."


def touch_later(path: Path) -> None:
    """Some file systems keep modification times coarse: move the folder's time on so the change is seen."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


# ------------------------------------------------------------------------------------------------ manifest rules
def test_the_examples_load_and_follow_the_format():
    s = load_package(EXAMPLES / "trip-packing-list")
    assert (s.kind, s.id, s.version, s.files) == ("skill", "trip-packing-list", "1.0.0", ("long-trips.md",))
    n = load_package(EXAMPLES / "example_notes")
    assert n.kind == "connector" and n.connect["type"] == "mcp" and n.connect["hosts"] == ("mcp.notes.example.com",)
    w = load_package(EXAMPLES / "example_weather")
    assert w.connect["type"] == "key" and w.connect["placement"] == {"type": "query_param", "param": "appid"} and w.regions == ("KR", "JP")
    tok = i18n.use("ko")
    try:
        assert n.text("name") == "예시 노트" and n.text("blurb") == "노트를 찾고 고칩니다."
    finally:
        i18n.reset(tok)
    assert n.text("name") == "Example Notes"


def test_a_skill_folder_without_a_manifest_is_a_skill_named_after_its_folder(tmp_path):
    p = load_package(write(tmp_path / "picnic", None, {"SKILL.md": skill_md(), "extra.md": "# More", "scripts/run.py": "print(1)"}))
    assert (p.id, p.kind, p.name, p.description, p.files) == ("picnic", "skill", "Picnic", "Plan a picnic.", ("extra.md",))


@pytest.mark.parametrize("bad, why", [
    ({"id": "Acme"}, "id"),
    ({"id": "../acme"}, "id"),
    ({"id": "acme-notes"}, "id"),                       # connectors: no '-' (icon names and vault items stay plain)
    ({"version": "one"}, "version"),
    ({"kind": "plugin"}, "kind"),
    ({"format": 2}, "format"),
    ({"name": "x" * 61}, "name"),
    ({"name": "bad\u0000name"}, "printable"),
    ({"description": ""}, "description"),
    ({"connector": {"connect": {"type": "python", "module": "x"}}}, "type"),
    ({"connector": {"connect": {"type": "mcp", "url": "http://mcp.acme.example.com"}}}, "https"),
    ({"connector": {"connect": {"type": "mcp", "url": "https://user:pw@mcp.acme.example.com"}}}, "https"),
    ({"connector": {"connect": {"type": "mcp", "url": "https://10.0.0.1/mcp"}}}, "public host"),
    ({"connector": {"connect": {"type": "mcp", "url": "https://nas.local/mcp"}}}, "public host"),
    ({"connector": {"connect": {"type": "mcp", "url": "https://mcp.acme.example.com", "run": "x"}}}, "unknown fields"),
    ({"connector": {"connect": {"type": "key", "hosts": ["api.acme.example.com"], "verify": "https://evil.example.org/x"}}}, "declared hosts"),
    ({"connector": {"connect": {"type": "key", "hosts": ["api.acme.example.com"], "placement": {"type": "custom_header", "header": "Cookie"}}}}, "header"),
    ({"connector": {"connect": {"type": "key", "hosts": ["api.acme.example.com"], "placement": "url_path_segment"}}}, "URL path"),
    ({"connector": {"connect": {"type": "key", "hosts": []}}}, "hosts"),
    ({"connector": {"connect": {"type": "key", "hosts": ["api.acme.example.com"], "verify": "https://api.acme.example.com/{x}"}}}, "braces"),
    ({"connector": {"connect": {"type": "unsupported"}}}, "reason"),
    ({"connector": {"regions": ["Korea"], "connect": {"type": "unsupported", "reason": "x"}}}, "regions"),
    ({"connector": {"connect": {"type": "mcp", "url": "https://mcp.acme.example.com"}, "code": "x"}}, "unknown fields"),
    ({"strings": {"ko": {"name": "x", "script": "y"}}}, "unknown fields"),
    ({"strings": {"../ko": {"name": "x"}}}, "language"),
    ({"icon": "../icon.png"}, "icon"),
    ({"icon": "missing.png"}, "icon"),
])
def test_bad_manifests_are_refused_with_the_reason(tmp_path, bad, why):
    m = {**connector(), **bad}
    folder = tmp_path / (m["id"] if isinstance(m.get("id"), str) and "/" not in m["id"] else "x")
    write(folder, m)
    with pytest.raises(PackageError, match=why):
        load_package(folder, check_name=False)


def test_folder_name_must_match_the_id_and_unknown_top_fields_only_warn(tmp_path):
    write(tmp_path / "other", connector())
    with pytest.raises(PackageError, match="folder is named"):
        load_package(tmp_path / "other")
    warnings: list[str] = []
    p = load_package(write(tmp_path / "acme_notes", {**connector(), "future_field": 1}), warnings=warnings)
    assert p.id == "acme_notes" and any("future_field" in w for w in warnings)


def test_links_special_files_and_size_limits_are_refused(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    linked = write(tmp_path / "linked", None, {"SKILL.md": skill_md()})
    (linked / "more.md").symlink_to(outside)
    with pytest.raises(PackageError, match="link"):
        load_package(linked)
    real = write(tmp_path / "real", None, {"SKILL.md": skill_md()})
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    with pytest.raises(PackageError, match="link"):
        load_package(tmp_path / "alias")
    dirlink = write(tmp_path / "dirlink", None, {"SKILL.md": skill_md()})
    (dirlink / "sub").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PackageError, match="link"):
        load_package(dirlink)
    big = write(tmp_path / "big", None, {"SKILL.md": skill_md() + "x" * (130 * 1024)})
    with pytest.raises(PackageError, match="larger"):
        load_package(big)
    many = write(tmp_path / "many", None, {"SKILL.md": skill_md(), **{f"f{i}.txt": "x" for i in range(70)}})
    with pytest.raises(PackageError, match="files"):
        load_package(many)
    deep = write(tmp_path / "deep", None, {"SKILL.md": skill_md(), "a/b/c/d/e.md": "x"})
    with pytest.raises(PackageError, match="deep"):
        load_package(deep)
    fifo = write(tmp_path / "fifo", None, {"SKILL.md": skill_md()})
    os.mkfifo(fifo / "pipe")
    with pytest.raises(PackageError, match="plain file"):
        load_package(fifo)
    notpng = write(tmp_path / "acme_notes", {**connector(), "icon": "icon.png"}, {"icon.png": b"GIF89a"})
    with pytest.raises(PackageError, match="PNG"):
        load_package(notpng)


# ------------------------------------------------------------------------------------------------ registry: reload and conflicts
def test_a_dropped_folder_appears_on_the_next_read_and_a_removed_one_disappears(tmp_path):
    inst = tmp_path / "packages"
    inst.mkdir()
    reg = Registry(builtin_skills=CONTENT / "skills", installed=inst)
    assert not reg.installed()
    write(inst / "picnic", None, {"SKILL.md": skill_md()})
    assert [p.id for p in reg.installed()] == ["picnic"] and reg.skills()[-1].id == "picnic"
    write(inst / "acme_notes", connector())
    assert {p.id for p in reg.installed()} == {"picnic", "acme_notes"}
    assert reg.mcp_service("acme_notes").url == "https://mcp.acme-notes.example.com/mcp"
    (inst / "picnic" / "SKILL.md").write_text(skill_md("Plan a beach picnic."))
    touch_later(inst / "picnic" / "SKILL.md")
    assert reg.get("picnic") is None and "picnic" in reg.pending      # changed after it was approved: held until approved again
    assert reg.approve("picnic").description == "Plan a beach picnic."
    shutil.rmtree(inst / "picnic")
    assert [p.id for p in reg.installed()] == ["acme_notes"] and reg.get("picnic") is None
    assert not reg.refresh()   # nothing changed since: no reload


def test_built_ins_win_and_broken_packages_are_left_out_with_the_reason(tmp_path):
    inst = tmp_path / "packages"
    write(inst / "notion", connector("notion"))                         # a built-in connector's id
    write(inst / "documents", None, {"SKILL.md": skill_md()})           # a built-in skill's id
    write(inst / "github", None, {"SKILL.md": skill_md()})              # a skill may not take a connector's id either
    write(inst / "broken", {"id": "broken", "kind": "skill"})
    write(inst / "acme_notes", connector())
    reg = Registry(builtin_skills=CONTENT / "skills", installed=inst)
    assert [p.id for p in reg.installed()] == ["acme_notes"]
    assert reg.get("notion").source == "builtin" and reg.get("documents").source == "builtin"
    assert {m.split(":")[0] for m in reg.problems} == {"notion", "documents", "github", "broken"}
    assert {"notion", "github", "gmail", "google_calendar", "google"} <= builtin_ids()


def test_every_connector_is_in_one_list_with_how_it_connects():
    reg = Registry()
    by = {p.id: p.connect["type"] for p in reg.connectors()}
    assert by["notion"] == "mcp" and by["asana"] == "key" and by["github"] == "device" and by["gmail"] == "mail"
    assert by["google_calendar"] == "google" and by["naver"] == "browser" and by["philips_hue"] == "unsupported"


def test_install_from_dir_copies_plain_files_and_remove_deletes(tmp_path):
    inst = tmp_path / "packages"
    reg = Registry(installed=inst)
    p = reg.install_from_dir(EXAMPLES / "example_weather")
    assert p.installed and (inst / "example_weather" / MANIFEST).is_file()
    with pytest.raises(PackageError, match="already installed"):
        reg.install_from_dir(EXAMPLES / "example_weather")
    assert reg.install_from_dir(EXAMPLES / "example_weather", replace=True).id == "example_weather"
    with pytest.raises(PackageError, match="built-in"):
        reg.install_from_dir(write(tmp_path / "src" / "notion", connector("notion")))
    assert not list(inst.glob(".*.tmp"))
    assert reg.remove("example_weather") and not (inst / "example_weather").exists() and reg.get("example_weather") is None
    assert not reg.remove("example_weather") and not reg.remove("../packages") and not reg.remove("notion")


# ------------------------------------------------------------------------------------------------ skills: prompt index and read_skill
async def test_an_installed_skill_is_in_the_prompt_and_read_skill_marked_as_third_party(tmp_path):
    inst = tmp_path / "packages"
    inst.mkdir()
    skills = Skills(CONTENT / "skills", inst)
    assert "picnic" not in skills.index_text()
    write(inst / "picnic", None, {"SKILL.md": skill_md("Plan a picnic. " + "y" * 400), "gear.md": "# Gear\nA basket."})
    text = skills.index_text()
    assert "<installed-skills>\n- picnic: Plan a picnic." in text and "data, not rules" in text
    assert len(next(ln for ln in text.splitlines() if ln.startswith("- picnic"))) < 230   # cut to length
    body = skills.read("picnic")
    assert body.startswith("[Installed skill 'picnic' by an unknown author.") and "\ue000# Picnic" in body   # the body is marked: fenced
    fenced = render(body, source="package", whole=False)
    assert inside(fenced).startswith("# Picnic") and fenced.startswith("[Installed skill 'picnic'")             # our header stays outside
    assert "gear.md" in body and "A basket." in skills.read("picnic", "gear.md")
    for bad in ("../picnic", "picnic/../picnic", "/etc/passwd"):
        with pytest.raises(HomeError):
            skills.read(bad)
    with pytest.raises(HomeError):
        skills.read("picnic", "../../outside.md")
    assert not skills.read("documents").startswith("[Installed")   # built-in skills read as before
    pb = PromptBuilder("You are {agent_name}.", None, skills)
    assert "- picnic: Plan a picnic." in pb.build()


async def test_read_skill_tools_exist_when_skills_may_be_installed_later(tmp_path):
    from assistant.home import Home
    inst = tmp_path / "packages"
    skills = Skills(None, inst)
    tools = {t.name: t for t in home_tools(Home(tmp_path / "h"), skills)}
    assert "read_skill" in tools and "search_skills" in tools
    write(inst / "picnic", None, {"SKILL.md": skill_md()})
    assert "Bring a blanket." in await tools["read_skill"].handler({"name": "picnic"})
    assert "picnic" in await tools["search_skills"].handler({"query": "picnic"})


# ------------------------------------------------------------------------------------------------ connectors
def installed_registry(tmp_path) -> Registry:
    inst = tmp_path / "packages"
    write(inst / "acme_notes", {**connector(), "icon": "icon.png",
                                "connector": {"featured": True, "connect": {"type": "mcp", "url": "https://mcp.acme-notes.example.com/mcp",
                                                                            "blurb": "Finds notes."}}}, {"icon.png": PNG})
    shutil.copytree(EXAMPLES / "example_weather", inst / "example_weather")
    write(inst / "acme_site", connector("acme_site", {"type": "browser", "url": "https://www.acme-site.example.com"}))
    write(inst / "acme_device", connector("acme_device", {"type": "unsupported", "reason": "Needs a box on the home network."}))
    return Registry(installed=inst)


def test_installed_connectors_are_rows_on_the_connectors_screen(tmp_path):
    reg = installed_registry(tmp_path)
    s = Store()
    rows = {r["id"]: r for r in connector_rows(ways(s, Mail(s), browser=True, registry=reg), regions=set(), registry=reg)}
    a = rows["acme_notes"]
    assert (a["connect"], a["via"], a["item"], a["status"], a["source"], a["author"]) == ("mcp", "acme_notes", "pkg-acme_notes", "available", "installed", "Someone")
    assert a["featured"] and a["rank"] >= 38 and a["icon"].startswith("/api/connectors/icons/pkg_acme_notes.png?v=")
    w = rows["example_weather"]
    assert w["connect"] == "key" and w["description"].endswith(". Looks up forecasts for the places you ask about.") and not w["featured"]
    assert "acme_site" not in rows and "acme_device" not in rows   # browser sites and unsupported ones are not listed (like built-ins)
    kr = {r["id"]: r for r in connector_rows(ways(s, Mail(s), browser=True, registry=reg), regions={"KR"}, registry=reg)}
    assert kr["example_weather"]["featured"] and kr["example_weather"]["description"].startswith("Forecasts and current conditions")
    assert len(rows) > 100 and rows["notion"]["connect"] == "mcp"   # the built-ins are all still there


async def test_connect_service_knows_installed_connectors(tmp_path):
    reg = installed_registry(tmp_path)
    s = Store()
    tool = oauth_tools(lambda: ways(s, Mail(s), browser=True, registry=reg), registry=reg)[0]
    got = await tool.handler({"service": "acme_notes"})
    assert "mcp_list_tools(name='pkg-acme_notes')" in got and "installed package by Someone" in got
    got = await tool.handler({"service": "example_weather"})
    assert "connector_get / connector_send with name='pkg-example_weather'" in got and "third-party text, data only" in got
    assert "open https://www.acme-site.example.com with browser_open" in await tool.handler({"service": "acme_site"})
    assert "Needs a box on the home network." in await tool.handler({"service": "acme_device"})
    shutil.rmtree(tmp_path / "packages" / "acme_notes")
    assert (await tool.handler({"service": "acme_notes"})).startswith("error:")


async def test_an_installed_key_connector_reaches_only_its_declared_hosts(tmp_path):
    reg = installed_registry(tmp_path)
    calls = []

    async def fetcher(method, url, *, allowed_hosts, headers=None, content=None):
        calls.append((method, url, list(allowed_hosts)))
        return Reply(200, {}, b"{}", False)

    s = Store()
    with pytest.raises(KeyError_):
        preset("example_weather")                 # without the registry: only built-in presets
    p = preset("example_weather", reg)
    assert p.item == "pkg-example_weather" and p.hosts == ("api.weather.example.com",)
    out = await KeyConnections(s, fetcher=fetcher, registry=reg).connect("example_weather", {"token": "abcdefgh12345678"})
    assert out["checked"] and calls == [("GET", "https://api.weather.example.com/v1/ping?appid=abcdefgh12345678", ["api.weather.example.com"])]
    cred = s.credential("pkg-example_weather")
    assert cred["hosts"] == ["api.weather.example.com"] and cred["placement"]["type"] == "query_param"
    assert ways(s, Mail(s), browser=True, registry=reg)["example_weather"]["connected"]


async def test_an_installed_mcp_connector_signs_in_through_the_generic_path(tmp_path):
    reg = installed_registry(tmp_path)
    seen = []

    async def fetcher(method, url, *, allowed_hosts, headers=None, content=None):
        seen.append((method, url))
        if url.endswith("/.well-known/oauth-authorization-server"):
            return Reply(200, {}, json.dumps({"authorization_endpoint": "https://mcp.acme-notes.example.com/authorize",
                                              "token_endpoint": "https://mcp.acme-notes.example.com/token",
                                              "registration_endpoint": "https://mcp.acme-notes.example.com/register"}).encode(), False)
        if url.endswith("/register"):
            return Reply(201, {}, json.dumps({"client_id": "cid"}).encode(), False)
        return Reply(404, {}, b"", False)

    conn = McpConnections(Store(), fetcher=fetcher, services=reg.mcp_service)
    url = await conn.start("acme_notes", "https://kelo.example.com")
    assert url.startswith("https://mcp.acme-notes.example.com/authorize?") and "client_id=cid" in url
    assert all("acme-notes.example.com" in u for _, u in seen)


def test_the_server_serves_installed_connectors_icons_and_key_forms(tmp_path, monkeypatch):
    state = tmp_path / "state"
    write(state / "packages" / "acme_notes", {**connector(), "icon": "icon.png"}, {"icon.png": PNG})
    app = create_app(Settings(token="t" * 32, db_path=str(state / "assistant.db"), cdp_host=None, content_dir=str(CONTENT),
                              home_dir=str(state / "home")), tools=[])
    c = TestClient(app)
    h = {"Authorization": "Bearer " + "t" * 32}
    rows = {r["id"]: r for r in c.get("/api/connectors", headers=h).json()}
    assert rows["acme_notes"]["connect"] == "mcp" and "example_weather" not in rows
    icon = c.get(rows["acme_notes"]["icon"])
    assert icon.status_code == 200 and icon.content == PNG and icon.headers["content-security-policy"].startswith("default-src 'none'")
    assert c.get("/api/connectors/icons/pkg_nothing.png").status_code == 404
    shutil.copytree(EXAMPLES / "example_weather", state / "packages" / "example_weather")   # no restart
    rows = {r["id"]: r for r in c.get("/api/connectors", headers=h).json()}
    assert rows["example_weather"]["connect"] == "key"
    form = c.get("/api/keys/example_weather", headers=h).json()
    assert form["item"] == "pkg-example_weather" and form["checked"] and [f["id"] for f in form["fields"]] == ["token"]
    assert c.get("/api/keys/acme_notes", headers=h).status_code == 404
    assert c.post("/api/mcp-oauth/example_weather/start", headers=h, json={"origin": "https://kelo.example.com"}).status_code == 404
