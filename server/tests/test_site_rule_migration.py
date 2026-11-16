"""The site rule moved to the Public Suffix List (2026-11-10). Rows saved under the old rule (known payment sites, PIN sites,
payment ways, task permissions) must keep matching: lookups accept both keys, and a one-time migration adds the new key next to the
old one where the host of a past payment is known."""

import json
import time

from assistant.payment_pins import PaymentPaths, PaymentPins
from assistant.schedule_grants import _target_ok
from assistant.store import Store
from assistant.wallet import SITE_RULE_META, Wallet, legacy_site_key, migrate_site_keys, site_key, site_keys

HOST = "x.shop.example.uk.com"          # old rule: uk.com; the list: example.uk.com


def test_the_two_rules_differ_for_some_hosts_and_site_keys_gives_both():
    assert legacy_site_key(HOST) == "uk.com" and site_key(HOST) == "example.uk.com"
    assert site_keys(HOST) == ["example.uk.com", "uk.com"]
    assert site_keys("pay.example.co.kr") == ["example.co.kr"]      # the same under both: one key
    assert site_keys("shop.example.com:8443") == ["example.com"]


def _old_database(tmp_path):
    """A database as a server before the change left it: rows keyed with the old rule."""
    store = Store(tmp_path / "s.db")
    Wallet(store)   # creates the tables (and runs the migration on an empty database)
    store.set_meta(SITE_RULE_META, "")
    store._run("INSERT INTO wallet_sites(site, first_at, payments) VALUES('uk.com', 100, 3)")
    store._run("INSERT INTO wallet_payments(ts, kind, site, host, summary, amount, currency, month) "
               "VALUES(100, 'approved', 'uk.com', ?, 'x', 1000, 'KRW', '2026-11')", (HOST,))
    return store


def test_the_migration_adds_the_new_key_once_and_keeps_the_old(tmp_path):
    store = _old_database(tmp_path)
    assert migrate_site_keys(store) == {"added": 1, "hosts": 1}
    rows = {r["site"]: r["payments"] for r in store._all("SELECT site, payments FROM wallet_sites")}
    assert rows == {"uk.com": 3, "example.uk.com": 3}
    assert store.meta(SITE_RULE_META) == "psl" and json.loads(store.meta("site_rule_migration"))["added"] == 1
    assert migrate_site_keys(store) == {}                            # once only
    assert Wallet(store).known_site(HOST)


def test_an_old_known_site_still_counts_without_a_payment_host(tmp_path):
    store = _old_database(tmp_path)
    store._run("DELETE FROM wallet_payments")
    w = Wallet(store)
    assert store._all("SELECT site FROM wallet_sites") == [{"site": "uk.com"}]
    assert w.known_site(HOST)                                        # matched through the old key
    assert not w.known_site("shop.example.com")


def test_pins_payment_ways_and_task_permissions_saved_under_the_old_rule_still_match(tmp_path):
    store = Store(tmp_path / "s.db")
    pins = PaymentPins(store)
    now = time.time()
    store._run("INSERT INTO payment_pins(id, site, label, digits, e2e_blob, created_at, updated_at) VALUES('p1', 'uk.com', "
               "'shop', 6, 'blob', ?, ?)", (now, now))
    assert pins.for_host(HOST)["id"] == "p1"
    paths = PaymentPaths(store)
    store._run("INSERT INTO payment_paths(site, path, outcome, updated_at) VALUES('uk.com', 'card_form', 'protected', ?)", (now,))
    assert [r["path"] for r in paths.for_site(HOST)] == ["card_form"]
    assert _target_ok("site:example.uk.com", {"site:uk.com"}) and not _target_ok("site:example.com", {"site:uk.com"})
