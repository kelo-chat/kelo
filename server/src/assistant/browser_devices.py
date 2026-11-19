"""The browser's identity as one checked table (docs/browser.md "Identity").

The browser has one view, the desktop one: this server's own Chrome, presenting as the machine's own OS (browser_identity.identity_for:
the User-Agent and the client hints; the CPU, navigator.platform and everything else are the machine's own; nothing is overridden in
JavaScript). IdentityRules checks what a browser reports (a report built by a live check or by a test) against that identity.
Nothing here talks to a browser.
"""
from __future__ import annotations

from typing import Any

from .browser_identity import identity_for


class IdentityRules:
    """What the desktop view must look like, checked against one context's report (a dict: the keys are the ones a live check collects).
    Each check returns a list of failure strings; an empty list means the context agrees with the desktop identity."""

    @staticmethod
    def desktop(ctx: dict[str, Any], *, full_version: str, machine_arch: str, context: str = "page") -> list[str]:
        f: list[str] = []
        expected = identity_for(full_version)   # the identity this browser states (Linux; Chrome OS only with the STATED_OS switch)
        ua = ctx.get("userAgent", "")
        if ua != expected["userAgent"]:
            f.append(f"desktop user agent {ua!r} != {expected['userAgent']!r}")
        if ctx.get("platform") != "Linux x86_64":
            f.append(f"navigator.platform {ctx.get('platform')!r}")
        ch = ctx.get("uaData") or {}
        meta = expected["userAgentMetadata"]
        if ch.get("platform") != meta["platform"] or ch.get("mobile") is not False or ch.get("formFactors") != ["Desktop"]:
            f.append(f"client hints are not a {meta['platform']} desktop: {ch.get('platform')!r}")
        if ch.get("platformVersion") != meta["platformVersion"]:
            f.append(f"client hints platformVersion {ch.get('platformVersion')!r} != {meta['platformVersion']!r}")
        if ch.get("architecture") != machine_arch or ch.get("bitness") != "64" or ch.get("model") not in ("", None):
            f.append(f"client hints architecture {ch.get('architecture')!r} != {machine_arch!r}")
        if ctx.get("cpuByteArch") not in (None, machine_arch):
            f.append("the CPU seen by script disagrees with the client hints")
        if context not in ("worker", "sw"):
            if ctx.get("maxTouchPoints") != 0:
                f.append("the desktop reports touch points")
            if ctx.get("pointerFine") is not True or ctx.get("hoverHover") is not True:
                f.append("the desktop has no fine pointer / hover")
            if ctx.get("plugins") != 5 or ctx.get("pdfViewerEnabled") is not True:
                f.append("desktop plugins / PDF viewer differ")
        if ctx.get("nativeText") is not True:
            f.append("a function was replaced in the browser")
        if ctx.get("webdriver") is True or (ctx.get("webdriver") is not False and context not in ("worker", "sw")) or "Headless" in ua:
            f.append("automation or headless markers")
        return f

    @classmethod
    def check_report(
        cls,
        report: dict[str, Any],
        *,
        full_version: str,
        machine_arch: str,
        profile_tz: str = "Asia/Seoul",
    ) -> tuple[list[str], list[str]]:
        """Validate a full multi-context report against the desktop identity (docs/browser.md, IdentityRules).
        Returns (failures, warnings).
        """
        fails: list[str] = []
        warns: list[str] = []

        contexts = ["page", "iframe", "worker", "serviceWorker"]
        for cname in contexts:
            if cname not in report or not report[cname]:
                fails.append(f"missing context report for {cname}")

        # Check each context
        for cname in contexts:
            ctx = report.get(cname)
            if not ctx:
                continue
            ctype = "page" if cname in ("page", "iframe") else "worker"
            for err in cls.desktop(ctx, full_version=full_version, machine_arch=machine_arch, context=ctype):
                fails.append(f"[{cname}] {err}")

        # Cross-context consistency checks
        active_ctxs = [(cname, report[cname]) for cname in contexts if report.get(cname)]
        if active_ctxs:
            first_name, first_ctx = active_ctxs[0]
            # UA agreement
            for cname, ctx in active_ctxs[1:]:
                if ctx.get("userAgent") != first_ctx.get("userAgent"):
                    fails.append(f"UA mismatch: {first_name} has {first_ctx.get('userAgent')!r} != {cname} has {ctx.get('userAgent')!r}")
                if ctx.get("platform") != first_ctx.get("platform"):
                    fails.append(f"platform mismatch: {first_name} has {first_ctx.get('platform')!r} != {cname} has {ctx.get('platform')!r}")
                if ctx.get("hardwareConcurrency") != first_ctx.get("hardwareConcurrency"):
                    fails.append(f"cores mismatch: {first_name} has {first_ctx.get('hardwareConcurrency')} != {cname} has {ctx.get('hardwareConcurrency')}")
                if ctx.get("timezone") != first_ctx.get("timezone"):
                    fails.append(f"timezone mismatch: {first_name} has {first_ctx.get('timezone')} != {cname} has {ctx.get('timezone')}")
                if ctx.get("locale") and first_ctx.get("locale") and ctx.get("locale") != first_ctx.get("locale"):
                    fails.append(f"locale mismatch: {first_name} has {first_ctx.get('locale')} != {cname} has {ctx.get('locale')}")

                # userAgentData checks
                ch1 = first_ctx.get("uaData") or {}
                ch2 = ctx.get("uaData") or {}
                for key in ("platform", "mobile", "model", "platformVersion"):
                    if ch1.get(key) != ch2.get(key):
                        fails.append(f"userAgentData.{key} mismatch between {first_name} and {cname}: {ch1.get(key)!r} != {ch2.get(key)!r}")

            # Timezone vs profile
            if first_ctx.get("timezone") and profile_tz and first_ctx.get("timezone") != profile_tz:
                fails.append(f"timezone {first_ctx.get('timezone')!r} != profile timezone {profile_tz!r}")

        # Request header checks
        headers = report.get("headers") or {}
        for cname in ("page", "iframe", "worker", "sw"):
            h = headers.get(cname) or {}
            ctx = report.get("serviceWorker" if cname == "sw" else cname)
            if not h or not ctx:
                continue
            # Header User-Agent == navigator.userAgent
            ua_header = h.get("user-agent") or h.get("User-Agent")
            if ua_header and ctx.get("userAgent") and ua_header != ctx.get("userAgent"):
                fails.append(f"header User-Agent != navigator.userAgent in {cname}")

            # Sec-CH-UA-Platform
            plat_header = (h.get("sec-ch-ua-platform") or h.get("Sec-CH-UA-Platform") or "").strip('"')
            ch = ctx.get("uaData") or {}
            if plat_header and ch.get("platform") and plat_header != ch.get("platform"):
                fails.append(f"header Sec-CH-UA-Platform {plat_header!r} != uaData.platform {ch.get('platform')!r} in {cname}")

            # Sec-CH-UA-Mobile
            mob_header = h.get("sec-ch-ua-mobile") or h.get("Sec-CH-UA-Mobile")
            if mob_header:
                expected_mob = "?1" if ch.get("mobile") else "?0"
                if mob_header != expected_mob:
                    fails.append(f"header Sec-CH-UA-Mobile {mob_header!r} != {expected_mob!r} in {cname}")

        # Collect WARN items
        page_ctx = report.get("page") or {}
        if page_ctx.get("webgpu") is None or page_ctx.get("webgpu") is False or page_ctx.get("webgpu") == "none":
            warns.append("WebGPU missing / null")
        if not page_ctx.get("hevc"):
            warns.append("HEVC codec unsupported")
        if not page_ctx.get("webShare"):
            warns.append("Web Share API missing")
        if page_ctx.get("speechVoices", 0) == 0:
            warns.append("Speech voices count is 0")
        if page_ctx.get("mediaDevicesCount", 0) == 0:
            warns.append("mediaDevices is empty []")

        return fails, warns


def accept_language(locale: str) -> str:
    """The Accept-Language list a Chrome of this locale sends: the locale, its language, then English ("ko-KR" -> "ko-KR,ko,en-US,en").
    "" for no locale (the browser's own list stays)."""
    loc = (locale or "").strip()
    if not loc:
        return ""
    base = loc.split("-")[0]
    parts = [loc] + ([base] if base != loc else [])
    if base != "en":
        parts += [tag for tag in ("en-US", "en") if tag not in parts]
    return ",".join(parts)
