"""Unit and integration tests for identity_check and IdentityRules.check_report."""

from __future__ import annotations

import platform
import pytest

from assistant.browser_devices import IdentityRules
from assistant.tools.identity_check import (
    ACH, CHECK_ORIGIN, FRAME_ORIGIN, FetchHandler, run_identity_check
)
from assistant.tools.browser import BrowserSession, browser_owner

FULL = "158.0.8127.36"
ARCH = "arm" if platform.machine().lower() in ("arm64", "aarch64") else "x86"


def make_desktop_context(**over):
    ctx = {
        "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36",
        "platform": "Linux x86_64",
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "languages": ["ko-KR", "ko", "en-US", "en"],
        "language": "ko-KR",
        "locale": "ko",
        "timezone": "Asia/Seoul",
        "webdriver": False,
        "uaData": {
            "brands": [
                {"brand": "Chromium", "version": "158"},
                {"brand": "Not.A/Brand", "version": "24"},
                {"brand": "Google Chrome", "version": "158"},
            ],
            "mobile": False,
            "platform": "Linux",
            "model": "",
            "platformVersion": "",
            "architecture": ARCH,
            "bitness": "64",
            "formFactors": ["Desktop"],
        },
        "maxTouchPoints": 0,
        "plugins": 5,
        "pdfViewerEnabled": True,
        "screen": [1440, 900],
        "dpr": 1.0,
        "innerWidth": 1440,
        "innerHeight": 900,
        "pointerFine": True,
        "pointerCoarse": False,
        "hoverHover": True,
        "hoverNone": False,
        "metaViewport": True,
        "nativeText": True,
        "cpuByteArch": ARCH,
    }
    ctx.update(over)
    return ctx


def test_the_desktop_view_states_linux_and_a_chrome_os_desktop_fails():
    from assistant.browser_identity import CHROME_OS_PLATFORM_VERSIONS
    assert CHROME_OS_PLATFORM_VERSIONS[158] == "16868.13.0"
    ctx = make_desktop_context()
    assert ctx["uaData"]["platform"] == "Linux" and ctx["uaData"]["platformVersion"] == ""
    chromeos = make_desktop_context(userAgent="Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36")
    chromeos["uaData"] = dict(ctx["uaData"], platform="Chrome OS", platformVersion="16868.13.0")
    fails, _ = IdentityRules.check_report({"page": chromeos, "iframe": chromeos, "worker": chromeos, "serviceWorker": chromeos},
                                          full_version=FULL, machine_arch=ARCH)
    assert any("desktop user agent" in f for f in fails) and any("client hints are not" in f for f in fails), fails


def test_the_desktop_view_states_chrome_os_with_the_switch_on(monkeypatch):
    from assistant import browser_identity
    monkeypatch.setattr(browser_identity, "STATED_OS", "chromeos")
    chromeos = make_desktop_context(userAgent="Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36")
    chromeos["uaData"] = dict(make_desktop_context()["uaData"], platform="Chrome OS", platformVersion="16868.13.0")
    fails, _ = IdentityRules.check_report({"page": chromeos, "iframe": chromeos, "worker": chromeos, "serviceWorker": chromeos},
                                          full_version=FULL, machine_arch=ARCH)
    assert fails == [], fails


def test_identity_rules_check_report_passes_on_complete_desktop_report():
    report = {
        "page": make_desktop_context(),
        "iframe": make_desktop_context(),
        "worker": make_desktop_context(),
        "serviceWorker": make_desktop_context(),
        "headers": {
            "page": {
                "user-agent": make_desktop_context()["userAgent"],
                "sec-ch-ua-platform": '"Linux"',
                "sec-ch-ua-mobile": "?0",
            },
            "iframe": {
                "user-agent": make_desktop_context()["userAgent"],
                "sec-ch-ua-platform": '"Linux"',
                "sec-ch-ua-mobile": "?0",
            },
            "worker": {
                "user-agent": make_desktop_context()["userAgent"],
                "sec-ch-ua-platform": '"Linux"',
                "sec-ch-ua-mobile": "?0",
            },
            "sw": {
                "user-agent": make_desktop_context()["userAgent"],
                "sec-ch-ua-platform": '"Linux"',
                "sec-ch-ua-mobile": "?0",
            },
        },
    }
    fails, warns = IdentityRules.check_report(report, full_version=FULL, machine_arch=ARCH)
    assert fails == [], fails


async def test_live_identity_check_flow_on_chromium():
    pw = pytest.importorskip("playwright.async_api")
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"no Playwright Chromium ({type(e).__name__})")

        # Create session backed by this browser
        page = await b.new_page()

        async def connect():
            return page

        s = BrowserSession(cdp_host="127.0.0.1", connect=connect, new_context=b.new_context)
        s._browser = b

        res = await run_identity_check(s, key="task:identity-check", browser="main")
        assert res["full_version"]
        assert "desktop" in res and "phone" not in res and "back_to_desktop" not in res
        # Ensure reports collected all 4 contexts
        for stage in ("desktop",):
            rep = res[stage]["report"]
            assert rep.get("page") is not None
            assert rep.get("iframe") is not None
            assert rep.get("worker") is not None
            assert rep.get("serviceWorker") is not None
        await b.close()
