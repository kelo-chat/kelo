"""Chrome identity for Chromium builds (assistant/browser_identity.py)."""
import asyncio
import json

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
    p = bi.override("157.0.8103.38", machine="aarch64")
    assert p["userAgent"] == "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
    assert "Chromium" not in p["userAgent"]
    m = p["userAgentMetadata"]
    assert (m["platform"], m["platformVersion"], m["architecture"], m["bitness"], m["model"], m["mobile"], m["wow64"]) == \
        ("Linux", "", "arm", "64", "", False, False)
    assert m["formFactors"] == ["Desktop"] and m["fullVersion"] == "157.0.8103.38"
    assert bi.machine_arch("x86_64") == ("x86", "64")
    assert bi.machine_arch("armv7l") == ("arm", "32")


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
        ident = bi.Identity(ws, bi.override("157.0.8103.38", machine="x86_64"))
        ws.ident = ident
        await ident.attached({"sessionId": "S", "targetInfo": {"type": "page"}, "waitingForDebugger": True})
        await ident.attached({"sessionId": "W", "targetInfo": {"type": "service_worker"}, "waitingForDebugger": True})
        return [(m["method"], m.get("sessionId")) for m in ws.sent]

    assert asyncio.run(go()) == [
        ("Emulation.setUserAgentOverride", "S"), ("Target.setAutoAttach", "S"), ("Runtime.runIfWaitingForDebugger", "S"),
        ("Network.setUserAgentOverride", "W"), ("Target.setAutoAttach", "W"), ("Runtime.runIfWaitingForDebugger", "W"),
    ]
