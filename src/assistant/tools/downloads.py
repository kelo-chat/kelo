"""Files that pages download, kept per tab ("download area"), until the agent copies one into the user's library.

How it works: the browser is told (`Browser.setDownloadBehavior`, behavior allowAndName, events on) to write every download into its
download folder (/downloads in the browser container) under the download's id; the server reads that folder through a read-only mount
of the same volume (BROWSER_DOWNLOADS_DIR, /browser-downloads). `Browser.downloadWillBegin` says which frame (so which tab) started it,
`Browser.downloadProgress` how far it got. The browser container deletes files older than a day by itself.

Refused, and cancelled in the browser before they finish:
- programs and scripts, by name (`BLOCKED_SUFFIXES`) and, when saving, by their first bytes (`looks_executable`);
- anything larger than `MAX_DOWNLOAD` bytes.
A tab's list holds its last `KEEP_PER_TAB` downloads; nothing here is kept across a server restart (the files age out in the browser).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_DOWNLOAD = 200 * 1024**2          # bytes of one download; a bigger one is cancelled
MAX_SAVE = 100 * 1024**2              # bytes the library takes in one file (deliverables.MAX_FILE)
KEEP_PER_TAB = 30
BLOCKED_SUFFIXES = {
    ".exe", ".msi", ".msix", ".msp", ".appx", ".appxbundle", ".bat", ".cmd", ".com", ".scr", ".pif", ".cpl", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".dll", ".sys", ".jar", ".apk", ".aab", ".xapk", ".ipa", ".dmg", ".pkg", ".mpkg",
    ".app", ".deb", ".rpm", ".snap", ".flatpak", ".appimage", ".run", ".bin", ".sh", ".bash", ".zsh", ".command", ".csh", ".ksh", ".so",
    ".dylib", ".elf", ".out", ".py", ".pyw", ".pl", ".rb", ".php", ".scpt", ".workflow", ".gadget", ".inf", ".application", ".xbap",
    ".crx", ".xpi", ".vsix", ".mst", ".ade", ".adp", ".chm", ".ws", ".msc", ".jnlp", ".iso", ".img", ".vhd", ".vhdx",
}


def blocked_name(name: str) -> bool:
    n = (name or "").lower().strip().rstrip(".")
    return any(n.endswith(s) for s in BLOCKED_SUFFIXES)


def looks_executable(head: bytes) -> bool:
    """The first bytes of a program: Windows (MZ), Linux (ELF), macOS (Mach-O, fat), a script with #!, a Java class."""
    return (head.startswith((b"MZ", b"\x7fELF", b"#!", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                             b"\xfe\xed\xfa\xcf")))


def clean_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._\-가-힣 ()]+", "_", Path(str(name or "").replace("\\", "/")).name).strip(" .")
    return base[:120] or "download"


@dataclass(slots=True)
class Download:
    guid: str
    key: str              # the tab (owner key) that started it
    name: str             # the name the site suggested (cleaned)
    url: str
    started_at: float
    state: str = "in_progress"   # in_progress | completed | canceled | blocked
    received: int = 0
    total: int = 0
    reason: str = ""      # why it was blocked or cancelled
    context: str = ""     # browser context id (a private tab's own)

    def view(self) -> dict[str, Any]:
        host = re.sub(r"^[a-z]+://", "", self.url).split("/")[0][:120] if self.url.startswith(("http://", "https://")) else ""
        return {"name": self.name, "state": self.state, "bytes": self.received, "total": self.total or None, "site": host,
                "started_at": self.started_at, "reason": self.reason or None}


class DownloadArea:
    def __init__(self, local_dir: str | Path | None, browser_dir: str = "/downloads", *, max_bytes: int = MAX_DOWNLOAD,
                 clock: Any = time.time):
        self.local = Path(local_dir) if local_dir else None
        self.browser_dir = browser_dir
        self.max_bytes = max_bytes
        self.clock = clock
        self.items: dict[str, Download] = {}

    # ------------------------------------------------------------------ browser events
    def begin(self, key: str, params: dict[str, Any], context: str = "") -> Download:
        """A download started in `key`'s tab. Returns it; state "blocked" = cancel it now."""
        guid = str(params.get("guid") or "")
        name = clean_name(str(params.get("suggestedFilename") or "") or str(params.get("url") or "").rsplit("/", 1)[-1])
        taken = {d.name for d in self.items.values() if d.key == key and d.guid != guid}
        if name in taken:   # two downloads of the same name in one tab: the second is report-1.pdf
            stem, dot, suf = name.rpartition(".")
            stem, suf = (stem, "." + suf) if dot and stem else (name, "")
            n = 1
            while f"{stem}-{n}{suf}" in taken:
                n += 1
            name = f"{stem}-{n}{suf}"
        d = Download(guid, key, name, str(params.get("url") or "")[:500], self.clock(), context=context)
        if blocked_name(name):
            d.state, d.reason = "blocked", "programs and scripts are not downloaded"
        self.items[guid] = d
        mine = [x for x in self.items.values() if x.key == key]
        for old in sorted(mine, key=lambda x: x.started_at)[:-KEEP_PER_TAB]:
            self.items.pop(old.guid, None)
        return d

    def progress(self, params: dict[str, Any]) -> Download | None:
        """A progress event. Returns the download when it must be cancelled now (too large), else None."""
        d = self.items.get(str(params.get("guid") or ""))
        if d is None or d.state in ("blocked", "canceled"):
            return None
        d.received = int(params.get("receivedBytes") or 0)
        d.total = int(params.get("totalBytes") or 0)
        state = str(params.get("state") or "")
        if state == "completed":
            d.state = "completed"
        elif state == "canceled":
            d.state = "canceled" if not d.reason else d.state
        if d.state == "in_progress" and (d.received > self.max_bytes or d.total > self.max_bytes):
            d.state, d.reason = "canceled", f"larger than {self.max_bytes // 1024**2} MB"
            return d
        return None

    # ------------------------------------------------------------------ the agent's view
    def list(self, key: str) -> list[dict[str, Any]]:
        return [d.view() for d in sorted(self.items.values(), key=lambda x: -x.started_at) if d.key == key]

    def find(self, key: str, name: str) -> Download:
        want = (name or "").strip()
        mine = [d for d in sorted(self.items.values(), key=lambda x: -x.started_at) if d.key == key]
        d = next((d for d in mine if d.name == want), None) or next((d for d in mine if d.name == clean_name(want)), None)
        if d is None:
            raise DownloadError(f"no download named {want!r} in this tab. Downloads: {', '.join(x.name for x in mine) or '(none)'}")
        return d

    def read(self, d: Download, cap: int = MAX_SAVE) -> bytes:
        """The file of a finished download, checked again (size, programs)."""
        if d.state == "blocked":
            raise DownloadError(f"{d.name} was blocked: {d.reason}")
        if d.state != "completed":
            raise DownloadError(f"{d.name} is not finished ({d.state.replace('_', ' ')}{': ' + d.reason if d.reason else ''})")
        if self.local is None:
            raise DownloadError("this server cannot see the browser's download folder (BROWSER_DOWNLOADS_DIR is not set)")
        if not re.fullmatch(r"[A-Za-z0-9\-]{8,64}", d.guid):
            raise DownloadError("unknown download")
        p = self.local / d.guid
        try:
            if p.is_symlink() or not p.is_file():
                raise DownloadError(f"{d.name} is no longer in the download folder (downloads are kept for a day)")
            size = p.stat().st_size
            if size > cap:
                raise DownloadError(f"{d.name} is {size // 1024**2} MB; the library takes files up to {cap // 1024**2} MB")
            data = p.read_bytes()
        except PermissionError:
            raise DownloadError("the download folder could not be read") from None
        if looks_executable(data[:8]):
            d.state, d.reason = "blocked", "the file is a program"
            raise DownloadError(f"{d.name} turned out to be a program; it is not kept")
        return data


class DownloadError(Exception):
    """Safe to show the agent."""
