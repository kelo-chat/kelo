"""Named browser profiles as sealed site units (store, brief C2a)."""

import pytest

from assistant.store import Store
from assistant.vault import VaultError

UNIT = {"cookies": [{"name": "sid", "value": "SECRET-COOKIE-VALUE", "domain": ".shop.example", "path": "/"}],
        "storage": {"https://shop.example": {"k": "v"}}}


def test_a_profile_is_created_listed_and_deleted(tmp_path):
    st = Store(tmp_path / "a.db")
    p = st.create_browser_profile("  Work account ")
    assert p["id"].startswith("p_") and len(p["id"]) == 10 and p["label"] == "Work account"
    assert [x["label"] for x in st.browser_profiles()] == ["Work account"]
    assert st.delete_browser_profile(p["id"]) is True
    assert st.browser_profiles() == [] and st.delete_browser_profile(p["id"]) is False


def test_label_rules(tmp_path):
    st = Store(tmp_path / "a.db")
    for bad in ("", "   ", "x" * 41):
        with pytest.raises(ValueError):
            st.create_browser_profile(bad)
    st.create_browser_profile("Second")
    with pytest.raises(ValueError):
        st.create_browser_profile("second")                       # unique, case-insensitively


def test_a_unit_round_trips_sealed_and_the_raw_row_holds_no_cookie_value(tmp_path):
    st = Store(tmp_path / "a.db")
    p = st.create_browser_profile("Work")
    assert st.browser_unit(p["id"], "shop.example") == {"cookies": [], "storage": {}}
    st.save_browser_unit(p["id"], "shop.example", UNIT)
    assert st.browser_unit(p["id"], "shop.example") == UNIT
    raw = st._all("SELECT unit FROM browser_profile_units")[0]["unit"]
    assert "SECRET-COOKIE-VALUE" not in str(raw)


def test_a_unit_does_not_open_under_another_profile_or_site(tmp_path):
    st = Store(tmp_path / "a.db")
    a, b = st.create_browser_profile("A"), st.create_browser_profile("B")
    st.save_browser_unit(a["id"], "shop.example", UNIT)
    sealed = st._all("SELECT unit FROM browser_profile_units")[0]["unit"]
    st._run("INSERT INTO browser_profile_units(profile, site, unit, updated_at) VALUES (?, 'shop.example', ?, 0)", (b["id"], sealed))
    assert st.browser_unit(b["id"], "shop.example") == {"cookies": [], "storage": {}}     # bound to its own profile and site


def test_deleting_a_profile_removes_its_units_and_sign_in_rows(tmp_path):
    st = Store(tmp_path / "a.db")
    p = st.create_browser_profile("Work")
    st.save_browser_unit(p["id"], "shop.example", UNIT)
    st.note_browser_sign_in("shop.example", "login", profile=p["id"])
    st.note_browser_sign_in("shop.example", "login")                                   # the default profile's row stays
    assert [r["site"] for r in st.browser_profiles()[0]["sites"]] == ["shop.example"]
    st.delete_browser_profile(p["id"])
    assert st._all("SELECT * FROM browser_profile_units") == []
    assert [r["profile"] for r in st.browser_sign_ins()] == [""]


def test_forget_a_unit(tmp_path):
    st = Store(tmp_path / "a.db")
    p = st.create_browser_profile("Work")
    st.save_browser_unit(p["id"], "shop.example", UNIT)
    assert st.forget_browser_unit(p["id"], "shop.example") is True
    assert st.forget_browser_unit(p["id"], "shop.example") is False
    assert st.browser_unit(p["id"], "shop.example") == {"cookies": [], "storage": {}}


def test_a_locked_vault_refuses_a_unit(tmp_path):
    st = Store(tmp_path / "a.db")
    p = st.create_browser_profile("Work")
    st.save_browser_unit(p["id"], "shop.example", UNIT)
    st.set_vault_locked(True)
    with pytest.raises(VaultError, match="locked"):
        st.browser_unit(p["id"], "shop.example")
    with pytest.raises(VaultError, match="locked"):
        st.save_browser_unit(p["id"], "shop.example", UNIT)
