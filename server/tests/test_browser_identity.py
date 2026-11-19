"""Chrome identity for Chromium builds (assistant/browser_identity.py)."""
import asyncio
import json

import pytest

from assistant import browser_identity as bi


def test_brands_match_real_google_chrome_157():
    # what Google Chrome 157.0.8103.38 (linux/arm64) reported in navigator.userAgentData and Sec-CH-UA-Full-Version-List
    brands, full = bi.brand_lists("157.0.8103.38")
    assert brands == [{"brand": "Not-A.Brand", "version": "99"}, {"brand": "Google Chrome", "version": "157"},
                      {"brand": "Chromium", "version": "157"}]
    assert full == [{"brand": "Not-A.Brand", "version": "99.0.0.0"}, {"brand": "Google Chrome", "version": "157.0.8103.38"},
                    {"brand": "Chromium", "version": "157.0.8103.38"}]


def test_grease_follows_chromium_rules():
    brands, _ = bi.brand_lists("156.0.8078.79")        # 156: seed 156 -> ':' and '-', version 8, order (0,1,2)
    assert brands == [{"brand": "Not:A-Brand", "version": "8"}, {"brand": "Chromium", "version": "156"},
                      {"brand": "Google Chrome", "version": "156"}]
    for major in range(120, 200):
        names = [b["brand"] for b in bi.brand_lists(f"{major}.0.1.2")[0]]
        assert sorted(names[i] for i in range(3) if not names[i].startswith("Not")) == ["Chromium", "Google Chrome"]


def test_user_agent_and_metadata():
    # Chrome OS, as Google Chrome 158 on a Chromebook sends it (the reduced platform is one constant on every Chromebook, x86_64 too)
    p = bi.override("158.0.8127.36", machine="aarch64")
    assert p["userAgent"] == "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    assert "Linux" not in p["userAgent"] and "Chromium" not in p["userAgent"]
    m = p["userAgentMetadata"]
    assert (m["platform"], m["architecture"], m["bitness"], m["model"], m["mobile"], m["wow64"]) == \
        ("Chrome OS", "arm", "64", "", False, False)
    assert m["platformVersion"] == bi.CHROME_OS_PLATFORM_VERSIONS[158]
    assert m["formFactors"] == ["Desktop"] and m["fullVersion"] == "158.0.8127.36"
    assert "platform" not in p                       # navigator.platform stays what Chrome OS reports: Linux x86_64
    assert bi.machine_arch("x86_64") == ("x86", "64")
    assert bi.machine_arch("armv7l") == ("arm", "32")


def test_the_chrome_os_platform_version_table_has_the_pinned_milestone():
    # docker/server.Dockerfile pins Google Chrome; the table row for its milestone is the Chrome OS platform version that shipped with it
    import re
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parents[2] / "docker/server.Dockerfile").read_text()
    pinned = int(re.search(r"^ARG CHROME_VERSION=(\d+)\.", dockerfile, re.M).group(1))
    assert pinned in bi.CHROME_OS_PLATFORM_VERSIONS, f"no Chrome OS platform version for Chrome {pinned}: add its row to CHROME_OS_PLATFORM_VERSIONS"
    assert re.fullmatch(r"\d{5}\.\d+\.\d+", bi.CHROME_OS_PLATFORM_VERSIONS[pinned])


def test_the_dockerfile_table_and_the_module_agree():
    import re
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parents[2] / "docker/server.Dockerfile").read_text()
    rows = {int(m.group(1)): m.group(2) for m in re.finditer(r"^#\s+(\d+)\s+(\d{5}\.\d+\.\d+)\s*$", dockerfile, re.M)}
    assert rows == bi.CHROME_OS_PLATFORM_VERSIONS


def test_a_milestone_without_a_row_fails_instead_of_inventing_a_version():
    with pytest.raises(KeyError):
        bi.override("999.0.0.0")


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        self.ident._wait.pop(msg["id"]).set_result({})


def test_attached_target_gets_override_children_and_is_resumed():
    async def go():
        ws = FakeWS()
        ident = bi.Identity(ws, bi.override("158.0.8127.36", machine="x86_64"))
        ws.ident = ident
        await ident.attached({"sessionId": "S", "targetInfo": {"type": "page"}, "waitingForDebugger": True})
        await ident.attached({"sessionId": "W", "targetInfo": {"type": "service_worker"}, "waitingForDebugger": True})
        return [(m["method"], m.get("sessionId")) for m in ws.sent]

    assert asyncio.run(go()) == [
        ("Emulation.setUserAgentOverride", "S"), ("Target.setAutoAttach", "S"), ("Runtime.runIfWaitingForDebugger", "S"),
        ("Network.setUserAgentOverride", "W"), ("Target.setAutoAttach", "W"), ("Runtime.runIfWaitingForDebugger", "W"),
    ]


def test_a_milestone_without_a_row_keeps_the_linux_identity(monkeypatch):
    p = bi.identity_for("999.0.0.0", machine="x86_64")
    assert p["userAgent"] == "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/999.0.0.0 Safari/537.36"
    assert (p["userAgentMetadata"]["platform"], p["userAgentMetadata"]["platformVersion"]) == ("Linux", "")
    monkeypatch.setattr(bi, "STATED_OS", "chromeos")       # the switch on, but no row for 999: still Linux, nothing invented
    assert bi.identity_for("999.0.0.0", machine="x86_64") == p


def test_the_default_states_the_machine_own_os_linux():
    assert bi.STATED_OS == "linux"
    p = bi.identity_for("158.0.8127.36", machine="x86_64")
    assert p["userAgent"] == "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    m = p["userAgentMetadata"]
    assert (m["platform"], m["platformVersion"], m["architecture"], m["bitness"], m["mobile"]) == ("Linux", "", "x86", "64", False)
    assert m["fullVersion"] == "158.0.8127.36" and m["formFactors"] == ["Desktop"] and m["model"] == ""
    assert bi.user_agent(158) == p["userAgent"]
    assert "CrOS" not in p["userAgent"] and "Chrome OS" not in json.dumps(p)
    assert "platform" not in p                           # navigator.platform is the machine's own in every case


def test_the_chromeos_switch_states_chrome_os_where_the_milestone_has_a_row(monkeypatch):
    monkeypatch.setattr(bi, "STATED_OS", "chromeos")
    p = bi.identity_for("158.0.8127.36", machine="x86_64")
    assert p["userAgent"] == "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    assert (p["userAgentMetadata"]["platform"], p["userAgentMetadata"]["platformVersion"]) == ("Chrome OS", bi.CHROME_OS_PLATFORM_VERSIONS[158])
    assert bi.user_agent(158) == p["userAgent"]
    assert bi.identity_for("158.0.8127.36") == bi.override("158.0.8127.36")


def test_a_stated_os_other_than_linux_or_chromeos_is_refused(monkeypatch):
    monkeypatch.setattr(bi, "STATED_OS", "windows")
    with pytest.raises(ValueError):
        bi.identity_for("158.0.8127.36")


def test_the_module_cli_reports_the_switch_and_the_user_agent():
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    out = subprocess.run([sys.executable, "-m", "assistant.browser_identity", "--stated-os"], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout.strip() == "linux", out
    ua = subprocess.run([sys.executable, "-m", "assistant.browser_identity", "--user-agent", "158.0.8127.36"], capture_output=True, text=True, env=env)
    assert ua.stdout.strip() == "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36", ua


def test_the_attached_page_gets_the_stated_linux_identity_by_default():
    async def go():
        ws = FakeWS()
        ident = bi.Identity(ws, bi.identity_for("158.0.8127.36", machine="x86_64"))
        ws.ident = ident
        await ident.attached({"sessionId": "S", "targetInfo": {"type": "page"}, "waitingForDebugger": True})
        return ws.sent[0]["params"]

    p = asyncio.run(go())
    assert p["userAgent"].startswith("Mozilla/5.0 (X11; Linux x86_64)")
    assert p["userAgentMetadata"]["platform"] == "Linux" and p["userAgentMetadata"]["platformVersion"] == ""
