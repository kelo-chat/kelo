#!/usr/bin/env python3
"""Small App Store Connect API client for tools/build/ios.sh. Standard library + the `openssl` command only.

The API key comes from an env file (default ~/.appstoreconnect/kelo.env: ISSUER_ID, KEY_ID, KEY_PATH to the .p8). The key, its
contents and the signed token (JWT) are never printed, logged or put on a command line; only the key file's path is handed to
`openssl` (the data to sign goes through stdin).

    asc.py check                                  can the key sign in? prints the team's app count only
    asc.py app-id [--bundle chat.kelo.app]        the app's numeric id
    asc.py builds [--bundle ...] [--limit 5]      newest builds: id, build number, processing state
    asc.py wait-build --version 26110913 [--timeout 2700] [--interval 60]
                                                  waits until that build is VALID (exit 0), FAILED/INVALID (exit 2) or the time
                                                  is up (exit 124); a build that has not shown up yet counts as waiting
    asc.py get /v1/...                            raw GET (JSON on stdout), for anything else
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.appstoreconnect.apple.com"
DEFAULT_ENV = os.path.expanduser(os.environ.get("KELO_ASC_ENV", "~/.appstoreconnect/kelo.env"))


class AscError(Exception):
    pass


def read_env(path):
    if not os.path.isfile(path):
        raise AscError(f"no App Store Connect key settings at {path} (ISSUER_ID, KEY_ID, KEY_PATH)")
    d = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = os.path.expanduser(v.strip().strip('"').strip("'"))
    missing = [k for k in ("ISSUER_ID", "KEY_ID", "KEY_PATH") if not d.get(k)]
    if missing:
        raise AscError(f"{path} lacks {', '.join(missing)}")
    if not os.path.isfile(d["KEY_PATH"]):
        raise AscError("the key file named by KEY_PATH does not exist")
    return d


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _der_to_raw(der):
    # ECDSA signature from openssl is DER: SEQUENCE { INTEGER r, INTEGER s }; JWT wants r||s, 32 bytes each
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    out = b""
    for _ in range(2):
        if der[i] != 2:
            raise AscError("unexpected signature format from openssl")
        ln = der[i + 1]
        out += der[i + 2:i + 2 + ln].lstrip(b"\x00").rjust(32, b"\x00")
        i += 2 + ln
    return out


class Client:
    def __init__(self, env_path=DEFAULT_ENV):
        self._env = read_env(env_path)
        self._token = None
        self._token_exp = 0

    def _auth(self):
        now = int(time.time())
        if self._token and now < self._token_exp - 60:
            return self._token
        e = self._env
        head = _b64(json.dumps({"alg": "ES256", "kid": e["KEY_ID"], "typ": "JWT"}).encode())
        exp = now + 1100  # Apple allows at most 20 minutes
        body = _b64(json.dumps({"iss": e["ISSUER_ID"], "iat": now, "exp": exp, "aud": "appstoreconnect-v1"}).encode())
        msg = f"{head}.{body}".encode()
        p = subprocess.run(["openssl", "dgst", "-sha256", "-sign", e["KEY_PATH"]], input=msg, capture_output=True, timeout=30)
        if p.returncode != 0:
            raise AscError("openssl could not sign with the key (is KEY_PATH a valid .p8?)")  # stderr not echoed
        self._token, self._token_exp = f"{head}.{body}.{_b64(_der_to_raw(p.stdout))}", exp
        return self._token

    def call(self, method, path, body=None):
        url = path if path.startswith("https://") else API + path
        if not url.startswith(API + "/"):
            raise AscError("only api.appstoreconnect.apple.com is called")  # the token never goes anywhere else
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self._auth())
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            try:
                return ex.code, json.loads(raw)
            except ValueError:
                return ex.code, {"raw": raw.decode(errors="replace")[:2000]}
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            return 0, {"network_error": type(ex).__name__}

    def get_ok(self, path):
        st, js = self.call("GET", path)
        if st != 200:
            errs = [f"{e.get('status')} {e.get('title')}: {e.get('detail')}" for e in js.get("errors", [])] or [str(js)[:500]]
            raise AscError(f"GET {path.split('?')[0]} -> HTTP {st}: " + "; ".join(errs))
        return js

    def app_id(self, bundle):
        js = self.get_ok("/v1/apps?" + urllib.parse.urlencode({"filter[bundleId]": bundle, "fields[apps]": "bundleId"}))
        for d in js.get("data", []):
            if d["attributes"].get("bundleId") == bundle:
                return d["id"]
        raise AscError(f"no app with bundle id {bundle} in this App Store Connect team")

    def builds(self, app, version=None, limit=5):
        q = {"filter[app]": app, "sort": "-uploadedDate", "limit": str(limit),
             "fields[builds]": "version,processingState,uploadedDate,expired"}
        if version:
            q["filter[version]"] = str(version)
        js = self.get_ok("/v1/builds?" + urllib.parse.urlencode(q))
        return [(d["id"], d["attributes"].get("version"), d["attributes"].get("processingState"),
                 d["attributes"].get("uploadedDate")) for d in js.get("data", [])]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=DEFAULT_ENV, help="key settings file (default ~/.appstoreconnect/kelo.env or $KELO_ASC_ENV)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    for name in ("app-id", "builds", "wait-build"):
        s = sub.add_parser(name)
        s.add_argument("--bundle", default="chat.kelo.app")
        if name == "builds":
            s.add_argument("--limit", type=int, default=5)
        if name == "wait-build":
            s.add_argument("--version", required=True, help="the build number (CFBundleVersion)")
            s.add_argument("--timeout", type=int, default=2700, help="seconds before giving up (default 45 min)")
            s.add_argument("--interval", type=int, default=60)
    g = sub.add_parser("get")
    g.add_argument("path")
    a = ap.parse_args(argv)

    try:
        c = Client(a.env)
        if a.cmd == "check":
            js = c.get_ok("/v1/apps?limit=200&fields[apps]=bundleId")
            print(f"ok: the key signs in ({len(js.get('data', []))} app(s) in the team)")
        elif a.cmd == "app-id":
            print(c.app_id(a.bundle))
        elif a.cmd == "builds":
            for row in c.builds(c.app_id(a.bundle), limit=a.limit):
                print(*row)
        elif a.cmd == "get":
            st, js = c.call("GET", a.path)
            print(json.dumps(js, indent=1))
            return 0 if st == 200 else 1
        elif a.cmd == "wait-build":
            app = c.app_id(a.bundle)
            deadline = time.monotonic() + a.timeout
            last = None
            while True:
                try:
                    rows = c.builds(app, version=a.version, limit=1)
                    state = rows[0][2] if rows else "NOT_YET_LISTED"
                except AscError as ex:  # a passing API hiccup: keep waiting until the deadline
                    state = f"API_ERROR ({ex})"
                if state != last:
                    print(f"{time.strftime('%H:%M:%S')} build {a.version}: {state}", flush=True)
                    last = state
                if state == "VALID":
                    # Processed is not released: TestFlight and review do not count. The operator records it when it is live.
                    print(f"When this build is live in the App Store (Ready for Distribution), record it with\n"
                          f"  assistant release-record ios <version> {a.version} appstore")
                    return 0
                if state in ("FAILED", "INVALID"):
                    return 2
                if time.monotonic() + a.interval > deadline:
                    print(f"gave up after {a.timeout}s; last state {state}", file=sys.stderr)
                    return 124
                time.sleep(a.interval)
    except AscError as ex:
        print(f"asc: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
