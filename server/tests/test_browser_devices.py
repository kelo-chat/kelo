"""The browser's identity (assistant/browser_devices.py): IdentityRules reports exactly the disagreements it is given with the
desktop identity, and the phone view is gone (one view: the desktop). Pure: no browser."""

from assistant.browser_devices import IdentityRules, accept_language

FULL = "131.0.6778.85"
PINNED_FULL = "158.0.8127.36"   # the milestone with a Chrome OS row (browser_identity.CHROME_OS_PLATFORM_VERSIONS)


def test_the_browser_has_no_phone_identity_left():
    import assistant.browser_devices as bd
    assert not any(hasattr(bd, name) for name in ("PHONE_DEVICE", "PHONE_USER_AGENT", "PROFILES", "DeviceProfile", "DESKTOP_DEVICE"))
    assert not hasattr(IdentityRules, "phone")


def test_the_accept_language_list_of_a_locale():
    assert accept_language("ko-KR") == "ko-KR,ko,en-US,en"
    assert accept_language("") == ""


LINUX_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
CHROME_OS_UA = "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"


def desktop_report(**over):
    # the desktop view as the default states it (browser_identity.identity_for: Linux, the machine's own OS)
    r = {"userAgent": LINUX_UA,
         "platform": "Linux x86_64",
         "uaData": {"platform": "Linux", "mobile": False, "formFactors": ["Desktop"], "architecture": "x86", "bitness": "64", "model": "",
                    "platformVersion": ""},
         "cpuByteArch": "x86", "maxTouchPoints": 0, "pointerFine": True, "hoverHover": True, "plugins": 5, "pdfViewerEnabled": True,
         "nativeText": True, "webdriver": False}
    r.update(over)
    return r


def test_a_desktop_report_that_agrees_has_no_failures():
    assert IdentityRules.desktop(desktop_report(), full_version=PINNED_FULL, machine_arch="x86") == []


def test_a_desktop_that_states_chrome_os_is_reported_against_the_default_linux_view():
    chromeos = desktop_report(userAgent=CHROME_OS_UA)
    chromeos["uaData"] = dict(chromeos["uaData"], platform="Chrome OS", platformVersion="16868.13.0")
    fails = IdentityRules.desktop(chromeos, full_version=PINNED_FULL, machine_arch="x86")
    assert any("desktop user agent" in f for f in fails) and any("client hints are not a Linux" in f for f in fails), fails


def test_with_the_chrome_os_switch_the_desktop_view_states_chrome_os(monkeypatch):
    from assistant import browser_identity
    monkeypatch.setattr(browser_identity, "STATED_OS", "chromeos")
    chromeos = desktop_report(userAgent=CHROME_OS_UA)
    chromeos["uaData"] = dict(chromeos["uaData"], platform="Chrome OS", platformVersion="16868.13.0")
    assert IdentityRules.desktop(chromeos, full_version=PINNED_FULL, machine_arch="x86") == []
    fails = IdentityRules.desktop(desktop_report(), full_version=PINNED_FULL, machine_arch="x86")
    assert any("desktop user agent" in f for f in fails) and any("client hints are not a Chrome OS" in f for f in fails), fails


def test_a_desktop_that_says_arm_while_the_client_hints_say_x86_is_reported():
    fails = IdentityRules.desktop(desktop_report(cpuByteArch="arm"), full_version=FULL, machine_arch="x86")
    assert any("CPU seen by script" in f for f in fails)


def test_the_desktop_without_a_mouse_is_reported():
    fails = IdentityRules.desktop(desktop_report(pointerFine=False, hoverHover=False), full_version=FULL, machine_arch="x86")
    assert any("fine pointer" in f for f in fails)
