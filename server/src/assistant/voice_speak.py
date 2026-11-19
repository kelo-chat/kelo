"""Kelo's own voice (text to speech for call mode): which voice model the owner chose, its download, the voice and speed, and
the sentences spoken through the voice container (voice_service.py, POST /speak).

The text is held in memory for one request and never stored or logged (only its length and the timings are logged). The model
files come from voice_models.SPEECH_OUT, pinned and checked like the speech-to-text models (voice_download.py).
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import Any, Protocol

from . import voice_models as vm
from .spoken_numbers import SpokenNumbers
from .voice_download import DownloadJob, ModelDownloader

log = logging.getLogger("assistant.voice_speak")

SPEECH_ID = vm.SUPERTONIC_ID
SPEAK_MAX_CHARS = 1000
SPEAK_TIMEOUT_S = 30.0
SPEAK_MAX_WAITING = 4
DEFAULT_STEPS = 8
SPEED_MIN, SPEED_MAX = 0.7, 1.5

# store meta keys (the store is the server's small key-value table)
M_MODEL, M_DECLINED, M_ERROR, M_VOICE, M_SPEED = "speech_model", "speech_declined", "speech_error", "speech_voice", "speech_speed"

# error codes -> the HTTP status the routes answer with (api/voice.py)
STATUS = {"voice_speech_not_ready": 409, "voice_speech_too_long": 413, "voice_speech_lang": 422, "voice_speech_args": 422,
          "voice_speech_empty": 422, "voice_busy": 429, "voice_runtime_missing": 503, "voice_timeout": 504}


class SpeakError(Exception):
    """code: one of STATUS"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SpeechClientProtocol(Protocol):
    async def health(self) -> dict[str, Any]: ...
    async def speak(self, text: str, voice: int, speed: float, lang: str, steps: int,
                    timeout_s: float) -> tuple[bytes, int]: ...


def _check_voice(voice: int) -> int:
    if not isinstance(voice, int) or isinstance(voice, bool) or not 0 <= voice <= 9:
        raise SpeakError("voice_speech_args")
    return voice


def _check_speed(speed: float) -> float:
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not SPEED_MIN <= speed <= SPEED_MAX:
        raise SpeakError("voice_speech_args")
    return float(speed)


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class VoiceOut:
    def __init__(self, store: Any, downloader: ModelDownloader, client: SpeechClientProtocol, numbers: SpokenNumbers):
        self.store = store
        self.downloader = downloader
        self.client = client
        self.numbers = numbers
        self.job = DownloadJob(downloader, self._on_error)
        self._sem = asyncio.Semaphore(1)
        self._waiting = 0

    # ---- settings (store meta)
    def _chosen(self) -> vm.SpeechModel | None:
        return vm.SPEECH_OUT.get(self.store.meta(M_MODEL) or "")

    def _on_error(self, code: str) -> None:
        self.store.set_meta(M_ERROR, code)

    def _state(self, chosen: vm.SpeechModel | None) -> str:
        if chosen is None:
            return "off" if self.store.meta(M_DECLINED) else "offered"
        if self.job.running():
            return "downloading"
        if self.downloader.ready(chosen):
            return "ready"
        return "failed" if (self.job.error or self.store.meta(M_ERROR)) else "offered"

    async def _runtime_ready(self) -> bool:
        try:
            health = await asyncio.wait_for(self.client.health(), timeout=2.0)
        except Exception:  # noqa: BLE001 - no voice container answers
            return False
        return bool(health.get("ok"))

    async def view(self) -> dict[str, Any]:
        model = vm.SPEECH_OUT[SPEECH_ID]
        chosen = self._chosen()
        state = self._state(chosen)
        progress = None
        if state == "downloading" and self.job.progress is not None:
            progress = {"done": self.job.progress.done, "total": self.job.progress.total}
        return {
            "state": state,
            "declined": bool(self.store.meta(M_DECLINED)),
            "model": model.view(),
            "progress": progress,
            "error": self.job.error or (self.store.meta(M_ERROR) or None),
            "voice": int(self.store.meta(M_VOICE) or 0),
            "speed": float(self.store.meta(M_SPEED) or 1.0),
            "voices": [{"id": sid, "kind": kind, "n": n} for sid, kind, n in vm.VOICES],
            "languages": list(vm.SPEAK_LANGS),
            "runtime": "ready" if await self._runtime_ready() else "missing",
        }

    # ---- downloads and choice
    def _start(self) -> None:
        self.store.set_meta(M_ERROR, "")
        self.job.start(vm.SPEECH_OUT[SPEECH_ID])

    async def choose(self) -> None:
        model = vm.SPEECH_OUT[SPEECH_ID]
        self.store.set_meta(M_DECLINED, "")
        self.store.set_meta(M_MODEL, model.id)
        if not self.downloader.ready(model):
            self._start()

    async def decline(self) -> None:
        self.store.set_meta(M_DECLINED, "1")

    async def remove(self) -> None:
        self.job.cancel()
        self.downloader.remove(SPEECH_ID)
        self.store.set_meta(M_MODEL, "")
        self.store.set_meta(M_ERROR, "")

    async def resume(self) -> None:
        """At server start: a model chosen but not complete resumes its download."""
        chosen = self._chosen()
        if chosen is not None and not self.downloader.ready(chosen):
            self._start()

    def set_prefs(self, voice: int | None, speed: float | None) -> None:
        if voice is not None:
            self.store.set_meta(M_VOICE, str(_check_voice(voice)))
        if speed is not None:
            self.store.set_meta(M_SPEED, str(_check_speed(speed)))

    # ---- speaking
    async def speak(self, text: str, lang: str, voice: int | None = None,
                    speed: float | None = None) -> tuple[bytes, dict[str, Any]]:
        chosen = self._chosen()
        if chosen is None or not self.downloader.ready(chosen):
            raise SpeakError("voice_speech_not_ready")
        if len(text) > SPEAK_MAX_CHARS:
            raise SpeakError("voice_speech_too_long")
        sid = _check_voice(int(self.store.meta(M_VOICE) or 0) if voice is None else voice)
        speed_value = _check_speed(float(self.store.meta(M_SPEED) or 1.0) if speed is None else speed)
        code = vm.speak_lang(lang)
        if code is None:
            raise SpeakError("voice_speech_lang")
        spoken = self.numbers.apply(text.strip(), lang)
        if not spoken:
            raise SpeakError("voice_speech_empty")
        if self._sem.locked() and self._waiting >= SPEAK_MAX_WAITING:
            raise SpeakError("voice_busy")
        loop = asyncio.get_running_loop()
        self._waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self._waiting -= 1
        started = loop.time()
        try:
            pcm, rate = await asyncio.wait_for(
                self.client.speak(spoken, sid, speed_value, code, DEFAULT_STEPS, SPEAK_TIMEOUT_S), SPEAK_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            raise SpeakError("voice_timeout") from e
        except Exception as e:  # noqa: BLE001 - the container refused or is not answering
            raise SpeakError(_container_code(e)) from e
        finally:
            self._sem.release()
        took = int((loop.time() - started) * 1000)
        seconds = len(pcm) / 2 / rate if rate else 0.0
        log.info("speech out: chars=%d seconds=%.1f took_ms=%d", len(text), seconds, took)   # never the text
        return _wav(pcm, rate), {"seconds": round(seconds, 2), "took_ms": took}


def _container_code(e: Exception) -> str:
    """A 409 or 400 from the container is the same answer the server gives; anything else means no voice container."""
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 409:
            return "voice_speech_not_ready"
        if e.response.status_code == 400:
            return "voice_speech_args"
    return "voice_runtime_missing"
