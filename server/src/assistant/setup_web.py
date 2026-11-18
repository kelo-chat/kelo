"""`assistant setup --web`: the guided setup (wizard.py) in a page on this computer, opened in the browser.

The wizard runs unchanged in a thread. `WebUI` is its UI object (wizard.UI): every call becomes an event for the page, and each
question waits until the page answers it (30 minutes at most). If nobody answers in that time the setup stops there: no suggested
answer is taken for the owner, so nothing is published or opened that the owner did not choose.

The server listens on 127.0.0.1 only. Every request except the page itself must carry the one-time token in the X-Kelo-Setup
header; the page reads the token from the URL fragment (#t=...), which a browser never sends to the server, so the token stays out
of server logs and Referer headers. The Host header must name 127.0.0.1 or localhost with this port (DNS-rebinding guard). The
server stops 60 s after the summary or the failure has been delivered, or after 2 hours at most.
"""

from __future__ import annotations

import html
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from . import i18n

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("setup_web.html")
TOKEN_HEADER = "X-Kelo-Setup"
BIND = "127.0.0.1"
ANSWER_TIMEOUT = 30 * 60.0
GRACE = 60.0
MAX_AGE = 2 * 3600.0
POLL = 25.0
MAX_BODY = 4096
MAX_ANSWER = 500
FINAL = ("summary", "fail")
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def page_strings(lang: str) -> dict[str, str]:
    """The page's own labels, keyed by their English text (the page shows the key when a label is missing)."""
    def t(text: str) -> str:
        return i18n.t(text, lang=lang)

    return {
        "Continue": t("Continue"),
        "yes": t("yes"),
        "no": t("no"),
        "All set": t("All set"),
        "Useful later": t("Useful later"),
        "Connecting to the setup…": t("Connecting to the setup…"),
        "Setup has ended. You can close this page.": t("Setup has ended. You can close this page."),
        "The link is not valid. Open the link that the installer or the terminal printed.": t(
            "The link is not valid. Open the link that the installer or the terminal printed."),
        "The connection to the setup was lost. Run the setup again.": t("The connection to the setup was lost. Run the setup again."),
        "Could not send your answer. Try again.": t("Could not send your answer. Try again."),
    }


@dataclass
class _Question:
    kind: str
    options: tuple[str, ...] = ()
    answered: bool = False
    value: Any = None
    done: threading.Event = field(default_factory=threading.Event)


def _check(q: _Question, value: Any) -> None:
    if q.kind == "choose":
        ok = isinstance(value, str) and value in q.options
    elif q.kind == "confirm":
        ok = isinstance(value, bool)
    else:
        ok = isinstance(value, str) and len(value) <= MAX_ANSWER
    if not ok:
        raise ValueError(f"not a valid answer to {q.kind}")


class SetupTimedOut(BaseException):
    """No answer came in time. A BaseException on purpose: no `except Exception` in the wizard may swallow it and go on with a default."""


class WebUI:
    """The wizard's UI (wizard.UI) for the browser page. Events are kept in order; the page reads them with `events`."""

    def __init__(self, lang: str, *, answer_timeout: float = ANSWER_TIMEOUT):
        self.lang = lang
        self.answer_timeout = answer_timeout
        self.finished = False                     # a summary or a failure has been emitted: the run is over
        self.delivered_at: float | None = None    # when the page first received that final event
        self._cond = threading.Condition()
        self._events: list[dict[str, Any]] = []
        self._pending: dict[int, _Question] = {}
        self._emit({"type": "hello", "lang": lang, "strings": page_strings(lang)})

    # ------------------------------------------------------------------------------------------------ the event list
    def _emit(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._cond:
            event = {"id": len(self._events) + 1, **event}
            self._events.append(event)
            if event["type"] in FINAL:
                self.finished = True
            self._cond.notify_all()
        return event

    def events(self, after: int, timeout: float = POLL) -> list[dict[str, Any]]:
        """Every event with an id above `after`; waits up to `timeout` seconds when there is none yet."""
        with self._cond:
            self._cond.wait_for(lambda: len(self._events) > after, timeout)
            out = list(self._events[after:])
            if self.delivered_at is None and any(e["type"] in FINAL for e in out):
                self.delivered_at = time.monotonic()
            return out

    def answer(self, qid: int, value: Any) -> None:
        """The page's answer to question `qid`. LookupError when that question is not waiting (answered or timed out)."""
        with self._cond:
            q = self._pending.get(qid)
            if q is None:
                raise LookupError(qid)
            _check(q, value)
            q.value, q.answered = value, True
            del self._pending[qid]
            self._emit({"type": "resolved", "id": qid})
        q.done.set()

    # ------------------------------------------------------------------------------------------------ wizard.UI
    def banner(self, rerun: bool) -> None:
        self._emit({
            "type": "banner",
            "tagline": i18n.t("Your personal assistant, on a server you run.", lang=self.lang),
            "rerun": rerun,
            "rerun_text": i18n.t("Kelo is already set up here. This run updates it and keeps your data, keys and phones.",
                                 lang=self.lang) if rerun else "",
        })

    def step(self, n: int, title: str) -> None:
        self._emit({"type": "step", "n": n, "total": 4, "title": title})

    def ok(self, text: str) -> None:
        self._emit({"type": "ok", "text": text})

    def warn(self, text: str) -> None:
        self._emit({"type": "warn", "text": text})

    def info(self, text: str) -> None:
        self._emit({"type": "info", "text": text})

    def fail(self, title: str, lines: list[str]) -> None:
        self._emit({"type": "fail", "title": title, "lines": list(lines)})

    def choose(self, question: str, options: list[tuple[str, str, str]], default: str) -> str:
        opts = [{"key": k, "label": label, "note": note} for k, label, note in options]
        return self._ask("choose", question, default, tuple(k for k, _, _ in options), opts)

    def ask(self, question: str, default: str = "") -> str:
        return self._ask("ask", question, default)

    def confirm(self, question: str, default: bool) -> bool:
        return self._ask("confirm", question, default)

    def _ask(self, kind: str, question: str, default: Any, keys: tuple[str, ...] = (), options: list[dict[str, str]] | None = None) -> Any:
        ev = self._emit({"type": kind, "question": question, "default": default, "options": options or []})
        q = _Question(kind, keys)
        with self._cond:
            self._pending[ev["id"]] = q
        if not q.done.wait(self.answer_timeout):
            with self._cond:
                timed_out = not q.answered
                if timed_out:
                    self._pending.pop(ev["id"], None)
            if timed_out:
                log.warning("setup question %s got no answer; the setup stopped", ev["id"])
                self._emit({"type": "resolved", "id": ev["id"]})
                self.fail(i18n.t("Setup stopped", lang=self.lang),
                          [i18n.t("No answer for {minutes} minutes, so setup stopped. What you answered is kept. Run `{command}` to finish.",
                                  lang=self.lang, minutes=int(self.answer_timeout // 60), command="kelo setup")])
                raise SetupTimedOut(ev["id"])
        return q.value

    def live(self, title: str, run: Callable[[Callable[[str], None]], int]) -> int:
        from .wizard import clean_line

        started = time.monotonic()
        self._emit({"type": "live", "title": title})

        def on_line(line: str) -> None:
            if text := clean_line(line, 300):
                self._emit({"type": "log", "text": text})

        code = run(on_line)
        secs = int(time.monotonic() - started)
        self._emit({"type": "live_end", "title": title, "code": code})
        if code == 0:
            self.ok(f"{title}  [{secs // 60}:{secs % 60:02d}]")
        return code

    def qr(self, link: str) -> None:
        from .connect import qr_matrix

        rows = ["".join("1" if dark else "0" for dark in row) for row in qr_matrix(link)]
        self._emit({"type": "qr", "link": link, "matrix": rows})

    def summary(self, url: str, commands: list[tuple[str, str]], note: str) -> None:
        self._emit({"type": "summary", "url": url, "note": note,
                    "commands": [{"command": c, "what": w} for c, w in commands]})


class _Handler(BaseHTTPRequestHandler):
    server: SetupServer
    server_version = "KeloSetup"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:    # nothing is logged: no paths, tokens or answers
        return

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"{BIND}:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def _token_ok(self) -> bool:
        given = (self.headers.get(TOKEN_HEADER) or "").encode()
        return secrets.compare_digest(given, self.server.token.encode())

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: Any) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _plain(self, code: int, text: str) -> None:
        self._send(code, text.encode(), "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._plain(403, "forbidden")
        path, _, query = self.path.partition("?")
        if path == "/":                          # the page holds no secret; it needs the token to ask for anything
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/events":
            if not self._token_ok():
                return self._plain(403, "forbidden")
            raw = parse_qs(query).get("after", ["0"])[0]
            if not raw.isdigit():
                return self._plain(400, "bad after")
            return self._json(200, self.server.ui.events(int(raw)))
        self._plain(404, "not found")

    def do_POST(self) -> None:
        if not self._host_ok() or not self._token_ok():
            return self._plain(403, "forbidden")
        if self.path.partition("?")[0] != "/answer":
            return self._plain(404, "not found")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._plain(400, "bad length")
        if not 0 <= length <= MAX_BODY:
            return self._plain(413, "too large")
        try:
            data = json.loads(self.rfile.read(length) or b"null")
            qid, value = data["id"], data["value"]
        except (ValueError, KeyError, TypeError):
            return self._plain(400, "bad answer")
        if not isinstance(qid, int) or isinstance(qid, bool):
            return self._plain(400, "bad id")
        try:
            self.server.ui.answer(qid, value)
        except LookupError:
            return self._plain(409, "no question is waiting for an answer")
        except ValueError:
            return self._plain(400, "bad value")
        self._json(200, {"ok": True})


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, ui: WebUI, token: str, *, port: int, grace: float, max_age: float):
        super().__init__((BIND, port), _Handler)
        self.ui = ui
        self.token = token
        self.grace = grace
        self.max_age = max_age
        self.exit_code: int | None = None        # the wizard's exit code once its thread is done
        self.halt = threading.Event()            # set by stop(): close now
        self.closed = threading.Event()          # set when the server has stopped

    def stop(self) -> None:
        self.halt.set()

    def wait(self) -> int:
        """Blocks until the server has stopped; the wizard's exit code (1 when it did not finish)."""
        self.closed.wait()
        return 1 if self.exit_code is None else self.exit_code


def _drive(run_wizard: Callable[[WebUI], int], srv: SetupServer) -> None:
    code = 1
    try:
        code = run_wizard(srv.ui)
    except SetupTimedOut:
        pass                                     # the page already got the reason (a fail event); the run ends here
    except Exception:  # noqa: BLE001 - the page must still get a last word
        log.exception("the guided setup stopped with an error")
    if not srv.ui.finished:
        lang = srv.ui.lang
        srv.ui.fail(i18n.t("Setup stopped", lang=lang),
                    [i18n.t("Something went wrong. The details are in the terminal.", lang=lang)])
        code = 1
    srv.exit_code = code


def _watch(srv: SetupServer) -> None:
    started = time.monotonic()
    while not srv.halt.wait(0.5):
        now = time.monotonic()
        delivered = srv.ui.delivered_at
        if (delivered is not None and now >= delivered + srv.grace) or now - started >= srv.max_age:
            break
    srv.shutdown()
    srv.server_close()
    srv.closed.set()


def serve(run_wizard: Callable[[WebUI], int], *, lang: str, host: str = BIND, port: int = 0,
          grace: float | None = None, max_age: float | None = None, answer_timeout: float | None = None) -> tuple[str, SetupServer]:
    """Starts the page and the wizard (run_wizard(ui) -> exit code, in a thread). Returns (the URL with the token, the server).
    The timings default to the module's GRACE, MAX_AGE and ANSWER_TIMEOUT (read when called, so tests can shorten them)."""
    if host != BIND:
        raise ValueError("the setup page listens on 127.0.0.1 only")
    token = secrets.token_urlsafe(24)
    ui = WebUI(lang, answer_timeout=ANSWER_TIMEOUT if answer_timeout is None else answer_timeout)
    srv = SetupServer(ui, token, port=port, grace=GRACE if grace is None else grace,
                      max_age=MAX_AGE if max_age is None else max_age)
    url = f"http://{BIND}:{srv.server_port}/#t={token}"
    threading.Thread(target=_drive, args=(run_wizard, srv), name="kelo-setup-wizard", daemon=True).start()
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, name="kelo-setup-http", daemon=True).start()
    threading.Thread(target=_watch, args=(srv,), name="kelo-setup-stop", daemon=True).start()
    return url, srv


def write_page(url: str) -> Path:
    """A small page that sends the browser to the setup link. The file is created 0600 in the account's temp folder, so only
    this account can read the token in it. Opening the file (not the link) keeps the token off every command line."""
    fd, name = tempfile.mkstemp(prefix="kelo-setup-", suffix=".html")
    target = html.escape(url, quote=True)
    body = ('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            f'<meta http-equiv="refresh" content="0; url={target}"><title>Kelo</title><a href="{target}">Kelo</a>')
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return Path(name)


def run_web(env_file: Path, *, start: bool = True, browser: str | None = None, clis: list[str] | None = None,
            profile: str | None = None, release: bool = False, open_browser: bool = True, page_file: bool = False,
            out: Callable[[str], None] = print, opener: Callable[[str], Any] = webbrowser.open, host: Any = None,
            port: int = 0) -> int:
    """`assistant setup --web`: the guided setup in the browser. Exit code = the wizard's.
    The browser is always started with the page file's address. `page_file` prints that file's path instead of the link
    (the macOS installer opens it; the link never reaches its log or its command line). Otherwise the link is printed."""
    from .wizard import Wizard, detect_lang

    def run_wizard(ui: WebUI) -> int:
        return Wizard(env_file, ui, start=start, browser=browser, clis=clis, profile=profile, release=release, host=host)()

    url, srv = serve(run_wizard, lang=detect_lang(), port=port)
    page = write_page(url)
    try:
        out(str(page) if page_file else url)
        if open_browser:
            opener(page.as_uri())
        try:
            return srv.wait()
        except KeyboardInterrupt:
            srv.stop()
            return 130
    finally:
        page.unlink(missing_ok=True)
