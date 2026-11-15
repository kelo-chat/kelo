"""The guided `assistant setup`: its decisions, with no real Docker, network or terminal (fake runner, fake UI)."""

import io
import types

import pytest

from assistant import wizard
from assistant.cli import read_env
from assistant.wizard import (
    GB,
    Machine,
    Wizard,
    check_docker,
    detect_lang,
    merge_profiles,
    shortfalls,
    suggested_tier,
    tier,
    translate,
)

KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


class FakeUI:
    """Answers from a script, everything shown kept in `shown`. Raises if the wizard asks something the test did not expect."""

    def __init__(self, *answers, lang="en"):
        self.answers = list(answers)
        self.lang = lang
        self.shown: list[str] = []
        self.asked: list[str] = []
        self.qr_links: list[str] = []
        self.live_titles: list[str] = []

    def _next(self, kind, question):
        self.asked.append(f"{kind}: {question}")
        if not self.answers:
            raise AssertionError(f"unexpected question: {question}")
        return self.answers.pop(0)

    def banner(self, rerun): self.shown.append(f"banner rerun={rerun}")
    def step(self, n, title): self.shown.append(f"step {n} {title}")
    def ok(self, text): self.shown.append("ok " + text)
    def warn(self, text): self.shown.append("warn " + text)
    def info(self, text): self.shown.append("info " + text)
    def fail(self, title, lines): self.shown.append("fail " + title + " | " + " ".join(lines))

    def choose(self, question, options, default):
        self.shown.append("choose " + question + " " + repr(options) + f" default={default}")
        got = self._next("choose", question)
        return default if got == "" else got

    def ask(self, question, default=""):
        return self._next("ask", question) or default


    def confirm(self, question, default):
        got = self._next("confirm", question)
        return default if got == "" else got

    def live(self, title, run):
        self.live_titles.append(title)
        return run(lambda line: self.shown.append("log " + line))

    def qr(self, link): self.qr_links.append(link)
    def summary(self, url, commands, note): self.shown.append(f"summary {url} | {note}")


class Docker:
    """Stands in for subprocess.run and for the streamed `docker compose up`."""

    def __init__(self, *, cpus=8, mem=16 * GB, running=True, compose=True, denied=False, up_code=0):
        self.cpus, self.mem, self.running, self.compose, self.denied, self.up_code = cpus, mem, running, compose, denied, up_code
        self.calls: list[list[str]] = []
        self.streamed: list[list[str]] = []

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["docker", "version"]:
            ok.stdout = "28.4.0\n"
        elif cmd[:3] == ["docker", "compose", "version"]:
            if not self.compose:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="'compose' is not a docker command")
            ok.stdout = "2.39.2\n"
        elif cmd[:2] == ["docker", "info"]:
            if self.denied:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="permission denied while trying to connect to the Docker daemon socket")
            if not self.running:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon")
            ok.stdout = f"{self.cpus} {self.mem}\n"
        return ok

    def stream(self, cmd, cwd, on_line):
        self.streamed.append(cmd)
        for line in ("#1 [server internal] load build definition", "\x1b[32m#5 DONE 0.4s\x1b[0m", " Container kelo-server  Started"):
            on_line(line + "\n")
        return self.up_code


class Http:
    """The running server and the Kelo address service, answered locally."""

    def __init__(self, healthy=True, taken=()):
        self.healthy, self.taken = healthy, set(taken)
        self.posts: list[tuple[str, dict]] = []

    def get(self, url, **kw):
        assert url.startswith("http://127.0.0.1:"), url
        return types.SimpleNamespace(status_code=200 if self.healthy else 503)

    def post(self, url, json=None, **kw):
        self.posts.append((url, json))
        if url.endswith("/broker/tunnel/claim"):
            if json["name"] in self.taken:
                return types.SimpleNamespace(status_code=409, json=lambda: {"detail": "that name is taken"}, text="taken")
            return types.SimpleNamespace(status_code=200, json=lambda: {"hostname": f"{json['name']}.kelo.chat", "tunnel_token": "t" * 40})
        if url.endswith("/api/devices/pair"):
            return types.SimpleNamespace(status_code=200, json=lambda: {"link": "https://alice.kelo.chat/pair#one-time"})
        raise AssertionError(url)


def make(tmp_path, ui, docker=None, http=None, **kw):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    docker = docker or Docker()
    http = http or Http()
    w = Wizard(tmp_path / ".env", ui, run=docker.run, stream=docker.stream, get=http.get, post=http.post,
               which=lambda name: f"/usr/bin/{name}", system="Linux", free_disk=lambda p: 200 * GB, sleep=lambda s: None,
               health_timeout=10, **kw)
    return w, docker, http


# ------------------------------------------------------------------------------------------------------------- pure parts
def test_the_terminal_speaks_english_unless_kelo_lang_says_otherwise():
    none = lambda: ""
    assert detect_lang({"LANG": "ko_KR.UTF-8"}, windows=none) == "en"            # the system language is not followed
    assert detect_lang({}, windows=lambda: "ko_KR") == "en"
    assert detect_lang({"LANG": "ko_KR.UTF-8", "KELO_LANG": "ko"}, windows=none) == "ko"
    assert detect_lang({"KELO_LANG": "zh_TW"}) == "zh-Hant"
    assert detect_lang({"KELO_LANG": "pt_PT"}) == "pt-BR"
    assert detect_lang({"KELO_LANG": "xx"}) == "en"


def test_every_wizard_template_is_translated_in_every_language():
    import ast
    import json
    from pathlib import Path

    from assistant import i18n
    tree = ast.parse(Path(wizard.__file__).read_text(encoding="utf-8"))
    used = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str) and getattr(n.func, "attr", getattr(n.func, "id", "")) in ("t", "N_")}
    assert len(used) > 60
    for f in sorted(i18n.LOCALES.glob("*.json")):
        have = json.loads(f.read_text(encoding="utf-8"))
        assert not [k for k in used if k not in have], f.name
    assert translate("Kelo address (offered for now)", "ko") == "Kelo 주소 (현재 한정 제공)"   # "Default address" until d376eaa


def test_sizes_are_checked_against_this_computer():
    small = Machine(cpus=2, ram=int(3.8 * GB), disk_free=15 * GB)
    assert suggested_tier(small) == "minimum"
    assert {w for w, _, _ in shortfalls(tier("recommended"), small)} == {"RAM", "CPUs", "free disk"}
    assert shortfalls(tier("minimum"), small) == []
    assert shortfalls(tier("unlimited"), Machine(1, 1, 1)) == []
    assert suggested_tier(Machine(8, 16 * GB, 200 * GB)) == "recommended"
    assert shortfalls(tier("recommended"), Machine(4, int(3.9 * GB), 50 * GB)) == []   # Docker shows a bit less than installed
    assert shortfalls(tier("minimum"), Machine(None, None, None)) == []              # unknown is not judged


def test_compose_profiles_keep_what_is_not_a_cli():
    assert merge_profiles("kelo,claude", ["agy"]) == "kelo,agy"
    assert merge_profiles("", ["claude", "agy"]) == "claude,agy"
    assert merge_profiles("cloudflare,agy", []) == "cloudflare"


def test_docker_check_tells_missing_stopped_and_denied_apart():
    assert not check_docker(Docker().run, which=lambda n: None).installed
    assert check_docker(Docker().run, which=lambda n: "/x").ok
    assert not check_docker(Docker(compose=False).run, which=lambda n: "/x").compose
    stopped = check_docker(Docker(running=False).run, which=lambda n: "/x")
    assert stopped.installed and not stopped.running
    denied = check_docker(Docker(denied=True).run, which=lambda n: "/x")
    assert denied.running and not denied.permission and not denied.ok


# ------------------------------------------------------------------------------------------------------------- whole runs
def test_first_run_end_to_end(tmp_path):
    ui = FakeUI("",            # size: the suggestion (recommended on this 8 CPU / 16 GB computer)
                "1", "alice",  # default address, name
                True)          # sign a phone in
    w, docker, _http = make(tmp_path, ui)
    assert w() == 0
    env = tmp_path / ".env"
    assert read_env(env, "ASSISTANT_PROVIDER") == "" and read_env(env, "ASSISTANT_MODEL") == ""   # chosen in the app
    assert len(read_env(env, "ASSISTANT_TOKEN")) >= 32 and read_env(env, "ASSISTANT_SECRET_KEY")
    assert read_env(env, "SERVER_MEMORY") == str(1 * GB)                 # recommended sizes were saved
    assert read_env(env, "CONNECT_MODE") == "kelo" and read_env(env, "CONNECT_URL") == "https://alice.kelo.chat"
    assert "kelo" in read_env(env, "COMPOSE_PROFILES").split(",")
    assert docker.streamed and docker.streamed[0][-4:] == ["up", "-d", "--build", "--remove-orphans"]
    assert ui.qr_links == ["https://alice.kelo.chat/pair#one-time"]
    assert [s.split(" ", 2)[1] for s in ui.shown if s.startswith("step ")] == ["1", "2", "3", "4"]
    everything = "\n".join(ui.shown + ui.asked)
    assert read_env(env, "ASSISTANT_TOKEN") not in everything and "t" * 40 not in everything
    assert not any("key" in q.lower() or "model" in q.lower() for q in ui.asked)      # no model or key questions
    assert any(s.startswith("summary ") and "AI model in the Kelo app" in s for s in ui.shown)
    assert "\x1b" not in everything.split("log ", 1)[0]


def test_docker_missing_shows_one_screen_and_changes_nothing(tmp_path):
    ui = FakeUI()
    w, _docker, _ = make(tmp_path, ui)
    w.which = lambda name: None
    w.system = "Darwin"
    assert w() == 1
    assert not (tmp_path / ".env").exists()
    fail = next(s for s in ui.shown if s.startswith("fail "))
    assert "Docker is not installed" in fail and "docs.docker.com/desktop" in fail
    assert ui.asked == []


def test_docker_not_running_on_linux_says_how_to_start_it_in_korean(tmp_path):
    ui = FakeUI(lang="ko")
    w, _, _ = make(tmp_path, ui, Docker(running=False))
    assert w() == 1
    fail = next(s for s in ui.shown if s.startswith("fail "))
    assert "systemctl start docker" in fail and "is installed but not running" not in fail


def test_small_computer_is_warned_and_still_allowed(tmp_path):
    ui = FakeUI("1", "3")                    # recommended anyway, no address
    w, _, _ = make(tmp_path, ui, Docker(cpus=2, mem=3 * GB))
    assert w() == 0
    choose = next(s for s in ui.shown if s.startswith("choose How much"))
    assert choose.endswith("default=2")       # minimum was suggested
    assert "('1', 'Recommended', 'RAM 4 GB · CPU 4 · disk 20 GB')" in choose and "('2', 'Minimum', 'RAM 2 GB · CPU 2 · disk 10 GB')" in choose
    assert "('3', 'No limit', 'everything this computer has (RAM 3 GB · CPU 2 · 200 GB free)')" in choose
    warn = next(s for s in ui.shown if s.startswith("warn "))
    assert "less than Recommended asks for" in warn and "RAM 3 GB of 4 GB" in warn and "CPUs 2 of 4" in warn
    assert ui.qr_links == []                  # no outside address: no QR for a phone that could not reach it


def test_model_settings_already_in_env_are_left_alone(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"ASSISTANT_PROVIDER=openrouter\nASSISTANT_MODEL=some/model\nOPENROUTER_API_KEY={KEY}\n")
    ui = FakeUI("", "3")
    w, _, _ = make(tmp_path, ui)
    assert w() == 0
    assert read_env(env, "ASSISTANT_PROVIDER") == "openrouter" and read_env(env, "ASSISTANT_MODEL") == "some/model"
    assert read_env(env, "OPENROUTER_API_KEY") == KEY
    assert KEY not in "\n".join(ui.shown)


def test_rerun_keeps_choices_and_updates(tmp_path):
    first = FakeUI("", "3")                                # recommended, no address
    w, _, _ = make(tmp_path, first, clis=["claude"])        # `assistant setup --cli claude`: only from the command line
    assert w() == 0
    env = tmp_path / ".env"
    token = read_env(env, "ASSISTANT_TOKEN")
    assert read_env(env, "COMPOSE_PROFILES") == "claude"
    assert not any("subscription" in q.lower() for q in first.asked)
    again = FakeUI("", "3")                                # Enter, not now
    w2, docker, _ = make(tmp_path, again)
    assert w2() == 0
    assert "banner rerun=True" in again.shown
    assert read_env(env, "ASSISTANT_TOKEN") == token
    assert read_env(env, "COMPOSE_PROFILES") == "claude"   # kept as it was
    assert again.live_titles == ["Updating and restarting Kelo"]
    assert docker.streamed


def test_taken_name_can_be_retried(tmp_path):
    ui = FakeUI("", "1", "bob", True, "alice", False)
    w, _, _http = make(tmp_path, ui, http=Http(taken={"bob"}))
    assert w() == 0
    assert any("that name is taken" in s for s in ui.shown)
    assert read_env(tmp_path / ".env", "KELO_NAME") == "alice"


def test_compose_failure_stops_before_the_phone_step(tmp_path):
    ui = FakeUI("")
    w, _, http = make(tmp_path, ui, Docker(up_code=17))
    assert w() == 1
    assert any(s.startswith("fail ") and "exit 17" in s for s in ui.shown)
    assert not any(s.startswith("step 4") for s in ui.shown) and http.posts == []


def test_no_start_saves_answers_without_docker_compose_up(tmp_path):
    ui = FakeUI("3", "2")
    w, docker, _ = make(tmp_path, ui, start=False)
    assert w() == 0
    assert docker.streamed == [] and not any(c[:3] == ["docker", "compose", "-f"] and "up" in c for c in docker.calls)
    assert read_env(tmp_path / ".env", "SERVER_MEMORY") in ("", "0")          # no limit = nothing capped
    assert any("assistant connect tailscale" in s for s in ui.shown)


def test_ctrl_c_is_a_calm_stop(tmp_path):
    class Interrupt(FakeUI):
        def ask(self, question, default=""):
            raise KeyboardInterrupt
    ui = Interrupt("", "1")                                # size, default address, then Ctrl+C at the name
    w, _, _ = make(tmp_path, ui)
    assert w() == 130
    assert any("Stopped." in s for s in ui.shown)


# ------------------------------------------------------------------------------------------------------------- the look
def test_rich_ui_renders_without_colour(tmp_path, monkeypatch):
    from rich.console import Console
    monkeypatch.setenv("NO_COLOR", "1")
    buf = io.StringIO()
    console = Console(file=buf, width=100, no_color=True, force_terminal=False)
    answers = iter(["", "3"])                              # size, not now
    ui = wizard.RichUI("en", console=console, input_fn=lambda _: next(answers))
    w, _, _ = make(tmp_path, ui)
    assert w() == 0
    text = buf.getvalue()
    assert "1/4" in text and "4/4" in text and "✓" in text and "Kelo" in text
    assert "Choose the AI model in the Kelo app" in text
    assert "\x1b[" not in text


def test_mascot_and_qr_fit_a_phone_sized_terminal():
    lines = wizard.mascot_lines()
    assert len(lines) <= 8 and all(len(l) <= 16 for l in lines)
    from rich.console import Console
    buf = io.StringIO()
    ui = wizard.RichUI("en", console=Console(file=buf, width=50, color_system="truecolor", force_terminal=True))
    ui.qr("https://alice.kelo.chat/pair#" + "x" * 40)
    assert max(len(wizard.ANSI.sub("", l)) for l in buf.getvalue().splitlines()) <= 50
    wide = io.StringIO()
    ui = wizard.RichUI("en", console=Console(file=wide, width=200, color_system="truecolor", force_terminal=True))
    ui.qr("https://alice.kelo.chat/#pair=ABCDEFGHJKLMNP")
    art = [l for l in wide.getvalue().splitlines() if "▀" in l]
    assert 17 <= len(art) <= 19 and all(len(wizard.ANSI.sub("", l).strip()) <= 37 for l in art)   # compact even when there is room


def test_plain_and_yes_keep_the_old_setup(tmp_path, monkeypatch):
    from assistant import cli
    called = []
    monkeypatch.setattr(wizard, "run", lambda *a, **k: called.append(k) or 0)
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    env = tmp_path / ".env"
    assert cli.main(["setup", "--yes", "--env-file", str(env)]) == 0
    assert called == [] and read_env(env, "BROWSER") == "chromium"
    assert cli.main(["setup", "--env-file", str(env), "--no-start", "--profile", "minimum"]) == 0
    assert called == [{"start": False, "browser": None, "clis": None, "profile": "minimum"}]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_both_languages_run_through(tmp_path, lang):
    ui = FakeUI("", "3", lang=lang)
    w, _, _ = make(tmp_path, ui)
    assert w() == 0
    assert sum(1 for s in ui.shown if s.startswith("step ")) == 4
