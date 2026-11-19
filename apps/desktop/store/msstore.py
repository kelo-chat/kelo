#!/usr/bin/env python3
"""Upload a Kelo MSIX to the Microsoft Store with the Store submission API, from this Mac. Python standard library only.

    python3 -I apps/desktop/store/msstore.py check
    python3 -I apps/desktop/store/msstore.py status [--submission ID]
    python3 -I apps/desktop/store/msstore.py commit [--submission ID] [--wait MIN]
    python3 -I apps/desktop/store/msstore.py submit --msix PATH [--notes-en TEXT] [--notes-ko TEXT] [--commit] [--wait MIN]

Settings come from --env or $KELO_MSSTORE_ENV (default ~/.config/kelo/msstore.env), lines TENANT_ID=, CLIENT_ID=, CLIENT_SECRET=,
APP_ID= (the Store ID, 9N...). The file must be mode 600. The secret, the access token and the upload (SAS) address are never
printed or put on a command line. `submit` only prepares a draft submission; nothing is sent to certification without --commit.
Exit codes: 0 ok, 2 refused / API error, 124 waited out. See MSSTORE.md.
"""
import argparse
import json
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

LOGIN_BASE = "https://login.microsoftonline.com"
API_HOST = "https://manage.devcenter.microsoft.com"
RESOURCE = "https://manage.devcenter.microsoft.com"
POLL_SECONDS = 30
DEFAULT_ENV = "~/.config/kelo/msstore.env"

_secrets = set()  # exact values (secret, token, SAS url) that must never reach the output


class Refused(Exception):
    pass


def hide(value):
    if value and len(value) >= 6:
        _secrets.add(value)
    return value


def scrub(text):
    text = str(text)
    for s in sorted(_secrets, key=len, reverse=True):
        text = text.replace(s, "[hidden]")
    text = re.sub(r"https?://\S*[?&]sig=\S*", "[hidden url]", text)
    text = re.sub(r"(?i)\b(sig|sv|se|sp|sr|spr|st|skoid|sktid)=[^&\s\"']+", r"\1=[hidden]", text)
    text = re.sub(r"eyJ[A-Za-z0-9_\-]{8,}(\.[A-Za-z0-9_\-]+)*", "[hidden token]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{16,}", r"\1[hidden]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-~.]{40,}\b", "[hidden]", text)
    return text


def say(msg=""):
    print(scrub(msg))


def err(msg):
    print(scrub(msg), file=sys.stderr)


# ------------------------------------------------------------------------------------------------------------------ settings
def load_env(path):
    path = os.path.expanduser(path)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise Refused(f"settings file {path} not found (see apps/desktop/store/MSSTORE.md, step C)")
    if st.st_mode & 0o077:
        raise Refused(f"settings file {path} can be read by other users (mode {stat.S_IMODE(st.st_mode):o}); run: chmod 600 {path}")
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            values[k.strip()] = v
    missing = [k for k in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET", "APP_ID") if not values.get(k)]
    if missing:
        raise Refused(f"settings file {path} lacks: {', '.join(missing)}")
    hide(values["CLIENT_SECRET"])
    return values


# ------------------------------------------------------------------------------------------------------------------ http
def api_error_text(body):
    try:
        data = json.loads(body)
    except Exception:
        return scrub(body[:300]) if body else ""
    parts = []
    if isinstance(data, dict):
        for k in ("code", "message", "error", "error_description"):
            if data.get(k):
                parts.append(f"{k}={data[k]}")
        for e in data.get("errorDetails") or []:
            if isinstance(e, dict):
                parts.append(f"{e.get('code', '')}: {e.get('description') or e.get('message') or ''}")
    return scrub("; ".join(parts) or body[:300])


def request(method, url, token=None, body=None, form=None, headers=None, data_file=None, size=None):
    h = dict(headers or {})
    data = None
    if token:
        h["Authorization"] = "Bearer " + token
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif data_file is not None:
        data = data_file
        h["Content-Length"] = str(size)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raise Refused(f"{method} {scrub(urllib.parse.urlsplit(url).path)} failed: HTTP {e.code} {api_error_text(e.read().decode('utf-8', 'replace'))}")
    except (urllib.error.URLError, OSError) as e:
        raise Refused(f"{method} {scrub(urllib.parse.urlsplit(url).path)} failed: {scrub(getattr(e, 'reason', e))}")
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


class Store:
    def __init__(self, env):
        self.env = env
        self.base = f"{API_HOST}/v1.0/my/applications/{env['APP_ID']}"
        self.token = None

    def sign_in(self):
        _, data = request("POST", f"{LOGIN_BASE}/{self.env['TENANT_ID']}/oauth2/token", form={
            "grant_type": "client_credentials", "client_id": self.env["CLIENT_ID"],
            "client_secret": self.env["CLIENT_SECRET"], "resource": RESOURCE})
        tok = (data or {}).get("access_token")
        if not tok:
            raise Refused("sign-in failed: the login answer had no access token")
        self.token = hide(tok)

    def call(self, method, path="", body=None):
        return request(method, self.base + path, token=self.token, body=body)[1]


# ------------------------------------------------------------------------------------------------------------------ helpers
def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        raise Refused(f"cannot read the version '{v}'")


def pad(t, n=4):
    return t + (0,) * (n - len(t))


def sub_id(obj):
    return (obj or {}).get("id")


def describe_status(st):
    say(f"status: {st.get('status')}")
    d = st.get("statusDetails") or {}
    for key in ("errors", "warnings"):
        for e in d.get(key) or []:
            say(f"  {key[:-1]}: {e.get('code', '')} {e.get('details') or e.get('description') or ''}".rstrip())
    for c in d.get("certificationReports") or []:
        say(f"  certification report: {c.get('reportUrl') or c.get('date', '')}")


def manifest_identity(msix):
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(msix) as z:
            root = ET.fromstring(z.read("AppxManifest.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        raise Refused(f"{msix}: cannot read AppxManifest.xml ({type(e).__name__})")
    ident = next((e for e in root.iter() if e.tag.split("}")[-1] == "Identity"), None)
    if ident is None:
        raise Refused(f"{msix}: AppxManifest.xml has no Identity")
    return ident.get("Name", ""), ident.get("Publisher", ""), ident.get("Version", "")


# ------------------------------------------------------------------------------------------------------------------ commands
def cmd_check(store, a):
    store.sign_in()
    app = store.call("GET")
    say(f"app: {app.get('primaryName', '(no name)')}")
    last = sub_id(app.get("lastPublishedApplicationSubmission"))
    if last:
        sub = store.call("GET", f"/submissions/{last}/status")
        say(f"last published submission: {last} ({(sub or {}).get('status', 'unknown')})")
    else:
        say("last published submission: none")
    pend = sub_id(app.get("pendingApplicationSubmission"))
    say(f"pending submission: {pend}" if pend else "pending submission: none")
    return 0


def cmd_status(store, a):
    store.sign_in()
    sid = a.submission
    if not sid:
        sid = sub_id(store.call("GET").get("pendingApplicationSubmission"))
        if not sid:
            say("no pending submission (use --submission ID for a specific one)")
            return 0
    say(f"submission: {sid}")
    describe_status(store.call("GET", f"/submissions/{sid}/status") or {})
    return 0


def cmd_submit(store, a):
    if not os.path.isfile(a.msix):
        raise Refused(f"{a.msix} not found")
    name, _publisher, version = manifest_identity(a.msix)
    new_v = pad(vtuple(version))
    store.sign_in()
    app = store.call("GET")
    last = sub_id(app.get("lastPublishedApplicationSubmission"))
    last_sub = store.call("GET", f"/submissions/{last}") if last else None
    # identity
    want = app.get("packageIdentityName")
    if not want:
        for p in (last_sub or {}).get("applicationPackages") or []:
            want = p.get("packageIdentityName") or p.get("identityName") or want
    if want and want != name:
        raise Refused(f"the MSIX's Identity Name '{name}' is not the Store product's ('{want}'); build it with the product's identity")
    if not want:
        say("note: the Store did not report the product's identity name, so it was not compared")
    # version
    if last_sub:
        for p in last_sub.get("applicationPackages") or []:
            if p.get("version") and pad(vtuple(p["version"])) >= new_v:
                raise Refused(f"version {version} is not higher than the published package version {p['version']}; raise the version")
    # pending
    pend = sub_id(app.get("pendingApplicationSubmission"))
    if pend:
        raise Refused(f"a submission is already pending (id {pend}); finish or delete it in Partner Center, then run again (this tool never deletes one)")

    say("creating a new submission (a copy of the last published one)")
    sub = store.call("POST", "/submissions")
    sid = sub["id"]
    upload_url = hide(sub.get("fileUploadUrl"))
    if not upload_url:
        raise Refused(f"submission {sid} has no upload address")
    for p in sub.get("applicationPackages") or []:
        p["fileStatus"] = "PendingDelete"
    sub.setdefault("applicationPackages", []).append({"fileName": os.path.basename(a.msix), "fileStatus": "PendingUpload"})
    for lang, notes in (("en", a.notes_en), ("ko", a.notes_ko)):
        if notes is None:
            continue
        for key, listing in (sub.get("listings") or {}).items():
            if key.lower().split("-")[0] == lang:
                listing.setdefault("baseListing", {})["releaseNotes"] = notes
    store.call("PUT", f"/submissions/{sid}", sub)
    say(f"submission {sid} updated (old packages marked for deletion, {os.path.basename(a.msix)} added)")

    fd, tmp = tempfile.mkstemp(suffix=".zip")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "wb") as out, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(a.msix, os.path.basename(a.msix))
        size = os.path.getsize(tmp)
        say(f"uploading the package ({size // 1024} KB)")
        with open(tmp, "rb") as f:
            request("PUT", upload_url, headers={"x-ms-blob-type": "BlockBlob"}, data_file=f, size=size)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    say("package uploaded")

    if not a.commit:
        say(f"draft submission {sid} is ready. Look at it in Partner Center without changing it (a change there blocks the API), then run: msstore.py commit")
        return 0
    return commit_and_wait(store, sid, a.wait)


def commit_and_wait(store, sid, wait):
    store.call("POST", f"/submissions/{sid}/commit")
    say(f"submission {sid} committed; waiting for it to leave CommitStarted (at most {wait} min)")
    deadline = time.monotonic() + wait * 60
    while True:
        st = store.call("GET", f"/submissions/{sid}/status") or {}
        if st.get("status") != "CommitStarted":
            describe_status(st)
            if "Failed" in str(st.get("status")):
                return 2
            # Certification takes days, so this script never marks a release live. The operator records it when it is.
            say("When it is published in the Microsoft Store, record it with\n"
                "  assistant release-record desktop <version> <build> msstore\n"
                "(it also publishes the signed store row to kelo.chat; run on the operator's computer)\n"
                "(build = major*100000000 + minor*100000 + patch of the package number, for example 1.0.8 -> 100000008)")
            return 0
        if time.monotonic() >= deadline:
            say("still CommitStarted after the wait; check later with: status --submission " + sid)
            return 124
        time.sleep(POLL_SECONDS)


def cmd_commit(store, a):
    store.sign_in()
    sid = a.submission or sub_id(store.call("GET").get("pendingApplicationSubmission"))
    if not sid:
        raise Refused("there is no pending submission to commit (make a draft first with: submit --msix PATH)")
    return commit_and_wait(store, sid, a.wait)


def main(argv=None):
    p = argparse.ArgumentParser(description="Microsoft Store submission from this Mac (see MSSTORE.md)")
    p.add_argument("--env", default=os.environ.get("KELO_MSSTORE_ENV", DEFAULT_ENV))
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check")
    s = sp.add_parser("status")
    s.add_argument("--submission")
    s = sp.add_parser("commit", help="send the pending draft (or --submission ID) to certification")
    s.add_argument("--submission")
    s.add_argument("--wait", type=float, default=20)
    s = sp.add_parser("submit")
    s.add_argument("--msix", required=True)
    s.add_argument("--notes-en")
    s.add_argument("--notes-ko")
    s.add_argument("--commit", action="store_true", help="really send it to certification (without it only a draft is made)")
    s.add_argument("--wait", type=float, default=20, help="minutes to wait after --commit (default 20)")
    a = p.parse_args(argv)
    try:
        store = Store(load_env(a.env))
        return {"check": cmd_check, "status": cmd_status, "commit": cmd_commit, "submit": cmd_submit}[a.cmd](store, a)
    except Refused as e:
        err(f"refused: {e}")
        return 2
    except Exception as e:  # never let a traceback carry a header or url
        err(f"error: {type(e).__name__}: {scrub(e)}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
