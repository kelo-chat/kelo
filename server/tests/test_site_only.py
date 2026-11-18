"""Site-only mode (ASSISTANT_SITE_ONLY=1): an extra copy that serves kelo.chat, the download feeds, the sign-in and push brokers,
reports and beta requests, and nothing else. Every route that is not on the list answers 404 {"code": "site_only"} (websockets
close 4404), so a route added later is refused on a site copy until someone lists it on purpose (site_only.py)."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from assistant.server import Settings, create_app
from assistant.site_only import SiteRoutes

from route_table import table
from test_server import TOKEN

AUTH = {"Authorization": f"Bearer {TOKEN}"}
ENV = {"ASSISTANT_TOKEN": TOKEN, "ASSISTANT_PROVIDER": "p", "ASSISTANT_MODEL": "m", "BROWSER_CDP_HOST": ""}


def site_app(tmp_path, **kw):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "site.db"),
                               site_only=True, **kw),
                      tools=[], stream_fn=lambda *a, **k: None)


def test_site_only_settings_force_the_assistant_parts_off():
    s = Settings.from_env({**ENV, "ASSISTANT_SITE_ONLY": "1", "KEYRING_URL": "http://keyring:9500", "KEYRING_TOKEN": "x" * 32,
                           "ANDROID_DEVICE": "h:1", "ASSISTANT_UPDATER_URL": "http://updater:9400", "UPDATER_TOKEN": "y" * 32,
                           "KELO_AUTO_UPDATE": "1", "KELO_ROUTE_DIR": "/routes", "WORKROOM_SOCKET_DIR": "/work",
                           "BROWSER_CDP_HOST": "browser"})
    assert s.site_only is True
    assert s.scheduler is False and s.cdp_host is None and s.keyring_url == "" and s.android_device == ""
    assert s.updater_url == "" and s.auto_update is False and s.route_dir == "" and s.workroom_dir == ""
    assert s.operator is True                                            # a site copy is an operator unless said otherwise
    off = Settings.from_env({**ENV, "ASSISTANT_SITE_ONLY": "1", "ASSISTANT_OPERATOR": "0"})
    assert off.operator is False and off.site_only is True
    assert Settings.from_env(ENV).site_only is False                     # the main copy: unchanged


def test_every_route_not_on_the_list_is_404_site_only(tmp_path):
    """Default deny: every HTTP route of the app that the allowlist does not name answers 404 site_only, with the access key too."""
    app = site_app(tmp_path)
    gate = SiteRoutes()
    refused = [(m, row["path"]) for row in table(app) if row["kind"] == "APIRoute"
               for m in row["methods"] if not gate.allowed(m, row["path"])]
    served = [(m, row["path"]) for row in table(app) if row["kind"] == "APIRoute"
              for m in row["methods"] if gate.allowed(m, row["path"])]
    assert len(refused) > 100 and served, "the app has the routes of the assistant and the site's own"
    with TestClient(app, raise_server_exceptions=False) as c:
        for method, path in refused:
            url = "/".join("1" if seg.startswith("{") else seg for seg in path.split("/"))
            r = c.request(method, url, headers=AUTH, json={} if method in ("POST", "PUT", "PATCH") else None)
            assert r.status_code == 404, (method, path, r.status_code)
            assert r.json().get("code") == "site_only", (method, path)


def test_the_site_routes_still_work(tmp_path):
    app = site_app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/healthz").status_code == 200
        assert c.get("/broker/push/status").status_code == 200
        about = c.get("/api/about", headers=AUTH)
        assert about.status_code == 200 and about.json()["db"]["ok"] is True
        assert c.get("/download/latest.json").status_code != 404         # a site route: answered by the downloads reader


def test_chat_pair_and_websockets_are_refused(tmp_path):
    app = site_app(tmp_path)
    with TestClient(app) as c:
        for method, path in (("POST", "/api/pair"), ("POST", "/api/chat"), ("GET", "/api/devices")):
            r = c.request(method, path, headers=AUTH, json={})
            assert r.status_code == 404 and r.json()["code"] == "site_only", path
        with pytest.raises(WebSocketDisconnect) as closed:
            with c.websocket_connect("/api/turns/1/ws", headers=AUTH) as ws:
                ws.receive_text()
        assert closed.value.code == 4404


def test_site_only_starts_no_background_assistant_work(tmp_path, monkeypatch):
    """The lifespan of a site copy starts none of the assistant's background work (the workers, the phone-side route, the messenger
    and home-assistant watches); the same app as a normal copy starts them all (the control), so the recorder is not idle."""
    from assistant.ha_events import HaEvents
    from assistant.vpn import VpnRoute
    from assistant.workers import Workers

    def recorder(log, name, is_async):
        if is_async:
            async def fake(self, *a, **k):
                log.append(name)
        else:
            def fake(self, *a, **k):
                log.append(name)
                return []
        return fake

    def run(site: bool) -> list[str]:
        log: list[str] = []
        monkeypatch.setattr(HaEvents, "restart", recorder(log, "ha", False))
        monkeypatch.setattr(Workers, "startup", recorder(log, "workers", False))
        monkeypatch.setattr(VpnRoute, "start", recorder(log, "vpn", True))
        app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / f"l{site}.db"),
                                  site_only=site), tools=[], stream_fn=lambda *a, **k: None)
        with TestClient(app):
            pass
        return log

    assert "ha" in run(False) and "workers" in run(False)     # the control: a normal copy starts them
    assert run(True) == []
