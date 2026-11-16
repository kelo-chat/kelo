"""Keyguard: the master key of the server's SECRETS, held by the phones (docs/security/e2e-and-phone-key.md section 2).

Scope (the owner's decision, 2026-11-10): only secrets: the vault (website logins, service keys, connector tokens), the provider API
keys and the auto-pay cards. Chats, memory, files and the personal data the phones share are NOT under Keyguard: they stay readable
after a restart. Personal data (phone calendar, contacts, notifications...) is sealed with its own key on the server (`data.key`)
once Keyguard was turned on, so a locked server still has it.

`KeyGuard` is the only code that reads `master.key` or $ASSISTANT_SECRET_KEY; `Store` takes its sealers from it.

Modes:
- "server" (default): the master key is `<state>/master.key` (or the env var), as before.
- "phone": the key exists only in memory while unlocked. After a restart the server is LOCKED: every use of a secret raises
  `Locked` (the API answers 423 {"code": "locked"}) until a phone sends the key (`unlock`). Each phone keeps its own
  wrapped copy (the server stores it, never opens it: format byte 0x01 Android Keystore AES-GCM, 0x02 iPhone Secure Enclave ECIES;
  the phones use the AAD "kelo-keyguard v1"). A recovery code (24 characters, shown once) wraps the key with Argon2id + AES-256-GCM.
  A check value HMAC-SHA256(K, "kelo keyguard check") tells a wrong key from the right one.

State file `<state>/keyguard.json` (0600): {"mode", "check", "phones": [{"device_id", "alg", "blob", "created"}],
"recovery": {"salt", "nonce", "wrapped", "params"}}. It never holds the key itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from .vault import Sealer, aad_for

CHECK_LABEL = b"kelo keyguard check"
BLOB_KINDS = (0x01, 0x02)          # Android Keystore AES-GCM, iPhone Secure Enclave ECIES (spec 2.4)
MAX_BLOB = 4096
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I
RECOVERY_LEN = 24
ARGON = {"iterations": 3, "lanes": 4, "memory_cost": 64 * 1024}   # 64 MiB (RFC 9106 second recommendation)
ENROLL_SECONDS = 300.0
VAULT_INFO = b"assistant/vault/v2"
CARDS_INFO = b"assistant/wallet/cards/v1"
DATA_INFO = b"assistant/personal/v1"


class Locked(Exception):
    """A secret is needed while Keyguard holds no key (the server restarted in phone mode). The API answers 423 {"code": "locked"}."""


class KeyGuardError(ValueError):
    """A refused Keyguard step (the message is safe for the app). `code` names it for the apps."""

    def __init__(self, message: str, code: str = "keyguard", status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def load_server_key(path: str | Path | None, secret: str | None) -> bytes:
    """Master key in server mode: $ASSISTANT_SECRET_KEY, else a 0600 file next to the DB (made on first start)."""
    if secret:
        try:
            Fernet(secret.encode())
        except ValueError as e:
            raise RuntimeError("ASSISTANT_SECRET_KEY must be a Fernet key: python -c "
                               "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'") from e
        return secret.encode()
    if path is None:
        return Fernet.generate_key()
    keyfile = Path(path)
    if not keyfile.exists():
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return keyfile.read_bytes().strip()


def check_value(key: bytes) -> str:
    return hmac.new(key, CHECK_LABEL, hashlib.sha256).hexdigest()


def new_recovery_code() -> str:
    return "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LEN))


def _norm_code(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def _derive(code: str, salt: bytes, params: dict[str, int]) -> bytes:
    return Argon2id(salt=salt, length=32, iterations=params["iterations"], lanes=params["lanes"],
                    memory_cost=params["memory_cost"]).derive(_norm_code(code).encode())


def wrap_recovery(key: bytes, code: str, params: dict[str, int] | None = None) -> dict[str, Any]:
    params = dict(params or ARGON)
    salt, nonce = os.urandom(16), os.urandom(12)
    wrapped = AESGCM(_derive(code, salt, params)).encrypt(nonce, key, b"kelo-keyguard recovery v1")
    b = base64.b64encode
    return {"salt": b(salt).decode(), "nonce": b(nonce).decode(), "wrapped": b(wrapped).decode(), "params": params}


def unwrap_recovery(rec: dict[str, Any], code: str) -> bytes | None:
    d = base64.b64decode
    try:
        return AESGCM(_derive(code, d(rec["salt"]), rec["params"])).decrypt(d(rec["nonce"]), d(rec["wrapped"]), b"kelo-keyguard recovery v1")
    except Exception:  # noqa: BLE001 - a wrong code
        return None


class KeyGuard:
    def __init__(self, state_dir: str | Path | None, env_secret: str | None = None, clock: Any = time.time):
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.env_secret = env_secret or None
        self.clock = clock
        self._lock = threading.RLock()
        self._key: bytes | None = None
        self._cache: dict[tuple[bytes, bytes], Sealer] = {}
        self._enrolling: dict[str, dict[str, Any]] = {}
        self._on_unlock: list[Any] = []   # work waiting for the unlock (after_unlock)
        self.state = self._read_state()
        if self.mode == "server":
            self._key = load_server_key(self.master_file, self.env_secret)
        self._data_key = self._read_data_key()

    # ---- files --------------------------------------------------------------------------------------------------------------------
    @property
    def master_file(self) -> Path | None:
        return self.state_dir / "master.key" if self.state_dir is not None else None

    @property
    def state_file(self) -> Path | None:
        return self.state_dir / "keyguard.json" if self.state_dir is not None else None

    @property
    def data_file(self) -> Path | None:
        return self.state_dir / "data.key" if self.state_dir is not None else None

    def _read_state(self) -> dict[str, Any]:
        f = self.state_file
        if f is not None and f.exists():
            return json.loads(f.read_text())
        return {"mode": "server", "phones": []}

    def _write_state(self) -> None:
        f = self.state_file
        if f is None:
            return
        tmp = f.with_name(f.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(self.state, indent=1))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, f)

    def _read_data_key(self) -> bytes | None:
        f = self.data_file
        if f is not None and f.exists():
            return f.read_bytes().strip()
        return self._data_key if hasattr(self, "_data_key") else None

    # ---- the key ------------------------------------------------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.state.get("mode", "server")

    @property
    def locked(self) -> bool:
        return self._key is None

    def key(self) -> bytes:
        k = self._key
        if k is None:
            raise Locked("the server is locked: open Kelo on a phone that holds the key to unlock it")
        return k

    def _sealer(self, key: bytes, info: bytes) -> Sealer:
        s = self._cache.get((key, info))
        if s is None:
            s = self._cache[(key, info)] = Sealer(key, info=info)
        return s

    def sealer(self, info: bytes) -> Sealer:
        return self._sealer(self.key(), info)

    def fernet(self) -> Fernet:
        return Fernet(self.key())

    def system_fernet(self) -> Fernet:
        """Kelo's own working keys in the api_keys table (names with a colon: this copy's install secrets, the phone digest key, the
        operator's service tokens): not the owner's passwords, keys or cards, and needed while locked (the unlock push itself).
        They follow the personal data's key once Keyguard was turned on, the master key before."""
        if self._data_key is not None:
            return Fernet(self._data_key)
        return self.fernet()

    def data_sealer(self) -> Sealer:
        """Personal data: its own key once Keyguard was turned on (readable while locked), else the master key as before."""
        if self._data_key is not None:
            return self._sealer(self._data_key, DATA_INFO)
        return self.sealer(DATA_INFO)

    # ---- the API's view ----------------------------------------------------------------------------------------------------------
    def view(self, device_names: dict[int, str] | None = None) -> dict[str, Any]:
        names = device_names or {}
        return {"mode": self.mode, "locked": self.locked, "env_key": bool(self.env_secret),
                "recovery": bool(self.state.get("recovery")),
                "phones": [{"device_id": p["device_id"], "name": names.get(p["device_id"], ""), "alg": p["alg"], "created": p["created"]}
                           for p in self.state.get("phones", [])]}

    def blob_for(self, device_id: int) -> dict[str, Any] | None:
        p = next((p for p in self.state.get("phones", []) if p["device_id"] == device_id), None)
        return {"alg": p["alg"], "blob": p["blob"]} if p else None

    # ---- turning it on, more phones ------------------------------------------------------------------------------------------------
    def start_enroll(self, device_id: int) -> dict[str, Any]:
        """Step 1 of enabling (or of adding another phone while unlocked): the key the phone must wrap, sent only inside KE2E.
        Enabling makes a NEW key K' (nothing is changed until finish); adding a phone hands out the current one."""
        with self._lock:
            if self.env_secret:
                raise KeyGuardError("the master key is set in the server's .env (ASSISTANT_SECRET_KEY); remove it there first",
                                    "env_key")
            if self.state_dir is None:
                raise KeyGuardError("this server keeps no state folder", "no_state")
            enabling = self.mode != "phone"
            if not enabling and self.locked:
                raise Locked("the server is locked")
            if any(p["device_id"] == device_id for p in self.state.get("phones", [])):
                raise KeyGuardError("this phone already holds the key", "enrolled")
            new = Fernet.generate_key() if enabling else self.key()
            eid = secrets.token_urlsafe(16)
            self._enrolling = {k: v for k, v in self._enrolling.items() if v["until"] > self.clock()}
            self._enrolling[eid] = {"device_id": device_id, "key": new, "enabling": enabling, "until": self.clock() + ENROLL_SECONDS}
            out = {"enrollment_id": eid, "key": new.decode(), "enabling": enabling}
            if enabling:
                out["recovery_code"] = self._enrolling[eid]["code"] = new_recovery_code()
            return out

    def finish_enroll(self, store: Any, device_id: int, enrollment_id: str, alg: int, blob: str) -> dict[str, Any]:
        """Step 2: the phone's wrapped copy. Enabling re-seals every secret from K to K' in one transaction, keeps personal data under
        its own key, stores the recovery wrap and the check value, and deletes master.key."""
        with self._lock:
            e = self._enrolling.get(enrollment_id)
            if e is None or e["device_id"] != device_id or e["until"] < self.clock():
                raise KeyGuardError("this enrollment ran out or is not this phone's; start again", "enrollment", 404)
            raw = _check_blob(alg, blob)
            phone = {"device_id": device_id, "alg": alg, "blob": raw, "created": self.clock()}
            if e["enabling"]:
                old = self.key()
                new = e["key"]
                data_key = self._data_key or Fernet.generate_key()
                rekey(store, old, new, old_data=self._data_key, new_data=data_key)
                if self._data_key is None and self.data_file is not None:
                    _write_secret(self.data_file, data_key)
                self._data_key = data_key
                self.state = {"mode": "phone", "check": check_value(new), "phones": [phone],
                              "recovery": wrap_recovery(new, e["code"]), "enabled_at": self.clock()}
                self._write_state()
                self._key = new
                if self.master_file is not None and self.master_file.exists():
                    _shred(self.master_file)
            else:
                self.state.setdefault("phones", []).append(phone)
                self._write_state()
            self._enrolling.pop(enrollment_id, None)
            return self.view()

    def pending_rotation(self, device_id: int, enrollment_id: str, alg: int, blob: str) -> tuple[bytes, bytes] | None:
        """What finish_enroll would do to the key, checked but not done: (old key, new key) when turning Keyguard on changes it, else
        None. Raises what finish_enroll would raise for the same input, so the caller can re-seal outside the key first."""
        with self._lock:
            e = self._enrolling.get(enrollment_id)
            if e is None or e["device_id"] != device_id or e["until"] < self.clock():
                raise KeyGuardError("this enrollment ran out or is not this phone's; start again", "enrollment", 404)
            _check_blob(alg, blob)
            if not e["enabling"] or self.locked:
                return None
            return (self.key(), e["key"]) if self.key() != e["key"] else None

    # ---- after a restart --------------------------------------------------------------------------------------------------------------
    def unlock(self, key_b64: str) -> bool:
        with self._lock:
            if self.mode != "phone":
                raise KeyGuardError("Keyguard is off", "off")
            try:
                key = base64.urlsafe_b64encode(base64.urlsafe_b64decode(key_b64.encode()))
                Fernet(key)
            except Exception:  # noqa: BLE001
                raise KeyGuardError("that is not the key", "wrong_key", 422) from None
            if not hmac.compare_digest(check_value(key), self.state.get("check", "")):
                raise KeyGuardError("that is not the key", "wrong_key", 422)
            self._key = key
        self._unlocked()
        return True

    def recover(self, code: str) -> bool:
        """The recovery code instead of a phone: unlocks (the phones' copies stay; a new phone can enroll while unlocked)."""
        with self._lock:
            rec = self.state.get("recovery")
            if self.mode != "phone" or not rec:
                raise KeyGuardError("Keyguard is off", "off")
            key = unwrap_recovery(rec, code)
            if key is None or not hmac.compare_digest(check_value(key), self.state.get("check", "")):
                raise KeyGuardError("that recovery code does not open the key", "wrong_code", 422)
            self._key = key
        self._unlocked()
        return True

    def remove_phone(self, device_id: int) -> dict[str, Any]:
        with self._lock:
            phones = self.state.get("phones", [])
            if not any(p["device_id"] == device_id for p in phones):
                raise KeyGuardError("this phone holds no key", "not_enrolled", 404)
            if len(phones) == 1:
                raise KeyGuardError("the last phone cannot be removed; turn Keyguard off instead", "last_keyguard_phone")
            self.state["phones"] = [p for p in phones if p["device_id"] != device_id]
            self._write_state()
            return self.view()

    def forget_device(self, device_id: int) -> bool:
        """A signed-out device takes its copy with it. The last copy while Keyguard is on is refused (turn it off first, or use the
        recovery code later). Returns whether it held one."""
        with self._lock:
            phones = self.state.get("phones", [])
            if not any(p["device_id"] == device_id for p in phones):
                return False
            if self.mode == "phone" and len(phones) == 1:
                raise KeyGuardError("this is the only phone that holds the key of your passwords, keys and cards; add another phone "
                                    "or turn Keyguard off before removing it", "last_keyguard_phone")
            self.state["phones"] = [p for p in phones if p["device_id"] != device_id]
            self._write_state()
            return True

    def write_master_from_env(self) -> bool:
        """Before ASSISTANT_SECRET_KEY leaves .env (`assistant keyguard enable`): the same key goes into master.key (0600), so the
        server opens its secrets after the restart. A different master.key is kept aside as master.key.bak-<time>. Never logged."""
        with self._lock:
            if not self.env_secret or self.master_file is None:
                return False
            f = self.master_file
            if f.exists() and f.read_bytes().strip() == self.env_secret.encode():
                return False
            if f.exists():
                os.replace(f, f.with_name(f"master.key.bak-{int(self.clock())}"))
            _write_secret(f, self.env_secret.encode())
            return True

    def after_unlock(self, fn: Any) -> None:
        """Run `fn` (sync, or returning a coroutine) now if unlocked, else once right after the unlock (resumed jobs, deferred work)."""
        if not self.locked:
            _call(fn)
        else:
            self._on_unlock.append(fn)

    def _unlocked(self) -> None:
        waiting, self._on_unlock = self._on_unlock, []
        for fn in waiting:
            _call(fn)

    async def wait_unlocked(self, timeout: float, poll: float = 2.0) -> bool:
        """For background work: wait (up to `timeout` seconds) for a phone to unlock. True once unlocked."""
        import asyncio
        end = time.monotonic() + timeout
        while self.locked and time.monotonic() < end:
            await asyncio.sleep(min(poll, max(0.0, end - time.monotonic())))
        return not self.locked

    def disable(self) -> dict[str, Any]:
        """Back to the key on the server (master.key written again). Only while unlocked."""
        with self._lock:
            if self.mode != "phone":
                raise KeyGuardError("Keyguard is off", "off")
            key = self.key()
            if self.master_file is not None:
                _write_secret(self.master_file, key)
            self.state = {"mode": "server", "phones": []}
            self._write_state()
            return self.view()


def is_system_key(name: str) -> bool:
    """An api_keys row that is Kelo's own working key ("push:install_id", "operator:firebase", ...), not an AI provider's API key."""
    return ":" in name


def _call(fn: Any) -> None:
    import asyncio
    import inspect
    import logging
    try:
        r = fn()
        if inspect.isawaitable(r):
            asyncio.ensure_future(r)
    except Exception:  # noqa: BLE001 - one waiting job never stops the others (its own error is its own business)
        logging.getLogger("uvicorn.error").exception("work waiting for Keyguard's unlock failed")


def _check_blob(alg: int, blob: str) -> str:
    if alg not in BLOB_KINDS:
        raise KeyGuardError("unknown wrap format", "blob", 422)
    try:
        raw = base64.b64decode(blob.encode(), validate=True)
    except Exception:  # noqa: BLE001
        raise KeyGuardError("the wrapped key is not base64", "blob", 422) from None
    if not 16 <= len(raw) <= MAX_BLOB or raw[0] != alg:
        raise KeyGuardError("the wrapped key does not start with its format byte", "blob", 422)
    return blob


def _write_secret(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _shred(path: Path) -> None:
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(os.urandom(max(size, 64)))
            f.flush()
            os.fsync(f.fileno())
    finally:
        path.unlink(missing_ok=True)


# ---- re-sealing ------------------------------------------------------------------------------------------------------------------------
DATA_ROWS = (   # personal data: (table, key columns, aad from a row)
    ("phone_calendar", ("owner",), lambda r: f"phone-calendar:{r['owner']}"),
    ("phone_data", ("owner", "kind"), lambda r: f"phone-{r['kind']}:{r['owner']}"),
    ("phone_notes", ("id",), lambda r: f"phone-notifications:{r['owner']}"),
    ("phone_tasks", ("id",), lambda r: f"phone-task:{r['owner']}"),
)


def rekey(store: Any, old: bytes, new: bytes, *, old_data: bytes | None, new_data: bytes) -> dict[str, int]:
    """Every secret from the old master key to the new one, and personal data to its own key, in ONE transaction (all or nothing).
    A value that does not open with the old key (altered, or from a lost key) is left as it is: it did not open before either."""
    f_old, f_new = Fernet(old), Fernet(new)
    v_old, v_new = Sealer(old, info=VAULT_INFO), Sealer(new, info=VAULT_INFO)
    c_old, c_new = Sealer(old, info=CARDS_INFO), Sealer(new, info=CARDS_INFO)
    d_old, d_new = Sealer(old_data or old, info=DATA_INFO), Sealer(new_data, info=DATA_INFO)
    counts = {"api_keys": 0, "credentials": 0, "cards": 0, "personal": 0}
    db = store._db
    with store._lock:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        try:
            db.execute("BEGIN IMMEDIATE")
            f_sys_old, f_sys_new = Fernet(old_data or old), Fernet(new_data)
            for prov, value in db.execute("SELECT provider, key FROM api_keys").fetchall():
                system = is_system_key(prov)   # Kelo's own working keys go with the personal data's key (readable while locked)
                try:
                    plain = (f_sys_old if system else f_old).decrypt(value.encode())
                except InvalidToken:
                    continue
                db.execute("UPDATE api_keys SET key = ? WHERE provider = ?", ((f_sys_new if system else f_new).encrypt(plain).decode(), prov))
                counts["api_keys"] += 1
            for row in db.execute("SELECT name, kind, hosts, placement, secret FROM credentials WHERE secret IS NOT NULL").fetchall():
                name, kind, hosts, placement, secret = row
                aad = aad_for(name, kind, json.loads(hosts), json.loads(placement))
                plain = v_old.open(secret, aad)
                if plain is None and not secret.startswith("v2:"):
                    try:
                        plain = f_old.decrypt(secret.encode()).decode()
                    except InvalidToken:
                        plain = None
                if plain is None:
                    continue
                db.execute("UPDATE credentials SET secret = ? WHERE name = ?", (v_new.seal(plain, aad), name))
                counts["credentials"] += 1
            if "wallet_cards" in tables:
                for cid, last4, sealed in db.execute("SELECT id, last4, sealed FROM wallet_cards WHERE sealed IS NOT NULL").fetchall():
                    aad = json.dumps([cid, "card", last4], separators=(",", ":"))
                    plain = c_old.open(sealed, aad)
                    if plain is None:
                        continue
                    db.execute("UPDATE wallet_cards SET sealed = ? WHERE id = ?", (c_new.seal(plain, aad), cid))
                    counts["cards"] += 1
            if new_data != (old_data or old):
                for table, keys, aad_of in DATA_ROWS:
                    if table not in tables:
                        continue
                    cols = ", ".join(dict.fromkeys(("owner", *keys, "data")))
                    for r in db.execute(f"SELECT {cols} FROM {table}").fetchall():
                        row = dict(zip(dict.fromkeys(("owner", *keys, "data")), r))
                        plain = d_old.open(row["data"], aad_of(row))
                        if plain is None:
                            continue
                        where = " AND ".join(f"{k} = ?" for k in keys)
                        db.execute(f"UPDATE {table} SET data = ? WHERE {where}", (d_new.seal(plain, aad_of(row)), *[row[k] for k in keys]))
                        counts["personal"] += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
    return counts
