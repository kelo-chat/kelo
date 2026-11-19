"""Download a speech model into the models folder: https only, Hugging Face hosts only, every hop checked, resumable with
Range, each file checked by sha256, then renamed into place. Written once `ready.json` lists every file.

The server does the download; the voice container only reads the folder (see voice_service.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from . import voice_models as vm
from .netguard import NetGuardError, resolve_public

log = logging.getLogger("assistant.voice")

CHUNK = 1024 * 1024
MAX_HOPS = 5
HEADROOM = 200 * 1024 * 1024
ALLOWED_SUFFIXES = (".hf.co", ".huggingface.co")
ALLOWED_HOSTS = ("huggingface.co",)


class DownloadError(Exception):
    """code: offline | checksum | no_room | refused_host | http_<n>"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Progress:
    model_id: str
    done: int
    total: int
    state: str          # downloading | verifying | ready | failed
    error: str | None = None


def _host_allowed(host: str) -> bool:
    return host in ALLOWED_HOSTS or any(host.endswith(s) for s in ALLOWED_SUFFIXES)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class ModelDownloader:
    def __init__(self, root: Path, client_factory: Callable[[], httpx.AsyncClient],
                 disk_free: Callable[[Path], int], resolver: Callable[..., list] | None = None):
        self.root = Path(root)
        self._client_factory = client_factory
        self._disk_free = disk_free
        self._resolver = resolver

    def _folder(self, model_id: str) -> Path:
        if model_id not in vm.ALL:
            raise ValueError("unknown speech model")
        return self.root / model_id

    def _complete(self, folder: Path, f: vm.ModelFile) -> bool:
        p = folder / f.name
        return p.is_file() and p.stat().st_size == f.size and _sha256(p) == f.sha256

    async def fetch(self, model: vm.SpeechModel, on_progress: Callable[[Progress], None]) -> None:
        folder = self._folder(model.id)
        folder.mkdir(parents=True, exist_ok=True)
        todo = [f for f in model.files if not self._complete(folder, f)]
        remaining = sum(f.size for f in todo)
        if self._disk_free(self.root) < remaining + HEADROOM:
            raise DownloadError("no_room")
        done = sum(f.size for f in model.files if f not in todo)
        total = model.size_bytes
        on_progress(Progress(model.id, done, total, "downloading"))
        async with self._client_factory() as client:
            for f in model.files:
                if f not in todo:
                    continue
                part = folder / (f.name + ".part")
                await self._get(client, f, part, lambda n, base=done: on_progress(
                    Progress(model.id, base + n, total, "downloading")))
                on_progress(Progress(model.id, done + f.size, total, "verifying"))
                if _sha256(part) != f.sha256:
                    part.unlink(missing_ok=True)
                    raise DownloadError("checksum")
                os.replace(part, folder / f.name)
                done += f.size
        meta = {"model": model.id, "files": {f.name: f.sha256 for f in model.files}, "at": int(time.time())}
        tmp = folder / "ready.json.tmp"
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, folder / "ready.json")
        on_progress(Progress(model.id, total, total, "ready"))

    async def _get(self, client: httpx.AsyncClient, f: vm.ModelFile, part: Path,
                   on_bytes: Callable[[int], None]) -> None:
        start = part.stat().st_size if part.exists() else 0
        if start > f.size:
            part.unlink()
            start = 0
        if start == f.size:
            on_bytes(start)
            return
        url = f.url
        for _ in range(MAX_HOPS + 1):
            await self._check_url(url)
            headers = {"Range": f"bytes={start}-"} if start else {}
            try:
                request = client.build_request("GET", url, headers=headers)
                response = await client.send(request, stream=True, follow_redirects=False)
            except httpx.TransportError:
                raise DownloadError("offline") from None
            try:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code == 416 and start:
                    return
                if response.status_code not in (200, 206):
                    raise DownloadError(f"http_{response.status_code}")
                if response.status_code == 200 and start:
                    start = 0
                mode = "ab" if start else "wb"
                got = start
                on_bytes(got)
                with part.open(mode) as fh:
                    async for chunk in response.aiter_bytes(CHUNK):
                        fh.write(chunk)
                        got += len(chunk)
                        on_bytes(got)
                return
            finally:
                await response.aclose()
        raise DownloadError("refused_host")

    async def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or not _host_allowed(parts.hostname):
            raise DownloadError("refused_host")
        try:
            await resolve_public(parts.hostname, self._resolver)
        except NetGuardError:
            raise DownloadError("refused_host") from None

    def remove(self, model_id: str) -> None:
        shutil.rmtree(self._folder(model_id), ignore_errors=True)

    def ready(self, model: vm.SpeechModel) -> bool:
        folder = self.root / model.id
        meta_path = folder / "ready.json"
        if not meta_path.is_file():
            return False
        return all((folder / f.name).is_file() and (folder / f.name).stat().st_size == f.size for f in model.files)


class DownloadJob:
    """One background download of one model, with its progress and last error (kept in memory; the caller keeps meta)."""

    def __init__(self, downloader: ModelDownloader, on_error: Callable[[str], None]):
        self.downloader = downloader
        self.on_error = on_error
        self.progress: Progress | None = None
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, model: vm.SpeechModel) -> None:
        if self.running():
            return
        self.error = None

        def on_progress(p: Progress) -> None:
            self.progress = p

        async def run() -> None:
            try:
                await self.downloader.fetch(model, on_progress)
            except DownloadError as e:
                self.error = e.code
                self.on_error(e.code)
                log.info("speech model download failed: %s", e.code)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never leave the state stuck on downloading
                self.error = "failed"
                self.on_error("failed")
                log.exception("speech model download crashed")
            finally:
                self.progress = None

        self._task = asyncio.get_running_loop().create_task(run())

    def cancel(self) -> None:
        if self.running():
            self._task.cancel()
