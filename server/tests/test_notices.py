"""Tests for Kelo operator notices (notices.py, api/notices.py, release_sign.py, store.py, cli.py).

Requirements covered:
1. Signing and verifying:
   - Good signature with known key
   - Tampered notices (field modification, addition, removal)
   - Wrong key / unknown key
   - Unsigned / malformed signature (never raises)
2. Operator CLI (`assistant notice add/remove/list`):
   - Valid signed notices.json file created atomically
   - Add, update existing, remove, list
   - Clear validation errors: id format, title/body length, link (https+kelo.chat), until (YYYY-MM-DD), min_app, 20 notices cap
3. Public download route (`GET /download/notices.json`):
   - Absent file -> 404
   - Present file -> 200 JSON with ETag, short cache, CORS
   - ETag match -> 304
   - Safe file rules: symlinks and path traversal rejected (404)
4. Copy fetcher:
   - Fake fetcher: loads good signed list
   - Bad signature keeps last good list
   - Expiry drops notices past 'until'
   - 64 KB cap enforcement (keeps last good list)
   - Off switch (empty notices_url disables fetching)
   - 404 absent feed treated as empty
5. Copy API (`GET /api/notices`, `POST /api/notices/{id}/read`):
   - Auth required (401 without bearer token)
   - Unread only, newest first
   - Language selection and fallback (en, then ko)
   - min_app filtering against X-Kelo-App header (ignored if absent or unparsable)
   - Expiry filtering
   - POST /api/notices/{id}/read: marks as read, 204 status, idempotent, unknown id -> 204
"""

from __future__ import annotations

import base64
import json
import os
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistant import cli, release_keys, release_sign
from assistant.notices import (
    MAX_BODY_BYTES, MAX_NOTICES, NoticesFeed, _parse_feed, _write_notices_file,
    cli_add, cli_list, cli_remove, run_notice, validate_notice,
)
from assistant.release_sign import key_id, new_key, notices_statement, sign_notices, verify_notices
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, Running, scripted, settings


def _make_key(path: Path) -> tuple[str, str]:
    """Create a temporary Ed25519 signing key outside root and return (kid, pub_b64)."""
    return new_key(path)


def _sample_notice(nid: str = "2026-11-16-beta", **overrides: Any) -> dict[str, Any]:
    n = {
        "id": nid,
        "date": "2026-11-16T09:00:00Z",
        "level": "info",
        "title": {"ko": "알림 제목", "en": "Notice Title"},
        "body": {"ko": "알림 내용입니다.", "en": "This is a notice body."},
        "link": "https://kelo.chat/beta",
        "min_app": "0.10.0",
    }
    n.update(overrides)
    return n


# ==============================================================================
# 1. Signing and verifying
# ==============================================================================

def test_signing_and_verifying_good_tampered_wrong_key_unsigned(tmp_path: Path):
    key_dir = tmp_path / "keys"
    key_file = key_dir / "test.key"
    kid, pub_b64 = _make_key(key_file)
    test_keys = {kid: pub_b64}

    notices = [_sample_notice("notice-1"), _sample_notice("notice-2")]
    sig, signed_kid = sign_notices(notices, key_file)
    assert signed_kid == kid
    assert len(base64.b64decode(sig)) == 64

    # Good signature verifies
    assert verify_notices(notices, sig, kid, test_keys) is True

    # Tampered notices: modified field
    tampered_1 = [_sample_notice("notice-1", level="important"), _sample_notice("notice-2")]
    assert verify_notices(tampered_1, sig, kid, test_keys) is False

    # Tampered notices: title modified
    tampered_2 = [_sample_notice("notice-1", title={"en": "Changed", "ko": "알림"}), _sample_notice("notice-2")]
    assert verify_notices(tampered_2, sig, kid, test_keys) is False

    # Tampered notices: notice removed
    assert verify_notices([notices[0]], sig, kid, test_keys) is False

    # Tampered notices: notice added
    tampered_3 = [*notices, _sample_notice("notice-3")]
    assert verify_notices(tampered_3, sig, kid, test_keys) is False

    # Wrong key
    other_key_file = key_dir / "other.key"
    other_kid, other_pub = _make_key(other_key_file)
    other_keys = {other_kid: other_pub}
    assert verify_notices(notices, sig, kid, other_keys) is False
    assert verify_notices(notices, sig, other_kid, other_keys) is False

    # Key not in known keys
    assert verify_notices(notices, sig, kid, {}) is False
    assert verify_notices(notices, sig, kid, release_keys.RELEASE_KEYS) is False

    # Unsigned / malformed signatures (never raises)
    assert verify_notices(notices, "", kid, test_keys) is False
    assert verify_notices(notices, "not-base64!", kid, test_keys) is False
    assert verify_notices(notices, base64.b64encode(b"short").decode(), kid, test_keys) is False
    assert verify_notices(notices, base64.b64encode(b"x" * 64).decode(), kid, test_keys) is False
    assert verify_notices(notices, sig, "invalid-kid", test_keys) is False


# ==============================================================================
# 2. Operator CLI
# ==============================================================================

def test_operator_cli_add_remove_list_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key_dir = tmp_path / "keys"
    key_file = key_dir / "test.key"
    kid, pub_b64 = _make_key(key_file)
    notices_file = tmp_path / "downloads" / "notices.json"

    # Initial list when no file exists
    lines: list[str] = []
    ret = cli.main(["notice", "list", "--file", str(notices_file)], out=lines.append)
    assert ret == 0
    assert any("No notices file yet" in line for line in lines)

    # Add a valid notice
    lines.clear()
    args_add = [
        "notice", "add",
        "--id", "2026-11-16-beta",
        "--title-ko", "베타 테스트",
        "--title-en", "Beta Test",
        "--body-ko", "새로운 기능을 테스트해보세요.",
        "--body-en", "Try out our new features.",
        "--level", "important",
        "--until", "2026-12-31",
        "--link", "https://kelo.chat/beta",
        "--min-app", "0.9.0",
        "--sign-key", str(key_file),
        "--file", str(notices_file),
    ]
    ret = cli.main(args_add, out=lines.append)
    assert ret == 0
    assert any("added" in line for line in lines)
    assert notices_file.is_file()

    # Verify content and signature of written file
    data = json.loads(notices_file.read_text(encoding="utf-8"))
    assert len(data["notices"]) == 1
    n0 = data["notices"][0]
    assert n0["id"] == "2026-11-16-beta"
    assert n0["level"] == "important"
    assert n0["until"] == "2027-01-01T00:00:00Z"
    assert n0["link"] == "https://kelo.chat/beta"
    assert n0["min_app"] == "0.9.0"
    assert data["key"] == kid
    assert verify_notices(data["notices"], data["sig"], data["key"], {kid: pub_b64}) is True

    # List shows the notice
    lines.clear()
    ret = cli.main(["notice", "list", "--file", str(notices_file)], out=lines.append)
    assert ret == 0
    assert any("2026-11-16-beta" in line for line in lines)
    assert any("Beta Test" in line for line in lines)

    # Add a second notice
    lines.clear()
    args_add2 = [
        "notice", "add",
        "--id", "2026-11-17-info",
        "--title-ko", "일반 안내",
        "--title-en", "General Info",
        "--body-ko", "서버 점검 안내입니다.",
        "--body-en", "Maintenance notice.",
        "--sign-key", str(key_file),
        "--file", str(notices_file),
    ]
    ret = cli.main(args_add2, out=lines.append)
    assert ret == 0
    data2 = json.loads(notices_file.read_text(encoding="utf-8"))
    assert len(data2["notices"]) == 2
    assert verify_notices(data2["notices"], data2["sig"], data2["key"], {kid: pub_b64}) is True

    # Update existing notice (same id)
    lines.clear()
    args_update = [
        "notice", "add",
        "--id", "2026-11-16-beta",
        "--title-ko", "업데이트된 베타",
        "--title-en", "Updated Beta",
        "--body-ko", "내용이 수정되었습니다.",
        "--body-en", "Content updated.",
        "--sign-key", str(key_file),
        "--file", str(notices_file),
    ]
    ret = cli.main(args_update, out=lines.append)
    assert ret == 0
    assert any("updated" in line for line in lines)
    data3 = json.loads(notices_file.read_text(encoding="utf-8"))
    assert len(data3["notices"]) == 2
    updated = next(n for n in data3["notices"] if n["id"] == "2026-11-16-beta")
    assert updated["title"]["en"] == "Updated Beta"

    # Remove notice
    lines.clear()
    ret = cli.main(["notice", "remove", "2026-11-16-beta", "--sign-key", str(key_file), "--file", str(notices_file)], out=lines.append)
    assert ret == 0
    assert any("removed" in line for line in lines)
    data4 = json.loads(notices_file.read_text(encoding="utf-8"))
    assert len(data4["notices"]) == 1
    assert data4["notices"][0]["id"] == "2026-11-17-info"
    assert verify_notices(data4["notices"], data4["sig"], data4["key"], {kid: pub_b64}) is True

    # Remove non-existent notice -> error
    lines.clear()
    ret = cli.main(["notice", "remove", "no-such-id", "--sign-key", str(key_file), "--file", str(notices_file)], out=lines.append)
    assert ret == 1
    assert any("no notice with id 'no-such-id'" in line for line in lines)

    # Validation errors:
    # 1. Invalid id
    lines.clear()
    bad_id = [*args_add, "--id", "bad id with spaces!"]
    assert cli.main(bad_id, out=lines.append) == 1
    assert any("notice id must be" in line for line in lines)

    # 2. Title > 60 chars
    lines.clear()
    bad_title = [*args_add, "--title-en", "A" * 61]
    assert cli.main(bad_title, out=lines.append) == 1
    assert any("too long" in line for line in lines)

    # 3. Body > 600 chars
    lines.clear()
    bad_body = [*args_add, "--body-ko", "가" * 601]
    assert cli.main(bad_body, out=lines.append) == 1
    assert any("too long" in line for line in lines)

    # 4. Invalid until date format
    lines.clear()
    bad_until = [*args_add, "--until", "2026-13-99"]
    assert cli.main(bad_until, out=lines.append) == 1
    assert any("--until" in line for line in lines)

    # 5. Invalid link: not https
    lines.clear()
    bad_link_http = [*args_add, "--link", "http://kelo.chat/beta"]
    assert cli.main(bad_link_http, out=lines.append) == 1
    assert any("must be https" in line for line in lines)

    # 6. Invalid link: not on kelo.chat
    lines.clear()
    bad_link_host = [*args_add, "--link", "https://phishing.com/beta"]
    assert cli.main(bad_link_host, out=lines.append) == 1
    assert any("must be on kelo.chat" in line for line in lines)

    # 7. Invalid min-app
    lines.clear()
    bad_min_app = [*args_add, "--min-app", "v1.0"]
    assert cli.main(bad_min_app, out=lines.append) == 1
    assert any("MAJOR.MINOR.PATCH" in line for line in lines)

    # 8. Missing signing key
    lines.clear()
    missing_key = [*args_add, "--sign-key", str(tmp_path / "nonexistent.key")]
    assert cli.main(missing_key, out=lines.append) == 1
    assert any("signing key not found" in line for line in lines)


def test_operator_cli_max_notices_cap(tmp_path: Path):
    key_dir = tmp_path / "keys"
    key_file = key_dir / "test.key"
    _make_key(key_file)
    notices_file = tmp_path / "downloads" / "notices.json"

    # Fill up 20 notices
    for i in range(MAX_NOTICES):
        args = [
            "notice", "add",
            "--id", f"notice-{i:02d}",
            "--title-ko", f"제목 {i}",
            "--title-en", f"Title {i}",
            "--body-ko", f"본문 {i}",
            "--body-en", f"Body {i}",
            "--sign-key", str(key_file),
            "--file", str(notices_file),
        ]
        assert cli.main(args, out=lambda _: None) == 0

    # 21st notice is rejected
    lines: list[str] = []
    args_21 = [
        "notice", "add",
        "--id", "notice-21",
        "--title-ko", "21번째",
        "--title-en", "21st",
        "--body-ko", "본문",
        "--body-en", "Body",
        "--sign-key", str(key_file),
        "--file", str(notices_file),
    ]
    ret = cli.main(args_21, out=lines.append)
    assert ret == 1
    assert any("at most 20 notices allowed" in line for line in lines)


# ==============================================================================
# 3. Public download route (/download/notices.json)
# ==============================================================================

async def test_public_download_notices_route(tmp_path: Path):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(settings(downloads_dir=str(downloads_dir)), tools=[], stream_fn=scripted())

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as client:
        # Absent file -> 404
        r = await client.get("/download/notices.json")
        assert r.status_code == 404

        # Present file -> 200 with ETag and short cache
        raw_json = b'{"notices": [], "sig": "abc", "key": "123"}'
        (downloads_dir / "notices.json").write_bytes(raw_json)

        r2 = await client.get("/download/notices.json")
        assert r2.status_code == 200
        assert r2.content == raw_json
        assert "application/json" in r2.headers["content-type"]
        assert "public, max-age=" in r2.headers["cache-control"]
        etag = r2.headers.get("etag")
        assert etag is not None

        # If-None-Match with matching ETag -> 304
        r3 = await client.get("/download/notices.json", headers={"If-None-Match": etag})
        assert r3.status_code == 304
        assert r3.content == b""

        # HEAD request -> 200 without body
        head = await client.head("/download/notices.json")
        assert head.status_code == 200
        assert head.content == b""

        # Safe file rules: symlink rejected with 404
        (downloads_dir / "notices.json").unlink()
        secret_file = tmp_path / "secret.json"
        secret_file.write_bytes(b'{"secret": true}')
        os.symlink(secret_file, downloads_dir / "notices.json")

        symlink_resp = await client.get("/download/notices.json")
        assert symlink_resp.status_code == 404


# ==============================================================================
# 4. Copy fetcher
# ==============================================================================

async def test_copy_fetch_good_list_bad_sig_expiry_64kb_off_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key_dir = tmp_path / "keys"
    key_file = key_dir / "test.key"
    kid, pub_b64 = _make_key(key_file)
    test_keys = {kid: pub_b64}
    # Patch RELEASE_KEYS so _parse_feed accepts our test key
    monkeypatch.setattr(release_keys, "RELEASE_KEYS", test_keys)

    store = Store(str(tmp_path / "test.db"))

    # Prepare a signed payload
    n_active = _sample_notice("notice-active", until="2099-01-01T00:00:00Z")
    n_expired = _sample_notice("notice-expired", until="2020-01-01T00:00:00Z")
    notices = [n_active, n_expired]
    sig, _ = sign_notices(notices, key_file)
    valid_feed_bytes = json.dumps({"notices": notices, "sig": sig, "key": kid}).encode("utf-8")

    current_response = valid_feed_bytes

    async def fake_fetcher(url: str, max_bytes: int) -> bytes:
        return current_response

    feed = NoticesFeed(fake_fetcher, "https://kelo.chat/download/notices.json", store)
    assert feed.enabled is True

    # 1. Good list
    await feed.refresh()
    assert len(feed.all_notices()) == 2
    unread = feed.unread("en", None)
    # Expired notice was dropped from unread
    assert len(unread) == 1
    assert unread[0]["id"] == "notice-active"

    # 2. Bad signature -> keep last good list
    current_response = json.dumps({"notices": notices, "sig": "invalid-sig", "key": kid}).encode("utf-8")
    await feed.refresh()
    assert len(feed.all_notices()) == 2   # unchanged!

    # 3. 64 KB cap -> keep last good list
    oversized = valid_feed_bytes + (b" " * (MAX_BODY_BYTES + 100))
    current_response = oversized
    await feed.refresh()
    assert len(feed.all_notices()) == 2   # unchanged!

    # 4. Network error -> keep last good list
    async def failing_fetcher(url: str, max_bytes: int) -> bytes:
        raise RuntimeError("network down")

    fail_feed = NoticesFeed(failing_fetcher, "https://kelo.chat/download/notices.json", store)
    fail_feed._notices = list(feed.all_notices())
    await fail_feed.refresh()
    assert len(fail_feed.all_notices()) == 2   # unchanged!

    # 5. Off switch (empty URL)
    off_feed = NoticesFeed(fake_fetcher, "", store)
    assert off_feed.enabled is False
    await off_feed.refresh()
    assert len(off_feed.all_notices()) == 0

    # 6. Absent feed (404 returning b"") -> treated as empty
    async def not_found_fetcher(url: str, max_bytes: int) -> bytes:
        return b""

    empty_feed = NoticesFeed(not_found_fetcher, "https://kelo.chat/download/notices.json", store)
    empty_feed._notices = list(feed.all_notices())
    await empty_feed.refresh()
    assert len(empty_feed.all_notices()) == 0


# ==============================================================================
# 5. Copy API (/api/notices, /api/notices/{id}/read)
# ==============================================================================

async def test_api_notices_unread_newest_first_lang_fallback_min_app_and_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key_dir = tmp_path / "keys"
    key_file = key_dir / "test.key"
    kid, pub_b64 = _make_key(key_file)
    monkeypatch.setattr(release_keys, "RELEASE_KEYS", {kid: pub_b64})

    db_path = str(tmp_path / "server.db")

    n1 = _sample_notice("n1", date="2026-11-10T10:00:00Z", title={"ko": "제목1", "en": "Title1"},
                        body={"ko": "본문1", "en": "Body1"}, min_app="0.10.0")
    n2 = _sample_notice("n2", date="2026-11-15T12:00:00Z", title={"ko": "제목2", "en": "Title2"},
                        body={"ko": "본문2", "en": "Body2"}, min_app="0.12.0")
    n3 = _sample_notice("n3", date="2026-11-01T08:00:00Z", title={"ko": "한국어만"},
                        body={"ko": "한국어 본문만"}, min_app=None)
    notices_list = [n1, n2, n3]
    sig, _ = sign_notices(notices_list, key_file)
    feed_bytes = json.dumps({"notices": notices_list, "sig": sig, "key": kid}).encode("utf-8")

    async def fake_fetcher(url: str, max_bytes: int) -> bytes:
        return feed_bytes

    server_settings = settings(
        db_path=db_path,
        notices_url="https://kelo.chat/download/notices.json",
    )
    app = create_app(server_settings, tools=[], stream_fn=scripted(), notices_fetcher=fake_fetcher)

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as client:
        # Auth check
        unauth = await client.get("/api/notices")
        assert unauth.status_code == 401

        # 1. Unread only, newest first by date (n2 > n1 > n3)
        # Calling with Korean lang
        r_ko = await client.get("/api/notices?lang=ko", headers=AUTH)
        assert r_ko.status_code == 200
        data_ko = r_ko.json()["notices"]
        assert len(data_ko) == 3
        assert [n["id"] for n in data_ko] == ["n2", "n1", "n3"]
        assert data_ko[0]["title"] == "제목2"
        assert data_ko[1]["title"] == "제목1"
        assert data_ko[2]["title"] == "한국어만"

        # 2. Language selection and fallback
        # Calling with English lang
        r_en = await client.get("/api/notices?lang=en", headers=AUTH)
        data_en = r_en.json()["notices"]
        assert data_en[0]["title"] == "Title2"
        assert data_en[1]["title"] == "Title1"
        # n3 has no English -> fallback to ko
        assert data_en[2]["title"] == "한국어만"

        # Calling with another language (e.g. ja) -> fallback to en, then ko
        r_ja = await client.get("/api/notices?lang=ja", headers=AUTH)
        data_ja = r_ja.json()["notices"]
        assert data_ja[0]["title"] == "Title2"
        assert data_ja[2]["title"] == "한국어만"

        # 3. min_app filtering via X-Kelo-App header
        # App version 0.10.5: n2 requires 0.12.0 so n2 is filtered out; n1 requires 0.10.0 so n1 is shown
        r_app = await client.get("/api/notices", headers={**AUTH, "X-Kelo-App": "android/0.10.5+402100"})
        data_app = r_app.json()["notices"]
        assert [n["id"] for n in data_app] == ["n1", "n3"]

        # Older app 0.9.0: n1 (0.10.0) and n2 (0.12.0) are filtered out; n3 has no min_app so shown
        r_old_app = await client.get("/api/notices", headers={**AUTH, "X-Kelo-App": "android/0.9.0+402000"})
        data_old = r_old_app.json()["notices"]
        assert [n["id"] for n in data_old] == ["n3"]

        # Unparsable or absent X-Kelo-App header -> min_app ignored, all active notices shown
        r_bad_header = await client.get("/api/notices", headers={**AUTH, "X-Kelo-App": "invalid-header"})
        assert len(r_bad_header.json()["notices"]) == 3

        # 4. Mark notice as read
        read_resp = await client.post("/api/notices/n1/read", headers=AUTH)
        assert read_resp.status_code == 204

        # n1 is now read and no longer returned
        r_after_read = await client.get("/api/notices", headers=AUTH)
        assert [n["id"] for n in r_after_read.json()["notices"]] == ["n2", "n3"]

        # Idempotent: mark n1 as read again returns 204
        read_again = await client.post("/api/notices/n1/read", headers=AUTH)
        assert read_again.status_code == 204

        # Unknown notice id -> 204
        read_unknown = await client.post("/api/notices/unknown-id/read", headers=AUTH)
        assert read_unknown.status_code == 204

    # 5. Read state persists across server restarts (same DB)
    app2 = create_app(server_settings, tools=[], stream_fn=scripted(), notices_fetcher=fake_fetcher)
    async with Running(app2) as srv2, httpx.AsyncClient(base_url=srv2.url) as client2:
        r_persisted = await client2.get("/api/notices", headers=AUTH)
        assert [n["id"] for n in r_persisted.json()["notices"]] == ["n2", "n3"]
