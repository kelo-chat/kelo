"""The operator's oldest supported app build (ASSISTANT_MIN_APP_ANDROID / _IOS / _DESKTOP): the apps read it from /api/profile (and
/api/about) and ask an older build to update first (Android Play: an immediate in-app update; the file builds and the desktop app: their
own update download; iPhone: the App Store / TestFlight). Every value on the wire is a whole number (the Android app reads them so)."""

import httpx
import pytest

from assistant.server import Settings, create_app, desktop_version_code

from test_server import AUTH, TOKEN, Running


def settings_for(tmp_path, **env):
    s = Settings.from_env({"ASSISTANT_TOKEN": TOKEN, "ASSISTANT_HOME": str(tmp_path), **env})
    s.provider, s.model, s.cdp_host = "p", "m", None
    return s


def test_no_minimum_by_default(tmp_path):
    assert settings_for(tmp_path).min_app_version == {}
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_ANDROID="").min_app_version == {}
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_ANDROID="0").min_app_version == {}   # 0 = no minimum


def test_android_minimum_is_a_version_code(tmp_path):
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_ANDROID=" 402400 ").min_app_version == {"android": 402400}


def test_ios_minimum_is_a_build_number(tmp_path):
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_IOS="57").min_app_version == {"ios": 57}
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_IOS="0").min_app_version == {}


def test_desktop_minimum_is_the_app_version_sent_as_one_number(tmp_path):
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_DESKTOP=" 1.0.3 ").min_app_version == {"desktop": 100000003}
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_DESKTOP="1.1").min_app_version == {"desktop": 100100000}
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_DESKTOP="0").min_app_version == {}
    assert settings_for(tmp_path, ASSISTANT_MIN_APP_DESKTOP="0.0.0").min_app_version == {}


def test_desktop_codes_order_like_versions():
    order = ["0.9.99999", "1.0.2", "1.0.3", "1.0.10", "1.2.0", "1.10.0", "2.0.0", "255.255.65535"]
    codes = [desktop_version_code(v) for v in order]
    assert codes == sorted(codes) and len(set(codes)) == len(codes)
    assert desktop_version_code("1") == desktop_version_code("1.0.0") == 100000000


def test_all_three_together(tmp_path):
    s = settings_for(tmp_path, ASSISTANT_MIN_APP_ANDROID="402400", ASSISTANT_MIN_APP_IOS="12", ASSISTANT_MIN_APP_DESKTOP="1.0.3")
    assert s.min_app_version == {"android": 402400, "ios": 12, "desktop": 100000003}


@pytest.mark.parametrize("key", ["ASSISTANT_MIN_APP_ANDROID", "ASSISTANT_MIN_APP_IOS"])
@pytest.mark.parametrize("bad", ["0.1.402400", "abc", "-5", "1.0"])
def test_a_bad_minimum_stops_the_start_with_a_clear_message(tmp_path, key, bad):
    with pytest.raises(RuntimeError, match=key):
        settings_for(tmp_path, **{key: bad})


@pytest.mark.parametrize("bad", ["abc", "-1", "1.0.3.0", "v1.0.3", "1.0.3-beta", "1.1000.0", "1.0.100000", "1..3"])
def test_a_bad_desktop_minimum_stops_the_start_with_a_clear_message(tmp_path, bad):
    with pytest.raises(RuntimeError, match="ASSISTANT_MIN_APP_DESKTOP"):
        settings_for(tmp_path, ASSISTANT_MIN_APP_DESKTOP=bad)


async def test_profile_and_about_carry_the_minimum(tmp_path):
    s = settings_for(tmp_path, ASSISTANT_MIN_APP_ANDROID="402400")
    s.db_path = str(tmp_path / "a.db")
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["min_app_version"] == {"android": 402400}
        put = await c.put("/api/profile", json={"user_name": "Mina"})
        assert put.status_code == 200 and put.json()["min_app_version"] == {"android": 402400}
        assert (await c.get("/api/about")).json()["min_app_version"] == {"android": 402400}
        assert (await httpx.AsyncClient(base_url=srv.url).get("/api/about")).status_code == 401   # still behind the token


async def test_without_a_minimum_the_field_is_empty(tmp_path):
    s = settings_for(tmp_path)
    s.db_path = str(tmp_path / "a.db")
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["min_app_version"] == {}
        assert (await c.get("/api/about")).json()["min_app_version"] == {}


async def test_profile_and_about_carry_every_platform(tmp_path):
    s = settings_for(tmp_path, ASSISTANT_MIN_APP_IOS="12", ASSISTANT_MIN_APP_DESKTOP="1.0.3")
    s.db_path = str(tmp_path / "a.db")
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        want = {"ios": 12, "desktop": 100000003}
        assert (await c.get("/api/profile")).json()["min_app_version"] == want
        assert (await c.get("/api/about")).json()["min_app_version"] == want
        assert all(isinstance(v, int) for v in want.values())
