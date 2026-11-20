"""The program of the `voice` container (ROLE=voice): speech to text with a model the server downloaded (voice_download.py).

It imports only the standard library, sherpa_onnx and numpy, not the rest of `assistant`. It listens on a Unix socket in the
voice-io volume and answers three routes:

    GET  /health                                  -> {"ok", "loaded": model id | null, "busy"}
    POST /transcribe  (X-Kelo-Voice-Model, X-Kelo-Voice-Lang; body = PCM16 mono 16 kHz) -> {"text", "lang", "segments", "took_ms"}
    POST /speak       (X-Kelo-Speech-Voice/-Speed/-Lang/-Steps; body = UTF-8 text) -> raw PCM16 mono at the model's rate
    POST /unload                                  -> {"ok": true}

Kelo's own voice (Supertonic 3, call mode) is loaded from /voice-models/supertonic-3/ the same way, by its own Speaker with its own
lock, so a transcription and a spoken sentence may run side by side. The text of a sentence is never logged.

Models are read from /voice-models/<id>/ (read-only mount) and only when ready.json lists their files. One model is kept loaded
and dropped after VOICE_IDLE_SECONDS without use. Audio is never written anywhere: it lives in memory for one call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("kelo.voice")

SAMPLE_RATE = 16000
MAX_PCM_BYTES = 125 * SAMPLE_RATE * 2          # 2 minutes plus the 5 s grace, PCM16 mono
VAD_WINDOW = 512                               # Silero VAD takes 512 samples at 16 kHz
VAD_MAX_SPEECH_S = 20.0
VAD_MIN_SILENCE_S = 0.25
LEAD_SAMPLES = SAMPLE_RATE         # one second of audio before a segment, when there is one
TAIL_SAMPLES = 0                   # no tail: the next segment may start within half a second and its words would repeat
JOIN_GAP_SAMPLES = SAMPLE_RATE     # VAD pieces less than a second apart are one stretch of speech, decoded once (owner 2026-11-20:
                                   # a short pause inside a sentence split a word, and each piece alone came out as broken syllables)
JOIN_MAX_SAMPLES = int(VAD_MAX_SPEECH_S * SAMPLE_RATE)   # a stretch is never longer than this
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿　-〿＀-￯]")
TAG = re.compile(r"<\|[^|>]*\|>")

# the files each model folder must hold (names only; sizes and hashes were checked when the server downloaded them)
MODEL_FILES: dict[str, tuple[str, ...]] = {
    "sensevoice-small": ("model.int8.onnx", "tokens.txt"),
    "parakeet-tdt-v3": ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
    "supertonic-3": ("duration_predictor.int8.onnx", "text_encoder.int8.onnx", "vector_estimator.int8.onnx", "vocoder.int8.onnx",
                     "tts.json", "unicode_indexer.bin", "voice.bin"),
}

SPEECH_ID = "supertonic-3"
# A copy of voice_models.SPEAK_LANGS: this program must not import `assistant`. A test checks the two are equal.
SPEAK_LANGS = ("en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id",
               "it", "lt", "lv", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi")
MAX_SPEAK_BYTES = 4000              # UTF-8 bytes; the server limits to 1000 characters
SPEAK_THREADS_DEFAULT = 2
SPEAK_STEPS_DEFAULT = 8


def clean_text(raw: str) -> str:
    return TAG.sub("", raw or "").strip()


def join_segments(texts: list[str]) -> str:
    """Join the text of the speech segments: a space between words of Latin or Hangul text, nothing where either side is Chinese,
    Japanese or Kana (those languages do not put spaces between words)."""
    out = ""
    for t in (s.strip() for s in texts):
        if not t:
            continue
        if out and not (CJK.search(out[-1]) or CJK.search(t[0])):
            out += " "
        out += t
    return out


def lang_code(raw_lang: str) -> str:
    """SenseVoice reports its language as a tag, e.g. "<|ko|>"; return the bare code or ""."""
    m = re.search(r"<\|([a-z]{2,3})\|>", raw_lang or "")
    return m.group(1) if m else ""


def model_dir(root: Path, model_id: str) -> Path:
    if model_id not in MODEL_FILES:
        raise ValueError("unknown speech model")
    return root / model_id


def ready_or_raise(root: Path, model_id: str) -> Path:
    folder = model_dir(root, model_id)
    meta = folder / "ready.json"
    if not meta.is_file():
        raise RuntimeError("model not ready")
    listed = json.loads(meta.read_text(encoding="utf-8")).get("files", {})
    for name in MODEL_FILES[model_id]:
        if name not in listed or not (folder / name).is_file():
            raise RuntimeError("model not ready")
    return folder


def make_tts(folder: Path, threads: int) -> Any:
    """Supertonic 3 through sherpa-onnx. Returns an adapter with generate(text, sid, speed, lang, steps) -> (samples, rate)."""
    import sherpa_onnx  # imported here so tests can run without the native library
    m = sherpa_onnx.OfflineTtsSupertonicModelConfig(
        str(folder / "duration_predictor.int8.onnx"), str(folder / "text_encoder.int8.onnx"),
        str(folder / "vector_estimator.int8.onnx"), str(folder / "vocoder.int8.onnx"), str(folder / "tts.json"),
        str(folder / "unicode_indexer.bin"), str(folder / "voice.bin"))
    tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(supertonic=m, num_threads=threads)))
    return _SherpaSpeech(tts, sherpa_onnx)


class _SherpaSpeech:
    def __init__(self, tts: Any, sherpa_onnx: Any):
        self.tts = tts
        self.sherpa_onnx = sherpa_onnx

    def generate(self, text: str, sid: int, speed: float, lang: str, steps: int) -> tuple[Any, int]:
        g = self.sherpa_onnx.GenerationConfig()
        g.sid = sid
        g.speed = speed
        g.num_steps = steps
        g.extra = {"lang": lang}
        audio = self.tts.generate(text, g)
        return audio.samples, int(audio.sample_rate)


def to_pcm16(samples: Any) -> bytes:
    """Float samples in [-1, 1] -> PCM16 little endian, mono. Values outside the range are clipped, never wrapped."""
    import numpy as np
    a = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    return np.rint(a * 32767.0).astype("<i2").tobytes()


class Speaker:
    """Loads Supertonic once (from /voice-models/supertonic-3, ready.json required), speaks one text at a time, drops the model after
    idle_seconds like Engine. Its own lock: a transcription and a sentence may run side by side."""

    def __init__(self, root: Path, threads: int, idle_seconds: float, tts_factory: Callable[[Path, int], Any] = make_tts,
                 now: Callable[[], float] = time.monotonic):
        self.root = root
        self.threads = threads
        self.tts_factory = tts_factory
        self.idle = IdleClock(idle_seconds, now)
        self.loaded = False
        self.lock = asyncio.Lock()
        self._tts: Any = None

    def unload(self) -> None:
        self._tts = None
        self.loaded = False

    def _speak(self, text: str, sid: int, speed: float, lang: str, steps: int) -> tuple[bytes, int]:
        if self._tts is None:
            folder = ready_or_raise(self.root, SPEECH_ID)
            self._tts = self.tts_factory(folder, self.threads)
            self.loaded = True
        samples, rate = self._tts.generate(text, sid, speed, lang, steps)
        return to_pcm16(samples), rate

    async def speak(self, text: str, sid: int, speed: float, lang: str, steps: int) -> tuple[bytes, int, int]:
        async with self.lock:
            self.idle.touch()
            started = time.monotonic()
            pcm, rate = await asyncio.to_thread(self._speak, text, sid, speed, lang, steps)
            self.idle.touch()
            took = int((time.monotonic() - started) * 1000)
            log.info("speech: chars=%d sid=%d took_ms=%d", len(text), sid, took)   # never the text
            return pcm, rate, took


class IdleClock:
    """Decides when the loaded model has been idle long enough to drop. The clock is injected for tests."""

    def __init__(self, idle_seconds: float, now: Callable[[], float] = time.monotonic):
        self.idle_seconds = idle_seconds
        self.now = now
        self.last = now()

    def touch(self) -> None:
        self.last = self.now()

    def expired(self) -> bool:
        return self.now() - self.last >= self.idle_seconds


def make_recognizer(folder: Path, model_id: str, threads: int, hint: str | None) -> Any:
    import sherpa_onnx  # imported here so tests can run without the native library
    if model_id == "sensevoice-small":
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(folder / "model.int8.onnx"), tokens=str(folder / "tokens.txt"), num_threads=threads,
            use_itn=True, language=hint or "auto")
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
        joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"), num_threads=threads,
        model_type="nemo_transducer")


def make_vad(silero_path: str) -> Any:
    import sherpa_onnx
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = silero_path
    cfg.silero_vad.min_silence_duration = VAD_MIN_SILENCE_S
    cfg.silero_vad.max_speech_duration = VAD_MAX_SPEECH_S
    cfg.silero_vad.threshold = 0.5
    cfg.sample_rate = SAMPLE_RATE
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=VAD_MAX_SPEECH_S * 6 + 10)


class Engine:
    """Loads one model at a time, runs VAD, decodes one speech segment at a time (a batch would double the peak memory)."""

    def __init__(self, root: Path, silero_path: str, threads: int, idle_seconds: float,
                 recognizer_factory: Callable[..., Any] = make_recognizer,
                 vad_factory: Callable[[str], Any] = make_vad, now: Callable[[], float] = time.monotonic):
        self.root = root
        self.silero_path = silero_path
        self.threads = threads
        self.recognizer_factory = recognizer_factory
        self.vad_factory = vad_factory
        self.idle = IdleClock(idle_seconds, now)
        self.loaded_id: str | None = None
        self._model: Any = None
        self._vad: Any = None
        self.lock = asyncio.Lock()

    def unload(self) -> None:
        self._model = None
        self._vad = None
        self.loaded_id = None

    def _load(self, model_id: str, hint: str | None) -> None:
        if self.loaded_id == model_id:
            return
        self.unload()
        folder = ready_or_raise(self.root, model_id)
        self._model = self.recognizer_factory(folder, model_id, self.threads, hint)
        self._vad = self.vad_factory(self.silero_path)
        self.loaded_id = model_id

    def _recognize(self, pcm16: bytes, model_id: str, hint: str | None) -> dict[str, Any]:
        import numpy as np
        self._load(model_id, hint)
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        texts: list[str] = []
        lang = ""
        vad = self._vad
        if hasattr(vad, "reset"):
            vad.reset()
        segments = 0
        prev_end = 0               # where the last decoded stretch ended (a lead-in never reaches back over it)
        group: list[int] = []      # [start, end) of the stretch being gathered, in samples

        def decode(chunk: list[float]) -> None:
            nonlocal lang, segments
            stream = self._model.create_stream()
            stream.accept_waveform(SAMPLE_RATE, chunk)
            self._model.decode_stream(stream)
            result = stream.result
            segments += 1
            if clean_text(result.text):
                texts.append(clean_text(result.text))
            lang = lang or lang_code(getattr(result, "lang", ""))
            del stream

        def decode_group() -> None:
            # The model writes spaced-out syllables when a piece begins right at speech (measured with the real SenseVoice model:
            # 0.8 s of audio before the speech gives clean text). So each stretch gets up to one second of the audio before it.
            nonlocal prev_end
            if not group:
                return
            start, end = group
            lead = max(0, min(LEAD_SAMPLES, start - prev_end))
            decode(samples[start - lead:end + TAIL_SAMPLES].tolist())
            prev_end = end
            group.clear()

        def take_ready() -> None:
            while not vad.empty():
                seg = vad.front
                start = getattr(seg, "start", None)
                own = list(seg.samples)   # read before pop(): the segment's buffer goes with it
                vad.pop()
                if start is None:         # a VAD without positions: each piece alone, as it is
                    decode_group()
                    decode(own)
                    continue
                end = start + len(own)
                if group and start - group[1] < JOIN_GAP_SAMPLES and end - group[0] <= JOIN_MAX_SAMPLES:
                    group[1] = end        # close to the stretch: part of it
                    continue
                decode_group()
                group[:] = [start, end]

        for i in range(0, len(samples), VAD_WINDOW):
            vad.accept_waveform(samples[i:i + VAD_WINDOW])
            take_ready()
        vad.flush()
        take_ready()
        decode_group()
        return {"text": join_segments(texts), "lang": lang, "segments": segments}

    async def transcribe(self, model_id: str, hint: str | None, pcm16: bytes) -> dict[str, Any]:
        async with self.lock:
            self.idle.touch()
            started = time.monotonic()
            out = await asyncio.to_thread(self._recognize, pcm16, model_id, hint)
            self.idle.touch()
            out["took_ms"] = int((time.monotonic() - started) * 1000)
            log.info("voice: model=%s bytes=%d took_ms=%d", model_id, len(pcm16), out["took_ms"])
            return out

    async def idle_watch(self, every: float = 30.0) -> None:
        while True:
            await asyncio.sleep(every)
            if self.loaded_id and not self.lock.locked() and self.idle.expired():
                self.unload()
                log.info("voice: model unloaded after idle time")


async def _read_request(reader: asyncio.StreamReader) -> Any:
    line = await reader.readline()
    if not line:
        return None
    method, path, _ = (line.decode("latin-1").strip().split(" ", 2) + ["", ""])[:3]
    headers: dict[str, str] = {}
    while True:
        h = await reader.readline()
        if h in (b"\r\n", b"\n", b""):
            break
        k, _, v = h.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length") or 0)
    if length > MAX_PCM_BYTES:
        return method, path, headers, None   # too long: the route answers 413 and the body is not read
    body = await reader.readexactly(length) if length else b""
    return method, path, headers, body


def _reply(writer: asyncio.StreamWriter, status: int, obj: dict[str, Any]) -> None:
    text = {200: "OK", 400: "Bad Request", 404: "Not Found", 413: "Payload Too Large", 500: "Internal Server Error"}.get(status, "")
    body = json.dumps(obj).encode()
    writer.write(f"HTTP/1.1 {status} {text}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                 f"Connection: close\r\n\r\n".encode() + body)


def _reply_bytes(writer: asyncio.StreamWriter, status: int, body: bytes, headers: dict[str, str]) -> None:
    text = {200: "OK", 400: "Bad Request", 409: "Conflict"}.get(status, "")
    extra = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    writer.write(f"HTTP/1.1 {status} {text}\r\n{extra}Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


class SpeakArgsError(ValueError):
    pass


def parse_speak(headers: dict[str, str], body: bytes | None) -> tuple[str, int, float, str, int]:
    """The arguments of POST /speak, checked: (text, voice, speed, lang, steps). Anything off raises SpeakArgsError."""
    try:
        voice = int(headers.get("x-kelo-speech-voice", ""))
        speed = float(headers.get("x-kelo-speech-speed", ""))
        steps = int(headers.get("x-kelo-speech-steps") or SPEAK_STEPS_DEFAULT)
    except ValueError as e:
        raise SpeakArgsError from e
    lang = headers.get("x-kelo-speech-lang", "")
    if not 0 <= voice <= 9 or not 0.5 <= speed <= 2.0 or not 1 <= steps <= 16 or lang not in SPEAK_LANGS:
        raise SpeakArgsError
    if body is None or not 1 <= len(body) <= MAX_SPEAK_BYTES:
        raise SpeakArgsError
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SpeakArgsError from e
    return text, voice, speed, lang, steps


def make_handler(engine: Engine, speaker: "Speaker | None" = None) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await _read_request(reader)
            if req is None:
                return
            method, path, headers, body = req
            if method == "GET" and path == "/health":
                _reply(writer, 200, {"ok": True, "loaded": engine.loaded_id, "busy": engine.lock.locked(),
                                     "speaking": bool(speaker and speaker.lock.locked()),
                                     "speech_loaded": bool(speaker and speaker.loaded)})
            elif method == "POST" and path == "/unload":
                if not engine.lock.locked():
                    engine.unload()
                if speaker is not None and not speaker.lock.locked():
                    speaker.unload()
                _reply(writer, 200, {"ok": True})
            elif method == "POST" and path == "/speak":
                try:
                    text, voice, speed, lang, steps = parse_speak(headers, body)
                except SpeakArgsError:
                    _reply(writer, 400, {"code": "voice_speech_args"})
                else:
                    if speaker is None:
                        _reply(writer, 409, {"code": "voice_speech_not_ready"})
                    else:
                        try:
                            pcm, rate, took = await speaker.speak(text, voice, speed, lang, steps)
                        except RuntimeError:
                            _reply(writer, 409, {"code": "voice_speech_not_ready"})
                        else:
                            _reply_bytes(writer, 200, pcm, {"Content-Type": "application/octet-stream",
                                                            "X-Kelo-Speech-Rate": str(rate), "X-Kelo-Took-Ms": str(took)})
            elif method == "POST" and path == "/transcribe":
                if body is None or len(body) > MAX_PCM_BYTES:
                    _reply(writer, 413, {"code": "voice_too_long"})
                else:
                    model_id = headers.get("x-kelo-voice-model", "")
                    lang = headers.get("x-kelo-voice-lang", "auto")
                    hint = None if lang in ("", "auto") else lang
                    try:
                        _reply(writer, 200, await engine.transcribe(model_id, hint, body))
                    except RuntimeError:
                        _reply(writer, 409, {"code": "voice_not_ready"})
                    except ValueError:
                        _reply(writer, 400, {"code": "voice_model"})
            else:
                _reply(writer, 404, {"detail": "not found"})
            await writer.drain()
        except Exception:  # noqa: BLE001 - a broken request must not stop the container
            log.exception("voice request failed")
            try:
                _reply(writer, 500, {"code": "voice_failed"})
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
        finally:
            writer.close()

    return handle


def unload_idle(engine: Engine, speaker: "Speaker | None") -> None:
    """Drops each loaded model that has been idle long enough and is not running now."""
    if engine.loaded_id and not engine.lock.locked() and engine.idle.expired():
        engine.unload()
        log.info("voice: model unloaded after idle time")
    if speaker is not None and speaker.loaded and not speaker.lock.locked() and speaker.idle.expired():
        speaker.unload()
        log.info("speech: model unloaded after idle time")


async def idle_watch_all(engine: Engine, speaker: "Speaker | None", every: float = 30.0) -> None:
    while True:
        await asyncio.sleep(every)
        unload_idle(engine, speaker)


async def serve(socket_path: str, root: Path, idle_seconds: float, threads: int, silero_path: str,
                speak_threads: int = SPEAK_THREADS_DEFAULT) -> None:
    engine = Engine(root, silero_path, threads, idle_seconds)
    speaker = Speaker(root, speak_threads, idle_seconds)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(make_handler(engine, speaker), path=socket_path)
    os.chmod(socket_path, 0o660)
    asyncio.get_running_loop().create_task(idle_watch_all(engine, speaker))
    log.info("voice service listening on %s", socket_path)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    socket_path = os.environ.get("VOICE_SOCKET", "/run/voice/voice.sock")
    root = Path(os.environ.get("VOICE_MODELS_DIR", "/voice-models"))
    idle = float(os.environ.get("VOICE_IDLE_SECONDS", "600"))
    threads = int(os.environ.get("VOICE_THREADS") or min(4, os.cpu_count() or 2))
    silero = os.environ.get("VOICE_SILERO", "/opt/voice/silero_vad.onnx")
    speak_threads = int(os.environ.get("VOICE_SPEAK_THREADS") or min(SPEAK_THREADS_DEFAULT, os.cpu_count() or 2))
    asyncio.run(serve(socket_path, root, idle, threads, silero, speak_threads))


if __name__ == "__main__":
    main()
