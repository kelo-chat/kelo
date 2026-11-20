"""Server-side voice input: the owner's recording is turned into text here, by the `voice` container (voice_service.py).

One Voice per server. Its settings live in the store's meta (voice_model, voice_declined, voice_error). A model download runs in
a background task and resumes at start if the server stopped during it. Audio is held in memory for one call and is never
written to disk or logged (only seconds and took_ms are logged).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from . import i18n
from . import voice_models as vm
from .voice_download import DownloadError, ModelDownloader, Progress

log = logging.getLogger("assistant.voice")

MAX_SECONDS = 120
GRACE_SECONDS = 5
SAMPLE_RATE = 16000
MAX_WAITING = 2
TIMEOUT_S = 90.0
MAX_BODY = 44 + (MAX_SECONDS + GRACE_SECONDS) * SAMPLE_RATE * 2   # 44-byte WAV header + PCM16 mono

_TAG = re.compile(r"<\|[^|>]*\|>")


class VoiceError(Exception):
    """code: voice_not_ready | voice_too_long | voice_format | voice_busy | voice_runtime_missing | voice_timeout"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class VoiceClientProtocol(Protocol):
    async def health(self) -> dict[str, Any]: ...
    async def transcribe(self, model_id: str, lang: str | None, pcm16: bytes, timeout_s: float) -> dict[str, Any]: ...
    async def unload(self) -> None: ...
    async def speak(self, text: str, voice: int, speed: float, lang: str, steps: int, timeout_s: float) -> tuple[bytes, int]: ...


def clean_text(raw: str) -> tuple[str, str]:
    """Strip SenseVoice's <|tag|> markers; return (text, language code or "") from a leading <|ko|>-style tag."""
    lang = ""
    m = re.match(r"\s*<\|([a-z]{2,3})\|>", raw or "")
    if m and m.group(1) in ("ko", "ja", "zh", "en", "yue"):
        lang = m.group(1)
    text = _TAG.sub("", raw or "").strip()
    return text, lang


def owner_language(meta_get: Callable[[str], str], app_lang: str | None) -> str:
    """The owner's language: store ui_lang when ui_lang_from is set (the owner chose it or it was guessed by the server), else the
    app's language, else English. A single request never flips it (i18n.py)."""
    if meta_get("ui_lang_from") and meta_get("ui_lang"):
        return meta_get("ui_lang")
    return app_lang or meta_get("ui_lang") or i18n.DEFAULT


def pcm_from_wav(wav: bytes) -> bytes:
    """16-bit mono 16 kHz WAV -> raw PCM16 frames. Anything else is refused (voice_format) and a too-long clip (voice_too_long)."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != SAMPLE_RATE:
                raise VoiceError("voice_format")
            frames = w.readframes(w.getnframes())
            seconds = w.getnframes() / SAMPLE_RATE
    except (wave.Error, EOFError) as e:
        raise VoiceError("voice_format") from e
    if seconds > MAX_SECONDS + GRACE_SECONDS:
        raise VoiceError("voice_too_long")
    return frames


class Voice:
    MAX_SECONDS = MAX_SECONDS

    def __init__(self, store: Any, downloader: ModelDownloader, client: VoiceClientProtocol,
                 converter: Callable[[str], str] | None = None):
        self.store = store
        self.downloader = downloader
        self.client = client
        self.converter = converter            # zh-Hant owners: Simplified -> Traditional (D6); identity when None
        self._task: asyncio.Task | None = None
        self._progress: Progress | None = None
        self._error: str | None = None
        self._sem = asyncio.Semaphore(1)
        self._waiting = 0

    # ---- settings (store meta)
    def _chosen(self) -> vm.SpeechModel | None:
        return vm.CATALOG.get(self.store.meta("voice_model") or "")

    def owner_language(self, app_lang: str | None) -> str:
        return owner_language(self.store.meta, app_lang)

    def _state(self, chosen: vm.SpeechModel | None, recommended: vm.SpeechModel | None) -> str:
        if chosen is None:
            if recommended is None:
                return "unsupported"
            return "off" if self.store.meta("voice_declined") else "offered"
        if self._task is not None and not self._task.done():
            return "downloading"
        if self.downloader.ready(chosen):
            return "ready"
        return "failed" if self._error else "off"

    async def runtime_ready(self) -> bool:
        try:
            health = await asyncio.wait_for(self.client.health(), timeout=2.0)
        except Exception:  # noqa: BLE001 - no voice container answers
            return False
        return bool(health.get("ok"))

    async def view(self, app_lang: str | None) -> dict[str, Any]:
        lang = self.owner_language(app_lang)
        rec = vm.recommend(lang)
        chosen = self._chosen()
        state = self._state(chosen, rec)
        progress = None
        if state == "downloading" and self._progress is not None:
            progress = {"done": self._progress.done, "total": self._progress.total}
        return {
            "state": state,
            "declined": bool(self.store.meta("voice_declined")),
            "recommended": rec.view() if rec else None,
            "model": chosen.view() if chosen else None,
            "progress": progress,
            "error": self._error or (self.store.meta("voice_error") or None),
            "models": [m.view() for m in vm.CATALOG.values()],
            "max_seconds": MAX_SECONDS,
            "runtime": "ready" if await self.runtime_ready() else "missing",
        }

    # ---- downloads
    def _start_download(self, model: vm.SpeechModel) -> None:
        self._error = None
        self.store.set_meta("voice_error", "")

        def on_progress(p: Progress) -> None:
            self._progress = p

        async def run() -> None:
            try:
                await self.downloader.fetch(model, on_progress)
            except DownloadError as e:
                self._error = e.code
                self.store.set_meta("voice_error", e.code)
                log.info("speech model download failed: %s", e.code)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never leave the state stuck on downloading
                self._error = "failed"
                self.store.set_meta("voice_error", "failed")
                log.exception("speech model download crashed")
            finally:
                self._progress = None

        self._task = asyncio.get_running_loop().create_task(run())

    async def resume(self) -> None:
        """At server start: a model chosen but not complete resumes its download."""
        chosen = self._chosen()
        if chosen and not self.downloader.ready(chosen):
            self._start_download(chosen)

    async def choose(self, model_id: str) -> dict[str, Any]:
        model = vm.CATALOG.get(model_id)
        if model is None:
            raise KeyError(model_id)
        self.store.set_meta("voice_declined", "")
        self.store.set_meta("voice_model", model.id)
        if not self.downloader.ready(model) and (self._task is None or self._task.done()):
            self._start_download(model)
        return model.view()

    async def decline(self) -> None:
        self.store.set_meta("voice_declined", "1")

    async def remove(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        chosen = self._chosen()
        if chosen:
            self.downloader.remove(chosen.id)
        self.store.set_meta("voice_model", "")
        self._error = None
        self.store.set_meta("voice_error", "")

    # ---- recognition
    async def transcribe(self, wav: bytes, app_lang: str | None = None) -> dict[str, Any]:
        pcm = pcm_from_wav(wav)
        model = self._chosen()
        if model is None or not self.downloader.ready(model):
            raise VoiceError("voice_not_ready")
        if self._sem.locked() and self._waiting >= MAX_WAITING:
            raise VoiceError("voice_busy")
        lang = self.owner_language(app_lang)
        hint = None   # automatic language detection (owner 2026-11-20): the model finds the language; the container sends "auto"
        seconds = len(pcm) / 2 / SAMPLE_RATE
        self._waiting += 1
        try:
            await self._sem.acquire()
        except BaseException:
            self._waiting -= 1
            raise
        self._waiting -= 1
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            got = await asyncio.wait_for(self.client.transcribe(model.id, hint, pcm, TIMEOUT_S), TIMEOUT_S)
        except asyncio.TimeoutError as e:
            raise VoiceError("voice_timeout") from e
        except Exception as e:  # noqa: BLE001 - the container is not answering
            raise VoiceError("voice_runtime_missing") from e
        finally:
            self._sem.release()
            del pcm
        text, found = clean_text(got.get("text", ""))
        if self.converter is not None and lang == "zh-Hant" and text:
            text = self.converter(text)
        took = int((loop.time() - started) * 1000)
        log.info("voice: seconds=%.1f took_ms=%d", seconds, took)
        return {"text": text, "lang": found, "seconds": round(seconds, 1), "took_ms": took}


class SocketVoiceClient:
    """HTTP over the voice container's Unix socket (voice.sock in the voice-io volume; voice_service.py answers it)."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def _client(self, timeout: float) -> Any:
        import httpx
        return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=self.socket_path), base_url="http://voice",
                                 timeout=timeout)

    async def health(self) -> dict[str, Any]:
        async with self._client(2.0) as c:
            r = await c.get("/health")
            r.raise_for_status()
            return r.json()

    async def transcribe(self, model_id: str, lang: str | None, pcm16: bytes, timeout_s: float) -> dict[str, Any]:
        headers = {"X-Kelo-Voice-Model": model_id, "X-Kelo-Voice-Lang": lang or "auto", "Content-Type": "application/octet-stream"}
        async with self._client(timeout_s) as c:
            r = await c.post("/transcribe", content=pcm16, headers=headers)
            r.raise_for_status()
            return r.json()

    async def unload(self) -> None:
        async with self._client(5.0) as c:
            await c.post("/unload")

    async def speak(self, text: str, voice: int, speed: float, lang: str, steps: int, timeout_s: float) -> tuple[bytes, int]:
        """Kelo's voice: the text as UTF-8 in, raw PCM16 mono out (voice_service.py POST /speak); the rate comes in a header."""
        headers = {"X-Kelo-Speech-Voice": str(voice), "X-Kelo-Speech-Speed": str(speed), "X-Kelo-Speech-Lang": lang,
                   "X-Kelo-Speech-Steps": str(steps), "Content-Type": "text/plain; charset=utf-8"}
        async with self._client(timeout_s) as c:
            r = await c.post("/speak", content=text.encode("utf-8"), headers=headers)
            r.raise_for_status()
            return r.content, int(r.headers["X-Kelo-Speech-Rate"])
