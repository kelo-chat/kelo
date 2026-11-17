import pytest


@pytest.fixture(autouse=True)
def no_real_cli_probes(monkeypatch):
    """Server tests must never spawn the user's real `claude`/`agy`."""
    from assistant.providers import cli_manage

    async def fake_status(name):
        return {"installed": False, "installable": False, "logged_in": None, "hint": ""}

    monkeypatch.setattr(cli_manage, "cli_status", fake_status)

    async def no_windows(name, *, local=False):
        return []   # no `claude -p /usage` / `agy -p /usage` (model_limits.QuotaGuard asks after a limit error); a test sets its own

    no_windows.real = cli_manage.cli_usage   # the real one, for the tests of cli_usage itself (real_cli_usage)
    monkeypatch.setattr(cli_manage, "cli_usage", no_windows)

    async def no_real_signout(*a, **kw):
        raise AssertionError("tests must never run a real CLI sign-out")

    monkeypatch.setattr(cli_manage, "_run_cli", no_real_signout)


@pytest.fixture
def real_cli_usage(monkeypatch, no_real_cli_probes):
    """The real cli_manage.cli_usage back (a test of it points AGY_COMMAND at a fake script)."""
    from assistant.providers import cli_manage
    monkeypatch.setattr(cli_manage, "cli_usage", cli_manage.cli_usage.real)


@pytest.fixture(autouse=True)
def no_leftover_endpoints():
    """Endpoints typed in one test's app (and OpenRouter's per-key notes) must not leak into the next test."""
    from assistant import balances
    from assistant.providers.registry import clear_stored_base_urls
    yield
    clear_stored_base_urls()
    balances._no_credits.clear()


@pytest.fixture(autouse=True)
def no_leftover_language():
    """The owner's language a test's app stored (i18n.set_fallback) must not decide the language of the next test."""
    from assistant import i18n
    i18n.set_fallback(None)
    yield
    i18n.set_fallback(None)


@pytest.fixture(autouse=True)
def no_release_lookups():
    """No test asks npm / Google for the latest Codex, Claude Code or agy version (the pinned Codex version is used), and no model
    list a test fetched (or its last-good copy, backoff and thinking levels) is seen by the next test."""
    from assistant import effort, models
    from assistant.providers import releases

    async def offline(what):
        return None

    releases.set_fetcher(offline)
    yield
    releases.set_fetcher(None)
    for d in (models._cache, models._labels, models._good, models._backoff, models._shrunk, effort._listed):
        d.clear()


@pytest.fixture(autouse=True)
def plain_browser_input():
    """Browser tests click and fill instantly (the plain Playwright calls), so they stay fast and repeatable. server/tests/test_humanize.py
    turns human-paced input on with a seed and no waiting."""
    from assistant.tools import humanize
    humanize.configure(reset=True, enabled=False)
    yield
    humanize.configure(reset=True)


@pytest.fixture(autouse=True)
def short_entry_wait(monkeypatch):
    """A followed reply waits up to 10 minutes for a fill-in card it asked for (server.ENTRY_WAIT_SECONDS); a test whose script asks
    for one and never answers would sit there. 2 s still lets a test answer within the wait; tests about the wait set their own."""
    monkeypatch.setattr("assistant.server.ENTRY_WAIT_SECONDS", 2.0)


@pytest.fixture(autouse=True)
def keyguard_on_for_auto_pay(request, monkeypatch):
    """Auto-pay is allowed only while Keyguard is on (wallet.py keyguard_off). Most wallet and purchase tests are about auto-pay
    itself, so Keyguard counts as on for them; a test marked `keyguard_real` sees the real state."""
    if request.node.get_closest_marker("keyguard_real"):
        return
    from assistant.wallet import Wallet
    monkeypatch.setattr(Wallet, "keyguard_on", lambda self: True)


@pytest.fixture(autouse=True)
def _no_agy_spawn_gap(monkeypatch):
    """agy starts wait SPAWN_GAP_SECONDS for each other in production (agy_pool.guard_token); tests start fake agy only."""
    from assistant.providers import agy_pool
    monkeypatch.setattr(agy_pool, "SPAWN_GAP_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def no_background_task_names(request, monkeypatch):
    """A scripted fake model counts its calls: the background task-name call (task_names.py) would use one up. Only
    test_task_names.py runs the namer for real."""
    if request.module.__name__ != "test_task_names":
        from assistant.task_names import TaskNamer
        monkeypatch.setattr(TaskNamer, "name_later", lambda self, *a, **kw: False)


@pytest.fixture(autouse=True)
def no_app_list_lookups(monkeypatch):
    """No test reads kelo.chat/download/latest.json (app_updates.py); a test that wants it passes app_list_fetcher."""
    from assistant import app_updates

    async def offline():
        raise OSError("offline in tests")

    monkeypatch.setattr(app_updates, "fetch_latest", offline)


@pytest.fixture(autouse=True)
def no_promise_check(monkeypatch):
    """The promise check (scheduled_runs.py) is one more model request after a chat reply: off, so a test's scripted model keeps its
    order; the tests of the check turn it on."""
    from assistant import scheduled_runs
    monkeypatch.setattr(scheduled_runs, "PROMISE_CHECK", False)


@pytest.fixture(autouse=True)
def a_stand_in_step_judge(monkeypatch):
    """A browser or phone session built in a test has a fake step judge that answers by the words of the test pages (fake_judge.py), the
    way the owner's model does in life. A test that wants another verdict, or none, sets session.step_judge itself."""
    from assistant.android.tools import AndroidSession
    from assistant.tools.browser import BrowserSession
    from fake_judge import FakeJudge, by_page_words

    for cls in (BrowserSession, AndroidSession):
        real = cls.__init__

        def init(self, *a, __real=real, **kw):
            __real(self, *a, **kw)
            self.step_judge = FakeJudge(by_page_words)
        monkeypatch.setattr(cls, "__init__", init)


@pytest.fixture(autouse=True)
def weather_dns_is_fake(monkeypatch):
    """The weather's alternate route asks this machine's DNS for the normal answer and remembers the other servers it found. No test
    looks up a real name, and no test sees the answers another test kept: the local answer is the North American server (the owner's
    network on 2026-11-17), and the cache starts empty."""
    from assistant import weather

    async def local(host):
        return {"51.81.167.44"}
    monkeypatch.setattr(weather, "_local_ips", local)
    weather._ALT.clear()
    weather._NORMAL_DOWN_UNTIL.clear()
    yield
    weather._ALT.clear()
    weather._NORMAL_DOWN_UNTIL.clear()
