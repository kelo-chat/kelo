"""Live identity check tool (brief ~/kelo/kelo-agents/briefs/browser-identity.md section 8).

Exercises Kelo's real browser session across four contexts:
- Main page
- Cross-site iframe (OOPIF)
- Dedicated worker
- Service worker (/sw.js)

Served entirely through browser-level DevTools Fetch interception on:
- https://identity-check.invalid/*
- https://identity-frame.invalid/*
(No network traffic, zero foreign requests intercepted).

Runs one report of the tab (the desktop view, the browser's only view) and checks it with IdentityRules.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import platform
import sys
import time
from typing import Any

from ..browser_devices import IdentityRules
from .browser import BrowserSession, browser_owner
from .work_browser import WorkBrowser, work_tab

ACH = (
    "sec-ch-ua-arch, sec-ch-ua-bitness, sec-ch-ua-model, sec-ch-ua-platform-version, "
    "sec-ch-ua-full-version-list, sec-ch-ua-wow64, sec-ch-ua-form-factors, "
    "sec-ch-device-memory, sec-ch-dpr, sec-ch-viewport-width"
)

CHECK_ORIGIN = "https://identity-check.invalid"
FRAME_ORIGIN = "https://identity-frame.invalid"

COLLECT_SIGNALS_JS = """
async function collectContextIdentity(isWorker) {
  const o = {};
  const n = navigator;
  o.userAgent = n.userAgent;
  o.platform = n.platform;
  o.hardwareConcurrency = n.hardwareConcurrency;
  o.deviceMemory = n.deviceMemory || null;
  o.languages = Array.from(n.languages || []);
  o.language = n.language;
  o.locale = Intl.DateTimeFormat().resolvedOptions().locale;
  o.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  o.webdriver = (typeof n.webdriver === 'boolean') ? n.webdriver : false;

  // Client Hints
  if (n.userAgentData) {
    try {
      const d = n.userAgentData;
      o.uaData = {
        brands: d.brands,
        mobile: d.mobile,
        platform: d.platform,
      };
      const he = await d.getHighEntropyValues([
        'architecture', 'bitness', 'model', 'platformVersion',
        'fullVersionList', 'wow64', 'formFactors', 'uaFullVersion'
      ]);
      Object.assign(o.uaData, he);
    } catch (e) {
      o.uaDataErr = String(e);
    }
  }

  // CPU byte check via Float32 NaN sign byte
  try {
    const f = new Float32Array(1), u8 = new Uint8Array(f.buffer);
    f[0] = Infinity; f[0] = f[0] - f[0];
    o.nanByte = u8[3];
    if (u8[3] === 127) o.cpuByteArch = 'arm';
    else if (u8[3] === 255) o.cpuByteArch = 'x86';
  } catch (e) {}

  if (!isWorker) {
    // Window/Document specific
    o.maxTouchPoints = n.maxTouchPoints || 0;
    o.plugins = n.plugins ? n.plugins.length : 0;
    o.pdfViewerEnabled = typeof n.pdfViewerEnabled === 'boolean' ? n.pdfViewerEnabled : null;
    o.screen = [screen.width, screen.height];
    o.availScreen = [screen.availWidth, screen.availHeight];
    o.dpr = window.devicePixelRatio;
    o.innerWidth = window.innerWidth;
    o.innerHeight = window.innerHeight;
    o.outerWidth = window.outerWidth;
    o.outerHeight = window.outerHeight;
    o.visualViewport = window.visualViewport ? [visualViewport.width, visualViewport.height, visualViewport.scale] : null;
    o.orientation = screen.orientation ? screen.orientation.type : null;
    o.orientationAngle = screen.orientation ? screen.orientation.angle : 0;
    o.pointerFine = matchMedia('(pointer:fine)').matches;
    o.pointerCoarse = matchMedia('(pointer:coarse)').matches;
    o.hoverHover = matchMedia('(hover:hover)').matches;
    o.hoverNone = matchMedia('(hover:none)').matches;
    o.metaViewport = true;

    // WebGL
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (gl) {
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        if (dbg) {
          o.gpu = [
            gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
            gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
          ];
        }
      }
    } catch (e) {}

    // Function toString native check
    try {
      let nativeOk = true;
      if (typeof WebGLRenderingContext !== 'undefined') {
        const s = Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter);
        if (s !== 'function getParameter() { [native code] }') nativeOk = false;
      }
      if (typeof Navigator !== 'undefined') {
        const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'plugins');
        if (desc && desc.get) {
          const s = Function.prototype.toString.call(desc.get);
          if (s !== 'function get plugins() { [native code] }') nativeOk = false;
        }
        const descPdf = Object.getOwnPropertyDescriptor(Navigator.prototype, 'pdfViewerEnabled');
        if (descPdf && descPdf.get) {
          const s = Function.prototype.toString.call(descPdf.get);
          if (s !== 'function get pdfViewerEnabled() { [native code] }') nativeOk = false;
        }
      }
      const sToString = Function.prototype.toString.call(Function.prototype.toString);
      if (sToString !== 'function toString() { [native code] }') nativeOk = false;
      o.nativeText = nativeOk;
    } catch (e) {
      o.nativeText = false;
    }

    // Warnings signals
    o.webgpu = (typeof navigator.gpu !== 'undefined' && navigator.gpu !== null);
    o.webShare = typeof navigator.share === 'function';
    o.speechVoices = (typeof window.speechSynthesis !== 'undefined') ? speechSynthesis.getVoices().length : 0;
    try {
      const v = document.createElement('video');
      o.hevc = v.canPlayType('video/mp4; codecs="hev1.1.6.L93.B0"') !== '';
    } catch (e) { o.hevc = false; }
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      o.mediaDevicesCount = devs.length;
    } catch (e) { o.mediaDevicesCount = 0; }
  } else {
    // Worker specific WebGL via OffscreenCanvas
    try {
      if (typeof OffscreenCanvas !== 'undefined') {
        const oc = new OffscreenCanvas(1, 1);
        const gl = oc.getContext('webgl');
        if (gl) {
          const dbg = gl.getExtension('WEBGL_debug_renderer_info');
          if (dbg) {
            o.gpu = [
              gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
              gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
            ];
          }
        }
      }
    } catch (e) {}

    try {
      let nativeOk = true;
      if (typeof WebGLRenderingContext !== 'undefined') {
        const s = Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter);
        if (s !== 'function getParameter() { [native code] }') nativeOk = false;
      }
      const sToString = Function.prototype.toString.call(Function.prototype.toString);
      if (sToString !== 'function toString() { [native code] }') nativeOk = false;
      o.nativeText = nativeOk;
    } catch (e) {
      o.nativeText = false;
    }
  }

  return o;
}
"""

MAIN_HTML = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Identity Check</title>
</head>
<body style="height: 3500px; margin: 0; padding: 20px;">
  <h1>Kelo Identity Check</h1>
  <div style="height: 1200px;"></div>
  <input id="test-input" type="text" style="width: 280px; height: 35px; font-size: 16px;" value="" />
  <div style="margin-top: 20px;">
    <iframe id="cross-frame" src="{FRAME_ORIGIN}/frame.html" style="width: 320px; height: 180px; border: 1px solid #ccc;"></iframe>
  </div>

  <script>
    window.ev = {{mouse: 0, touch: 0, key: 0, wheel: 0}};
    const real = (e) => !(e.sourceCapabilities && e.sourceCapabilities.firesTouchEvents);
    for (const t of ['mousemove', 'mousedown', 'mouseup']) {{
      addEventListener(t, (e) => {{ if (real(e)) ev.mouse++; }}, true);
    }}
    addEventListener('wheel', () => ev.wheel++, true);
    for (const t of ['touchstart', 'touchmove', 'touchend']) {{
      addEventListener(t, () => ev.touch++, true);
    }}
    addEventListener('keydown', () => ev.key++, true);

    window.identityReport = {{
      page: null,
      iframe: null,
      worker: null,
      serviceWorker: null
    }};

    window.addEventListener('message', (event) => {{
      const msg = event.data;
      if (!msg) return;
      if (msg.context === 'iframe') {{
        window.identityReport.iframe = msg.data;
      }} else if (msg.context === 'worker') {{
        window.identityReport.worker = msg.data;
      }} else if (msg.context === 'sw') {{
        window.identityReport.serviceWorker = msg.data;
      }}
    }});

    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.addEventListener('message', (event) => {{
        const msg = event.data;
        if (msg && msg.context === 'sw') {{
          window.identityReport.serviceWorker = msg.data;
        }}
      }});
    }}

    {COLLECT_SIGNALS_JS}

    async function initCheck() {{
      try {{
        // Page identity
        const pageData = await collectContextIdentity(false);
        window.identityReport.page = pageData;
        try {{ await fetch('/echo?from=page'); }} catch (e) {{}}

        // Dedicated worker
        try {{
          const w = new Worker('/worker.js');
          w.onmessage = (e) => {{
            if (e.data && e.data.context === 'worker') {{
              window.identityReport.worker = e.data.data;
            }}
          }};
        }} catch (e) {{
          window.identityReport.worker = {{err: String(e)}};
        }}

        // Service worker
        if ('serviceWorker' in navigator) {{
          try {{
            const reg = await navigator.serviceWorker.register('/sw.js');
            const ask = (sw) => {{
              if (!sw) return;
              try {{
                const mc = new MessageChannel();
                mc.port1.onmessage = (event) => {{
                  if (event.data && event.data.context === 'sw') {{
                    window.identityReport.serviceWorker = event.data.data;
                  }}
                }};
                sw.postMessage({{action: 'getIdentity'}}, [mc.port2]);
              }} catch (e) {{}}
            }};
            const sw = reg.installing || reg.waiting || reg.active;
            if (sw) {{
              ask(sw);
              sw.addEventListener('statechange', () => {{
                if (sw.state === 'activated' || sw.state === 'installed') ask(sw);
              }});
            }}
            navigator.serviceWorker.ready.then((r) => ask(r.active));
          }} catch (e) {{
            window.identityReport.serviceWorker = {{err: String(e)}};
          }}
        }}
      }} catch (err) {{
        console.error('initCheck error', err);
      }}
    }}

    initCheck();
  </script>
</body>
</html>
"""

FRAME_HTML = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Identity Frame</title>
</head>
<body>
  <h3>Cross-Site Frame</h3>
  <script>
    {COLLECT_SIGNALS_JS}

    async function initFrame() {{
      try {{
        const data = await collectContextIdentity(false);
        try {{ await fetch('/echo?from=iframe'); }} catch (e) {{}}
        window.parent.postMessage({{context: 'iframe', data: data}}, '*');
      }} catch (e) {{
        window.parent.postMessage({{context: 'iframe', data: {{err: String(e)}}}}, '*');
      }}
    }}
    initFrame();
  </script>
</body>
</html>
"""

WORKER_JS = f"""
{COLLECT_SIGNALS_JS}

(async () => {{
  try {{
    const data = await collectContextIdentity(true);
    try {{ await fetch('/echo?from=worker'); }} catch (e) {{}}
    postMessage({{context: 'worker', data: data}});
  }} catch (e) {{
    postMessage({{context: 'worker', data: {{err: String(e)}}}});
  }}
}})();
"""

SW_JS = f"""
{COLLECT_SIGNALS_JS}

self.addEventListener('install', (e) => {{
  self.skipWaiting();
}});

self.addEventListener('activate', (e) => {{
  e.waitUntil(self.clients.claim().then(() => broadcastIdentity()));
}});

async function broadcastIdentity() {{
  try {{
    const data = await collectContextIdentity(true);
    try {{ await fetch('/echo?from=sw'); }} catch (e) {{}}
    const clients = await self.clients.matchAll({{includeUncontrolled: true, type: 'window'}});
    for (const c of clients) {{
      c.postMessage({{context: 'sw', data: data}});
    }}
  }} catch (e) {{}}
}}

self.addEventListener('message', async (e) => {{
  if (e.data && e.data.action === 'getIdentity') {{
    try {{
      const data = await collectContextIdentity(true);
      try {{ await fetch('/echo?from=sw'); }} catch (e) {{}}
      if (e.ports && e.ports[0]) {{
        e.ports[0].postMessage({{context: 'sw', data: data}});
      }}
      if (e.source) {{
        e.source.postMessage({{context: 'sw', data: data}});
      }}
      const clients = await self.clients.matchAll({{includeUncontrolled: true, type: 'window'}});
      for (const c of clients) {{
        c.postMessage({{context: 'sw', data: data}});
      }}
    }} catch (err) {{}}
  }}
}});
"""


class FetchHandler:
    def __init__(self, cdp: Any) -> None:
        self.cdp = cdp
        self.headers_by_from: dict[str, dict[str, str]] = {}

    async def start(self) -> None:
        self.cdp.on("Fetch.requestPaused", lambda p: asyncio.create_task(self._on_paused(p)))
        await self.cdp.send(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": f"{CHECK_ORIGIN}/*"},
                    {"urlPattern": f"{FRAME_ORIGIN}/*"},
                ]
            },
        )

    async def stop(self) -> None:
        try:
            await self.cdp.send("Fetch.disable", {})
        except Exception:
            pass

    async def _on_paused(self, params: dict[str, Any]) -> None:
        req_id = params["requestId"]
        req = params.get("request") or {}
        url = req.get("url", "")
        headers = req.get("headers") or {}

        # Capture echo requests
        if "/echo" in url:
            from_ctx = "page"
            if "from=" in url:
                from_ctx = url.split("from=")[1].split("&")[0]
            self.headers_by_from[from_ctx] = dict(headers)
            await self._fulfill(req_id, 200, "application/json", '{"ok":true}')
            return

        # Serve static documents/scripts
        if url.rstrip("/") == CHECK_ORIGIN:
            await self._fulfill(req_id, 200, "text/html; charset=utf-8", MAIN_HTML, accept_ch=True)
        elif url.rstrip("/") == f"{FRAME_ORIGIN}/frame.html":
            await self._fulfill(req_id, 200, "text/html; charset=utf-8", FRAME_HTML, accept_ch=True)
        elif url == f"{CHECK_ORIGIN}/worker.js":
            await self._fulfill(req_id, 200, "application/javascript; charset=utf-8", WORKER_JS)
        elif url == f"{CHECK_ORIGIN}/sw.js":
            await self._fulfill(req_id, 200, "application/javascript; charset=utf-8", SW_JS)
        else:
            await self._fulfill(req_id, 200, "text/plain", "ok")

    async def _fulfill(
        self, req_id: str, status: int, content_type: str, body: str, accept_ch: bool = False
    ) -> None:
        resp_headers = [{"name": "Content-Type", "value": content_type}]
        if accept_ch:
            resp_headers.append({"name": "Accept-CH", "value": ACH})
        body_b64 = base64.b64encode(body.encode("utf-8")).decode("utf-8")
        try:
            await self.cdp.send(
                "Fetch.fulfillRequest",
                {
                    "requestId": req_id,
                    "responseCode": status,
                    "responseHeaders": resp_headers,
                    "body": body_b64,
                },
            )
        except Exception:
            pass


async def wait_for_report(page: Any, timeout: float = 12.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rep = await page.evaluate("() => window.identityReport")
            if rep and rep.get("page") and rep.get("iframe") and rep.get("worker") and rep.get("serviceWorker"):
                return rep
        except Exception:
            pass
        await asyncio.sleep(0.3)
    try:
        return await page.evaluate("() => window.identityReport") or {}
    except Exception as e:
        return {"error": str(e)}


async def run_identity_check(
    session: BrowserSession,
    *,
    key: str = "task:identity-check",
    browser: str = "work",
    profile_tz: str = "Asia/Seoul",
) -> dict[str, Any]:
    """Run the live identity check of the browser's desktop view (its one view)."""
    token = browser_owner.set(key)
    try:
        # Determine Chrome version & architecture
        if browser == "work" and session.work:
            if not await session.work.ensure():
                raise RuntimeError("Work browser is not answering")
            browser_inst = session.work._browser
        else:
            if session._browser is None:
                await session._ensure_browser()
            browser_inst = session._browser
        b_cdp = await browser_inst.new_browser_cdp_session()

        ver_info = await b_cdp.send("Browser.getVersion", {})
        product = ver_info.get("product", "Chrome/158.0.8127.36")
        full_version = product.split("/")[1] if "/" in product else "158.0.8127.36"
        machine_arch = "arm" if platform.machine().lower() in ("arm64", "aarch64") else "x86"

        fetch_handler = FetchHandler(b_cdp)
        await fetch_handler.start()

        try:
            # Set private mode for isolation
            await session.set_private(True)
            page = await session.page()

            results: dict[str, Any] = {
                "full_version": full_version,
                "machine_arch": machine_arch,
                "browser": browser,
                "desktop": {},
                "all_passed": True,
            }

            await page.goto(f"{CHECK_ORIGIN}/")
            desktop_raw = await wait_for_report(page)
            desktop_report = dict(desktop_raw)
            desktop_report["headers"] = dict(fetch_handler.headers_by_from)
            desktop_fails, desktop_warns = IdentityRules.check_report(
                desktop_report, full_version=full_version, machine_arch=machine_arch, profile_tz=profile_tz
            )
            results["desktop"] = {
                "full_version": full_version,
                "machine_arch": machine_arch,
                "browser": browser,
                "report": desktop_report,
                "fails": desktop_fails,
                "warns": desktop_warns,
                "ok": len(desktop_fails) == 0,
            }
            if desktop_fails:
                results["all_passed"] = False

            return results
        finally:
            await fetch_handler.stop()
            try:
                await b_cdp.detach()
            except Exception:
                pass
            await session.close_tab(key)
    finally:
        browser_owner.reset(token)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Kelo live identity check")
    parser.add_argument("--browser", choices=["work", "main"], default="work", help="Target browser (default: work)")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    parser.add_argument("--cdp-host", default=None, help="CDP host")
    parser.add_argument("--cdp-port", type=int, default=None, help="CDP port")
    parser.add_argument("--work-host", default=None, help="Work browser CDP host")
    parser.add_argument("--work-port", type=int, default=None, help="Work browser CDP port")
    args = parser.parse_args()

    # Determine CDP hosts / ports
    cdp_host = args.cdp_host or os.environ.get("BROWSER_CDP_HOST", "browser")
    cdp_port = args.cdp_port or int(os.environ.get("BROWSER_CDP_PORT", "9223"))
    work_host = args.work_host or os.environ.get("WORK_BROWSER_CDP_HOST", "work-browser")
    work_port = args.work_port or int(os.environ.get("WORK_BROWSER_CDP_PORT", "9225"))

    session = BrowserSession(cdp_host, cdp_port, allow_private=True)
    if work_host:
        session.work = WorkBrowser(work_host, work_port)
        session.work.on_connected = session.work_connected

    key = "task:identity-check" if args.browser == "work" else "identity-check"
    try:
        res = await run_identity_check(session, key=key, browser=args.browser)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res["all_passed"] else 1
        else:
            print("=" * 60)
            print(f"Kelo Browser Identity Check (Browser: {args.browser}, Chrome: {res['full_version']}, Arch: {res['machine_arch']})")
            print("=" * 60)
            for stage in ("desktop",):
                st = res[stage]
                status = "PASS" if st.get("ok") else "FAIL"
                print(f"\n--- {stage.upper()}: {status} ---")
                if st.get("fails"):
                    print("  FAILURES:")
                    for f in st["fails"]:
                        print(f"    - {f}")
                if st.get("warns"):
                    print("  WARNINGS:")
                    for w in st["warns"]:
                        print(f"    - {w}")
            print("\n" + "=" * 60)
            print(f"OVERALL RESULT: {'PASS' if res['all_passed'] else 'FAIL'}")
            print("=" * 60)
            return 0 if res["all_passed"] else 1
    finally:
        await session.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
