"""desktop/store/msstore.py against a local fake of the login, Partner Center and blob endpoints (no Microsoft host is called)."""

import importlib.util
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "desktop" / "store" / "msstore.py"
spec = importlib.util.spec_from_file_location("msstore", SCRIPT)
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)

SECRET = "s3cr3t-client-secret-value"
TOKEN = "tok-ABCDEFGHIJKLMNOP-access"
SAS = "sig=SASSIGNATURE123456&se=2030"
APP = "9NTESTAPP01"


class Fake:
    def __init__(self):
        self.log = []
        self.blob = None
        self.blob_headers = None
        self.put_body = None
        self.pending = None
        self.last_version = "1.0.0.0"
        self.identity = "kelo-chat.KeloAI"
        self.polls = 0
        self.final = "PreProcessing"
        self.never_finish = False


def make_server(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, obj=None):
            body = json.dumps(obj).encode() if obj is not None else b""
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n)

        def do_POST(self):
            fake.log.append(("POST", self.path))
            data = self.body()
            if self.path.endswith("/oauth2/token"):
                assert SECRET in data.decode() and "client_credentials" in data.decode()
                return self.send(200, {"access_token": TOKEN})
            assert self.headers["Authorization"] == "Bearer " + TOKEN
            if self.path.endswith("/submissions"):
                return self.send(201, {
                    "id": "SUB2", "fileUploadUrl": f"http://127.0.0.1:{self.server.server_port}/blob/x.zip?{SAS}",
                    "applicationPackages": [{"fileName": "old.msix", "fileStatus": "Uploaded", "version": fake.last_version}],
                    "listings": {"en-us": {"baseListing": {"releaseNotes": "old en"}},
                                 "ko-kr": {"baseListing": {"releaseNotes": "old ko"}},
                                 "de-de": {"baseListing": {"releaseNotes": "old de"}}}})
            if self.path.endswith("/commit"):
                fake.polls = 0
                return self.send(202, {"status": "CommitStarted"})
            self.send(404)

        def do_PUT(self):
            fake.log.append(("PUT", self.path.split("?")[0]))
            data = self.body()
            if self.path.startswith("/blob/"):
                fake.blob, fake.blob_headers = data, dict(self.headers)
                return self.send(201)
            assert self.headers["Authorization"] == "Bearer " + TOKEN
            fake.put_body = json.loads(data)
            self.send(200, fake.put_body)

        def do_GET(self):
            fake.log.append(("GET", self.path))
            assert self.headers["Authorization"] == "Bearer " + TOKEN
            if self.path.endswith(f"/applications/{APP}"):
                app = {"primaryName": "Kelo AI", "packageIdentityName": fake.identity,
                       "lastPublishedApplicationSubmission": {"id": "SUB1"}}
                if fake.pending:
                    app["pendingApplicationSubmission"] = {"id": fake.pending}
                return self.send(200, app)
            if self.path.endswith("/submissions/SUB1"):
                return self.send(200, {"id": "SUB1", "applicationPackages": [{"fileName": "old.msix", "version": fake.last_version}]})
            if self.path.endswith("/submissions/SUB1/status"):
                return self.send(200, {"status": "Published"})
            if self.path.endswith("/status"):
                fake.polls += 1
                if fake.never_finish or fake.polls < 2:
                    return self.send(200, {"status": "CommitStarted"})
                return self.send(200, {"status": fake.final, "statusDetails": {"errors": [{"code": "E1", "details": "bad thing"}] if "Failed" in fake.final else []}})
            self.send(404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = Fake()
    srv = make_server(fake)
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(ms, "LOGIN_BASE", base)
    monkeypatch.setattr(ms, "API_HOST", base)
    monkeypatch.setattr(ms, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(ms, "_secrets", set())
    f = tmp_path / "msstore.env"
    f.write_text(f"TENANT_ID=tenant\nCLIENT_ID=client\nCLIENT_SECRET={SECRET}\nAPP_ID={APP}\n")
    f.chmod(0o600)
    yield fake, f, tmp_path
    srv.shutdown()


def msix(tmp_path, name="kelo-chat.KeloAI", version="1.0.1.0"):
    p = tmp_path / "Kelo.msix"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("AppxManifest.xml", f'<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"><Identity Name="{name}" Publisher="CN=X" Version="{version}"/></Package>')
    return p


def run(capsys, f, *args):
    rc = ms.main(["--env", str(f), *args])
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert SECRET not in text and TOKEN not in text and "SASSIGNATURE" not in text and "/blob/" not in text
    return rc, out.out + out.err


def test_check(env, capsys):
    fake, f, _ = env
    rc, out = run(capsys, f, "check")
    assert rc == 0 and "Kelo AI" in out and "SUB1" in out and "Published" in out and "pending submission: none" in out
    assert all(m == "GET" or p.endswith("/oauth2/token") for m, p in fake.log)


def test_status_pending(env, capsys):
    fake, f, _ = env
    fake.pending = "SUB9"
    rc, out = run(capsys, f, "status")
    assert rc == 0 and "SUB9" in out


def test_submit_draft_does_not_commit(env, capsys):
    fake, f, tmp = env
    rc, out = run(capsys, f, "submit", "--msix", str(msix(tmp)), "--notes-en", "New", "--notes-ko", "새")
    assert rc == 0 and "draft submission SUB2 is ready" in out, out
    pk = fake.put_body["applicationPackages"]
    assert pk[0]["fileStatus"] == "PendingDelete" and pk[1] == {"fileName": "Kelo.msix", "fileStatus": "PendingUpload"}
    ls = fake.put_body["listings"]
    assert ls["en-us"]["baseListing"]["releaseNotes"] == "New" and ls["ko-kr"]["baseListing"]["releaseNotes"] == "새"
    assert ls["de-de"]["baseListing"]["releaseNotes"] == "old de"
    assert {k.lower(): v for k, v in fake.blob_headers.items()}["x-ms-blob-type"] == "BlockBlob"
    assert fake.blob[:2] == b"PK"
    assert not any(p.endswith("/commit") for _, p in fake.log)


def test_submit_without_notes_leaves_listings(env, capsys):
    fake, f, tmp = env
    assert run(capsys, f, "submit", "--msix", str(msix(tmp)))[0] == 0
    assert fake.put_body["listings"]["en-us"]["baseListing"]["releaseNotes"] == "old en"


def test_commit_polls_to_final(env, capsys):
    fake, f, tmp = env
    rc, out = run(capsys, f, "submit", "--msix", str(msix(tmp)), "--commit")
    assert rc == 0 and "status: PreProcessing" in out
    assert any(p.endswith("/SUB2/commit") for _, p in fake.log) and fake.polls == 2


def test_commit_command_sends_pending_draft(env, capsys):
    fake, f, _ = env
    fake.pending = "SUB2"
    rc, out = run(capsys, f, "commit")
    assert rc == 0 and "status: PreProcessing" in out
    assert any(p.endswith("/SUB2/commit") for _, p in fake.log)


def test_commit_command_nothing_pending_refused(env, capsys):
    fake, f, _ = env
    rc, out = run(capsys, f, "commit")
    assert rc == 2 and "no pending submission" in out and not any(p.endswith("/commit") for _, p in fake.log)


def test_commit_failed_status_exits_2(env, capsys):
    fake, f, tmp = env
    fake.final = "PreProcessingFailed"
    rc, out = run(capsys, f, "submit", "--msix", str(msix(tmp)), "--commit")
    assert rc == 2 and "bad thing" in out


def test_commit_wait_timeout_124(env, capsys):
    fake, f, tmp = env
    fake.never_finish = True
    rc, _ = run(capsys, f, "submit", "--msix", str(msix(tmp)), "--commit", "--wait", "0.0003")
    assert rc == 124


def test_pending_refused(env, capsys):
    fake, f, tmp = env
    fake.pending = "SUB9"
    rc, out = run(capsys, f, "submit", "--msix", str(msix(tmp)))
    assert rc == 2 and "SUB9" in out and not any(m == "POST" and p.endswith("/submissions") for m, p in fake.log)


def test_lower_version_refused(env, capsys):
    fake, f, tmp = env
    rc, out = run(capsys, f, "submit", "--msix", str(msix(tmp, version="1.0.0.0")))
    assert rc == 2 and "not higher" in out and not any(m == "POST" and p.endswith("/submissions") for m, p in fake.log)


def test_wrong_identity_refused(env, capsys):
    fake, f, tmp = env
    rc, out = run(capsys, f, "submit", "--msix", str(msix(tmp, name="other.App")))
    assert rc == 2 and "Identity Name" in out and not any(m == "POST" and p.endswith("/submissions") for m, p in fake.log)


def test_env_mode_644_refused(env, capsys):
    fake, f, _ = env
    f.chmod(0o644)
    rc, out = run(capsys, f, "check")
    assert rc == 2 and "chmod 600" in out and fake.log == []


def test_api_error_is_scrubbed(env, capsys):
    fake, f, _ = env
    ms._secrets.add(TOKEN)
    text = ms.scrub(f"failed with {TOKEN} and https://x.blob/y?{SAS} and eyJhbGciOiJSUzI1NiJ9.abc.def")
    assert TOKEN not in text and "SASSIGNATURE" not in text and "eyJ" not in text
